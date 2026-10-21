from __future__ import annotations

import math

import pytest

from backend.experiments import EvaluationContractError, MetricAggregate, MetricObservation, build_evaluation_report
from tests.unit.test_query_tracing import chain
from backend.experiments import build_query_trace


def report_inputs(tmp_path):
    plan, retrieval, context, generation = chain(tmp_path)
    trace = build_query_trace(retrieval, plan, context=context, generation=generation)
    return plan, trace


def test_zero_is_a_computed_metric_and_report_is_deterministic(tmp_path):
    plan, trace = report_inputs(tmp_path)
    observation = MetricObservation(trace.trace_id, "m1", "Exact", "computed", 0.0, "score", "higher_is_better", "ground_truth", "v1")
    aggregate = MetricAggregate("m1", "Exact", "computed", 0.0, "score", "higher_is_better", "ground_truth", "v1", 1, 0, 0, 0)
    first = build_evaluation_report([trace], [observation], [aggregate], run_fingerprint=plan.run_fingerprint, benchmark_id="bench", benchmark_version="1")
    second = build_evaluation_report([trace], [observation], [aggregate], run_fingerprint=plan.run_fingerprint, benchmark_id="bench", benchmark_version="1")
    assert first.evaluation_fingerprint == second.evaluation_fingerprint
    assert first.observations[0].value == 0.0


@pytest.mark.parametrize("status", ["skipped", "failed", "unavailable"])
def test_noncomputed_observations_require_reason_and_none_value(status):
    with pytest.raises(EvaluationContractError):
        MetricObservation("trace", "m1", "Metric", status, 0.0, "score", "descriptive", "no_reference", "v1", reason_code="not_used")


def test_invalid_metric_values_and_aggregate_counts_are_rejected(tmp_path):
    plan, trace = report_inputs(tmp_path)
    with pytest.raises(EvaluationContractError):
        MetricObservation(trace.trace_id, "m1", "Metric", "computed", math.nan, "score", "descriptive", "no_reference", "v1")
    with pytest.raises(EvaluationContractError):
        MetricAggregate("m1", "Metric", "no_eligible_values", 0.0, "score", "descriptive", "no_reference", "v1", 0, 1, 0, 0)
    observation = MetricObservation(trace.trace_id, "m1", "Metric", "computed", 1.0, "score", "descriptive", "no_reference", "v1")
    aggregate = MetricAggregate("m1", "Metric", "computed", 1.0, "score", "descriptive", "no_reference", "v1", 2, 0, 0, 0)
    with pytest.raises(EvaluationContractError):
        build_evaluation_report([trace], [observation], [aggregate], run_fingerprint=plan.run_fingerprint, benchmark_id="bench", benchmark_version="1")


def test_partial_and_no_eligible_states_preserve_explicit_counts(tmp_path):
    plan, trace = report_inputs(tmp_path)
    observations = (
        MetricObservation(trace.trace_id, "m1", "Metric", "computed", 0.4, "score", "higher_is_better", "retrieved_context", "v1"),
        MetricObservation(trace.trace_id, "m2", "Metric", "skipped", None, "score", "not_applicable", "not_applicable", "v1", reason_code="no_reference"),
    )
    aggregates = (
        MetricAggregate("m1", "Metric", "computed", 0.4, "score", "higher_is_better", "retrieved_context", "v1", 1, 0, 0, 0),
        MetricAggregate("m2", "Metric", "no_eligible_values", None, "score", "not_applicable", "not_applicable", "v1", 0, 1, 0, 0),
    )
    report = build_evaluation_report([trace], observations, aggregates, run_fingerprint=plan.run_fingerprint, benchmark_id="bench", benchmark_version="1")
    assert report.aggregates[1].value is None
