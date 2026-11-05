"""Authenticated, report-pinned question answering orchestration."""

from __future__ import annotations

from collections import OrderedDict, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import secrets
import stat
import threading
import time
from typing import Any, Callable

from backend.config.paths import PathContainmentError, resolve_owned_path
from backend.engine.context import (
    ContextError,
    ContextFormatter,
    EvidenceContext,
    SelectedImageEvidence,
    SelectedTextEvidence,
    verify_context_fingerprint,
)
from backend.engine.generation import (
    GenerationClientError,
    GenerationInferenceError,
    GenerationResult,
    LocalGenerationAdapter,
)
from backend.engine.manifest import DocumentManifest, build_document_manifest
from backend.engine.retrieval import (
    HybridRetrievalResult,
    RetrievalReportFailed,
    RetrievalReportMismatch,
    RetrievalReportPartial,
    RetrievalReportUnavailable,
    RetrievalStoreError,
    SemanticRetrievalError,
    query_id_for,
    retrieve_for_plan,
)
from backend.experiments.run_plan import RunPlan
from backend.services.index_selection import IndexSelectionError, IndexSelectionService, PinnedIndexReport
from backend.services.model_registry import ModelAdapterRegistry, ModelRuntimeError


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REFERENCE = re.compile(r"^[0-9a-f]{32}$")
_SAFE_RECORD_ID = re.compile(r"^[A-Za-z0-9._:-]{1,192}$")
_CITATION = re.compile(r"\[([TI][0-9]{1,3})\]")
_MODEL_ABSTENTION = re.compile(
    r"^\s*(?:\*{0,2})?(?:insufficient evidence\b|not enough evidence\b|"
    r"the (?:supplied|provided|retrieved) evidence is insufficient\b|"
    r"i (?:cannot|can't|am unable to) answer (?:from|based on) (?:the )?(?:supplied|provided|retrieved) evidence\b)",
    re.IGNORECASE,
)
_MAX_QUESTION_CHARS = 4000
_MAX_QUESTION_TOKENS = 512
_MAX_ANSWER_CHARS = 64 * 1024
_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_MAX_IMAGE_REFERENCES = 128
_IMAGE_REFERENCE_TTL_SECONDS = 300.0


class AskError(RuntimeError):
    """A bounded public failure code for the Ask workflow."""

    def __init__(self, code: str, status_code: int = 500) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", code or ""):
            code = "ask_failed"
        self.code = code
        self.status_code = status_code if 400 <= status_code <= 599 else 500
        super().__init__(self.code)


@dataclass(frozen=True)
class AskImagePayload:
    data: bytes
    content_type: str


@dataclass(frozen=True)
class _ImageReference:
    root: Path
    relative_path: str
    content_type: str
    size_bytes: int
    sha256: str
    expires_at: float


class AskImageReferenceRegistry:
    """Short-lived in-memory references to verified index-owned raster images."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] | None = None,
        ttl_seconds: float = _IMAGE_REFERENCE_TTL_SECONDS,
        max_references: int = _MAX_IMAGE_REFERENCES,
    ) -> None:
        if not isinstance(ttl_seconds, (int, float)) or not 1 <= float(ttl_seconds) <= 3600:
            raise ValueError("image_reference_ttl_invalid")
        if isinstance(max_references, bool) or not isinstance(max_references, int) or not 1 <= max_references <= 1024:
            raise ValueError("image_reference_limit_invalid")
        self._clock = clock or time.monotonic
        self._ttl = float(ttl_seconds)
        self._maximum = max_references
        self._lock = threading.RLock()
        self._entries: OrderedDict[str, _ImageReference] = OrderedDict()

    @staticmethod
    def _is_reparse(info: os.stat_result, path: Path) -> bool:
        if stat.S_ISLNK(info.st_mode):
            return True
        if int(getattr(info, "st_file_attributes", 0)) & 0x400:
            return True
        junction = getattr(path, "is_junction", None)
        try:
            return bool(junction()) if callable(junction) else False
        except OSError:
            return True

    @classmethod
    def _has_reparse_component(cls, path: Path) -> bool:
        absolute = Path(os.path.abspath(path))
        current = Path(absolute.anchor)
        for part in absolute.parts[1:]:
            current = current / part
            try:
                info = current.lstat()
            except FileNotFoundError:
                continue
            except OSError:
                return True
            if cls._is_reparse(info, current):
                return True
        return False

    @staticmethod
    def _media_type(data: bytes) -> str | None:
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "image/webp"
        return None

    @classmethod
    def _read_verified(
        cls,
        root: Path,
        candidate_path: Path,
        *,
        expected_sha256: str | None = None,
    ) -> tuple[bytes, str, str, str]:
        try:
            resolved_root = Path(root).expanduser().resolve(strict=True)
            raw_candidate = Path(candidate_path)
            if not raw_candidate.is_absolute():
                raw_candidate = resolved_root / raw_candidate
            if cls._has_reparse_component(resolved_root) or cls._has_reparse_component(raw_candidate):
                raise AskError("image_evidence_unsafe", 404)
            resolved = resolve_owned_path(resolved_root, raw_candidate)
            relative = resolved.relative_to(resolved_root).as_posix()
            before = resolved.lstat()
        except AskError:
            raise
        except (OSError, PathContainmentError, ValueError) as exc:
            raise AskError("image_evidence_unavailable", 404) from exc
        if not relative or relative.startswith("../") or not stat.S_ISREG(before.st_mode):
            raise AskError("image_evidence_unsafe", 404)
        if before.st_size <= 0 or before.st_size > _MAX_IMAGE_BYTES:
            raise AskError("image_evidence_size_invalid", 413)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = -1
        try:
            descriptor = os.open(resolved, flags)
            with os.fdopen(descriptor, "rb", closefd=True) as handle:
                descriptor = -1
                opened = os.fstat(handle.fileno())
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = handle.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > _MAX_IMAGE_BYTES:
                        raise AskError("image_evidence_size_invalid", 413)
                    chunks.append(chunk)
                finished = os.fstat(handle.fileno())
            after = resolved.lstat()
        except AskError:
            raise
        except OSError as exc:
            raise AskError("image_evidence_unavailable", 404) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        signature = lambda info: (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if (cls._is_reparse(after, resolved) or signature(before) != signature(opened) or
                signature(opened) != signature(finished) or signature(finished) != signature(after) or total != before.st_size):
            raise AskError("image_evidence_changed", 404)
        data = b"".join(chunks)
        media_type = cls._media_type(data)
        if media_type is None:
            raise AskError("image_evidence_type_invalid", 415)
        digest = hashlib.sha256(data).hexdigest()
        if expected_sha256 is not None and digest != expected_sha256:
            raise AskError("image_evidence_changed", 404)
        return data, media_type, digest, relative

    def _prune(self, now: float) -> None:
        expired = [key for key, value in self._entries.items() if value.expires_at <= now]
        for key in expired:
            self._entries.pop(key, None)

    def register(self, root: Path, image: SelectedImageEvidence, expected_sha256: str | None) -> tuple[str, str, int] | None:
        if not isinstance(image, SelectedImageEvidence) or not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
            return None
        try:
            data, content_type, digest, relative = self._read_verified(Path(root), image.resolved_path, expected_sha256=expected_sha256)
        except AskError:
            return None
        size_bytes = len(data)
        del data
        now = self._clock()
        reference = secrets.token_hex(16)
        entry = _ImageReference(Path(root).resolve(strict=True), relative, content_type, size_bytes, digest, now + self._ttl)
        with self._lock:
            self._prune(now)
            while len(self._entries) >= self._maximum:
                self._entries.popitem(last=False)
            self._entries[reference] = entry
        return reference, content_type, entry.size_bytes

    def read(self, reference: str) -> AskImagePayload:
        if not isinstance(reference, str) or not _REFERENCE.fullmatch(reference):
            raise AskError("image_evidence_expired", 404)
        now = self._clock()
        with self._lock:
            self._prune(now)
            entry = self._entries.get(reference)
            if entry is None:
                raise AskError("image_evidence_expired", 404)
        path = entry.root.joinpath(*PurePosixPath(entry.relative_path).parts)
        try:
            data, content_type, _digest, _relative = self._read_verified(entry.root, path, expected_sha256=entry.sha256)
        except AskError as exc:
            with self._lock:
                self._entries.pop(reference, None)
            raise AskError("image_evidence_expired", 404) from exc
        if content_type != entry.content_type or len(data) != entry.size_bytes:
            with self._lock:
                self._entries.pop(reference, None)
            raise AskError("image_evidence_expired", 404)
        return AskImagePayload(data, content_type)


class AskService:
    """Run one report-pinned retrieval/context/generation request."""

    def __init__(
        self,
        source_root: str | Path,
        storage_root: str | Path,
        selection_service: IndexSelectionService,
        model_registry: ModelAdapterRegistry,
        context_formatter: ContextFormatter,
        generation_adapter: LocalGenerationAdapter,
        *,
        retrieval_callable: Callable[..., HybridRetrievalResult] | None = None,
        max_concurrent_queries: int = 2,
        image_registry: AskImageReferenceRegistry | None = None,
    ) -> None:
        if not Path(source_root).is_absolute() or not Path(storage_root).is_absolute():
            raise ValueError("ask_paths_invalid")
        if not isinstance(selection_service, IndexSelectionService) or not isinstance(model_registry, ModelAdapterRegistry):
            raise ValueError("ask_services_invalid")
        if not isinstance(context_formatter, ContextFormatter) or not isinstance(generation_adapter, LocalGenerationAdapter):
            raise ValueError("ask_pipeline_invalid")
        if isinstance(max_concurrent_queries, bool) or not isinstance(max_concurrent_queries, int) or not 1 <= max_concurrent_queries <= 4:
            raise ValueError("ask_concurrency_invalid")
        if retrieval_callable is not None and not callable(retrieval_callable):
            raise ValueError("ask_retrieval_invalid")
        self._source_root = Path(source_root).resolve(strict=True)
        self._storage_root = Path(storage_root).resolve(strict=True)
        self._selection = selection_service
        self._models = model_registry
        self._formatter = context_formatter
        self._generation = generation_adapter
        self._retrieve = retrieval_callable or retrieve_for_plan
        self._capacity = threading.BoundedSemaphore(max_concurrent_queries)
        self._images = image_registry or AskImageReferenceRegistry()

    def _validate_question(self, question: str, plan: RunPlan) -> str:
        if not isinstance(question, str):
            raise AskError("question_invalid", 400)
        normalized = question.strip()
        if (not normalized or len(normalized) > _MAX_QUESTION_CHARS or
                any((ord(char) < 32 and char not in "\n\r\t") or ord(char) == 127 for char in normalized)):
            raise AskError("question_invalid", 400)
        try:
            import tiktoken

            encoder = tiktoken.get_encoding(plan.resolved_config().tiktoken_encoding)
            token_count = len(encoder.encode(normalized))
        except Exception as exc:
            raise AskError("ask_tokenizer_unavailable", 503) from exc
        if token_count > _MAX_QUESTION_TOKENS:
            raise AskError("question_too_long", 413)
        return normalized

    def _adapters(self, plan: RunPlan) -> dict[str, Any]:
        config = plan.resolved_config()
        if config.retrieval_mode == "bm25":
            return {}
        try:
            values: dict[str, Any] = {
                "text_adapter": self._models.get_text_adapter(config),
                "image_adapter": self._models.get_image_adapter(config),
            }
            if config.retrieval_mode == "hybrid" and config.use_reranker:
                values["reranker_adapter"] = self._models.get_reranker(config)
            return values
        except ModelRuntimeError as exc:
            raise AskError("model_adapter_unavailable", 503) from exc

    @staticmethod
    def _manifest_sources(manifest: DocumentManifest) -> dict[str, list[Any]]:
        output: dict[str, list[Any]] = defaultdict(list)
        for entry in manifest.entries:
            output[PurePosixPath(entry.relative_path).name.casefold()].append(entry)
        return output

    @staticmethod
    def _source_entry(source: str, sources: dict[str, list[Any]]) -> Any:
        if (not isinstance(source, str) or not source.strip() or source != source.strip() or
                source in {".", ".."} or "/" in source or "\\" in source or ":" in source or
                any(ord(char) < 32 or ord(char) == 127 for char in source) or
                PureWindowsPath(source).drive):
            raise AskError("evidence_source_unresolved", 500)
        matches = sources.get(source.casefold(), [])
        if len(matches) != 1:
            raise AskError("evidence_source_unresolved", 500)
        return matches[0]

    @classmethod
    def _source_entry_for_evidence(cls, source: str, hit: Any, sources: dict[str, list[Any]]) -> Any:
        metadata = getattr(hit, "metadata", None)
        raw_source = metadata.get("source") if isinstance(metadata, Mapping) else None
        entry = cls._source_entry(raw_source, sources)
        # ContextFormatter renders a sanitized filename. Confirm that it came
        # from this exact manifest filename instead of guessing from a path's
        # basename or trusting a Chroma-supplied absolute path.
        rendered_source = "".join(" " if ord(char) < 32 or ord(char) == 127 else char for char in raw_source)
        rendered_source = " ".join(rendered_source.split())
        if len(rendered_source) > 120:
            rendered_source = rendered_source[:119].rstrip() + "…"
        if rendered_source != source:
            raise AskError("evidence_source_unresolved", 500)
        return entry

    @staticmethod
    def _page_number(value: str) -> int | None:
        if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,5}", value):
            return None
        return int(value)

    @staticmethod
    def _record_id(value: str, warnings: list[str]) -> str | None:
        if isinstance(value, str) and _SAFE_RECORD_ID.fullmatch(value):
            return value
        warnings.append("evidence_record_id_unavailable")
        return None

    def _public_text_evidence(self, context: EvidenceContext, sources: dict[str, list[Any]], warnings: list[str]) -> list[dict[str, Any]]:
        if len(context.selected_text) > 32:
            raise AskError("ask_response_limit", 500)
        result: list[dict[str, Any]] = []
        for item in context.selected_text:
            if not isinstance(item, SelectedTextEvidence) or len(item.text) > 32_000:
                raise AskError("ask_response_limit", 500)
            entry = self._source_entry_for_evidence(item.source, item.hit, sources)
            page = self._page_number(item.page)
            if page is None:
                warnings.append("evidence_page_unavailable")
            result.append({
                "label": item.label,
                "document_id": entry.document_id,
                "source_path": entry.relative_path,
                "page_number": page,
                "passage": item.text,
                "record_id": self._record_id(item.record_id, warnings),
                "truncated": bool(item.truncated),
            })
        return result

    def _public_image_evidence(
        self,
        context: EvidenceContext,
        sources: dict[str, list[Any]],
        generation: GenerationResult,
        config: Any,
        warnings: list[str],
    ) -> list[dict[str, Any]]:
        if len(context.selected_images) > 16:
            raise AskError("ask_response_limit", 500)
        result: list[dict[str, Any]] = []
        for index, item in enumerate(context.selected_images):
            if not isinstance(item, SelectedImageEvidence):
                raise AskError("ask_evidence_invalid", 500)
            entry = self._source_entry_for_evidence(item.source, item.hit, sources)
            page = self._page_number(item.page)
            if page is None:
                warnings.append("evidence_page_unavailable")
            expected_hash = generation.image_hashes[index] if index < len(generation.image_hashes) else None
            registered = self._images.register(Path(config.images_dir), item, expected_hash)
            if registered is None:
                warnings.append("image_evidence_unavailable")
            reference, content_type, size_bytes = registered if registered is not None else (None, None, None)
            caption = item.caption if isinstance(item.caption, str) else ""
            result.append({
                "label": item.label,
                "document_id": entry.document_id,
                "source_path": entry.relative_path,
                "page_number": page,
                "caption": caption[:2000],
                "record_id": self._record_id(item.record_id, warnings),
                "image_reference": reference,
                "content_type": content_type,
                "size_bytes": size_bytes,
            })
        return result

    @staticmethod
    def _validate_retrieval(result: Any, question: str, plan: RunPlan) -> HybridRetrievalResult:
        if not isinstance(result, HybridRetrievalResult):
            raise AskError("retrieval_result_invalid", 502)
        if (result.index_fingerprint != plan.index_fingerprint or
                result.document_manifest_hash != plan.document_manifest_hash or
                result.query_text != question or not isinstance(result.modalities, tuple) or
                result.modalities != ("text", "image")):
            raise AskError("retrieval_identity_mismatch", 502)
        expected_id = query_id_for(question, plan, result.modalities, result.effective_text_k, result.effective_image_k)
        if result.query_id != expected_id:
            raise AskError("retrieval_identity_mismatch", 502)
        return result

    @staticmethod
    def _validate_generation(value: Any, context: EvidenceContext, plan: RunPlan, config: Any) -> GenerationResult:
        if not isinstance(value, GenerationResult):
            raise AskError("generation_result_invalid", 502)
        if value.status == "failed":
            if value.error_code == "ollama_unavailable":
                raise AskError("ollama_unavailable", 503)
            raise AskError("generation_failed", 502)
        if (value.status != "succeeded" or not isinstance(value.response, str) or not value.response.strip() or
                len(value.response) > _MAX_ANSWER_CHARS or value.query_id != context.query_id or
                value.query_text != context.query_text or value.index_fingerprint != plan.index_fingerprint or
                value.document_manifest_hash != plan.document_manifest_hash or value.run_fingerprint != plan.run_fingerprint or
                value.context_fingerprint != context.context_fingerprint or value.model_name != config.llm_model or
                value.context_tokens != context.text_context_tokens or value.attempts < 1 or
                value.images_sent != len(context.selected_images) or len(value.image_hashes) != len(context.selected_images) or
                any(not isinstance(digest, str) or not _SHA256.fullmatch(digest) for digest in value.image_hashes) or
                not isinstance(value.prompt_fingerprint, str) or not _SHA256.fullmatch(value.prompt_fingerprint)):
            raise AskError("generation_result_invalid", 502)
        return value

    @staticmethod
    def _public_model_identifier(value: Any) -> str:
        if not isinstance(value, str):
            return "configured_model"
        candidate = value.strip()
        if (not candidate or len(candidate) > 128 or Path(candidate).is_absolute() or
                PureWindowsPath(candidate).drive or "\\" in candidate or
                not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}", candidate)):
            return "configured_model"
        return candidate

    def _base_response(
        self,
        *,
        question: str,
        plan: RunPlan,
        pinned: PinnedIndexReport,
        context: EvidenceContext,
        outcome: str,
        answer: str | None,
        failure_code: str | None,
        model_identifier: str | None,
        text_evidence: list[dict[str, Any]],
        image_evidence: list[dict[str, Any]],
        cited: list[str],
        warnings: list[str],
    ) -> dict[str, Any]:
        response = {
            "query_id": context.query_id,
            "question": question,
            "outcome": outcome,
            "answer": answer,
            "model_reported_insufficient_evidence": bool(
                outcome == "answered" and isinstance(answer, str) and _MODEL_ABSTENTION.search(answer)
            ),
            "failure_code": failure_code,
            "model_identifier": model_identifier,
            "index_fingerprint": plan.index_fingerprint,
            "document_manifest_hash": plan.document_manifest_hash,
            "run_fingerprint": plan.run_fingerprint,
            "context_fingerprint": context.context_fingerprint,
            "selected_build_id": pinned.build_id,
            "selected_report_sha256": pinned.report_sha256,
            "selection_revision": pinned.revision,
            "text_evidence": text_evidence,
            "image_evidence": image_evidence,
            "referenced_citations": cited,
            "warnings": list(dict.fromkeys(warnings))[:32],
        }
        try:
            encoded = __import__("json").dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except Exception as exc:
            raise AskError("ask_response_invalid", 500) from exc
        if len(encoded) > 2 * 1024 * 1024:
            raise AskError("ask_response_limit", 500)
        return response

    def ask(self, raw_question: str) -> dict[str, Any]:
        if not self._capacity.acquire(blocking=False):
            raise AskError("ask_capacity_exhausted", 429)
        try:
            try:
                plan, pinned = self._selection.resolve_active_plan()
            except IndexSelectionError:
                raise
            question = self._validate_question(raw_question, plan)
            config = plan.resolved_config()
            self._selection.validate_pinned(plan, pinned)
            adapters = self._adapters(plan)
            try:
                raw_result = self._retrieve(
                    question,
                    plan,
                    self._storage_root,
                    build_report_path=pinned.report_path,
                    modalities=("text", "image"),
                    **adapters,
                )
            except (RetrievalReportUnavailable, RetrievalReportMismatch, RetrievalReportFailed, RetrievalReportPartial, RetrievalStoreError) as exc:
                code = "index_store_unavailable" if isinstance(exc, (RetrievalReportUnavailable, RetrievalStoreError)) else "selected_report_incompatible"
                raise AskError(code, 503 if code == "index_store_unavailable" else 409) from exc
            except SemanticRetrievalError as exc:
                raise AskError("retrieval_failed", 502) from exc
            except Exception as exc:
                raise AskError("retrieval_failed", 502) from exc
            result = self._validate_retrieval(raw_result, question, plan)
            self._selection.validate_pinned(plan, pinned)
            try:
                context = self._formatter.format(result, plan)
                verify_context_fingerprint(context, config)
            except ContextError as exc:
                raise AskError("ask_context_failed", 500) from exc
            if (context.query_id != result.query_id or context.query_text != question or
                    context.index_fingerprint != plan.index_fingerprint or
                    context.document_manifest_hash != plan.document_manifest_hash or
                    context.run_fingerprint != plan.run_fingerprint):
                raise AskError("context_identity_mismatch", 502)

            try:
                manifest = build_document_manifest(self._source_root)
            except Exception as exc:
                raise AskError("source_manifest_unavailable", 503) from exc
            if manifest.content_hash != pinned.document_manifest_hash:
                raise IndexSelectionError("index_selection_stale")
            source_map = self._manifest_sources(manifest)
            warnings = list(context.warnings)
            text_evidence = self._public_text_evidence(context, source_map, warnings)
            if not text_evidence and not context.selected_images:
                return self._base_response(
                    question=question, plan=plan, pinned=pinned, context=context,
                    outcome="insufficient_evidence", answer=None, failure_code="no_usable_evidence",
                    model_identifier=None, text_evidence=[], image_evidence=[], cited=[], warnings=warnings,
                )

            self._selection.validate_pinned(plan, pinned)
            try:
                generated = self._generation.generate(context, plan)
            except GenerationClientError as exc:
                raise AskError("ollama_unavailable", 503) from exc
            except GenerationInferenceError as exc:
                if exc.error_code == "ollama_unavailable":
                    raise AskError("ollama_unavailable", 503) from exc
                raise AskError("generation_failed", 502) from exc
            except Exception as exc:
                raise AskError("generation_failed", 502) from exc
            generated = self._validate_generation(generated, context, plan, config)
            self._selection.validate_pinned(plan, pinned)
            allowed = {item.label for item in context.selected_text} | {item.label for item in context.selected_images}
            referenced = [match.group(1) for match in _CITATION.finditer(generated.response)]
            cited = list(dict.fromkeys(label for label in referenced if label in allowed))
            if any(label not in allowed for label in referenced):
                warnings.append("unsupported_citation_label")
            image_evidence = self._public_image_evidence(context, source_map, generated, config, warnings)
            return self._base_response(
                question=question, plan=plan, pinned=pinned, context=context,
                outcome="answered", answer=generated.response, failure_code=None,
                model_identifier=self._public_model_identifier(generated.model_name), text_evidence=text_evidence,
                image_evidence=image_evidence, cited=cited, warnings=warnings,
            )
        finally:
            self._capacity.release()

    def read_image(self, reference: str) -> AskImagePayload:
        return self._images.read(reference)


__all__ = ["AskError", "AskImagePayload", "AskImageReferenceRegistry", "AskService"]
