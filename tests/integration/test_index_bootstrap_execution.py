from __future__ import annotations

import json

import pytest

from backend.engine.index_builder import build_index
from scripts.index_bootstrap import BootstrapError, execute_build, main, preview
from tests.integration.test_index_builder import _plan_with_text_and_optional_image, _valid_text_adapter, StubTextAdapter


def test_confirmed_fresh_build_uses_real_chroma_with_fake_embeddings(tmp_path, monkeypatch):
    source, config, _ = _plan_with_text_and_optional_image(tmp_path)
    storage = tmp_path / "owned"
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config.model_dump(mode="json")), encoding="utf-8")
    selected = preview(config_path, source, storage)

    def injected(plan, input_dir, storage_root, *, creation_policy):
        return build_index(plan, input_dir, storage_root, text_adapter=_valid_text_adapter(config), creation_policy=creation_policy)

    monkeypatch.setattr("backend.engine.index_builder.build_index", injected)
    result = execute_build(config_path, source, storage, expected_manifest_hash=selected.plan.document_manifest_hash, expected_index_fingerprint=selected.plan.index_fingerprint)
    assert result.status == "completed" and result.text_records_persisted == 1
    assert result.report_path.is_file()
    original = result.report_path.read_bytes()
    with pytest.raises(BootstrapError, match="index_already_exists"):
        execute_build(config_path, source, storage, expected_manifest_hash=selected.plan.document_manifest_hash, expected_index_fingerprint=selected.plan.index_fingerprint)
    assert result.report_path.read_bytes() == original


def test_confirmation_failures_precede_writes(tmp_path):
    source, config, _ = _plan_with_text_and_optional_image(tmp_path)
    storage = tmp_path / "owned"
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config.model_dump(mode="json")), encoding="utf-8")
    selected = preview(config_path, source, storage)
    with pytest.raises(BootstrapError, match="confirmations_required"):
        execute_build(config_path, source, storage, expected_manifest_hash=None, expected_index_fingerprint=None)
    with pytest.raises(BootstrapError, match="index_fingerprint_changed"):
        execute_build(config_path, source, storage, expected_manifest_hash=selected.plan.document_manifest_hash, expected_index_fingerprint="0" * 64)
    next(source.glob("*.pdf")).write_bytes(b"changed")
    with pytest.raises(BootstrapError, match="manifest_changed"):
        execute_build(config_path, source, storage, expected_manifest_hash=selected.plan.document_manifest_hash, expected_index_fingerprint=selected.plan.index_fingerprint)
    assert not storage.exists()


def test_failed_build_preserves_failure_report_without_claiming_success(tmp_path, monkeypatch):
    source, config, _ = _plan_with_text_and_optional_image(tmp_path)
    storage = tmp_path / "owned"
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config.model_dump(mode="json")), encoding="utf-8")
    selected = preview(config_path, source, storage)

    def injected(plan, input_dir, storage_root, *, creation_policy):
        return build_index(plan, input_dir, storage_root, text_adapter=StubTextAdapter(config, error=RuntimeError("private")), creation_policy=creation_policy)

    monkeypatch.setattr("backend.engine.index_builder.build_index", injected)
    result = execute_build(config_path, source, storage, expected_manifest_hash=selected.plan.document_manifest_hash, expected_index_fingerprint=selected.plan.index_fingerprint)
    assert result.status == "failed" and result.failure_stage == "text_embedding"
    assert result.report_path.is_file() and not (storage / "indexes").exists()


def test_cli_requires_both_confirmations_and_reports_actual_success(tmp_path, monkeypatch, capsys):
    source, config, _ = _plan_with_text_and_optional_image(tmp_path)
    storage = tmp_path / "owned"
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config.model_dump(mode="json")), encoding="utf-8")
    selected = preview(config_path, source, storage)
    args = ["--config", str(config_path), "--source", str(source), "--storage-root", str(storage)]
    assert main(args) == 0 and not storage.exists()
    assert main(args + ["--execute"]) == 2 and not storage.exists()
    assert "confirmations_required" in capsys.readouterr().err

    def injected(plan, input_dir, storage_root, *, creation_policy):
        return build_index(plan, input_dir, storage_root, text_adapter=_valid_text_adapter(config), creation_policy=creation_policy)

    monkeypatch.setattr("backend.engine.index_builder.build_index", injected)
    assert main(args + ["--execute", "--expected-manifest-hash", selected.plan.document_manifest_hash, "--expected-index-fingerprint", selected.plan.index_fingerprint]) == 0
    output = capsys.readouterr().out
    assert "Build status: completed" in output
    assert "Text records: 1" in output
    assert "RAG_WORKBENCH_BUILD_REPORT_PATH" in output
