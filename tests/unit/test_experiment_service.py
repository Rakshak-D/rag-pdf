from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.config import ResolvedRunConfig
from backend.engine.retrieval import RetrievalQueryError, query_id_for
from backend.experiments import (
    BenchmarkCase,
    BenchmarkSuite,
    ExperimentArtifactStore,
    ExperimentCatalog,
    build_ablation_plan,
)
from backend.services import (
    ExperimentService,
    PersistedAblationRun,
    PersistedEvaluationRun,
    ServiceInputError,
    ModelAdapterRegistry,
)
from notebooks.config import Config
from tests.unit.test_evaluation_runner import setup_chain


def _service(tmp_path: Path):
    plan, query, retrieval_result, formatter, generation = setup_chain(tmp_path)
    store = ExperimentArtifactStore(tmp_path / "artifacts")
    catalog = ExperimentCatalog(tmp_path / "catalog.sqlite", store)

    def retrieval(query_text, supplied_plan, *_args, **kwargs):
        modalities = kwargs["modalities"]
        text_k = kwargs["text_k"]
        image_k = kwargs["image_k"]
        return replace(
            retrieval_result,
            query_id=query_id_for(query_text, supplied_plan, modalities, text_k, image_k),
            index_fingerprint=supplied_plan.index_fingerprint,
            document_manifest_hash=supplied_plan.document_manifest_hash,
            modalities=modalities,
            effective_text_k=text_k,
            effective_image_k=image_k,
            query_text=query_text,
        )

    service = ExperimentService(catalog, tmp_path / "storage", tmp_path / "build.json", formatter, generation, retrieval_callable=retrieval)
    return service, plan, query


def test_single_evaluation_executes_and_registers_through_catalog(tmp_path):
    service, plan, query = _service(tmp_path)
    suite = BenchmarkSuite("bench", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5),))

    persisted = service.run_evaluation(plan, suite)

    assert isinstance(persisted, PersistedEvaluationRun)
    assert persisted.record.artifact_type == "evaluation_result"
    assert service.load_evaluation_result(persisted.record.artifact_id).report.evaluation_fingerprint == persisted.record.evaluation_fingerprint
    assert service.get_artifact(persisted.record.artifact_id) == persisted.record
    assert service.list_artifacts(benchmark_id="bench") == (persisted.record,)


def test_duplicate_query_identity_is_rejected_before_retrieval(tmp_path):
    service, plan, query = _service(tmp_path)
    calls = []
    service._retrieval_callable = lambda *args, **kwargs: calls.append(args)  # type: ignore[method-assign]
    suite = BenchmarkSuite("bench", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5), BenchmarkCase("case-2", query, ("text",), text_k=5)))

    with pytest.raises(ServiceInputError) as exc:
        service.run_evaluation(plan, suite)
    assert exc.value.error_code == "duplicate_query_identity"
    assert calls == []


def test_valid_failed_cases_are_persisted_without_fabricating_success(tmp_path):
    service, plan, query = _service(tmp_path)
    private_detail = "private retrieval diagnostic detail"
    retrieval_calls = 0

    def fail_retrieval(*_args, **_kwargs):
        nonlocal retrieval_calls
        retrieval_calls += 1
        raise RetrievalQueryError(private_detail)

    service._retrieval_callable = fail_retrieval  # type: ignore[method-assign]
    real_formatter = service._context_formatter

    class CountingFormatter:
        calls = 0

        def format(self, result, supplied_plan):
            self.calls += 1
            return real_formatter.format(result, supplied_plan)

    formatter = CountingFormatter()
    service._context_formatter = formatter  # type: ignore[assignment]
    generation_calls = 0

    class UnexpectedGeneration:
        def generate(self, *_args, **_kwargs):
            nonlocal generation_calls
            generation_calls += 1
            raise AssertionError("generation must not run after retrieval failure")

    service._generation_adapter = UnexpectedGeneration()  # type: ignore[assignment]
    suite = BenchmarkSuite("bench", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5),))

    persisted = service.run_evaluation(plan, suite)
    outcome = persisted.result.outcomes[0]
    trace = persisted.result.traces[0]
    loaded = service.load_evaluation_result(persisted.record.artifact_id)

    assert retrieval_calls == 1
    assert generation_calls == 0
    assert formatter.calls == 0
    assert outcome.status == "failed"
    assert outcome.reached_stage == "retrieval"
    assert outcome.trace_id == trace.trace_id
    assert outcome.error_code == "retrieval_failed"
    assert trace.failed_stage == "retrieval"
    assert trace.error_code == "retrieval_failed"
    assert trace.failure is not None and trace.failure.stage == "retrieval"
    assert trace.query_id == query_id_for(query, plan, ("text",), 5, None)
    assert trace.run_fingerprint == plan.run_fingerprint
    assert trace.context_fingerprint is None
    assert trace.prompt_fingerprint is None
    assert trace.context is None
    assert trace.generation is None
    assert trace.evidence == ()
    assert persisted.result.report.run_fingerprint == plan.run_fingerprint
    assert len(persisted.result.report.observations) == 19
    assert {item.metric_id for item in persisted.result.report.observations} == {f"M{number}" for number in range(1, 20)}
    assert all(item.status in {"computed", "skipped", "unavailable", "failed"} for item in persisted.result.report.observations)
    assert all(item.trace_id == trace.trace_id for item in persisted.result.report.observations)
    retrieval_latency = next(item for item in persisted.result.report.observations if item.metric_id == "M3")
    generation_latency = next(item for item in persisted.result.report.observations if item.metric_id == "M16")
    assert retrieval_latency.status == "failed" and retrieval_latency.value is None
    assert generation_latency.status == "skipped" and generation_latency.value is None
    assert persisted.record.status == "failed"
    assert loaded.outcomes == persisted.result.outcomes
    assert loaded.traces == persisted.result.traces
    assert loaded.report.evaluation_fingerprint == persisted.result.report.evaluation_fingerprint
    assert loaded.report.observations == persisted.result.report.observations
    assert loaded.report.aggregates == persisted.result.report.aggregates
    assert loaded.outcomes[0].reached_stage == "retrieval"
    assert "private retrieval diagnostic detail" not in repr(persisted)
    assert private_detail not in repr(loaded)
    artifact_path = tmp_path / "artifacts" / persisted.record.relative_path
    assert private_detail.encode("utf-8") not in artifact_path.read_bytes()


def test_generation_failure_persists_and_explicit_second_invocation_succeeds(tmp_path):
    service, plan, query = _service(tmp_path)
    delegate = service._generation_adapter
    generation_calls = 0
    retrieval_results = []
    original_retrieval = service._retrieval_callable
    real_formatter = service._context_formatter
    formatter_calls = 0
    private_detail = "private generation diagnostic detail"

    def recording_retrieval(*args, **kwargs):
        result = original_retrieval(*args, **kwargs)
        retrieval_results.append(result)
        return result

    class CountingFormatter:
        def format(self, result, supplied_plan):
            nonlocal formatter_calls
            formatter_calls += 1
            return real_formatter.format(result, supplied_plan)

    class FailOnceThenDelegate:
        def generate(self, context, supplied_plan):
            nonlocal generation_calls
            generation_calls += 1
            if generation_calls == 1:
                raise RuntimeError(private_detail)
            return delegate.generate(context, supplied_plan)

    service._retrieval_callable = recording_retrieval  # type: ignore[method-assign]
    service._generation_adapter = FailOnceThenDelegate()  # type: ignore[assignment]
    service._context_formatter = CountingFormatter()  # type: ignore[assignment]
    suite = BenchmarkSuite("bench", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5),))

    failed = service.run_evaluation(plan, suite)

    assert len(retrieval_results) == 1
    assert retrieval_results[0].query_text == query
    assert retrieval_results[0].index_fingerprint == plan.index_fingerprint
    assert retrieval_results[0].document_manifest_hash == plan.document_manifest_hash
    assert tuple(hit.record_id for hit in retrieval_results[0].text_hits) == ("chunk-1",)
    assert generation_calls == 1  # The first service invocation did not retry generation.
    assert formatter_calls == 1
    failed_outcome = failed.result.outcomes[0]
    failed_trace = failed.result.traces[0]
    assert failed_outcome.status == "failed"
    assert failed_outcome.reached_stage == "generation"
    assert failed_outcome.error_code == "generation_failed"
    assert failed_outcome.trace_id == failed_trace.trace_id
    assert failed_trace.failed_stage == "generation"
    assert failed_trace.error_code == "generation_failed"
    assert failed_trace.failure is not None and failed_trace.failure.stage == "generation"
    assert failed_trace.query_id == query_id_for(query, plan, ("text",), 5, None)
    assert failed_trace.run_fingerprint == plan.run_fingerprint
    assert failed_trace.context is not None
    assert failed_trace.context_fingerprint is not None
    assert failed_trace.context.selected_text_count == 1
    assert tuple(item.record_id for item in failed_trace.evidence) == ("chunk-1",)
    assert failed_trace.generation is None
    failed_m16 = next(item for item in failed.result.report.observations if item.metric_id == "M16")
    assert failed_m16.status == "failed" and failed_m16.value is None
    assert failed.record.status == "failed"
    assert service.get_artifact(failed.record.artifact_id) == failed.record
    loaded_failed = service.load_evaluation_result(failed.record.artifact_id)
    assert loaded_failed.outcomes == failed.result.outcomes
    assert loaded_failed.traces == failed.result.traces
    assert loaded_failed.traces[0].evidence == failed_trace.evidence
    assert loaded_failed.report.evaluation_fingerprint == failed.result.report.evaluation_fingerprint
    assert loaded_failed.report.observations == failed.result.report.observations
    assert loaded_failed.report.aggregates == failed.result.report.aggregates
    assert private_detail not in repr(failed)
    assert private_detail not in repr(loaded_failed)
    failed_artifact_path = tmp_path / "artifacts" / failed.record.relative_path
    assert private_detail.encode("utf-8") not in failed_artifact_path.read_bytes()

    # This second call is an explicit invocation by the test, not a runner retry.
    succeeded = service.run_evaluation(plan, suite)

    assert len(retrieval_results) == 2
    assert generation_calls == 2
    assert formatter_calls == 2
    assert succeeded.result.outcomes[0].status == "succeeded"
    assert succeeded.result.outcomes[0].reached_stage == "generation"
    assert succeeded.result.traces[0].generation is not None
    assert succeeded.result.traces[0].generation.status == "succeeded"
    assert succeeded.record.status == "succeeded"
    assert succeeded.record.artifact_id != failed.record.artifact_id
    assert service.get_artifact(succeeded.record.artifact_id) == succeeded.record
    loaded_succeeded = service.load_evaluation_result(succeeded.record.artifact_id)
    assert loaded_succeeded == succeeded.result
    assert loaded_succeeded.report.run_fingerprint == plan.run_fingerprint
    assert service.get_artifact(failed.record.artifact_id) == failed.record
    assert service.load_evaluation_result(failed.record.artifact_id) == failed.result
    records = {record.artifact_id: record for record in service.list_artifacts(benchmark_id="bench")}
    assert records[failed.record.artifact_id].status == "failed"
    assert records[succeeded.record.artifact_id].status == "succeeded"


def test_six_variant_ablation_delegates_and_registers_all_variants(tmp_path):
    service, base_plan, query = _service(tmp_path)
    base = base_plan.resolved_config()
    ablation_plan = build_ablation_plan(base, base_plan.document_manifest_hash)
    suite = BenchmarkSuite("bench", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5),))

    persisted = service.run_ablation(ablation_plan, suite)

    assert isinstance(persisted, PersistedAblationRun)
    assert tuple(item.variant_id for item in persisted.result.variants) == tuple(item.variant_id for item in ablation_plan.variants)
    assert len(service.list_comparisons(persisted.record.artifact_id)) == 19
    assert service.load_ablation_result(persisted.record.artifact_id, ablation_plan=ablation_plan).benchmark_id == "bench"


def test_service_rejects_invalid_plan_before_pipeline_work(tmp_path):
    service, plan, query = _service(tmp_path)
    suite = BenchmarkSuite("bench", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5),))
    object.__setattr__(plan, "run_fingerprint", "0" * 64)

    with pytest.raises(ServiceInputError):
        service.run_evaluation(plan, suite)


def test_registry_backed_retrieval_resolves_plan_compatible_adapters(tmp_path):
    service, plan, query = _service(tmp_path)
    retrieval_result = service._retrieval_callable(
        query,
        plan,
        service._storage_root,
        build_report_path=service._build_report_path,
        modalities=("text",),
        text_k=5,
        image_k=None,
        reranker_adapter=None,
    )
    captured = []
    text_adapter = SimpleNamespace(model_name="text", load=lambda: None, embed_query=lambda value: None, encode_text=lambda value: None)
    image_adapter = SimpleNamespace(model_name="image", pretrained="pretrained", load=lambda: None, embed_query=lambda value: None)
    reranker_adapter = SimpleNamespace(model_name="reranker", load=lambda: None, score_pairs=lambda query, texts: [])
    registry = ModelAdapterRegistry(
        text_factory=lambda _: text_adapter,
        image_factory=lambda _: image_adapter,
        reranker_factory=lambda _: reranker_adapter,
    )
    service = ExperimentService(
        service._catalog,
        service._storage_root,
        service._build_report_path,
        service._context_formatter,
        service._generation_adapter,
        model_registry=registry,
        retrieval_callable=lambda *args, **kwargs: captured.append(kwargs) or retrieval_result,
    )
    case = BenchmarkCase("case-1", query, ("text",), text_k=5)
    assert service._retrieval(case, plan) is retrieval_result
    assert captured[0]["text_adapter"] is text_adapter
    assert captured[0]["image_adapter"] is image_adapter
    assert captured[0]["reranker_adapter"] is reranker_adapter


def test_registry_and_direct_model_adapters_are_rejected_together(tmp_path):
    service, plan, _ = _service(tmp_path)
    registry = ModelAdapterRegistry()
    with pytest.raises(ServiceInputError) as error:
        ExperimentService(
            service._catalog,
            service._storage_root,
            service._build_report_path,
            service._context_formatter,
            service._generation_adapter,
            text_adapter=object(),
            model_registry=registry,
        )
    assert error.value.error_code == "conflicting_model_adapters"
