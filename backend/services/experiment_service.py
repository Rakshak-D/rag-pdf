"""Synchronous application orchestration for evaluation experiments."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backend.engine.context import ContextFormatter
from backend.engine.generation import LocalGenerationAdapter
from backend.engine.retrieval import HybridRetrievalResult, query_id_for, retrieve_for_plan

from backend.experiments.ablation_plans import AblationPlan, AblationVariantPlan
from backend.experiments.ablation_runner import AblationRunResult, AblationRunner, _comparison
from backend.experiments.benchmark import BenchmarkCase, BenchmarkSuite
from backend.experiments.catalog import (
    CatalogArtifactRecord,
    CatalogComparisonRecord,
    CatalogMetricRecord,
    ExperimentCatalog,
)
from backend.experiments.evaluation import EvaluationReport, MetricAggregate, MetricObservation, verify_evaluation_fingerprint
from backend.experiments.evaluation_runner import EvaluationCaseOutcome, EvaluationRunResult, EvaluationRunner
from backend.experiments.run_plan import RunPlan
from backend.experiments.tracing import QueryTrace


class ExperimentServiceError(ValueError):
    """Base error with a bounded stable public error code."""

    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(error_code)


class ServiceInputError(ExperimentServiceError):
    """Invalid plans, benchmark input, or service dependencies."""


class ServiceExecutionError(ExperimentServiceError):
    """Unexpected retrieval or evaluation execution failure."""


class ServiceResultIntegrityError(ExperimentServiceError):
    """Runner output failed application-boundary provenance validation."""


class ServicePersistenceError(ExperimentServiceError):
    """Artifact publication or catalog registration failed."""


class ServiceCatalogError(ExperimentServiceError):
    """A catalog or artifact read failed at the service boundary."""


def _stable_status(outcomes: tuple[EvaluationCaseOutcome, ...]) -> str:
    succeeded = sum(item.status == "succeeded" for item in outcomes)
    failed = sum(item.status == "failed" for item in outcomes)
    if failed == 0 and succeeded:
        return "succeeded"
    if succeeded:
        return "partial"
    return "failed"


def _effective_limits(case: BenchmarkCase, plan: RunPlan) -> tuple[int | None, int | None]:
    config = plan.resolved_config()
    text_k = case.text_k if "text" in case.modalities else None
    image_k = case.image_k if "image" in case.modalities else None
    return (
        text_k if text_k is not None else (config.text_k if "text" in case.modalities else None),
        image_k if image_k is not None else (config.image_k if "image" in case.modalities else None),
    )


def _validate_suite_for_plan(plan: RunPlan, suite: BenchmarkSuite) -> tuple[BenchmarkCase, ...]:
    if not isinstance(suite, BenchmarkSuite):
        raise ServiceInputError("benchmark_invalid")
    cases = tuple(suite.cases)
    identities = [query_id_for(case.query, plan, case.modalities, *_effective_limits(case, plan)) for case in cases]
    if len(set(identities)) != len(identities):
        raise ServiceInputError("duplicate_query_identity")
    return cases


def _validate_plan(plan: RunPlan) -> None:
    if not isinstance(plan, RunPlan):
        raise ServiceInputError("run_plan_invalid")
    try:
        plan.verify_integrity()
    except Exception as exc:
        raise ServiceInputError("run_plan_invalid") from exc


def _validate_ablation_plan(plan: AblationPlan) -> None:
    if not isinstance(plan, AblationPlan):
        raise ServiceInputError("ablation_plan_invalid")
    try:
        plan.__post_init__()
        for variant in plan.variants:
            variant.run_plan.verify_integrity()
    except Exception as exc:
        raise ServiceInputError("ablation_plan_invalid") from exc


def _validate_evaluation_result(result: EvaluationRunResult, plan: RunPlan, suite: BenchmarkSuite) -> None:
    if not isinstance(result, EvaluationRunResult) or not isinstance(result.report, EvaluationReport):
        raise ServiceResultIntegrityError("evaluation_result_invalid")
    report = result.report
    cases = _validate_suite_for_plan(plan, suite)
    if report.run_fingerprint != plan.run_fingerprint or report.benchmark_id != suite.benchmark_id or report.benchmark_version != suite.benchmark_version:
        raise ServiceResultIntegrityError("evaluation_provenance_mismatch")
    if len(result.traces) != len(cases) or len(result.outcomes) != len(cases):
        raise ServiceResultIntegrityError("evaluation_case_count_mismatch")
    if tuple(item.case_id for item in result.outcomes) != tuple(item.case_id for item in cases):
        raise ServiceResultIntegrityError("evaluation_case_order_mismatch")
    trace_ids = tuple(item.trace_id for item in result.traces)
    if tuple(report.trace_ids) != trace_ids or tuple(item.trace_id for item in result.outcomes) != trace_ids or len(set(trace_ids)) != len(trace_ids):
        raise ServiceResultIntegrityError("evaluation_trace_order_mismatch")
    if any(not isinstance(item, QueryTrace) for item in result.traces) or any(not isinstance(item, EvaluationCaseOutcome) for item in result.outcomes):
        raise ServiceResultIntegrityError("evaluation_trace_or_outcome_invalid")
    for case, trace in zip(cases, result.traces):
        text_k, image_k = _effective_limits(case, plan)
        expected_query_id = query_id_for(case.query, plan, case.modalities, text_k, image_k)
        if trace.query_id != expected_query_id or trace.run_fingerprint != plan.run_fingerprint or trace.index_fingerprint != plan.index_fingerprint or trace.document_manifest_hash != plan.document_manifest_hash:
            raise ServiceResultIntegrityError("evaluation_trace_provenance_mismatch")
    if any(not isinstance(item, MetricObservation) for item in report.observations) or any(not isinstance(item, MetricAggregate) for item in report.aggregates):
        raise ServiceResultIntegrityError("evaluation_metrics_invalid")
    try:
        verify_evaluation_fingerprint(report)
    except Exception as exc:
        raise ServiceResultIntegrityError("evaluation_fingerprint_invalid") from exc


def _validate_ablation_result(result: AblationRunResult, plan: AblationPlan, suite: BenchmarkSuite) -> None:
    if not isinstance(result, AblationRunResult):
        raise ServiceResultIntegrityError("ablation_result_invalid")
    _validate_ablation_plan(plan)
    _validate_suite_for_plan(plan.variants[0].run_plan, suite)
    if result.document_manifest_hash != plan.document_manifest_hash or result.shared_index_fingerprint != plan.shared_index_fingerprint or result.benchmark_id != suite.benchmark_id or result.benchmark_version != suite.benchmark_version:
        raise ServiceResultIntegrityError("ablation_provenance_mismatch")
    if tuple(item.variant_id for item in result.variants) != tuple(item.variant_id for item in plan.variants):
        raise ServiceResultIntegrityError("ablation_variant_order_mismatch")
    for variant_result, variant_plan in zip(result.variants, plan.variants):
        if variant_result.run_fingerprint != variant_plan.run_fingerprint:
            raise ServiceResultIntegrityError("ablation_variant_provenance_mismatch")
        if variant_result.evaluation_result is not None:
            _validate_evaluation_result(variant_result.evaluation_result, variant_plan.run_plan, suite)
            if variant_result.status != _stable_status(variant_result.evaluation_result.outcomes):
                raise ServiceResultIntegrityError("ablation_variant_status_mismatch")
    usable = sum(item.evaluation_result is not None for item in result.variants)
    expected_status = "failed" if usable == 0 else "succeeded" if all(item.status == "succeeded" for item in result.variants) else "partial"
    if result.status != expected_status:
        raise ServiceResultIntegrityError("ablation_status_mismatch")
    expected_comparisons = tuple(_comparison(f"M{number}", result.variants) for number in range(1, 20))
    if result.metric_comparisons != expected_comparisons:
        raise ServiceResultIntegrityError("ablation_comparison_mismatch")


def _evaluation_status(result: EvaluationRunResult) -> str:
    return _stable_status(result.outcomes)


@dataclass(frozen=True)
class PersistedEvaluationRun:
    result: EvaluationRunResult
    record: CatalogArtifactRecord

    def __post_init__(self) -> None:
        if not isinstance(self.result, EvaluationRunResult) or not isinstance(self.record, CatalogArtifactRecord):
            raise ServiceResultIntegrityError("persisted_evaluation_invalid")
        report = self.result.report
        if self.record.artifact_type != "evaluation_result" or self.record.artifact_id == "" or self.record.benchmark_id != report.benchmark_id or self.record.benchmark_version != report.benchmark_version or self.record.run_fingerprint != report.run_fingerprint or self.record.evaluation_fingerprint != report.evaluation_fingerprint or self.record.status != _evaluation_status(self.result):
            raise ServiceResultIntegrityError("persisted_evaluation_provenance_mismatch")


@dataclass(frozen=True)
class PersistedAblationRun:
    result: AblationRunResult
    record: CatalogArtifactRecord

    def __post_init__(self) -> None:
        if not isinstance(self.result, AblationRunResult) or not isinstance(self.record, CatalogArtifactRecord):
            raise ServiceResultIntegrityError("persisted_ablation_invalid")
        if self.record.artifact_type != "ablation_result" or self.record.artifact_id == "" or self.record.benchmark_id != self.result.benchmark_id or self.record.benchmark_version != self.result.benchmark_version or self.record.document_manifest_hash != self.result.document_manifest_hash or self.record.shared_index_fingerprint != self.result.shared_index_fingerprint or self.record.status != self.result.status:
            raise ServiceResultIntegrityError("persisted_ablation_provenance_mismatch")


class ExperimentService:
    """Thin synchronous boundary joining runners to the persistent catalog."""

    def __init__(
        self,
        catalog: ExperimentCatalog,
        storage_root: str | Path,
        build_report_path: str | Path,
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
        if not isinstance(catalog, ExperimentCatalog):
            raise ServiceInputError("catalog_invalid")
        if not isinstance(storage_root, (str, Path)) or not str(storage_root).strip() or not isinstance(build_report_path, (str, Path)) or not str(build_report_path).strip():
            raise ServiceInputError("storage_path_invalid")
        if not hasattr(context_formatter, "format") or not hasattr(generation_adapter, "generate"):
            raise ServiceInputError("pipeline_components_invalid")
        if retrieval_callable is not None and not callable(retrieval_callable):
            raise ServiceInputError("retrieval_callable_invalid")
        self._catalog = catalog
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

    def _retrieval(self, case: BenchmarkCase, plan: RunPlan) -> HybridRetrievalResult:
        text_k, image_k = _effective_limits(case, plan)
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

    def _runner_kwargs(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        if self._clock is not None:
            values["clock"] = self._clock
        if self._cpu_monitor_factory is not None:
            values["cpu_monitor_factory"] = self._cpu_monitor_factory
        if self._ram_monitor_factory is not None:
            values["ram_monitor_factory"] = self._ram_monitor_factory
        return values

    def run_evaluation(self, run_plan: RunPlan, benchmark_suite: BenchmarkSuite) -> PersistedEvaluationRun:
        _validate_plan(run_plan)
        _validate_suite_for_plan(run_plan, benchmark_suite)
        try:
            runner = EvaluationRunner(run_plan, self._retrieval, self._context_formatter, self._generation_adapter, text_embedder=self._text_embedder, **self._runner_kwargs())
            result = runner.run(benchmark_suite)
        except ExperimentServiceError:
            raise
        except Exception as exc:
            raise ServiceExecutionError("evaluation_execution_failed") from exc
        _validate_evaluation_result(result, run_plan, benchmark_suite)
        try:
            record = self._catalog.save_evaluation_result(result)
        except Exception as exc:
            raise ServicePersistenceError("evaluation_persistence_failed") from exc
        return PersistedEvaluationRun(result, record)

    def run_ablation(self, ablation_plan: AblationPlan, benchmark_suite: BenchmarkSuite) -> PersistedAblationRun:
        _validate_ablation_plan(ablation_plan)
        _validate_suite_for_plan(ablation_plan.variants[0].run_plan, benchmark_suite)
        try:
            runner = AblationRunner(
                ablation_plan,
                benchmark_suite,
                self._storage_root,
                self._build_report_path,
                self._context_formatter,
                self._generation_adapter,
                text_adapter=self._text_adapter,
                image_adapter=self._image_adapter,
                reranker_adapter=self._reranker_adapter,
                text_embedder=self._text_embedder,
                clock=self._clock,
                cpu_monitor_factory=self._cpu_monitor_factory,
                ram_monitor_factory=self._ram_monitor_factory,
                retrieval_callable=self._retrieval_callable,
            )
            result = runner.run()
        except ExperimentServiceError:
            raise
        except Exception as exc:
            raise ServiceExecutionError("ablation_execution_failed") from exc
        _validate_ablation_result(result, ablation_plan, benchmark_suite)
        try:
            record = self._catalog.save_ablation_result(result, ablation_plan=ablation_plan)
        except Exception as exc:
            raise ServicePersistenceError("ablation_persistence_failed") from exc
        return PersistedAblationRun(result, record)

    def get_artifact(self, artifact_id: str) -> CatalogArtifactRecord:
        return self._catalog.get_artifact(artifact_id)

    def list_artifacts(self, **filters: Any) -> tuple[CatalogArtifactRecord, ...]:
        return self._catalog.list_artifacts(**filters)

    def load_evaluation_result(self, artifact_id: str) -> EvaluationRunResult:
        return self._catalog.load_evaluation_result(artifact_id)

    def load_ablation_result(self, artifact_id: str, *, ablation_plan: AblationPlan) -> AblationRunResult:
        return self._catalog.load_ablation_result(artifact_id, ablation_plan=ablation_plan)

    def list_metric_aggregates(self, artifact_id: str, *, variant_id: str | None = None) -> tuple[CatalogMetricRecord, ...]:
        return self._catalog.list_metric_aggregates(artifact_id, variant_id=variant_id)

    def list_comparisons(self, artifact_id: str) -> tuple[CatalogComparisonRecord, ...]:
        return self._catalog.list_comparisons(artifact_id)


__all__ = [
    "ExperimentService",
    "PersistedEvaluationRun",
    "PersistedAblationRun",
    "ExperimentServiceError",
    "ServiceInputError",
    "ServiceExecutionError",
    "ServiceResultIntegrityError",
    "ServicePersistenceError",
    "ServiceCatalogError",
]
