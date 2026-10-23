"""Canonical, integrity-checked JSON for completed in-memory evaluations."""

from __future__ import annotations

import json
import math
import re
from typing import Any

from .ablation_plans import AblationPlan
from .ablation_runner import (
    ABLATION_EXECUTION_SCHEMA_VERSION,
    AblationMetricComparison,
    AblationRunResult,
    AblationVariantResult,
    _METRIC_IDS,
    _comparison,
    _variant_status,
)
from .evaluation import (
    EVALUATION_SCHEMA_VERSION,
    EvaluationReport,
    MetricAggregate,
    MetricObservation,
    build_evaluation_report,
    verify_evaluation_fingerprint,
)
from .evaluation_runner import EvaluationCaseOutcome, EvaluationRunResult
from .tracing import (
    TRACE_SCHEMA_VERSION,
    QueryTrace,
    TraceContext,
    TraceEvidence,
    TraceFailure,
    TraceGeneration,
    TraceStageTiming,
    _record_id,
    verify_trace_fingerprint,
)


SERIALIZATION_SCHEMA_VERSION = "1"
_EVALUATION_ARTIFACT = "evaluation_result"
_ABLATION_ARTIFACT = "ablation_result"
_HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


class SerializationError(ValueError):
    """Raised when a serialized evaluation artifact is invalid or tampered."""


class _DuplicateKey(ValueError):
    pass


def _reject_constant(value: str) -> Any:
    raise ValueError("non-finite JSON number")


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey("duplicate JSON object key")
        result[key] = value
    return result


def _load(value: str, artifact: str) -> dict[str, Any]:
    if not isinstance(value, str):
        raise SerializationError("serialized artifact must be text")
    try:
        payload = json.loads(value, object_pairs_hook=_no_duplicates, parse_constant=_reject_constant)
    except Exception as exc:
        raise SerializationError("serialized artifact is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise SerializationError("serialized artifact envelope must be an object")
    if set(payload) != {"artifact_type", "serialization_schema_version", "payload"}:
        raise SerializationError("serialized artifact envelope fields are invalid")
    if payload["artifact_type"] != artifact or payload["serialization_schema_version"] != SERIALIZATION_SCHEMA_VERSION:
        raise SerializationError("serialized artifact type or schema version is unsupported")
    if not isinstance(payload["payload"], dict):
        raise SerializationError("serialized artifact payload must be an object")
    return payload["payload"]


def _json(artifact: str, payload: dict[str, Any]) -> str:
    envelope = {"artifact_type": artifact, "serialization_schema_version": SERIALIZATION_SCHEMA_VERSION, "payload": payload}
    try:
        return json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except Exception as exc:
        raise SerializationError("artifact contains unsupported values") from exc


def _fields(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise SerializationError(f"{label} fields are invalid")
    return value


def _string(value: Any, label: str, *, nonempty: bool = True, maximum: int | None = None) -> str:
    if not isinstance(value, str) or (nonempty and not value) or (maximum is not None and len(value) > maximum):
        raise SerializationError(f"{label} is invalid")
    return value


def _hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise SerializationError(f"{label} is invalid")
    return value.lower()


def _code(value: Any, label: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not _CODE_RE.fullmatch(value):
        raise SerializationError(f"{label} is invalid")
    return value


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise SerializationError(f"{label} is invalid")
    return float(value)


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SerializationError(f"{label} is invalid")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise SerializationError(f"{label} is invalid")
    return value


def _failure(value: TraceFailure | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {"stage": value.stage, "error_code": value.error_code, "attempts": value.attempts, "elapsed_seconds": value.elapsed_seconds}


def _trace_to_json(trace: QueryTrace) -> dict[str, Any]:
    return {
        "schema_version": trace.schema_version,
        "trace_id": trace.trace_id,
        "trace_fingerprint": trace.trace_fingerprint,
        "query_id": trace.query_id,
        "index_fingerprint": trace.index_fingerprint,
        "document_manifest_hash": trace.document_manifest_hash,
        "run_fingerprint": trace.run_fingerprint,
        "context_fingerprint": trace.context_fingerprint,
        "prompt_fingerprint": trace.prompt_fingerprint,
        "status": trace.status,
        "failed_stage": trace.failed_stage,
        "error_code": trace.error_code,
        "evidence": [item.__dict__ for item in trace.evidence],
        "context": None if trace.context is None else {
            "context_fingerprint": trace.context.context_fingerprint,
            "selected_text": [list(item) for item in trace.context.selected_text],
            "selected_images": [list(item) for item in trace.context.selected_images],
            "text_context_tokens": trace.context.text_context_tokens,
            "max_context_tokens": trace.context.max_context_tokens,
            "input_text_candidates": trace.context.input_text_candidates,
            "input_image_candidates": trace.context.input_image_candidates,
            "selected_text_count": trace.context.selected_text_count,
            "selected_image_count": trace.context.selected_image_count,
            "diagnostics": [list(item) for item in trace.context.diagnostics],
            "warnings": list(trace.context.warnings),
        },
        "generation": None if trace.generation is None else trace.generation.__dict__,
        "timings": [item.__dict__ for item in trace.timings],
        "warnings": list(trace.warnings),
        "failure": _failure(trace.failure),
    }


def _trace_from_json(value: Any) -> QueryTrace:
    data = _fields(value, {"schema_version", "trace_id", "trace_fingerprint", "query_id", "index_fingerprint", "document_manifest_hash", "run_fingerprint", "context_fingerprint", "prompt_fingerprint", "status", "failed_stage", "error_code", "evidence", "context", "generation", "timings", "warnings", "failure"}, "trace")
    if data["schema_version"] != TRACE_SCHEMA_VERSION:
        raise SerializationError("trace schema version is unsupported")
    trace_id = _hash(data["trace_id"], "trace_id")
    trace_fingerprint = _hash(data["trace_fingerprint"], "trace_fingerprint")
    query_id = _hash(data["query_id"], "query_id")
    index = _hash(data["index_fingerprint"], "index_fingerprint")
    manifest = _hash(data["document_manifest_hash"], "document_manifest_hash")
    run = _hash(data["run_fingerprint"], "run_fingerprint")
    context_fp = None if data["context_fingerprint"] is None else _hash(data["context_fingerprint"], "context_fingerprint")
    prompt_fp = None if data["prompt_fingerprint"] is None else _hash(data["prompt_fingerprint"], "prompt_fingerprint")
    status = _string(data["status"], "trace status")
    if status not in {"retrieved", "context_ready", "succeeded", "failed"}:
        raise SerializationError("trace status is invalid")
    failed_stage = _code(data["failed_stage"], "failed_stage", optional=True)
    error_code = _code(data["error_code"], "trace error_code", optional=True)
    evidence: list[TraceEvidence] = []
    for raw in _list(data["evidence"], "trace evidence"):
        item = _fields(raw, {"modality", "record_id", "rank", "source", "page", "sources", "fused_score", "bm25_rank", "bm25_score", "semantic_rank", "semantic_distance", "semantic_similarity", "boosted_fused_score", "effective_distance", "effective_similarity", "cross_modal_boost", "reranker_score", "reranker_normalized_score", "final_rank_score", "reranker_applied"}, "trace evidence")
        numeric = {name: (None if item[name] is None else _finite(item[name], name)) for name in ("bm25_score", "semantic_distance", "semantic_similarity", "boosted_fused_score", "effective_distance", "effective_similarity", "cross_modal_boost", "reranker_score", "reranker_normalized_score", "final_rank_score")}
        ranks = {name: (None if item[name] is None else _nonnegative_int(item[name], name)) for name in ("bm25_rank", "semantic_rank")}
        sources = tuple(_code(source, "trace signal") for source in _list(item["sources"], "trace signals"))
        if not isinstance(item["reranker_applied"], bool):
            raise SerializationError("reranker_applied is invalid")
        evidence.append(TraceEvidence(item["modality"], _string(item["record_id"], "record_id"), _nonnegative_int(item["rank"], "rank"), _string(item["source"], "source", nonempty=False, maximum=160), _string(item["page"], "page", nonempty=False, maximum=80), sources, _finite(item["fused_score"], "fused_score"), ranks["bm25_rank"], numeric["bm25_score"], ranks["semantic_rank"], numeric["semantic_distance"], numeric["semantic_similarity"], numeric["boosted_fused_score"], numeric["effective_distance"], numeric["effective_similarity"], numeric["cross_modal_boost"], numeric["reranker_score"], numeric["reranker_normalized_score"], numeric["final_rank_score"], item["reranker_applied"]))
    context = None
    if data["context"] is not None:
        item = _fields(data["context"], {"context_fingerprint", "selected_text", "selected_images", "text_context_tokens", "max_context_tokens", "input_text_candidates", "input_image_candidates", "selected_text_count", "selected_image_count", "diagnostics", "warnings"}, "trace context")
        selected_text = []
        for entry in _list(item["selected_text"], "selected text"):
            if not isinstance(entry, list) or len(entry) != 3:
                raise SerializationError("selected text entry is invalid")
            if not isinstance(entry[2], bool):
                raise SerializationError("text truncation flag is invalid")
            record_id = _string(entry[1], "text record_id")
            if record_id != _record_id(record_id):
                raise SerializationError("text record_id is unsafe")
            selected_text.append((_string(entry[0], "text label", nonempty=False, maximum=160), record_id, entry[2]))
        selected_images = []
        for entry in _list(item["selected_images"], "selected images"):
            if not isinstance(entry, list) or len(entry) != 2:
                raise SerializationError("selected image entry is invalid")
            record_id = _string(entry[1], "image record_id")
            if record_id != _record_id(record_id):
                raise SerializationError("image record_id is unsafe")
            selected_images.append((_string(entry[0], "image label", nonempty=False, maximum=160), record_id))
        diagnostics = []
        for entry in _list(item["diagnostics"], "context diagnostics"):
            if not isinstance(entry, list) or len(entry) != 2 or not isinstance(entry[0], str) or isinstance(entry[1], (dict, list)):
                raise SerializationError("context diagnostic is invalid")
            if isinstance(entry[1], float) and not math.isfinite(entry[1]):
                raise SerializationError("context diagnostic is invalid")
            diagnostics.append((entry[0], entry[1]))
        warnings = tuple(_code(warning, "context warning") for warning in _list(item["warnings"], "context warnings"))
        selected_text_count = _nonnegative_int(item["selected_text_count"], "selected_text_count")
        selected_image_count = _nonnegative_int(item["selected_image_count"], "selected_image_count")
        if selected_text_count != len(selected_text) or selected_image_count != len(selected_images):
            raise SerializationError("context selected counts are inconsistent")
        context = TraceContext(_hash(item["context_fingerprint"], "context fingerprint"), tuple(selected_text), tuple(selected_images), _nonnegative_int(item["text_context_tokens"], "text_context_tokens"), _nonnegative_int(item["max_context_tokens"], "max_context_tokens"), _nonnegative_int(item["input_text_candidates"], "input_text_candidates"), _nonnegative_int(item["input_image_candidates"], "input_image_candidates"), selected_text_count, selected_image_count, tuple(diagnostics), warnings)
    generation = None
    if data["generation"] is not None:
        item = _fields(data["generation"], {"model_name", "status", "error_code", "attempts", "context_tokens", "prompt_tokens", "output_tokens", "images_sent", "image_hashes", "response_hash", "prompt_fingerprint", "temperature", "output_limit", "think_mode", "retries", "retry_backoff_seconds"}, "trace generation")
        if item["status"] not in {"succeeded", "failed"} or not isinstance(item["think_mode"], bool):
            raise SerializationError("trace generation status or mode is invalid")
        generation = TraceGeneration(_string(item["model_name"], "model_name"), item["status"], _code(item["error_code"], "generation error_code", optional=True), _nonnegative_int(item["attempts"], "attempts"), _nonnegative_int(item["context_tokens"], "context_tokens"), _nonnegative_int(item["prompt_tokens"], "prompt_tokens"), _nonnegative_int(item["output_tokens"], "output_tokens"), _nonnegative_int(item["images_sent"], "images_sent"), tuple(_hash(value, "image hash") for value in _list(item["image_hashes"], "image hashes")), None if item["response_hash"] is None else _hash(item["response_hash"], "response_hash"), _hash(item["prompt_fingerprint"], "prompt_fingerprint"), _finite(item["temperature"], "temperature"), _nonnegative_int(item["output_limit"], "output_limit"), item["think_mode"], _nonnegative_int(item["retries"], "retries"), _finite(item["retry_backoff_seconds"], "retry_backoff_seconds"))
    timings = []
    for item in _list(data["timings"], "trace timings"):
        raw = _fields(item, {"stage", "elapsed_seconds"}, "trace timing")
        timings.append(TraceStageTiming(_code(raw["stage"], "timing stage") or "", _finite(raw["elapsed_seconds"], "elapsed_seconds")))
    warnings = tuple(_code(item, "trace warning") for item in _list(data["warnings"], "trace warnings"))
    failure = None
    if data["failure"] is not None:
        raw = _fields(data["failure"], {"stage", "error_code", "attempts", "elapsed_seconds"}, "trace failure")
        failure = TraceFailure(_code(raw["stage"], "failure stage") or "", _code(raw["error_code"], "failure error_code") or "", None if raw["attempts"] is None else _nonnegative_int(raw["attempts"], "failure attempts"), None if raw["elapsed_seconds"] is None else _finite(raw["elapsed_seconds"], "failure elapsed_seconds"))
    trace = QueryTrace(TRACE_SCHEMA_VERSION, trace_id, trace_fingerprint, query_id, index, manifest, run, context_fp, prompt_fp, status, failed_stage, error_code, tuple(evidence), context, generation, tuple(timings), warnings, failure)
    if context is None and context_fp is not None:
        raise SerializationError("trace context fingerprint is inconsistent")
    if context is not None and context.context_fingerprint != context_fp:
        raise SerializationError("trace context summary is inconsistent")
    if generation is None and prompt_fp is not None:
        raise SerializationError("trace prompt fingerprint is inconsistent")
    if generation is not None and generation.prompt_fingerprint != prompt_fp:
        raise SerializationError("trace generation summary is inconsistent")
    if failure is not None and (failed_stage != failure.stage or error_code != failure.error_code):
        raise SerializationError("trace failure summary is inconsistent")
    if status == "retrieved" and (context is not None or generation is not None or failure is not None):
        raise SerializationError("trace status is inconsistent")
    if status == "context_ready" and (context is None or generation is not None or failure is not None):
        raise SerializationError("trace status is inconsistent")
    if status == "succeeded" and (generation is None or generation.status != "succeeded" or failure is not None):
        raise SerializationError("trace status is inconsistent")
    if status == "failed" and failure is None and not (generation is not None and generation.status == "failed"):
        raise SerializationError("trace failure status is inconsistent")
    try:
        verify_trace_fingerprint(trace)
    except Exception as exc:
        raise SerializationError("trace fingerprint verification failed") from exc
    return trace


def _report_to_json(report: EvaluationReport) -> dict[str, Any]:
    return {
        "schema_version": report.schema_version,
        "evaluation_fingerprint": report.evaluation_fingerprint,
        "run_fingerprint": report.run_fingerprint,
        "benchmark_id": report.benchmark_id,
        "benchmark_version": report.benchmark_version,
        "trace_ids": list(report.trace_ids),
        "observations": [item.__dict__ for item in report.observations],
        "aggregates": [item.__dict__ for item in report.aggregates],
    }


def _report_from_json(value: Any, traces: tuple[QueryTrace, ...]) -> EvaluationReport:
    data = _fields(value, {"schema_version", "evaluation_fingerprint", "run_fingerprint", "benchmark_id", "benchmark_version", "trace_ids", "observations", "aggregates"}, "evaluation report")
    if data["schema_version"] != EVALUATION_SCHEMA_VERSION:
        raise SerializationError("evaluation report schema version is unsupported")
    report_trace_ids = tuple(_hash(item, "trace_id") for item in _list(data["trace_ids"], "report trace_ids"))
    observations = []
    observation_fields = {"trace_id", "metric_id", "metric_name", "status", "value", "unit", "optimization_direction", "reference_kind", "implementation_version", "model_identifier", "reason_code", "methodology_note"}
    for raw in _list(data["observations"], "report observations"):
        item = _fields(raw, observation_fields, "metric observation")
        observations.append(MetricObservation(**item))
    aggregates = []
    aggregate_fields = {"metric_id", "metric_name", "status", "value", "unit", "optimization_direction", "reference_kind", "implementation_version", "eligible_count", "skipped_count", "failed_count", "unavailable_count", "model_identifier", "methodology_note"}
    for raw in _list(data["aggregates"], "report aggregates"):
        item = _fields(raw, aggregate_fields, "metric aggregate")
        aggregates.append(MetricAggregate(**item))
    try:
        report = build_evaluation_report(traces, observations, aggregates, run_fingerprint=_hash(data["run_fingerprint"], "run_fingerprint"), benchmark_id=_string(data["benchmark_id"], "benchmark_id", maximum=120), benchmark_version=_string(data["benchmark_version"], "benchmark_version", maximum=80))
    except Exception as exc:
        raise SerializationError("evaluation report validation failed") from exc
    if report.trace_ids != report_trace_ids or report.evaluation_fingerprint != _hash(data["evaluation_fingerprint"], "evaluation_fingerprint"):
        raise SerializationError("evaluation report fingerprint or trace IDs are inconsistent")
    try:
        verify_evaluation_fingerprint(report)
    except Exception as exc:
        raise SerializationError("evaluation fingerprint verification failed") from exc
    return report


def _evaluation_to_json(result: EvaluationRunResult) -> dict[str, Any]:
    return {"report": _report_to_json(result.report), "traces": [_trace_to_json(item) for item in result.traces], "outcomes": [item.__dict__ for item in result.outcomes]}


def _evaluation_from_json(value: Any) -> EvaluationRunResult:
    data = _fields(value, {"report", "traces", "outcomes"}, "evaluation result")
    traces = tuple(_trace_from_json(item) for item in _list(data["traces"], "result traces"))
    report = _report_from_json(data["report"], traces)
    if tuple(item.trace_id for item in traces) != report.trace_ids or any(item.run_fingerprint != report.run_fingerprint for item in traces):
        raise SerializationError("evaluation trace provenance is inconsistent")
    outcomes = []
    outcome_fields = {"case_id", "status", "reached_stage", "trace_id", "error_code"}
    for raw in _list(data["outcomes"], "result outcomes"):
        item = _fields(raw, outcome_fields, "case outcome")
        case_id = _string(item["case_id"], "case_id", maximum=80)
        status = _string(item["status"], "case outcome status")
        if status not in {"succeeded", "failed"}:
            raise SerializationError("case outcome status is invalid")
        trace_id = _hash(item["trace_id"], "outcome trace_id")
        reached = _code(item["reached_stage"], "reached_stage")
        error = _code(item["error_code"], "outcome error_code", optional=True)
        outcomes.append(EvaluationCaseOutcome(case_id, status, reached or "", trace_id, error))
    outcome_tuple = tuple(outcomes)
    if len(outcome_tuple) != len(traces) or len({item.case_id for item in outcome_tuple}) != len(outcome_tuple) or {item.trace_id for item in outcome_tuple} != set(report.trace_ids):
        raise SerializationError("case outcomes do not match traces")
    return EvaluationRunResult(report, traces, outcome_tuple)


def serialize_evaluation_result(result: EvaluationRunResult) -> str:
    """Serialize one complete evaluation result without persistence."""

    if not isinstance(result, EvaluationRunResult):
        raise SerializationError("evaluation result is invalid")
    try:
        if tuple(item.trace_id for item in result.traces) != result.report.trace_ids:
            raise SerializationError("evaluation result traces are inconsistent")
        for trace in result.traces:
            verify_trace_fingerprint(trace)
        verify_evaluation_fingerprint(result.report)
        return _json(_EVALUATION_ARTIFACT, _evaluation_to_json(result))
    except SerializationError:
        raise
    except Exception as exc:
        raise SerializationError("evaluation result cannot be serialized") from exc


def deserialize_evaluation_result(value: str) -> EvaluationRunResult:
    """Strictly reconstruct and fingerprint-check an evaluation result."""

    try:
        return _evaluation_from_json(_load(value, _EVALUATION_ARTIFACT))
    except SerializationError:
        raise
    except Exception as exc:
        raise SerializationError("evaluation result failed validation") from exc


def _aggregate_to_json(value: MetricAggregate | None) -> dict[str, Any] | None:
    return None if value is None else value.__dict__


def _ablation_to_json(result: AblationRunResult) -> dict[str, Any]:
    return {
        "schema_version": result.schema_version,
        "status": result.status,
        "document_manifest_hash": result.document_manifest_hash,
        "shared_index_fingerprint": result.shared_index_fingerprint,
        "benchmark_id": result.benchmark_id,
        "benchmark_version": result.benchmark_version,
        "variants": [{"variant_id": item.variant_id, "run_fingerprint": item.run_fingerprint, "status": item.status, "evaluation_result": None if item.evaluation_result is None else _evaluation_to_json(item.evaluation_result), "error_code": item.error_code} for item in result.variants],
        "metric_comparisons": [{"metric_id": item.metric_id, "state": item.state, "aggregates": [_aggregate_to_json(aggregate) for aggregate in item.aggregates], "reason_code": item.reason_code} for item in result.metric_comparisons],
    }


def serialize_ablation_result(result: AblationRunResult) -> str:
    """Serialize one complete six-variant result without persistence."""

    if not isinstance(result, AblationRunResult):
        raise SerializationError("ablation result is invalid")
    try:
        return _json(_ABLATION_ARTIFACT, _ablation_to_json(result))
    except SerializationError:
        raise
    except Exception as exc:
        raise SerializationError("ablation result cannot be serialized") from exc


def deserialize_ablation_result(value: str, *, ablation_plan: AblationPlan) -> AblationRunResult:
    """Reconstruct an ablation result and bind it to the accepted plan."""

    if not isinstance(ablation_plan, AblationPlan):
        raise SerializationError("ablation_plan is invalid")
    try:
        ablation_plan.__post_init__()
        payload = _load(value, _ABLATION_ARTIFACT)
        data = _fields(payload, {"schema_version", "status", "document_manifest_hash", "shared_index_fingerprint", "benchmark_id", "benchmark_version", "variants", "metric_comparisons"}, "ablation result")
        if data["schema_version"] != ABLATION_EXECUTION_SCHEMA_VERSION:
            raise SerializationError("ablation execution schema version is unsupported")
        manifest = _hash(data["document_manifest_hash"], "document_manifest_hash")
        shared_index = _hash(data["shared_index_fingerprint"], "shared_index_fingerprint")
        if manifest != ablation_plan.document_manifest_hash or shared_index != ablation_plan.shared_index_fingerprint:
            raise SerializationError("ablation result does not match the accepted plan")
        benchmark_id = _string(data["benchmark_id"], "benchmark_id", maximum=120)
        benchmark_version = _string(data["benchmark_version"], "benchmark_version", maximum=80)
        raw_variants = _list(data["variants"], "ablation variants")
        if len(raw_variants) != 6:
            raise SerializationError("ablation result must contain six variants")
        variants = []
        for expected, raw in zip(ablation_plan.variants, raw_variants):
            item = _fields(raw, {"variant_id", "run_fingerprint", "status", "evaluation_result", "error_code"}, "ablation variant")
            if item["variant_id"] != expected.variant_id or _hash(item["run_fingerprint"], "variant run_fingerprint") != expected.run_fingerprint:
                raise SerializationError("ablation variant identity does not match the accepted plan")
            nested = None if item["evaluation_result"] is None else _evaluation_from_json(item["evaluation_result"])
            if nested is not None:
                if nested.report.benchmark_id != benchmark_id or nested.report.benchmark_version != benchmark_version:
                    raise SerializationError("nested evaluation benchmark identity is inconsistent")
                for trace in nested.traces:
                    if trace.index_fingerprint != shared_index or trace.document_manifest_hash != manifest or trace.run_fingerprint != expected.run_fingerprint:
                        raise SerializationError("nested trace provenance is inconsistent")
                if _variant_status(nested) != item["status"]:
                    raise SerializationError("variant status disagrees with nested evaluation")
            elif item["status"] != "failed" or _code(item["error_code"], "variant error_code") is None:
                raise SerializationError("runner-level variant failure is invalid")
            variants.append(AblationVariantResult(expected.variant_id, expected.run_fingerprint, item["status"], nested, _code(item["error_code"], "variant error_code", optional=True)))
        variant_tuple = tuple(variants)
        case_orders = [tuple(item.case_id for item in variant.evaluation_result.outcomes) for variant in variant_tuple if variant.evaluation_result is not None]
        if case_orders and any(order != case_orders[0] for order in case_orders[1:]):
            raise SerializationError("variant benchmark case order is inconsistent")
        raw_comparisons = _list(data["metric_comparisons"], "metric comparisons")
        if len(raw_comparisons) != len(_METRIC_IDS):
            raise SerializationError("ablation result metric comparisons are incomplete")
        comparisons = []
        for expected_metric, raw in zip(_METRIC_IDS, raw_comparisons):
            item = _fields(raw, {"metric_id", "state", "aggregates", "reason_code"}, "metric comparison")
            if item["metric_id"] != expected_metric:
                raise SerializationError("metric comparisons are missing, duplicated, or out of order")
            slots = []
            for aggregate in _list(item["aggregates"], "comparison aggregates"):
                if aggregate is None:
                    slots.append(None)
                else:
                    slots.append(MetricAggregate(**_fields(aggregate, {"metric_id", "metric_name", "status", "value", "unit", "optimization_direction", "reference_kind", "implementation_version", "eligible_count", "skipped_count", "failed_count", "unavailable_count", "model_identifier", "methodology_note"}, "comparison aggregate")))
            comparisons.append(AblationMetricComparison(expected_metric, item["state"], tuple(slots), _code(item["reason_code"], "comparison reason_code", optional=True)))
        expected_comparisons = tuple(_comparison(metric, variant_tuple) for metric in _METRIC_IDS)
        if tuple(comparisons) != expected_comparisons:
            raise SerializationError("metric comparison rows disagree with variant reports")
        usable = sum(item.evaluation_result is not None for item in variant_tuple)
        expected_status = "failed" if usable == 0 else "succeeded" if all(item.status == "succeeded" for item in variant_tuple) else "partial"
        if data["status"] != expected_status:
            raise SerializationError("ablation overall status disagrees with variants")
        return AblationRunResult(ABLATION_EXECUTION_SCHEMA_VERSION, expected_status, manifest, shared_index, benchmark_id, benchmark_version, variant_tuple, expected_comparisons)
    except SerializationError:
        raise
    except Exception as exc:
        raise SerializationError("ablation result failed validation") from exc


__all__ = [
    "SERIALIZATION_SCHEMA_VERSION",
    "SerializationError",
    "serialize_evaluation_result",
    "deserialize_evaluation_result",
    "serialize_ablation_result",
    "deserialize_ablation_result",
]
