from __future__ import annotations

from types import SimpleNamespace

from backend.experiments.ablation_runner import (
    AblationMetricComparison,
    AblationRunResult,
    AblationVariantResult,
    _comparison,
)
from backend.experiments.evaluation import MetricAggregate
from backend.experiments.evaluation_runner import EvaluationRunResult


def _aggregate(metric_id: str, *, name: str | None = None) -> MetricAggregate:
    return MetricAggregate(
        metric_id,
        name or metric_id,
        "computed",
        0.0,
        "score",
        "higher_is_better",
        "retrieved_context",
        "test-v1",
        1,
        0,
        0,
        0,
    )


def _evaluation(run_fingerprint: str, suite_id: str = "bench") -> EvaluationRunResult:
    trace = SimpleNamespace(trace_id="a" * 64)
    outcome = SimpleNamespace(case_id="case-1", trace_id=trace.trace_id, status="succeeded")
    report = SimpleNamespace(
        run_fingerprint=run_fingerprint,
        benchmark_id=suite_id,
        benchmark_version="v1",
        trace_ids=(trace.trace_id,),
        aggregates=tuple(_aggregate(f"M{number}") for number in range(1, 20)),
    )
    return EvaluationRunResult(report, (trace,), (outcome,))


def test_comparison_preserves_zeros_and_marks_m4_not_comparable():
    variants = tuple(
        AblationVariantResult(
            f"{number}_Dense_only" if number == 1 else (
                "2_BM25_only" if number == 2 else
                "3_Hybrid_static" if number == 3 else
                "4_Hybrid_adaptive" if number == 4 else
                "5_Hybrid_reranker" if number == 5 else "6_Full_pipeline"
            ),
            f"{number:x}".rjust(64, "0"),
            "succeeded",
            _evaluation(f"{number:x}".rjust(64, "0")),
        )
        for number in range(1, 7)
    )
    m4 = _comparison("M4", variants)
    m3 = _comparison("M3", variants)
    assert isinstance(m4, AblationMetricComparison)
    assert m4.state == "not_comparable"
    assert m4.reason_code == "mode_dependent_score_family"
    assert all(item is not None and item.value == 0.0 for item in m4.aggregates)
    assert m3.state == "comparable"

    result = AblationRunResult(
        "1",
        "succeeded",
        "b" * 64,
        "c" * 64,
        "bench",
        "v1",
        variants,
        tuple(_comparison(f"M{number}", variants) for number in range(1, 20)),
    )
    assert result.status == "succeeded"
    assert tuple(item.variant_id for item in result.variants) == (
        "1_Dense_only", "2_BM25_only", "3_Hybrid_static",
        "4_Hybrid_adaptive", "5_Hybrid_reranker", "6_Full_pipeline",
    )
