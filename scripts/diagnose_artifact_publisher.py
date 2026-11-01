"""Run a fixed-budget, fresh-process artifact-publication diagnostic matrix."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid


MAX_ATTEMPTS_PER_SCENARIO = 4
CHILD_TIMEOUT_SECONDS = 90
MAX_CAPTURE_BYTES = 2 * 1024 * 1024
SCENARIOS = (
    (
        "evaluation",
        "tests/unit/test_artifact_store.py::test_concurrent_evaluation_saves_publish_one_complete_canonical_artifact",
    ),
    (
        "ablation",
        "tests/unit/test_artifact_store.py::test_concurrent_ablation_saves_are_idempotent_and_plan_bound",
    ),
)


def classify_child_result(payload, *, attempt_id: str, scenario: str, exit_code: int | None, timed_out: bool, failure_evidence: bool) -> str:
    """Validate a child success claim; unknown failure provenance is indeterminate."""
    if timed_out:
        return "harness_orchestration_error"
    if not isinstance(payload, dict):
        return "harness_orchestration_error" if exit_code != 0 else "indeterminate_failure"
    required = {"schema_version", "attempt_id", "scenario", "interpreter_pid", "expected_threads",
                "completed_threads", "thread_outcomes", "artifact_id", "artifact_integrity_verified",
                "temporary_cleanup_verified", "evidence_complete", "classification"}
    if set(payload) != required:
        return "indeterminate_failure"
    valid = (type(payload["schema_version"]) is int and payload["schema_version"] == 1 and payload["attempt_id"] == attempt_id
             and payload["scenario"] == scenario and type(payload["interpreter_pid"]) is int
             and payload["interpreter_pid"] > 0 and type(payload["expected_threads"]) is int
             and payload["expected_threads"] == 4 and type(payload["completed_threads"]) is int
             and payload["completed_threads"] == 4 and payload["thread_outcomes"] == ["returned"] * 4
             and isinstance(payload["artifact_id"], str) and re.fullmatch(r"[0-9a-f]{64}", payload["artifact_id"])
             and payload["artifact_integrity_verified"] is True and payload["temporary_cleanup_verified"] is True
             and payload["evidence_complete"] is True and payload["classification"] == "passed")
    return "passed" if valid and exit_code == 0 and not failure_evidence else "indeterminate_failure"


def aggregate_attempt(*, timed_out: bool, output_error: bool, exit_code: int | None,
                      failure_evidence: bool, child_record_present: bool,
                      failure_classification: str, child_classification: str) -> str:
    if timed_out:
        return "harness_orchestration_error"
    if output_error:
        return "indeterminate_failure" if failure_evidence else "harness_orchestration_error"
    if failure_evidence and (child_record_present or exit_code == 0):
        return "indeterminate_failure"
    if failure_evidence:
        allowed = {"harness_gate_timeout", "injected_fault", "natural_publisher_exception_evidence", "indeterminate_failure"}
        return failure_classification if isinstance(failure_classification, str) and failure_classification in allowed else "indeterminate_failure"
    if child_classification == "passed":
        return "passed" if child_record_present and exit_code == 0 else "indeterminate_failure"
    return child_classification if isinstance(child_classification, str) and child_classification in {"harness_orchestration_error", "indeterminate_failure"} else "indeterminate_failure"


def _truncate(data: bytes) -> bytes:
    if len(data) <= MAX_CAPTURE_BYTES:
        return data
    return data[:MAX_CAPTURE_BYTES] + b"\n[diagnostic output truncated at configured limit]\n"


def _has_publisher_evidence(attempt_root: Path) -> bool:
    pytest_root = attempt_root / "pytest-temp"
    return any(path.parent.name == "artifact-publisher-diagnostics" for path in pytest_root.rglob("failure-evidence.json"))


def classify_failure_evidence(payload, *, attempt_id=None, scenario=None) -> str:
    if (not isinstance(payload, dict) or type(payload.get("schema_version")) is not int
            or payload.get("schema_version") != 1 or not isinstance(payload.get("errors"), list)
            or len(payload["errors"]) != 4 or payload.get("diagnostic_record_errors") != []
            or not isinstance(payload.get("worker_alive_after_join"), list)
            or len(payload["worker_alive_after_join"]) != 4
            or any(value is not False for value in payload["worker_alive_after_join"])
            or not isinstance(payload.get("worker_outcomes"), list) or len(payload["worker_outcomes"]) != 4
            or type(payload.get("link_arrivals")) is not int or not 0 <= payload["link_arrivals"] <= 4
            or payload.get("peer_failed") is not True or payload.get("tracebacks_truncated") is True
            or payload.get("link_gate_released") is not True
            or not isinstance(payload.get("gate_release_reason"), str)
            or payload.get("gate_release_reason") not in {"all_arrived", "peer_failure", "cleanup"}
            or type(payload.get("process_id")) is not int or payload["process_id"] <= 0
            or not isinstance(payload.get("attempt_id"), str) or not payload["attempt_id"]
            or not isinstance(payload.get("artifact_type"), str)
            or payload.get("artifact_type") not in {"evaluation_result", "ablation_result"}):
        return "indeterminate_failure"
    if ((payload["gate_release_reason"] == "peer_failure" and payload["peer_failed"] is not True)
            or (payload["gate_release_reason"] == "all_arrived" and payload["link_arrivals"] != 4)
            or payload["gate_release_reason"] == "cleanup"):
        return "indeterminate_failure"
    if attempt_id is not None and payload["attempt_id"] != attempt_id:
        return "indeterminate_failure"
    if scenario is not None and payload["artifact_type"] != f"{scenario}_result":
        return "indeterminate_failure"
    if any(item is not None and not isinstance(item, dict) for item in payload["errors"]):
        return "indeterminate_failure"
    errors = [item for item in payload["errors"] if item is not None]
    if any((item is not None and (item.get("index") != index or payload["worker_outcomes"][index] != "raised"))
           or (item is None and payload["worker_outcomes"][index] != "returned")
           for index, item in enumerate(payload["errors"])):
        return "indeterminate_failure"
    if not errors or any(type(item.get("index")) is not int or not 0 <= item["index"] < 4
                         or type(item.get("process_id")) is not int or item["process_id"] != payload["process_id"]
                         or type(item.get("thread_id")) is not int or item["thread_id"] <= 0
                         or type(item.get("observed_order")) is not int
                         or not isinstance(item.get("traceback"), str) or not item["traceback"]
                         or item.get("traceback_truncated") is True for item in errors):
        return "indeterminate_failure"
    if sorted(item["observed_order"] for item in errors) != list(range(len(errors))):
        return "indeterminate_failure"
    if any(not isinstance(item.get("provenance"), str) for item in errors):
        return "indeterminate_failure"
    origins = {item["provenance"] for item in errors}
    if origins == {"harness_gate_timeout"} and all(item.get("provenance_basis") == "explicit_marker" for item in errors):
        return "harness_gate_timeout"
    if origins == {"injected_fault"} and all(item.get("provenance_basis") == "explicit_marker" for item in errors):
        return "injected_fault"
    if origins == {"natural_publisher_exception_evidence"}:
        events = payload.get("events_by_worker")
        if (isinstance(events, dict) and all(item.get("provenance_basis") == "builtin_link_explicit_cause"
            and isinstance(events.get(str(item["index"])), list)
            and any(isinstance(event, dict) and event.get("stage") == "builtin_link_raised"
                    and event.get("process_id") == item["process_id"] and event.get("thread_id") == item["thread_id"]
                    and event.get("worker_index") == item["index"]
                    for event in events[str(item["index"])]) for item in errors)):
            return "natural_publisher_exception_evidence"
    return "indeterminate_failure"


def _classify_publisher_evidence(attempt_root: Path, *, attempt_id=None, scenario=None) -> tuple[str, str | None]:
    pytest_root = attempt_root / "pytest-temp"
    evidence_paths = [
        path for path in pytest_root.rglob("failure-evidence.json")
        if path.parent.name == "artifact-publisher-diagnostics"
    ]
    if not evidence_paths:
        return "indeterminate_failure", None
    if len(evidence_paths) != 1:
        return "indeterminate_failure", None
    path = evidence_paths[0]
    try:
        if path.stat().st_size > MAX_CAPTURE_BYTES:
            return "indeterminate_failure", path.relative_to(attempt_root).as_posix()
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "indeterminate_failure", path.relative_to(attempt_root).as_posix()
    return classify_failure_evidence(payload, attempt_id=attempt_id, scenario=scenario), path.relative_to(attempt_root).as_posix()


def _run_attempt(base_root: Path, scenario: str, node_id: str, ordinal: int, *, command_override=None) -> dict[str, object]:
    attempt_id = f"{scenario}-{ordinal:02d}-{uuid.uuid4().hex[:12]}"
    attempt_root = base_root / attempt_id
    try:
        attempt_root.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        return {"attempt_id": attempt_id, "scenario": scenario, "ordinal": ordinal,
                "classification": "harness_orchestration_error", "output_error_type": type(exc).__name__,
                "actual_pytest_interpreter_pid": None, "popen_pid": None}
    pytest_root = attempt_root / "pytest-temp"
    stdout_path = attempt_root / "pytest-output.log"
    metadata_path = attempt_root / "attempt.json"
    env = os.environ.copy()
    env["RAG_ARTIFACT_PUBLISH_DIAGNOSTICS"] = "1"
    env["RAG_ARTIFACT_PUBLISH_ATTEMPT"] = attempt_id
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "-q",
        f"--basetemp={pytest_root}",
        node_id,
    ]
    if command_override is not None:
        command = list(command_override)
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            command,
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError as exc:
        result = {"attempt_id": attempt_id, "scenario": scenario, "ordinal": ordinal,
                  "classification": "harness_orchestration_error", "startup_error_type": type(exc).__name__,
                  "actual_pytest_interpreter_pid": None, "popen_pid": None}
        try:
            metadata_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        except OSError as output_error:
            result["output_error_type"] = type(output_error).__name__
        return result
    popen_pid = process.pid
    timed_out = False
    cleanup_error = None
    try:
        output, _ = process.communicate(timeout=CHILD_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        timed_out = True
        if os.name == "nt":
            try:
                terminated = subprocess.run(
                    ["taskkill", "/PID", str(popen_pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    check=False,
                )
                if terminated.returncode != 0:
                    cleanup_error = "taskkill_failed"
                    try:
                        process.terminate()
                    except OSError:
                        cleanup_error = "terminate_failed"
            except (OSError, subprocess.TimeoutExpired):
                cleanup_error = "taskkill_unavailable"
                try:
                    process.terminate()
                except OSError:
                    cleanup_error = "terminate_failed"
        else:
            try:
                process.terminate()
            except OSError:
                cleanup_error = "terminate_failed"
        try:
            output, _ = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                cleanup_error = "kill_failed"
            try:
                output, _ = process.communicate(timeout=10)
            except subprocess.TimeoutExpired as exc:
                cleanup_error = "child_not_reaped"
                output = exc.output or b""
    output = _truncate(output or b"")
    output_error_type = None
    try:
        stdout_path.write_bytes(output)
    except OSError as exc:
        output_error_type = type(exc).__name__
    evidence_found = _has_publisher_evidence(attempt_root)
    return_code = process.returncode
    evidence_classification, evidence_reference = _classify_publisher_evidence(attempt_root, attempt_id=attempt_id, scenario=scenario)
    child_paths = list(pytest_root.rglob("child-result.json"))
    child_payload = None
    if len(child_paths) == 1:
        try:
            if child_paths[0].stat().st_size <= MAX_CAPTURE_BYTES:
                child_payload = json.loads(child_paths[0].read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    child_classification = classify_child_result(child_payload, attempt_id=attempt_id, scenario=scenario,
                                                  exit_code=return_code, timed_out=timed_out,
                                                  failure_evidence=evidence_found)
    result: dict[str, object] = {
        "attempt_id": attempt_id,
        "scenario": scenario,
        "ordinal": ordinal,
        "popen_pid": popen_pid,
        "actual_pytest_interpreter_pid": child_payload.get("interpreter_pid") if child_classification == "passed" else None,
        "pid_note": "Popen PID may identify a Windows launcher; interpreter PID is accepted only from validated child evidence.",
        "exit_code": return_code,
        "timed_out": timed_out,
        "cleanup_error": cleanup_error,
        "publisher_failure_evidence_found": evidence_found,
        "failure_evidence_reference": evidence_reference,
        "output_error_type": output_error_type,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "command": command,
        "attempt_directory": attempt_root.name,
        "classification": aggregate_attempt(timed_out=timed_out, output_error=bool(output_error_type),
            exit_code=return_code, failure_evidence=evidence_found, child_record_present=bool(child_paths),
            failure_classification=evidence_classification, child_classification=child_classification),
    }
    try:
        metadata_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as exc:
        result["output_error_type"] = type(exc).__name__
        result["classification"] = "indeterminate_failure" if evidence_found else "harness_orchestration_error"
    print(
        f"{scenario} attempt {ordinal}: popen_pid={popen_pid} exit={return_code} "
        f"evidence={evidence_found} elapsed={result['elapsed_seconds']}s"
    )
    return result


def main() -> int:
    root = Path(tempfile.gettempdir()) / "rag-pdf-artifact-publisher-diagnostics"
    incident_id = f"incident-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}"
    base_root = root / incident_id
    base_root.mkdir(parents=True, exist_ok=False)
    print(f"Fixed budget: {MAX_ATTEMPTS_PER_SCENARIO} fresh processes per scenario; maximum 8 total.")
    print(f"Incident ID: {incident_id}")
    results: list[dict[str, object]] = []
    stop_all = False
    for scenario, node_id in SCENARIOS:
        scenario_failed = False
        for ordinal in range(1, MAX_ATTEMPTS_PER_SCENARIO + 1):
            result = _run_attempt(base_root, scenario, node_id, ordinal)
            results.append(result)
            classification = result["classification"]
            if classification == "natural_publisher_exception_evidence":
                print(f"Stopping {scenario} scenario after preserving the first publisher exception evidence.")
                scenario_failed = True
                break
            if classification != "passed":
                print("Stopping the matrix because the attempt failed without classifiable publisher evidence.")
                scenario_failed = True
                stop_all = True
                break
        if stop_all:
            break
        if scenario_failed:
            # A natural failure is retained; the other artifact type remains a separate bounded experiment.
            continue
    summary = {
        "incident_id": incident_id,
        "budget_per_scenario": MAX_ATTEMPTS_PER_SCENARIO,
        "maximum_total_attempts": 8,
        "attempts_executed": len(results),
        "results": results,
    }
    (base_root / "matrix-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Matrix attempts executed: {len(results)}. Evidence retained under the incident's temporary directory.")
    return 1 if any(item["classification"] != "passed" for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
