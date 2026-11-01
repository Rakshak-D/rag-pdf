"""Opt-in pytest plugin for the two known temporary-store HNSW test paths.

Activate explicitly with ``-p scripts.diagnose_chroma_hnsw_pytest_plugin`` and
``--hnsw-diagnostic-dir <existing-or-new-directory>``. Ordinary pytest runs do
not import or activate this plugin.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from scripts.diagnose_chroma_hnsw import (
    _inventory,
    _runtime_info,
    _save_failure_evidence,
)


_TARGETS = {
    "tests/integration/test_chroma_store.py::test_supplied_vectors_are_persistent_and_modalities_are_separate",
    "tests/integration/test_retrieval.py::test_combined_retrieval_validates_report_and_keeps_modalities_separate",
}
_HNSW_ERROR_TEXT = "Error creating hnsw segment reader: Nothing found on disk"
_MAX_TRACE_EVENTS = 128


def is_known_hnsw_error(error: BaseException) -> bool:
    """Match the known Chroma error through the project's exception wrapper."""
    pending: list[BaseException] = [error]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        if _HNSW_ERROR_TEXT in str(current):
            return True
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return False


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _cache_observation(store: Any) -> dict[str, Any]:
    """Read installed-version internals without changing cache behavior."""
    try:
        from chromadb.api.rust import RustBindingsAPI
        from chromadb.api.shared_system_client import SharedSystemClient

        client = store._client
        system = client._system
        api = client._system.instance(RustBindingsAPI)
        return {
            "status": "observed",
            "value": int(api.hnsw_cache_size),
            "source": "unsupported_read_only_installed_internal",
            "current_system_cached": any(system is item for item in SharedSystemClient._identifier_to_system.values()),
            "cached_system_count": len(SharedSystemClient._identifier_to_system),
            "eviction_observed": "unknown",
        }
    except Exception as exc:
        return {
            "status": "unknown",
            "error_type": type(exc).__name__,
            "eviction_observed": "unknown",
        }


def _system_cached_after_close(client: Any) -> dict[str, Any]:
    try:
        from chromadb.api.shared_system_client import SharedSystemClient

        system = client._system
        return {
            "system_cached": any(system is item for item in SharedSystemClient._identifier_to_system.values()),
            "cached_system_count": len(SharedSystemClient._identifier_to_system),
        }
    except Exception as exc:
        return {"observation_error_type": type(exc).__name__}


def _lease_observation(store: Any, marker_present: bool) -> dict[str, Any]:
    """Distinguish a lease object owned here from a marker with unknown owner state."""
    lease = getattr(store, "_writer_lease", None)
    if lease is None:
        return {
            "marker_present": marker_present,
            "state": "owner_unobserved_active_vs_stale_unknown" if marker_present else "no_marker_observed",
            "source": "marker_presence_only",
        }
    try:
        owner_pid = int(lease._pid)
        owner_thread = int(lease._thread)
        active = bool(lease._active)
        store_claimed = bool(lease._store_claimed)
        current_owner = active and owner_pid == os.getpid() and owner_thread == threading.get_ident()
        return {
            "marker_present": marker_present,
            "state": "active_current_process_thread" if current_owner else "lease_object_owner_mismatch_or_inactive",
            "source": "unsupported_read_only_lease_object_snapshot",
            "lease_active": active,
            "store_claimed": store_claimed,
            "owner_process_id": owner_pid,
            "owner_thread_id": owner_thread,
        }
    except Exception as exc:
        return {
            "marker_present": marker_present,
            "state": "lease_object_present_owner_state_unknown",
            "source": "unsupported_read_only_lease_object_snapshot",
            "observation_error_type": type(exc).__name__,
        }


def _bounded_file_observation(store: Any) -> dict[str, Any]:
    """Stat only Chroma-owned fixture files; never read a live database or index."""
    chroma_root = Path(store.index_root) / "chroma"
    names = {
        "chroma.sqlite3", "chroma.sqlite3-wal", "chroma.sqlite3-shm",
        "chroma.sqlite3-journal", "data_level0.bin", "header.bin",
        "length.bin", "link_lists.bin", "index_metadata.pickle",
    }
    files: list[dict[str, Any]] = []
    directories: set[str] = set()
    visited = 0
    truncated = False
    if not chroma_root.is_dir():
        return {"root_present": False, "files": [], "segment_directory_count": 0, "truncated": False}
    for base, dirs, filenames in os.walk(chroma_root, followlinks=False):
        visited += 1
        if visited > 64:
            truncated = True
            break
        base_path = Path(base)
        dirs[:] = [name for name in dirs if not (base_path / name).is_symlink()]
        for directory in dirs:
            relative_directory = (base_path / directory).relative_to(chroma_root)
            if len(relative_directory.parts) == 1:
                directories.add(relative_directory.as_posix())
        for filename in filenames:
            if filename not in names:
                continue
            path = base_path / filename
            try:
                info = path.stat(follow_symlinks=False)
                files.append({
                    "relative_path": path.relative_to(chroma_root).as_posix(),
                    "size_bytes": info.st_size,
                    "mtime_ns": info.st_mtime_ns,
                    "is_symlink": path.is_symlink(),
                })
            except OSError as exc:
                files.append({
                    "relative_path": path.relative_to(chroma_root).as_posix(),
                    "stat_error_type": type(exc).__name__,
                })
            if len(files) >= 128:
                truncated = True
                break
        if truncated:
            break
    return {
        "root_present": True,
        "files": files,
        "segment_directory_count": len(directories),
        "truncated": truncated,
    }


def collect_test_store_evidence(
    store: Any,
    modality: str,
    *,
    temporary_test_root: Path | None,
    output_root: Path,
    full_traceback: str,
    operation_context: dict[str, Any] | None = None,
) -> tuple[Path | None, list[str]]:
    """Write a post-exception snapshot, inspecting pickle only for owned test data."""
    errors: list[str] = []
    evidence_dir: Path | None = None
    try:
        evidence_dir = output_root / (
            "failure-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex
        )
        evidence_dir.mkdir(exist_ok=False)
    except Exception as exc:
        return None, [f"evidence_directory:{type(exc).__name__}"]

    chroma_root = Path(store.index_root) / "chroma"
    storage_root = Path(store.storage_root)
    synthetic_owned = (
        temporary_test_root is not None
        and _within(storage_root, temporary_test_root)
        and _within(Path(store.index_root), temporary_test_root)
        and _within(chroma_root, temporary_test_root)
    )

    snapshot: dict[str, Any] = {
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "timing": "after_rust_query_exception_before_return_to_test",
        "note": "Filesystem inventory is post-exception and may differ from transient state at the failure instant.",
    }
    if synthetic_owned:
        for key, collector in (("relevant_files", lambda: _inventory(chroma_root)),):
            try:
                snapshot[key] = collector()
            except Exception as exc:
                error_type = type(exc).__name__
                snapshot[key] = {"collection_error_type": error_type}
                errors.append(f"{key}:{error_type}")
        snapshot["sqlite_content_inspection"] = "deferred_while_chroma_owner_process_is_active"
        snapshot["pickle_content_inspection"] = "not_performed_presence_and_size_only"
    else:
        snapshot["filesystem_inspection"] = "not_performed_test_temporary_index_provenance_unavailable"

    collection_name = None
    try:
        collection_name = str(store._collections[modality].name)
    except Exception as exc:
        errors.append(f"collection_identity:{type(exc).__name__}")

    try:
        import chromadb

        chroma_version = chromadb.__version__
    except Exception as exc:
        chroma_version = "unknown"
        errors.append(f"chromadb_version:{type(exc).__name__}")

    bounded_observation = (
        _bounded_file_observation(store)
        if synthetic_owned
        else {"inspection": "not_performed_test_temporary_index_provenance_unavailable"}
    )
    report = {
        "failure_kind": "hnsw_reader_error",
        "test_temporary_index_provenance_verified": synthetic_owned,
        "store_relative_index_path": _relative_store_index_path(store),
        "operation": "ChromaVectorStore._query",
        "collection_modality": modality,
        "collection_name": collection_name,
        "runtime": _runtime_info(chroma_version, None, None),
        "cache_observation": _cache_observation(store),
        "operation_context": operation_context or {},
        "bounded_file_observation": bounded_observation,
        "eviction_observed": "unknown",
        "snapshot": snapshot,
        "diagnostic_collection_errors": errors,
        "traceback_file": "traceback.txt",
    }
    write_errors = _save_failure_evidence(evidence_dir, report, full_traceback)
    errors.extend(write_errors)
    if errors:
        print(
            "Chroma diagnostic collection/write errors (exception types only): " + ", ".join(errors),
            file=sys.stderr,
        )
    return evidence_dir, errors


def _relative_store_index_path(store: Any) -> str | None:
    try:
        return Path(store.index_root).relative_to(Path(store.storage_root)).as_posix()
    except (AttributeError, ValueError, OSError, TypeError):
        return None


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("opt-in Chroma HNSW diagnostics")
    group.addoption(
        "--hnsw-diagnostic-dir",
        action="store",
        default=None,
        metavar="PATH",
        help="explicit output directory required when the diagnostic plugin is enabled",
    )
    group.addoption("--hnsw-attempt-id", action="store", default=None, help="safe identifier for this isolated attempt")
    group.addoption("--hnsw-scenario", action="store", default=None, help="controlled experiment scenario label")


def pytest_configure(config: pytest.Config) -> None:
    raw = config.getoption("--hnsw-diagnostic-dir")
    if not raw:
        raise pytest.UsageError("--hnsw-diagnostic-dir is required when the HNSW diagnostic plugin is enabled")
    output_root = Path(raw).expanduser()
    if output_root.exists() and output_root.is_symlink():
        raise pytest.UsageError("HNSW diagnostic output directory cannot be a symlink")
    try:
        output_root.mkdir(parents=True, exist_ok=True)
        if not output_root.is_dir():
            raise OSError("not a directory")
        output_root = output_root.resolve()
    except OSError as exc:
        raise pytest.UsageError(f"HNSW diagnostic output directory is unavailable ({type(exc).__name__})") from exc
    config._hnsw_diagnostic_output_root = output_root
    config._hnsw_attempt_id = (
        config.getoption("--hnsw-attempt-id")
        or os.environ.get("HNSW_DIAGNOSTIC_ATTEMPT_ID")
        or "attempt-id-unprovided"
    )
    config._hnsw_scenario = (
        config.getoption("--hnsw-scenario")
        or os.environ.get("HNSW_DIAGNOSTIC_SCENARIO")
        or "scenario-unprovided"
    )


def _wrap_query(
    original_query: Any,
    *,
    output_root: Path,
    temporary_test_root: Path | None,
    checkpoint: Any = None,
    failure_context: Any = None,
):
    def diagnostic_query(store: Any, modality: str, query_embedding: Any, n_results: int):
        diagnostic_errors: list[str] = []

        def observe(stage: str, details: dict[str, Any] | None = None) -> None:
            if checkpoint is None:
                return
            try:
                checkpoint(stage, store, details)
            except Exception as diagnostic_error:
                diagnostic_errors.append(f"{stage}:{type(diagnostic_error).__name__}")

        if checkpoint is not None:
            observe(f"query_{modality}_started", {"requested_results": n_results})
        try:
            result = original_query(store, modality, query_embedding, n_results)
            if checkpoint is not None:
                try:
                    returned_count = len(result.get("ids", [[]])[0])
                except (AttributeError, IndexError, TypeError):
                    returned_count = None
                observe(f"query_{modality}_completed", {"returned_count": returned_count})
            return result
        except Exception as error:
            if is_known_hnsw_error(error):
                original_traceback = traceback.format_exc()
                if checkpoint is not None:
                    observe(f"query_{modality}_raised", {
                        "exception_type": type(error).__name__,
                        "exception_message": str(error)[:512],
                    })
                context = {}
                if failure_context is not None:
                    try:
                        context = failure_context(store, modality, error, original_traceback)
                    except Exception as diagnostic_error:
                        diagnostic_errors.append(f"failure_context:{type(diagnostic_error).__name__}")
                if diagnostic_errors:
                    context = {**context, "diagnostic_callback_errors": diagnostic_errors}
                try:
                    evidence_dir, _errors = collect_test_store_evidence(
                        store,
                        modality,
                        temporary_test_root=temporary_test_root,
                        output_root=output_root,
                        full_traceback=original_traceback,
                        operation_context=context,
                    )
                    if evidence_dir is not None:
                        print(f"HNSW test-path evidence saved under: {evidence_dir}", file=sys.stderr)
                except Exception as diagnostic_error:
                    print(
                        "HNSW diagnostic failed without replacing query exception "
                        f"({type(diagnostic_error).__name__})",
                        file=sys.stderr,
                    )
            raise

    return diagnostic_query


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_call(item: pytest.Item):
    normalized_nodeid = item.nodeid.replace("\\", "/")
    target_names = {target.rsplit("::", 1)[-1] for target in _TARGETS}
    if normalized_nodeid not in _TARGETS and item.name not in target_names:
        yield
        return

    from backend.vectorstore.chroma_store import ChromaVectorStore

    original_query = ChromaVectorStore._query
    output_root: Path = item.config._hnsw_diagnostic_output_root
    temporary_test_root = item.funcargs.get("tmp_path")
    safe_temp_root = Path(temporary_test_root) if temporary_test_root is not None else None
    started_ns = time.monotonic_ns()
    events: list[dict[str, Any]] = []
    event_lock = threading.Lock()
    events_truncated = False
    query_state: dict[str, Any] = {
        "ordinal": 0,
        "modality": None,
        "collection_name": None,
        "collection_id": None,
    }

    def append_event(stage: str, store: Any = None, details: dict[str, Any] | None = None) -> dict[str, Any]:
        nonlocal events_truncated
        event: dict[str, Any] = {
            "stage": stage,
            "elapsed_ns": time.monotonic_ns() - started_ns,
            "process_id": os.getpid(),
            "thread_id": threading.get_ident(),
            "attempt_id": getattr(item.config, "_hnsw_attempt_id", "attempt-id-unprovided"),
            "scenario": getattr(item.config, "_hnsw_scenario", "scenario-unprovided"),
        }
        if stage.startswith("query_") and stage.endswith("_started"):
            query_state["ordinal"] += 1
            query_state["modality"] = stage[len("query_"):-len("_started")]
            if store is not None:
                collection = getattr(store, "_collections", {}).get(query_state["modality"])
                query_state["collection_name"] = getattr(collection, "name", None)
                collection_id = getattr(collection, "id", None)
                query_state["collection_id"] = str(collection_id) if collection_id is not None else None
        if query_state["ordinal"]:
            event.update({
                "query_ordinal": query_state["ordinal"],
                "query_modality": query_state["modality"],
                "collection_name": query_state["collection_name"],
                "collection_id": query_state["collection_id"],
            })
        if store is not None:
            try:
                event["fingerprint_prefix"] = store.manifest.index_fingerprint[:12]
                reservation_present = (
                    Path(store.storage_root) / ".writer-leases" / store.manifest.index_fingerprint
                ).is_dir()
                event["reservation_present"] = reservation_present
                event["lease_observation"] = _lease_observation(store, reservation_present)
                event["cache"] = _cache_observation(store)
                safe_store = (
                    safe_temp_root is not None
                    and _within(Path(store.storage_root), safe_temp_root)
                    and _within(Path(store.index_root), safe_temp_root)
                )
                event["files"] = (
                    _bounded_file_observation(store)
                    if safe_store
                    else {"inspection": "not_performed_test_temporary_index_provenance_unavailable"}
                )
                if safe_store:
                    event["index_fingerprint"] = store.manifest.index_fingerprint
            except Exception as exc:
                event["observation_error_type"] = type(exc).__name__
        if details:
            event.update(details)
        with event_lock:
            if len(events) < _MAX_TRACE_EVENTS:
                events.append(event)
            else:
                events_truncated = True
        return event

    def checkpoint(stage: str, store: Any = None, details: dict[str, Any] | None = None) -> None:
        append_event(stage, store, details)

    def failure_context(store: Any, modality: str, error: BaseException, original_traceback: str) -> dict[str, Any]:
        return {
            "last_checkpoint": events[-1]["stage"] if events else None,
            "event_count": len(events),
            "process_id": os.getpid(),
            "thread_id": threading.get_ident(),
            "modality": modality,
            "query_ordinal": query_state["ordinal"],
            "collection_name": query_state["collection_name"],
            "collection_id": query_state["collection_id"],
            "exception_type": type(error).__name__,
            "exception_message": str(error)[:512],
            "traceback": original_traceback,
            "timeline": list(events),
        }

    # The target test creates and populates its store before the first retrieval.
    # These wrappers record only operation boundaries and bounded file metadata.
    original_open_descriptor = ChromaVectorStore.__dict__.get("open")
    original_open_existing_descriptor = ChromaVectorStore.__dict__.get("open_existing")
    original_open = getattr(original_open_descriptor, "__func__", None)
    original_open_existing = getattr(original_open_existing_descriptor, "__func__", None)
    original_count = ChromaVectorStore.count
    original_upsert_text = ChromaVectorStore.upsert_text
    original_upsert_image = ChromaVectorStore.upsert_image
    original_close = ChromaVectorStore.close

    if original_open is not None:
        def tracked_open(cls: Any, *args: Any, **kwargs: Any):
            checkpoint("store_open_started")
            try:
                store = original_open(cls, *args, **kwargs)
            except BaseException as exc:
                checkpoint("store_open_raised", details={"exception_type": type(exc).__name__})
                raise
            checkpoint("store_open_completed", store)
            return store
        ChromaVectorStore.open = classmethod(tracked_open)

    if original_open_existing is not None:
        def tracked_open_existing(cls: Any, *args: Any, **kwargs: Any):
            checkpoint("open_existing_started")
            try:
                store = original_open_existing(cls, *args, **kwargs)
            except BaseException as exc:
                checkpoint("open_existing_raised", details={"exception_type": type(exc).__name__})
                raise
            checkpoint("open_existing_completed", store)
            return store
        ChromaVectorStore.open_existing = classmethod(tracked_open_existing)

    def tracked_count(store: Any, modality: str):
        checkpoint(f"count_{modality}_started", store)
        try:
            count = original_count(store, modality)
        except BaseException as exc:
            checkpoint(f"count_{modality}_raised", store, {"exception_type": type(exc).__name__})
            raise
        checkpoint(f"count_{modality}_completed", store, {"count": count})
        return count

    def tracked_upsert_text(store: Any, ids: Any, *args: Any, **kwargs: Any):
        checkpoint("text_upsert_started", store, {"record_count": len(ids)})
        result = original_upsert_text(store, ids, *args, **kwargs)
        checkpoint("text_upsert_completed", store, {"record_count": len(ids)})
        return result

    def tracked_upsert_image(store: Any, ids: Any, *args: Any, **kwargs: Any):
        checkpoint("image_upsert_started", store, {"record_count": len(ids)})
        result = original_upsert_image(store, ids, *args, **kwargs)
        checkpoint("image_upsert_completed", store, {"record_count": len(ids)})
        return result

    def tracked_close(store: Any):
        checkpoint("store_close_started", store)
        client = getattr(store, "_client", None)
        try:
            return original_close(store)
        finally:
            details = _system_cached_after_close(client) if client is not None else {"client_reference_present": False}
            details["reservation_present"] = (
                Path(store.storage_root) / ".writer-leases" / store.manifest.index_fingerprint
            ).is_dir()
            details["lease_observation"] = _lease_observation(store, details["reservation_present"])
            checkpoint("store_close_completed", details=details)

    ChromaVectorStore.count = tracked_count
    ChromaVectorStore.upsert_text = tracked_upsert_text
    ChromaVectorStore.upsert_image = tracked_upsert_image
    ChromaVectorStore.close = tracked_close
    ChromaVectorStore._query = _wrap_query(
        original_query,
        output_root=output_root,
        temporary_test_root=safe_temp_root,
        checkpoint=checkpoint,
        failure_context=failure_context,
    )
    try:
        yield
    finally:
        ChromaVectorStore.count = original_count
        ChromaVectorStore.upsert_text = original_upsert_text
        ChromaVectorStore.upsert_image = original_upsert_image
        ChromaVectorStore.close = original_close
        if original_open_descriptor is not None:
            ChromaVectorStore.open = original_open_descriptor
        if original_open_existing_descriptor is not None:
            ChromaVectorStore.open_existing = original_open_existing_descriptor
        ChromaVectorStore._query = original_query
        run_id = "target-run-" + uuid.uuid4().hex
        trace_path = output_root / f"{run_id}.json"
        try:
            trace_path.write_text(json.dumps({
                "test_nodeid": item.nodeid,
                "attempt_id": getattr(item.config, "_hnsw_attempt_id", "attempt-id-unprovided"),
                "scenario": getattr(item.config, "_hnsw_scenario", "scenario-unprovided"),
                "target_match": "exact" if normalized_nodeid in _TARGETS else "test_name_fallback",
                "events": events,
                "events_truncated": events_truncated,
            }, sort_keys=True, allow_nan=False), encoding="utf-8")
        except Exception as exc:
            print(f"CHROMA_LIFECYCLE_TRACE_WRITE_FAILED={type(exc).__name__}", file=sys.stderr)
        print("CHROMA_LIFECYCLE_TRACE=" + json.dumps({
            "test_nodeid": item.nodeid,
            "attempt_id": getattr(item.config, "_hnsw_attempt_id", "attempt-id-unprovided"),
            "scenario": getattr(item.config, "_hnsw_scenario", "scenario-unprovided"),
            "target_match": "exact" if normalized_nodeid in _TARGETS else "test_name_fallback",
            "events": events,
            "events_truncated": events_truncated,
        }, sort_keys=True, allow_nan=False))
