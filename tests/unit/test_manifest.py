from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

import backend.engine.manifest as manifest_module
from backend.engine.manifest import (
    DocumentManifest,
    DocumentManifestEntry,
    ManifestMismatchError,
    ManifestSourceChangedError,
    ManifestValidationError,
    build_document_manifest,
    stage_document_snapshot,
    verify_document_manifest,
)
from backend.config import PathContainmentError


def write_pdf_bytes(root: Path, relative: str, content: bytes = b"%PDF-1.4\nfixture") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_manifest_is_stable_and_independent_of_absolute_source_root(tmp_path):
    first_root = tmp_path / "first" / "source"
    second_root = tmp_path / "second" / "source"
    for root in (first_root, second_root):
        write_pdf_bytes(root, "nested/a.pdf", b"same-a")
        write_pdf_bytes(root, "b.pdf", b"same-b")

    first = build_document_manifest(first_root)
    second = build_document_manifest(second_root)
    assert first.content_hash == second.content_hash
    assert first.to_serializable_json() == second.to_serializable_json()
    assert [entry.relative_path for entry in first.entries] == ["b.pdf", "nested/a.pdf"]
    assert all("source" not in entry.relative_path for entry in first.entries)


def test_manifest_records_exact_size_sha_and_stable_document_identity(tmp_path):
    path = write_pdf_bytes(tmp_path, "doc.pdf", b"exact bytes")
    manifest = build_document_manifest(tmp_path)
    entry = manifest.entries[0]
    assert entry.size_bytes == path.stat().st_size
    assert entry.sha256 == hashlib.sha256(b"exact bytes").hexdigest()
    assert entry.document_id.startswith("doc_")
    assert len(entry.document_id) == 68


@pytest.mark.parametrize("operation", ["add", "remove", "rename", "modify"])
def test_manifest_hash_changes_when_corpus_changes(tmp_path, operation):
    write_pdf_bytes(tmp_path, "one.pdf", b"one")
    baseline = build_document_manifest(tmp_path)
    if operation == "add":
        write_pdf_bytes(tmp_path, "two.pdf", b"two")
    elif operation == "remove":
        (tmp_path / "one.pdf").unlink()
    elif operation == "rename":
        (tmp_path / "one.pdf").rename(tmp_path / "renamed.pdf")
    else:
        (tmp_path / "one.pdf").write_bytes(b"changed")
    assert build_document_manifest(tmp_path).content_hash != baseline.content_hash


def test_empty_manifest_is_valid_and_deterministic(tmp_path):
    first = build_document_manifest(tmp_path)
    second = build_document_manifest(tmp_path)
    assert first.entries == ()
    assert first.content_hash == second.content_hash
    assert DocumentManifest.from_serializable_json(first.to_serializable_json()) == first


def test_manifest_validation_rejects_paths_hashes_sizes_ids_and_duplicates():
    valid = {
        "relative_path": "a.pdf",
        "size_bytes": 1,
        "sha256": "a" * 64,
        "document_id": "doc_" + "b" * 64,
    }
    with pytest.raises(ValidationError):
        DocumentManifestEntry(**{**valid, "relative_path": "C:/absolute.pdf"})
    with pytest.raises(ValidationError):
        DocumentManifestEntry(**{**valid, "relative_path": "../escape.pdf"})
    with pytest.raises(ValidationError):
        DocumentManifestEntry(**{**valid, "sha256": "not-a-hash"})
    with pytest.raises(ValidationError):
        DocumentManifestEntry(**{**valid, "size_bytes": -1})
    with pytest.raises(ValidationError):
        DocumentManifestEntry(**{**valid, "document_id": "not-an-id"})

    first = DocumentManifestEntry(**valid)
    with pytest.raises(ValidationError, match="duplicate"):
        DocumentManifest(
            entries=(first, first),
            content_hash="a" * 64,
        )


def test_manifest_serialization_rejects_tampered_content_hash(tmp_path):
    write_pdf_bytes(tmp_path, "a.pdf")
    manifest = build_document_manifest(tmp_path)
    payload = manifest.model_dump(mode="json")
    payload["content_hash"] = "b" * 64
    with pytest.raises(ManifestValidationError, match="content_hash"):
        DocumentManifest.from_serializable_json(__import__("json").dumps(payload))


def test_verification_detects_added_missing_changed_and_renamed_files(tmp_path):
    write_pdf_bytes(tmp_path, "a.pdf", b"a")
    manifest = build_document_manifest(tmp_path)
    verify_document_manifest(tmp_path, manifest)

    (tmp_path / "a.pdf").write_bytes(b"changed")
    with pytest.raises(ManifestMismatchError, match="size|content hash"):
        verify_document_manifest(tmp_path, manifest)

    (tmp_path / "a.pdf").unlink()
    write_pdf_bytes(tmp_path, "b.pdf", b"a")
    with pytest.raises(ManifestMismatchError, match="added PDF|missing PDF"):
        verify_document_manifest(tmp_path, manifest)


def test_staging_preserves_nested_paths_and_reuses_verified_snapshot(tmp_path):
    source = tmp_path / "source"
    write_pdf_bytes(source, "nested/a.pdf", b"nested bytes")
    write_pdf_bytes(source, "b.pdf", b"root bytes")
    manifest = build_document_manifest(source)
    storage = tmp_path / "owned"

    snapshot = stage_document_snapshot(source, manifest, storage)
    assert snapshot == storage / "snapshots" / manifest.content_hash
    assert (snapshot / "nested" / "a.pdf").read_bytes() == b"nested bytes"
    assert (snapshot / "b.pdf").read_bytes() == b"root bytes"
    assert stage_document_snapshot(source, manifest, storage) == snapshot


@pytest.mark.parametrize("layout", ["equal", "storage-child", "source-child"])
def test_snapshot_staging_rejects_overlapping_source_and_storage_roots(tmp_path, layout):
    source = tmp_path / "source"
    write_pdf_bytes(source, "a.pdf", b"source")
    manifest = build_document_manifest(source)
    if layout == "equal":
        storage = source
    elif layout == "storage-child":
        storage = source / "owned"
    else:
        storage = tmp_path / "owned"
        source = storage / "source"
        write_pdf_bytes(source, "a.pdf", b"source")
        manifest = build_document_manifest(source)

    with pytest.raises(PathContainmentError, match="must be disjoint"):
        stage_document_snapshot(source, manifest, storage)
    assert not (storage / "snapshots").exists()


def test_corrupt_existing_snapshot_is_not_overwritten(tmp_path):
    source = tmp_path / "source"
    write_pdf_bytes(source, "a.pdf", b"source")
    manifest = build_document_manifest(source)
    storage = tmp_path / "owned"
    snapshot = stage_document_snapshot(source, manifest, storage)
    (snapshot / "a.pdf").write_bytes(b"corrupt")
    with pytest.raises(ManifestMismatchError):
        stage_document_snapshot(source, manifest, storage)
    assert (snapshot / "a.pdf").read_bytes() == b"corrupt"


def test_staging_failure_does_not_promote_partial_snapshot(tmp_path, monkeypatch):
    source = tmp_path / "source"
    write_pdf_bytes(source, "a.pdf", b"a")
    write_pdf_bytes(source, "nested/b.pdf", b"b")
    manifest = build_document_manifest(source)
    storage = tmp_path / "owned"

    def fail_copy(*args, **kwargs):
        raise ManifestSourceChangedError("simulated source change")

    monkeypatch.setattr(manifest_module, "_copy_verified_file", fail_copy)
    with pytest.raises(ManifestSourceChangedError):
        stage_document_snapshot(source, manifest, storage)
    assert not (storage / "snapshots" / manifest.content_hash).exists()
    assert not list((storage / "snapshots").glob(".*staging-*"))


def test_hashing_detects_source_metadata_change(tmp_path, monkeypatch):
    write_pdf_bytes(tmp_path, "a.pdf", b"a")
    real_signature = manifest_module._signature
    calls = 0

    def changing_signature(path):
        nonlocal calls
        calls += 1
        signature = real_signature(path)
        if calls == 2:
            return replace(signature, mtime_ns=signature.mtime_ns + 1)
        return signature

    monkeypatch.setattr(manifest_module, "_signature", changing_signature)
    with pytest.raises(ManifestSourceChangedError, match="changed while hashing"):
        build_document_manifest(tmp_path)


def test_snapshot_rejects_symlinked_input_directory_when_supported(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (source / "linked").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable in this environment")
    with pytest.raises(ManifestValidationError, match="symlinked directory"):
        build_document_manifest(source)
