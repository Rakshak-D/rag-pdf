from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from backend.app import create_app
from backend.experiments import BenchmarkCase, BenchmarkSuite, build_ablation_plan
from backend.experiments.serialization import serialize_evaluation_result
from backend.services import ExperimentJobManager
from tests.unit.test_experiment_service import _service


def _headers():
    return {"Authorization": "Bearer test-token"}


def test_health_authentication_and_security_headers(tmp_path: Path):
    service, _, _ = _service(tmp_path)
    client = TestClient(create_app(service, api_token="test-token"))

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "api_version": "v1"}
    assert health.headers["cache-control"] == "no-store"
    assert health.headers["x-content-type-options"] == "nosniff"
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


def _evaluation_payload(plan, query):
    return {
        "run_plan": json.loads(plan.to_serializable_json()),
        "benchmark_suite": {
            "benchmark_id": "bench",
            "benchmark_version": "1",
            "cases": [{"case_id": "case-1", "query": query, "modalities": ["text"], "text_k": 5, "required_pages": []}],
        },
    }


@pytest.mark.parametrize(
    ("content_type", "body", "expected_status", "expected_code"),
    [
        (None, b"{}", 415, "unsupported_content_type"),
        ("text/plain", b"{}", 415, "unsupported_content_type"),
        ("application/json", b"", 400, "request_invalid"),
        ("application/json", b"\xff", 400, "request_invalid"),
        ("application/json", b"{bad json", 400, "request_invalid"),
        ("application/json", b"[]", 400, "request_invalid"),
        ("application/json", b'{"x":1,"x":2}', 400, "request_invalid"),
        ("application/json", b'{"x":NaN}', 400, "request_invalid"),
        ("application/json", b'{"unexpected":"private input"}', 400, "request_invalid"),
    ],
)
def test_http_input_errors_are_bounded_and_stable(tmp_path, content_type, body, expected_status, expected_code):
    service, _, _ = _service(tmp_path)
    headers = _headers()
    if content_type is not None:
        headers["Content-Type"] = content_type
    with TestClient(create_app(service, api_token="test-token")) as client:
        response = client.post("/api/v1/evaluations", content=body, headers=headers)
    assert response.status_code == expected_status
    assert response.json() == {"error": {"code": expected_code}}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    decoded_body = body.decode("utf-8", errors="ignore")
    if decoded_body:
        assert decoded_body not in response.text


def test_all_api_routes_authenticate_before_parsing_or_execution(tmp_path: Path):
    service, _, _ = _service(tmp_path)
    calls = []
    service.run_evaluation = lambda *_args, **_kwargs: calls.append("evaluation")  # type: ignore[method-assign]
    service.run_ablation = lambda *_args, **_kwargs: calls.append("ablation")  # type: ignore[method-assign]
    paths = [
        ("POST", "/api/v1/evaluations"),
        ("POST", "/api/v1/ablations"),
        ("POST", "/api/v1/jobs/evaluations"),
        ("POST", "/api/v1/jobs/ablations"),
        ("POST", "/api/v1/jobs/123e4567-e89b-12d3-a456-426614174000/cancel"),
        ("GET", "/api/v1/runtime/models"),
        ("GET", "/api/v1/jobs"),
        ("GET", "/api/v1/jobs/123e4567-e89b-12d3-a456-426614174000"),
        ("GET", "/api/v1/artifacts"),
        ("GET", f"/api/v1/artifacts/{'a' * 64}"),
        ("GET", f"/api/v1/artifacts/{'a' * 64}/metrics"),
        ("GET", f"/api/v1/artifacts/{'a' * 64}/comparisons"),
        ("GET", f"/api/v1/artifacts/{'a' * 64}/result"),
    ]
    with TestClient(create_app(service, api_token="expected-secret")) as client:
        for method, path in paths:
            for credentials in ({}, {"Authorization": "Bearer"}, {"Authorization": "bearer test-token"}, {"Authorization": "Bearer wrong-secret"}):
                response = client.request(method, path, content=b"not json", headers=credentials)
                assert response.status_code == 401
                assert response.json() == {"error": {"code": "authentication_required"}}
                assert "expected-secret" not in response.text and "wrong-secret" not in response.text
                assert response.headers["cache-control"] == "no-store"
                assert response.headers["x-content-type-options"] == "nosniff"
    assert calls == []


def test_internal_errors_are_bounded_and_keep_security_headers(tmp_path: Path):
    service, plan, query = _service(tmp_path)
    service.run_evaluation = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("private-token C:\\private\\corpus"))  # type: ignore[method-assign]
    body = json.dumps(_evaluation_payload(plan, query)).encode("utf-8")
    with TestClient(create_app(service, api_token="test-token")) as client:
        response = client.post("/api/v1/evaluations", content=body, headers={**_headers(), "Content-Type": "application/json"})
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "internal_error"}}
    assert "private-token" not in response.text and "C:\\private\\corpus" not in response.text
    assert query not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_body_limit_rejects_evaluation_ablation_and_job_before_execution(tmp_path: Path):
    service, _, _ = _service(tmp_path)
    calls = []
    service.run_evaluation = lambda *_args, **_kwargs: calls.append("evaluation")  # type: ignore[method-assign]
    service.run_ablation = lambda *_args, **_kwargs: calls.append("ablation")  # type: ignore[method-assign]
    manager = ExperimentJobManager(service, tmp_path / "jobs.sqlite")
    manager_calls = []
    manager.submit_evaluation = lambda *_args, **_kwargs: manager_calls.append("evaluation")  # type: ignore[method-assign]
    manager.submit_ablation = lambda *_args, **_kwargs: manager_calls.append("ablation")  # type: ignore[method-assign]
    headers = {**_headers(), "Content-Type": "application/json"}
    oversized = b'{"x":"' + b"x" * 64 + b'"}'

    with TestClient(create_app(service, api_token="test-token", max_request_body_bytes=32, job_manager=manager)) as client:
        for path in ("/api/v1/evaluations", "/api/v1/ablations", "/api/v1/jobs/evaluations", "/api/v1/jobs/ablations"):
            response = client.post(path, content=oversized, headers=headers)
            assert response.status_code == 413
            assert response.json() == {"error": {"code": "request_body_too_large"}}
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["x-content-type-options"] == "nosniff"
    assert calls == []
    assert manager_calls == []
    assert manager.list_jobs() == ()
    assert service._catalog.list_artifacts() == ()


def test_body_limit_is_enforced_while_consuming_asgi_stream(tmp_path: Path):
    service, _, _ = _service(tmp_path)
    app = create_app(service, api_token="test-token", max_request_body_bytes=8)
    receive_calls = 0
    sent = []

    async def receive():
        nonlocal receive_calls
        receive_calls += 1
        if receive_calls == 1:
            return {"type": "http.request", "body": b"012345678", "more_body": True}
        return {"type": "http.request", "body": b"remainder-that-must-not-be-read", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": "/api/v1/evaluations",
        "raw_path": b"/api/v1/evaluations", "query_string": b"", "root_path": "",
        "headers": [(b"authorization", b"Bearer test-token"), (b"content-type", b"application/json")],
        "client": ("test", 123), "server": ("test", 80),
    }
    asyncio.run(app(scope, receive, send))
    response_start = next(message for message in sent if message["type"] == "http.response.start")
    response_body = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
    assert response_start["status"] == 413
    assert json.loads(response_body) == {"error": {"code": "request_body_too_large"}}
    assert receive_calls == 1


def test_exact_request_limit_is_decided_by_content_validity(tmp_path: Path):
    service, plan, query = _service(tmp_path)
    body = json.dumps(_evaluation_payload(plan, query), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    with TestClient(create_app(service, api_token="test-token", max_request_body_bytes=len(body))) as client:
        response = client.post("/api/v1/evaluations", content=body, headers={**_headers(), "Content-Type": "application/json"})
    assert response.status_code == 201


def test_result_response_limit_rejects_without_truncating_or_mutating_artifact(tmp_path: Path):
    service, plan, query = _service(tmp_path)
    suite = BenchmarkSuite("bench", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5),))
    persisted = service.run_evaluation(plan, suite)
    result_bytes = serialize_evaluation_result(persisted.result).encode("utf-8")
    assert hashlib.sha256(result_bytes).hexdigest() == persisted.record.artifact_id
    original_catalog_record = service.get_artifact(persisted.record.artifact_id)

    with TestClient(create_app(service, api_token="test-token", max_request_body_bytes=len(result_bytes) - 1)) as client:
        oversized = client.get(f"/api/v1/artifacts/{persisted.record.artifact_id}/result", headers=_headers())
    assert oversized.status_code == 413
    assert oversized.json() == {"error": {"code": "response_body_too_large"}}
    assert oversized.headers["cache-control"] == "no-store"
    assert oversized.headers["x-content-type-options"] == "nosniff"
    assert "artifact_type" not in oversized.text
    assert service.get_artifact(persisted.record.artifact_id) == original_catalog_record
    assert serialize_evaluation_result(service.load_evaluation_result(persisted.record.artifact_id)).encode("utf-8") == result_bytes

    with TestClient(create_app(service, api_token="test-token", max_request_body_bytes=len(result_bytes))) as client:
        exact = client.get(f"/api/v1/artifacts/{persisted.record.artifact_id}/result", headers=_headers())
    assert exact.status_code == 200
    assert exact.content == result_bytes


def test_ablation_response_still_requires_plan_before_size_check(tmp_path: Path):
    service, base_plan, query = _service(tmp_path)
    plan = build_ablation_plan(base_plan.resolved_config(), base_plan.document_manifest_hash)
    suite = BenchmarkSuite("bench", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5),))
    persisted = service.run_ablation(plan, suite)
    params = {"base_config": json.dumps(base_plan.resolved_config().model_dump(mode="json")), "document_manifest_hash": plan.document_manifest_hash}
    with TestClient(create_app(service, api_token="test-token", max_request_body_bytes=64)) as client:
        missing = client.get(f"/api/v1/artifacts/{persisted.record.artifact_id}/result", headers=_headers())
        oversized = client.get(f"/api/v1/artifacts/{persisted.record.artifact_id}/result", headers=_headers(), params=params)
    assert missing.status_code == 400
    assert missing.json() == {"error": {"code": "ablation_plan_required"}}
    assert oversized.status_code == 413
    assert oversized.json() == {"error": {"code": "response_body_too_large"}}
