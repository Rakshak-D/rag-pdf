from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.diagnostics.artifact_publisher import ArtifactPublishTrace, MAX_EVENTS_PER_THREAD, HarnessGateTimeout, InjectedPublisherFault, exception_provenance
from scripts.diagnose_artifact_publisher import _classify_publisher_evidence, classify_child_result, aggregate_attempt
from backend.experiments.artifact_store import ArtifactStoreError, ExperimentArtifactStore
import backend.experiments.artifact_store as store_module
import json
import threading
import sys


def _failure_payload(errors, events=None):
    items = []
    for index, item in enumerate(errors):
        origin = item["provenance"]
        basis = "builtin_link_explicit_cause" if origin == "natural_publisher_exception_evidence" else "explicit_marker"
        items.append({"index": index, "process_id": 1, "thread_id": index + 1, "observed_order": index, "traceback": "trace",
                      "provenance_basis": basis, **item})
    return {"schema_version": 1, "attempt_id": "test-attempt", "artifact_type": "evaluation_result",
            "process_id": 1, "link_gate_released": True, "gate_release_reason": "peer_failure",
            "errors": items + [None] * (4 - len(items)), "diagnostic_record_errors": [], "worker_alive_after_join": [False] * 4,
            "link_arrivals": 1, "peer_failed": True,
            "worker_outcomes": ["raised"] * len(items) + ["returned"] * (4 - len(items)),
            "events_by_worker": events or {}}


@pytest.mark.parametrize("suppressed", [True, False])
def test_incidental_gate_context_is_not_causal(suppressed):
    try:
        try:
            raise HarnessGateTimeout("gate")
        except HarnessGateTimeout:
            if suppressed:
                raise RuntimeError("independent") from None
            raise RuntimeError("independent")
    except RuntimeError as exc:
        original_tb = exc.__traceback__
        assert isinstance(exc.__context__, HarnessGateTimeout)
        assert exc.__suppress_context__ is suppressed
        assert exception_provenance(exc) == "indeterminate_failure"
        assert exc.__traceback__ is original_tb


def test_unrecognized_cause_and_cycles_do_not_establish_gate_provenance():
    gate = HarnessGateTimeout("gate")
    assert exception_provenance(gate) == "harness_gate_timeout"
    wrapper = RuntimeError("unknown wrapper")
    wrapper.__cause__ = gate
    assert exception_provenance(wrapper) == "indeterminate_failure"
    wrapper.__cause__ = wrapper
    wrapper.__context__ = wrapper
    assert exception_provenance(wrapper) == "indeterminate_failure"


def test_injected_fault_is_identified_directly_and_through_real_publisher(tmp_path, monkeypatch):
    marker = InjectedPublisherFault("deliberate synthetic fault")
    assert exception_provenance(marker) == "injected_fault"
    original = store_module.os
    class Proxy:
        def __getattr__(self, name):
            return getattr(original, name)
        def link(self, *args, **kwargs):
            raise marker
    monkeypatch.setattr(store_module, "os", Proxy())
    with pytest.raises(ArtifactStoreError) as caught:
        ExperimentArtifactStore(tmp_path / "store")._publish("evaluation_result", b"synthetic")
    assert caught.value.__cause__ is marker
    assert marker.__traceback__ is not None
    assert exception_provenance(caught.value) == "injected_fault"
    evidence = tmp_path / "pytest-temp" / "artifact-publisher-diagnostics"
    evidence.mkdir(parents=True)
    (evidence / "failure-evidence.json").write_text(json.dumps(_failure_payload([{"provenance": "injected_fault"}])))
    assert _classify_publisher_evidence(tmp_path)[0] == "injected_fault"


def _trace(tmp_path: Path, payload: bytes = b"synthetic-canonical-payload") -> ArtifactPublishTrace:
    return ArtifactPublishTrace(
        attempt_id="unit-diagnostic-attempt",
        artifact_type="evaluation_result",
        artifact_id=sha256(payload).hexdigest(),
        expected_bytes=payload,
        store_root=tmp_path / "artifacts",
        test_root=tmp_path,
    )


def test_trace_is_bounded_and_writes_nothing_for_successful_observations(tmp_path):
    trace = _trace(tmp_path)
    trace.bind_worker(0)
    for number in range(MAX_EVENTS_PER_THREAD * 3):
        trace.record("query", ordinal=number)

    events = trace.events_by_worker()["0"]
    assert len(events) == MAX_EVENTS_PER_THREAD
    assert events[0]["ordinal"] == MAX_EVENTS_PER_THREAD * 2
    assert events[-1]["ordinal"] == MAX_EVENTS_PER_THREAD * 3 - 1
    assert not (tmp_path / "artifact-publisher-diagnostics").exists()


def test_instrumentation_preserves_arguments_results_and_original_exceptions(tmp_path, monkeypatch):
    trace = _trace(tmp_path)
    trace.bind_worker(0)
    seen = []
    expected_error = RuntimeError("synthetic publisher failure")

    class Store:
        def _destination(self, artifact_type, artifact_id):
            seen.append((artifact_type, artifact_id))
            if artifact_id == "fail":
                raise expected_error
            return (artifact_type, artifact_id)

        def _existing_or_raise(self, path, expected, artifact_type, artifact_id):
            return (path, expected, artifact_type, artifact_id)

        def _publish(self, artifact_type, data):
            return self._destination(artifact_type, data)

    module = SimpleNamespace(
        ExperimentArtifactStore=Store,
        serialize_evaluation_result=lambda value, suffix="": (value, suffix),
        serialize_ablation_result=lambda value: ("ablation", value),
        tempfile=__import__("tempfile"),
    )
    trace.instrument_store_module(monkeypatch, module)

    serialized = module.serialize_evaluation_result("input", suffix="kept")
    assert serialized == ("input", "kept")
    result = Store()._publish("evaluation_result", "ok")
    assert result == ("evaluation_result", "ok")
    assert seen == [("evaluation_result", "ok")]

    with pytest.raises(RuntimeError) as caught:
        Store()._publish("evaluation_result", "fail")
    assert caught.value is expected_error
    assert any(event["stage"] == "publisher_operation_raised" for event in trace.events_by_worker()["0"])
    assert not (tmp_path / "artifact-publisher-diagnostics").exists()


def test_callback_failure_preserves_original_exception(tmp_path, monkeypatch):
    trace = _trace(tmp_path)
    trace.bind_worker(0)
    original_error = ValueError("original publisher error")
    class Store:
        def _destination(self, *args):
            raise original_error
        def _existing_or_raise(self, *args):
            pass
        def _publish(self, *args):
            return self._destination()
    module = SimpleNamespace(ExperimentArtifactStore=Store, serialize_evaluation_result=lambda x: x,
                             serialize_ablation_result=lambda x: x, tempfile=__import__("tempfile"))
    def failed_callback(stage, **details):
        if stage == "publisher_operation_raised":
            raise RuntimeError("callback failure")
    monkeypatch.setattr(trace, "record", failed_callback)
    trace.instrument_store_module(monkeypatch, module)
    with pytest.raises(ValueError) as caught:
        Store()._publish("evaluation_result", b"synthetic")
    assert caught.value is original_error
    assert any(frame.name == "_destination" for frame in __import__("traceback").extract_tb(caught.value.__traceback__))
    assert trace._record_errors == ["RuntimeError", "RuntimeError"]


def test_base_exception_from_diagnostic_callback_does_not_replace_publisher_error(tmp_path, monkeypatch):
    trace = _trace(tmp_path)
    original = ValueError("publisher")
    class Store:
        def _destination(self, *args): raise original
        def _existing_or_raise(self, *args): pass
        def _publish(self, *args): return self._destination()
    module = SimpleNamespace(ExperimentArtifactStore=Store, serialize_evaluation_result=lambda x: x,
                             serialize_ablation_result=lambda x: x, tempfile=__import__("tempfile"))
    def callback(stage, **kwargs):
        if stage == "publisher_operation_raised": raise KeyboardInterrupt("diagnostic")
    monkeypatch.setattr(trace, "record", callback)
    trace.instrument_store_module(monkeypatch, module)
    with pytest.raises(ValueError) as caught:
        Store()._publish("evaluation_result", b"synthetic")
    assert caught.value is original
    assert trace._record_errors == ["KeyboardInterrupt", "KeyboardInterrupt"]


@pytest.mark.parametrize("marker", [HarnessGateTimeout("gate"), InjectedPublisherFault("injected")])
def test_callback_failure_preserves_real_wrapped_publisher_cause(tmp_path, monkeypatch, marker):
    trace = _trace(tmp_path)
    trace.bind_worker(0)
    original_os = store_module.os
    class Proxy:
        def __getattr__(self, name):
            return getattr(original_os, name)
        def link(self, *args, **kwargs):
            raise marker
    monkeypatch.setattr(store_module, "os", Proxy())
    def callback(stage, **details):
        if stage == "publisher_operation_raised":
            raise RuntimeError("callback")
    monkeypatch.setattr(trace, "record", callback)
    trace.instrument_store_module(monkeypatch, store_module)
    with pytest.raises(ArtifactStoreError) as caught:
        ExperimentArtifactStore(tmp_path / "store")._publish("evaluation_result", b"synthetic")
    assert caught.value.__cause__ is marker
    assert marker.__traceback__ is not None
    assert exception_provenance(caught.value) in {"harness_gate_timeout", "injected_fault"}
    assert trace._record_errors == ["RuntimeError"]


def test_successful_operation_retains_diagnostic_callback_error(tmp_path, monkeypatch):
    trace = _trace(tmp_path)
    trace.bind_worker(0)
    class Store:
        def _destination(self, *args): return "destination"
        def _existing_or_raise(self, *args): return None
        def _publish(self, *args): return "success"
    module = SimpleNamespace(ExperimentArtifactStore=Store, serialize_evaluation_result=lambda x: x,
                             serialize_ablation_result=lambda x: x, tempfile=__import__("tempfile"))
    def callback(stage, **details): raise RuntimeError("callback")
    monkeypatch.setattr(trace, "record", callback)
    trace.instrument_store_module(monkeypatch, module)
    assert Store()._publish("evaluation_result", b"synthetic") == "success"
    assert trace._record_errors == ["RuntimeError", "RuntimeError"]
    with pytest.raises(RuntimeError, match="incomplete"):
        trace.write_success_evidence("evaluation", [])


def test_evidence_serialization_failure_is_reported(tmp_path, monkeypatch):
    trace = _trace(tmp_path)
    monkeypatch.setattr("tests.diagnostics.artifact_publisher.json.dumps", lambda *a, **k: (_ for _ in ()).throw(ValueError("encoding")))
    reference, error = trace.write_failure_evidence(errors=[], first_failure_index=None, checkpoints=[], results=[],
        link_arrivals=0, link_gate_released=False, peer_failed=False, worker_alive=[])
    assert reference is None
    assert error == "ValueError"


def test_evidence_callback_failure_preserves_injected_publisher_cause(tmp_path, monkeypatch):
    from tests.unit.test_artifact_store import _publish_concurrently
    trace = _trace(tmp_path)
    def fail_evidence(**kwargs): raise RuntimeError("evidence unavailable")
    monkeypatch.setattr(trace, "write_failure_evidence", fail_evidence)
    def publish(): raise InjectedPublisherFault("controlled injected fault")
    with pytest.raises(AssertionError) as caught:
        _publish_concurrently(monkeypatch, publish, diagnostic_trace=trace)
    assert isinstance(caught.value.__cause__, InjectedPublisherFault)
    assert "evidence_error=RuntimeError" in str(caught.value)
    assert caught.value.__cause__.__traceback__ is not None


def test_base_exception_from_evidence_callback_preserves_publisher_cause(tmp_path, monkeypatch):
    from tests.unit.test_artifact_store import _publish_concurrently
    trace = _trace(tmp_path)
    def fail_evidence(**kwargs): raise KeyboardInterrupt("diagnostic")
    monkeypatch.setattr(trace, "write_failure_evidence", fail_evidence)
    def publish(): raise InjectedPublisherFault("controlled")
    with pytest.raises(AssertionError) as caught:
        _publish_concurrently(monkeypatch, publish, diagnostic_trace=trace)
    assert isinstance(caught.value.__cause__, InjectedPublisherFault)
    assert "evidence_error=KeyboardInterrupt" in str(caught.value)


def test_failure_context_callback_error_is_separate_from_publisher_error(tmp_path, monkeypatch):
    from tests.unit.test_artifact_store import _publish_concurrently
    from scripts.diagnose_artifact_publisher import classify_failure_evidence
    trace = _trace(tmp_path)
    def fail_stage(index): raise RuntimeError("context unavailable")
    monkeypatch.setattr(trace, "current_stage", fail_stage)
    def publish(): raise InjectedPublisherFault("injected")
    with pytest.raises(AssertionError) as caught:
        _publish_concurrently(monkeypatch, publish, diagnostic_trace=trace)
    assert isinstance(caught.value.__cause__, InjectedPublisherFault)
    evidence = json.loads((tmp_path / "artifact-publisher-diagnostics" / "failure-evidence.json").read_text())
    assert "RuntimeError" in evidence["diagnostic_record_errors"]
    assert classify_failure_evidence(evidence) == "indeterminate_failure"


def test_traceback_formatter_failure_preserves_publisher_error(tmp_path, monkeypatch):
    import tests.unit.test_artifact_store as helpers
    trace = _trace(tmp_path)
    def fail_format(): raise RuntimeError("formatter unavailable")
    monkeypatch.setattr(helpers.traceback, "format_exc", fail_format)
    def publish(): raise InjectedPublisherFault("controlled injected fault")
    with pytest.raises(AssertionError) as caught:
        helpers._publish_concurrently(monkeypatch, publish, diagnostic_trace=trace)
    assert isinstance(caught.value.__cause__, InjectedPublisherFault)
    evidence = json.loads((tmp_path / "artifact-publisher-diagnostics" / "failure-evidence.json").read_text())
    assert "RuntimeError" in evidence["diagnostic_record_errors"]
    assert any(item and "InjectedPublisherFault" in item["traceback"] for item in evidence["errors"])


def test_exception_text_failure_preserves_original_and_reports_incomplete_evidence(tmp_path, monkeypatch):
    import tests.unit.test_artifact_store as helpers
    from scripts.diagnose_artifact_publisher import classify_failure_evidence
    trace = _trace(tmp_path)
    class BadCause(Exception):
        def __repr__(self): raise RuntimeError("cause formatting failed")
    def publish():
        try:
            raise BadCause("synthetic")
        except BadCause as cause:
            raise InjectedPublisherFault("injected") from cause
    with pytest.raises(AssertionError) as caught:
        helpers._publish_concurrently(monkeypatch, publish, diagnostic_trace=trace)
    assert isinstance(caught.value.__cause__, InjectedPublisherFault)
    evidence = json.loads((tmp_path / "artifact-publisher-diagnostics" / "failure-evidence.json").read_text())
    assert "RuntimeError" in evidence["diagnostic_record_errors"]
    assert any(item and item["cause"] == "diagnostic text unavailable" for item in evidence["errors"])
    assert classify_failure_evidence(evidence) == "indeterminate_failure"


def test_child_success_contract_requires_complete_consistent_evidence():
    payload = {"schema_version": 1, "attempt_id": "attempt", "scenario": "evaluation", "interpreter_pid": 42,
               "expected_threads": 4, "completed_threads": 4, "thread_outcomes": ["returned"] * 4,
               "artifact_id": "a" * 64, "artifact_integrity_verified": True, "temporary_cleanup_verified": True,
               "evidence_complete": True, "classification": "passed"}
    def classify(value, exit_code=0, timed_out=False, failure_evidence=False):
        return classify_child_result(value, attempt_id="attempt", scenario="evaluation", exit_code=exit_code,
                                     timed_out=timed_out, failure_evidence=failure_evidence)
    assert classify(payload) == "passed"
    assert classify(None) == "indeterminate_failure"
    assert classify(payload, exit_code=1) == "indeterminate_failure"
    assert classify(payload, timed_out=True) == "harness_orchestration_error"
    assert classify(payload, failure_evidence=True) == "indeterminate_failure"
    assert classify({**payload, "completed_threads": 3}) == "indeterminate_failure"
    assert classify({**payload, "schema_version": 2}) == "indeterminate_failure"
    assert classify({**payload, "schema_version": True}) == "indeterminate_failure"
    assert classify(None, exit_code=1) == "harness_orchestration_error"


def test_aggregate_rejects_success_and_failure_records_together():
    base = {"timed_out": False, "output_error": False, "exit_code": 1, "failure_evidence": True,
            "child_record_present": True, "failure_classification": "natural_publisher_exception_evidence",
            "child_classification": "indeterminate_failure"}
    assert aggregate_attempt(**base) == "indeterminate_failure"
    assert aggregate_attempt(**{**base, "child_record_present": False}) == "natural_publisher_exception_evidence"
    assert aggregate_attempt(**{**base, "child_record_present": False, "timed_out": True}) == "harness_orchestration_error"
    assert aggregate_attempt(**{**base, "child_record_present": False, "failure_classification": "passed"}) == "indeterminate_failure"
    assert aggregate_attempt(**{**base, "failure_evidence": False, "child_record_present": False,
                                "exit_code": 0, "child_classification": "passed"}) == "indeterminate_failure"


def test_failure_classifier_requires_natural_boundary_evidence(tmp_path):
    evidence = tmp_path / "pytest-temp" / "artifact-publisher-diagnostics"
    evidence.mkdir(parents=True)
    path = evidence / "failure-evidence.json"
    path.write_text("not json")
    assert _classify_publisher_evidence(tmp_path)[0] != "natural_publisher_exception_evidence"
    path.write_bytes(b"\xff")
    assert _classify_publisher_evidence(tmp_path)[0] == "indeterminate_failure"
    path.write_text(json.dumps({"schema_version": 1, "errors": [{"provenance": "natural_publisher_exception_evidence"}]}))
    assert _classify_publisher_evidence(tmp_path)[0] == "indeterminate_failure"
    malformed = _failure_payload([{"provenance": "harness_gate_timeout"}])
    malformed["errors"].append("unknown")
    path.write_text(json.dumps(malformed))
    assert _classify_publisher_evidence(tmp_path)[0] == "indeterminate_failure"
    malformed = _failure_payload([{"provenance": "harness_gate_timeout"}])
    malformed["gate_release_reason"] = []
    path.write_text(json.dumps(malformed))
    assert _classify_publisher_evidence(tmp_path)[0] == "indeterminate_failure"
    malformed = _failure_payload([{"provenance": "harness_gate_timeout"}])
    malformed["errors"][0]["provenance"] = []
    path.write_text(json.dumps(malformed))
    assert _classify_publisher_evidence(tmp_path)[0] == "indeterminate_failure"
    malformed = _failure_payload([{"provenance": "harness_gate_timeout"}])
    malformed["worker_alive_after_join"] = [0] * 4
    path.write_text(json.dumps(malformed))
    assert _classify_publisher_evidence(tmp_path)[0] == "indeterminate_failure"
    malformed = _failure_payload([{"provenance": "harness_gate_timeout"}])
    malformed["peer_failed"] = False
    path.write_text(json.dumps(malformed))
    assert _classify_publisher_evidence(tmp_path)[0] == "indeterminate_failure"
    error = {"provenance": "natural_publisher_exception_evidence"}
    event = {"stage": "builtin_link_raised", "worker_index": 0, "process_id": 1, "thread_id": 1}
    path.write_text(json.dumps(_failure_payload([error], {"0": [event]})))
    assert _classify_publisher_evidence(tmp_path)[0] == "natural_publisher_exception_evidence"
    contradictory = _failure_payload([error], {"0": [event]})
    contradictory["worker_outcomes"][0] = "returned"
    path.write_text(json.dumps(contradictory))
    assert _classify_publisher_evidence(tmp_path)[0] == "indeterminate_failure"
    path.write_text(json.dumps(_failure_payload([error, {"provenance": "harness_gate_timeout"}], {"0": [event]})))
    assert _classify_publisher_evidence(tmp_path)[0] == "indeterminate_failure"


def test_child_startup_failure_is_orchestration_error(tmp_path, monkeypatch):
    from scripts import diagnose_artifact_publisher as runner
    def fail(*args, **kwargs): raise FileNotFoundError("synthetic startup failure")
    monkeypatch.setattr(runner.subprocess, "Popen", fail)
    result = runner._run_attempt(tmp_path, "evaluation", "unused", 1)
    assert result["classification"] == "harness_orchestration_error"
    assert result["popen_pid"] is None


def test_real_child_timeout_is_reaped_and_not_natural(tmp_path, monkeypatch):
    from scripts import diagnose_artifact_publisher as runner
    monkeypatch.setattr(runner, "CHILD_TIMEOUT_SECONDS", 1)
    result = runner._run_attempt(tmp_path, "evaluation", "unused", 1,
        command_override=[sys.executable, "-c", "import threading; threading.Event().wait()"])
    assert result["classification"] == "harness_orchestration_error"
    assert result["timed_out"] is True
    assert result["exit_code"] is not None
    assert result["actual_pytest_interpreter_pid"] is None


def test_real_child_crash_without_evidence_is_not_natural(tmp_path):
    from scripts import diagnose_artifact_publisher as runner
    result = runner._run_attempt(tmp_path, "evaluation", "unused", 1,
        command_override=[sys.executable, "-c", "raise SystemExit(7)"])
    assert result["classification"] == "harness_orchestration_error"
    assert result["exit_code"] == 7


def test_unavailable_output_directory_is_orchestration_error(tmp_path):
    from scripts import diagnose_artifact_publisher as runner
    blocked = tmp_path / "file"
    blocked.write_text("synthetic")
    result = runner._run_attempt(blocked, "evaluation", "unused", 1)
    assert result["classification"] == "harness_orchestration_error"
    assert result["popen_pid"] is None


def test_success_evidence_write_failure_is_not_success(tmp_path, monkeypatch):
    trace = _trace(tmp_path)
    trace._workers = {index: (index, []) for index in range(4)}
    references = [SimpleNamespace(artifact_id=trace.artifact_id)] * 4
    original_open = Path.open
    def fail_open(path, *args, **kwargs):
        if path.name == "child-result.json":
            raise OSError("synthetic output failure")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", fail_open)
    with pytest.raises(OSError, match="synthetic output failure"):
        trace.write_success_evidence("evaluation", references)


def test_injected_prelink_fault_preserves_peer_outcomes(tmp_path, monkeypatch):
    from tests.unit.test_artifact_store import _publish_concurrently
    trace = _trace(tmp_path)
    all_entered = threading.Barrier(4)
    store = ExperimentArtifactStore(tmp_path / "artifacts")
    def publish():
        all_entered.wait(timeout=5)
        if threading.current_thread().name == "artifact-publisher-0":
            raise InjectedPublisherFault("controlled pre-link fault")
        return store._publish("evaluation_result", b"synthetic")
    with pytest.raises(AssertionError) as caught:
        _publish_concurrently(monkeypatch, publish, diagnostic_trace=trace)
    assert isinstance(caught.value.__cause__, InjectedPublisherFault)
    evidence = json.loads((tmp_path / "artifact-publisher-diagnostics" / "failure-evidence.json").read_text())
    assert evidence["peer_failed"] is True
    assert evidence["gate_release_reason"] == "peer_failure"
    assert evidence["worker_alive_after_join"] == [False] * 4
    assert evidence["errors"][0]["provenance"] == "injected_fault"
    assert len(evidence["worker_outcomes"]) == 4
    assert 0 <= evidence["link_arrivals"] <= 3


def test_injected_fault_and_marked_gate_timeout_are_both_preserved(tmp_path, monkeypatch):
    import tests.unit.test_artifact_store as helpers
    trace = _trace(tmp_path)
    gate_failed = threading.Event()
    entered = threading.Barrier(4)
    store = ExperimentArtifactStore(tmp_path / "artifacts")
    def gate_marker(message):
        gate_failed.set()
        return HarnessGateTimeout(message)
    monkeypatch.setattr(helpers, "HarnessGateTimeout", gate_marker)
    def publish():
        entered.wait(timeout=5)
        if threading.current_thread().name == "artifact-publisher-0":
            assert gate_failed.wait(timeout=5)
            raise InjectedPublisherFault("injected after observed gate timeout")
        return store._publish("evaluation_result", b"synthetic")
    with pytest.raises(AssertionError):
        helpers._publish_concurrently(monkeypatch, publish, diagnostic_trace=trace, gate_timeout=0.05)
    evidence = json.loads((tmp_path / "artifact-publisher-diagnostics" / "failure-evidence.json").read_text())
    origins = {item["provenance"] for item in evidence["errors"] if item is not None}
    assert "harness_gate_timeout" in origins
    assert "injected_fault" in origins
    assert evidence["worker_alive_after_join"] == [False] * 4


def test_prelink_injection_followed_by_gate_timeout_is_not_natural(tmp_path, monkeypatch):
    import tests.unit.test_artifact_store as helpers
    trace = _trace(tmp_path)
    gate_failed = threading.Event()
    entered = threading.Barrier(4)
    store = ExperimentArtifactStore(tmp_path / "artifacts")
    original_stage = trace.current_stage
    def stage(index):
        if index == 0:
            assert gate_failed.wait(timeout=5)
        return original_stage(index)
    monkeypatch.setattr(trace, "current_stage", stage)
    def gate_marker(message):
        gate_failed.set()
        return HarnessGateTimeout(message)
    monkeypatch.setattr(helpers, "HarnessGateTimeout", gate_marker)
    def publish():
        entered.wait(timeout=5)
        if threading.current_thread().name == "artifact-publisher-0":
            raise InjectedPublisherFault("controlled pre-link fault")
        return store._publish("evaluation_result", b"synthetic")
    with pytest.raises(AssertionError):
        helpers._publish_concurrently(monkeypatch, publish, diagnostic_trace=trace, gate_timeout=0.05)
    evidence = json.loads((tmp_path / "artifact-publisher-diagnostics" / "failure-evidence.json").read_text())
    origins = {item["provenance"] for item in evidence["errors"] if item is not None}
    assert {"harness_gate_timeout", "injected_fault"}.issubset(origins)
    from scripts.diagnose_artifact_publisher import classify_failure_evidence
    assert classify_failure_evidence(evidence) == "indeterminate_failure"
    assert evidence["worker_alive_after_join"] == [False] * 4


def test_unmarked_publisher_boundary_failure_with_peer_release_is_indeterminate(tmp_path, monkeypatch):
    from tests.unit.test_artifact_store import _publish_concurrently
    from scripts.diagnose_artifact_publisher import classify_failure_evidence
    trace = _trace(tmp_path)
    entered = threading.Barrier(4)
    store = ExperimentArtifactStore(tmp_path / "artifacts")
    original_destination = store._destination
    def destination(*args):
        if threading.current_thread().name == "artifact-publisher-0":
            raise ArtifactStoreError("unmarked synthetic boundary failure")
        return original_destination(*args)
    monkeypatch.setattr(store, "_destination", destination)
    def publish():
        entered.wait(timeout=5)
        return store._publish("evaluation_result", b"synthetic")
    with pytest.raises(AssertionError) as caught:
        _publish_concurrently(monkeypatch, publish, diagnostic_trace=trace)
    assert isinstance(caught.value.__cause__, ArtifactStoreError)
    evidence = json.loads((tmp_path / "artifact-publisher-diagnostics" / "failure-evidence.json").read_text())
    assert evidence["errors"][0]["provenance"] == "indeterminate_failure"
    assert evidence["peer_failed"] is True
    assert classify_failure_evidence(evidence) == "indeterminate_failure"


def test_isolated_child_publishes_valid_success_evidence(tmp_path):
    from scripts.diagnose_artifact_publisher import _run_attempt
    result = _run_attempt(tmp_path, "evaluation", "tests/unit/test_artifact_store.py::test_concurrent_evaluation_saves_publish_one_complete_canonical_artifact", 1)
    assert result["classification"] == "passed"
    assert result["exit_code"] == 0
    assert isinstance(result["actual_pytest_interpreter_pid"], int)
    assert result["actual_pytest_interpreter_pid"] > 0


def test_failure_evidence_is_bounded_and_observes_only_the_test_owned_artifact(tmp_path):
    payload = b"published synthetic artifact"
    trace = _trace(tmp_path, payload)
    trace.bind_worker(0)
    destination = trace.store_root / "evaluation_result" / trace.artifact_id[:2] / f"{trace.artifact_id}.json"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(payload)

    reference, error = trace.write_failure_evidence(
        errors=[{"index": 0, "type": "SyntheticError", "traceback": "synthetic-only"}],
        first_failure_index=0,
        checkpoints=["publication_entered"],
        results=[None],
        link_arrivals=0,
        link_gate_released=True,
        peer_failed=True,
        worker_alive=[False],
    )

    assert error is None
    assert reference == "artifact-publisher-diagnostics/failure-evidence.json"
    evidence_path = tmp_path / reference
    assert evidence_path.stat().st_size < 2 * 1024 * 1024
    content = evidence_path.read_text(encoding="utf-8")
    assert '"destination_matches_expected_bytes": true' in content
    assert "synthetic-only" in content
    assert str(tmp_path) not in content


def test_trace_rejects_a_store_outside_the_test_owned_root(tmp_path):
    payload = b"synthetic"
    with pytest.raises(ValueError, match="beneath its test-owned root"):
        ArtifactPublishTrace(
            attempt_id="unsafe-root",
            artifact_type="evaluation_result",
            artifact_id=sha256(payload).hexdigest(),
            expected_bytes=payload,
            store_root=tmp_path.parent / "outside-store",
            test_root=tmp_path,
        )


def test_gate_timeout_wrapped_by_real_publisher_is_not_natural(tmp_path, monkeypatch):
    original = store_module.os
    class Proxy:
        def __getattr__(self, name):
            return getattr(original, name)
        def link(self, *args, **kwargs):
            raise HarnessGateTimeout("synthetic gate timeout")
    monkeypatch.setattr(store_module, "os", Proxy())
    with pytest.raises(ArtifactStoreError) as caught:
        ExperimentArtifactStore(tmp_path / "store")._publish("evaluation_result", b"synthetic")
    assert isinstance(caught.value.__cause__, HarnessGateTimeout)
    assert exception_provenance(caught.value) == "harness_gate_timeout"
    evidence = tmp_path / "pytest-temp" / "artifact-publisher-diagnostics"
    evidence.mkdir(parents=True)
    (evidence / "failure-evidence.json").write_text(json.dumps(_failure_payload([{"provenance": exception_provenance(caught.value)}])))
    assert _classify_publisher_evidence(tmp_path)[0] == "harness_gate_timeout"


def test_unknown_timeout_and_mixed_failures_are_indeterminate(tmp_path):
    assert exception_provenance(TimeoutError("filesystem timeout")) == "indeterminate_failure"
    evidence = tmp_path / "pytest-temp" / "artifact-publisher-diagnostics"
    evidence.mkdir(parents=True)
    path = evidence / "failure-evidence.json"
    path.write_text(json.dumps(_failure_payload([
        {"observed_order": 0, "provenance": "harness_gate_timeout"},
        {"observed_order": 1, "provenance": "indeterminate_failure"},
    ])))
    assert _classify_publisher_evidence(tmp_path)[0] == "indeterminate_failure"
