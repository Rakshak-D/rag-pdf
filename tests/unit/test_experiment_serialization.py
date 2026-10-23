from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.config import ResolvedRunConfig
from backend.experiments import (
    AblationRunResult,
    AblationVariantResult,
    EvaluationCaseOutcome,
    EvaluationRunResult,
    MetricAggregate,
    MetricObservation,
    SerializationError,
    build_ablation_plan,
    build_evaluation_report,
    build_failed_query_trace,
    deserialize_ablation_result,
    deserialize_evaluation_result,
    serialize_ablation_result,
    serialize_evaluation_result,
)
from backend.experiments.ablation_runner import _comparison
from backend.experiments.tracing import TraceFailure
from notebooks.config import Config


def _evaluation(tmp_path: Path) -> EvaluationRunResult:
    config = ResolvedRunConfig.from_legacy(Config(reset_collections_on_start=False))
    plan = __import__("backend.experiments", fromlist=["build_run_plan"]).build_run_plan(config, "a" * 64)
    trace = build_failed_query_trace(plan, query="private question", modalities=("text",), text_k=5, image_k=None, failure=TraceFailure("retrieval", "retrieval_failed"))
    observations = tuple(MetricObservation(trace.trace_id, f"M{number}", f"Metric {number}", "skipped", None, "score", "descriptive", "no_reference", "test-v1", reason_code="not_reached") for number in range(1, 20))
    aggregates = tuple(MetricAggregate(f"M{number}", f"Metric {number}", "no_eligible_values", None, "score", "descriptive", "no_reference", "test-v1", 0, 1, 0, 0) for number in range(1, 20))
    report = build_evaluation_report((trace,), observations, aggregates, run_fingerprint=plan.run_fingerprint, benchmark_id="synthetic", benchmark_version="v1")
    return EvaluationRunResult(report, (trace,), (EvaluationCaseOutcome("case-1", "failed", "retrieval", trace.trace_id, "retrieval_failed"),))


def test_evaluation_round_trip_is_deterministic_and_redacted(tmp_path):
    result = _evaluation(tmp_path)
    serialized = serialize_evaluation_result(result)
    restored = deserialize_evaluation_result(serialized)
    assert serialize_evaluation_result(restored) == serialized
    assert restored.report.evaluation_fingerprint == result.report.evaluation_fingerprint
    assert restored.traces[0].trace_id == result.traces[0].trace_id
    assert "private question" not in serialized
    assert "retrieval_failed" in serialized


def test_evaluation_serialization_rejects_tampering_and_duplicate_keys(tmp_path):
    result = _evaluation(tmp_path)
    payload = json.loads(serialize_evaluation_result(result))
    payload["payload"]["traces"][0]["error_code"] = "changed"
    with pytest.raises(SerializationError):
        deserialize_evaluation_result(json.dumps(payload, separators=(",", ":")))
    with pytest.raises(SerializationError):
        deserialize_evaluation_result('{"artifact_type":"evaluation_result","artifact_type":"evaluation_result","serialization_schema_version":"1","payload":{}}')
    payload = json.loads(serialize_evaluation_result(result))
    payload["artifact_type"] = "ablation_result"
    with pytest.raises(SerializationError):
        deserialize_evaluation_result(json.dumps(payload))


def test_ablation_round_trip_requires_the_accepted_plan():
    base = ResolvedRunConfig.from_legacy(Config(reset_collections_on_start=False))
    plan = build_ablation_plan(base, "b" * 64)
    variants = []
    for variant in plan.variants:
        evaluation = _evaluation(Path("."))
        # Rebind the synthetic failed trace/report to this variant's run identity.
        trace = build_failed_query_trace(variant.run_plan, query="private question", modalities=("text",), text_k=5, image_k=None, failure=TraceFailure("retrieval", "retrieval_failed"))
        observations = tuple(MetricObservation(trace.trace_id, f"M{number}", f"Metric {number}", "skipped", None, "score", "descriptive", "no_reference", "test-v1", reason_code="not_reached") for number in range(1, 20))
        aggregates = tuple(MetricAggregate(f"M{number}", f"Metric {number}", "no_eligible_values", None, "score", "descriptive", "no_reference", "test-v1", 0, 1, 0, 0) for number in range(1, 20))
        report = build_evaluation_report((trace,), observations, aggregates, run_fingerprint=variant.run_plan.run_fingerprint, benchmark_id="synthetic", benchmark_version="v1")
        evaluation = EvaluationRunResult(report, (trace,), (EvaluationCaseOutcome("case-1", "failed", "retrieval", trace.trace_id, "retrieval_failed"),))
        variants.append(AblationVariantResult(variant.variant_id, variant.run_fingerprint, "failed", evaluation))
    variant_tuple = tuple(variants)
    result = AblationRunResult("1", "partial", plan.document_manifest_hash, plan.shared_index_fingerprint, "synthetic", "v1", variant_tuple, tuple(_comparison(f"M{number}", variant_tuple) for number in range(1, 20)))
    serialized = serialize_ablation_result(result)
    restored = deserialize_ablation_result(serialized, ablation_plan=plan)
    assert serialize_ablation_result(restored) == serialized
    assert restored.variants[0].run_fingerprint == plan.variants[0].run_fingerprint
    with pytest.raises(SerializationError):
        deserialize_ablation_result(serialized, ablation_plan=build_ablation_plan(base, "c" * 64))
