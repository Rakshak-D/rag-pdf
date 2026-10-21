"""Deterministic, provenance-preserving evidence context selection."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from time import perf_counter
from typing import Any

from backend.config.paths import PathContainmentError, resolve_owned_path
from backend.experiments.run_plan import RunPlan
from backend.engine.retrieval import HybridHit, HybridRetrievalResult


CONTEXT_SCHEMA_VERSION = "1"


class ContextError(ValueError):
    """Base error for context validation and selection."""


class ContextInputError(ContextError):
    """Raised when a retrieval result or run plan is invalid."""


class ContextImagePathError(ContextError):
    """Raised for an unsafe configured image root or candidate path."""


class ContextTokenizationError(ContextError):
    """Raised when the configured tokenizer cannot count context text."""


@dataclass(frozen=True)
class SelectedTextEvidence:
    label: str
    record_id: str
    source: str
    page: str
    text: str
    hit: HybridHit
    truncated: bool = False


@dataclass(frozen=True)
class SelectedImageEvidence:
    label: str
    record_id: str
    source: str
    page: str
    caption: str
    resolved_path: Path
    hit: HybridHit


@dataclass(frozen=True)
class EvidenceContext:
    query_id: str
    index_fingerprint: str
    document_manifest_hash: str
    run_fingerprint: str
    context_fingerprint: str
    text_context: str
    text_context_tokens: int
    selected_text: tuple[SelectedTextEvidence, ...]
    selected_images: tuple[SelectedImageEvidence, ...]
    diagnostics: Mapping[str, Any]
    warnings: tuple[str, ...]
    elapsed_seconds: float


def _safe_page(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return "N/A"
    if isinstance(value, (str, int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return "N/A"
        return str(value)
    return "N/A"


def _source_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return "unknown"
    parts = [part for part in value.replace("\\", "/").split("/") if part]
    return parts[-1] if parts else "unknown"


def _finite_distance(hit: HybridHit) -> float | None:
    value = hit.effective_distance if hit.effective_distance is not None else hit.semantic_distance
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _normalized_text(value: str) -> str:
    return " ".join(value.split()).casefold()


def _shingles(value: str, size: int = 5) -> set[str]:
    words = _normalized_text(value).split()
    return {" ".join(words[index:index + size]) for index in range(max(1, len(words) - size + 1))} if words else set()


def _percentile(values: Sequence[float], cutoff: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("cannot calculate percentile from an empty sequence")
    position = (len(ordered) - 1) * cutoff / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


class ContextFormatter:
    """Select and format bounded evidence from one verified retrieval result."""

    def __init__(self, *, encoder: Callable[[str], Sequence[int]] | Any | None = None) -> None:
        self._encoder = encoder

    def _encode(self, text: str, config: Any) -> list[int]:
        try:
            encoder = self._encoder
            if encoder is None:
                import tiktoken
                encoder = tiktoken.get_encoding(config.tiktoken_encoding)
            values = encoder.encode(text) if hasattr(encoder, "encode") else encoder(text)
            values = list(values)
            if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
                raise TypeError("encoder returned non-integer tokens")
            return values
        except ContextTokenizationError:
            raise
        except Exception as exc:
            raise ContextTokenizationError("configured tokenizer could not encode context text") from exc

    @staticmethod
    def _validate_inputs(result: Any, plan: Any) -> tuple[HybridRetrievalResult, RunPlan, Any]:
        if not isinstance(result, HybridRetrievalResult):
            raise ContextInputError("context selection requires a HybridRetrievalResult")
        if not isinstance(plan, RunPlan):
            raise ContextInputError("context selection requires a RunPlan")
        try:
            plan.verify_integrity()
        except Exception as exc:
            raise ContextInputError("RunPlan integrity verification failed") from exc
        if result.index_fingerprint != plan.index_fingerprint:
            raise ContextInputError("retrieval index fingerprint does not match the RunPlan")
        if result.document_manifest_hash != plan.document_manifest_hash:
            raise ContextInputError("retrieval document manifest does not match the RunPlan")
        return result, plan, plan.resolved_config()

    def _select_text(self, hits: Sequence[HybridHit], config: Any, warnings: list[str]) -> tuple[list[HybridHit], dict[str, Any]]:
        valid: list[HybridHit] = []
        exact: set[str] = set()
        shingles: list[set[str]] = []
        exact_duplicates = near_duplicates = 0
        for hit in hits:
            if not isinstance(hit, HybridHit) or not isinstance(hit.document, str) or not hit.document.strip():
                warnings.append("text_candidate_invalid")
                continue
            normalized = _normalized_text(hit.document)
            if normalized in exact:
                exact_duplicates += 1
                continue
            current = _shingles(hit.document)
            if any(current and previous and len(current & previous) / len(current | previous) >= 0.70 for previous in shingles):
                near_duplicates += 1
                continue
            exact.add(normalized)
            shingles.append(current)
            valid.append(hit)

        rejected = 0
        missing_distance = 0
        filter_mode = "disabled"
        if config.use_filtering:
            finite = [distance for hit in valid if (distance := _finite_distance(hit)) is not None]
            if config.use_percentile_filtering and finite:
                threshold = _percentile(finite, config.percentile_cutoff)
                filter_mode = "percentile"
            else:
                threshold = config.text_distance_threshold
                filter_mode = "distance_threshold"
            filtered = []
            for hit in valid:
                distance = _finite_distance(hit)
                if distance is None:
                    missing_distance += 1
                    filtered.append(hit)
                elif distance <= threshold:
                    filtered.append(hit)
                else:
                    rejected += 1
            if not filtered and valid:
                filtered = valid[:config.max_text_chunks]
                warnings.append("text_filter_fallback")
            valid = filtered
        selected = valid[:config.max_text_chunks]
        return selected, {"valid_text_candidates": len(valid), "exact_duplicates": exact_duplicates, "near_duplicates": near_duplicates, "text_filter_rejected": rejected, "text_filter_missing_distance": missing_distance, "text_filter_fallback": "text_filter_fallback" in warnings, "text_filter_mode": filter_mode}

    def _format_text(self, hits: Sequence[HybridHit], config: Any, warnings: list[str]) -> tuple[str, tuple[SelectedTextEvidence, ...], dict[str, int]]:
        if not hits:
            return "", (), {"text_entries_truncated": 0, "text_entries_budget_skipped": 0, "text_context_tokens": 0}
        output: list[str] = []
        selected: list[SelectedTextEvidence] = []
        truncated = skipped = 0
        for hit in hits:
            metadata = hit.metadata if isinstance(hit.metadata, Mapping) else {}
            source = _source_name(metadata.get("source"))
            page = _safe_page(metadata.get("page_num"))
            label = f"T{len(selected) + 1}"
            header = f"[{label}] Source: {source}, page {page} (ID: {hit.record_id})\n"
            text = hit.document.strip() if isinstance(hit.document, str) else ""
            if not text:
                continue
            candidate = header + text
            joined = "\n\n".join(output + [candidate])
            if len(self._encode(joined, config)) <= config.max_context_tokens:
                output.append(candidate)
                selected.append(SelectedTextEvidence(label, hit.record_id, source, page, text, hit))
                continue
            prefix = "\n\n".join(output + [header])
            if len(self._encode(prefix, config)) > config.max_context_tokens:
                skipped += 1
                continue
            marker = " …[truncated]"
            low, high, best = 0, len(text), ""
            while low <= high:
                middle = (low + high) // 2
                body = text[:middle].rstrip() + marker
                candidate = header + body
                if len(self._encode("\n\n".join(output + [candidate]), config)) <= config.max_context_tokens:
                    best = body
                    low = middle + 1
                else:
                    high = middle - 1
            if not best:
                skipped += 1
                continue
            output.append(header + best)
            selected.append(SelectedTextEvidence(label, hit.record_id, source, page, best, hit, True))
            truncated += 1
        text_context = "\n\n".join(output)
        return text_context, tuple(selected), {"text_entries_truncated": truncated, "text_entries_budget_skipped": skipped, "text_context_tokens": len(self._encode(text_context, config)) if text_context else 0}

    def _select_images(self, hits: Sequence[HybridHit], config: Any, warnings: list[str]) -> tuple[tuple[SelectedImageEvidence, ...], dict[str, int]]:
        selected: list[SelectedImageEvidence] = []
        rejected_missing = rejected_unsafe = rejected_threshold = 0
        for hit in hits:
            metadata = hit.metadata if isinstance(hit.metadata, Mapping) else {}
            candidate = metadata.get("image_path")
            if not isinstance(candidate, str) or not candidate.strip():
                rejected_missing += 1
                warnings.append("image_path_missing")
                continue
            try:
                resolved = resolve_owned_path(config.images_dir, candidate)
            except PathContainmentError:
                rejected_unsafe += 1
                warnings.append("image_path_outside_owned_root")
                continue
            if not resolved.is_file():
                rejected_missing += 1
                warnings.append("image_file_missing")
                continue
            distance = _finite_distance(hit)
            if config.use_filtering and distance is not None and distance > config.image_distance_threshold:
                rejected_threshold += 1
                continue
            source = _source_name(metadata.get("source"))
            page = _safe_page(metadata.get("page_num"))
            selected.append(SelectedImageEvidence(f"I{len(selected) + 1}", hit.record_id, source, page, str(metadata.get("caption_text") or ""), resolved, hit))
            if len(selected) >= config.max_images:
                break
        return tuple(selected), {"image_candidates_rejected_missing": rejected_missing, "image_candidates_rejected_unsafe": rejected_unsafe, "image_candidates_rejected_threshold": rejected_threshold}

    def format(self, result: HybridRetrievalResult, run_plan: RunPlan) -> EvidenceContext:
        started = perf_counter()
        result, plan, config = self._validate_inputs(result, run_plan)
        warnings: list[str] = []
        text_hits, text_diag = self._select_text(result.text_hits, config, warnings)
        text_context, selected_text, budget_diag = self._format_text(text_hits, config, warnings)
        selected_images, image_diag = self._select_images(result.image_hits, config, warnings)
        diagnostics = {"input_text_candidates": len(result.text_hits), "input_image_candidates": len(result.image_hits), "selected_text_count": len(selected_text), "selected_image_count": len(selected_images), **text_diag, **budget_diag, **image_diag, "max_context_tokens": config.max_context_tokens, "max_images": config.max_images}
        fingerprint_payload = {"schema": CONTEXT_SCHEMA_VERSION, "query_id": result.query_id, "index_fingerprint": result.index_fingerprint, "document_manifest_hash": result.document_manifest_hash, "run_fingerprint": plan.run_fingerprint, "text": text_context, "text_ids": [item.record_id for item in selected_text], "image_ids": [item.record_id for item in selected_images]}
        context_fingerprint = hashlib.sha256(json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
        return EvidenceContext(result.query_id, result.index_fingerprint, result.document_manifest_hash, plan.run_fingerprint, context_fingerprint, text_context, budget_diag["text_context_tokens"], selected_text, selected_images, diagnostics, tuple(warnings), max(0.0, perf_counter() - started))
