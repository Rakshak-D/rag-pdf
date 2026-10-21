"""Immutable, redacted contracts for in-memory query execution traces."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from backend.engine.context import EvidenceContext, verify_context_fingerprint, _safe_page, _source_name
from backend.engine.retrieval import HybridHit, HybridRetrievalResult, query_id_for
from backend.engine.generation import GenerationResult
from backend.experiments.run_plan import RunPlan


TRACE_SCHEMA_VERSION = "1"
_HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_ALLOWED_MODALITIES = {"text", "image"}
_DIAGNOSTIC_KEYS = {
    "candidate_pool_size", "final_result_count", "bm25_count", "semantic_count",
    "overlap_count", "query_type", "semantic_fallback", "adaptive_weight_source",
}


class TraceContractError(ValueError):
    """Raised when trace inputs do not form one consistent execution chain."""


def _finite(value: Any, *, label: str, allow_none: bool = True) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise TraceContractError(f"{label} must be a finite number or None")
    return float(value)


def _nonnegative_int(value: Any, *, label: str, allow_none: bool = True) -> int | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TraceContractError(f"{label} must be a non-negative integer or None")
    return value


def _hash(value: Any, *, label: str, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise TraceContractError(f"{label} must be a SHA-256 hexadecimal string")
    return value.lower()


def _code(value: Any, *, label: str, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not _CODE_RE.fullmatch(value):
        raise TraceContractError(f"{label} must be a short stable code")
    return value


def _bounded_text(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 160 or any(ord(char) < 32 for char in value):
        raise TraceContractError(f"{label} is invalid")
    return value


def _display(value: Any, *, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    cleaned = " ".join(" " if ord(char) < 32 or ord(char) == 127 else char for char in value).split()
    return " ".join(cleaned)[:120] or fallback


def _safe_warning(value: Any) -> str:
    if not isinstance(value, str) or not _CODE_RE.fullmatch(value):
        raise TraceContractError("trace warnings must be stable codes")
    return value


@dataclass(frozen=True)
class TraceStageTiming:
    stage: str
    elapsed_seconds: float

    def __post_init__(self) -> None:
        if not isinstance(self.stage, str) or not _CODE_RE.fullmatch(self.stage):
            raise TraceContractError("trace stage names must be stable codes")
        _finite(self.elapsed_seconds, label="stage elapsed_seconds", allow_none=False)
        if self.elapsed_seconds < 0:
            raise TraceContractError("stage elapsed_seconds must be non-negative")


@dataclass(frozen=True)
class TraceFailure:
    stage: str
    error_code: str
    attempts: int | None = None
    elapsed_seconds: float | None = None

    def __post_init__(self) -> None:
        TraceStageTiming(self.stage, 0.0)
        _code(self.error_code, label="failure error_code")
        _nonnegative_int(self.attempts, label="failure attempts")
        value = _finite(self.elapsed_seconds, label="failure elapsed_seconds")
        if value is not None and value < 0:
            raise TraceContractError("failure elapsed_seconds must be non-negative")


@dataclass(frozen=True)
class TraceEvidence:
    modality: Literal["text", "image"]
    record_id: str
    rank: int
    source: str
    page: str
    sources: tuple[str, ...]
    fused_score: float
    bm25_rank: int | None = None
    bm25_score: float | None = None
    semantic_rank: int | None = None
    semantic_distance: float | None = None
    semantic_similarity: float | None = None
    boosted_fused_score: float | None = None
    effective_distance: float | None = None
    effective_similarity: float | None = None
    cross_modal_boost: float | None = None
    reranker_score: float | None = None
    reranker_normalized_score: float | None = None
    final_rank_score: float | None = None
    reranker_applied: bool = False

    def __post_init__(self) -> None:
        if self.modality not in _ALLOWED_MODALITIES or not isinstance(self.record_id, str) or not self.record_id:
            raise TraceContractError("trace evidence modality and record_id are invalid")
        if isinstance(self.rank, bool) or not isinstance(self.rank, int) or self.rank < 1:
            raise TraceContractError("trace evidence rank must be positive")
        _finite(self.fused_score, label="fused_score", allow_none=False)
        for name in ("bm25_score", "semantic_distance", "semantic_similarity", "boosted_fused_score", "effective_distance", "effective_similarity", "cross_modal_boost", "reranker_score", "reranker_normalized_score", "final_rank_score"):
            _finite(getattr(self, name), label=name)
        for name in ("bm25_rank", "semantic_rank"):
            _nonnegative_int(getattr(self, name), label=name)
        if not isinstance(self.sources, tuple) or any(not isinstance(value, str) for value in self.sources):
            raise TraceContractError("trace evidence sources must be an immutable string tuple")


@dataclass(frozen=True)
class TraceContext:
    context_fingerprint: str
    selected_text: tuple[tuple[str, str, bool], ...]
    selected_images: tuple[tuple[str, str], ...]
    text_context_tokens: int
    max_context_tokens: int
    input_text_candidates: int
    input_image_candidates: int
    selected_text_count: int
    selected_image_count: int
    diagnostics: tuple[tuple[str, int | float | str | bool], ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class TraceGeneration:
    model_name: str
    status: Literal["succeeded", "failed"]
    error_code: str | None
    attempts: int
    context_tokens: int
    prompt_tokens: int
    output_tokens: int
    images_sent: int
    image_hashes: tuple[str, ...]
    response_hash: str | None
    prompt_fingerprint: str
    temperature: float
    output_limit: int
    think_mode: bool
    retries: int
    retry_backoff_seconds: float


@dataclass(frozen=True)
class QueryTrace:
    schema_version: str
    trace_id: str
    trace_fingerprint: str
    query_id: str
    index_fingerprint: str
    document_manifest_hash: str
    run_fingerprint: str
    context_fingerprint: str | None
    prompt_fingerprint: str | None
    status: Literal["retrieved", "context_ready", "succeeded", "failed"]
    failed_stage: str | None
    error_code: str | None
    evidence: tuple[TraceEvidence, ...]
    context: TraceContext | None
    generation: TraceGeneration | None
    timings: tuple[TraceStageTiming, ...]
    warnings: tuple[str, ...]
    failure: TraceFailure | None = None


def _trace_evidence(result: HybridRetrievalResult) -> tuple[TraceEvidence, ...]:
    values: list[TraceEvidence] = []
    for modality, hits in (("text", result.text_hits), ("image", result.image_hits)):
        for hit in hits:
            if not isinstance(hit, HybridHit):
                raise TraceContractError("retrieval evidence contains an invalid hit")
            metadata = hit.metadata if isinstance(hit.metadata, Mapping) else {}
            values.append(TraceEvidence(
                modality, hit.record_id, hit.rank,
                _source_name(metadata.get("source")), _safe_page(metadata.get("page_num")),
                tuple(_display(value, fallback="unknown") for value in hit.sources),
                _finite(hit.fused_score, label="fused_score", allow_none=False),
                hit.bm25_rank, hit.bm25_score, hit.semantic_rank,
                hit.semantic_distance, hit.semantic_similarity,
                hit.boosted_fused_score, hit.effective_distance, hit.effective_similarity,
                hit.cross_modal_boost, hit.reranker_score, hit.reranker_normalized_score,
                hit.final_rank_score, hit.reranker_applied,
            ))
    return tuple(values)


def _trace_context(context: EvidenceContext, config: Any) -> TraceContext:
    diagnostics: list[tuple[str, int | float | str | bool]] = []
    for key in sorted(_DIAGNOSTIC_KEYS):
        value = context.diagnostics.get(key)
        if isinstance(value, bool) or isinstance(value, (int, float, str)):
            if isinstance(value, float) and not math.isfinite(value):
                raise TraceContractError("context diagnostics must be finite")
            diagnostics.append((key, value))
    for value in context.warnings:
        _safe_warning(value)
    def diagnostic_count(name: str) -> int:
        value = context.diagnostics.get(name, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise TraceContractError(f"context diagnostic {name} must be a non-negative integer")
        return value
    return TraceContext(
        _hash(context.context_fingerprint, label="context fingerprint", allow_none=False),
        tuple((item.label, item.record_id, item.truncated) for item in context.selected_text),
        tuple((item.label, item.record_id) for item in context.selected_images),
        context.text_context_tokens, config.max_context_tokens,
        diagnostic_count("input_text_candidates"),
        diagnostic_count("input_image_candidates"),
        len(context.selected_text), len(context.selected_images), tuple(diagnostics), tuple(context.warnings),
    )


def _trace_generation(generation: GenerationResult, config: Any) -> TraceGeneration:
    if generation.status not in {"succeeded", "failed"}:
        raise TraceContractError("generation status is invalid")
    if generation.status == "succeeded" and (not isinstance(generation.response, str) or not generation.response.strip()):
        raise TraceContractError("successful generation must contain a response")
    if generation.status == "failed" and generation.response is not None:
        raise TraceContractError("failed generation must not contain a response")
    _bounded_text(generation.model_name, label="generation model_name")
    _nonnegative_int(generation.attempts, label="generation attempts", allow_none=False)
    for name in ("context_tokens", "prompt_tokens", "output_tokens", "images_sent"):
        _nonnegative_int(getattr(generation, name), label=name, allow_none=False)
    prompt = _hash(generation.prompt_fingerprint, label="prompt fingerprint", allow_none=False)
    response_hash = hashlib.sha256(generation.response.encode("utf-8")).hexdigest() if generation.response is not None else None
    if response_hash is not None and response_hash != hashlib.sha256(generation.response.encode("utf-8")).hexdigest():
        raise TraceContractError("response hash mismatch")
    return TraceGeneration(
        generation.model_name, generation.status, _code(generation.error_code, label="generation error_code", allow_none=True),
        generation.attempts, generation.context_tokens, generation.prompt_tokens, generation.output_tokens,
        generation.images_sent, tuple(_hash(value, label="image hash", allow_none=False) for value in generation.image_hashes),
        response_hash, prompt, config.llm_temperature, config.llm_max_tokens, config.llm_think_mode,
        config.llm_retries, config.llm_retry_backoff_sec,
    )


def _fingerprint_payload(trace: QueryTrace) -> dict[str, Any]:
    return {
        "schema": trace.schema_version, "query_id": trace.query_id,
        "index": trace.index_fingerprint, "manifest": trace.document_manifest_hash,
        "run": trace.run_fingerprint, "context": trace.context_fingerprint,
        "prompt": trace.prompt_fingerprint, "status": trace.status,
        "failed_stage": trace.failed_stage, "error_code": trace.error_code,
        "failure": {"stage": trace.failure.stage, "error_code": trace.failure.error_code, "attempts": trace.failure.attempts} if trace.failure else None,
        "evidence": [item.__dict__ for item in trace.evidence],
        "context_summary": trace.context.__dict__ if trace.context else None,
        "generation_summary": trace.generation.__dict__ if trace.generation else None,
        "warnings": trace.warnings,
    }


def build_query_trace(
    retrieval: HybridRetrievalResult,
    run_plan: RunPlan,
    *,
    context: EvidenceContext | None = None,
    generation: GenerationResult | None = None,
    failure: TraceFailure | None = None,
    timings: Sequence[TraceStageTiming] = (),
) -> QueryTrace:
    """Validate and assemble a redacted trace without invoking any pipeline stage."""

    if not isinstance(retrieval, HybridRetrievalResult) or not isinstance(run_plan, RunPlan):
        raise TraceContractError("trace construction requires a HybridRetrievalResult and RunPlan")
    try:
        run_plan.verify_integrity()
    except Exception as exc:
        raise TraceContractError("RunPlan integrity verification failed") from exc
    if not isinstance(retrieval.query_text, str) or not retrieval.query_text.strip() or retrieval.query_id != query_id_for(retrieval.query_text, run_plan, retrieval.modalities, retrieval.effective_text_k, retrieval.effective_image_k):
        raise TraceContractError("retrieval query identity is invalid")
    if retrieval.index_fingerprint != run_plan.index_fingerprint or retrieval.document_manifest_hash != run_plan.document_manifest_hash:
        raise TraceContractError("retrieval provenance does not match the RunPlan")
    config = run_plan.resolved_config()
    context_summary = None
    context_fp = None
    if context is not None:
        if not isinstance(context, EvidenceContext) or context.query_id != retrieval.query_id or context.query_text != retrieval.query_text or context.index_fingerprint != retrieval.index_fingerprint or context.document_manifest_hash != retrieval.document_manifest_hash or context.run_fingerprint != run_plan.run_fingerprint:
            raise TraceContractError("context provenance does not match retrieval and RunPlan")
        if context.modalities != retrieval.modalities or context.text_k != retrieval.effective_text_k or context.image_k != retrieval.effective_image_k:
            raise TraceContractError("context retrieval identity does not match retrieval")
        try:
            verify_context_fingerprint(context, config)
        except Exception as exc:
            raise TraceContractError("context fingerprint verification failed") from exc
        context_summary = _trace_context(context, config)
        context_fp = context.context_fingerprint
    generation_summary = None
    prompt_fp = None
    if generation is not None:
        if context is None or generation.query_id != retrieval.query_id or generation.query_text != retrieval.query_text or generation.index_fingerprint != retrieval.index_fingerprint or generation.document_manifest_hash != retrieval.document_manifest_hash or generation.run_fingerprint != run_plan.run_fingerprint or generation.context_fingerprint != context.context_fingerprint:
            raise TraceContractError("generation provenance does not match context and RunPlan")
        generation_summary = _trace_generation(generation, config)
        prompt_fp = generation.prompt_fingerprint
    if failure is not None and not isinstance(failure, TraceFailure):
        raise TraceContractError("failure must be a TraceFailure")
    status: Literal["retrieved", "context_ready", "succeeded", "failed"] = "retrieved"
    if context is not None:
        status = "context_ready"
    if generation is not None:
        status = "succeeded" if generation.status == "succeeded" else "failed"
    if failure is not None:
        status = "failed"
    derived_failure = failure
    if derived_failure is None and generation is not None and generation.status == "failed":
        derived_failure = TraceFailure("generation", generation.error_code or "generation_failed", generation.attempts, generation.elapsed_seconds)
    failed_stage = derived_failure.stage if derived_failure else None
    error_code = derived_failure.error_code if derived_failure else None
    evidence = _trace_evidence(retrieval)
    warning_values = tuple(_safe_warning(value) for value in retrieval.warnings)
    timing_values = tuple(timings)
    for timing in timing_values:
        if not isinstance(timing, TraceStageTiming):
            raise TraceContractError("timings must contain TraceStageTiming values")
    trace = QueryTrace(TRACE_SCHEMA_VERSION, "0" * 64, "0" * 64, retrieval.query_id, retrieval.index_fingerprint, retrieval.document_manifest_hash, run_plan.run_fingerprint, context_fp, prompt_fp, status, failed_stage, error_code, evidence, context_summary, generation_summary, timing_values, warning_values, derived_failure)
    fingerprint = hashlib.sha256(json.dumps(_fingerprint_payload(trace), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
    return QueryTrace(trace.schema_version, fingerprint, fingerprint, trace.query_id, trace.index_fingerprint, trace.document_manifest_hash, trace.run_fingerprint, trace.context_fingerprint, trace.prompt_fingerprint, trace.status, trace.failed_stage, trace.error_code, trace.evidence, trace.context, trace.generation, trace.timings, trace.warnings, trace.failure)


def build_failed_query_trace(
    run_plan: RunPlan,
    *,
    query: str,
    modalities: tuple[str, ...],
    text_k: int | None,
    image_k: int | None,
    failure: TraceFailure,
) -> QueryTrace:
    """Create a failed trace before retrieval exists, without fabricated evidence."""

    if not isinstance(run_plan, RunPlan):
        raise TraceContractError("failed trace construction requires a RunPlan")
    try:
        run_plan.verify_integrity()
    except Exception as exc:
        raise TraceContractError("RunPlan integrity verification failed") from exc
    if not isinstance(query, str) or not query.strip() or not isinstance(modalities, tuple) or not modalities or any(value not in _ALLOWED_MODALITIES for value in modalities) or len(set(modalities)) != len(modalities):
        raise TraceContractError("failed trace query identity is invalid")
    if any(isinstance(value, bool) or value is not None and (not isinstance(value, int) or value <= 0) for value in (text_k, image_k)):
        raise TraceContractError("failed trace retrieval limits are invalid")
    if not isinstance(failure, TraceFailure):
        raise TraceContractError("failure must be a TraceFailure")
    query_id = query_id_for(query.strip(), run_plan, modalities, text_k, image_k)
    trace = QueryTrace(TRACE_SCHEMA_VERSION, "0" * 64, "0" * 64, query_id, run_plan.index_fingerprint, run_plan.document_manifest_hash, run_plan.run_fingerprint, None, None, "failed", failure.stage, failure.error_code, (), None, None, (), (), failure)
    fingerprint = hashlib.sha256(json.dumps(_fingerprint_payload(trace), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
    return QueryTrace(trace.schema_version, fingerprint, fingerprint, trace.query_id, trace.index_fingerprint, trace.document_manifest_hash, trace.run_fingerprint, None, None, trace.status, trace.failed_stage, trace.error_code, (), None, None, (), (), failure)
