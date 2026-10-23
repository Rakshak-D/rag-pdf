"""Synchronous, in-memory execution of the six validated ablation plans."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable, Sequence
from typing import Literal

from backend.engine.context import ContextFormatter
from backend.engine.generation import LocalGenerationAdapter
from backend.engine.retrieval import HybridRetrievalResult, retrieve_for_plan

from .ablation_plans import AblationPlan, AblationVariantPlan
from .benchmark import BenchmarkCase, BenchmarkSuite
from .evaluation import MetricAggregate
from .evaluation_runner import EvaluationRunResult, EvaluationRunner
from .run_plan import RunPlan


ABLATION_EXECUTION_SCHEMA_VERSION = "1"
_VARIANT_IDS = (
    "1_Dense_only",
    "2_BM25_only",
    "3_Hybrid_static",
    "4_Hybrid_adaptive",
    "5_Hybrid_reranker",
    "6_Full_pipeline",
)
_METRIC_IDS = tuple(f"M{number}" for number in range(1, 20))
_HASH_LENGTH = 64


class AblationExecutionError(ValueError):
    """Raised when an ablation execution request or result is invalid."""


def _hash(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) != _HASH_LENGTH or any(c not in "0123456789abcdef" for c in value.lower()):
        raise AblationExecutionError(f"{label} is invalid")
    return value.lower()


def _code(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 64:
        raise AblationExecutionError("error_code is invalid")
    if not (value[0].islower() and all(c.islower() or c.isdigit() or c in "_.-" for c in value)):
        raise AblationExecutionError("error_code is invalid")
    return value


@dataclass(frozen=True)
class AblationVariantResult:
    """The validated result of one planned variant."""

    variant_id: str
    run_fingerprint: str
    status: Literal["succeeded", "partial", "failed"]
    evaluation_result: EvaluationRunResult | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.variant_id not in _VARIANT_IDS:
            raise AblationExecutionError("unknown variant result")
        _hash(self.run_fingerprint, "run_fingerprint")
        if self.status not in {"succeeded", "partial", "failed"}:
            raise AblationExecutionError("variant status is invalid")
        _code(self.error_code)
        if self.evaluation_result is None and self.status != "failed":
            raise AblationExecutionError("successful variant results require an evaluation result")
        if self.evaluation_result is not None and not isinstance(self.evaluation_result, EvaluationRunResult):
            raise AblationExecutionError("evaluation result is invalid")
        if self.evaluation_result is not None and self.evaluation_result.report.run_fingerprint != self.run_fingerprint:
            raise AblationExecutionError("variant evaluation has the wrong run fingerprint")

    @property
    def evaluation(self) -> EvaluationRunResult | None:
        return self.evaluation_result


@dataclass(frozen=True)
class AblationMetricComparison:
    """A metric-preserving comparison row across the six variants."""

    metric_id: str
    state: Literal["comparable", "incomplete", "not_comparable"]
    aggregates: tuple[MetricAggregate | None, ...]
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if self.metric_id not in _METRIC_IDS:
            raise AblationExecutionError("comparison metric_id is invalid")
        if self.state not in {"comparable", "incomplete", "not_comparable"}:
            raise AblationExecutionError("comparison state is invalid")
        if not isinstance(self.aggregates, tuple) or len(self.aggregates) != 6:
            raise AblationExecutionError("comparison must contain six aggregate slots")
        if any(item is not None and not isinstance(item, MetricAggregate) for item in self.aggregates):
            raise AblationExecutionError("comparison aggregate is invalid")
        _code(self.reason_code)

    @property
    def values(self) -> tuple[MetricAggregate | None, ...]:
        """Return the original aggregate objects without reducing their metadata."""

        return self.aggregates


@dataclass(frozen=True)
class AblationRunResult:
    """Immutable in-memory output for one six-variant execution."""

    schema_version: str
    status: Literal["succeeded", "partial", "failed"]
    document_manifest_hash: str
    shared_index_fingerprint: str
    benchmark_id: str
    benchmark_version: str
    variants: tuple[AblationVariantResult, ...]
    metric_comparisons: tuple[AblationMetricComparison, ...]

    def __post_init__(self) -> None:
        if self.schema_version != ABLATION_EXECUTION_SCHEMA_VERSION:
            raise AblationExecutionError("unsupported ablation execution schema version")
        _hash(self.document_manifest_hash, "document_manifest_hash")
        _hash(self.shared_index_fingerprint, "shared_index_fingerprint")
        if self.status not in {"succeeded", "partial", "failed"}:
            raise AblationExecutionError("overall status is invalid")
        if not self.benchmark_id or not self.benchmark_version:
            raise AblationExecutionError("benchmark identity is required")
        if not isinstance(self.variants, tuple) or tuple(item.variant_id for item in self.variants) != _VARIANT_IDS:
            raise AblationExecutionError("variant results must be complete and ordered")
        if any(not isinstance(item, AblationVariantResult) for item in self.variants):
            raise AblationExecutionError("variant result is invalid")
        if len({item.run_fingerprint for item in self.variants}) != 6:
            raise AblationExecutionError("variant run fingerprints must be distinct")
        if not isinstance(self.metric_comparisons, tuple):
            raise AblationExecutionError("metric comparisons must be immutable")
        if tuple(item.metric_id for item in self.metric_comparisons) != _METRIC_IDS:
            raise AblationExecutionError("metric comparisons must contain M1 through M19 in order")

    @property
    def variant_results(self) -> tuple[AblationVariantResult, ...]:
        return self.variants

    @property
    def comparisons(self) -> tuple[AblationMetricComparison, ...]:
        return self.metric_comparisons


def _variant_status(result: EvaluationRunResult) -> Literal["succeeded", "partial", "failed"]:
    statuses = [outcome.status for outcome in result.outcomes]
    succeeded = sum(status == "succeeded" for status in statuses)
    failed = sum(status == "failed" for status in statuses)
    if failed == 0 and succeeded:
        return "succeeded"
    if succeeded:
        return "partial"
    return "failed"


def _validate_result(result: EvaluationRunResult, variant: AblationVariantPlan, suite: BenchmarkSuite) -> None:
    if not isinstance(result, EvaluationRunResult):
        raise AblationExecutionError("evaluation runner returned an invalid result")
    report = result.report
    if report.run_fingerprint != variant.run_fingerprint or report.benchmark_id != suite.benchmark_id or report.benchmark_version != suite.benchmark_version:
        raise AblationExecutionError("evaluation result provenance does not match the variant")
    if len(result.traces) != len(suite.cases) or len(result.outcomes) != len(suite.cases):
        raise AblationExecutionError("evaluation result case count does not match the benchmark")
    if tuple(item.case_id for item in result.outcomes) != tuple(item.case_id for item in suite.cases):
        raise AblationExecutionError("evaluation result case order does not match the benchmark")
    if tuple(item.trace_id for item in result.outcomes) != tuple(item.trace_id for item in result.traces):
        raise AblationExecutionError("evaluation result trace order is inconsistent")
    if tuple(report.trace_ids) != tuple(item.trace_id for item in result.traces):
        raise AblationExecutionError("evaluation report traces are inconsistent")
    for trace in result.traces:
        if (
            trace.run_fingerprint != variant.run_fingerprint
            or trace.index_fingerprint != variant.index_fingerprint
            or trace.document_manifest_hash != variant.run_plan.document_manifest_hash
        ):
            raise AblationExecutionError("evaluation trace provenance does not match the variant")


def _comparison(metric_id: str, variants: tuple[AblationVariantResult, ...]) -> AblationMetricComparison:
    aggregates: list[MetricAggregate | None] = []
    for variant in variants:
        aggregate = None
        if variant.evaluation_result is not None:
            aggregate = next((item for item in variant.evaluation_result.report.aggregates if item.metric_id == metric_id), None)
        aggregates.append(aggregate)
    slots = tuple(aggregates)
    if any(item is None for item in slots):
        return AblationMetricComparison(metric_id, "incomplete", slots, "missing_variant_or_metric_aggregate")
    if metric_id == "M4":
        return AblationMetricComparison(metric_id, "not_comparable", slots, "mode_dependent_score_family")
    core = {(item.metric_name, item.unit, item.optimization_direction, item.reference_kind, item.implementation_version, item.model_identifier) for item in slots if item is not None}
    if len(core) != 1:
        return AblationMetricComparison(metric_id, "not_comparable", slots, "inconsistent_metric_metadata")
    return AblationMetricComparison(metric_id, "comparable", slots)


class AblationRunner:
    """Execute six already-validated plans sequentially without persistence."""

    def __init__(
        self,
        ablation_plan: AblationPlan,
        benchmark_suite: BenchmarkSuite,
        storage_root: object,
        build_report_path: object,
        context_formatter: ContextFormatter,
        generation_adapter: LocalGenerationAdapter,
        *,
        text_adapter: object | None = None,
        image_adapter: object | None = None,
        reranker_adapter: object | None = None,
        text_embedder: object | None = None,
        clock: Callable[[], float] | None = None,
        cpu_monitor_factory: object | None = None,
        ram_monitor_factory: object | None = None,
        retrieval_callable: Callable[..., HybridRetrievalResult] | None = None,
    ) -> None:
        if not isinstance(ablation_plan, AblationPlan):
            raise AblationExecutionError("ablation_plan is invalid")
        if not isinstance(benchmark_suite, BenchmarkSuite):
            raise AblationExecutionError("benchmark_suite is invalid")
        if not hasattr(context_formatter, "format") or not hasattr(generation_adapter, "generate"):
            raise AblationExecutionError("pipeline components are invalid")
        self._plan = ablation_plan
        self._suite = benchmark_suite
        self._storage_root = storage_root
        self._build_report_path = build_report_path
        self._context_formatter = context_formatter
        self._generation_adapter = generation_adapter
        self._text_adapter = text_adapter
        self._image_adapter = image_adapter
        self._reranker_adapter = reranker_adapter
        self._text_embedder = text_embedder
        self._clock = clock
        self._cpu_monitor_factory = cpu_monitor_factory
        self._ram_monitor_factory = ram_monitor_factory
        self._retrieval_callable = retrieval_callable or retrieve_for_plan

    def _preflight(self) -> None:
        self._plan.__post_init__()
        for variant in self._plan.variants:
            variant.run_plan.verify_integrity()
            validator = object.__new__(EvaluationRunner)
            validator._plan = variant.run_plan
            validator._validate_suite(self._suite)

    def _retrieval(self, case: BenchmarkCase, plan: RunPlan) -> HybridRetrievalResult:
        config = plan.resolved_config()
        text_k = case.text_k if "text" in case.modalities else None
        image_k = case.image_k if "image" in case.modalities else None
        text_k = text_k if text_k is not None else (config.text_k if "text" in case.modalities else None)
        image_k = image_k if image_k is not None else (config.image_k if "image" in case.modalities else None)
        return self._retrieval_callable(
            case.query,
            plan,
            self._storage_root,
            build_report_path=self._build_report_path,
            modalities=case.modalities,
            text_adapter=self._text_adapter,
            image_adapter=self._image_adapter,
            text_k=text_k,
            image_k=image_k,
            reranker_adapter=self._reranker_adapter,
        )

    def run(self, benchmark_suite: BenchmarkSuite | None = None) -> AblationRunResult:
        if benchmark_suite is not None:
            if not isinstance(benchmark_suite, BenchmarkSuite):
                raise AblationExecutionError("benchmark_suite is invalid")
            self._suite = benchmark_suite
        self._preflight()
        results: list[AblationVariantResult] = []
        for variant in self._plan.variants:
            try:
                runner_kwargs = {}
                if self._clock is not None:
                    runner_kwargs["clock"] = self._clock
                if self._cpu_monitor_factory is not None:
                    runner_kwargs["cpu_monitor_factory"] = self._cpu_monitor_factory
                if self._ram_monitor_factory is not None:
                    runner_kwargs["ram_monitor_factory"] = self._ram_monitor_factory
                runner = EvaluationRunner(
                    variant.run_plan,
                    self._retrieval,
                    self._context_formatter,
                    self._generation_adapter,
                    text_embedder=self._text_embedder,
                    **runner_kwargs,
                )
                evaluation = runner.run(self._suite)
                _validate_result(evaluation, variant, self._suite)
                results.append(AblationVariantResult(variant.variant_id, variant.run_fingerprint, _variant_status(evaluation), evaluation))
            except Exception:
                results.append(AblationVariantResult(variant.variant_id, variant.run_fingerprint, "failed", None, "ablation_variant_failed"))
        ordered = tuple(results)
        usable = sum(item.evaluation_result is not None for item in ordered)
        status: Literal["succeeded", "partial", "failed"]
        if usable == 0:
            status = "failed"
        elif all(item.status == "succeeded" for item in ordered):
            status = "succeeded"
        else:
            status = "partial"
        comparisons = tuple(_comparison(metric_id, ordered) for metric_id in _METRIC_IDS)
        return AblationRunResult(
            ABLATION_EXECUTION_SCHEMA_VERSION,
            status,
            self._plan.document_manifest_hash,
            self._plan.shared_index_fingerprint,
            self._suite.benchmark_id,
            self._suite.benchmark_version,
            ordered,
            comparisons,
        )


__all__ = [
    "ABLATION_EXECUTION_SCHEMA_VERSION",
    "AblationExecutionError",
    "AblationVariantResult",
    "AblationMetricComparison",
    "AblationRunResult",
    "AblationRunner",
]
