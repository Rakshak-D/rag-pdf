from __future__ import annotations

from pathlib import Path
import json
from threading import Event

from starlette.testclient import TestClient

from backend.app import create_app
from backend.embeddings.text import TextEmbeddingAdapter
from backend.services import ModelAdapterRegistry
from backend.services import ExperimentJobManager
from tests.unit.test_experiment_service import _service


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
