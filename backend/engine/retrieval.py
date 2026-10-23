"""Verified, semantic-only retrieval over an existing local index."""

from __future__ import annotations

from dataclasses import dataclass, replace
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
from backend.engine.bm25 import BM25Index, BM25Error
from backend.engine.fusion import FusionEngine, FusionError
from backend.engine.cross_modal import apply_cross_modal_boosts
from backend.engine.reranking import CrossEncoderReranker, RerankerError


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

class HybridRetrievalError(SemanticRetrievalError):
    """Raised when lexical/semantic hybrid retrieval cannot complete."""


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
    text_embedding_seconds: object | None = None
    image_embedding_seconds: object | None = None
    text_records_persisted: int | None = None
    image_records_persisted: int | None = None

@dataclass(frozen=True)
class HybridHit:
    record_id: str
    rank: int
    fused_score: float
    metadata: Mapping[str, Any]
    document: str | None = None
    caption: str | None = None
    bm25_rank: int | None = None
    bm25_score: float | None = None
    semantic_rank: int | None = None
    semantic_distance: float | None = None
    semantic_similarity: float | None = None
    sources: tuple[str, ...] = ()
    cross_modal_boost: float = 0.0
    boosted_fused_score: float | None = None
    effective_distance: float | None = None
    effective_similarity: float | None = None
    reranker_score: float | None = None
    reranker_normalized_score: float | None = None
    normalized_fused_score: float | None = None
    normalized_cross_modal_boost: float | None = None
    final_rank_score: float | None = None
    reranker_applied: bool = False

@dataclass(frozen=True)
class HybridRetrievalResult:
    query_id: str
    index_fingerprint: str
    document_manifest_hash: str
    modalities: tuple[Modality, ...]
    effective_text_k: int | None
    effective_image_k: int | None
    text_hits: tuple[HybridHit, ...]
    image_hits: tuple[HybridHit, ...]
    fusion_strategy: str
    effective_weights: tuple[float, float]
    diagnostics: Mapping[str, Any]
    modality_timings: tuple[tuple[Modality, float], ...]
    total_elapsed_seconds: float
    cross_modal_elapsed_seconds: float = 0.0
    reranker_elapsed_seconds: float = 0.0
    warnings: tuple[str, ...] = ()
    query_text: str = ""
    text_embedding_seconds: object | None = None
    image_embedding_seconds: object | None = None
    text_records_persisted: int | None = None
    image_records_persisted: int | None = None


@dataclass(frozen=True)
class _SuccessfulBuildReport:
    build_id: str
    index_fingerprint: str
    document_manifest_hash: str
    text_records_persisted: int
    image_records_persisted: int
    text_embedding_seconds: object | None = None
    image_embedding_seconds: object | None = None


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
        text_embedding_seconds=payload.get("text_embedding_seconds"),
        image_embedding_seconds=payload.get("image_embedding_seconds"),
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


def query_id_for(query: str, plan: RunPlan, modalities: tuple[Modality, ...], text_k: int | None, image_k: int | None) -> str:
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


_query_id = query_id_for


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
        query_id=query_id_for(normalized_query, run_plan, requested, requested_text_k, requested_image_k),
        index_fingerprint=run_plan.index_fingerprint,
        document_manifest_hash=run_plan.document_manifest_hash,
        modalities=requested,
        effective_text_k=min(requested_text_k, report.text_records_persisted) if requested_text_k is not None else None,
        effective_image_k=min(requested_image_k, report.image_records_persisted) if requested_image_k is not None else None,
        text_hits=text_hits,
        image_hits=image_hits,
        modality_timings=tuple(timings),
        total_elapsed_seconds=max(0.0, perf_counter() - started),
        text_embedding_seconds=report.text_embedding_seconds,
        image_embedding_seconds=report.image_embedding_seconds,
        text_records_persisted=report.text_records_persisted,
        image_records_persisted=report.image_records_persisted,
    )


def _semantic_result_to_hybrid(
    query: str,
    run_plan: RunPlan,
    result: SemanticRetrievalResult,
) -> HybridRetrievalResult:
    """Normalize semantic-only hits into the downstream hybrid contract."""

    text_hits = tuple(
        HybridHit(
            record_id=hit.chunk_id,
            rank=hit.rank,
            fused_score=hit.similarity,
            metadata=hit.metadata,
            document=hit.document,
            semantic_rank=hit.rank,
            semantic_distance=hit.distance,
            semantic_similarity=hit.similarity,
            sources=("semantic",),
        )
        for hit in result.text_hits
    )
    image_hits = tuple(
        HybridHit(
            record_id=hit.image_id,
            rank=hit.rank,
            fused_score=hit.similarity,
            metadata=hit.metadata,
            caption=hit.metadata.get("caption_text") if isinstance(hit.metadata.get("caption_text"), str) else None,
            semantic_rank=hit.rank,
            semantic_distance=hit.distance,
            semantic_similarity=hit.similarity,
            sources=("semantic",),
        )
        for hit in result.image_hits
    )
    return HybridRetrievalResult(
        query_id=query_id_for(query, run_plan, result.modalities, result.effective_text_k, result.effective_image_k),
        index_fingerprint=result.index_fingerprint,
        document_manifest_hash=result.document_manifest_hash,
        modalities=result.modalities,
        effective_text_k=result.effective_text_k,
        effective_image_k=result.effective_image_k,
        text_hits=text_hits,
        image_hits=image_hits,
        fusion_strategy="semantic",
        effective_weights=(0.0, 1.0),
        diagnostics={"mode": "semantic"},
        modality_timings=result.modality_timings,
        total_elapsed_seconds=result.total_elapsed_seconds,
        warnings=result.warnings,
        query_text=query,
        text_embedding_seconds=result.text_embedding_seconds,
        image_embedding_seconds=result.image_embedding_seconds,
        text_records_persisted=result.text_records_persisted,
        image_records_persisted=result.image_records_persisted,
    )


def _bm25_statistics(stats: Any) -> dict[str, float | int | bool]:
    if not isinstance(stats, Mapping):
        return {}
    result: dict[str, float | int | bool] = {}
    for key in ("query_length_tokens", "expanded_tokens", "non_zero_docs"):
        value = stats.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            continue
        result[key] = value
    for key in ("max_score", "mean_score", "min_score", "std_score", "corpus_coverage"):
        value = stats.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            continue
        result[key] = float(value)
    if isinstance(stats.get("used_overlap_fallback"), bool):
        result["used_overlap_fallback"] = stats["used_overlap_fallback"]
    return result


def _retrieve_bm25(
    query: str,
    run_plan: RunPlan,
    storage_root: str | Path,
    *,
    build_report_path: str | Path,
    modalities: Sequence[str],
    text_k: int | None,
    image_k: int | None,
) -> HybridRetrievalResult:
    """Retrieve lexical candidates from the verified existing index only."""

    started = perf_counter()
    if not isinstance(run_plan, RunPlan):
        raise TypeError("retrieve_for_plan requires a RunPlan")
    run_plan.verify_integrity()
    config = run_plan.resolved_config()
    normalized_query = _validate_query(query)
    requested = _validate_modalities(modalities)
    requested_text_k = _validate_k(text_k, label="text_k", default=config.text_k) if "text" in requested else None
    requested_image_k = _validate_k(image_k, label="image_k", default=config.image_k) if "image" in requested else None
    report = _validate_build_report(storage_root, build_report_path, run_plan)
    store: ChromaVectorStore | None = None
    text_hits: tuple[HybridHit, ...] = ()
    image_hits: tuple[HybridHit, ...] = ()
    timings: list[tuple[Modality, float]] = []
    diagnostics: dict[str, Any] = {"mode": "bm25"}
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
        if text_count != report.text_records_persisted or image_count != report.image_records_persisted:
            raise RetrievalReportError("persisted collection counts do not match the successful build report")
        effective_text_k = min(requested_text_k, text_count) if requested_text_k is not None else None
        effective_image_k = min(requested_image_k, image_count) if requested_image_k is not None else None
        for modality, count, limit in (("text", text_count, effective_text_k), ("image", image_count, effective_image_k)):
            if modality not in requested:
                continue
            query_started = perf_counter()
            try:
                records = store.read_records(modality)
                documents = [
                    ((document or "") if modality == "text" else str(metadata.get("caption_text") or identifier))
                    for identifier, document, metadata in zip(records.ids, records.documents, records.metadatas)
                ]
            except Exception as exc:
                raise RetrievalStoreError(f"could not read {modality} records") from exc
            try:
                index = BM25Index(documents, records.ids, records.metadatas)
                response = index.query(normalized_query, limit or 1)
            except BM25Error as exc:
                raise HybridRetrievalError(f"{modality} BM25 retrieval failed") from exc
            except Exception as exc:
                raise HybridRetrievalError(f"{modality} BM25 retrieval failed") from exc
            if modality == "text":
                hits: list[HybridHit] = []
                for rank, (identifier, document, metadata, score) in enumerate(zip(response["ids"][0], response["documents"][0], response["metadatas"][0], response["scores"]), 1):
                    if not isinstance(score, (int, float)) or isinstance(score, bool) or not math.isfinite(float(score)):
                        raise RetrievalQueryError("BM25 returned an invalid score")
                    hits.append(HybridHit(identifier, rank, float(score), _decode_metadata(metadata), document=document, bm25_rank=rank, bm25_score=float(score), sources=("bm25",)))
                text_hits = tuple(hits)
            else:
                hits = []
                for rank, (identifier, metadata, score) in enumerate(zip(response["ids"][0], response["metadatas"][0], response["scores"]), 1):
                    if not isinstance(score, (int, float)) or isinstance(score, bool) or not math.isfinite(float(score)):
                        raise RetrievalQueryError("BM25 returned an invalid score")
                    decoded = _decode_metadata(metadata)
                    caption = decoded.get("caption_text") if isinstance(decoded.get("caption_text"), str) else None
                    hits.append(HybridHit(identifier, rank, float(score), decoded, caption=caption, bm25_rank=rank, bm25_score=float(score), sources=("bm25",)))
                image_hits = tuple(hits)
            timings.append((modality, max(0.0, perf_counter() - query_started)))
            diagnostics[modality] = {"mode": "bm25", "result_count": len(response["ids"][0]), "bm25_stats": _bm25_statistics(response.get("bm25_stats"))}
        return HybridRetrievalResult(
            query_id=query_id_for(normalized_query, run_plan, requested, effective_text_k, effective_image_k),
            index_fingerprint=run_plan.index_fingerprint,
            document_manifest_hash=run_plan.document_manifest_hash,
            modalities=requested,
            effective_text_k=effective_text_k,
            effective_image_k=effective_image_k,
            text_hits=text_hits,
            image_hits=image_hits,
            fusion_strategy="bm25",
            effective_weights=(1.0, 0.0),
            diagnostics=diagnostics,
            modality_timings=tuple(timings),
            total_elapsed_seconds=max(0.0, perf_counter() - started),
            query_text=normalized_query,
            text_embedding_seconds=report.text_embedding_seconds,
            image_embedding_seconds=report.image_embedding_seconds,
            text_records_persisted=report.text_records_persisted,
            image_records_persisted=report.image_records_persisted,
        )
    finally:
        if store is not None:
            store.close()


def retrieve_for_plan(
    query: str,
    run_plan: RunPlan,
    storage_root: str | Path,
    *,
    build_report_path: str | Path,
    modalities: Sequence[str] = ("text", "image"),
    text_adapter: Any = None,
    image_adapter: Any = None,
    text_k: int | None = None,
    image_k: int | None = None,
    reranker_adapter: CrossEncoderReranker | None = None,
) -> HybridRetrievalResult:
    """Dispatch retrieval according to the verified RunPlan mode."""

    if not isinstance(run_plan, RunPlan):
        raise TypeError("retrieve_for_plan requires a RunPlan")
    run_plan.verify_integrity()
    mode = run_plan.resolved_config().retrieval_mode
    normalized_query = _validate_query(query)
    if mode == "semantic":
        semantic = retrieve_semantic(normalized_query, run_plan, storage_root, build_report_path=build_report_path, modalities=modalities, text_adapter=text_adapter, image_adapter=image_adapter, text_k=text_k, image_k=image_k)
        return _semantic_result_to_hybrid(normalized_query, run_plan, semantic)
    if mode == "bm25":
        return _retrieve_bm25(normalized_query, run_plan, storage_root, build_report_path=build_report_path, modalities=modalities, text_k=text_k, image_k=image_k)
    if mode == "hybrid":
        return retrieve_hybrid(normalized_query, run_plan, storage_root, build_report_path=build_report_path, modalities=modalities, text_adapter=text_adapter, image_adapter=image_adapter, text_k=text_k, image_k=image_k, reranker_adapter=reranker_adapter)
    raise RetrievalInputError("unsupported retrieval mode")


def _hybrid_hits(fused: Mapping[str, Any], bm: Mapping[str, Any], sem: Mapping[str, Any], *, limit: int, image: bool) -> tuple[HybridHit, ...]:
    ids = fused.get("ids", [[]])[0]; scores = fused.get("fused_scores", [[]])[0]
    bi = {x: i for i, x in enumerate(bm.get("ids", [[]])[0])}; si = {x: i for i, x in enumerate(sem.get("ids", [[]])[0])}
    out = []
    for rank, (identifier, score) in enumerate(zip(ids[:limit], scores[:limit]), 1):
        if not isinstance(identifier, str) or not identifier or not isinstance(score, (int, float)) or not math.isfinite(float(score)):
            raise RetrievalQueryError("fusion returned an invalid result")
        i, j = bi.get(identifier), si.get(identifier); meta = (bm.get("metadatas", [[]])[0][i] if i is not None else sem.get("metadatas", [[]])[0][j])
        meta = _decode_metadata(meta)
        doc = (bm.get("documents", [[]])[0][i] if i is not None else sem.get("documents", [[]])[0][j])
        raw_scores = bm.get("scores", [])
        raw_scores = raw_scores[0] if raw_scores and isinstance(raw_scores[0], list) else raw_scores
        out.append(HybridHit(identifier, rank, float(score), meta, None if image else doc, meta.get("caption_text") if image else None, i + 1 if i is not None else None, raw_scores[i] if i is not None else None, j + 1 if j is not None else None, sem.get("distances", [[]])[0][j] if j is not None else None, 1.0 - float(sem.get("distances", [[]])[0][j]) if j is not None else None, tuple(x for x, present in (("bm25", i is not None), ("semantic", j is not None)) if present)))
    return tuple(out)


def retrieve_hybrid(query: str, run_plan: RunPlan, storage_root: str | Path, *, build_report_path: str | Path, modalities: Sequence[str] = ("text", "image"), text_adapter: Any = None, image_adapter: Any = None, text_k: int | None = None, image_k: int | None = None, reranker_adapter: CrossEncoderReranker | None = None) -> HybridRetrievalResult:
    """Retrieve and fuse lexical and semantic candidates independently per modality."""
    started = perf_counter()
    if not isinstance(run_plan, RunPlan): raise TypeError("retrieve_hybrid requires a RunPlan")
    run_plan.verify_integrity(); config = run_plan.resolved_config(); q = _validate_query(query); requested = _validate_modalities(modalities)
    tk = _validate_k(text_k, label="text_k", default=config.text_k) if "text" in requested else None; ik = _validate_k(image_k, label="image_k", default=config.image_k) if "image" in requested else None
    report = _validate_build_report(storage_root, build_report_path, run_plan); _validate_adapter_configuration(text_adapter, config, modality="text"); _validate_adapter_configuration(image_adapter, config, modality="image")
    if config.use_reranker and reranker_adapter is not None and getattr(reranker_adapter, "model_name", None) != config.reranker_model:
        raise RetrievalInputError("injected reranker configuration does not match the RunPlan")
    store = None; timings=[]; text_hits=(); image_hits=(); diagnostics={}; effective=(config.bm25_weight, config.semantic_weight); strategy=config.fusion_type
    try:
        try: store=ChromaVectorStore.open_existing(storage_root, run_plan)
        except Exception as exc: raise RetrievalStoreError("could not open the referenced existing index") from exc
        if store.count("text") != report.text_records_persisted or store.count("image") != report.image_records_persisted: raise RetrievalReportError("persisted collection counts do not match the successful build report")
        engine=FusionEngine(config)
        for modality, k, adapter in (("text",tk,text_adapter),("image",ik,image_adapter)):
            if modality not in requested: continue
            t0=perf_counter(); records=store.read_records(modality); n=len(records.ids)
            if not n: timings.append((modality, perf_counter()-t0)); continue
            docs=[(d or "") if modality=="text" else str(m.get("caption_text") or identifier) for identifier,d,m in zip(records.ids,records.documents,records.metadatas)]
            try: bm=BM25Index(docs, records.ids, records.metadatas).query(q, min(n, max(k or 1, 20)))
            except BM25Error as exc: raise HybridRetrievalError(f"{modality} BM25 retrieval failed") from exc
            if adapter is None: adapter = TextEmbeddingAdapter(config) if modality=="text" else ImageEmbeddingAdapter(config)
            try:
                vector=adapter.embed_query(q); sem_raw=store.query_text(vector, min(n, max(k or 1, 20))) if modality=="text" else store.query_image(vector, min(n, max(k or 1, 20)))
                sem = {"ids": sem_raw["ids"], "documents": sem_raw.get("documents", [[None]*len(sem_raw["ids"][0])]), "metadatas": sem_raw["metadatas"], "distances": sem_raw["distances"]}
                _outer_query_values(sem, "ids"); _outer_query_values(sem, "distances")
            except Exception as exc: raise RetrievalQueryError(f"{modality} semantic retrieval failed") from exc
            bw, sw, diag=engine.adaptive_weights(q,bm); effective=(bw,sw); diagnostics[modality]=diag
            candidate_limit = min(n, max(k or n, config.rerank_k if modality == "text" and config.use_reranker else k or n))
            fused, stats=(engine.fuse(bm,sem,bw,sw,candidate_limit) if config.use_weighted_fusion else engine.rrf(bm,sem,bw,sw,candidate_limit)); diagnostics[modality]={**diag,**stats,"candidate_pool_size":candidate_limit}
            hits=_hybrid_hits(fused,bm,sem,limit=candidate_limit,image=modality=="image")
            if modality=="text": text_hits=hits
            else: image_hits=hits
            timings.append((modality,perf_counter()-t0))
        boost_started = perf_counter()
        text_hits, image_hits, warnings, boost_diag = apply_cross_modal_boosts(text_hits, image_hits, config)
        diagnostics["cross_modal"] = boost_diag
        rerank_elapsed = 0.0
        candidate_pool_size = len(text_hits)
        if config.use_reranker and text_hits:
            rerank_started = perf_counter()
            try:
                reranker = reranker_adapter or CrossEncoderReranker(config)
                text_hits = tuple(reranker.rerank_items(q, text_hits, top_k=min(config.rerank_k, len(text_hits))))
            except RerankerError as exc:
                raise HybridRetrievalError("cross-encoder reranking failed") from exc
            except Exception as exc:
                raise HybridRetrievalError("cross-encoder reranking failed") from exc
            rerank_elapsed = max(0.0, perf_counter() - rerank_started)
            diagnostics["reranking"] = {"candidate_pool_size": candidate_pool_size, "final_result_count": len(text_hits), "enabled": True}
        else:
            text_hits = tuple(sorted(text_hits, key=lambda hit: (-(hit.boosted_fused_score if hit.boosted_fused_score is not None else hit.fused_score))))[:tk] if text_hits else ()
            diagnostics["reranking"] = {"candidate_pool_size": candidate_pool_size, "final_result_count": len(text_hits), "enabled": False}
        image_hits = tuple(image_hits[:ik]) if ik is not None else image_hits
        text_hits = tuple(replace(hit, rank=index + 1) for index, hit in enumerate(text_hits))
        image_hits = tuple(replace(hit, rank=index + 1) for index, hit in enumerate(image_hits))
    finally:
        if store is not None: store.close()
    return HybridRetrievalResult(_query_id(q,run_plan,requested,tk,ik),run_plan.index_fingerprint,run_plan.document_manifest_hash,requested,tk,ik,text_hits,image_hits,strategy,effective,diagnostics,tuple(timings),perf_counter()-started,max(0.0, perf_counter()-boost_started) if 'boost_started' in locals() else 0.0, rerank_elapsed if 'rerank_elapsed' in locals() else 0.0, warnings if 'warnings' in locals() else (), q, report.text_embedding_seconds, report.image_embedding_seconds, report.text_records_persisted, report.image_records_persisted)
