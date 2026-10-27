from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.config import ResolvedRunConfig
from backend.engine.retrieval import query_id_for
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
    service._retrieval_callable = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("private backend detail"))  # type: ignore[method-assign]
    suite = BenchmarkSuite("bench", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5),))

    persisted = service.run_evaluation(plan, suite)

    assert persisted.result.outcomes[0].status == "failed"
    assert persisted.record.status == "failed"
    assert "private backend detail" not in repr(persisted)
    assert service.load_evaluation_result(persisted.record.artifact_id).outcomes[0].status == "failed"


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
