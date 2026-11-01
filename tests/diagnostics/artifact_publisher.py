"""Bounded, opt-in diagnostics for isolated artifact publication tests."""

from __future__ import annotations

from collections import deque
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any


MAX_EVENTS_PER_THREAD = 64
MAX_THREADS = 8
MAX_EVIDENCE_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_SNAPSHOT_BYTES = 8 * 1024 * 1024
MAX_TEMP_ENTRIES = 64
MAX_TRACEBACK_CHARS = 96 * 1024


class HarnessGateTimeout(TimeoutError):
    """Raised exclusively by the test-owned final-link coordination gate."""


class InjectedPublisherFault(OSError):
    """Explicit marker for deliberate test-only publisher fault injection."""


def exception_provenance(exc: BaseException) -> str:
    """Trust only the gate marker and the exact publisher's explicit wrapper."""
    from backend.experiments.artifact_store import ArtifactStoreError

    if type(exc) is HarnessGateTimeout:
        return "harness_gate_timeout"
    if type(exc) is ArtifactStoreError and type(exc.__cause__) is HarnessGateTimeout:
        return "harness_gate_timeout"
    if type(exc) is InjectedPublisherFault:
        return "injected_fault"
    if type(exc) is ArtifactStoreError and type(exc.__cause__) is InjectedPublisherFault:
        return "injected_fault"
    return "indeterminate_failure"


class ArtifactPublishTrace:
    """Keep bounded worker-local events and persist explicit test evidence."""

    def __init__(
        self,
        *,
        attempt_id: str,
        artifact_type: str,
        artifact_id: str,
        expected_bytes: bytes,
        store_root: Path,
        test_root: Path,
    ) -> None:
        if not isinstance(attempt_id, str) or not attempt_id or len(attempt_id) > 96:
            raise ValueError("diagnostic attempt ID is invalid")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", attempt_id):
            raise ValueError("diagnostic attempt ID contains unsafe characters")
        if artifact_type not in {"evaluation_result", "ablation_result"}:
            raise ValueError("diagnostic artifact type is invalid")
        if not isinstance(artifact_id, str) or len(artifact_id) != 64 or any(ch not in "0123456789abcdef" for ch in artifact_id):
            raise ValueError("diagnostic artifact ID is invalid")
        if not isinstance(expected_bytes, bytes) or len(expected_bytes) > MAX_ARTIFACT_SNAPSHOT_BYTES:
            raise ValueError("diagnostic payload exceeds the bounded snapshot size")
        if hashlib.sha256(expected_bytes).hexdigest() != artifact_id:
            raise ValueError("diagnostic payload does not match its artifact ID")
        self.attempt_id = attempt_id
        self.artifact_type = artifact_type
        self.artifact_id = artifact_id
        self.expected_bytes = expected_bytes
        self.store_root = Path(store_root)
        self.test_root = Path(test_root)
        if not self._is_within(self.store_root, self.test_root):
            raise ValueError("diagnostic store must be beneath its test-owned root")
        self._workers: dict[int, tuple[int, deque[dict[str, Any]]]] = {}
        self._stages: dict[int, str] = {}
        self._bind_lock = threading.Lock()
        self._record_errors: list[str] = []

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        try:
            path.resolve(strict=False).relative_to(root.resolve(strict=False))
            return True
        except (OSError, ValueError):
            return False

    def bind_worker(self, worker_index: int) -> None:
        if isinstance(worker_index, bool) or not isinstance(worker_index, int) or not 0 <= worker_index < MAX_THREADS:
            raise ValueError("diagnostic worker index is invalid")
        identity = threading.get_ident()
        with self._bind_lock:
            self._workers[identity] = (worker_index, deque(maxlen=MAX_EVENTS_PER_THREAD))

    def record(self, stage: str, **details: Any) -> None:
        """Record one safe event without allowing instrumentation to affect work."""
        try:
            worker = self._workers.get(threading.get_ident())
            if worker is None:
                return
            worker_index, events = worker
            self._stages[worker_index] = str(stage)[:80]
            event: dict[str, Any] = {
                "attempt_id": self.attempt_id,
                "worker_index": worker_index,
                "process_id": os.getpid(),
                "thread_id": threading.get_ident(),
                "artifact_type": self.artifact_type,
                "artifact_id_prefix": self.artifact_id[:12],
                "stage": str(stage)[:80],
                "monotonic_ns": time.monotonic_ns(),
            }
            for key, value in details.items():
                if isinstance(value, (str, int, float, bool)) or value is None:
                    event[str(key)[:48]] = value if not isinstance(value, str) else value[:240]
            events.append(event)
        except Exception as exc:
            # Never let diagnostic bookkeeping replace a publisher exception.
            try:
                if len(self._record_errors) < 16:
                    self._record_errors.append(type(exc).__name__)
            except Exception:
                pass

    def current_stage(self, worker_index: int) -> str:
        return self._stages.get(worker_index, "stage_unknown")

    def _record_safely(self, stage: str, **details: Any) -> None:
        try:
            self.record(stage, **details)
        except BaseException as exc:
            if len(self._record_errors) < 16:
                self._record_errors.append(type(exc).__name__)

    def instrument_store_module(self, monkeypatch, module) -> None:
        """Wrap only module-local publication boundaries when explicitly enabled."""
        for name in ("serialize_evaluation_result", "serialize_ablation_result"):
            original = getattr(module, name)

            def serializer_wrapper(*args, _original=original, _name=name, **kwargs):
                self._record_safely("serialization_started", operation=_name)
                try:
                    value = _original(*args, **kwargs)
                except BaseException as exc:
                    self._record_safely("serialization_raised", operation=_name, exception_type=type(exc).__name__)
                    raise
                self._record_safely("serialization_completed", operation=_name)
                return value

            monkeypatch.setattr(module, name, serializer_wrapper)

        for name, started, completed in (
            ("_destination", "destination_validation_started", "destination_validated"),
            ("_existing_or_raise", "existing_destination_check_started", "existing_destination_verified"),
            ("_publish", "publish_started", "publication_completed"),
        ):
            original = getattr(module.ExperimentArtifactStore, name)

            def method_wrapper(instance, *args, _original=original, _name=name, _started=started, _completed=completed, **kwargs):
                self._record_safely(_started, operation=_name)
                try:
                    value = _original(instance, *args, **kwargs)
                except BaseException as exc:
                    self._record_safely("publisher_operation_raised", operation=_name, exception_type=type(exc).__name__)
                    raise
                self._record_safely(_completed, operation=_name)
                return value

            monkeypatch.setattr(module.ExperimentArtifactStore, name, method_wrapper)

        original_tempfile = module.tempfile

        class TempfileProxy:
            def __getattr__(self, name):
                return getattr(original_tempfile, name)

            @staticmethod
            def mkstemp(*args, **kwargs):
                self._record_safely("temporary_file_create_started")
                try:
                    result = original_tempfile.mkstemp(*args, **kwargs)
                except BaseException as exc:
                    self._record_safely("temporary_file_create_raised", exception_type=type(exc).__name__)
                    raise
                self._record_safely("temporary_file_created", name=Path(result[1]).name)
                return result

        monkeypatch.setattr(module, "tempfile", TempfileProxy())

    def events_by_worker(self) -> dict[str, list[dict[str, Any]]]:
        return {
            str(worker_index): list(events)
            for worker_index, events in sorted(self._workers.values(), key=lambda item: item[0])
        }

    def write_success_evidence(self, scenario: str, references: list[Any]) -> None:
        """Publish test-only success evidence after the caller's integrity assertions."""
        if scenario not in {"evaluation", "ablation"} or f"{scenario}_result" != self.artifact_type or self._record_errors:
            raise RuntimeError("diagnostic success evidence is incomplete")
        if len(references) != 4 or any(item is None or item.artifact_id != self.artifact_id for item in references):
            raise RuntimeError("publisher references do not agree")
        if (sorted(item[0] for item in self._workers.values()) != [0, 1, 2, 3]
                or any(item != references[0] for item in references)):
            raise RuntimeError("publisher thread results are incomplete")
        if not self._is_within(self.store_root, self.test_root):
            raise RuntimeError("diagnostic root is unsafe")
        root = self.test_root / "artifact-publisher-diagnostics"
        if root.is_symlink() or not self._is_within(root, self.test_root):
            raise RuntimeError("diagnostic evidence root is unsafe")
        root.mkdir(exist_ok=True)
        payload = {"schema_version": 1, "attempt_id": self.attempt_id, "scenario": scenario,
                   "interpreter_pid": os.getpid(), "expected_threads": 4, "completed_threads": 4,
                   "thread_outcomes": ["returned"] * 4, "artifact_id": self.artifact_id,
                   "artifact_integrity_verified": True, "temporary_cleanup_verified": True,
                   "evidence_complete": True, "classification": "passed"}
        with (root / "child-result.json").open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True)

    def _artifact_state(self) -> dict[str, Any]:
        root = self.store_root
        test_root = self.test_root
        if not self._is_within(root, test_root):
            return {"status": "not_inspected_root_outside_test_temp"}
        try:
            resolved_root = root.resolve(strict=False)
            resolved_test_root = test_root.resolve(strict=False)
            relative_root = resolved_root.relative_to(resolved_test_root)
        except (OSError, ValueError):
            return {"status": "not_inspected_root_containment_unverified"}

        destination = root / self.artifact_type / self.artifact_id[:2] / f"{self.artifact_id}.json"
        current = resolved_test_root
        safe_components = True
        try:
            for part in (*relative_root.parts, self.artifact_type, self.artifact_id[:2], f"{self.artifact_id}.json"):
                current = current / part
                if current.is_symlink():
                    safe_components = False
                    break
        except OSError:
            safe_components = False
        if not safe_components:
            return {"status": "unsafe_symlink_component", "store_root_relative": relative_root.as_posix()}

        state: dict[str, Any] = {
            "status": "observed_after_workers_joined",
            "store_root_relative": relative_root.as_posix(),
            "destination_relative": (Path(self.artifact_type) / self.artifact_id[:2] / f"{self.artifact_id}.json").as_posix(),
            "destination_exists": False,
            "destination_is_regular_file": False,
            "destination_size_bytes": None,
            "destination_sha256": None,
            "destination_matches_expected_bytes": False,
            "content_observation": "not_read",
            "temporary_files": [],
            "temporary_files_truncated": False,
        }
        try:
            info = destination.stat(follow_symlinks=False)
        except FileNotFoundError:
            info = None
        except OSError as exc:
            state["destination_stat_error_type"] = type(exc).__name__
            info = None
        if info is not None:
            state["destination_exists"] = True
            state["destination_is_regular_file"] = destination.is_file() and not destination.is_symlink()
            state["destination_size_bytes"] = int(info.st_size)
            if state["destination_is_regular_file"]:
                if info.st_size <= MAX_ARTIFACT_SNAPSHOT_BYTES:
                    try:
                        with destination.open("rb") as stream:
                            data = stream.read(MAX_ARTIFACT_SNAPSHOT_BYTES + 1)
                        if len(data) <= MAX_ARTIFACT_SNAPSHOT_BYTES:
                            state["destination_sha256"] = hashlib.sha256(data).hexdigest()
                            state["destination_matches_expected_bytes"] = data == self.expected_bytes
                            state["content_observation"] = "bounded_post_join_read"
                        else:
                            state["content_observation"] = "size_limit_exceeded_during_read"
                    except OSError as exc:
                        state["content_observation"] = "read_failed"
                        state["destination_read_error_type"] = type(exc).__name__
                else:
                    state["content_observation"] = "artifact_snapshot_size_limit_exceeded"

        parent = destination.parent
        try:
            if parent.is_dir() and not parent.is_symlink():
                inspected = 0
                with os.scandir(parent) as entries:
                    for entry in entries:
                        if inspected >= MAX_TEMP_ENTRIES:
                            state["temporary_files_truncated"] = True
                            break
                        inspected += 1
                        if not entry.name.startswith(".artifact-") or not entry.name.endswith(".tmp"):
                            continue
                        try:
                            entry_stat = entry.stat(follow_symlinks=False)
                            state["temporary_files"].append({
                                "name": entry.name[:160],
                                "is_symlink": entry.is_symlink(),
                                "size_bytes": int(entry_stat.st_size),
                            })
                        except OSError as exc:
                            state["temporary_files"].append({"name": entry.name[:160], "stat_error_type": type(exc).__name__})
        except OSError as exc:
            state["temporary_file_scan_error_type"] = type(exc).__name__
        return state

    def write_failure_evidence(
        self,
        *,
        errors: list[dict[str, Any] | None],
        first_failure_index: int | None,
        checkpoints: list[str],
        results: list[Any],
        link_arrivals: int,
        link_gate_released: bool,
        peer_failed: bool,
        worker_alive: list[bool],
        gate_release_reason: str | None = None,
    ) -> tuple[str | None, str | None]:
        """Persist bounded diagnostics beneath this test's disposable root."""
        if not self._is_within(self.store_root, self.test_root):
            return None, "test_root_containment_unverified"
        try:
            bounded_errors: list[dict[str, Any] | None] = []
            for error in errors[:MAX_THREADS]:
                if error is None:
                    bounded_errors.append(None)
                    continue
                item: dict[str, Any] = {}
                for key in ("index", "process_id", "thread_id", "type", "message", "traceback", "cause", "context", "checkpoint", "operation_stage", "observed_order", "elapsed", "link_arrivals_at_exception_observation", "link_arrivals", "provenance", "provenance_basis"):
                    value = error.get(key)
                    if isinstance(value, str):
                        limit = MAX_TRACEBACK_CHARS if key == "traceback" else 2048
                        item[key] = value[:limit]
                        if len(value) > limit:
                            item[f"{key}_truncated"] = True
                    elif isinstance(value, (int, float, bool)) or value is None:
                        item[key] = value
                bounded_errors.append(item)
            evidence_root = self.test_root / "artifact-publisher-diagnostics"
            if evidence_root.is_symlink() or not self._is_within(evidence_root, self.test_root):
                return None, "evidence_root_unsafe"
            evidence_root.mkdir(exist_ok=True)
            payload = {
                "schema_version": 1,
                "attempt_id": self.attempt_id,
                "process_id": os.getpid(),
                "artifact_type": self.artifact_type,
                "artifact_id_prefix": self.artifact_id[:12],
                "failure_order_semantics": "first_worker_to_enter_exception_capture; simultaneous throw order is not observable",
                "first_failure_worker_index": first_failure_index,
                "worker_stage_at_failure": [
                    None if error is None else error.get("operation_stage")
                    for error in errors[:MAX_THREADS]
                ],
                "link_arrivals": link_arrivals,
                "link_gate_released": link_gate_released,
                "gate_release_reason": gate_release_reason,
                "peer_failed": peer_failed,
                "checkpoints_by_worker": list(checkpoints[:MAX_THREADS]),
                "worker_alive_after_join": list(worker_alive[:MAX_THREADS]),
                "worker_outcomes": [
                    "raised" if error is not None else "returned" if result is not None else "no_result"
                    for error, result in zip(errors[:MAX_THREADS], results[:MAX_THREADS])
                ],
                "errors": bounded_errors,
                "events_by_worker": self.events_by_worker() if not any(worker_alive) else {"status": "not_snapshotted_workers_still_alive"},
                "diagnostic_record_errors": list(self._record_errors[:16]) if not any(worker_alive) else ["not_snapshotted_workers_still_alive"],
                "artifact_state_after_join": self._artifact_state() if not any(worker_alive) else {"status": "not_inspected_workers_still_alive"},
                "catalog_state": "not_in_scenario_direct_artifact_store_concurrency_test",
            }
            encoded = (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
            if len(encoded) > MAX_EVIDENCE_BYTES:
                payload["errors"] = [
                    None if error is None else {
                        "index": error.get("index"),
                        "thread": error.get("thread"),
                        "type": error.get("type"),
                        "message": str(error.get("message", ""))[:512],
                        "checkpoint": error.get("checkpoint"),
                        "observed_order": error.get("observed_order"),
                    }
                    for error in errors[:MAX_THREADS]
                ]
                payload["tracebacks_truncated"] = True
                encoded = (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
            if len(encoded) > MAX_EVIDENCE_BYTES:
                return None, "evidence_size_limit_exceeded"
            target = evidence_root / "failure-evidence.json"
            with target.open("x", encoding="utf-8") as stream:
                stream.write(encoded.decode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            return "artifact-publisher-diagnostics/failure-evidence.json", None
        except Exception as exc:
            return None, type(exc).__name__


__all__ = [
    "MAX_EVENTS_PER_THREAD",
    "ArtifactPublishTrace",
]
