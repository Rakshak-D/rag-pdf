import traceback
from argparse import Namespace
from pathlib import Path
import json

import pytest

from scripts import chroma_application_compatibility_probe as probe
from scripts.chroma_application_compatibility_probe import (
    OWNERSHIP_MARKER,
    OWNERSHIP_MARKER_VALUE,
    SCHEMA_VERSION,
    TEXT_IDS,
    _all_trials_completed,
    _event,
    _invoke_observed_query,
    _record_query_event,
    _validate_reader_result,
    _validate_worker_root,
    _validate_writer_result,
)


class FakeCollection:
    def __init__(self, response=None, error=None):
        self.response = response or {"ids": [["synthetic-id"]], "distances": [[0.0]]}
        self.error = error
        self.calls = []

    def query(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error is not None:
            raise self.error
        return self.response


def observe(fake, vector, limit, *, ordinal, modality, recorder=None,
            events=None, instrumentation_errors=None, calls=None):
    events = [] if events is None else events
    instrumentation_errors = [] if instrumentation_errors is None else instrumentation_errors
    calls = [] if calls is None else calls
    response = _invoke_observed_query(
        lambda: fake.query(query_embeddings=vector, n_results=limit),
        events=events,
        instrumentation_errors=instrumentation_errors,
        query_calls=calls,
        ordinal=ordinal,
        modality=modality,
        collection_name=f"{modality}_vectors",
        collection_id=f"collection-{modality}",
        n_results=limit,
        recorder=recorder,
    )
    return response, events, instrumentation_errors, calls


def _success_evidence():
    writer_evidence = {
        "index_fingerprint": "a" * 64,
        "run_fingerprint": "b" * 64,
        "document_manifest_hash": "c" * 64,
        "build_id": "build-1",
        "report_sha256": "d" * 64,
        "index_manifest_sha256": "e" * 64,
    }
    calls = []
    requests = []
    for ordinal, modality, ids, distances, n_results in (
        (1, "text", ["text-1", "text-2"], [0.0, 1.0], 2),
        (2, "image", ["image-1"], [0.0], 1),
        (3, "text", ["text-1", "text-2"], [0.0, 1.0], 2),
    ):
        calls.append({
            "ordinal": ordinal,
            "modality": modality,
            "collection_name": f"{modality}_vectors",
            "collection_id": f"collection-{modality}",
            "method_entered": True,
            "completed": True,
            "n_results": n_results,
            "returned_ids": ids,
            "distances": distances,
            "distances_finite": True,
        })
        requests.append({
            "ordinal": ordinal,
            "modality": modality,
            "collection_name": f"{modality}_vectors",
            "collection_id": f"collection-{modality}",
            "store_method_entered": True,
            "store_method_completed": True,
            "n_results": n_results,
        })
    reader = {
        "schema_version": SCHEMA_VERSION,
        "attempt_id": "attempt-1",
        "role": "reader",
        "status": "passed",
        "final_classification": "passed",
        "evidence_complete": True,
        "chroma_version": "1.5.0",
        "pid": 4321,
        "python_version": "3.10.11",
        "writer_process_completed": True,
        "report_identity_validated": True,
        "open_existing_completed": 3,
        "collection_metadata_verified": True,
        "text_count": 2,
        "image_count": 1,
        "lease_released": True,
        "writer_reservation_present_after_retrieval": False,
        "instrumentation_errors": [],
        "query_calls": calls,
        "query_requests": requests,
        "report_checks": [
            {
                "build_id": "build-1",
                "index_fingerprint": writer_evidence["index_fingerprint"],
                "document_manifest_hash": writer_evidence["document_manifest_hash"],
                "text_count": 2,
                "image_count": 1,
                "status": "completed",
            }
            for _ in range(3)
        ],
        "count_observations": [
            {"open_ordinal": ordinal, "modality": modality, "count": count}
            for ordinal in (1, 2, 3)
            for modality, count in (("text", 2), ("image", 1))
        ],
        "close_observations": [
            {"released": True, "reservation_after": False} for _ in range(3)
        ],
        "exclusive_lease_conflicts": [{"error": "writer_lease_busy"} for _ in range(3)],
        "collection_metadata": {
            modality: {
                "name": f"{modality}_vectors",
                "id": f"collection-{modality}",
                "metadata": {
                    "store_schema_version": "1",
                    "index_fingerprint": writer_evidence["index_fingerprint"],
                    "document_manifest_hash": writer_evidence["document_manifest_hash"],
                    "index_schema_version": "1",
                    "modality": modality,
                    "collection_name": f"{modality}_vectors",
                    "hnsw:space": "cosine",
                },
            }
            for modality in ("text", "image")
        },
        "index_fingerprint": writer_evidence["index_fingerprint"],
        "run_fingerprint": writer_evidence["run_fingerprint"],
        "document_manifest_hash": writer_evidence["document_manifest_hash"],
        "build_id": writer_evidence["build_id"],
        "network_attempts": [],
        "report_sha256_before": writer_evidence["report_sha256"],
        "report_sha256_after": writer_evidence["report_sha256"],
        "index_manifest_sha256_before": writer_evidence["index_manifest_sha256"],
        "index_manifest_sha256_after": writer_evidence["index_manifest_sha256"],
    }
    writer_process = {"worker_result": writer_evidence, "exit_code": 0, "timed_out": False}
    reader_process = {
        "python_pid": 4321,
        "exit_code": 0,
        "timed_out": False,
        "worker_failure": None,
    }
    return reader, writer_process, reader_process


def _writer_result(attempt_id="attempt-1", version="1.5.0", pid=1234):
    return {
        "schema_version": SCHEMA_VERSION,
        "attempt_id": attempt_id,
        "role": "writer",
        "status": "passed",
        "final_classification": "passed",
        "evidence_complete": True,
        "writer_completed": True,
        "lease_released": True,
        "chroma_version": version,
        "pid": pid,
        "python_version": "3.10.11",
        "reported_text_records": 2,
        "reported_image_records": 1,
        "writer_reservation_present_after_close": False,
        "fresh_build_marker_present_after_finalize": False,
        "report_sha256": "d" * 64,
        "index_manifest_sha256": "e" * 64,
        "instrumentation_errors": [],
        "network_attempts": [],
    }


def test_text_public_query_is_called_once_with_unchanged_arguments_and_result():
    vector = object()
    response_value = {"ids": [["text-1"]], "distances": [[0.0]]}
    fake = FakeCollection(response=response_value)

    response, events, errors, calls = observe(
        fake, vector, 2, ordinal=1, modality="text"
    )

    assert len(fake.calls) == 1
    assert fake.calls[0] == ((), {"query_embeddings": vector, "n_results": 2})
    assert fake.calls[0][1]["query_embeddings"] is vector
    assert response is response_value
    assert calls[0]["method_entered"] is True
    assert calls[0]["completed"] is True
    assert [event["stage"] for event in events] == ["query_started", "query_completed"]
    assert errors == []


def test_image_public_query_is_called_once_and_records_completion():
    vector = object()
    fake = FakeCollection(response={"ids": [["image-1"]], "distances": [[0.0]]})

    response, events, errors, calls = observe(
        fake, vector, 1, ordinal=1, modality="image"
    )

    assert len(fake.calls) == 1
    assert fake.calls[0][1]["query_embeddings"] is vector
    assert response["ids"] == [["image-1"]]
    assert calls[0]["modality"] == "image"
    assert calls[0]["completed"] is True
    assert events[-1]["stage"] == "query_completed"
    assert errors == []


def test_repeated_text_requests_each_call_the_public_query():
    vector = object()
    fake = FakeCollection(response={"ids": [["text-1"]], "distances": [[0.0]]})
    all_events = []
    all_errors = []
    all_calls = []

    for ordinal in (1, 2, 3):
        observe(fake, vector, 2, ordinal=ordinal, modality="text",
                events=all_events, instrumentation_errors=all_errors, calls=all_calls)

    assert len(fake.calls) == 3
    assert [call["ordinal"] for call in all_calls] == [1, 2, 3]
    assert [call["modality"] for call in all_calls] == ["text"] * 3
    assert all(call["completed"] for call in all_calls)
    assert len([event for event in all_events if event["stage"] == "query_completed"]) == 3
    assert all_errors == []


def test_duplicate_stage_regression_keeps_envelope_stage_authoritative():
    events = []
    observation = {"ordinal": 1, "stage": "from-payload", "pid": 999, "modality": "text"}

    _record_query_event(events, "query_started", observation)

    assert events[0]["stage"] == "query_started"
    assert events[0]["pid"] != 999
    assert events[0]["modality"] == "text"
    with pytest.raises(TypeError, match="multiple values for argument 'stage'"):
        _event(events, "query_started", **observation)


def test_underlying_query_exception_identity_cause_and_traceback_are_preserved():
    vector = object()
    cause = ValueError("synthetic root cause")
    original = RuntimeError("synthetic query failure")

    class RaisingCollection(FakeCollection):
        def query(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            raise original from cause

    fake = RaisingCollection()
    events = []
    errors = []
    calls = []
    with pytest.raises(RuntimeError) as caught:
        observe(fake, vector, 2, ordinal=1, modality="text", events=events,
                instrumentation_errors=errors, calls=calls)

    assert caught.value is original
    assert caught.value.__cause__ is cause
    assert caught.value.__traceback__ is not None
    assert "query" in "\n".join(frame.name for frame in traceback.extract_tb(caught.value.__traceback__))
    assert len(fake.calls) == 1
    assert calls[0]["method_entered"] is True
    assert calls[0]["completed"] is False
    assert calls[0]["exception"]["type"] == "RuntimeError"
    assert [event["stage"] for event in events] == ["query_started", "query_failed"]
    assert errors == []


def test_observer_callback_failure_does_not_prevent_or_replace_query():
    vector = object()
    response_value = {"ids": [["text-1"]], "distances": [[0.0]]}
    fake = FakeCollection(response=response_value)

    def broken_recorder(*_args):
        raise RuntimeError("observer callback failed")

    response, _events, errors, calls = observe(
        fake, vector, 2, ordinal=1, modality="text", recorder=broken_recorder
    )

    assert len(fake.calls) == 1
    assert response is response_value
    assert calls[0]["completed"] is True
    assert errors
    assert all(item["origin"] == "instrumentation" for item in errors)
    evidence, writer, process = _success_evidence()
    evidence["instrumentation_errors"] = errors
    assert not _validate_reader_result(
        evidence, attempt_id="attempt-1", chroma_version="1.5.0",
        writer_process=writer, reader_process=process,
    )[0]


def test_observer_failure_does_not_replace_query_failure():
    vector = object()
    original = RuntimeError("underlying query failure")
    fake = FakeCollection(error=original)

    def broken_recorder(*_args):
        raise OSError("event sink unavailable")

    events = []
    errors = []
    calls = []
    with pytest.raises(RuntimeError) as caught:
        observe(fake, vector, 2, ordinal=1, modality="text", recorder=broken_recorder,
                events=events, instrumentation_errors=errors, calls=calls)

    assert caught.value is original
    assert len(fake.calls) == 1
    assert calls[0]["method_entered"] is True and calls[0]["completed"] is False
    assert errors and all(item["origin"] == "instrumentation" for item in errors)
    assert "query_completed" not in [event["stage"] for event in events]


def test_child_success_requires_actual_query_calls_and_complete_result_evidence():
    evidence, writer, reader = _success_evidence()
    assert _validate_reader_result(
        evidence, attempt_id="attempt-1", chroma_version="1.5.0",
        writer_process=writer, reader_process=reader,
    ) == (True, "passed")

    missing_calls = {**evidence, "query_calls": []}
    assert not _validate_reader_result(
        missing_calls, attempt_id="attempt-1", chroma_version="1.5.0",
        writer_process=writer, reader_process=reader,
    )[0]

    contradictory = {**evidence, "query_calls": [dict(call) for call in evidence["query_calls"]]}
    contradictory["query_calls"][0]["method_entered"] = False
    assert not _validate_reader_result(
        contradictory, attempt_id="attempt-1", chroma_version="1.5.0",
        writer_process=writer, reader_process=reader,
    )[0]

    malformed = {**evidence, "query_calls": [None]}
    assert not _validate_reader_result(
        malformed, attempt_id="attempt-1", chroma_version="1.5.0",
        writer_process=writer, reader_process=reader,
    )[0]

    wrong_collection = {**evidence, "query_calls": [dict(call) for call in evidence["query_calls"]]}
    wrong_collection["query_calls"][0]["collection_id"] = "another-collection"
    assert not _validate_reader_result(
        wrong_collection, attempt_id="attempt-1", chroma_version="1.5.0",
        writer_process=writer, reader_process=reader,
    )[0]

    active_lease = {**evidence, "writer_reservation_present_after_retrieval": True}
    assert not _validate_reader_result(
        active_lease, attempt_id="attempt-1", chroma_version="1.5.0",
        writer_process=writer, reader_process=reader,
    )[0]


def test_child_exit_zero_does_not_override_missing_query_evidence():
    evidence, writer, reader = _success_evidence()
    reader["exit_code"] = 0
    evidence.pop("query_calls")
    assert not _validate_reader_result(
        evidence, attempt_id="attempt-1", chroma_version="1.5.0",
        writer_process=writer, reader_process=reader,
    )[0]


def test_incomplete_attempt_matrix_cannot_be_aggregated_as_success():
    assert _all_trials_completed([
        {"completed": True, "classification": "A"},
        {"completed": True, "classification": "A"},
    ])
    assert not _all_trials_completed([])
    assert not _all_trials_completed([
        {"completed": True, "classification": "A"},
        {"completed": False, "classification": "C"},
    ])
    assert not _all_trials_completed([{"completed": True}, {}])
    assert not _all_trials_completed([{"completed": True, "classification": "C"}])


def test_writer_result_requires_attempt_identity_and_exit_evidence():
    result = _writer_result()
    process = {
        "python_pid": result["pid"],
        "exit_code": 0,
        "timed_out": False,
        "worker_failure": None,
    }
    assert _validate_writer_result(
        result, attempt_id="attempt-1", chroma_version="1.5.0", process=process
    ) == (True, "passed")
    assert not _validate_writer_result(
        {**result, "attempt_id": "stale-attempt"}, attempt_id="attempt-1",
        chroma_version="1.5.0", process=process,
    )[0]
    assert not _validate_writer_result(
        result, attempt_id="attempt-1", chroma_version="1.5.0",
        process={**process, "exit_code": 1},
    )[0]


def test_reader_parser_rejects_wrong_attempt_and_contradictory_process_evidence():
    evidence, writer, reader = _success_evidence()
    assert not _validate_reader_result(
        evidence, attempt_id="other-attempt", chroma_version="1.5.0",
        writer_process=writer, reader_process=reader,
    )[0]
    assert not _validate_reader_result(
        evidence, attempt_id="attempt-1", chroma_version="1.5.0",
        writer_process=writer, reader_process={**reader, "worker_failure": {"status": "failed"}},
    )[0]


def test_worker_root_requires_attempt_bound_temporary_marker(tmp_path):
    root = tmp_path / "owned"
    root.mkdir()
    attempt_id = "attempt-safe"
    version = "1.5.0"
    (root / OWNERSHIP_MARKER).write_text(
        OWNERSHIP_MARKER_VALUE + attempt_id + "\n" + version + "\n", encoding="ascii"
    )
    project_root = Path(probe.__file__).resolve().parents[1]

    assert _validate_worker_root(root, project_root, attempt_id, version)[0] == root.resolve()
    with pytest.raises(RuntimeError, match="ownership marker"):
        _validate_worker_root(root, project_root, "other-attempt", version)


def test_invalid_observer_evidence_makes_parent_runner_exit_nonzero(monkeypatch, tmp_path, capsys):
    baseline = tmp_path / "baseline-python.exe"
    candidate = tmp_path / "candidate-python.exe"
    baseline.write_bytes(b"synthetic path placeholder")
    candidate.write_bytes(b"synthetic path placeholder")
    output = tmp_path / "matrix-output"
    monkeypatch.setattr(probe, "_interpreter_identity", lambda *_args: {"pip_check": "passed"})

    def fake_run_child(interpreter, mode, root, project_root, version, attempt_id):
        if mode == "writer":
            result = _writer_result(attempt_id, version, pid=1234)
            probe._json_write(root / "writer-result.json", result)
            return {
                "mode": mode, "launcher_pid": 1200, "python_pid": 1234,
                "exit_code": 0, "timed_out": False,
                "worker_result": result, "worker_failure": None,
            }
        failure = {
            "schema_version": SCHEMA_VERSION,
            "attempt_id": attempt_id,
            "role": "reader",
            "status": "failed",
            "final_classification": "instrumentation_invalid",
            "chroma_version": version,
            "pid": 2345,
            "python_version": "3.10.11",
            "traceback": "synthetic observer failure",
            "query_calls": [],
            "instrumentation_errors": [{"origin": "instrumentation"}],
        }
        return {
            "mode": mode, "launcher_pid": 2300, "python_pid": 2345,
            "exit_code": 1, "timed_out": False,
            "worker_result": None, "worker_failure": failure,
        }

    monkeypatch.setattr(probe, "_run_child", fake_run_child)
    exit_code = probe._run(Namespace(
        baseline_python=str(baseline),
        candidate_python=str(candidate),
        output_root=str(output),
    ))

    summary = json.loads((output / "matrix-summary.json").read_text(encoding="utf-8"))
    assert exit_code != 0
    assert summary["final_classification"] == "C"
    assert summary["confirmation_stopped_early"] is True
    assert len(summary["trials"]) == 1
    assert summary["trials"][0]["classification"] == "C"
    assert "1.5.9" not in [trial["version"] for trial in summary["trials"]]
    assert capsys.readouterr().out
