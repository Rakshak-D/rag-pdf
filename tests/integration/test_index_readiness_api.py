from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from backend.app import create_app
from backend.experiments.ablation_plans import create_ablation_plan
from backend.services.index_readiness import IndexReadinessService
from tests.integration.test_retrieval import make_plan, write_report
from tests.unit.test_experiment_service import _service


def setup_api(tmp_path, monkeypatch):
    config, plan = make_plan(tmp_path)
    storage = tmp_path / "index"
    report = write_report(storage, plan)
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    service, _, _ = _service(app_dir)
    readiness = IndexReadinessService(tmp_path / "pdfs", storage, report)
    monkeypatch.setattr("backend.vectorstore.ChromaVectorStore.open_existing", lambda *_args, **_kw: pytest.fail("Chroma must not open"))
    monkeypatch.setattr("chromadb.PersistentClient", lambda *_args, **_kw: pytest.fail("Chroma client must not be constructed"))
    monkeypatch.setattr("backend.embeddings.text.TextEmbeddingAdapter.load", lambda *_args, **_kw: pytest.fail("text model must not load"))
    monkeypatch.setattr("backend.embeddings.image.ImageEmbeddingAdapter.load", lambda *_args, **_kw: pytest.fail("image model must not load"))
    monkeypatch.setattr("backend.engine.reranking.CrossEncoderReranker.load", lambda *_args, **_kw: pytest.fail("reranker must not load"))
    client = TestClient(create_app(service, api_token="token", document_source_root=tmp_path / "pdfs", index_readiness_service=readiness))
    body = {"workflow": "evaluation", "resolved_config": config.model_dump(mode="json"), "expected_manifest_hash": plan.document_manifest_hash}
    return client, body, report, storage, plan


def post(client, body):
    return client.post("/api/v1/index/readiness", headers={"Authorization": "Bearer token"}, json=body)


def test_matching_report_is_not_claimed_as_store_verification(tmp_path, monkeypatch):
    client, body, report, storage, plan = setup_api(tmp_path, monkeypatch)
    before = report.read_bytes()
    response = post(client, body)
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "report_match"
    assert result["expected_index_fingerprint"] == plan.index_fingerprint
    assert result["store_checked"] is False and result["observed_text_records"] is None
    assert result["reported_text_records"] == 1 and result["reported_image_records"] == 1
    assert str(tmp_path) not in response.text
    assert report.read_bytes() == before and not (storage / "indexes").exists()
    assert client.post("/api/v1/index/readiness", json=body).status_code == 401


@pytest.mark.parametrize(("change", "expected"), [
    ({"index_fingerprint": "b" * 64}, ("mismatch", "identity_mismatch")),
    ({"document_manifest_hash": "c" * 64}, ("mismatch", "identity_mismatch")),
    ({"status": "failed"}, ("mismatch", "build_failed")),
    ({"partial_persistence": True}, ("mismatch", "partial_persistence")),
    ({"report_schema_version": "999"}, ("indeterminate", "report_invalid")),
    ({"status": "unknown"}, ("indeterminate", "report_invalid")),
    ({"status": []}, ("indeterminate", "report_invalid")),
    ({"partial_persistence": None}, ("indeterminate", "report_invalid")),
])
def test_incompatible_reports_have_stable_status(tmp_path, monkeypatch, change, expected):
    client, body, report, _, _ = setup_api(tmp_path, monkeypatch)
    payload = json.loads(report.read_text(encoding="utf-8"))
    payload.update(change)
    report.write_text(json.dumps(payload), encoding="utf-8")
    response = post(client, body)
    assert response.status_code == 200
    assert (response.json()["status"], response.json()["reason_code"]) == expected
    assert response.json()["reported_text_records"] is None


def test_missing_malformed_and_changed_manifest(tmp_path, monkeypatch):
    client, body, report, _, _ = setup_api(tmp_path, monkeypatch)
    report.write_text("not json", encoding="utf-8")
    assert post(client, body).json()["status"] == "indeterminate"
    report.unlink()
    assert post(client, body).json()["status"] == "unavailable"
    (tmp_path / "pdfs" / "document.pdf").write_bytes(b"changed")
    response = post(client, body)
    assert response.status_code == 409 and response.json()["error"]["code"] == "manifest_changed"


def test_ablation_uses_canonical_shared_fingerprint(tmp_path, monkeypatch):
    client, body, _, _, _ = setup_api(tmp_path, monkeypatch)
    body["workflow"] = "ablation"
    response = post(client, body)
    assert response.status_code == 200
    from backend.config.schema import ResolvedRunConfig
    expected = create_ablation_plan(ResolvedRunConfig.model_validate(body["resolved_config"]), body["expected_manifest_hash"])
    assert response.json()["expected_index_fingerprint"] == expected.shared_index_fingerprint


def test_invalid_inputs_and_empty_corpus_do_not_inspect_report(tmp_path, monkeypatch):
    client, body, report, _, _ = setup_api(tmp_path, monkeypatch)
    original = report.read_bytes()
    invalid = dict(body, resolved_config={})
    assert post(client, invalid).json()["error"]["code"] == "configuration_invalid"
    destructive = dict(body, resolved_config={**body["resolved_config"], "reset_collections_on_start": True})
    assert post(client, destructive).json()["error"]["code"] == "destructive_configuration_forbidden"
    assert post(client, dict(body, unexpected="private")).status_code == 400
    assert post(client, dict(body, expected_manifest_hash="z" * 64)).status_code == 400
    (tmp_path / "pdfs" / "document.pdf").unlink()
    assert post(client, body).json()["error"]["code"] == "manifest_empty"
    assert report.read_bytes() == original


def test_report_path_outside_owned_root_is_indeterminate(tmp_path, monkeypatch):
    client, body, report, _, _ = setup_api(tmp_path, monkeypatch)
    outside = IndexReadinessService(tmp_path / "pdfs", tmp_path / "other-root", report)
    client.app.state.index_readiness_service = outside
    response = post(client, body)
    assert response.status_code == 200
    assert response.json()["status"] == "indeterminate"
    assert str(tmp_path) not in response.text
