from __future__ import annotations

from backend.experiments import MetricAggregate, MetricObservation, build_evaluation_report, build_query_trace
from tests.unit.test_query_tracing import chain


def test_retrieval_context_generation_trace_and_evaluation_contracts(tmp_path):
    plan, retrieval, context, generation = chain(tmp_path)
    trace = build_query_trace(retrieval, plan, context=context, generation=generation)
    observation = MetricObservation(trace.trace_id, "m1", "Synthetic contract metric", "computed", 0.0, "score", "descriptive", "no_reference", "contract-v1")
    aggregate = MetricAggregate("m1", "Synthetic contract metric", "computed", 0.0, "score", "descriptive", "no_reference", "contract-v1", 1, 0, 0, 0)
    report = build_evaluation_report([trace], [observation], [aggregate], run_fingerprint=plan.run_fingerprint, benchmark_id="synthetic", benchmark_version="1")
    assert report.run_fingerprint == plan.run_fingerprint
    assert report.trace_ids == (trace.trace_id,)
    assert report.observations[0].value == 0.0
    assert trace.context_fingerprint == context.context_fingerprint
    assert trace.prompt_fingerprint == generation.prompt_fingerprint
