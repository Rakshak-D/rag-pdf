from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
from threading import Barrier, Event, Lock
import time

from starlette.testclient import TestClient

from backend.app import create_app
from backend.embeddings.image import ImageEmbeddingAdapter
from backend.embeddings.text import TextEmbeddingAdapter
from backend.engine.reranking import CrossEncoderReranker
from backend.experiments import ExperimentArtifactStore, build_run_plan
from backend.experiments.cpu_metrics import CpuUsageResult
from backend.experiments.memory_metrics import RamUsageResult
from backend.services import ExperimentJobManager, ExperimentService, ModelAdapterRegistry
from tests.unit.test_experiment_service import _service


class _FakeTextModel:
    def encode(self, sentences, **_kwargs):
        if isinstance(sentences, str):
            return [1.0, 0.0]
        return [[1.0, 0.0] for _ in sentences]


class _CpuMonitor:
    def start(self):
        return None

    def stop(self):
        return CpuUsageResult("unavailable", reason_code="cpu_samples_unavailable")


class _RamMonitor:
    def start(self):
        return None

    def stop(self):
        return RamUsageResult("unavailable", reason_code="ram_samples_unavailable")


def _registry(config, monkeypatch):
    import backend.embeddings.text as text_module

    monkeypatch.setattr(text_module, "_default_device", lambda: "cpu")
    created = Counter()
    adapters = {}
    lock = Lock()

    def text_factory(value):
        with lock:
            created["text"] += 1
        adapter = TextEmbeddingAdapter(value, model_loader=lambda *_: _FakeTextModel())
        adapters["text"] = adapter
        return adapter

    def image_factory(value):
        with lock:
            created["image"] += 1
        adapter = ImageEmbeddingAdapter(value, model_loader=lambda *_: None)
        adapters["image"] = adapter
        return adapter

    def reranker_factory(value):
        with lock:
            created["reranker"] += 1
        adapter = CrossEncoderReranker(value, loader=lambda _name: None)
        adapters["reranker"] = adapter
        return adapter

    return ModelAdapterRegistry(text_factory=text_factory, image_factory=image_factory, reranker_factory=reranker_factory), created, adapters


def _terminal_job(client, job_id, headers, *, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/jobs/{job_id}", headers=headers)
        assert response.status_code == 200
        record = response.json()["job"]
        if record["status"] in {"succeeded", "partial", "failed", "cancelled", "interrupted"}:
            return record
        Event().wait(0.01)
    raise AssertionError("job did not reach a terminal state before the deadline")


def _job_payload(plan, query, case_id):
    return {
        "run_plan": json.loads(plan.to_serializable_json()),
        "benchmark_suite": {
            "benchmark_id": "concurrent-bench",
            "benchmark_version": "v1",
            "cases": [{"case_id": case_id, "query": query, "modalities": ["text"], "text_k": 5, "required_pages": []}],
        },
    }


def _metric_projection(row):
    return (row.metric_id, row.metric_name, row.status, row.value, row.unit, row.optimization_direction, row.reference_kind, row.implementation_version, row.eligible_count, row.skipped_count, row.failed_count, row.unavailable_count, row.model_identifier, row.methodology_note)


def _registry_backed_service(tmp_path, monkeypatch, retrieval_wrapper):
    base_service, base_plan, query = _service(tmp_path)
    config = base_plan.resolved_config().model_copy(update={"use_reranker": True})
    plan = build_run_plan(config, base_plan.document_manifest_hash)
    registry, factory_calls, adapters = _registry(config, monkeypatch)

    def retrieval(query_text, run_plan, storage_root, **kwargs):
        return retrieval_wrapper(base_service._retrieval_callable, query_text, run_plan, storage_root, **kwargs)

    service = ExperimentService(
        base_service._catalog,
        base_service._storage_root,
        base_service._build_report_path,
        base_service._context_formatter,
        base_service._generation_adapter,
        cpu_monitor_factory=_CpuMonitor,
        ram_monitor_factory=_RamMonitor,
        model_registry=registry,
        retrieval_callable=retrieval,
    )
    return service, plan, query, registry, factory_calls, adapters


def test_job_submission_returns_202_and_polls_without_payload_leak(tmp_path: Path):
    service, plan, query = _service(tmp_path)
    manager = ExperimentJobManager(service, tmp_path / "jobs.sqlite")
    suite = {"benchmark_id": "bench", "benchmark_version": "1", "cases": [{"case_id": "case-1", "query": query, "modalities": ["text"], "text_k": 5, "required_pages": []}]}
    with TestClient(create_app(service, api_token="test-token", job_manager=manager)) as client:
        response = client.post(
            "/api/v1/jobs/evaluations",
            headers={"Authorization": "Bearer test-token"},
            json={"run_plan": json.loads(plan.to_serializable_json()), "benchmark_suite": suite},
        )
        assert response.status_code == 202
        assert response.headers["location"].endswith(response.json()["job"]["job_id"])
        job_id = response.json()["job"]["job_id"]
        for _ in range(100):
            polled = client.get(f"/api/v1/jobs/{job_id}", headers={"Authorization": "Bearer test-token"})
            if polled.json()["job"]["status"] in {"succeeded", "partial", "failed"}:
                break
            Event().wait(0.01)
        assert polled.status_code == 200
        assert polled.json()["job"]["artifact_id"]
        assert query not in polled.text


def test_job_routes_require_configuration_and_authentication(tmp_path: Path):
    service, _, _ = _service(tmp_path)
    client = TestClient(create_app(service, api_token="test-token"))
    assert client.get("/api/v1/jobs").json() == {"error": {"code": "authentication_required"}}
    assert client.get("/api/v1/jobs", headers={"Authorization": "Bearer test-token"}).json() == {"error": {"code": "job_execution_unconfigured"}}


def test_runtime_model_status_is_authenticated_and_does_not_warm_models(tmp_path: Path):
    service, plan, _ = _service(tmp_path)
    calls = []
    registry = ModelAdapterRegistry(text_factory=lambda config: calls.append(config.text_embed_model) or TextEmbeddingAdapter(config, model_loader=lambda *_: None))
    service_with_registry = type(service)(
        service._catalog,
        service._storage_root,
        service._build_report_path,
        service._context_formatter,
        service._generation_adapter,
        model_registry=registry,
        retrieval_callable=service._retrieval_callable,
    )
    client = TestClient(create_app(service_with_registry, api_token="test-token"))
    assert client.get("/api/v1/runtime/models").status_code == 401
    response = client.get("/api/v1/runtime/models", headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 200
    assert response.json() == {"components": []}
    assert calls == []


def test_runtime_model_status_is_read_only_and_safe(tmp_path: Path):
    service, _, _ = _service(tmp_path)
    client = TestClient(create_app(service, api_token="test-token"))
    response = client.get("/api/v1/runtime/models", headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 503
    assert response.json() == {"error": {"code": "runtime_registry_unconfigured"}}


def test_two_http_jobs_overlap_share_registry_and_persist_distinct_results(tmp_path: Path, monkeypatch):
    rendezvous = Barrier(2)
    both_entered = Event()
    arrivals = []
    arrivals_lock = Lock()
    observed_adapters = []
    observations_lock = Lock()

    def synchronized_retrieval(base_retrieval, query_text, run_plan, storage_root, **kwargs):
        with observations_lock:
            observed_adapters.append((kwargs.get("text_adapter"), kwargs.get("image_adapter"), kwargs.get("reranker_adapter")))
        with arrivals_lock:
            arrivals.append(query_text)
            if len(arrivals) == 2:
                both_entered.set()
        rendezvous.wait(timeout=10)
        return base_retrieval(query_text, run_plan, storage_root, **kwargs)

    service, plan, base_query, registry, factory_calls, adapters = _registry_backed_service(tmp_path, monkeypatch, synchronized_retrieval)
    manager = ExperimentJobManager(service, tmp_path / "jobs.sqlite", worker_count=2, max_pending_jobs=4)
    headers = {"Authorization": "Bearer test-token"}
    queries = (f"{base_query} concurrent alpha", f"{base_query} concurrent beta")
    artifact_store = ExperimentArtifactStore(tmp_path / "artifacts")

    try:
        with TestClient(create_app(service, api_token="test-token", job_manager=manager)) as client:
            try:
                before = client.get("/api/v1/runtime/models", headers=headers)
                assert before.status_code == 200 and before.json() == {"components": []}
                assert factory_calls == Counter()

                responses = [
                    client.post(
                        "/api/v1/jobs/evaluations",
                        headers=headers,
                        json=_job_payload(plan, query, f"case-{index}"),
                    )
                    for index, query in enumerate(queries, 1)
                ]
                assert all(response.status_code == 202 for response in responses)
                job_ids = [response.json()["job"]["job_id"] for response in responses]
                for response, job_id in zip(responses, job_ids):
                    assert response.headers["location"] == f"/api/v1/jobs/{job_id}"
                assert both_entered.wait(8), "the two job workers did not overlap in retrieval"

                records = [_terminal_job(client, job_id, headers) for job_id in job_ids]
                assert all(record["status"] == "succeeded" for record in records)
                assert all(record["artifact_id"] and record["result_status"] == "succeeded" and record["error_code"] is None for record in records)
                assert Counter(arrivals) == Counter(queries)
                assert len(observed_adapters) == 2
                for field_index, kind in enumerate(("text", "image", "reranker")):
                    values = [item[field_index] for item in observed_adapters]
                    assert values[0] is adapters[kind]
                    assert values[1] is values[0]
                    assert factory_calls[kind] == 1

                artifact_ids = [record["artifact_id"] for record in records]
                assert artifact_ids[0] != artifact_ids[1]
                catalog_records = [service.get_artifact(artifact_id) for artifact_id in artifact_ids]
                assert all(record.artifact_type == "evaluation_result" for record in catalog_records)
                assert len(service.list_artifacts(benchmark_id="concurrent-bench")) == 2
                for artifact_id, catalog_record in zip(artifact_ids, catalog_records):
                    result = service.load_evaluation_result(artifact_id)
                    assert result.report.run_fingerprint == plan.run_fingerprint
                    assert catalog_record.evaluation_fingerprint == result.report.evaluation_fingerprint
                    metrics = service.list_metric_aggregates(artifact_id)
                    assert len(metrics) == len(result.report.aggregates) == 19
                    indexed_metrics = {item.metric_id: _metric_projection(item) for item in metrics}
                    report_metrics = {item.metric_id: _metric_projection(item) for item in result.report.aggregates}
                    assert indexed_metrics == report_metrics

                assert len(manager.list_jobs(status="succeeded")) == 2
                stored_artifacts = artifact_store.list_artifacts("evaluation_result")
                assert {item.artifact_id for item in stored_artifacts} == set(artifact_ids)
                count_before_snapshot = factory_calls.copy()
                after = client.get("/api/v1/runtime/models", headers=headers)
                assert after.status_code == 200
                assert {item["component_kind"] for item in after.json()["components"]} == {"text", "image", "reranker"}
                assert factory_calls == count_before_snapshot

                original_text_adapter = adapters["text"]
                alternate = registry.get_text_adapter(plan.resolved_config().model_copy(update={"text_embed_batch_size": plan.resolved_config().text_embed_batch_size + 1}))
                assert alternate is not original_text_adapter
                assert factory_calls["text"] == 2
            finally:
                rendezvous.abort()
    finally:
        rendezvous.abort()
        manager.shutdown()


def test_one_worker_failure_does_not_stop_concurrent_success(tmp_path: Path, monkeypatch):
    rendezvous = Barrier(2)
    both_entered = Event()
    arrivals = []
    arrivals_lock = Lock()

    class ControlledWorkerAbort(BaseException):
        pass

    def fail_one_retrieval(base_retrieval, query_text, run_plan, storage_root, **kwargs):
        with arrivals_lock:
            arrivals.append(query_text)
            if len(arrivals) == 2:
                both_entered.set()
        rendezvous.wait(timeout=10)
        if query_text == "controlled-failure-query private prompt":
            raise ControlledWorkerAbort("private exception C:\\private\\model-cache")
        return base_retrieval(query_text, run_plan, storage_root, **kwargs)

    service, plan, _, registry, factory_calls, _adapters = _registry_backed_service(tmp_path, monkeypatch, fail_one_retrieval)
    manager = ExperimentJobManager(service, tmp_path / "jobs.sqlite", worker_count=2, max_pending_jobs=4)
    artifact_store = ExperimentArtifactStore(tmp_path / "artifacts")
    headers = {"Authorization": "Bearer test-token"}
    failing_query = "controlled-failure-query private prompt"
    successful_query = "successful concurrent query"
    try:
        with TestClient(create_app(service, api_token="test-token", job_manager=manager)) as client:
            try:
                submitted = [
                    client.post("/api/v1/jobs/evaluations", headers=headers, json=_job_payload(plan, query, case_id))
                    for query, case_id in ((failing_query, "case-fail"), (successful_query, "case-ok"))
                ]
                assert all(response.status_code == 202 for response in submitted)
                job_ids = [response.json()["job"]["job_id"] for response in submitted]
                assert both_entered.wait(8), "both jobs did not enter retrieval concurrently"
                failed, succeeded = [_terminal_job(client, job_id, headers) for job_id in job_ids]

                assert failed["status"] == "failed"
                assert failed["error_code"] == "job_execution_failed"
                assert failed["artifact_id"] is None
                assert failed["result_status"] is None
                assert succeeded["status"] == "succeeded"
                assert succeeded["artifact_id"] is not None
                assert succeeded["error_code"] is None
                failed_response = client.get(f"/api/v1/jobs/{job_ids[0]}", headers=headers)
                assert failing_query not in failed_response.text
                assert "private exception" not in failed_response.text and "private\\model-cache" not in failed_response.text
                assert service.list_artifacts(benchmark_id="concurrent-bench") == (service.get_artifact(succeeded["artifact_id"]),)
                loaded = service.load_evaluation_result(succeeded["artifact_id"])
                assert loaded.report.run_fingerprint == plan.run_fingerprint
                assert service.load_evaluation_result(succeeded["artifact_id"]).report.evaluation_fingerprint == service.get_artifact(succeeded["artifact_id"]).evaluation_fingerprint
                assert tuple(item.artifact_id for item in artifact_store.list_artifacts("evaluation_result")) == (succeeded["artifact_id"],)
                assert len(manager.list_jobs()) == 2
                assert factory_calls["text"] == 1
                assert registry.snapshot().components
            finally:
                rendezvous.abort()
    finally:
        rendezvous.abort()
        manager.shutdown()
