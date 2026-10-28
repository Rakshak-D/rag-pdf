from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from backend.app import create_app
from backend.api.schemas import RunPlanRequest
from backend.config import ResolvedRunConfig
from backend.engine.manifest import ManifestSourceChangedError, build_document_manifest
from backend.experiments.run_plan import build_run_plan
from tests.unit.test_experiment_service import _service


def _headers():
    return {"Authorization": "Bearer test-token"}


def _source(tmp_path):
    source = tmp_path / "pdf-source"
    source.mkdir()
    (source / "paper.pdf").write_bytes(b"synthetic pdf bytes")
    return source


def _app(tmp_path, source=None, *, limit=24 * 1024 * 1024):
    service, plan, _ = _service(tmp_path)
    app = create_app(service, api_token="test-token", max_request_body_bytes=limit, document_source_root=source)
    return app, service, plan


def test_manifest_route_returns_canonical_relative_entries_without_local_paths(tmp_path):
    source = _source(tmp_path)
    app, service, _ = _app(tmp_path, source)
    expected = build_document_manifest(source)
    with TestClient(app) as client:
        response = client.get("/api/v1/documents/manifest", headers=_headers())

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": expected.schema_version,
        "content_hash": expected.content_hash,
        "entry_count": 1,
        "empty": False,
        "entries": [expected.entries[0].model_dump(mode="json")],
    }
    assert str(tmp_path) not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert service.list_artifacts() == ()
    assert not (tmp_path / "storage" / "snapshots").exists()
    assert not (tmp_path / "build.json").exists()


def test_empty_manifest_is_explicit_but_cannot_prepare_a_plan(tmp_path):
    source = tmp_path / "empty-source"
    source.mkdir()
    app, _, plan = _app(tmp_path, source)
    config = plan.resolved_config().model_dump(mode="json")
    config["reset_collections_on_start"] = False
    body = {"resolved_config": config, "expected_manifest_hash": build_document_manifest(source).content_hash}
    with TestClient(app) as client:
        inspected = client.get("/api/v1/documents/manifest", headers=_headers())
        prepared = client.post("/api/v1/run-plans", headers=_headers(), json=body)
    assert inspected.status_code == 200
    assert inspected.json()["empty"] is True
    assert inspected.json()["entries"] == []
    assert prepared.status_code == 409
    assert prepared.json() == {"error": {"code": "manifest_empty"}}


def test_run_plan_preparation_returns_domain_plan_accepted_by_evaluation_contract(tmp_path):
    source = _source(tmp_path)
    app, service, old_plan = _app(tmp_path, source)
    manifest = build_document_manifest(source)
    config = old_plan.resolved_config().model_dump(mode="json")
    config["reset_collections_on_start"] = False
    body = {"resolved_config": config, "expected_manifest_hash": manifest.content_hash}

    with TestClient(app) as client:
        response = client.post("/api/v1/run-plans", headers=_headers(), json=body)

    assert response.status_code == 200
    result = response.json()
    plan = build_run_plan(ResolvedRunConfig.model_validate(config), manifest.content_hash)
    returned_plan = result["run_plan"]
    assert returned_plan == json.loads(plan.to_serializable_json())
    returned_plan_domain = RunPlanRequest.model_validate(returned_plan).to_domain()
    returned_plan_domain.verify_integrity()
    assert result["manifest"] == {
        "schema_version": manifest.schema_version,
        "content_hash": manifest.content_hash,
        "entry_count": 1,
    }
    assert str(tmp_path) not in response.text
    assert service.list_artifacts() == ()
    assert not (tmp_path / "storage" / "snapshots").exists()
    assert not (tmp_path / "build.json").exists()


def test_stale_manifest_hash_rejected_after_source_changes(tmp_path):
    source = _source(tmp_path)
    app, _, plan = _app(tmp_path, source)
    displayed = build_document_manifest(source)
    (source / "new.pdf").write_bytes(b"changed after inspection")
    config = plan.resolved_config().model_dump(mode="json")
    config["reset_collections_on_start"] = False

    with TestClient(app) as client:
        response = client.post("/api/v1/run-plans", headers=_headers(), json={
            "resolved_config": config,
            "expected_manifest_hash": displayed.content_hash,
        })

    assert response.status_code == 409
    assert response.json() == {"error": {"code": "manifest_changed"}}
    assert "run_plan" not in response.json()


@pytest.mark.parametrize("case", [
    {"resolved_config": {}, "expected_manifest_hash": "bad"},
    {"resolved_config": {}, "expected_manifest_hash": "a" * 64, "extra": True},
])
def test_run_plan_rejects_invalid_hash_and_unexpected_fields(tmp_path, case):
    source = _source(tmp_path)
    app, _, _ = _app(tmp_path, source)
    with TestClient(app) as client:
        response = client.post("/api/v1/run-plans", headers=_headers(), json=case)
    assert response.status_code == 400
    expected = "manifest_hash_invalid" if case.get("expected_manifest_hash") == "bad" else "request_invalid"
    assert response.json() == {"error": {"code": expected}}


def test_invalid_and_destructive_configurations_are_rejected_without_rewriting(tmp_path):
    source = _source(tmp_path)
    app, _, plan = _app(tmp_path, source)
    manifest_hash = build_document_manifest(source).content_hash
    config = plan.resolved_config().model_dump(mode="json")
    config["reset_collections_on_start"] = False
    invalid = dict(config, text_k=0)
    destructive = dict(config, reset_collections_on_start=True)
    with TestClient(app) as client:
        bad_config = client.post("/api/v1/run-plans", headers=_headers(), json={"resolved_config": invalid, "expected_manifest_hash": manifest_hash})
        unsafe = client.post("/api/v1/run-plans", headers=_headers(), json={"resolved_config": destructive, "expected_manifest_hash": manifest_hash})
    assert bad_config.status_code == 400
    assert bad_config.json() == {"error": {"code": "configuration_invalid"}}
    assert unsafe.status_code == 400
    assert unsafe.json() == {"error": {"code": "destructive_configuration_forbidden"}}
    assert "reset_collections_on_start" not in unsafe.text


def test_manifest_builder_change_failure_is_sanitized(tmp_path, monkeypatch):
    source = _source(tmp_path)
    app, _, _ = _app(tmp_path, source)

    def changed(_root):
        raise ManifestSourceChangedError(f"changed at {source / 'paper.pdf'}")

    monkeypatch.setattr("backend.api.experiments.build_document_manifest", changed)
    with TestClient(app) as client:
        response = client.get("/api/v1/documents/manifest", headers=_headers())
    assert response.status_code == 409
    assert response.json() == {"error": {"code": "manifest_source_changed"}}
    assert str(tmp_path) not in response.text


def test_unconfigured_source_and_authentication_fail_safely(tmp_path):
    app, _, _ = _app(tmp_path)
    with TestClient(app) as client:
        unconfigured = client.get("/api/v1/documents/manifest", headers=_headers())
        unauthorized = client.get("/api/v1/documents/manifest")
    assert unconfigured.status_code == 503
    assert unconfigured.json() == {"error": {"code": "manifest_source_unconfigured"}}
    assert unauthorized.status_code == 401
    assert unauthorized.json() == {"error": {"code": "authentication_required"}}


def test_manifest_response_limit_fails_without_returning_partial_entries(tmp_path):
    source = _source(tmp_path)
    app, _, _ = _app(tmp_path, source, limit=64)
    with TestClient(app) as client:
        response = client.get("/api/v1/documents/manifest", headers=_headers())
    assert response.status_code == 413
    assert response.json() == {"error": {"code": "manifest_response_too_large"}}
    assert "relative_path" not in response.text


def test_symlinked_pdf_input_is_not_returned_as_a_valid_manifest(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / "outside.pdf"
    target.write_bytes(b"synthetic")
    try:
        (source / "linked.pdf").symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable in this environment")
    app, _, _ = _app(tmp_path, source)
    with TestClient(app) as client:
        response = client.get("/api/v1/documents/manifest", headers=_headers())
    assert response.status_code == 409
    assert response.json() == {"error": {"code": "manifest_invalid"}}
    assert str(tmp_path) not in response.text
