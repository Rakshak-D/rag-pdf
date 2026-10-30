from __future__ import annotations

import json
import os
from pathlib import Path
import socket

import pytest

from backend.config.writer_lease import WriterLease
from backend.engine.reranking import CrossEncoderReranker
from backend.embeddings.image import ImageEmbeddingAdapter
from backend.embeddings.text import TextEmbeddingAdapter
from backend.engine import index_builder
from backend.services.index_inventory import IndexInventoryService
from backend.vectorstore import ChromaVectorStore
from backend.vectorstore.chroma_store import _IndexStoreManifest
from scripts import report_handoff_preflight as preflight_module
from scripts.report_handoff_preflight import PreflightError, run_preflight
from tests.integration.test_retrieval import make_plan, write_report


BUILD_ID = "a" * 32


def _setup(tmp_path: Path):
    config, plan = make_plan(tmp_path)
    source = tmp_path / "pdfs"
    storage = tmp_path / "storage"
    storage.mkdir()
    config_path = tmp_path / "resolved-config.json"
    config_path.write_text(config.model_dump_json(), encoding="utf-8")
    return config, plan, source, storage, config_path


def _args(source: Path, storage: Path, config_path: Path, plan, build_id: str = BUILD_ID):
    return dict(
        config_path=config_path,
        source_root=source,
        storage_root=storage,
        build_id=build_id,
        expected_manifest_hash=plan.document_manifest_hash,
        expected_index_fingerprint=plan.index_fingerprint,
    )


def _seed_report(storage: Path, plan, build_id: str = BUILD_ID, **kwargs) -> Path:
    return write_report(storage, plan, build_id=build_id, **kwargs)


def _seed_index(storage: Path, plan) -> Path:
    index_root = storage / "indexes" / plan.index_fingerprint
    (index_root / "chroma").mkdir(parents=True)
    index_manifest = _IndexStoreManifest(
        index_fingerprint=plan.index_fingerprint,
        document_manifest_hash=plan.document_manifest_hash,
    )
    (index_root / "index-manifest.json").write_text(index_manifest.canonical_json(), encoding="utf-8")
    return index_root


def _snapshot(root: Path):
    rows = []
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes() if path.is_file() and not path.is_symlink() else None
        rows.append((relative, info.st_mode, info.st_size, info.st_mtime_ns, content))
    return tuple(rows)


def test_explicit_selection_reports_only_report_level_evidence_and_is_read_only(tmp_path, monkeypatch):
    config, plan, source, storage, config_path = _setup(tmp_path)
    selected = _seed_report(storage, plan, BUILD_ID, text_count=3, image_count=2)
    _seed_report(storage, plan, "b" * 32, text_count=99, image_count=99)
    _seed_index(storage, plan)
    before = _snapshot(storage)
    env_before = {key: os.environ.get(key) for key in ("RAG_WORKBENCH_INDEX_STORAGE_ROOT", "RAG_WORKBENCH_BUILD_REPORT_PATH")}

    def forbidden(*_args, **_kwargs):
        pytest.fail("preflight attempted a Chroma or writer operation")

    monkeypatch.setattr(WriterLease, "acquire", forbidden)
    monkeypatch.setattr("chromadb.PersistentClient", forbidden)
    monkeypatch.setattr(ChromaVectorStore, "open_existing", forbidden)
    monkeypatch.setattr(ChromaVectorStore, "create_new", forbidden)
    monkeypatch.setattr(index_builder, "build_index", forbidden)
    monkeypatch.setattr(TextEmbeddingAdapter, "load", forbidden)
    monkeypatch.setattr(ImageEmbeddingAdapter, "load", forbidden)
    monkeypatch.setattr(CrossEncoderReranker, "load", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)

    result = run_preflight(**_args(source, storage, config_path, plan))
    assert result["result"] == "report_compatible_for_manual_review"
    assert result["build_id"] == BUILD_ID
    assert result["report_reference"] == f"build-artifacts/{BUILD_ID}/build-report.json"
    assert result["reported_text_records"] == 3
    assert result["reported_image_records"] == 2
    assert result["check_level"] == "report" and result["store_checked"] is False
    assert result["filesystem_observation"] == {
        "index_directory": "present",
        "index_manifest": "identity_matches",
        "chroma_directory": "present_not_opened",
        "writer_reservation": "absent_at_check",
    }
    assert "RAG_WORKBENCH_INDEX_STORAGE_ROOT" in " ".join(result["manual_runtime_steps"])
    assert before == _snapshot(storage)
    assert selected.is_file()
    assert env_before == {key: os.environ.get(key) for key in env_before}
    assert not (storage / ".writer-leases").exists()


@pytest.mark.parametrize(
    ("build_id", "expected_error"),
    [
        ("bad", "build_id_invalid"),
        ("c" * 32, "report_missing"),
    ],
)
def test_invalid_or_missing_explicit_selection_fails_closed(tmp_path, build_id, expected_error):
    _, plan, source, storage, config_path = _setup(tmp_path)
    with pytest.raises(PreflightError) as caught:
        run_preflight(**_args(source, storage, config_path, plan, build_id))
    assert caught.value.code == expected_error


def test_multiple_reports_are_never_implicitly_selected(tmp_path):
    _, plan, source, storage, config_path = _setup(tmp_path)
    _seed_report(storage, plan, BUILD_ID)
    _seed_report(storage, plan, "b" * 32)
    _seed_index(storage, plan)
    with pytest.raises(PreflightError, match="build_id_invalid"):
        run_preflight(**_args(source, storage, config_path, plan, ""))
    result = run_preflight(**_args(source, storage, config_path, plan, "b" * 32))
    assert result["build_id"] == "b" * 32


@pytest.mark.parametrize(
    ("which", "error"),
    [("manifest", "manifest_changed"), ("fingerprint", "index_fingerprint_changed")],
)
def test_confirmed_identities_must_match_recomputed_plan(tmp_path, which, error):
    _, plan, source, storage, config_path = _setup(tmp_path)
    _seed_report(storage, plan)
    _seed_index(storage, plan)
    args = _args(source, storage, config_path, plan)
    args["expected_manifest_hash" if which == "manifest" else "expected_index_fingerprint"] = "0" * 64
    with pytest.raises(PreflightError) as caught:
        run_preflight(**args)
    assert caught.value.code == error


def test_changed_source_is_rejected(tmp_path):
    _, plan, source, storage, config_path = _setup(tmp_path)
    _seed_report(storage, plan)
    _seed_index(storage, plan)
    (source / "document.pdf").write_bytes(b"changed after preview")
    with pytest.raises(PreflightError) as caught:
        run_preflight(**_args(source, storage, config_path, plan))
    assert caught.value.code == "manifest_changed"


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"status": "failed"}, "report_failed"),
        ({"partial_persistence": True}, "report_partial"),
        ({"report_schema_version": "999"}, "report_corrupt"),
        ({"build_id": "f" * 32}, "report_corrupt"),
        ({"index_fingerprint": "0" * 64}, "report_identity_mismatch"),
        ({"document_manifest_hash": "0" * 64}, "report_identity_mismatch"),
        ({"status": "unknown"}, "report_corrupt"),
        ({"report_path": "../outside/build-report.json"}, "report_path_unsafe"),
        ({"artifact_root": "../outside"}, "report_path_unsafe"),
    ],
)
def test_unsuccessful_or_inconsistent_reports_are_rejected(tmp_path, changes, error):
    _, plan, source, storage, config_path = _setup(tmp_path)
    report = _seed_report(storage, plan)
    payload = json.loads(report.read_text(encoding="utf-8"))
    payload.update(changes)
    report.write_text(json.dumps(payload), encoding="utf-8")
    _seed_index(storage, plan)
    with pytest.raises(PreflightError) as caught:
        run_preflight(**_args(source, storage, config_path, plan))
    assert caught.value.code == error
    assert report.exists()


def test_malformed_and_oversized_reports_are_rejected_without_rewrite(tmp_path):
    _, plan, source, storage, config_path = _setup(tmp_path)
    report = _seed_report(storage, plan)
    original = report.read_bytes()
    report.write_bytes(b"{not json")
    malformed = report.read_bytes()
    _seed_index(storage, plan)
    with pytest.raises(PreflightError, match="report_corrupt"):
        run_preflight(**_args(source, storage, config_path, plan))
    assert report.read_bytes() == malformed
    report.write_bytes(b" " * (1024 * 1024 + 1))
    oversized = report.read_bytes()
    with pytest.raises(PreflightError, match="report_oversized"):
        run_preflight(**_args(source, storage, config_path, plan))
    assert report.read_bytes() == oversized
    assert report.read_bytes() != original


def test_non_file_report_entry_is_corrupt_not_missing(tmp_path):
    _, plan, source, storage, config_path = _setup(tmp_path)
    report = _seed_report(storage, plan)
    report.unlink()
    report.mkdir()
    _seed_index(storage, plan)
    with pytest.raises(PreflightError, match="report_corrupt"):
        run_preflight(**_args(source, storage, config_path, plan))


def test_incomplete_marker_missing_index_and_writer_reservation_block_handoff(tmp_path):
    _, plan, source, storage, config_path = _setup(tmp_path)
    report = _seed_report(storage, plan)
    with pytest.raises(PreflightError, match="index_directory_missing"):
        run_preflight(**_args(source, storage, config_path, plan))

    index_root = _seed_index(storage, plan)
    marker = index_root / ".fresh-build-incomplete"
    marker.write_text(plan.index_fingerprint, encoding="ascii")
    with pytest.raises(PreflightError, match="incomplete_marker_present"):
        run_preflight(**_args(source, storage, config_path, plan))
    marker.unlink()

    lease = storage / ".writer-leases" / plan.index_fingerprint
    lease.mkdir(parents=True)
    with pytest.raises(PreflightError, match="writer_reservation_present"):
        run_preflight(**_args(source, storage, config_path, plan))
    assert report.exists() and not marker.exists() and lease.is_dir()


def test_index_manifest_identity_and_chroma_directory_are_required_but_not_opened(tmp_path):
    _, plan, source, storage, config_path = _setup(tmp_path)
    _seed_report(storage, plan)
    index_root = _seed_index(storage, plan)
    manifest = index_root / "index-manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(PreflightError, match="index_manifest_mismatch"):
        run_preflight(**_args(source, storage, config_path, plan))
    wrong_version = _IndexStoreManifest(
        index_fingerprint=plan.index_fingerprint,
        document_manifest_hash=plan.document_manifest_hash,
        store_schema_version="999",
    )
    manifest.write_text(wrong_version.canonical_json(), encoding="utf-8")
    with pytest.raises(PreflightError, match="index_manifest_mismatch"):
        run_preflight(**_args(source, storage, config_path, plan))
    valid = _IndexStoreManifest(index_fingerprint=plan.index_fingerprint, document_manifest_hash=plan.document_manifest_hash)
    manifest.write_text(valid.canonical_json(), encoding="utf-8")
    (index_root / "chroma").rmdir()
    with pytest.raises(PreflightError, match="chroma_directory_missing"):
        run_preflight(**_args(source, storage, config_path, plan))


@pytest.mark.parametrize("payload_text,error", [
    ('{"a":1,"a":2}', "configuration_invalid"),
    ('{"a":NaN}', "configuration_invalid"),
    ('{}', "configuration_invalid"),
])
def test_config_json_is_strict_and_complete(tmp_path, payload_text, error):
    _, plan, source, storage, config_path = _setup(tmp_path)
    config_path.write_text(payload_text, encoding="utf-8")
    with pytest.raises(PreflightError) as caught:
        run_preflight(**_args(source, storage, config_path, plan))
    assert caught.value.code == error


def test_destructive_config_and_overlapping_roots_are_rejected(tmp_path):
    config, plan, source, storage, config_path = _setup(tmp_path)
    values = config.model_dump(mode="json")
    values["workbench_policy"]["allow_collection_delete"] = True
    config_path.write_text(json.dumps(values), encoding="utf-8")
    with pytest.raises(PreflightError, match="destructive_configuration_forbidden"):
        run_preflight(**_args(source, storage, config_path, plan))

    config_path.write_text(config.model_dump_json(), encoding="utf-8")
    with pytest.raises(PreflightError, match="source_storage_overlap"):
        run_preflight(**_args(source, source, config_path, plan))


def test_missing_config_and_unsafe_trusted_roots_have_stable_codes(tmp_path):
    _, plan, source, storage, config_path = _setup(tmp_path)
    with pytest.raises(PreflightError, match="configuration_missing"):
        run_preflight(**{**_args(source, storage, config_path, plan), "config_path": None})
    with pytest.raises(PreflightError, match="source_root_invalid"):
        run_preflight(**_args(source / "missing", storage, config_path, plan))
    with pytest.raises(PreflightError, match="storage_root_invalid"):
        run_preflight(**_args(source, storage / "missing", config_path, plan))


def test_report_change_during_preflight_is_detected(tmp_path, monkeypatch):
    _, plan, source, storage, config_path = _setup(tmp_path)
    report = _seed_report(storage, plan)
    _seed_index(storage, plan)
    original = IndexInventoryService.inspect_build_id
    calls = 0

    def change_between_checks(self, *args):
        nonlocal calls
        result = original(self, *args)
        calls += 1
        if calls == 1:
            payload = json.loads(report.read_text(encoding="utf-8"))
            payload["text_records_persisted"] = 7
            report.write_text(json.dumps(payload), encoding="utf-8")
        return result

    monkeypatch.setattr(IndexInventoryService, "inspect_build_id", change_between_checks)
    with pytest.raises(PreflightError, match="report_changed_during_inspection"):
        run_preflight(**_args(source, storage, config_path, plan))


def test_source_change_during_index_observation_is_rejected(tmp_path, monkeypatch):
    _, plan, source, storage, config_path = _setup(tmp_path)
    _seed_report(storage, plan)
    _seed_index(storage, plan)
    original = preflight_module._observe_index
    calls = 0

    def change_after_first_observation(*args):
        nonlocal calls
        result = original(*args)
        calls += 1
        if calls == 1:
            (source / "document.pdf").write_bytes(b"source changed during preflight")
        return result

    monkeypatch.setattr(preflight_module, "_observe_index", change_after_first_observation)
    with pytest.raises(PreflightError, match="manifest_changed"):
        run_preflight(**_args(source, storage, config_path, plan))


def test_symlinked_report_is_rejected_and_target_is_preserved(tmp_path):
    _, plan, source, storage, config_path = _setup(tmp_path)
    report = _seed_report(storage, plan)
    target = report.with_name("real-report.json")
    report.rename(target)
    try:
        report.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Windows symlink creation is unavailable in this environment: {type(exc).__name__}")
    _seed_index(storage, plan)
    with pytest.raises(PreflightError, match="report_path_unsafe"):
        run_preflight(**_args(source, storage, config_path, plan))
    assert target.is_file()


def test_symlinked_index_directory_is_rejected_without_following_it(tmp_path):
    _, plan, source, storage, config_path = _setup(tmp_path)
    _seed_report(storage, plan)
    outside = tmp_path / "outside-index"
    outside.mkdir()
    (outside / "chroma").mkdir()
    expected = _IndexStoreManifest(
        index_fingerprint=plan.index_fingerprint,
        document_manifest_hash=plan.document_manifest_hash,
    )
    (outside / "index-manifest.json").write_text(expected.canonical_json(), encoding="utf-8")
    indexes = storage / "indexes"
    indexes.mkdir()
    link = indexes / plan.index_fingerprint
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Windows symlink creation is unavailable in this environment: {type(exc).__name__}")
    with pytest.raises(PreflightError, match="index_directory_unsafe"):
        run_preflight(**_args(source, storage, config_path, plan))
    assert (outside / "index-manifest.json").is_file()
