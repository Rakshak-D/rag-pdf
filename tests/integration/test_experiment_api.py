from __future__ import annotations

import json
from pathlib import Path

from starlette.testclient import TestClient

from backend.app import create_app
from backend.experiments import BenchmarkCase, BenchmarkSuite
from tests.unit.test_experiment_service import _service


def _headers():
    return {"Authorization": "Bearer test-token"}


def test_health_authentication_and_security_headers(tmp_path: Path):
    service, _, _ = _service(tmp_path)
    client = TestClient(create_app(service, api_token="test-token"))

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "api_version": "v1"}
    assert client.get("/api/v1/artifacts").json() == {"error": {"code": "authentication_required"}}
    assert client.get("/api/v1/artifacts", headers={"Authorization": "Bearer wrong"}).status_code == 401
    response = client.get("/api/v1/artifacts", headers=_headers())
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_evaluation_submission_reads_and_metrics_use_service(tmp_path: Path):
    service, plan, query = _service(tmp_path)
    client = TestClient(create_app(service, api_token="test-token"))
    suite = {"benchmark_id": "bench", "benchmark_version": "1", "cases": [{"case_id": "case-1", "query": query, "modalities": ["text"], "text_k": 5, "required_pages": []}]}
    response = client.post("/api/v1/evaluations", headers=_headers(), json={"run_plan": json.loads(plan.to_serializable_json()), "benchmark_suite": suite})
    assert response.status_code == 201
    artifact_id = response.json()["artifact"]["artifact_id"]
    assert "What is the evidence?" not in response.text
    assert client.get(f"/api/v1/artifacts/{artifact_id}", headers=_headers()).status_code == 200
    assert client.get(f"/api/v1/artifacts/{artifact_id}/metrics", headers=_headers()).status_code == 200
    result = client.get(f"/api/v1/artifacts/{artifact_id}/result", headers=_headers())
    assert result.status_code == 200
    assert result.headers["content-type"].startswith("application/json")
    assert client.get("/api/v1/artifacts", headers=_headers(), params={"limit": "bad"}).json() == {"error": {"code": "query_invalid"}}


def test_ablation_submission_and_plan_bound_result_load(tmp_path: Path):
    service, plan, query = _service(tmp_path)
    client = TestClient(create_app(service, api_token="test-token"))
    suite = {"benchmark_id": "bench", "benchmark_version": "1", "cases": [{"case_id": "case-1", "query": query, "modalities": ["text"], "text_k": 5, "required_pages": []}]}
    base_config = plan.resolved_config().model_dump(mode="json")
    payload = {"base_config": base_config, "document_manifest_hash": plan.document_manifest_hash, "benchmark_suite": suite}
    response = client.post("/api/v1/ablations", headers=_headers(), json=payload)
    assert response.status_code == 201
    artifact_id = response.json()["artifact"]["artifact_id"]
    assert len(response.json()["result"]["variants"]) == 6
    assert client.get(f"/api/v1/artifacts/{artifact_id}/comparisons", headers=_headers()).status_code == 200
    loaded = client.get(f"/api/v1/artifacts/{artifact_id}/result", headers=_headers(), params={"base_config": json.dumps(base_config), "document_manifest_hash": plan.document_manifest_hash})
    assert loaded.status_code == 200
    assert loaded.json()["artifact_type"] == "ablation_result"
