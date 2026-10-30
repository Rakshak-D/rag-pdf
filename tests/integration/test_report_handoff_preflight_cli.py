from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from backend.vectorstore.chroma_store import _IndexStoreManifest
from tests.integration.test_retrieval import make_plan, write_report


def _tree_snapshot(root: Path):
    result = []
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        result.append((path.relative_to(root).as_posix(), info.st_mode, info.st_size,
                       info.st_mtime_ns, path.read_bytes() if path.is_file() else None))
    return tuple(result)


def test_actual_cli_preflights_explicit_report_without_storage_mutation(tmp_path):
    config, plan = make_plan(tmp_path)
    source = tmp_path / "pdfs"
    storage = tmp_path / "storage"
    storage.mkdir()
    config_file = tmp_path / "config.json"
    config_file.write_text(config.model_dump_json(), encoding="utf-8")
    build_id = "d" * 32
    report = write_report(storage, plan, build_id=build_id, text_count=4, image_count=1)
    index_root = storage / "indexes" / plan.index_fingerprint
    (index_root / "chroma").mkdir(parents=True)
    manifest = _IndexStoreManifest(
        index_fingerprint=plan.index_fingerprint,
        document_manifest_hash=plan.document_manifest_hash,
    )
    (index_root / "index-manifest.json").write_text(manifest.canonical_json(), encoding="utf-8")
    before = _tree_snapshot(storage)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/report_handoff_preflight.py",
            "--config", str(config_file),
            "--source", str(source),
            "--storage-root", str(storage),
            "--build-id", build_id,
            "--expected-manifest-hash", plan.document_manifest_hash,
            "--expected-index-fingerprint", plan.index_fingerprint,
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=40,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["result"] == "report_compatible_for_manual_review"
    assert payload["build_id"] == build_id
    assert payload["report_reference"] == f"build-artifacts/{build_id}/build-report.json"
    assert payload["reported_text_records"] == 4
    assert payload["reported_image_records"] == 1
    assert payload["check_level"] == "report" and payload["store_checked"] is False
    assert str(tmp_path) not in result.stdout
    assert before == _tree_snapshot(storage)
    assert report.is_file()
    assert not (storage / ".writer-leases").exists()


def test_actual_cli_returns_bounded_error_without_success_shaped_output(tmp_path):
    config, plan = make_plan(tmp_path)
    source = tmp_path / "pdfs"
    storage = tmp_path / "storage"
    storage.mkdir()
    config_file = tmp_path / "config.json"
    config_file.write_text(config.model_dump_json(), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "scripts/report_handoff_preflight.py",
            "--config", str(config_file),
            "--source", str(source),
            "--storage-root", str(storage),
            "--build-id", "e" * 32,
            "--expected-manifest-hash", plan.document_manifest_hash,
            "--expected-index-fingerprint", plan.index_fingerprint,
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=40,
        check=False,
    )
    assert result.returncode == 3
    assert result.stdout == ""
    assert "error_code: report_missing" in result.stderr
    assert str(tmp_path) not in result.stderr
