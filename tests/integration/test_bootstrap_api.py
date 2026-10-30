from __future__ import annotations

import json
from pathlib import Path
import threading

import pytest
from starlette.testclient import TestClient

from backend.app.runtime import LocalRuntimeSettings, RuntimeConfigurationError, build_runtime
from backend.bootstrap_app.runtime import BootstrapConfigurationError, BootstrapSettings, build_bootstrap_runtime
from backend.engine.manifest import build_document_manifest
from backend.experiments.run_plan import build_run_plan
from backend.config import ResolvedRunConfig
from tests.unit.test_experiment_service import _service


TOKEN = "bootstrap-test-token-0123456789-abcdef"


def _fixture(tmp_path: Path, *, capacity: int = 2, limit: int = 2 * 1024 * 1024, adapter_factory=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    _service_value, base_plan, _query = _service(tmp_path)
    source = Path(base_plan.resolved_config().pdf_dir)
    (source / "bootstrap.pdf").write_bytes(b"synthetic PDF manifest fixture")
    storage = tmp_path / "bootstrap-index"
    state = tmp_path / "bootstrap-state"
    state.mkdir()
    settings = BootstrapSettings(
        api_token=TOKEN,
        source_root=source,
        storage_root=storage,
        state_dir=state,
        queue_capacity=capacity,
        max_request_body_bytes=limit,
    )
    runtime = build_bootstrap_runtime(settings, adapter_factory=adapter_factory)
    config = base_plan.resolved_config().model_dump(mode="json")
    config["reset_collections_on_start"] = False
    manifest = build_document_manifest(source)
    plan = build_run_plan(base_plan.resolved_config().model_copy(update={"reset_collections_on_start": False}), manifest.content_hash)
    return runtime, source, storage, state, config, manifest, plan


def _headers():
    return {"Authorization": f"Bearer {TOKEN}"}


def _preview_body(config):
    return {"resolved_config": config}


def test_bootstrap_starts_without_build_report_and_exposes_only_bootstrap_routes(tmp_path, monkeypatch):
    runtime, _source, storage, state, config, manifest, plan = _fixture(tmp_path)
    from backend.vectorstore.chroma_store import ChromaVectorStore
    from backend.embeddings.image import ImageEmbeddingAdapter
    from backend.embeddings.text import TextEmbeddingAdapter

    monkeypatch.setattr(ChromaVectorStore, "open", lambda *args, **kwargs: pytest.fail("preview opened Chroma"))
    monkeypatch.setattr(ChromaVectorStore, "create_new", lambda *args, **kwargs: pytest.fail("preview created Chroma"))
    monkeypatch.setattr(TextEmbeddingAdapter, "__init__", lambda *args, **kwargs: pytest.fail("preview constructed text model adapter"))
    monkeypatch.setattr(ImageEmbeddingAdapter, "__init__", lambda *args, **kwargs: pytest.fail("preview constructed image model adapter"))
    assert not storage.exists()
    assert not (state / "bootstrap-jobs.sqlite3").exists()
    with TestClient(runtime.app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok", "api_version": "v1"}
        assert client.get("/api/v1/bootstrap/status").status_code == 401
        status = client.get("/api/v1/bootstrap/status", headers=_headers())
        assert status.status_code == 200
        assert status.json() == {"mode": "bootstrap", "status": "running", "accepting_jobs": True, "worker_count": 1, "queue_capacity": 2}
        response = client.post("/api/v1/bootstrap/preview", headers=_headers(), json=_preview_body(config))
        assert response.status_code == 200, response.text
        assert response.json() == {
            "document_count": 1,
            "manifest_hash": manifest.content_hash,
            "index_fingerprint": plan.index_fingerprint,
            "run_fingerprint": plan.run_fingerprint,
            "existing_index": False,
            "fresh_build_eligible": True,
            "reason_code": "fresh_build_available",
        }
        assert client.post("/api/v1/evaluations", headers=_headers(), json={}).status_code == 404
    assert not storage.exists()
    assert str(tmp_path) not in response.text


def test_bootstrap_authentication_body_validation_and_source_binding(tmp_path):
    runtime, source, _storage, _state, config, _manifest, _plan = _fixture(tmp_path)
    with TestClient(runtime.app) as client:
        assert client.get("/api/v1/bootstrap/status").json() == {"error": {"code": "authentication_required"}}
        assert client.get("/api/v1/bootstrap/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
        for raw in (
            b'{"resolved_config":{},"resolved_config":{}}',
            b'{"resolved_config":{"llm_temperature":NaN}}',
            b"not-json",
        ):
            response = client.post("/api/v1/bootstrap/preview", headers={**_headers(), "Content-Type": "application/json"}, content=raw)
            assert response.status_code == 400
            assert response.json() == {"error": {"code": "request_invalid"}}

        incomplete = client.post("/api/v1/bootstrap/preview", headers=_headers(), json={"resolved_config": {}})
        assert incomplete.status_code == 400
        assert incomplete.json()["error"]["code"] == "configuration_invalid"
        destructive = dict(config, reset_collections_on_start=True)
        rejected = client.post("/api/v1/bootstrap/preview", headers=_headers(), json=_preview_body(destructive))
        assert rejected.status_code == 400
        assert rejected.json()["error"]["code"] == "destructive_configuration_forbidden"
        redirected = dict(config, pdf_dir=str(tmp_path / "elsewhere"))
        response = client.post("/api/v1/bootstrap/preview", headers=_headers(), json=_preview_body(redirected))
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "configuration_source_mismatch"
        assert str(source) not in response.text


def test_bootstrap_preview_empty_existing_index_and_changed_confirmation(tmp_path):
    runtime, source, storage, _state, config, manifest, plan = _fixture(tmp_path)
    with TestClient(runtime.app) as client:
        # A new empty source is represented as a bounded conflict, never a plan.
        pdf = source / "bootstrap.pdf"
        old_bytes = pdf.read_bytes()
        pdf.unlink()
        empty = client.post("/api/v1/bootstrap/preview", headers=_headers(), json=_preview_body(config))
        assert empty.status_code == 409
        assert empty.json() == {"error": {"code": "manifest_empty"}}
        pdf.write_bytes(old_bytes)

        index = storage / "indexes" / plan.index_fingerprint
        index.mkdir(parents=True)
        sentinel = index / "sentinel"
        sentinel.write_bytes(b"incomplete index evidence")
        existing = client.post("/api/v1/bootstrap/preview", headers=_headers(), json=_preview_body(config))
        assert existing.status_code == 200
        assert existing.json()["existing_index"] is True
        assert existing.json()["fresh_build_eligible"] is False
        assert existing.json()["reason_code"] == "index_already_exists"
        refused = client.post("/api/v1/bootstrap/jobs", headers=_headers(), json={
            "resolved_config": config,
            "expected_manifest_hash": manifest.content_hash,
            "expected_index_fingerprint": plan.index_fingerprint,
            "client_request_id": "request-id-00000007",
        })
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "index_already_exists"
        assert sentinel.read_bytes() == b"incomplete index evidence"

        # Use a separate storage root to reach identity mismatch before path conflict.
        other_runtime, _source, _root, _state, other_config, _manifest, _plan = _fixture(tmp_path / "other")
        with TestClient(other_runtime.app) as other_client:
            stale_manifest = other_client.post("/api/v1/bootstrap/jobs", headers=_headers(), json={
                "resolved_config": other_config,
                "expected_manifest_hash": "0" * 64,
                "expected_index_fingerprint": "0" * 64,
                "client_request_id": "request-id-00000001",
            })
            assert stale_manifest.status_code == 409
            assert stale_manifest.json()["error"]["code"] == "manifest_changed"


def test_bootstrap_job_admission_idempotency_conflict_and_inspection(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def adapter_factory():
        entered.set()
        release.wait(5)
        raise RuntimeError("private test failure")

    runtime, _source, _storage, _state, config, manifest, plan = _fixture(tmp_path, adapter_factory=adapter_factory)
    body = {
        "resolved_config": config,
        "expected_manifest_hash": manifest.content_hash,
        "expected_index_fingerprint": plan.index_fingerprint,
        "client_request_id": "request-id-00000001",
    }
    try:
        with TestClient(runtime.app) as client:
            accepted = client.post("/api/v1/bootstrap/jobs", headers=_headers(), json=body)
            assert accepted.status_code == 202, accepted.text
            job_id = accepted.json()["job"]["job_id"]
            assert accepted.json()["job"]["client_request_id"] == body["client_request_id"]
            assert accepted.json()["job"]["status"] in {"queued", "running"}
            assert entered.wait(5), "worker did not reach the trusted adapter seam"

            replay = client.post("/api/v1/bootstrap/jobs", headers=_headers(), json=body)
            assert replay.status_code == 202
            assert replay.json()["job"]["job_id"] == job_id

            changed = json.loads(json.dumps(body))
            changed["resolved_config"]["text_k"] += 1
            changed_config = ResolvedRunConfig.model_validate(changed["resolved_config"])
            changed_plan = build_run_plan(changed_config, manifest.content_hash)
            changed["expected_index_fingerprint"] = changed_plan.index_fingerprint
            conflict = client.post("/api/v1/bootstrap/jobs", headers=_headers(), json=changed)
            assert conflict.status_code == 409
            assert conflict.json()["error"]["code"] == "request_id_conflict"

            listed = client.get("/api/v1/bootstrap/jobs?limit=10&offset=0", headers=_headers())
            assert listed.status_code == 200
            assert len(listed.json()["jobs"]) == 1
            lookup = client.get(f"/api/v1/bootstrap/jobs/{job_id}", headers=_headers())
            assert lookup.status_code == 200
            assert lookup.json()["job"]["job_id"] == job_id
            assert "private test failure" not in lookup.text
            unknown = client.get("/api/v1/bootstrap/jobs/00000000-0000-0000-0000-000000000000", headers=_headers())
            assert unknown.status_code == 404
            assert unknown.json() == {"error": {"code": "job_not_found"}}
            bad_query = client.get("/api/v1/bootstrap/jobs?limit=101", headers=_headers())
            assert bad_query.status_code == 400
    finally:
        release.set()


def test_bootstrap_submission_rejects_changed_manifest_and_fingerprint(tmp_path):
    runtime, source, _storage, _state, config, manifest, plan = _fixture(tmp_path)
    request = {
        "resolved_config": config,
        "expected_manifest_hash": manifest.content_hash,
        "expected_index_fingerprint": plan.index_fingerprint,
        "client_request_id": "request-id-00000002",
    }
    with TestClient(runtime.app) as client:
        stale_fingerprint = dict(request, expected_index_fingerprint="0" * 64)
        response = client.post("/api/v1/bootstrap/jobs", headers=_headers(), json=stale_fingerprint)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "index_fingerprint_changed"

        (source / "second.pdf").write_bytes(b"source changed after preview")
        stale_manifest = client.post("/api/v1/bootstrap/jobs", headers=_headers(), json=request)
        assert stale_manifest.status_code == 409
        assert stale_manifest.json()["error"]["code"] == "manifest_changed"


def test_bootstrap_api_queue_capacity_and_active_fingerprint_are_enforced(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def adapter_factory():
        entered.set()
        release.wait(10)
        raise RuntimeError("controlled smoke failure")

    runtime, _source, _storage, state, config, manifest, plan = _fixture(
        tmp_path, capacity=1, adapter_factory=adapter_factory,
    )
    original = {
        "resolved_config": config,
        "expected_manifest_hash": manifest.content_hash,
        "expected_index_fingerprint": plan.index_fingerprint,
        "client_request_id": "request-id-00000003",
    }
    config_two = dict(config, text_embed_model=config["text_embed_model"] + "-variant")
    plan_two = build_run_plan(ResolvedRunConfig.model_validate(config_two), manifest.content_hash)
    second = {
        "resolved_config": config_two,
        "expected_manifest_hash": manifest.content_hash,
        "expected_index_fingerprint": plan_two.index_fingerprint,
        "client_request_id": "request-id-00000004",
    }
    config_three = dict(config, image_embed_model=config["image_embed_model"] + "-variant")
    plan_three = build_run_plan(ResolvedRunConfig.model_validate(config_three), manifest.content_hash)
    third = {
        "resolved_config": config_three,
        "expected_manifest_hash": manifest.content_hash,
        "expected_index_fingerprint": plan_three.index_fingerprint,
        "client_request_id": "request-id-00000005",
    }
    try:
        with TestClient(runtime.app) as client:
            first_response = client.post("/api/v1/bootstrap/jobs", headers=_headers(), json=original)
            assert first_response.status_code == 202
            first_id = first_response.json()["job"]["job_id"]
            assert entered.wait(5), "first worker did not reach the controlled adapter boundary"

            same_fingerprint = dict(original, client_request_id="request-id-00000006")
            conflict = client.post("/api/v1/bootstrap/jobs", headers=_headers(), json=same_fingerprint)
            assert conflict.status_code == 409
            assert conflict.json()["error"]["code"] == "fingerprint_already_active"

            second_response = client.post("/api/v1/bootstrap/jobs", headers=_headers(), json=second)
            assert second_response.status_code == 202
            full = client.post("/api/v1/bootstrap/jobs", headers=_headers(), json=third)
            assert full.status_code == 429
            assert full.json()["error"]["code"] == "queue_capacity_exhausted"

            replay = client.post("/api/v1/bootstrap/jobs", headers=_headers(), json=original)
            assert replay.status_code == 202
            assert replay.json()["job"]["job_id"] == first_id
            with runtime.job_manager._connect() as db:
                rows = db.execute("SELECT client_request_id,status FROM bootstrap_jobs ORDER BY client_request_id").fetchall()
            assert len(rows) == 2
            assert {row["client_request_id"] for row in rows} == {original["client_request_id"], second["client_request_id"]}
            assert runtime.job_manager._queue.qsize() == 1

            # Shutdown closes admission and interrupts queued work before waiting
            # for the controlled active worker to return.
            assert runtime.job_manager.shutdown(timeout=0.01) is False
            rejected = client.post("/api/v1/bootstrap/jobs", headers=_headers(), json=third)
            assert rejected.status_code == 503
            with runtime.job_manager._connect() as db:
                queued = db.execute("SELECT status,error_code FROM bootstrap_jobs WHERE client_request_id=?", (second["client_request_id"],)).fetchone()
            assert tuple(queued) == ("interrupted", "server_shutdown")
            release.set()
            worker = runtime.job_manager._worker
            assert worker is not None
            worker.join(15)
            assert not worker.is_alive()
            assert runtime.job_manager.shutdown(timeout=15) is True
    finally:
        release.set()
        worker = runtime.job_manager._worker
        if worker is not None:
            worker.join(15)
        if runtime.job_manager._owner_token is not None and (worker is None or not worker.is_alive()):
            runtime.job_manager.shutdown(timeout=15)
    assert not (state / ".bootstrap-instance-owner").exists()


def test_bootstrap_request_body_limit_and_path_settings(tmp_path):
    runtime, _source, _storage, _state, config, *_rest = _fixture(tmp_path, limit=128)
    with TestClient(runtime.app) as client:
        response = client.post("/api/v1/bootstrap/preview", headers=_headers(), json=_preview_body(config))
        assert response.status_code == 413
        assert response.json() == {"error": {"code": "request_body_too_large"}}

    source = tmp_path / "source-alone"
    source.mkdir()
    state = tmp_path / "state-alone"
    state.mkdir()
    with pytest.raises(BootstrapConfigurationError, match="bootstrap_token_invalid"):
        BootstrapSettings(api_token="short", source_root=source, storage_root=tmp_path / "storage-alone", state_dir=state)
    with pytest.raises(BootstrapConfigurationError, match="bootstrap_host_must_be_loopback"):
        BootstrapSettings(api_token=TOKEN, source_root=source, storage_root=tmp_path / "storage-alone", state_dir=state, host="0.0.0.0")
    nested_state = source / "state"
    nested_state.mkdir()
    with pytest.raises(BootstrapConfigurationError, match="bootstrap_paths_overlap"):
        BootstrapSettings(api_token=TOKEN, source_root=source, storage_root=tmp_path / "storage-alone", state_dir=nested_state)


def test_bootstrap_settings_environment_requires_all_trusted_roots(tmp_path):
    with pytest.raises(BootstrapConfigurationError, match="bootstrap_required_setting_missing"):
        BootstrapSettings.from_environment({})
    source = tmp_path / "source"
    state = tmp_path / "state"
    source.mkdir()
    state.mkdir()
    env = {
        "RAG_WORKBENCH_BOOTSTRAP_API_TOKEN": TOKEN,
        "RAG_WORKBENCH_BOOTSTRAP_PDF_SOURCE_DIR": str(source),
        "RAG_WORKBENCH_BOOTSTRAP_INDEX_STORAGE_ROOT": str(tmp_path / "storage"),
        "RAG_WORKBENCH_BOOTSTRAP_STATE_DIR": str(state),
        "RAG_WORKBENCH_BOOTSTRAP_API_PORT": "70000",
    }
    with pytest.raises(BootstrapConfigurationError, match="bootstrap_port_invalid"):
        BootstrapSettings.from_environment(env)


def test_bootstrap_settings_reject_symlinked_trusted_roots(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    target = tmp_path / "storage-target"
    target.mkdir()
    link = tmp_path / "storage-link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink unavailable: {type(exc).__name__}")
    with pytest.raises(BootstrapConfigurationError, match="bootstrap_path_symlink"):
        BootstrapSettings(api_token=TOKEN, source_root=source, storage_root=link, state_dir=state)


def test_ordinary_runtime_still_requires_build_report(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    index = tmp_path / "index"
    index.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    artifacts = tmp_path / "artifacts"
    settings = LocalRuntimeSettings(
        api_token="ordinary-test-token",
        data_dir=data,
        artifact_store_dir=artifacts,
        catalog_db_path=state / "catalog.sqlite3",
        jobs_db_path=state / "jobs.sqlite3",
        index_storage_root=index,
        build_report_path=index / "build-artifacts" / "missing" / "build-report.json",
    )
    with pytest.raises(RuntimeConfigurationError, match="build_report_missing"):
        build_runtime(settings)
    assert not artifacts.exists()
