r"""Opt-in synthetic Chroma HNSW query diagnostic.

Run with the repository environment, for example:
    .\rag-env\Scripts\python.exe scripts\diagnose_chroma_hnsw.py --runs 5

No project index, corpus, model, or network service is used. A failed probe
preserves its temporary directory and writes failure-time diagnostics there.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_NATIVE_HNSW_FILES = {
    "data_level0.bin",
    "header.bin",
    "length.bin",
    "link_lists.bin",
    "index_metadata.pickle",
}
_SQLITE_FILES = {
    "chroma.sqlite3",
    "chroma.sqlite3-journal",
    "chroma.sqlite3-wal",
    "chroma.sqlite3-shm",
}
_SQLITE_TABLES = ("segments", "collections", "segment_metadata", "max_seq_id")
_MAX_RUNS = 20
_VECTOR_COUNT = 80
_DIMENSIONS = 64
_ANCHOR_NAME = "hnsw_diagnostic_anchor"
_PRESSURE_NAME = "hnsw_diagnostic_pressure"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_info(chroma_version: str, cache_size: int | None, override: int | None) -> dict[str, Any]:
    return {
        "python_version": sys.version.replace("\n", " "),
        "python_implementation": sys.implementation.name,
        "platform": platform.platform(),
        "architecture": platform.architecture()[0],
        "chromadb_version": chroma_version,
        "hnsw_cache_size_observed": cache_size,
        "hnsw_cache_size_override": override,
        "hnsw_cache_setting_publicly_configurable": False,
    }


def _inventory(chroma_root: Path) -> dict[str, Any]:
    """Return bounded file metadata only; never read vector or document data."""
    files: list[dict[str, Any]] = []
    for base, dirs, names in os.walk(chroma_root, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(base) / name).is_symlink()]
        for name in names:
            if name not in _NATIVE_HNSW_FILES and name not in _SQLITE_FILES:
                continue
            path = Path(base) / name
            try:
                stat = path.stat(follow_symlinks=False)
                files.append({
                    "relative_path": path.relative_to(chroma_root).as_posix(),
                    "size_bytes": stat.st_size,
                    "modified_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                    "is_symlink": path.is_symlink(),
                })
            except OSError as exc:
                files.append({
                    "relative_path": path.relative_to(chroma_root).as_posix(),
                    "stat_error_type": type(exc).__name__,
                })
            if len(files) >= 256:
                return {"files": files, "truncated": True}
    return {"files": files, "truncated": False}


def _metadata_pickle_summary(chroma_root: Path) -> dict[str, Any]:
    candidates = [
        Path(base) / name
        for base, _, names in os.walk(chroma_root, followlinks=False)
        for name in names
        if name == "index_metadata.pickle"
    ]
    if not candidates:
        return {"present": False}
    summaries = []
    for path in candidates[:64]:
        summary: dict[str, Any] = {
            "relative_path": path.relative_to(chroma_root).as_posix(),
            "present": True,
        }
        try:
            summary["size_bytes"] = path.stat(follow_symlinks=False).st_size
            # This is only an opt-in probe's own freshly created synthetic store.
            # Never use this diagnostic on an index supplied by a caller.
            import pickle

            with path.open("rb") as stream:
                metadata = pickle.load(stream)
            labels = getattr(metadata, "id_to_label", None)
            summary["pickle_loadable"] = True
            summary["id_to_label_count"] = len(labels) if labels is not None else None
            summary["id_to_label_empty"] = len(labels) == 0 if labels is not None else None
        except Exception as exc:
            summary["pickle_loadable"] = False
            summary["inspection_error_type"] = type(exc).__name__
        summaries.append(summary)
    return {"present": True, "files": summaries, "truncated": len(candidates) > 64}


def _sqlite_summary(chroma_root: Path) -> dict[str, Any]:
    path = chroma_root / "chroma.sqlite3"
    if not path.exists():
        return {"present": False}
    result: dict[str, Any] = {"present": True}
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=0.25)
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            result["row_counts"] = {
                table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                for table in _SQLITE_TABLES
                if table in tables
            }
        finally:
            connection.close()
    except Exception as exc:
        result["inspection_error_type"] = type(exc).__name__
    return result


def _safe_snapshot(chroma_root: Path) -> tuple[dict[str, Any], list[str]]:
    """Collect each diagnostic independently so one failure cannot hide another."""
    snapshot: dict[str, Any] = {"captured_utc": _timestamp(), "timing": "post_exception_sequential"}
    errors: list[str] = []
    collectors = (
        ("inventory", lambda: _inventory(chroma_root)),
        ("index_metadata_pickle", lambda: _metadata_pickle_summary(chroma_root)),
        ("sqlite", lambda: _sqlite_summary(chroma_root)),
    )
    for name, collect in collectors:
        try:
            snapshot[name] = collect()
        except Exception as exc:
            error_type = type(exc).__name__
            snapshot[name] = {"collection_error_type": error_type}
            errors.append(f"{name}:{error_type}")
    snapshot["note"] = "Post-exception observations only; they do not establish filesystem state at the instant of query failure."
    return snapshot, errors


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _save_failure_evidence(run_dir: Path, report: dict[str, Any], full_traceback: str) -> list[str]:
    """Best-effort evidence writing that never replaces the original traceback."""
    errors: list[str] = []
    try:
        _write_json(run_dir / "diagnostic.json", report)
    except Exception as exc:
        errors.append(f"diagnostic_json_write:{type(exc).__name__}")
    try:
        (run_dir / "traceback.txt").write_text(full_traceback, encoding="utf-8")
    except Exception as exc:
        errors.append(f"traceback_file_write:{type(exc).__name__}")
    return errors


def _run_child(run_dir: Path, cache_override: int | None) -> int:
    chromadb = None
    operations: list[dict[str, str]] = []
    cache_observed: int | None = None
    try:
        import chromadb as chromadb_package
        from chromadb.config import Settings

        chromadb = chromadb_package
        if cache_override is not None:
            # Diagnostic-only unsupported override. This is deliberately absent
            # from the default probe and is restored before the process exits.
            from chromadb.api.rust import RustBindingsAPI

            original_start = RustBindingsAPI.start

            def start_with_cache_size(api) -> None:
                api.hnsw_cache_size = cache_override
                original_start(api)

            RustBindingsAPI.start = start_with_cache_size
            try:
                client = chromadb.PersistentClient(
                    path=str(run_dir / "chroma"),
                    settings=Settings(anonymized_telemetry=False),
                )
            finally:
                RustBindingsAPI.start = original_start
            api = client._system.instance(RustBindingsAPI)
            cache_observed = api.hnsw_cache_size
        else:
            client = chromadb.PersistentClient(
                path=str(run_dir / "chroma"),
                settings=Settings(anonymized_telemetry=False),
            )
            # Read-only diagnostic observation through Chroma's installed Rust
            # system; hnsw_cache_size is not a public client setting.
            from chromadb.api.rust import RustBindingsAPI

            cache_observed = client._system.instance(RustBindingsAPI).hnsw_cache_size

        operations.append({"operation": "PersistentClient created", "path_role": "this run's temporary chroma directory"})
        anchor = client.get_or_create_collection(_ANCHOR_NAME, metadata={"hnsw:space": "cosine"})
        operations.append({"operation": "get_or_create_collection", "collection": _ANCHOR_NAME})
        embeddings = [
            [math.sin((row + 1) * (column + 1) * 0.017) for column in range(_DIMENSIONS)]
            for row in range(_VECTOR_COUNT)
        ]
        anchor.add(ids=[f"synthetic-{i}" for i in range(_VECTOR_COUNT)], embeddings=embeddings)
        operations.append({"operation": "add synthetic vectors", "collection": _ANCHOR_NAME, "count": str(_VECTOR_COUNT)})
        pressure = client.get_or_create_collection(_PRESSURE_NAME, metadata={"hnsw:space": "cosine"})
        operations.append({"operation": "get_or_create_collection", "collection": _PRESSURE_NAME})
        pressure.add(ids=["synthetic-pressure"], embeddings=[embeddings[0]])
        operations.append({"operation": "add synthetic vector", "collection": _PRESSURE_NAME, "count": "1"})
        config = anchor.configuration
        hnsw_config = config.get("hnsw") if isinstance(config, dict) else None
        try:
            result = anchor.query(query_embeddings=[embeddings[0]], n_results=_VECTOR_COUNT, include=["distances"])
        except Exception as query_error:
            full_traceback = traceback.format_exc()
            chroma_root = run_dir / "chroma"
            first_snapshot, diagnostic_errors = _safe_snapshot(chroma_root)
            second_snapshot, second_errors = _safe_snapshot(chroma_root)
            diagnostic_errors.extend(second_errors)
            hnsw_error = (
                "Error creating hnsw segment reader: Nothing found on disk"
                in str(query_error)
            )
            report = {
                "status": "failed",
                "failure_kind": "hnsw_reader_error" if hnsw_error else "query_error",
                "runtime": _runtime_info(chromadb.__version__, cache_observed, cache_override),
                "eviction_observed": "unknown",
                "eviction_observation_limit": "No supported read-only observation establishes whether this query followed an HNSW cache eviction.",
                "operations": operations + [{"operation": "query", "collection": _ANCHOR_NAME, "requested_results": str(_VECTOR_COUNT)}],
                "collection_hnsw_configuration": hnsw_config,
                "post_exception_snapshot_before_second_inspection": first_snapshot,
                "post_exception_snapshot_after_first_inspection": second_snapshot,
                "diagnostic_collection_errors": diagnostic_errors,
                "full_traceback_file": "traceback.txt",
            }
            evidence_write_errors = _save_failure_evidence(run_dir, report, full_traceback)
            print("Chroma query failed; diagnostic directory preserved.", file=sys.stderr)
            print(full_traceback, file=sys.stderr, end="")
            if diagnostic_errors or evidence_write_errors:
                print(
                    "Diagnostic collection/write issues (exception types only): "
                    + ", ".join(diagnostic_errors + evidence_write_errors),
                    file=sys.stderr,
                )
            return 1

        returned = len(result.get("ids", [[]])[0])
        report = {
            "status": "passed" if returned == _VECTOR_COUNT else "unexpected_result_count",
            "runtime": _runtime_info(chromadb.__version__, cache_observed, cache_override),
            "operations": operations + [{"operation": "query", "collection": _ANCHOR_NAME, "requested_results": str(_VECTOR_COUNT)}],
            "collection_hnsw_configuration": hnsw_config,
            "query_returned_count": returned,
        }
        _write_json(run_dir / "diagnostic.json", report)
        print(json.dumps({"status": "passed", "query_returned_count": returned, "hnsw_cache_size": cache_observed}))
        return 0 if returned == _VECTOR_COUNT else 2
    except Exception:
        full_traceback = traceback.format_exc()
        chroma_root = run_dir / "chroma"
        snapshot, diagnostic_errors = _safe_snapshot(chroma_root) if chroma_root.exists() else (None, [])
        report = {
            "status": "probe_failed_before_or_outside_query",
            "runtime": _runtime_info(getattr(chromadb, "__version__", "unknown"), cache_observed, cache_override),
            "operations": operations,
            "post_exception_snapshot": snapshot,
            "diagnostic_collection_errors": diagnostic_errors,
            "full_traceback_file": "traceback.txt",
        }
        evidence_write_errors = _save_failure_evidence(run_dir, report, full_traceback)
        print(full_traceback, file=sys.stderr, end="")
        if diagnostic_errors or evidence_write_errors:
            print(
                "Diagnostic collection/write issues (exception types only): "
                + ", ".join(diagnostic_errors + evidence_write_errors),
                file=sys.stderr,
            )
        return 2


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=5, help=f"bounded independent probes, 1..{_MAX_RUNS} (default: 5)")
    parser.add_argument(
        "--cache-size",
        type=int,
        default=None,
        help="diagnostic-only unsupported RustBindingsAPI cache override; omitted by default",
    )
    parser.add_argument("--keep-success", action="store_true", help="preserve successful probe directories too")
    parser.add_argument("--_child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--_run-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args._child:
        if args._run_dir is None:
            parser.error("internal child requires --_run-dir")
        return args
    if not 1 <= args.runs <= _MAX_RUNS:
        parser.error(f"--runs must be between 1 and {_MAX_RUNS}")
    if args.cache_size is not None and not 1 <= args.cache_size <= 100_000:
        parser.error("--cache-size must be between 1 and 100000")
    return args


def main() -> int:
    args = _parse_args()
    if args._child:
        return _run_child(args._run_dir, args.cache_size)

    root = Path(tempfile.mkdtemp(prefix=f"chroma-hnsw-diagnostic-{uuid.uuid4().hex[:8]}-")).resolve()
    print(f"probe_runs: {args.runs}")
    print(f"cache_override: {args.cache_size if args.cache_size is not None else 'none (installed default)'}")
    failures = 0
    completed = 0
    for run_number in range(1, args.runs + 1):
        run_dir = root / f"run-{run_number:02d}"
        run_dir.mkdir()
        command = [sys.executable, str(Path(__file__).resolve()), "--_child", "--_run-dir", str(run_dir)]
        if args.cache_size is not None:
            command.extend(["--cache-size", str(args.cache_size)])
        try:
            child = subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)
        except subprocess.TimeoutExpired:
            (run_dir / "runner_timeout.txt").write_text(
                "Probe child exceeded the 120-second diagnostic bound.\n", encoding="utf-8"
            )
            failures += 1
            completed += 1
            print(f"run {run_number}: timed out; evidence directory preserved", file=sys.stderr)
            print(f"failure_evidence_preserved: {run_dir}", file=sys.stderr)
            break
        completed += 1
        if child.stdout.strip():
            print(f"run {run_number}: {child.stdout.strip()}")
        if child.returncode != 0:
            failures += 1
            if child.stderr:
                print(child.stderr, file=sys.stderr, end="")
            print(f"failure_evidence_preserved: {run_dir}", file=sys.stderr)
            break
        if args.keep_success:
            print(f"success_evidence_preserved: {run_dir}")
        else:
            try:
                shutil.rmtree(run_dir)
            except OSError as exc:
                print(f"success_cleanup_incomplete: {run_dir} ({type(exc).__name__})")
                print("The probe child has exited; inspect/remove this synthetic directory manually if needed.")
    print(f"completed_runs: {completed}; successful_runs: {completed - failures}; failures: {failures}")
    if failures:
        print(f"diagnostic_root_preserved: {root}", file=sys.stderr)
        return 1
    try:
        root.rmdir()
        print("temporary probe directories cleaned: yes")
    except OSError as exc:
        print(f"temporary root cleanup incomplete: {type(exc).__name__}: {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
