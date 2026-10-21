"""Deterministic local orchestration for one verified RAG index build."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
from time import perf_counter
from typing import Any, Literal
import uuid

import numpy as np

from backend.config.paths import PathContainmentError, resolve_disjoint_paths, resolve_owned_path
from backend.config.schema import ResolvedRunConfig
from backend.embeddings.image import ImageEmbeddingAdapter, ImageEmbeddingBatchResult
from backend.embeddings.text import EmbeddingBatchResult, TextEmbeddingAdapter
from backend.experiments.run_plan import RunPlan
from backend.vectorstore import ChromaVectorStore

from .ingestion import (
    PdfExtractionResult,
    bbox_chunker,
    build_image_objects,
    extract_pdfs,
)
from .manifest import ManifestMismatchError, build_document_manifest


BuildStatus = Literal["completed", "completed_with_warnings", "failed"]


class IndexBuildError(RuntimeError):
    """Raised for invalid build inputs or failures before a result can be returned."""


class MetadataSerializationError(IndexBuildError, ValueError):
    """Raised when structured metadata cannot be safely represented in Chroma."""


@dataclass(frozen=True)
class IndexBuildResult:
    """Sanitized outcome and measurements for one operational build attempt."""

    build_id: str
    artifact_root: Path
    report_path: Path
    index_fingerprint: str
    document_manifest_hash: str
    status: BuildStatus
    extracted_pages: int
    text_chunks_prepared: int
    text_vectors_written: int
    image_objects_extracted: int
    image_vectors_written: int
    image_failures: int
    caption_fallbacks: int
    text_embedding_seconds: float
    image_embedding_seconds: float
    total_elapsed_seconds: float
    extraction_failures: tuple[dict[str, str], ...]
    warnings: tuple[str, ...]

    def to_serializable_dict(self) -> dict[str, Any]:
        return {
            "build_id": self.build_id,
            "artifact_root": str(self.artifact_root),
            "report_path": str(self.report_path),
            "index_fingerprint": self.index_fingerprint,
            "document_manifest_hash": self.document_manifest_hash,
            "status": self.status,
            "extracted_pages": self.extracted_pages,
            "text_chunks_prepared": self.text_chunks_prepared,
            "text_vectors_written": self.text_vectors_written,
            "image_objects_extracted": self.image_objects_extracted,
            "image_vectors_written": self.image_vectors_written,
            "image_failures": self.image_failures,
            "caption_fallbacks": self.caption_fallbacks,
            "text_embedding_seconds": self.text_embedding_seconds,
            "image_embedding_seconds": self.image_embedding_seconds,
            "total_elapsed_seconds": self.total_elapsed_seconds,
            "extraction_failures": list(self.extraction_failures),
            "warnings": list(self.warnings),
        }


def serialize_index_metadata(metadata: dict[str, Any]) -> dict[str, str | int | float | bool]:
    """Serialize research metadata using its established Chroma conventions.

    ``None`` becomes an empty string, scalar values remain scalar, and lists,
    tuples, and dictionaries become deterministic JSON strings.  Other values
    are rejected rather than coerced into an opaque representation.
    """

    if not isinstance(metadata, dict):
        raise MetadataSerializationError("index metadata must be a dictionary")
    result: dict[str, str | int | float | bool] = {}
    for key, value in metadata.items():
        if not isinstance(key, str) or not key.strip():
            raise MetadataSerializationError("index metadata keys must be non-empty strings")
        if value is None:
            result[key] = ""
        elif isinstance(value, bool):
            result[key] = value
        elif isinstance(value, (str, int)):
            result[key] = value
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise MetadataSerializationError(f"metadata value for {key!r} must be finite")
            result[key] = value
        elif isinstance(value, (list, tuple, dict)):
            try:
                result[key] = json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            except (TypeError, ValueError) as exc:
                raise MetadataSerializationError(
                    f"metadata value for {key!r} is not JSON serializable"
                ) from exc
        else:
            raise MetadataSerializationError(
                f"metadata value for {key!r} has unsupported type {type(value).__name__}"
            )
    return result


def _validate_adapter_configuration(
    text_adapter: Any | None,
    image_adapter: Any | None,
    config: ResolvedRunConfig,
) -> None:
    if text_adapter is not None:
        adapter_config = getattr(text_adapter, "config", None)
        if (
            getattr(text_adapter, "model_name", None) != config.text_embed_model
            or getattr(adapter_config, "text_embed_batch_size", None) != config.text_embed_batch_size
        ):
            raise IndexBuildError("injected text adapter configuration does not match the RunPlan")
    if image_adapter is not None:
        adapter_config = getattr(image_adapter, "config", None)
        if (
            getattr(image_adapter, "model_name", None) != config.image_embed_model
            or getattr(image_adapter, "pretrained", None) != config.image_embed_pretrained
            or getattr(adapter_config, "image_embed_batch_size", None) != config.image_embed_batch_size
            or getattr(adapter_config, "image_caption_image_weight", None)
            != config.image_caption_image_weight
        ):
            raise IndexBuildError("injected image adapter configuration does not match the RunPlan")


def _validate_build_paths(storage_root: str | Path, input_dir: str | Path, config: ResolvedRunConfig) -> Path:
    owned_root, _ = resolve_disjoint_paths(
        storage_root,
        config.pdf_dir if str(config.pdf_dir) else input_dir,
        first_label="index-build storage root",
        second_label="configured PDF source root",
    )
    # The actual input is checked as well because callers may intentionally
    # provide a source directory different from the configured legacy path.
    resolve_disjoint_paths(
        input_dir,
        owned_root,
        first_label="PDF source root",
        second_label="index-build storage root",
    )
    return owned_root


def _build_artifact_paths(owned_root: Path, build_id: str) -> tuple[Path, Path]:
    artifact_root = resolve_owned_path(owned_root, Path("build-artifacts") / build_id)
    image_root = resolve_owned_path(artifact_root, "images")
    for namespace in ("snapshots", "indexes"):
        reserved = resolve_owned_path(owned_root, namespace)
        if image_root == reserved or image_root.is_relative_to(reserved):
            raise PathContainmentError(
                f"build image output enters reserved {namespace} namespace: {image_root}"
            )
    return artifact_root, image_root


def _text_records(chunks: list[Any]) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks):
        content = getattr(chunk, "page_content", None)
        metadata = getattr(chunk, "metadata", None)
        if not isinstance(content, str) or not content.strip():
            raise IndexBuildError(f"text chunk at index {index} has invalid page_content")
        if not isinstance(metadata, dict):
            raise IndexBuildError(f"text chunk at index {index} has invalid metadata")
        chunk_id = metadata.get("chunk_id")
        if not isinstance(chunk_id, str) or not chunk_id.strip():
            raise IndexBuildError(f"text chunk at index {index} has no stable chunk_id")
        ids.append(chunk_id)
        documents.append(content)
        metadatas.append(serialize_index_metadata(metadata))
    if len(ids) != len(set(ids)):
        raise IndexBuildError("text chunk IDs are not unique")
    return ids, documents, metadatas


def _image_records(image_objects: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    ids: list[str] = []
    metadata: list[dict[str, Any]] = []
    for index, image in enumerate(image_objects):
        image_id = image.get("image_id")
        if not isinstance(image_id, str) or not image_id.strip():
            raise IndexBuildError(f"image object at index {index} has no stable image_id")
        ids.append(image_id)
        metadata.append(serialize_index_metadata({
            "image_id": image_id,
            "source": image.get("source", ""),
            "page_num": image.get("page_num", ""),
            "image_path": image.get("path", ""),
            "caption_text": image.get("caption_text"),
            "bbox": image.get("bbox"),
        }))
    if len(ids) != len(set(ids)):
        raise IndexBuildError("image IDs are not unique")
    return ids, metadata


def _write_report(result: IndexBuildResult) -> None:
    result.artifact_root.mkdir(parents=True, exist_ok=True)
    temporary = result.report_path.parent / f".{result.report_path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(result.to_serializable_dict(), stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, result.report_path)
    except OSError as exc:
        if temporary.exists():
            temporary.unlink()
        raise IndexBuildError("could not write the index-build report") from exc


def _failure_result(
    *,
    build_id: str,
    artifact_root: Path,
    report_path: Path,
    plan: RunPlan,
    started: float,
    extraction: PdfExtractionResult | None = None,
    text_chunks: int = 0,
    text_written: int = 0,
    image_objects: int = 0,
    image_written: int = 0,
    image_failures: int = 0,
    caption_fallbacks: int = 0,
    text_seconds: float = 0.0,
    image_seconds: float = 0.0,
    warnings: tuple[str, ...] = (),
) -> IndexBuildResult:
    failures = tuple(
        {
            "source": item.get("source", "unknown"),
            "error_type": item.get("error_type", "ExtractionError"),
            "message": "PDF extraction failed",
        }
        for item in (extraction.failed_files if extraction else [])
    )
    result = IndexBuildResult(
        build_id=build_id,
        artifact_root=artifact_root,
        report_path=report_path,
        index_fingerprint=plan.index_fingerprint,
        document_manifest_hash=plan.document_manifest_hash,
        status="failed",
        extracted_pages=len(extraction.pages) if extraction else 0,
        text_chunks_prepared=text_chunks,
        text_vectors_written=text_written,
        image_objects_extracted=image_objects,
        image_vectors_written=image_written,
        image_failures=image_failures,
        caption_fallbacks=caption_fallbacks,
        text_embedding_seconds=text_seconds,
        image_embedding_seconds=image_seconds,
        total_elapsed_seconds=max(0.0, perf_counter() - started),
        extraction_failures=failures,
        warnings=warnings,
    )
    _write_report(result)
    return result


def build_index(
    run_plan: RunPlan,
    input_dir: str | Path,
    storage_root: str | Path,
    *,
    text_adapter: Any | None = None,
    image_adapter: Any | None = None,
) -> IndexBuildResult:
    """Build one plan-bound text/image index without active-index promotion."""

    started = perf_counter()
    if not isinstance(run_plan, RunPlan):
        raise TypeError("build_index requires a RunPlan")
    run_plan.verify_integrity()
    config = run_plan.resolved_config()
    owned_root = _validate_build_paths(storage_root, input_dir, config)
    manifest = build_document_manifest(input_dir)
    if manifest.content_hash != run_plan.document_manifest_hash:
        raise ManifestMismatchError(
            "run-plan document_manifest_hash does not match source directory: "
            f"plan={run_plan.document_manifest_hash}, current={manifest.content_hash}"
        )
    _validate_adapter_configuration(text_adapter, image_adapter, config)

    build_id = uuid.uuid4().hex
    artifact_root, image_root = _build_artifact_paths(owned_root, build_id)
    report_path = artifact_root / "build-report.json"
    extraction: PdfExtractionResult | None = None
    try:
        extraction = extract_pdfs(
            run_plan,
            input_dir,
            storage_root,
            image_output_root=image_root,
        )
        if extraction.failed_files:
            return _failure_result(
                build_id=build_id,
                artifact_root=artifact_root,
                report_path=report_path,
                plan=run_plan,
                started=started,
                extraction=extraction,
            )

        chunks = bbox_chunker(extraction.pages, config)
        text_ids, text_documents, text_metadata = _text_records(chunks)
        image_objects = build_image_objects(extraction.pages)
        image_ids: list[str] = []
        image_metadata: list[dict[str, Any]] = []
        image_result: ImageEmbeddingBatchResult | None = None
        text_result: EmbeddingBatchResult | None = None

        if text_ids:
            adapter = text_adapter or TextEmbeddingAdapter(config)
            text_result = adapter.embed_documents(chunks)
            if text_result.embeddings.shape[0] != len(text_ids):
                raise IndexBuildError("text embedding rows do not align with prepared text records")
        if image_objects:
            adapter = image_adapter or ImageEmbeddingAdapter(config)
            image_result = adapter.embed_images(image_objects)
            successful_images = [dict(item) for item in image_result.image_objects]
            image_ids, image_metadata = _image_records(successful_images)
            if image_result.embeddings.shape[0] != len(image_ids):
                raise IndexBuildError("image embedding rows do not align with prepared image records")

        text_seconds = text_result.elapsed_time if text_result else 0.0
        image_seconds = image_result.elapsed_time if image_result else 0.0
        warnings = tuple(
            f"{issue.kind} issue for {issue.identifier}: {issue.message}"
            for issue in (image_result.issues if image_result else ())
        )
        store = None
        text_written = image_written = 0
        try:
            store = ChromaVectorStore.open(storage_root, run_plan)
            if text_result is not None:
                store.upsert_text(text_ids, text_result.embeddings, text_documents, text_metadata)
                text_written = len(text_ids)
            if image_result is not None and image_ids:
                store.upsert_image(image_ids, image_result.embeddings, image_metadata)
                image_written = len(image_ids)
            if store.count("text") != len(text_ids) or set(store.ids("text")) != set(text_ids):
                raise IndexBuildError("stored text collection does not match prepared records")
            if store.count("image") != len(image_ids) or set(store.ids("image")) != set(image_ids):
                raise IndexBuildError("stored image collection does not match prepared records")
        except Exception:
            return _failure_result(
                build_id=build_id,
                artifact_root=artifact_root,
                report_path=report_path,
                plan=run_plan,
                started=started,
                extraction=extraction,
                text_chunks=len(text_ids),
                text_written=text_written,
                image_objects=len(image_objects),
                image_written=image_written,
                image_failures=image_result.failed_loads if image_result else 0,
                caption_fallbacks=image_result.caption_fallbacks if image_result else 0,
                text_seconds=text_seconds,
                image_seconds=image_seconds,
                warnings=warnings + ("vector persistence failed; partial writes may exist",),
            )
        finally:
            if store is not None:
                store.close()

        image_failures = image_result.failed_loads if image_result else 0
        caption_fallbacks = image_result.caption_fallbacks if image_result else 0
        status: BuildStatus = (
            "completed_with_warnings"
            if image_failures or caption_fallbacks or warnings
            else "completed"
        )
        result = IndexBuildResult(
            build_id=build_id,
            artifact_root=artifact_root,
            report_path=report_path,
            index_fingerprint=run_plan.index_fingerprint,
            document_manifest_hash=run_plan.document_manifest_hash,
            status=status,
            extracted_pages=len(extraction.pages),
            text_chunks_prepared=len(text_ids),
            text_vectors_written=text_written,
            image_objects_extracted=len(image_objects),
            image_vectors_written=len(image_ids),
            image_failures=image_failures,
            caption_fallbacks=caption_fallbacks,
            text_embedding_seconds=text_seconds,
            image_embedding_seconds=image_seconds,
            total_elapsed_seconds=max(0.0, perf_counter() - started),
            extraction_failures=(),
            warnings=warnings,
        )
        _write_report(result)
        return result
    except (IndexBuildError, ManifestMismatchError):
        raise
    except Exception as exc:
        if extraction is not None:
            return _failure_result(
                build_id=build_id,
                artifact_root=artifact_root,
                report_path=report_path,
                plan=run_plan,
                started=started,
                extraction=extraction,
                warnings=("index build failed after execution began",),
            )
        raise IndexBuildError("index build failed before a report could be prepared") from exc
