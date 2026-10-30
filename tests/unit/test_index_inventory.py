from __future__ import annotations

import json
from pathlib import Path
import socket

import pytest

from backend.engine.manifest import build_document_manifest
from backend.services.index_inventory import IndexInventoryInputError, IndexInventoryService
from backend.config.writer_lease import WriterLease
from tests.integration.test_retrieval import make_plan, write_report


def _setup(tmp_path: Path):
    config, plan = make_plan(tmp_path)
    storage = tmp_path / "index-storage"
    storage.mkdir()
    service = IndexInventoryService(tmp_path / "pdfs", storage)
    return config, plan, storage, service


def _write(storage: Path, plan, build_id: str, **kwargs) -> Path:
    return write_report(storage, plan, build_id=build_id, **kwargs)


def _payload(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_empty_inventory_is_complete_and_read_only(tmp_path):
    config, plan, storage, service = _setup(tmp_path)
    before = sorted(p.relative_to(storage).as_posix() for p in storage.rglob("*"))
    page = service.discover(config, plan.document_manifest_hash)
    after = sorted(p.relative_to(storage).as_posix() for p in storage.rglob("*"))
    assert page.complete and page.reason_code == "no_build_reports"
    assert page.total_candidates == 0 and page.candidates == ()
    assert page.expected_index_fingerprint == plan.index_fingerprint
    assert page.document_manifest_hash == plan.document_manifest_hash
    assert page.check_level == "report" and page.store_checked is False
    assert before == after and not (storage / "indexes").exists()


def test_one_compatible_report_returns_only_validated_report_metadata(tmp_path):
    config, plan, storage, service = _setup(tmp_path)
    build_id = "a" * 32
    report = _write(storage, plan, build_id)
    before = report.read_bytes()
    page = service.discover(config, plan.document_manifest_hash)
    candidate, = page.candidates
    assert page.complete and candidate.compatibility == "report_match"
    assert candidate.reason_code == "report_matches"
    assert candidate.build_id == build_id
    assert candidate.report_reference == f"build-artifacts/{build_id}/build-report.json"
    assert candidate.expected_index_fingerprint == plan.index_fingerprint
    assert candidate.reported_index_fingerprint == plan.index_fingerprint
    assert candidate.reported_document_manifest_hash == plan.document_manifest_hash
    assert candidate.report_status == "completed"
    assert (candidate.reported_text_records, candidate.reported_image_records) == (1, 1)
    assert candidate.check_level == "report" and candidate.store_checked is False
    assert str(tmp_path) not in repr(candidate)
    assert report.read_bytes() == before and not (storage / "indexes").exists()


def test_multiple_reports_are_lexically_ordered_and_paginated_without_recency_claim(tmp_path):
    config, plan, storage, service = _setup(tmp_path)
    for build_id in ("f" * 32, "1" * 32, "8" * 32):
        _write(storage, plan, build_id)
    first = service.discover(config, plan.document_manifest_hash, limit=2, offset=0)
    second = service.discover(config, plan.document_manifest_hash, limit=2, offset=2)
    assert first.total_candidates == 3 and second.total_candidates == 3
    assert [c.build_id for c in first.candidates] == ["1" * 32, "8" * 32]
    assert [c.build_id for c in second.candidates] == ["f" * 32]
    assert all(c.compatibility == "report_match" for c in first.candidates + second.candidates)


@pytest.mark.parametrize(
    ("changes", "compatibility", "reason"),
    [
        ({"index_fingerprint": "b" * 64}, "mismatch", "index_fingerprint_mismatch"),
        ({"document_manifest_hash": "c" * 64}, "mismatch", "manifest_hash_mismatch"),
        ({"status": "failed"}, "failed", "build_failed"),
        ({"partial_persistence": True}, "incomplete", "partial_persistence"),
        ({"report_schema_version": "999"}, "invalid", "report_schema_unsupported"),
        ({"status": "unknown"}, "invalid", "report_status_invalid"),
    ],
)
def test_report_candidates_are_classified_without_exposing_unverified_counts(
    tmp_path, changes, compatibility, reason
):
    config, plan, storage, service = _setup(tmp_path)
    report = _write(storage, plan, "a" * 32)
    payload = _payload(report)
    payload.update(changes)
    report.write_text(json.dumps(payload), encoding="utf-8")
    candidate, = service.discover(config, plan.document_manifest_hash).candidates
    assert candidate.compatibility == compatibility
    assert candidate.reason_code == reason
    if compatibility != "report_match":
        assert candidate.reported_text_records is None
        assert candidate.reported_image_records is None
    if compatibility in {"failed", "incomplete"} and reason in {"build_failed", "partial_persistence"}:
        assert candidate.reported_index_fingerprint == plan.index_fingerprint
        assert candidate.reported_document_manifest_hash == plan.document_manifest_hash


def test_malformed_report_json_and_wrong_directory_build_id_are_invalid(tmp_path):
    config, plan, storage, service = _setup(tmp_path)
    first = storage / "build-artifacts" / ("a" * 32)
    first.mkdir(parents=True)
    (first / "build-report.json").write_text("{not json", encoding="utf-8")
    candidate, = service.discover(config, plan.document_manifest_hash).candidates
    assert candidate.compatibility == "invalid" and candidate.reason_code == "report_invalid"

    report = _write(storage, plan, "b" * 32)
    payload = _payload(report)
    payload["build_id"] = "c" * 32
    report.write_text(json.dumps(payload), encoding="utf-8")
    rows = service.discover(config, plan.document_manifest_hash).candidates
    wrong_id, = [row for row in rows if row.build_id == "b" * 32]
    assert wrong_id.compatibility == "invalid" and wrong_id.reason_code == "build_id_mismatch"


def test_report_path_claim_must_match_its_artifact_hierarchy(tmp_path):
    config, plan, storage, service = _setup(tmp_path)
    report = _write(storage, plan, "a" * 32)
    payload = _payload(report)
    payload["report_path"] = str(tmp_path / "outside" / "build-report.json")
    report.write_text(json.dumps(payload), encoding="utf-8")
    candidate, = service.discover(config, plan.document_manifest_hash).candidates
    assert candidate.compatibility == "invalid" and candidate.reason_code == "report_outside_storage"
    assert str(tmp_path) not in repr(candidate)


@pytest.mark.parametrize("corruption", ["duplicate_key", "non_finite", "path_traversal"])
def test_strict_inspection_rejects_duplicate_nonfinite_and_traversal_report_data(tmp_path, corruption):
    config, plan, storage, service = _setup(tmp_path)
    report = _write(storage, plan, "a" * 32)
    content = report.read_text(encoding="utf-8")
    if corruption == "duplicate_key":
        content = content.replace('"status": "completed"', '"status": "completed", "status": "failed"')
    elif corruption == "non_finite":
        content = content[:-1] + ', "text_embedding_seconds": NaN}'
    else:
        build_id = "a" * 32
        escaped_spelling = storage / "build-artifacts" / build_id / ".." / build_id / "build-report.json"
        payload = json.loads(content)
        payload["report_path"] = str(escaped_spelling)
        content = json.dumps(payload)
    report.write_text(content, encoding="utf-8")
    candidate, = service.discover(config, plan.document_manifest_hash).candidates
    assert candidate.compatibility == "invalid"
    assert candidate.reason_code in {"report_invalid", "report_location_invalid"}


def test_storage_overlap_and_invalid_manifest_identity_are_rejected(tmp_path):
    config, plan, _, _ = _setup(tmp_path)
    with pytest.raises(IndexInventoryInputError, match="inventory_roots_overlap"):
        IndexInventoryService(tmp_path / "pdfs", tmp_path / "pdfs")
    service = IndexInventoryService(tmp_path / "pdfs", tmp_path / "other-index")
    with pytest.raises(IndexInventoryInputError, match="manifest_hash_invalid"):
        service.discover(config, "not-a-sha256")
    with pytest.raises(IndexInventoryInputError, match="inventory_path_invalid"):
        IndexInventoryService(tmp_path / "pdfs", tmp_path / ".." / "outside")


def test_incomplete_or_destructive_configuration_is_rejected(tmp_path):
    config, plan, _, service = _setup(tmp_path)
    destructive = config.model_copy(update={"reset_collections_on_start": True})
    with pytest.raises(IndexInventoryInputError, match="destructive_configuration_forbidden"):
        service.discover(destructive, plan.document_manifest_hash)
    incomplete = {"pdf_dir": config.pdf_dir}
    with pytest.raises(IndexInventoryInputError, match="configuration_invalid"):
        service.discover(incomplete, plan.document_manifest_hash)


def test_invalid_build_id_is_reported_without_echoing_arbitrary_name(tmp_path):
    config, plan, storage, service = _setup(tmp_path)
    private_name = "operator-private-name..-folder"
    artifacts = storage / "build-artifacts"
    artifacts.mkdir()
    (artifacts / private_name).mkdir()
    candidate, = service.discover(config, plan.document_manifest_hash).candidates
    assert candidate.build_id is None and candidate.report_reference is None
    assert candidate.compatibility == "invalid" and candidate.reason_code == "build_id_invalid"
    assert private_name not in repr(candidate)
    assert candidate.candidate_id.startswith("entry-")


def test_missing_report_and_oversized_report_are_not_matches(tmp_path):
    config, plan, storage, service = _setup(tmp_path)
    (storage / "build-artifacts" / ("a" * 32)).mkdir(parents=True)
    missing, = service.discover(config, plan.document_manifest_hash).candidates
    assert missing.compatibility == "unavailable" and missing.reason_code == "report_unavailable"

    artifacts = storage / "build-artifacts"
    (artifacts / ("c" * 32)).write_text("unexpected file entry", encoding="utf-8")
    unexpected, = [row for row in service.discover(config, plan.document_manifest_hash).candidates if row.build_id == "c" * 32]
    assert unexpected.compatibility == "invalid" and unexpected.reason_code == "unexpected_entry_type"

    report = _write(storage, plan, "b" * 32)
    report.write_text(" " * 2048, encoding="utf-8")
    limited = IndexInventoryService(tmp_path / "pdfs", storage, max_report_bytes=256)
    row = [item for item in limited.discover(config, plan.document_manifest_hash).candidates if item.build_id == "b" * 32][0]
    assert row.compatibility == "incomplete" and row.reason_code == "report_too_large"
    assert row.reported_text_records is None and row.reported_image_records is None


def test_incomplete_marker_blocks_report_match_without_opening_store(tmp_path):
    config, plan, storage, service = _setup(tmp_path)
    _write(storage, plan, "a" * 32)
    index_root = storage / "indexes" / plan.index_fingerprint
    index_root.mkdir(parents=True)
    (index_root / ".fresh-build-incomplete").write_text(plan.index_fingerprint, encoding="ascii")
    row, = service.discover(config, plan.document_manifest_hash).candidates
    assert row.compatibility == "incomplete" and row.reason_code == "fresh_build_incomplete"
    assert row.store_checked is False


def test_capacity_limit_never_returns_a_partial_inventory_as_complete(tmp_path):
    config, plan, storage, _ = _setup(tmp_path)
    artifacts = storage / "build-artifacts"
    artifacts.mkdir()
    for digit in "012":
        (artifacts / (digit * 32)).mkdir()
    service = IndexInventoryService(tmp_path / "pdfs", storage, max_entries=2)
    page = service.discover(config, plan.document_manifest_hash, limit=2)
    assert not page.complete and page.reason_code == "inventory_limit_exceeded"
    assert page.total_candidates is None and page.candidates == ()


def test_input_manifest_configuration_and_pagination_validation(tmp_path):
    config, plan, storage, service = _setup(tmp_path)
    with pytest.raises(IndexInventoryInputError, match="manifest_changed"):
        service.discover(config, "0" * 64)
    with pytest.raises(IndexInventoryInputError, match="pagination_invalid"):
        service.discover(config, plan.document_manifest_hash, limit=0)
    (tmp_path / "pdfs" / "document.pdf").unlink()
    with pytest.raises(IndexInventoryInputError, match="manifest_empty"):
        service.discover(config, plan.document_manifest_hash)


def test_source_change_during_inventory_is_not_returned_as_a_stable_match(tmp_path, monkeypatch):
    config, plan, storage, service = _setup(tmp_path)
    _write(storage, plan, "a" * 32)
    original = service._inspect_candidate

    def change_source_after_candidate(*args):
        candidate = original(*args)
        (tmp_path / "pdfs" / "document.pdf").write_bytes(b"changed while inventory was running")
        return candidate

    monkeypatch.setattr(service, "_inspect_candidate", change_source_after_candidate)
    with pytest.raises(IndexInventoryInputError, match="manifest_changed"):
        service.discover(config, plan.document_manifest_hash)


def test_no_chroma_models_writer_lease_network_or_storage_mutation(tmp_path, monkeypatch):
    config, plan, storage, service = _setup(tmp_path)
    report = _write(storage, plan, "a" * 32)
    before = {p.relative_to(storage).as_posix(): p.read_bytes() for p in storage.rglob("*") if p.is_file()}

    def forbidden(*_args, **_kwargs):
        pytest.fail("read-only inventory invoked a forbidden initializer or network operation")

    monkeypatch.setattr(WriterLease, "acquire", forbidden)
    monkeypatch.setattr("chromadb.PersistentClient", forbidden)
    monkeypatch.setattr("backend.embeddings.text.TextEmbeddingAdapter.load", forbidden)
    monkeypatch.setattr("backend.embeddings.image.ImageEmbeddingAdapter.load", forbidden)
    monkeypatch.setattr("backend.engine.reranking.CrossEncoderReranker.load", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)

    page = service.discover(config, plan.document_manifest_hash)
    after = {p.relative_to(storage).as_posix(): p.read_bytes() for p in storage.rglob("*") if p.is_file()}
    assert len(page.candidates) == 1 and page.candidates[0].compatibility == "report_match"
    assert before == after and report.read_bytes() == before["build-artifacts/" + "a" * 32 + "/build-report.json"]
    assert not (storage / "indexes").exists()


def test_source_root_mismatch_and_symlinked_artifact_paths_fail_closed(tmp_path):
    config, plan, storage, _ = _setup(tmp_path)
    other = tmp_path / "other-source"
    other.mkdir()
    with pytest.raises(IndexInventoryInputError, match="configuration_source_mismatch"):
        IndexInventoryService(other, storage).discover(config, plan.document_manifest_hash)

    report = _write(storage, plan, "a" * 32)
    target = report.with_name("actual-report.json")
    report.rename(target)
    try:
        report.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Windows symlink creation is unavailable in this environment: {type(exc).__name__}")
    service = IndexInventoryService(tmp_path / "pdfs", storage)
    row, = service.discover(config, plan.document_manifest_hash).candidates
    assert row.compatibility == "invalid" and row.reason_code == "symlink_path"
    assert target.exists() and target.read_bytes()


def test_symlinked_build_artifacts_root_is_incomplete_and_never_followed(tmp_path):
    config, plan, storage, _ = _setup(tmp_path)
    outside = tmp_path / "outside-artifacts"
    outside.mkdir()
    (outside / ("a" * 32)).mkdir()
    try:
        (storage / "build-artifacts").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Windows symlink creation is unavailable in this environment: {type(exc).__name__}")
    page = IndexInventoryService(tmp_path / "pdfs", storage).discover(config, plan.document_manifest_hash)
    assert not page.complete and page.reason_code == "build_artifacts_symlink"
    assert page.candidates == () and (outside / ("a" * 32)).is_dir()
