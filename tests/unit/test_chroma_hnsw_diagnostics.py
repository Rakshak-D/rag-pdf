from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.diagnose_chroma_hnsw as diagnostic
from scripts.diagnose_chroma_hnsw_pytest_plugin import (
    _MINIMAL_EVENT_CAPACITY,
    _TARGETS,
    _lease_observation,
    _wrap_query,
    _wrap_query_minimal,
    collect_test_store_evidence,
    is_known_hnsw_error,
    pytest_runtest_call,
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


@pytest.mark.parametrize("failing_callback", ["checkpoint", "failure_context"])
def test_diagnostic_callback_failure_preserves_original_query_exception(
    tmp_path, monkeypatch, failing_callback
):
    import traceback

    import scripts.diagnose_chroma_hnsw_pytest_plugin as plugin

    original = RuntimeError("Error creating hnsw segment reader: Nothing found on disk")

    def query(*_args):
        raise original

    def fail_callback(*_args, **_kwargs):
        raise OSError("diagnostic callback failed")

    monkeypatch.setattr(plugin, "collect_test_store_evidence", lambda *_args, **_kwargs: (None, []))
    wrapped = _wrap_query(
        query,
        output_root=tmp_path,
        temporary_test_root=tmp_path,
        checkpoint=fail_callback if failing_callback == "checkpoint" else None,
        failure_context=fail_callback if failing_callback == "failure_context" else None,
    )

    with pytest.raises(RuntimeError) as raised:
        wrapped(object(), "text", (), 1)

    assert raised.value is original
    formatted = "".join(traceback.format_exception(raised.type, raised.value, raised.tb))
    assert "Error creating hnsw segment reader: Nothing found on disk" in formatted
    assert "in query" in formatted


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


def test_lease_observation_separates_owned_lease_from_owner_unknown_marker():
    import os
    import threading

    owned_lease = SimpleNamespace(
        _pid=os.getpid(),
        _thread=threading.get_ident(),
        _active=True,
        _store_claimed=True,
    )
    owned = _lease_observation(SimpleNamespace(_writer_lease=owned_lease), marker_present=True)
    assert owned == {
        "marker_present": True,
        "state": "active_current_process_thread",
        "source": "unsupported_read_only_lease_object_snapshot",
        "lease_active": True,
        "store_claimed": True,
        "owner_process_id": os.getpid(),
        "owner_thread_id": threading.get_ident(),
    }

    unowned_marker = _lease_observation(SimpleNamespace(_writer_lease=None), marker_present=True)
    assert unowned_marker == {
        "marker_present": True,
        "state": "owner_unobserved_active_vs_stale_unknown",
        "source": "marker_presence_only",
    }


def test_query_wrapper_preserves_original_traceback_and_checkpoint_context(tmp_path, monkeypatch):
    import scripts.diagnose_chroma_hnsw_pytest_plugin as plugin

    original = RuntimeError("Error creating hnsw segment reader: Nothing found on disk")
    events = []
    captured = {}

    def query(*_args):
        raise original

    def checkpoint(stage, _store, details):
        events.append((stage, details))

    def failure_context(_store, modality, error, full_traceback):
        return {
            "modality": modality,
            "exception_type": type(error).__name__,
            "last_checkpoint": events[-1][0],
            "traceback": full_traceback,
        }

    def collect(*_args, **kwargs):
        captured.update(kwargs)
        return tmp_path / "evidence", []

    monkeypatch.setattr(plugin, "collect_test_store_evidence", collect)
    wrapped = _wrap_query(
        query,
        output_root=tmp_path,
        temporary_test_root=tmp_path,
        checkpoint=checkpoint,
        failure_context=failure_context,
    )

    with pytest.raises(RuntimeError) as raised:
        wrapped(object(), "text", (), 1)

    assert raised.value is original
    assert [stage for stage, _ in events] == ["query_text_started", "query_text_raised"]
    context = captured["operation_context"]
    assert context["modality"] == "text"
    assert context["exception_type"] == "RuntimeError"
    assert context["last_checkpoint"] == "query_text_raised"
    assert "RuntimeError: Error creating hnsw segment reader: Nothing found on disk" in context["traceback"]


def test_live_query_failure_snapshot_defers_sqlite_and_pickle_contents(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("live-client diagnostic must not inspect database or pickle contents")

    monkeypatch.setattr(diagnostic, "_sqlite_summary", forbidden)
    monkeypatch.setattr(diagnostic, "_metadata_pickle_summary", forbidden)

    snapshot, errors = diagnostic._safe_snapshot(tmp_path)

    assert errors == []
    assert snapshot["sqlite_content_inspection"] == "deferred_until_client_owner_process_has_exited"
    assert snapshot["pickle_content_inspection"] == "not_performed_presence_and_size_only"
    assert snapshot["inventory"]["files"] == []


def test_target_hook_writes_bounded_trace_and_restores_store_methods(tmp_path, monkeypatch):
    import backend.app.runtime  # noqa: F401 - establishes the normal import order around the package cycle
    from backend.vectorstore.chroma_store import ChromaVectorStore

    original = {
        name: ChromaVectorStore.__dict__[name]
        for name in ("open", "open_existing", "count", "upsert_text", "upsert_image", "close", "_query")
    }
    original_query = lambda _store, _modality, _vector, _n: {"ids": [["fixture-record"]]}
    monkeypatch.setattr(ChromaVectorStore, "_query", original_query)
    item = SimpleNamespace(
        nodeid=next(iter(_TARGETS)),
        config=SimpleNamespace(
            _hnsw_diagnostic_output_root=tmp_path,
            _hnsw_attempt_id="unit-attempt",
            _hnsw_scenario="unit-scenario",
        ),
        funcargs={"tmp_path": tmp_path / "test-owned"},
    )
    (tmp_path / "test-owned").mkdir()

    hook = pytest_runtest_call(item)
    next(hook)
    fingerprint = "a" * 64
    store = SimpleNamespace(
        manifest=SimpleNamespace(index_fingerprint=fingerprint),
        storage_root=tmp_path / "test-owned",
        index_root=tmp_path / "test-owned" / "indexes" / fingerprint,
        _collections={
            "text": SimpleNamespace(name="synthetic_text", id="text-id"),
            "image": SimpleNamespace(name="synthetic_image", id="image-id"),
        },
        _client=SimpleNamespace(_system=object()),
        _writer_lease=None,
    )
    assert ChromaVectorStore._query(store, "text", [], 1) == {"ids": [["fixture-record"]]}
    assert ChromaVectorStore._query(store, "image", [], 1) == {"ids": [["fixture-record"]]}
    with pytest.raises(StopIteration):
        next(hook)

    traces = list(tmp_path.glob("target-run-*.json"))
    assert len(traces) == 1
    trace = json.loads(traces[0].read_text(encoding="utf-8"))
    assert trace["test_nodeid"] == item.nodeid
    assert trace["attempt_id"] == "unit-attempt"
    assert trace["scenario"] == "unit-scenario"
    assert trace["target_match"] == "exact"
    assert trace["events_truncated"] is False
    query_events = [event for event in trace["events"] if event["stage"].startswith("query_")]
    assert [(event["stage"], event["query_ordinal"], event["query_modality"])
            for event in query_events] == [
        ("query_text_started", 1, "text"),
        ("query_text_completed", 1, "text"),
        ("query_image_started", 2, "image"),
        ("query_image_completed", 2, "image"),
    ]
    assert all(event["collection_name"] for event in query_events)
    assert ChromaVectorStore.__dict__["_query"] is original_query
    for name, value in original.items():
        if name != "_query":
            assert ChromaVectorStore.__dict__[name] is value


def _minimal_test_store(tmp_path):
    fingerprint = "a" * 64
    storage = tmp_path / "storage"
    storage.mkdir(exist_ok=True)
    return SimpleNamespace(
        manifest=SimpleNamespace(
            index_fingerprint=fingerprint,
            document_manifest_hash="b" * 64,
        ),
        storage_root=storage,
        index_root=storage / "indexes" / fingerprint,
        _collections={"text": SimpleNamespace(name="text_vectors", id="collection-uuid")},
    )


def test_minimal_query_wrapper_preserves_arguments_result_and_bounded_events(tmp_path):
    output_root = tmp_path / "diagnostics"
    output_root.mkdir()
    store = _minimal_test_store(tmp_path)
    query_embedding = object()
    result = {"ids": [["fixture-record"]]}
    calls = []

    def original_query(received_store, modality, received_embedding, n_results):
        calls.append((received_store, modality, received_embedding, n_results))
        return result

    wrapped, events = _wrap_query_minimal(
        original_query,
        output_root=output_root,
        temporary_test_root=tmp_path,
    )
    for _ in range(_MINIMAL_EVENT_CAPACITY + 4):
        assert wrapped(store, "text", query_embedding, 7) is result

    assert len(calls) == _MINIMAL_EVENT_CAPACITY + 4
    assert all(call == (store, "text", query_embedding, 7) for call in calls)
    assert len(events) == _MINIMAL_EVENT_CAPACITY
    assert [event["query_ordinal"] for event in events] == list(range(5, 5 + _MINIMAL_EVENT_CAPACITY))
    assert all(event["result"] == "returned" for event in events)
    assert all(event["operation"] == "ChromaVectorStore._query" for event in events)
    assert all(event["collection_id"] == "collection-uuid" for event in events)
    assert all(event["thread_id"] is not None and event["process_id"] is not None for event in events)
    assert list(output_root.iterdir()) == []


def test_minimal_success_path_does_not_collect_failure_evidence(tmp_path, monkeypatch):
    import scripts.diagnose_chroma_hnsw_pytest_plugin as plugin

    store = _minimal_test_store(tmp_path)
    output_root = tmp_path / "diagnostics"
    output_root.mkdir()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("failure-only observation ran on a successful query")

    monkeypatch.setattr(plugin, "_minimal_failure_context", forbidden)
    monkeypatch.setattr(plugin, "collect_test_store_evidence", forbidden)
    wrapped, _events = _wrap_query_minimal(
        lambda *_args: {"ids": [["ok"]]},
        output_root=output_root,
        temporary_test_root=tmp_path,
    )

    assert wrapped(store, "text", object(), 1) == {"ids": [["ok"]]}
    assert list(output_root.iterdir()) == []


def test_minimal_query_failure_keeps_exception_and_writes_only_failure_evidence(tmp_path, monkeypatch):
    import traceback

    import scripts.diagnose_chroma_hnsw_pytest_plugin as plugin

    output_root = tmp_path / "diagnostics"
    output_root.mkdir()
    store = _minimal_test_store(tmp_path)
    original = RuntimeError("Error creating hnsw segment reader: Nothing found on disk")
    captured = {}

    def query(*_args):
        raise original

    def capture_evidence(_store, _modality, **kwargs):
        captured.update(kwargs)
        return None, []

    monkeypatch.setattr(plugin, "collect_test_store_evidence", capture_evidence)
    wrapped, events = _wrap_query_minimal(
        query,
        output_root=output_root,
        temporary_test_root=tmp_path,
    )

    with pytest.raises(RuntimeError) as raised:
        wrapped(store, "text", object(), 1)

    assert raised.value is original
    assert "Error creating hnsw segment reader: Nothing found on disk" in captured["full_traceback"]
    context = captured["operation_context"]
    assert context["diagnostic_mode"] == "minimal"
    assert context["failure_observation_timing"] == "after_chroma_query_exception"
    assert context["test_temporary_index_provenance_verified"] is True
    assert context["query_event_buffer_capacity"] == _MINIMAL_EVENT_CAPACITY
    assert context["collection_name"] == "text_vectors"
    assert context["query_event"]["query_ordinal"] == 1
    assert context["query_event"]["collection_id"] == "collection-uuid"
    assert context["query_event"]["result"] == "raised"
    assert list(events) == context["recent_query_events"]
    formatted = "".join(traceback.format_exception(raised.type, raised.value, raised.tb))
    assert "in query" in formatted


def test_minimal_evidence_failure_does_not_replace_original_exception(tmp_path, monkeypatch):
    import scripts.diagnose_chroma_hnsw_pytest_plugin as plugin

    original = RuntimeError("Error creating hnsw segment reader: Nothing found on disk")
    store = _minimal_test_store(tmp_path)
    output_root = tmp_path / "diagnostics"
    output_root.mkdir()

    def query(*_args):
        raise original

    def fail_evidence(*_args, **_kwargs):
        raise OSError("evidence storage unavailable")

    monkeypatch.setattr(plugin, "collect_test_store_evidence", fail_evidence)
    wrapped, _events = _wrap_query_minimal(
        query,
        output_root=output_root,
        temporary_test_root=tmp_path,
    )

    with pytest.raises(RuntimeError) as raised:
        wrapped(store, "text", object(), 1)

    assert raised.value is original


def test_off_hook_does_not_patch_query_method(tmp_path):
    import backend.app.runtime  # noqa: F401 - establishes ordinary package import order
    from backend.vectorstore.chroma_store import ChromaVectorStore

    original_query = ChromaVectorStore.__dict__["_query"]
    item = SimpleNamespace(
        nodeid=next(iter(_TARGETS)),
        name=next(iter(_TARGETS)).rsplit("::", 1)[-1],
        config=SimpleNamespace(_hnsw_diagnostic_mode="off"),
        funcargs={"tmp_path": tmp_path},
    )
    hook = pytest_runtest_call(item)
    next(hook)
    assert ChromaVectorStore.__dict__["_query"] is original_query
    with pytest.raises(StopIteration):
        next(hook)
    assert ChromaVectorStore.__dict__["_query"] is original_query


def test_minimal_hook_restores_query_method_without_success_trace(tmp_path, monkeypatch):
    import backend.app.runtime  # noqa: F401 - establishes ordinary package import order
    from backend.vectorstore.chroma_store import ChromaVectorStore

    result = {"ids": [["record"]]}
    monkeypatch.setattr(ChromaVectorStore, "_query", lambda *_args: result)
    original_query = ChromaVectorStore.__dict__["_query"]
    output_root = tmp_path / "diagnostics"
    output_root.mkdir()
    item = SimpleNamespace(
        nodeid=next(iter(_TARGETS)),
        name=next(iter(_TARGETS)).rsplit("::", 1)[-1],
        config=SimpleNamespace(
            _hnsw_diagnostic_mode="minimal",
            _hnsw_diagnostic_output_root=output_root,
        ),
        funcargs={"tmp_path": tmp_path},
    )
    hook = pytest_runtest_call(item)
    next(hook)
    assert ChromaVectorStore.__dict__["_query"] is not original_query
    store = _minimal_test_store(tmp_path)
    assert ChromaVectorStore._query(store, "text", object(), 1) is result
    with pytest.raises(StopIteration):
        next(hook)
    assert ChromaVectorStore.__dict__["_query"] is original_query
    assert list(output_root.iterdir()) == []
