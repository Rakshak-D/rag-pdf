from __future__ import annotations

import json

import pytest

from backend.config.schema import ResolvedRunConfig
from notebooks.config import Config
from scripts.index_bootstrap import BootstrapError, main, preview


def inputs(tmp_path):
    source = tmp_path / "pdfs"
    source.mkdir()
    (source / "document.pdf").write_bytes(b"synthetic PDF")
    storage = tmp_path / "index"
    config = ResolvedRunConfig.from_legacy(Config(pdf_dir=str(source), reset_collections_on_start=False))
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config.model_dump(mode="json")), encoding="utf-8")
    return path, source, storage


def test_preview_is_deterministic_and_performs_no_store_or_model_work(tmp_path, monkeypatch):
    path, source, storage = inputs(tmp_path)
    monkeypatch.setattr("chromadb.PersistentClient", lambda *_a, **_k: pytest.fail("unexpected Chroma client"))
    monkeypatch.setattr("backend.embeddings.text.TextEmbeddingAdapter.load", lambda *_a, **_k: pytest.fail("unexpected model load"))
    first = preview(path, source, storage)
    second = preview(path, source, storage)
    assert first == second and first.document_count == 1 and not first.index_exists
    assert not storage.exists()


@pytest.mark.parametrize("content", ['{"x":1,"x":2}', '{"value":NaN}', '[]', '{}'])
def test_invalid_or_incomplete_config_is_rejected_without_writes(tmp_path, content):
    path, source, storage = inputs(tmp_path)
    path.write_text(content, encoding="utf-8")
    with pytest.raises(BootstrapError):
        preview(path, source, storage)
    assert not storage.exists()


def test_existing_index_and_unconfirmed_execution_are_safe(tmp_path, capsys):
    path, source, storage = inputs(tmp_path)
    result = preview(path, source, storage)
    index = storage / "indexes" / result.plan.index_fingerprint
    index.mkdir(parents=True)
    sentinel = index / "existing"
    sentinel.write_bytes(b"preserve")
    assert preview(path, source, storage).index_exists
    exit_code = main(["--config", str(path), "--source", str(source), "--storage-root", str(storage), "--execute"])
    assert exit_code == 2
    assert "confirmations_required" in capsys.readouterr().err
    assert sentinel.read_bytes() == b"preserve"


def test_empty_source_and_overlapping_storage_are_rejected(tmp_path):
    path, source, storage = inputs(tmp_path)
    with pytest.raises(BootstrapError):
        preview(path, source, source / "storage")
    (source / "document.pdf").unlink()
    with pytest.raises(BootstrapError, match="manifest_empty"):
        preview(path, source, storage)


def test_source_change_changes_plan_identity(tmp_path):
    path, source, storage = inputs(tmp_path)
    first = preview(path, source, storage)
    (source / "document.pdf").write_bytes(b"changed source")
    second = preview(path, source, storage)
    assert first.plan.document_manifest_hash != second.plan.document_manifest_hash
    assert first.plan.index_fingerprint != second.plan.index_fingerprint
    assert not storage.exists()


def test_destructive_settings_are_rejected(tmp_path):
    path, source, storage = inputs(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["reset_collections_on_start"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BootstrapError, match="destructive_configuration_forbidden"):
        preview(path, source, storage)
    assert not storage.exists()


@pytest.mark.parametrize("change", [{"text_k": "5"}, {"workbench_policy": {}}])
def test_configuration_is_complete_and_not_coerced(tmp_path, change):
    path, source, storage = inputs(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(change)
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BootstrapError):
        preview(path, source, storage)
    assert not storage.exists()
