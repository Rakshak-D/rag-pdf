from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import threading

import pytest
from starlette.testclient import TestClient

import backend.services.bootstrap_documents as documents_service
from backend.bootstrap_app.runtime import build_bootstrap_runtime
from backend.config import ResolvedRunConfig
from backend.engine.manifest import build_document_manifest
from backend.experiments.run_plan import build_run_plan
from tests.integration.test_bootstrap_api import TOKEN, _fixture, _headers


def _pdf(*, pages: int = 1, encrypted: bool = False) -> bytes:
    import pymupdf

    document = pymupdf.open()
    try:
        for _ in range(pages):
            document.new_page()
        options = {}
        if encrypted:
            options = {
                "encryption": pymupdf.PDF_ENCRYPT_AES_256,
                "owner_pw": "test-owner-secret",
                "user_pw": "test-user-secret",
            }
        return document.tobytes(**options)
    finally:
        document.close()


def _document_fixture(tmp_path: Path, *, limit: int = 2 * 1024 * 1024, adapter_factory=None):
    runtime, source, storage, state, config, _manifest, _plan = _fixture(
        tmp_path, limit=limit, adapter_factory=adapter_factory
    )
    (source / "bootstrap.pdf").unlink()
    return runtime, source, storage, state, config


def _post(client: TestClient, body: bytes, *, filename: str = "Research note.pdf", headers=None):
    request_headers = {**_headers(), "Content-Type": "application/pdf", "X-Document-Filename": filename}
    if headers:
        request_headers.update(headers)
    return client.post("/api/v1/bootstrap/documents", headers=request_headers, content=body)


def test_documents_auth_empty_inventory_and_bounded_pagination(tmp_path):
    runtime, source, _storage, _state, _config = _document_fixture(tmp_path)
    with TestClient(runtime.app) as client:
        assert client.get("/api/v1/bootstrap/documents").status_code == 401
        assert client.post("/api/v1/bootstrap/documents", content=_pdf()).status_code == 401
        response = client.get("/api/v1/bootstrap/documents?limit=25&offset=0", headers=_headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["inventory_status"] == "available"
    assert body["documents"] == []
    assert body["total"] == 0 and body["complete"] is True
    assert body["manifest_hash"] == build_document_manifest(source).content_hash
    assert body["max_upload_bytes"] == 2 * 1024 * 1024
    assert body["max_pages"] == 200
    assert str(tmp_path) not in response.text
    assert "document_id" not in response.text


def test_document_inventory_returns_real_manifest_pages(tmp_path):
    runtime, source, *_ = _document_fixture(tmp_path)
    for name, payload in (("alpha.pdf", b"alpha"), ("bravo.pdf", b"bravo"), ("charlie.pdf", b"charlie")):
        (source / name).write_bytes(payload)
    manifest = build_document_manifest(source)
    with TestClient(runtime.app) as client:
        first = client.get("/api/v1/bootstrap/documents?limit=2&offset=0", headers=_headers())
        second = client.get("/api/v1/bootstrap/documents?limit=2&offset=2", headers=_headers())
    assert first.status_code == second.status_code == 200
    assert first.json()["total"] == 3 and second.json()["total"] == 3
    assert [entry["relative_path"] for entry in first.json()["documents"]] == ["alpha.pdf", "bravo.pdf"]
    assert [entry["relative_path"] for entry in second.json()["documents"]] == ["charlie.pdf"]
    assert first.json()["manifest_hash"] == second.json()["manifest_hash"] == manifest.content_hash


def test_valid_import_uses_manifest_identity_survives_restart_and_previews(tmp_path):
    def forbid_builder():
        pytest.fail("document import and preview must not construct an index adapter")

    runtime, source, storage, state, config = _document_fixture(tmp_path, adapter_factory=forbid_builder)
    pdf_bytes = _pdf()
    with TestClient(runtime.app) as client:
        initial = client.get("/api/v1/bootstrap/documents", headers=_headers()).json()
        assert initial["total"] == 0
        response = _post(client, pdf_bytes)
        assert response.status_code == 201, response.text
        imported = response.json()
        assert imported["outcome"] == "imported"
        assert imported["manifest_hash"]
        assert imported["document"] == {
            "document_id": build_document_manifest(source).entries[0].document_id,
            "relative_path": "Research note.pdf",
            "size_bytes": len(pdf_bytes),
            "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
        }
        listing = client.get("/api/v1/bootstrap/documents", headers=_headers()).json()
        manifest = build_document_manifest(source)
        assert listing["documents"] == [entry.model_dump(mode="json") for entry in manifest.entries]
        assert listing["manifest_hash"] == imported["manifest_hash"] == manifest.content_hash
        assert listing["total"] == 1
        assert not storage.exists()

    restarted = build_bootstrap_runtime(runtime.settings)
    with TestClient(restarted.app) as client:
        listing = client.get("/api/v1/bootstrap/documents", headers=_headers())
        assert listing.status_code == 200
        assert listing.json()["documents"][0]["document_id"] == imported["document"]["document_id"]
        current_manifest = build_document_manifest(source)
        preview = client.post(
            "/api/v1/bootstrap/preview",
            headers={**_headers(), "Content-Type": "application/json"},
            json={"resolved_config": config},
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["manifest_hash"] == current_manifest.content_hash
        assert preview.json()["document_count"] == 1
    assert not storage.exists()
    assert (state / "bootstrap-jobs.sqlite3").exists()


@pytest.mark.parametrize(
    "filename",
    [
        "../outside.pdf", "..\\outside.pdf", "C:outside.pdf", "C:\\outside.pdf",
        "/outside.pdf", "folder/name.pdf", "folder\\name.pdf", "CON.pdf",
        "aux.archive.pdf", "bad:name.pdf", "bad\nname.pdf", ".hidden.pdf", "Research.PDF",
    ],
)
def test_import_rejects_unsafe_or_noncanonical_filenames(tmp_path, filename):
    runtime, source, *_ = _document_fixture(tmp_path)
    with TestClient(runtime.app) as client:
        response = _post(client, _pdf(), filename=filename)
    assert response.status_code == 400
    assert response.json() == {"outcome": "rejected", "error": {"code": "filename_invalid"}}
    assert list(source.glob("*.pdf")) == []


def test_import_rejects_content_type_empty_and_invalid_pdf(tmp_path):
    runtime, source, *_ = _document_fixture(tmp_path)
    with TestClient(runtime.app) as client:
        unsupported = _post(client, _pdf(), headers={"Content-Type": "application/octet-stream"})
        empty = _post(client, b"", filename="Empty.pdf")
        invalid = _post(client, b"%PDF-not-a-document", filename="Invalid.pdf")
    assert unsupported.status_code == 415
    assert unsupported.json()["error"]["code"] == "unsupported_content_type"
    assert empty.status_code == 400 and empty.json()["error"]["code"] == "upload_empty"
    assert invalid.status_code == 400 and invalid.json()["error"]["code"] == "pdf_invalid", invalid.text
    assert list(source.glob("*.pdf")) == []


def test_import_rejects_encrypted_pdf_and_page_limit(tmp_path):
    runtime, source, *_ = _document_fixture(tmp_path)
    with TestClient(runtime.app) as client:
        encrypted = _post(client, _pdf(encrypted=True), filename="Encrypted.pdf")
        too_many_pages = _post(client, _pdf(pages=201), filename="Long.pdf")
    assert encrypted.status_code == 400
    assert encrypted.json()["error"]["code"] == "pdf_encrypted_or_unsupported"
    assert too_many_pages.status_code == 413
    assert too_many_pages.json()["error"]["code"] == "pdf_page_limit_exceeded"
    assert list(source.glob("*.pdf")) == []


def test_import_enforces_configured_body_limit(tmp_path):
    runtime, source, *_ = _document_fixture(tmp_path, limit=128)
    with TestClient(runtime.app) as client:
        response = _post(client, _pdf(), filename="Large.pdf")
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "upload_too_large"
    assert list(source.glob("*.pdf")) == []
    assert list(source.glob(".rag-upload-*.part")) == []


def test_import_rejects_duplicate_filename_and_duplicate_stem(tmp_path):
    runtime, source, *_ = _document_fixture(tmp_path)
    (source / "Existing.pdf").write_bytes(_pdf())
    with TestClient(runtime.app) as client:
        same_name = _post(client, _pdf(), filename="Existing.pdf")
        casefold_stem = _post(client, _pdf(), filename="existing.pdf")
    for response in (same_name, casefold_stem):
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "document_name_conflict"
    assert len(list(source.glob("*.pdf"))) == 1


def test_concurrent_imports_cannot_overwrite_or_commit_twice(tmp_path, monkeypatch):
    runtime, source, *_ = _document_fixture(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    original_validate = documents_service._validate_pdf
    calls = 0
    call_lock = threading.Lock()

    def delayed_validate(path, **kwargs):
        nonlocal calls
        with call_lock:
            calls += 1
            first = calls == 1
        if first:
            entered.set()
            assert release.wait(5)
        return original_validate(path, **kwargs)

    monkeypatch.setattr(documents_service, "_validate_pdf", delayed_validate)
    client = TestClient(runtime.app)
    try:
        with client:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(_post, client, _pdf(), filename="One.pdf")
                assert entered.wait(5)
                second = pool.submit(_post, client, _pdf(), filename="One.pdf")
                second_response = second.result(timeout=5)
                release.set()
                first_response = first.result(timeout=5)
    finally:
        release.set()
    assert sorted((first_response.status_code, second_response.status_code)) == [201, 409]
    assert len(list(source.glob("*.pdf"))) == 1
    assert list(source.glob(".rag-upload-*.part")) == []


def test_temporary_write_failure_is_rejected_and_cleaned(tmp_path, monkeypatch):
    runtime, source, *_ = _document_fixture(tmp_path)
    with TestClient(runtime.app) as client:
        monkeypatch.setattr(documents_service.os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError("private path")))
        response = _post(client, _pdf())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "upload_write_failed"
    assert list(source.glob("*.pdf")) == []
    assert list(source.glob(".rag-upload-*.part")) == []


def test_publication_failure_does_not_create_final_document(tmp_path, monkeypatch):
    runtime, source, *_ = _document_fixture(tmp_path)
    with TestClient(runtime.app) as client:
        monkeypatch.setattr(documents_service.os, "link", lambda *_args: (_ for _ in ()).throw(OSError("private path")))
        response = _post(client, _pdf())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "document_publication_failed"
    assert list(source.glob("*.pdf")) == []
    assert list(source.glob(".rag-upload-*.part")) == []


def test_publication_error_after_atomic_link_is_reported_as_committed_uncertain(tmp_path, monkeypatch):
    runtime, source, *_ = _document_fixture(tmp_path)
    original_link = documents_service.os.link
    pdf_bytes = _pdf()

    def link_then_raise(source_path, destination_path):
        original_link(source_path, destination_path)
        raise OSError("ambiguous publication response")

    with TestClient(runtime.app) as client:
        monkeypatch.setattr(documents_service.os, "link", link_then_raise)
        response = _post(client, pdf_bytes)
    assert response.status_code == 503
    assert response.json()["outcome"] == "committed_inventory_unavailable"
    assert response.json()["error_code"] == "publication_confirmation_failed"
    assert response.json()["document"]["sha256"] == hashlib.sha256(pdf_bytes).hexdigest()
    assert (source / "Research note.pdf").read_bytes() == pdf_bytes
    assert list(source.glob(".rag-upload-*.part")) == []


def test_manifest_confirmation_failure_reports_committed_unconfirmed_file(tmp_path, monkeypatch):
    runtime, source, *_ = _document_fixture(tmp_path)
    pdf_bytes = _pdf()
    original = documents_service.build_document_manifest
    calls = 0

    def fail_after_publication(path):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("private manifest failure")
        return original(path)

    with TestClient(runtime.app) as client:
        monkeypatch.setattr(documents_service, "build_document_manifest", fail_after_publication)
        response = _post(client, pdf_bytes)
    assert response.status_code == 503
    assert response.json()["outcome"] == "committed_inventory_unavailable"
    assert response.json()["manifest_hash"] is None
    assert response.json()["error_code"] == "manifest_confirmation_failed"
    record = response.json()["document"]
    assert record["relative_path"] == "Research note.pdf"
    assert record["sha256"] == hashlib.sha256(pdf_bytes).hexdigest()
    assert (source / "Research note.pdf").is_file()
    monkeypatch.setattr(documents_service, "build_document_manifest", original)
    assert build_document_manifest(source).entries[0].document_id == record["document_id"]


def test_manifest_rejects_symlinked_source_entry(tmp_path):
    runtime, source, *_ = _document_fixture(tmp_path)
    outside = tmp_path / "outside.pdf"
    outside_bytes = _pdf()
    outside.write_bytes(outside_bytes)
    link = source / "linked.pdf"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("file symlinks are unavailable in this Windows environment")
    with TestClient(runtime.app) as client:
        inventory = client.get("/api/v1/bootstrap/documents", headers=_headers())
        upload = _post(client, _pdf(), filename="New.pdf")
    assert inventory.status_code == 503
    assert upload.status_code == 503
    assert outside.read_bytes() == outside_bytes
    link.unlink()


def test_source_root_unavailable_fails_closed(tmp_path):
    runtime, source, *_ = _document_fixture(tmp_path)
    source.rmdir()
    with TestClient(runtime.app) as client:
        unavailable = client.get("/api/v1/bootstrap/documents", headers=_headers())
    assert unavailable.status_code == 503


def test_import_conflicts_with_queued_or_running_build(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def adapter_factory():
        entered.set()
        release.wait(5)
        raise RuntimeError("synthetic pre-build stop")

    runtime, source, _storage, _state, config = _document_fixture(tmp_path, adapter_factory=adapter_factory)
    (source / "seed.pdf").write_bytes(_pdf())
    manifest = build_document_manifest(source)
    plan_config = dict(config)
    resolved = ResolvedRunConfig.model_validate(plan_config)
    plan = build_run_plan(resolved, manifest.content_hash)
    body = {
        "resolved_config": plan_config,
        "expected_manifest_hash": manifest.content_hash,
        "expected_index_fingerprint": plan.index_fingerprint,
        "client_request_id": "document-build-request-0001",
    }
    try:
        with TestClient(runtime.app) as client:
            submitted = client.post("/api/v1/bootstrap/jobs", headers={**_headers(), "Content-Type": "application/json"}, json=body)
            assert submitted.status_code == 202, submitted.text
            assert entered.wait(5)
            rejected = _post(client, _pdf(), filename="During build.pdf")
            assert rejected.status_code == 409
            assert rejected.json()["error"]["code"] == "document_source_busy"
            assert not (source / "During build.pdf").exists()
    finally:
        release.set()


def test_build_admission_conflicts_with_an_active_source_mutation(tmp_path):
    runtime, source, _storage, _state, config = _document_fixture(tmp_path)
    (source / "seed.pdf").write_bytes(_pdf())
    manifest = build_document_manifest(source)
    plan_config = dict(config)
    plan = build_run_plan(ResolvedRunConfig.model_validate(plan_config), manifest.content_hash)
    body = {
        "resolved_config": plan_config,
        "expected_manifest_hash": manifest.content_hash,
        "expected_index_fingerprint": plan.index_fingerprint,
        "client_request_id": "source-mutation-request-01",
    }
    with TestClient(runtime.app) as client:
        with runtime.job_manager.source_mutation():
            response = client.post(
                "/api/v1/bootstrap/jobs",
                headers={**_headers(), "Content-Type": "application/json"},
                json=body,
            )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "document_source_busy"
        history = client.get("/api/v1/bootstrap/jobs", headers=_headers())
        assert history.status_code == 200 and history.json()["jobs"] == []
