"""Opt-in pytest plugin for the two known temporary-store HNSW test paths.

Activate explicitly with ``-p scripts.diagnose_chroma_hnsw_pytest_plugin`` and
``--hnsw-diagnostic-dir <existing-or-new-directory>``. Ordinary pytest runs do
not import or activate this plugin.
"""

from __future__ import annotations

import json
import sys
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from scripts.diagnose_chroma_hnsw import (
    _inventory,
    _metadata_pickle_summary,
    _runtime_info,
    _save_failure_evidence,
    _sqlite_summary,
)


_TARGETS = {
    "tests/integration/test_chroma_store.py::test_supplied_vectors_are_persistent_and_modalities_are_separate",
    "tests/integration/test_retrieval.py::test_combined_retrieval_validates_report_and_keeps_modalities_separate",
}
_HNSW_ERROR_TEXT = "Error creating hnsw segment reader: Nothing found on disk"


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

        client = store._client
        api = client._system.instance(RustBindingsAPI)
        return {
            "status": "observed",
            "value": int(api.hnsw_cache_size),
            "source": "unsupported_read_only_installed_internal",
            "eviction_observed": "unknown",
        }
    except Exception as exc:
        return {
            "status": "unknown",
            "error_type": type(exc).__name__,
            "eviction_observed": "unknown",
        }


def collect_test_store_evidence(
    store: Any,
    modality: str,
    *,
    temporary_test_root: Path | None,
    output_root: Path,
    full_traceback: str,
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
        for key, collector in (
            ("relevant_files", lambda: _inventory(chroma_root)),
            ("sqlite", lambda: _sqlite_summary(chroma_root)),
            ("index_metadata_pickle", lambda: _metadata_pickle_summary(chroma_root)),
        ):
            try:
                snapshot[key] = collector()
            except Exception as exc:
                error_type = type(exc).__name__
                snapshot[key] = {"collection_error_type": error_type}
                errors.append(f"{key}:{error_type}")
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

    report = {
        "failure_kind": "hnsw_reader_error",
        "test_temporary_index_provenance_verified": synthetic_owned,
        "store_relative_index_path": _relative_store_index_path(store),
        "operation": "ChromaVectorStore._query",
        "collection_modality": modality,
        "collection_name": collection_name,
        "runtime": _runtime_info(chroma_version, None, None),
        "cache_observation": _cache_observation(store),
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


def _wrap_query(original_query: Any, *, output_root: Path, temporary_test_root: Path | None):
    def diagnostic_query(store: Any, modality: str, query_embedding: Any, n_results: int):
        try:
            return original_query(store, modality, query_embedding, n_results)
        except Exception as error:
            if is_known_hnsw_error(error):
                original_traceback = traceback.format_exc()
                try:
                    evidence_dir, _errors = collect_test_store_evidence(
                        store,
                        modality,
                        temporary_test_root=temporary_test_root,
                        output_root=output_root,
                        full_traceback=original_traceback,
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
    if item.nodeid.replace("\\", "/") not in _TARGETS:
        yield
        return

    from backend.vectorstore.chroma_store import ChromaVectorStore

    original_query = ChromaVectorStore._query
    output_root: Path = item.config._hnsw_diagnostic_output_root
    temporary_test_root = item.funcargs.get("tmp_path")
    ChromaVectorStore._query = _wrap_query(
        original_query,
        output_root=output_root,
        temporary_test_root=Path(temporary_test_root) if temporary_test_root is not None else None,
    )
    try:
        yield
    finally:
        ChromaVectorStore._query = original_query
