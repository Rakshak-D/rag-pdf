"""Verified, semantic-only retrieval over an existing local index."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from backend.config.paths import PathContainmentError, resolve_owned_path
from backend.embeddings.image import ImageEmbeddingAdapter
from backend.embeddings.text import TextEmbeddingAdapter
from backend.experiments.run_plan import RunPlan
from backend.vectorstore import ChromaVectorStore


Modality = Literal["text", "image"]
REPORT_SCHEMA_VERSION = "1"
_SHA256_LENGTH = 64


class SemanticRetrievalError(RuntimeError):
    """Base error for report validation, store access, or semantic querying."""


class RetrievalInputError(SemanticRetrievalError, ValueError):
    """Raised for invalid query, modality, or top-k inputs."""


class RetrievalReportError(SemanticRetrievalError):
    """Raised when a build report is missing, malformed, or not successful."""


class RetrievalStoreError(SemanticRetrievalError):
    """Raised when the referenced existing index cannot be opened or checked."""


class RetrievalQueryError(SemanticRetrievalError):
    """Raised when query embedding or result validation fails."""


@dataclass(frozen=True)
class TextSemanticHit:
    chunk_id: str
    rank: int
    distance: float
    similarity: float
    document: str
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class ImageSemanticHit:
    image_id: str
    rank: int
    distance: float
    similarity: float
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class SemanticRetrievalResult:
    query_id: str
    index_fingerprint: str
    document_manifest_hash: str
    modalities: tuple[Modality, ...]
    effective_text_k: int | None
    effective_image_k: int | None
    text_hits: tuple[TextSemanticHit, ...]
    image_hits: tuple[ImageSemanticHit, ...]
    modality_timings: tuple[tuple[Modality, float], ...]
    total_elapsed_seconds: float
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class _SuccessfulBuildReport:
    build_id: str
    index_fingerprint: str
    document_manifest_hash: str
    text_records_persisted: int
    image_records_persisted: int


def _sha256_hash(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or len(value) != _SHA256_LENGTH:
        raise RetrievalReportError(f"report {label} must be a 64-character SHA-256 hash")
    lowered = value.lower()
    if any(char not in "0123456789abcdef" for char in lowered):
        raise RetrievalReportError(f"report {label} must be hexadecimal")
    return lowered


def _nonnegative_count(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RetrievalReportError(f"report {key} must be a non-negative integer")
    return value


def _validate_build_report(storage_root: str | Path, report_path: str | Path, plan: RunPlan) -> _SuccessfulBuildReport:
    owned_root = Path(storage_root).expanduser().resolve(strict=False)
    try:
        resolved_report = resolve_owned_path(owned_root, report_path)
    except PathContainmentError as exc:
        raise RetrievalReportError("build report must remain inside storage root") from exc
    if resolved_report.name != "build-report.json":
        raise RetrievalReportError("build report must be named build-report.json")
    try:
        relative = resolved_report.relative_to(owned_root)
    except ValueError as exc:
        raise RetrievalReportError("build report is outside storage root") from exc
    parts = relative.parts
    if len(parts) != 3 or parts[0] != "build-artifacts" or parts[2] != "build-report.json":
        raise RetrievalReportError(
            "build report must be directly under storage_root/build-artifacts/<build-id>"
        )
    build_id = parts[1]
    if not build_id or not resolved_report.is_file():
        raise RetrievalReportError("build report does not exist")
    try:
        payload = json.loads(resolved_report.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RetrievalReportError("build report is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise RetrievalReportError("build report must contain a JSON object")
    if payload.get("report_schema_version") != REPORT_SCHEMA_VERSION:
        raise RetrievalReportError("unsupported build report schema version")
    if payload.get("build_id") != build_id:
        raise RetrievalReportError("build report build_id does not match its artifact directory")
    for key in ("artifact_root", "report_path"):
        value = payload.get(key)
        if not isinstance(value, str):
            raise RetrievalReportError(f"build report {key} must be a path string")
        try:
            resolved_value = resolve_owned_path(owned_root, value)
        except PathContainmentError as exc:
            raise RetrievalReportError(f"build report {key} escapes storage root") from exc
        expected = resolved_report.parent if key == "artifact_root" else resolved_report
        if resolved_value != expected:
            raise RetrievalReportError(f"build report {key} does not match its artifact location")
    if payload.get("status") not in {"completed", "completed_with_warnings"}:
        raise RetrievalReportError("build report is not a successful build")
    if payload.get("partial_persistence") is not False:
        raise RetrievalReportError("build report indicates possible partial vector persistence")
    index_fingerprint = _sha256_hash(payload.get("index_fingerprint"), label="index_fingerprint")
    document_manifest_hash = _sha256_hash(
        payload.get("document_manifest_hash"), label="document_manifest_hash"
    )
    if index_fingerprint != plan.index_fingerprint:
        raise RetrievalReportError("build report index fingerprint does not match the RunPlan")
    if document_manifest_hash != plan.document_manifest_hash:
        raise RetrievalReportError("build report document manifest does not match the RunPlan")
    return _SuccessfulBuildReport(
        build_id=build_id,
        index_fingerprint=index_fingerprint,
        document_manifest_hash=document_manifest_hash,
        text_records_persisted=_nonnegative_count(
            payload, "text_records_persisted"
        ),
        image_records_persisted=_nonnegative_count(
            payload, "image_records_persisted"
        ),
    )


def _validate_query(query: Any) -> str:
    if not isinstance(query, str) or not query.strip():
        raise RetrievalInputError("query must be a non-empty string")
    return query.strip()


def _validate_modalities(modalities: Any) -> tuple[Modality, ...]:
    if isinstance(modalities, (str, bytes, bytearray)):
        raise RetrievalInputError("modalities must be a sequence containing text and/or image")
    try:
        values = tuple(modalities)
    except TypeError as exc:
        raise RetrievalInputError("modalities must be a sequence containing text and/or image") from exc
    if not values or any(value not in {"text", "image"} for value in values):
        raise RetrievalInputError("modalities may contain only 'text' and 'image'")
    if len(values) != len(set(values)):
        raise RetrievalInputError("modalities must not contain duplicates")
    return values  # type: ignore[return-value]


def _validate_k(value: Any, *, label: str, default: int) -> int:
    effective = default if value is None else value
    if isinstance(effective, bool) or not isinstance(effective, int) or effective <= 0:
        raise RetrievalInputError(f"{label} must be a positive integer")
    return effective


def _validate_adapter_configuration(adapter: Any | None, config: Any, *, modality: Modality) -> None:
    if adapter is None:
        return
    if modality == "text":
        adapter_config = getattr(adapter, "config", None)
        valid = (
            getattr(adapter, "model_name", None) == config.text_embed_model
            and getattr(adapter_config, "text_embed_batch_size", None) == config.text_embed_batch_size
        )
    else:
        adapter_config = getattr(adapter, "config", None)
        valid = (
            getattr(adapter, "model_name", None) == config.image_embed_model
            and getattr(adapter, "pretrained", None) == config.image_embed_pretrained
            and getattr(adapter_config, "image_embed_batch_size", None) == config.image_embed_batch_size
        )
    if not valid:
        raise RetrievalInputError(f"injected {modality} adapter configuration does not match the RunPlan")


def _decode_metadata(metadata: Any) -> dict[str, Any]:
    if not isinstance(metadata, Mapping):
        raise RetrievalQueryError("retrieval metadata must be an object")
    result = dict(metadata)
    for key in ("bbox", "related_image_ids", "related_image_bboxes"):
        value = result.get(key)
        if isinstance(value, str):
            try:
                result[key] = json.loads(value)
            except json.JSONDecodeError:
                # Preserve the stored scalar rather than inventing a value.
                pass
    return result


def _outer_query_values(result: Any, key: str) -> list[Any]:
    if not isinstance(result, Mapping):
        raise RetrievalQueryError("Chroma returned a non-object result")
    values = result.get(key)
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], list):
        raise RetrievalQueryError(f"Chroma result field {key} has an invalid shape")
    return values[0]


def _distance(value: Any) -> tuple[float, float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RetrievalQueryError("Chroma returned a non-numeric distance")
    distance = float(value)
    if not math.isfinite(distance):
        raise RetrievalQueryError("Chroma returned a non-finite distance")
    similarity = 1.0 - distance
    if not math.isfinite(similarity):
        raise RetrievalQueryError("derived cosine similarity is not finite")
    return distance, similarity


def _text_hits(result: Any) -> tuple[TextSemanticHit, ...]:
    ids = _outer_query_values(result, "ids")
    documents = _outer_query_values(result, "documents")
    metadata = _outer_query_values(result, "metadatas")
    distances = _outer_query_values(result, "distances")
    if not (len(ids) == len(documents) == len(metadata) == len(distances)):
        raise RetrievalQueryError("text result arrays are not aligned")
    hits: list[TextSemanticHit] = []
    for index, (identifier, document, item, raw_distance) in enumerate(
        zip(ids, documents, metadata, distances)
    ):
        if not isinstance(identifier, str) or not identifier.strip():
            raise RetrievalQueryError("text result contains an invalid ID")
        if not isinstance(document, str):
            raise RetrievalQueryError("text result contains invalid document content")
        distance, similarity = _distance(raw_distance)
        hits.append(
            TextSemanticHit(identifier, index + 1, distance, similarity, document, _decode_metadata(item))
        )
    return tuple(hits)


def _image_hits(result: Any) -> tuple[ImageSemanticHit, ...]:
    ids = _outer_query_values(result, "ids")
    metadata = _outer_query_values(result, "metadatas")
    distances = _outer_query_values(result, "distances")
    if not (len(ids) == len(metadata) == len(distances)):
        raise RetrievalQueryError("image result arrays are not aligned")
    hits: list[ImageSemanticHit] = []
    for index, (identifier, item, raw_distance) in enumerate(zip(ids, metadata, distances)):
        if not isinstance(identifier, str) or not identifier.strip():
            raise RetrievalQueryError("image result contains an invalid ID")
        distance, similarity = _distance(raw_distance)
        hits.append(
            ImageSemanticHit(identifier, index + 1, distance, similarity, _decode_metadata(item))
        )
    return tuple(hits)


def _query_id(query: str, plan: RunPlan, modalities: tuple[Modality, ...], text_k: int | None, image_k: int | None) -> str:
    payload = json.dumps(
        {
            "query": query,
            "index_fingerprint": plan.index_fingerprint,
            "modalities": modalities,
            "text_k": text_k,
            "image_k": image_k,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def retrieve_semantic(
    query: str,
    run_plan: RunPlan,
    storage_root: str | Path,
    *,
    build_report_path: str | Path,
    modalities: Sequence[str] = ("text", "image"),
    text_adapter: TextEmbeddingAdapter | None = None,
    image_adapter: ImageEmbeddingAdapter | None = None,
    text_k: int | None = None,
    image_k: int | None = None,
) -> SemanticRetrievalResult:
    """Retrieve semantic hits only from the exact successful build report."""

    started = perf_counter()
    if not isinstance(run_plan, RunPlan):
        raise TypeError("retrieve_semantic requires a RunPlan")
    run_plan.verify_integrity()
    config = run_plan.resolved_config()
    normalized_query = _validate_query(query)
    requested = _validate_modalities(modalities)
    requested_text_k = _validate_k(text_k, label="text_k", default=config.text_k) if "text" in requested else None
    requested_image_k = _validate_k(image_k, label="image_k", default=config.image_k) if "image" in requested else None
    report = _validate_build_report(storage_root, build_report_path, run_plan)
    _validate_adapter_configuration(text_adapter, config, modality="text")
    _validate_adapter_configuration(image_adapter, config, modality="image")

    store: ChromaVectorStore | None = None
    try:
        try:
            store = ChromaVectorStore.open_existing(storage_root, run_plan)
        except Exception as exc:
            raise RetrievalStoreError("could not open the referenced existing index") from exc
        try:
            text_count = store.count("text")
            image_count = store.count("image")
        except Exception as exc:
            raise RetrievalStoreError("could not read persisted collection counts") from exc
        if text_count != report.text_records_persisted:
            raise RetrievalStoreError("text collection count does not match the successful build report")
        if image_count != report.image_records_persisted:
            raise RetrievalStoreError("image collection count does not match the successful build report")

        text_hits: tuple[TextSemanticHit, ...] = ()
        image_hits: tuple[ImageSemanticHit, ...] = ()
        timings: list[tuple[Modality, float]] = []
        if "text" in requested and text_count:
            query_started = perf_counter()
            try:
                adapter = text_adapter or TextEmbeddingAdapter(config)
                vector = adapter.embed_query(normalized_query)
                raw = store.query_text(vector, min(requested_text_k, text_count))
                text_hits = _text_hits(raw)
            except SemanticRetrievalError:
                raise
            except Exception as exc:
                raise RetrievalQueryError("text semantic retrieval failed") from exc
            timings.append(("text", max(0.0, perf_counter() - query_started)))
        elif "text" in requested:
            timings.append(("text", 0.0))
        if "image" in requested and image_count:
            query_started = perf_counter()
            try:
                adapter = image_adapter or ImageEmbeddingAdapter(config)
                vector = adapter.embed_query(normalized_query)
                raw = store.query_image(vector, min(requested_image_k, image_count))
                image_hits = _image_hits(raw)
            except SemanticRetrievalError:
                raise
            except Exception as exc:
                raise RetrievalQueryError("image semantic retrieval failed") from exc
            timings.append(("image", max(0.0, perf_counter() - query_started)))
        elif "image" in requested:
            timings.append(("image", 0.0))
    finally:
        if store is not None:
            store.close()

    return SemanticRetrievalResult(
        query_id=_query_id(normalized_query, run_plan, requested, requested_text_k, requested_image_k),
        index_fingerprint=run_plan.index_fingerprint,
        document_manifest_hash=run_plan.document_manifest_hash,
        modalities=requested,
        effective_text_k=min(requested_text_k, report.text_records_persisted) if requested_text_k is not None else None,
        effective_image_k=min(requested_image_k, report.image_records_persisted) if requested_image_k is not None else None,
        text_hits=text_hits,
        image_hits=image_hits,
        modality_timings=tuple(timings),
        total_elapsed_seconds=max(0.0, perf_counter() - started),
    )
