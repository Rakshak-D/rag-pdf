from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.diagnose_chroma_hnsw_pytest_plugin import (
    _TARGETS,
    _wrap_query,
    collect_test_store_evidence,
    is_known_hnsw_error,
)


def test_known_hnsw_error_is_found_through_project_exception_chain():
    rust_error = RuntimeError("InternalError: Error creating hnsw segment reader: Nothing found on disk")
    wrapped_error = ValueError("Chroma rejected query")
    wrapped_error.__cause__ = rust_error

    assert is_known_hnsw_error(wrapped_error)
    assert not is_known_hnsw_error(RuntimeError("unrelated query failure"))


def test_evidence_collection_refuses_to_inspect_index_outside_test_temp_root(tmp_path):
    class Collection:
        name = "synthetic_text_collection"

    class Store:
        storage_root = tmp_path / "not-the-test-temp-root"
        index_root = storage_root / "indexes" / "index-id"
        _collections = {"text": Collection()}

    output_root = tmp_path / "diagnostics"
    output_root.mkdir()
    evidence_dir, errors = collect_test_store_evidence(
        Store(),
        "text",
        temporary_test_root=tmp_path / "different-test-root",
        output_root=output_root,
        full_traceback="original query traceback\n",
    )

    assert errors == []
    assert evidence_dir is not None
    report = json.loads((evidence_dir / "diagnostic.json").read_text(encoding="utf-8"))
    assert report["test_temporary_index_provenance_verified"] is False
    assert report["snapshot"]["filesystem_inspection"].startswith("not_performed")
    assert (evidence_dir / "traceback.txt").read_text(encoding="utf-8") == "original query traceback\n"
    assert len(list(output_root.iterdir())) == 1


def test_plugin_targets_only_the_two_named_integration_tests():
    assert _TARGETS == {
        "tests/integration/test_chroma_store.py::test_supplied_vectors_are_persistent_and_modalities_are_separate",
        "tests/integration/test_retrieval.py::test_combined_retrieval_validates_report_and_keeps_modalities_separate",
    }


def test_diagnostic_failure_does_not_replace_original_query_exception(tmp_path, monkeypatch):
    original = RuntimeError("Error creating hnsw segment reader: Nothing found on disk")

    def query(*_args):
        raise original

    def fail_diagnostic(*_args, **_kwargs):
        raise OSError("diagnostic output unavailable")

    monkeypatch.setattr(
        "scripts.diagnose_chroma_hnsw_pytest_plugin.collect_test_store_evidence",
        fail_diagnostic,
    )
    wrapped = _wrap_query(query, output_root=tmp_path, temporary_test_root=tmp_path)

    with pytest.raises(RuntimeError) as raised:
        wrapped(object(), "text", (), 1)

    assert raised.value is original


def test_non_hnsw_query_exception_does_not_trigger_diagnostics(tmp_path, monkeypatch):
    original = RuntimeError("unrelated query error")

    def query(*_args):
        raise original

    def unexpected_diagnostic(*_args, **_kwargs):
        raise AssertionError("diagnostics should be restricted to the known HNSW error")

    monkeypatch.setattr(
        "scripts.diagnose_chroma_hnsw_pytest_plugin.collect_test_store_evidence",
        unexpected_diagnostic,
    )
    wrapped = _wrap_query(query, output_root=tmp_path, temporary_test_root=tmp_path)

    with pytest.raises(RuntimeError) as raised:
        wrapped(object(), "text", (), 1)

    assert raised.value is original
