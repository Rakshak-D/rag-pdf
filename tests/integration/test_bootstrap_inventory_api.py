from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading

import pytest
from starlette.testclient import TestClient

from backend.config.writer_lease import WriterLease
from backend.embeddings.image import ImageEmbeddingAdapter
from backend.embeddings.text import TextEmbeddingAdapter
from backend.services.bootstrap_jobs import BootstrapJobManager
from backend.vectorstore.chroma_store import ChromaVectorStore
from tests.integration.test_bootstrap_api import TOKEN, _fixture, _headers
from tests.integration.test_retrieval import write_report


def _inventory_request(config, manifest_hash: str, *, limit: int = 50, offset: int = 0) -> dict:
    return {
        "resolved_config": config,
        "expected_manifest_hash": manifest_hash,
        "limit": limit,
        "offset": offset,
    }


def _write(storage: Path, plan, build_id: str, **changes) -> Path:
    status = changes.pop("status", "completed")
    partial = changes.pop("partial_persistence", False)
    path = write_report(storage, plan, build_id=build_id, status=status, partial=partial)
    if changes:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.update(changes)
        path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_inventory_auth_empty_result_and_no_store_headers(tmp_path):
    runtime, _source, storage, _state, config, manifest, _plan = _fixture(tmp_path)
    assert not storage.exists()
    with TestClient(runtime.app) as client:
        unauthorized = client.post(
            "/api/v1/bootstrap/index-inventory",
            json=_inventory_request(config, manifest.content_hash),
        )
        assert unauthorized.status_code == 401
        assert unauthorized.json() == {"error": {"code": "authentication_required"}}
        assert unauthorized.headers["cache-control"] == "no-store"
        wrong_token = client.post(
            "/api/v1/bootstrap/index-inventory",
            headers={"Authorization": "Bearer wrong-token"},
            json=_inventory_request(config, manifest.content_hash),
        )
        assert wrong_token.status_code == 401

        response = client.post(
            "/api/v1/bootstrap/index-inventory",
            headers=_headers(),
            json=_inventory_request(config, manifest.content_hash),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["candidates"] == []
        assert body["total_candidates"] == 0
        assert body["complete"] is True
        assert body["reason_code"] == "no_build_reports"
        assert body["check_level"] == "report" and body["store_checked"] is False
        assert body["document_manifest_hash"] == manifest.content_hash
        assert body["expected_index_fingerprint"]
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
    assert not storage.exists()


def test_inventory_returns_validated_report_candidate_without_private_paths(tmp_path):
    runtime, _source, storage, _state, config, manifest, plan = _fixture(tmp_path)
    build_id = "a" * 32
    report = _write(storage, plan, build_id)
    original = report.read_bytes()
    with TestClient(runtime.app) as client:
        response = client.post(
            "/api/v1/bootstrap/index-inventory",
            headers=_headers(),
            json=_inventory_request(config, manifest.content_hash),
        )
    assert response.status_code == 200, response.text
    candidate, = response.json()["candidates"]
    assert candidate["build_id"] == build_id
    assert candidate["report_reference"] == f"build-artifacts/{build_id}/build-report.json"
    assert candidate["compatibility"] == "report_match"
    assert candidate["reason_code"] == "report_matches"
    assert candidate["reported_text_records"] == 1
    assert candidate["reported_image_records"] == 1
    assert candidate["check_level"] == "report" and candidate["store_checked"] is False
    assert str(tmp_path) not in response.text
    assert report.read_bytes() == original
    assert not (storage / "indexes").exists()


@pytest.mark.parametrize(
    ("raw", "expected_status", "expected_code"),
    [
        (b"not-json", 400, "request_invalid"),
        (b'{"resolved_config":{},"resolved_config":{},"expected_manifest_hash":"' + b"0" * 64 + b'"}', 400, "request_invalid"),
        (b'{"resolved_config":{"x":NaN},"expected_manifest_hash":"' + b"0" * 64 + b'"}', 400, "request_invalid"),
    ],
)
def test_inventory_rejects_malformed_duplicate_and_nonfinite_json(tmp_path, raw, expected_status, expected_code):
    runtime, *_ = _fixture(tmp_path)
    with TestClient(runtime.app) as client:
        response = client.post(
            "/api/v1/bootstrap/index-inventory",
            headers={**_headers(), "Content-Type": "application/json"},
            content=raw,
        )
    assert response.status_code == expected_status
    assert response.json() == {"error": {"code": expected_code}}


def test_inventory_rejects_content_type_unknown_fields_pagination_and_config_safety(tmp_path):
    runtime, source, _storage, _state, config, manifest, _plan = _fixture(tmp_path)
    valid = _inventory_request(config, manifest.content_hash)
    with TestClient(runtime.app) as client:
        wrong_type = client.post(
            "/api/v1/bootstrap/index-inventory",
            headers={**_headers(), "Content-Type": "text/plain"},
            content=json.dumps(valid),
        )
        assert wrong_type.status_code == 415
        for body in (
            {**valid, "storage_root": str(tmp_path / "redirected")},
            {**valid, "limit": True},
            {**valid, "limit": 101},
            {**valid, "offset": 513},
            {"resolved_config": {"pdf_dir": config["pdf_dir"]}, "expected_manifest_hash": manifest.content_hash},
            {**valid, "resolved_config": {**config, "reset_collections_on_start": True}},
            {**valid, "resolved_config": {**config, "pdf_dir": str(tmp_path / "other-source")}},
        ):
            response = client.post(
                "/api/v1/bootstrap/index-inventory", headers=_headers(), json=body,
            )
            assert response.status_code == 400, response.text
            assert str(source) not in response.text


def test_inventory_rejects_changed_manifest_and_nonempty_source_change(tmp_path):
    runtime, source, _storage, _state, config, manifest, _plan = _fixture(tmp_path)
    with TestClient(runtime.app) as client:
        stale = client.post(
            "/api/v1/bootstrap/index-inventory", headers=_headers(),
            json=_inventory_request(config, "0" * 64),
        )
        assert stale.status_code == 409
        assert stale.json() == {"error": {"code": "manifest_changed"}}

        (source / "second.pdf").write_bytes(b"source changed after manifest inspection")
        changed = client.post(
            "/api/v1/bootstrap/index-inventory", headers=_headers(),
            json=_inventory_request(config, manifest.content_hash),
        )
        assert changed.status_code == 409
        assert changed.json() == {"error": {"code": "manifest_changed"}}


def test_inventory_rejects_empty_source_corpus(tmp_path):
    runtime, source, _storage, _state, config, manifest, _plan = _fixture(tmp_path)
    for pdf in source.glob("*.pdf"):
        pdf.unlink()
    with TestClient(runtime.app) as client:
        response = client.post(
            "/api/v1/bootstrap/index-inventory", headers=_headers(),
            json=_inventory_request(config, manifest.content_hash),
        )
    assert response.status_code == 409
    assert response.json() == {"error": {"code": "manifest_empty"}}


def test_inventory_classifies_multiple_candidates_and_paginates_deterministically(tmp_path):
    runtime, _source, storage, _state, config, manifest, plan = _fixture(tmp_path)
    good_id, mismatch_id, failed_id, partial_id, malformed_id, missing_id = (
        "1" * 32, "2" * 32, "3" * 32, "4" * 32, "5" * 32, "6" * 32,
    )
    _write(storage, plan, good_id)
    _write(storage, plan, mismatch_id, index_fingerprint="b" * 64)
    _write(storage, plan, failed_id, status="failed")
    _write(storage, plan, partial_id, partial_persistence=True)
    malformed = storage / "build-artifacts" / malformed_id
    malformed.mkdir(parents=True)
    (malformed / "build-report.json").write_text("{bad", encoding="utf-8")
    (storage / "build-artifacts" / missing_id).mkdir()

    with TestClient(runtime.app) as client:
        pages = []
        for offset in (0, 2, 4):
            response = client.post(
                "/api/v1/bootstrap/index-inventory", headers=_headers(),
                json=_inventory_request(config, manifest.content_hash, limit=2, offset=offset),
            )
            assert response.status_code == 200, response.text
            pages.append(response.json())
        upper_boundary = client.post(
            "/api/v1/bootstrap/index-inventory", headers=_headers(),
            json=_inventory_request(config, manifest.content_hash, limit=2, offset=512),
        )
        assert upper_boundary.status_code == 200
        assert upper_boundary.json()["candidates"] == []
        assert upper_boundary.json()["total_candidates"] == 6

    combined = [item for page in pages for item in page["candidates"]]
    assert all(page["complete"] and page["total_candidates"] == 6 for page in pages)
    assert [item["build_id"] for item in combined] == sorted((good_id, mismatch_id, failed_id, partial_id, malformed_id, missing_id))
    by_id = {item["build_id"]: item for item in combined}
    assert by_id[good_id]["compatibility"] == "report_match"
    assert by_id[mismatch_id]["compatibility"] == "mismatch"
    assert by_id[failed_id]["compatibility"] == "failed"
    assert by_id[partial_id]["compatibility"] == "incomplete"
    assert by_id[malformed_id]["compatibility"] == "invalid"
    assert by_id[missing_id]["compatibility"] == "unavailable"
    assert by_id[mismatch_id]["reported_text_records"] is None
    assert all(item["store_checked"] is False and item["check_level"] == "report" for item in combined)


def test_inventory_incomplete_marker_and_scan_capacity_are_explicit(tmp_path):
    runtime, _source, storage, _state, config, manifest, plan = _fixture(tmp_path)
    build_id = "a" * 32
    _write(storage, plan, build_id)
    index_dir = storage / "indexes" / plan.index_fingerprint
    index_dir.mkdir(parents=True)
    (index_dir / ".fresh-build-incomplete").write_text(plan.index_fingerprint, encoding="ascii")
    service = runtime.app.state.index_inventory_service
    service._max_entries = 1
    artifacts = storage / "build-artifacts"
    (artifacts / ("b" * 32)).mkdir()

    with TestClient(runtime.app) as client:
        response = client.post(
            "/api/v1/bootstrap/index-inventory", headers=_headers(),
            json=_inventory_request(config, manifest.content_hash),
        )
    assert response.status_code == 200
    body = response.json()
    assert body["complete"] is False
    assert body["total_candidates"] is None
    assert body["reason_code"] == "inventory_limit_exceeded"
    assert body["candidates"] == []

    # A separate inventory with a single report exposes the incomplete marker.
    runtime2, _source, storage2, _state, config2, manifest2, plan2 = _fixture(tmp_path / "marker")
    _write(storage2, plan2, "c" * 32)
    marker_dir = storage2 / "indexes" / plan2.index_fingerprint
    marker_dir.mkdir(parents=True)
    (marker_dir / ".fresh-build-incomplete").write_text(plan2.index_fingerprint, encoding="ascii")
    with TestClient(runtime2.app) as client:
        result = client.post(
            "/api/v1/bootstrap/index-inventory", headers=_headers(),
            json=_inventory_request(config2, manifest2.content_hash),
        )
    assert result.status_code == 200
    row, = result.json()["candidates"]
    assert row["compatibility"] == "incomplete"
    assert row["reason_code"] == "fresh_build_incomplete"


def test_inventory_response_size_is_bounded(tmp_path):
    runtime, _source, storage, _state, config, manifest, plan = _fixture(tmp_path)
    _write(storage, plan, "a" * 32)
    runtime.app.state.max_response_body_bytes = 1
    with TestClient(runtime.app) as client:
        response = client.post(
            "/api/v1/bootstrap/index-inventory", headers=_headers(),
            json=_inventory_request(config, manifest.content_hash),
        )
    assert response.status_code == 413
    assert response.json() == {"error": {"code": "response_body_too_large"}}


def test_inventory_request_body_limit_is_enforced(tmp_path):
    runtime, _source, _storage, _state, config, manifest, _plan = _fixture(tmp_path, limit=128)
    body = _inventory_request(config, manifest.content_hash)
    body["padding"] = "x" * 256
    with TestClient(runtime.app) as client:
        response = client.post(
            "/api/v1/bootstrap/index-inventory", headers=_headers(), json=body,
        )
    assert response.status_code == 413
    assert response.json() == {"error": {"code": "request_body_too_large"}}


def test_inventory_does_not_construct_index_models_or_submit_jobs(tmp_path, monkeypatch):
    runtime, _source, storage, _state, config, manifest, plan = _fixture(tmp_path)
    report = _write(storage, plan, "a" * 32)
    before = {path.relative_to(storage).as_posix(): path.read_bytes() for path in storage.rglob("*") if path.is_file()}

    def forbidden(*_args, **_kwargs):
        pytest.fail("read-only inventory crossed an execution boundary")

    monkeypatch.setattr(ChromaVectorStore, "open", forbidden)
    monkeypatch.setattr(ChromaVectorStore, "open_existing", forbidden)
    monkeypatch.setattr(ChromaVectorStore, "create_new", forbidden)
    monkeypatch.setattr(WriterLease, "acquire", forbidden)
    monkeypatch.setattr(TextEmbeddingAdapter, "__init__", forbidden)
    monkeypatch.setattr(ImageEmbeddingAdapter, "__init__", forbidden)
    monkeypatch.setattr("backend.engine.index_builder.build_index", forbidden)
    monkeypatch.setattr("backend.services.bootstrap_jobs.build_index", forbidden)
    monkeypatch.setattr(BootstrapJobManager, "submit", forbidden)

    with TestClient(runtime.app) as client:
        runtime.job_manager._accepting = False
        response = client.post(
            "/api/v1/bootstrap/index-inventory", headers=_headers(),
            json=_inventory_request(config, manifest.content_hash),
        )
    assert response.status_code == 200
    after = {path.relative_to(storage).as_posix(): path.read_bytes() for path in storage.rglob("*") if path.is_file()}
    assert before == after
    assert report.read_bytes() == before[f"build-artifacts/{'a' * 32}/build-report.json"]
    assert not (storage / "indexes").exists()


def test_inventory_scan_concurrency_is_bounded(tmp_path, monkeypatch):
    runtime, _source, _storage, _state, config, manifest, _plan = _fixture(tmp_path)
    service = runtime.app.state.index_inventory_service
    original_discover = service.discover
    entered_two = threading.Event()
    release = threading.Event()
    guard = threading.Lock()
    active = 0
    peak = 0

    def controlled_discover(*_args, **_kwargs):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
            if active == 2:
                entered_two.set()
        try:
            assert release.wait(8), "controlled scan release timed out"
            return original_discover(*_args, **_kwargs)
        finally:
            with guard:
                active -= 1

    monkeypatch.setattr(service, "discover", controlled_discover)
    body = _inventory_request(config, manifest.content_hash)
    with TestClient(runtime.app) as client:
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(client.post, "/api/v1/bootstrap/index-inventory", headers=_headers(), json=body) for _ in range(3)]
            try:
                assert entered_two.wait(8), "two bounded scans did not enter"
                with guard:
                    assert peak == 2
            finally:
                release.set()
            responses = [future.result(timeout=15) for future in futures]
    assert all(response.status_code == 200 for response in responses), [
        (response.status_code, response.text) for response in responses
    ]
    assert peak == 2 and active == 0
