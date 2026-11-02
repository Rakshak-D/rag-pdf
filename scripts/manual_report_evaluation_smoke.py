"""Exercise real retrieval through evaluation and persistent result round-trip.

This smoke uses a synthetic PDF, a fresh-only real Chroma build, deterministic
injected adapters, and disposable artifact/catalog roots. It is an explicitly
initiated Chroma operation, not a read-only integrity check or model-quality
evaluation.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import traceback
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import manual_report_handoff_smoke as handoff
from scripts import manual_report_retrieval_smoke as retrieval_smoke


EVIDENCE_MARKER = "EVALUATION_EVIDENCE_MARKER"
QUERY = "Where is the blue lantern?"
ANSWER = "The blue lantern is in the north archive [T1]."
METRIC_IDS = tuple(f"M{number}" for number in range(1, 20))


class EvaluationSmokeError(RuntimeError):
    def __init__(self, code: str, diagnostics_root: Path | None = None) -> None:
        self.code = code
        self.diagnostics_root = diagnostics_root
        super().__init__(code)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise EvaluationSmokeError("fixture_json_invalid")
    return value


def _post_exit_storage_observation(root: Path) -> dict[str, Any]:
    """Inspect only this smoke's disposable Chroma files after child exit."""
    try:
        temp_root = Path(tempfile.gettempdir()).resolve()
        resolved_root = root.resolve(strict=True)
        if not resolved_root.is_relative_to(temp_root):
            return {"status": "not_inspected_outside_temporary_root"}
        build_path = root / "build-result.json"
        if not build_path.is_file():
            return {"status": "build_evidence_unavailable"}
        build = _read_json(build_path)
        fingerprint = build.get("index_fingerprint")
        if not isinstance(fingerprint, str) or len(fingerprint) != 64 or any(ch not in "0123456789abcdef" for ch in fingerprint):
            return {"status": "build_identity_invalid"}
        build_id = build.get("build_id")
        if not isinstance(build_id, str) or len(build_id) != 32 or any(ch not in "0123456789abcdef" for ch in build_id):
            return {"status": "build_identity_invalid"}
        storage = root / "synthetic-index-storage"
        index_root = storage / "indexes" / fingerprint
        chroma_root = index_root / "chroma"
        from scripts.diagnose_chroma_hnsw import _inventory

        result: dict[str, Any] = {
            "status": "observed_after_child_exit",
            "fingerprint_prefix": fingerprint[:12],
            "report_present": (storage / "build-artifacts" / build_id / "build-report.json").is_file(),
            "index_manifest_present": (index_root / "index-manifest.json").is_file(),
            "incomplete_marker_present": (index_root / ".fresh-build-incomplete").exists(),
            "writer_reservation_present": (storage / ".writer-leases" / fingerprint).exists(),
            "chroma_file_inventory": _inventory(chroma_root),
            "sqlite_contents_read_after_child_exit": False,
        }
        database = chroma_root / "chroma.sqlite3"
        if not database.is_file():
            result["sqlite_metadata"] = {"status": "database_missing"}
            return result

        # The subprocess runner has returned (or killed and joined its timed-out
        # child) before this function is called. Open SQLite read-only only now.
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=0.25)
        try:
            result["sqlite_contents_read_after_child_exit"] = True
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            sqlite_result: dict[str, Any] = {"status": "read_only_after_owner_process_exit", "table_counts": {}}
            for table in ("collections", "segments", "collection_metadata", "segment_metadata", "max_seq_id", "embeddings"):
                if table in tables:
                    sqlite_result["table_counts"][table] = int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            if "collections" in tables:
                sqlite_result["collections"] = [
                    {"id": row[0], "name": row[1], "dimension": row[2]}
                    for row in connection.execute("SELECT id,name,dimension FROM collections ORDER BY name LIMIT 8")
                ]
            if "segments" in tables:
                sqlite_result["segments"] = [
                    {"id": row[0], "type": row[1], "scope": row[2], "collection_id": row[3]}
                    for row in connection.execute("SELECT id,type,scope,collection FROM segments ORDER BY collection,type LIMIT 16")
                ]
            if "collection_metadata" in tables:
                sqlite_result["collection_identity_metadata"] = [
                    {"collection_id": row[0], "key": row[1], "value": row[2]}
                    for row in connection.execute(
                        "SELECT collection_id,key,str_value FROM collection_metadata "
                        "WHERE key IN ('collection_name','document_manifest_hash','index_fingerprint','index_schema_version','modality','store_schema_version') "
                        "ORDER BY collection_id,key LIMIT 32"
                    )
                ]
            if "max_seq_id" in tables:
                sqlite_result["max_sequence_rows"] = [
                    {"segment_id": row[0], "sequence": row[1]}
                    for row in connection.execute("SELECT segment_id,seq_id FROM max_seq_id ORDER BY segment_id LIMIT 16")
                ]
            result["sqlite_metadata"] = sqlite_result
        finally:
            connection.close()
        return result
    except Exception as exc:
        return {"status": "post_exit_observation_failed", "exception_type": type(exc).__name__}


def _child_environment() -> dict[str, str]:
    env = handoff._clear_workbench_environment()
    env.pop("PYTHONPATH", None)
    env.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "ANONYMIZED_TELEMETRY": "FALSE",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
        "OLLAMA_HOST": "http://127.0.0.1:1",
    })
    for key in (
        "HNSW_DIAGNOSTIC_ATTEMPT_ID", "HNSW_DIAGNOSTIC_SCENARIO",
        "HNSW_DIAGNOSTIC_KEEP_SUCCESS", "HNSW_DIAGNOSTIC_ROOT_PARENT",
    ):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def _run_bounded(command: list[str], *, cwd: Path, env: dict[str, str], log_path: Path, timeout: float) -> subprocess.CompletedProcess:
    try:
        with log_path.open("wb") as stream:
            return subprocess.run(command, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                  timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise EvaluationSmokeError("isolated_child_timeout") from exc


def _build_child(config_path: Path, source: Path, storage: Path, root: Path, timeout: float) -> dict[str, Any]:
    result_path, failure_path = root / "build-result.json", root / "build-failure.json"
    completed = _run_bounded(
        [sys.executable, str(Path(handoff.__file__).resolve()), "--build-child", str(config_path),
         str(source), str(storage), str(result_path), str(failure_path)],
        cwd=ROOT, env=_child_environment(), log_path=root / "build-child.log", timeout=max(timeout, 180),
    )
    if completed.returncode != 0 or not result_path.is_file():
        detail = _read_json(failure_path) if failure_path.is_file() else {}
        raise EvaluationSmokeError("fresh_build_failed_" + str(detail.get("stage", "unknown")))
    return _read_json(result_path)


def _run_evaluation_child(
    root: Path,
    *,
    mode: str,
    config_path: Path,
    source: Path,
    storage: Path,
    report_path: Path,
    fixtures_path: Path,
    timeout: float,
) -> dict[str, Any]:
    case_root = root / "evaluation-cases" / mode
    case_root.mkdir(parents=True, exist_ok=True)
    result_path, failure_path = case_root / "result.json", case_root / "failure.json"
    command = [
        sys.executable, str(Path(__file__).resolve()), "--evaluation-child", mode,
        str(config_path), str(source), str(storage), str(report_path), str(fixtures_path),
        str(case_root), str(result_path), str(failure_path),
    ]
    completed = _run_bounded(command, cwd=ROOT, env=_child_environment(),
                              log_path=case_root / "child.log", timeout=timeout)
    if completed.returncode != 0 or not result_path.is_file():
        detail = _read_json(failure_path) if failure_path.is_file() else {}
        raise EvaluationSmokeError(
            "evaluation_child_failed_" + str(detail.get("stage", "unknown")) + "_"
            + str(detail.get("exception_type", "unknown")), root,
        )
    value = _read_json(result_path)
    if value.get("child_exit_code") != 0 or value.get("child_pid") is None:
        raise EvaluationSmokeError("evaluation_child_completion_evidence_invalid", root)
    return value


def _validate_success(value: dict[str, Any], *, plan: Any, manifest_hash: str, build: dict[str, Any]) -> None:
    if value.get("outcome_status") != "succeeded" or value.get("trace_status") != "succeeded":
        raise EvaluationSmokeError("evaluation_case_not_successful")
    if value.get("reached_stage") != "generation" or value.get("generation_status") != "succeeded":
        raise EvaluationSmokeError("evaluation_generation_stage_not_verified")
    if (value.get("run_fingerprint") != plan.run_fingerprint
            or value.get("evaluation_fingerprint") is None
            or value.get("index_fingerprint") != plan.index_fingerprint
            or value.get("document_manifest_hash") != manifest_hash
            or value.get("catalog_index_fingerprint") != plan.index_fingerprint
            or value.get("catalog_status") != "succeeded"):
        raise EvaluationSmokeError("evaluation_provenance_mismatch")
    if value.get("benchmark_id") != "manual-evaluation-smoke" or value.get("benchmark_version") != "1":
        raise EvaluationSmokeError("evaluation_benchmark_identity_mismatch")
    if (value.get("trace_id") != value.get("outcome_trace_id")
            or value.get("trace_id") not in value.get("report_trace_ids", [])
            or value.get("trace_query_id") != value.get("actual_retrieval_query_id")
            or value.get("catalog_run_fingerprint") != plan.run_fingerprint
            or value.get("catalog_evaluation_fingerprint") != value.get("evaluation_fingerprint")
            or value.get("catalog_document_manifest_hash") != manifest_hash):
        raise EvaluationSmokeError("evaluation_trace_or_catalog_provenance_mismatch")
    if value.get("metric_ids") != sorted(METRIC_IDS) or value.get("aggregate_metric_ids") != sorted(METRIC_IDS):
        raise EvaluationSmokeError("evaluation_metric_coverage_invalid")
    if value.get("metric_statuses_invalid") is not False:
        raise EvaluationSmokeError("evaluation_metric_status_invalid")
    if value.get("report_text_records") != build.get("reported_text_records") or value.get("report_image_records") != build.get("reported_image_records"):
        raise EvaluationSmokeError("evaluation_build_counts_mismatch")
    if value.get("retrieval_count_checks") != {
        "text": build.get("reported_text_records"), "image": build.get("reported_image_records")
    }:
        raise EvaluationSmokeError("retrieval_did_not_check_both_report_counts")
    if (value.get("retrieved_record_ids") != value.get("trace_evidence_record_ids")
            or not value.get("retrieved_record_ids")
            or value.get("retrieved_evidence_marker") is not True):
        raise EvaluationSmokeError("persisted_retrieval_evidence_not_verified")
    if (value.get("generation_prompt_contained_evidence") is not True
            or value.get("generation_response") != ANSWER
            or value.get("generation_response_hash_matches") is not True
            or value.get("generation_calls") != 1
            or value.get("query_adapter_calls") != [QUERY]):
        raise EvaluationSmokeError("deterministic_generation_evidence_invalid")
    if value.get("artifact_round_trip_equal") is not True or value.get("catalog_aggregate_count") != 19:
        raise EvaluationSmokeError("evaluation_artifact_catalog_round_trip_failed")
    if value.get("catalog_aggregates_match_report") is not True:
        raise EvaluationSmokeError("catalog_aggregates_disagree_with_report")
    if value.get("evaluation_artifact_relative_path") != value.get("catalog_relative_path"):
        raise EvaluationSmokeError("evaluation_artifact_catalog_reference_mismatch")
    if value.get("writer_lease_released") is not True:
        raise EvaluationSmokeError("evaluation_writer_lease_not_released")


def _run_smoke(root: Path, timeout: float) -> dict[str, Any]:
    import numpy as np

    from backend.config.schema import ResolvedRunConfig
    from backend.engine.manifest import build_document_manifest
    from backend.engine.retrieval import _validate_build_report
    from backend.experiments.run_plan import build_run_plan

    source = root / "synthetic-pdf-source"
    source.mkdir()
    storage = root / "synthetic-index-storage"
    storage.mkdir()
    config = handoff._make_config(source, root)
    values = config.model_dump(mode="python")
    values.update({
        "retrieval_mode": "semantic",
        "adaptive_weighting": False,
        "use_weighted_fusion": False,
        "llm_retries": 0,
        "llm_fail_fast": True,
    })
    config = ResolvedRunConfig.model_validate(values)
    config.validate_for_workbench()
    config_path = root / "resolved-config.json"
    config_path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    handoff._write_pdf(
        source / "synthetic.pdf",
        f"{EVIDENCE_MARKER}: blue lantern location north archive.",
    )
    manifest = build_document_manifest(source)
    plan = build_run_plan(config, manifest.content_hash)
    build = _build_child(config_path, source, storage, root, timeout)
    report_path = storage / Path(*build["report_reference"].split("/"))
    report = _validate_build_report(storage, report_path, plan)
    if (report.status not in {"completed", "completed_with_warnings"} or report.build_id != build.get("build_id")
            or report.document_manifest_hash != manifest.content_hash
            or report.index_fingerprint != plan.index_fingerprint
            or report.text_records_persisted != build.get("reported_text_records")
            or report.image_records_persisted != build.get("reported_image_records")
            or report.text_records_persisted <= 0):
        raise EvaluationSmokeError("genuine_build_report_invalid")
    index_manifest = storage / "indexes" / plan.index_fingerprint / "index-manifest.json"
    report_bytes, index_manifest_bytes = report_path.read_bytes(), index_manifest.read_bytes()

    fixtures = {
        "build_id": report.build_id,
        "manifest_hash": manifest.content_hash,
        "index_fingerprint": plan.index_fingerprint,
        "missing_report": str(storage / "build-artifacts" / ("f" * 32) / "build-report.json"),
        "identity_mismatch_report": str(retrieval_smoke._write_report_variant(
            report_path, storage, "a" * 32, updates={"document_manifest_hash": "0" * 64},
        )),
        "count_mismatch_report": str(retrieval_smoke._write_report_variant(
            report_path, storage, "b" * 32,
            updates={"text_records_persisted": report.text_records_persisted + 1},
        )),
        "report_text_records": report.text_records_persisted,
        "report_image_records": report.image_records_persisted,
    }
    fixtures_path = root / "evaluation-fixtures.json"
    _write_json(fixtures_path, fixtures)

    storage_before_preflight = handoff._tree_snapshot(storage)
    preflight = handoff._run_preflight(
        root, config_path=config_path, source=source, storage=storage,
        build_id=report.build_id, manifest_hash=manifest.content_hash,
        fingerprint=plan.index_fingerprint, label="evaluation_fixture", timeout=timeout,
    )
    if (preflight.get("result") != "report_compatible_for_manual_review"
            or preflight.get("build_id") != report.build_id
            or preflight.get("report_reference") != build.get("report_reference")
            or preflight.get("document_manifest_hash") != manifest.content_hash
            or preflight.get("index_fingerprint") != plan.index_fingerprint
            or preflight.get("check_level") != "report"
            or preflight.get("store_checked") is not False):
        raise EvaluationSmokeError("explicit_report_preflight_failed")
    if storage_before_preflight != handoff._tree_snapshot(storage):
        raise EvaluationSmokeError("preflight_modified_build_storage")

    before_evaluation = handoff._tree_snapshot(storage)
    positive = _run_evaluation_child(
        root, mode="positive", config_path=config_path, source=source, storage=storage,
        report_path=report_path, fixtures_path=fixtures_path, timeout=max(timeout, 120),
    )
    _validate_success(positive, plan=plan, manifest_hash=manifest.content_hash, build=build)
    after_evaluation = handoff._tree_snapshot(storage)
    chroma_delta = retrieval_smoke._summarize_chroma_delta(
        retrieval_smoke._snapshot_delta(before_evaluation, after_evaluation), plan.index_fingerprint,
    )
    if report_path.read_bytes() != report_bytes or index_manifest.read_bytes() != index_manifest_bytes:
        raise EvaluationSmokeError("evaluation_rewrote_build_evidence")
    if (storage / ".writer-leases" / plan.index_fingerprint).exists():
        raise EvaluationSmokeError("evaluation_child_left_writer_reservation")

    retrieval_failures = _run_evaluation_child(
        root, mode="retrieval_failures", config_path=config_path, source=source, storage=storage,
        report_path=report_path, fixtures_path=fixtures_path, timeout=max(timeout, 180),
    )
    generation_recovery = _run_evaluation_child(
        root, mode="generation_recovery", config_path=config_path, source=source, storage=storage,
        report_path=report_path, fixtures_path=fixtures_path, timeout=max(timeout, 120),
    )
    persistence_failures = _run_evaluation_child(
        root, mode="persistence_failures", config_path=config_path, source=source, storage=storage,
        report_path=report_path, fixtures_path=fixtures_path, timeout=max(timeout, 180),
    )
    for group_name, group in (("retrieval_failures", retrieval_failures),
                              ("generation_recovery", generation_recovery),
                              ("persistence_failures", persistence_failures)):
        if group.get("group") != group_name or group.get("assertions_passed") is not True:
            raise EvaluationSmokeError("evaluation_failure_contract_failed_" + group_name)
    if report_path.read_bytes() != report_bytes or index_manifest.read_bytes() != index_manifest_bytes:
        raise EvaluationSmokeError("evaluation_failure_case_rewrote_build_evidence")
    if (storage / ".writer-leases" / plan.index_fingerprint).exists():
        raise EvaluationSmokeError("evaluation_failure_child_left_writer_reservation")

    return {
        "build": {
            "status": build.get("status"), "build_id": report.build_id,
            "report_reference": build.get("report_reference"),
            "manifest_hash": manifest.content_hash, "index_fingerprint": plan.index_fingerprint,
            "text_records": report.text_records_persisted, "image_records": report.image_records_persisted,
        },
        "preflight": {
            "result": preflight.get("result"), "check_level": preflight.get("check_level"),
            "store_checked": preflight.get("store_checked"), "storage_unchanged": True,
        },
        "evaluation": positive,
        "retrieval_failure_cases": retrieval_failures.get("cases"),
        "generation_failure_and_recovery": generation_recovery.get("cases"),
        "persistence_failure_cases": persistence_failures.get("cases"),
        "chroma_filesystem_delta_after_positive_evaluation": chroma_delta,
        "report_and_index_manifest_unchanged": True,
        "all_child_processes_exited": True,
        "production_model_calls": 0,
        "live_http_submission": False,
    }


def run_smoke(*, timeout: float = 45) -> dict[str, Any]:
    if not 5 <= timeout <= 120:
        raise EvaluationSmokeError("timeout_invalid")
    root_parent = os.environ.get("HNSW_DIAGNOSTIC_ROOT_PARENT")
    if root_parent:
        supplied_parent = Path(root_parent)
        if supplied_parent.is_symlink():
            raise EvaluationSmokeError("diagnostic_root_parent_unsafe")
        parent = supplied_parent.resolve(strict=True)
        temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
        if parent == temp_root or not parent.is_relative_to(temp_root) or parent.is_symlink():
            raise EvaluationSmokeError("diagnostic_root_parent_unsafe")
        root = Path(tempfile.mkdtemp(prefix="rag-manual-evaluation-", dir=parent))
    else:
        root = Path(tempfile.mkdtemp(prefix="rag-manual-evaluation-"))
    try:
        result = _run_smoke(root, timeout)
    except BaseException as exc:
        post_exit_observation = _post_exit_storage_observation(root)
        failure_path = root / "smoke-failure.json"
        if not failure_path.exists():
            _write_json(failure_path, {
                "exception_type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            })
        try:
            smoke_failure = _read_json(failure_path)
            smoke_failure["post_exit_storage_observation"] = post_exit_observation
            _write_json(failure_path, smoke_failure)
            generation_failure = root / "evaluation-cases" / "generation_recovery" / "failure.json"
            if generation_failure.is_file():
                child_failure = _read_json(generation_failure)
                child_failure["post_exit_storage_observation"] = post_exit_observation
                _write_json(generation_failure, child_failure)
        except Exception:
            # Failure evidence already exists; post-mortem enrichment must not
            # replace its original exception or traceback.
            pass
        if isinstance(exc, EvaluationSmokeError):
            exc.diagnostics_root = root
            raise
        raise EvaluationSmokeError("unexpected_" + type(exc).__name__, root) from exc
    if os.environ.get("HNSW_DIAGNOSTIC_KEEP_SUCCESS") == "1":
        result["diagnostic_attempt_id"] = os.environ.get("HNSW_DIAGNOSTIC_ATTEMPT_ID", "attempt-id-unprovided")
        result["diagnostic_root_retained"] = str(root)
    else:
        shutil.rmtree(root)
    return result


def _evaluation_child(argv: list[str]) -> int:
    if len(argv) != 9:
        return 2
    mode, config_name, source_name, storage_name, report_name, fixtures_name, case_root_name, result_name, failure_name = argv
    stage = "evaluation_child_entry"
    diagnostic: dict[str, Any] | None = None
    lifecycle_events: list[dict[str, Any]] = []
    lifecycle_events_truncated = False
    lifecycle_started_ns = __import__("time").monotonic_ns()
    attempt_id = os.environ.get("HNSW_DIAGNOSTIC_ATTEMPT_ID", "attempt-id-unprovided")
    diagnostic_scenario = os.environ.get("HNSW_DIAGNOSTIC_SCENARIO", "evaluation_recovery")
    active_case_label: str | None = None
    query_ordinal = 0
    query_observations: list[dict[str, Any]] = []
    failure_path = Path(failure_name)
    try:
        import numpy as np
        import chromadb
        import platform
        import threading
        import time
        from chromadb.api.shared_system_client import SharedSystemClient

        from backend.config.schema import ResolvedRunConfig
        from backend.config.writer_lease import WriterLease
        from backend.engine.context import ContextFormatter
        from backend.engine.generation import LocalGenerationAdapter
        from backend.engine.manifest import build_document_manifest
        from backend.engine.retrieval import retrieve_for_plan
        from backend.experiments.run_plan import build_run_plan
        from backend.experiments.artifact_store import ExperimentArtifactStore
        from backend.experiments.benchmark import BenchmarkCase, BenchmarkSuite
        from backend.experiments.catalog import CatalogIntegrityError, ExperimentCatalog
        from backend.experiments.serialization import serialize_evaluation_result
        from backend.services.experiment_service import (
            ExperimentService, ServicePersistenceError, ServiceResultIntegrityError,
        )
        from backend.vectorstore.chroma_store import ChromaVectorStore
        import backend.engine.retrieval as retrieval_module
        import backend.services.experiment_service as service_module

        config_path, source, storage, report_path = map(Path, (config_name, source_name, storage_name, report_name))
        fixtures = _read_json(Path(fixtures_name))
        case_root = Path(case_root_name)
        config = ResolvedRunConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
        config.validate_for_workbench()
        manifest = build_document_manifest(source)
        plan = build_run_plan(config, manifest.content_hash)
        if (manifest.content_hash != fixtures.get("manifest_hash")
                or plan.index_fingerprint != fixtures.get("index_fingerprint")):
            raise EvaluationSmokeError("evaluation_child_plan_identity_mismatch")
        chroma_root = storage / "indexes" / plan.index_fingerprint / "chroma"
        index_manifest_path = storage / "indexes" / plan.index_fingerprint / "index-manifest.json"
        report_digest_at_child_entry = hashlib.sha256(report_path.read_bytes()).hexdigest()
        index_manifest_digest_at_child_entry = hashlib.sha256(index_manifest_path.read_bytes()).hexdigest()

        def bounded_chroma_files() -> dict[str, Any]:
            allowed = {
                "chroma.sqlite3", "chroma.sqlite3-wal", "chroma.sqlite3-shm", "chroma.sqlite3-journal",
                "data_level0.bin", "header.bin", "length.bin", "link_lists.bin", "index_metadata.pickle",
            }
            files: list[dict[str, Any]] = []
            segment_directories: set[str] = set()
            visited = 0
            truncated = False
            if not chroma_root.is_dir():
                return {"root_present": False, "files": [], "segment_directory_count": 0, "truncated": False}
            for base, directories, filenames in os.walk(chroma_root, followlinks=False):
                visited += 1
                if visited > 64:
                    truncated = True
                    break
                base_path = Path(base)
                directories[:] = [name for name in directories if not (base_path / name).is_symlink()]
                for name in directories:
                    candidate = base_path / name
                    if len(candidate.relative_to(chroma_root).parts) == 1:
                        segment_directories.add(candidate.name)
                for name in filenames:
                    if name not in allowed:
                        continue
                    candidate = base_path / name
                    try:
                        info = candidate.stat(follow_symlinks=False)
                        files.append({
                            "relative_path": candidate.relative_to(chroma_root).as_posix(),
                            "size_bytes": info.st_size,
                            "mtime_ns": info.st_mtime_ns,
                            "is_symlink": candidate.is_symlink(),
                        })
                    except OSError as exc:
                        files.append({
                            "relative_path": candidate.relative_to(chroma_root).as_posix(),
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
                "segment_directory_count": len(segment_directories),
                "truncated": truncated,
            }

        def cache_observation(client: Any = None) -> dict[str, Any]:
            try:
                entries = list(SharedSystemClient._identifier_to_system.items())
                current = getattr(client, "_system", None) if client is not None else None
                chroma_root_resolved = chroma_root.resolve(strict=False)
                matching = []
                for identifier, system in entries:
                    try:
                        if Path(identifier).resolve(strict=False) == chroma_root_resolved:
                            matching.append((identifier, system))
                    except (OSError, TypeError, ValueError):
                        continue
                return {
                    "cached_system_count": len(entries),
                    "same_chroma_path_entry_count": len(matching),
                    "current_system_cached": current is not None and any(current is item for _, item in entries),
                    "current_system_matches_chroma_path": current is not None and any(current is item for _, item in matching),
                }
            except Exception as exc:
                return {"observation_error_type": type(exc).__name__}

        def lifecycle_checkpoint(name: str, store: Any = None, *, client: Any = None,
                                 details: dict[str, Any] | None = None, snapshot: bool = False,
                                 hash_application_evidence: bool = False) -> dict[str, Any]:
            nonlocal lifecycle_events_truncated
            event: dict[str, Any] = {
                "stage": name,
                "elapsed_ns": time.monotonic_ns() - lifecycle_started_ns,
                "process_id": os.getpid(),
                "thread_id": threading.get_ident(),
                "attempt_id": attempt_id,
                "scenario": diagnostic_scenario,
            }
            if active_case_label is not None:
                event["case_label"] = active_case_label
            if name.startswith("query_") and query_ordinal:
                event["query_ordinal"] = query_ordinal
            if store is not None:
                event["fingerprint_prefix"] = store.manifest.index_fingerprint[:12]
                event["index_fingerprint"] = store.manifest.index_fingerprint
                event["reservation_present"] = (
                    storage / ".writer-leases" / plan.index_fingerprint
                ).is_dir()
                lease = getattr(store, "_writer_lease", None)
                if lease is None:
                    event["lease_observation"] = {
                        "state": "owner_unobserved_active_vs_stale_unknown",
                        "reservation_present": event["reservation_present"],
                    }
                else:
                    lease_pid = getattr(lease, "_pid", None)
                    lease_thread = getattr(lease, "_thread", None)
                    lease_active = bool(getattr(lease, "_active", False))
                    event["lease_observation"] = {
                        "state": "active_current_process_thread" if (
                            lease_active and lease_pid == os.getpid() and lease_thread == threading.get_ident()
                        ) else "lease_object_owner_mismatch_or_inactive",
                        "reservation_present": event["reservation_present"],
                        "lease_active": lease_active,
                        "store_claimed": bool(getattr(lease, "_store_claimed", False)),
                        "owner_process_id": lease_pid,
                        "owner_thread_id": lease_thread,
                    }
                client = client or getattr(store, "_client", None)
            if client is not None:
                event["cache"] = cache_observation(client)
            if snapshot:
                event["chroma_files"] = bounded_chroma_files()
                event["report_present"] = report_path.is_file()
                event["index_manifest_present"] = index_manifest_path.is_file()
                if hash_application_evidence:
                    if report_path.is_file():
                        event["report_sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
                    if index_manifest_path.is_file():
                        event["index_manifest_sha256"] = hashlib.sha256(index_manifest_path.read_bytes()).hexdigest()
            if details:
                event.update(details)
            if len(lifecycle_events) < 128:
                lifecycle_events.append(event)
            else:
                lifecycle_events_truncated = True
            return event

        def safe_lifecycle_checkpoint(name: str, store: Any = None, **kwargs: Any) -> dict[str, Any]:
            try:
                return lifecycle_checkpoint(name, store, **kwargs)
            except Exception as observation_error:
                return {
                    "stage": name,
                    "diagnostic_observation_error_type": type(observation_error).__name__,
                }

        def application_evidence_hashes() -> dict[str, Any]:
            evidence: dict[str, Any] = {}
            for name, path in (("report_sha256", report_path), ("index_manifest_sha256", index_manifest_path)):
                try:
                    evidence[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
                except OSError as observation_error:
                    evidence[name + "_observation_error_type"] = type(observation_error).__name__
            return evidence

        lifecycle_checkpoint("child_entry")
        lifecycle_checkpoint("imports_completed", details={
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
            "chromadb_version": chromadb.__version__,
            "fixture_builder_process_exited": True,
        })
        lifecycle_checkpoint("run_plan_reconstructed", details={
            "fingerprint_prefix": plan.index_fingerprint[:12],
            "manifest_prefix": manifest.content_hash[:12],
        })
        suite = BenchmarkSuite("manual-evaluation-smoke", "1", (
            BenchmarkCase("synthetic-case-1", QUERY, ("text",),
                          text_k=min(config.text_k, int(fixtures["report_text_records"])),
                          required_pages=("1",)),
        ))
        query_adapter_calls: list[str] = []
        generation_calls = 0
        generation_prompts: list[str] = []
        retrieved: list[dict[str, Any]] = []
        retrieval_attempts: list[dict[str, Any]] = []
        lease_events: list[dict[str, Any]] = []

        class TextQueryAdapter:
            def __init__(self) -> None:
                self.config = config
                self.model_name = config.text_embed_model

            def embed_query(self, query: str):
                query_adapter_calls.append(query)
                return np.asarray([1.0, 0.0, 0.0], dtype=np.float32)

        class Tokenizer:
            def encode(self, value: str) -> list[int]:
                return list(range(len(value.split())))

        class DeterministicClient:
            def __init__(self, should_fail: bool = False) -> None:
                self.should_fail = should_fail

            def chat(self, **request: Any) -> dict[str, Any]:
                nonlocal generation_calls
                generation_calls += 1
                prompt = request["messages"][1]["content"]
                generation_prompts.append(prompt)
                if self.should_fail:
                    raise RuntimeError("injected deterministic generation failure")
                if EVIDENCE_MARKER not in prompt:
                    raise RuntimeError("expected persisted evidence was absent from generation context")
                return {"message": {"content": ANSWER}}

        def make_generation(*, should_fail: bool = False):
            client = DeterministicClient(should_fail)
            return LocalGenerationAdapter(
                client_factory=lambda _host: client, encoder=Tokenizer(), sleeper=lambda _seconds: None,
                host="http://127.0.0.1:1",
            )

        def recording_retrieval(query: str, selected_plan: Any, selected_storage: Any, **kwargs: Any):
            lifecycle_checkpoint("retrieve_for_plan_started", details={
                "fingerprint_prefix": selected_plan.index_fingerprint[:12],
                "manifest_prefix": selected_plan.document_manifest_hash[:12],
            })
            try:
                value = retrieve_for_plan(query, selected_plan, selected_storage, **kwargs)
            except Exception as exc:
                retrieval_attempts.append({
                    "outcome": "raised", "exception_type": type(exc).__name__,
                    "message": str(exc), "traceback": traceback.format_exc(),
                })
                lifecycle_checkpoint("retrieve_for_plan_raised", details={
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc)[:512],
                }, snapshot=True)
                raise
            retrieved.append({
                "query_id": value.query_id,
                "index_fingerprint": value.index_fingerprint,
                "document_manifest_hash": value.document_manifest_hash,
                "text_count": value.text_records_persisted,
                "image_count": value.image_records_persisted,
                "text_record_ids": [hit.record_id for hit in value.text_hits],
                "text_documents": [hit.document for hit in value.text_hits],
            })
            retrieval_attempts.append({"outcome": "returned"})
            lifecycle_checkpoint("retrieve_for_plan_completed", snapshot=True)
            return value

        class NoProductionEmbedding:
            def __init__(self, *_args: Any, **_kwargs: Any) -> None:
                raise EvaluationSmokeError("production_embedding_constructor_called")

        retrieval_module.TextEmbeddingAdapter = NoProductionEmbedding
        retrieval_module.ImageEmbeddingAdapter = NoProductionEmbedding

        original_open_existing = ChromaVectorStore.open_existing.__func__
        original_count = ChromaVectorStore.count
        original_query = ChromaVectorStore._query
        original_store_close = ChromaVectorStore.close

        def tracked_open_existing(cls: Any, selected_storage: Any, selected_plan: Any):
            lifecycle_checkpoint("open_existing_started")
            client_cache_before = cache_observation()
            lifecycle_checkpoint("cache_before_open", details=client_cache_before)
            store = original_open_existing(cls, selected_storage, selected_plan)
            lifecycle_checkpoint("open_existing_completed", store, snapshot=True)
            return store

        def tracked_count(store: Any, modality: str):
            lifecycle_checkpoint(f"count_{modality}_started", store)
            count = original_count(store, modality)
            lifecycle_checkpoint(f"count_{modality}_completed", store, details={"count": count}, snapshot=True)
            return count

        def tracked_query(store: Any, modality: str, vector: Any, n_results: int):
            nonlocal diagnostic, query_ordinal
            query_ordinal += 1
            collection = getattr(store, "_collections", {}).get(modality)
            collection_name = getattr(collection, "name", None)
            collection_id = getattr(collection, "id", None)
            query_details = {
                "query_ordinal": query_ordinal,
                "query_modality": modality,
                "collection_name": collection_name,
                "collection_id": str(collection_id) if collection_id is not None else None,
                "requested_results": n_results,
            }
            pre_query_event = safe_lifecycle_checkpoint(
                f"query_{modality}_started", store, details=query_details, snapshot=True,
            )
            try:
                result = original_query(store, modality, vector, n_results)
            except BaseException as exc:
                query_traceback = traceback.format_exc()
                raised_event = safe_lifecycle_checkpoint(f"query_{modality}_raised", store, details={
                    **query_details,
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc)[:512],
                    "traceback": query_traceback,
                }, snapshot=True, hash_application_evidence=True)
                query_observations.append({
                    "attempt_id": attempt_id,
                    "scenario": diagnostic_scenario,
                    "case_label": active_case_label,
                    "query_ordinal": query_ordinal,
                    "modality": modality,
                    "collection_name": collection_name,
                    "collection_id": str(collection_id) if collection_id is not None else None,
                    "index_fingerprint": store.manifest.index_fingerprint,
                    "process_id": os.getpid(),
                    "thread_id": threading.get_ident(),
                    "cache": pre_query_event.get("cache"),
                    "reservation_present": pre_query_event.get("reservation_present"),
                    "lease_observation": pre_query_event.get("lease_observation"),
                    "pre_query_chroma_files": pre_query_event.get("chroma_files"),
                    "post_failure_chroma_files": raised_event.get("chroma_files"),
                    "outcome": "raised",
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc)[:512],
                })
                last_completed = next((
                    item["stage"] for item in reversed(lifecycle_events[:-1])
                    if item["stage"].endswith("_completed")
                ), None)
                diagnostic = {
                    "failure_stage": f"query_{modality}",
                    "query_ordinal": query_ordinal,
                    "case_label": active_case_label,
                    "collection_name": collection_name,
                    "collection_id": str(collection_id) if collection_id is not None else None,
                    "last_completed_checkpoint": last_completed,
                    "process_id": os.getpid(),
                    "thread_id": threading.get_ident(),
                    "fingerprint_prefix": store.manifest.index_fingerprint[:12],
                    "manifest_prefix": store.manifest.document_manifest_hash[:12],
                    "reservation_present_at_exception": (
                        storage / ".writer-leases" / plan.index_fingerprint
                    ).is_dir(),
                    "cache": cache_observation(store._client),
                    "chroma_files": bounded_chroma_files(),
                    **application_evidence_hashes(),
                    "traceback": query_traceback,
                    "timeline": list(lifecycle_events),
                }
                diagnostic["report_unchanged_from_child_entry"] = (
                    diagnostic.get("report_sha256") == report_digest_at_child_entry
                )
                diagnostic["index_manifest_unchanged_from_child_entry"] = (
                    diagnostic.get("index_manifest_sha256") == index_manifest_digest_at_child_entry
                )
                raise
            try:
                returned_count = len(result.get("ids", [[]])[0])
            except (AttributeError, IndexError, TypeError):
                returned_count = None
            completed_event = safe_lifecycle_checkpoint(
                f"query_{modality}_completed", store,
                details={**query_details, "returned_count": returned_count}, snapshot=True,
            )
            query_observations.append({
                "attempt_id": attempt_id,
                "scenario": diagnostic_scenario,
                "case_label": active_case_label,
                "query_ordinal": query_ordinal,
                "modality": modality,
                "collection_name": collection_name,
                "collection_id": str(collection_id) if collection_id is not None else None,
                "index_fingerprint": store.manifest.index_fingerprint,
                "process_id": os.getpid(),
                "thread_id": threading.get_ident(),
                "cache": pre_query_event.get("cache"),
                "reservation_present": pre_query_event.get("reservation_present"),
                "lease_observation": pre_query_event.get("lease_observation"),
                "pre_query_chroma_files": pre_query_event.get("chroma_files"),
                "outcome": "returned",
                "returned_count": returned_count,
                "post_query_chroma_files": completed_event.get("chroma_files"),
            })
            return result

        def tracked_store_close(store: Any):
            client = getattr(store, "_client", None)
            lifecycle_checkpoint("store_close_started", store, client=client)
            try:
                return original_store_close(store)
            finally:
                lifecycle_checkpoint("store_close_completed", store, client=client, snapshot=True)

        ChromaVectorStore.open_existing = classmethod(tracked_open_existing)
        ChromaVectorStore.count = tracked_count
        ChromaVectorStore._query = tracked_query
        ChromaVectorStore.close = tracked_store_close
        original_acquire = WriterLease.acquire.__func__
        original_release = WriterLease.release

        def tracked_acquire(cls, root: Any, fingerprint: str):
            lease = original_acquire(cls, root, fingerprint)
            lease_events.append({"event": "acquired", "pid": os.getpid(), "thread": __import__("threading").get_ident()})
            return lease

        def tracked_release(lease: Any):
            try:
                return original_release(lease)
            finally:
                lease_events.append({"event": "released", "pid": os.getpid(), "thread": __import__("threading").get_ident()})

        WriterLease.acquire = classmethod(tracked_acquire)
        WriterLease.release = tracked_release

        def service_for(label: str, *, selected_report: Path = report_path, failing_generation: bool = False):
            selected_root = case_root / label
            selected_root.mkdir(parents=True, exist_ok=True)
            artifact_root = selected_root / "artifacts"
            catalog_path = selected_root / "catalog.sqlite"
            store = ExperimentArtifactStore(artifact_root)
            catalog = ExperimentCatalog(catalog_path, store)
            service = ExperimentService(
                catalog, storage, selected_report, ContextFormatter(encoder=Tokenizer()),
                make_generation(should_fail=failing_generation), text_adapter=TextQueryAdapter(),
                retrieval_callable=recording_retrieval,
            )
            return service, store, catalog, artifact_root

        def summarize(persisted: Any, store: Any, catalog: Any, artifact_root: Path, *, calls_before: int,
                      retrieval_before: int, retrieval_attempt_before: int,
                      adapter_call_before: int, generation_prompt_before: int) -> dict[str, Any]:
            result = persisted.result
            trace, outcome = result.traces[0], result.outcomes[0]
            run_retrieval = retrieved[retrieval_before:]
            run_prompt = generation_prompts[generation_prompt_before:]
            actual_retrieval = run_retrieval[-1] if run_retrieval else None
            return {
                "catalog_status": persisted.record.status,
                "outcome_status": outcome.status,
                "reached_stage": outcome.reached_stage,
                "error_code": outcome.error_code,
                "trace_status": trace.status,
                "trace_id": trace.trace_id,
                "outcome_trace_id": outcome.trace_id,
                "report_trace_ids": list(result.report.trace_ids),
                "failed_stage": trace.failed_stage,
                "trace_evidence_record_ids": [item.record_id for item in trace.evidence if item.modality == "text"],
                "trace_context_present": trace.context is not None,
                "generation_status": trace.generation.status if trace.generation else None,
                "generation_response_hash": trace.generation.response_hash if trace.generation else None,
                "report_text_records": actual_retrieval["text_count"] if actual_retrieval else None,
                "report_image_records": actual_retrieval["image_count"] if actual_retrieval else None,
                "retrieved_record_ids": actual_retrieval["text_record_ids"] if actual_retrieval else [],
                "retrieved_evidence_marker": bool(actual_retrieval and any(EVIDENCE_MARKER in (text or "") for text in actual_retrieval["text_documents"])),
                "run_fingerprint": result.report.run_fingerprint,
                "evaluation_fingerprint": result.report.evaluation_fingerprint,
                "index_fingerprint": trace.index_fingerprint,
                "document_manifest_hash": trace.document_manifest_hash,
                "benchmark_id": result.report.benchmark_id,
                "benchmark_version": result.report.benchmark_version,
                "metric_ids": sorted(item.metric_id for item in result.report.observations),
                "metric_statuses_invalid": any(item.status not in {"computed", "skipped", "unavailable", "failed"} for item in result.report.observations),
                "metric_status_summary": {status: sum(item.status == status for item in result.report.observations) for status in ("computed", "skipped", "unavailable", "failed")},
                "aggregate_metric_ids": sorted(item.metric_id for item in result.report.aggregates),
                "artifact_id": persisted.record.artifact_id,
                "evaluation_artifact_relative_path": persisted.record.relative_path,
                "catalog_relative_path": persisted.record.relative_path,
                "artifact_exists": (artifact_root / Path(persisted.record.relative_path)).is_file(),
                "catalog_aggregate_count": len(catalog.list_metric_aggregates(persisted.record.artifact_id)),
                "retrieval_count_checks": {"text": actual_retrieval["text_count"], "image": actual_retrieval["image_count"]} if actual_retrieval else {},
                "generation_calls": generation_calls - calls_before,
                "generation_prompt_contained_evidence": bool(run_prompt and EVIDENCE_MARKER in run_prompt[-1]),
                "writer_lease_released": not (Path(storage) / ".writer-leases" / plan.index_fingerprint).exists(),
                "trace_query_id": trace.query_id,
                "trace_run_fingerprint": trace.run_fingerprint,
                "trace_index_fingerprint": trace.index_fingerprint,
                "trace_manifest_hash": trace.document_manifest_hash,
                "adapter_call_count": len(query_adapter_calls) - adapter_call_before,
                "retrieval_exception": retrieval_attempts[-1] if len(retrieval_attempts) > retrieval_attempt_before else None,
                "report_evaluation_fingerprint_valid": __import__("backend.experiments.evaluation", fromlist=["verify_evaluation_fingerprint"]).verify_evaluation_fingerprint(result.report) is None,
            }

        def run_persisted(label: str, *, selected_report: Path = report_path, failing_generation: bool = False):
            nonlocal stage, active_case_label
            stage = "service_construction"
            active_case_label = label
            query_observation_start = len(query_observations)
            service, store, catalog, artifact_root = service_for(label, selected_report=selected_report, failing_generation=failing_generation)
            calls_before = generation_calls
            retrieval_before = len(retrieved)
            retrieval_attempt_before = len(retrieval_attempts)
            adapter_call_before = len(query_adapter_calls)
            generation_prompt_before = len(generation_prompts)
            stage = "run_evaluation"
            persisted = service.run_evaluation(plan, suite)
            stage = "evaluation_summary"
            value = summarize(persisted, store, catalog, artifact_root, calls_before=calls_before,
                              retrieval_before=retrieval_before, retrieval_attempt_before=retrieval_attempt_before,
                              adapter_call_before=adapter_call_before,
                              generation_prompt_before=generation_prompt_before)
            value["retrieval_invocation_count"] = len(retrieved)
            value["retrieval_attempt_count"] = len(retrieval_attempts)
            value["retrieval_exception"] = retrieval_attempts[-1] if len(retrieval_attempts) > retrieval_attempt_before else None
            value["query_adapter_calls"] = list(query_adapter_calls)
            value["query_observations"] = query_observations[query_observation_start:]
            active_case_label = None
            return service, store, catalog, artifact_root, persisted, value

        if mode == "positive":
            stage = "positive_evaluation"
            service, store, catalog, artifact_root, persisted, summary = run_persisted("positive")
            from backend.experiments.evaluation import verify_evaluation_fingerprint
            from backend.experiments.serialization import serialize_evaluation_result

            loaded = service.load_evaluation_result(persisted.record.artifact_id)
            summary["artifact_round_trip_equal"] = serialize_evaluation_result(loaded) == serialize_evaluation_result(persisted.result)
            summary["catalog_aggregate_count"] = len(service.list_metric_aggregates(persisted.record.artifact_id))
            summary["catalog_index_fingerprint"] = persisted.record.shared_index_fingerprint
            summary["catalog_run_fingerprint"] = persisted.record.run_fingerprint
            summary["catalog_evaluation_fingerprint"] = persisted.record.evaluation_fingerprint
            summary["catalog_document_manifest_hash"] = persisted.record.document_manifest_hash
            summary["catalog_record_count"] = persisted.record.case_count
            summary["evaluation_artifact_relative_path"] = persisted.record.relative_path
            summary["catalog_relative_path"] = persisted.record.relative_path
            summary["generation_response"] = ANSWER
            summary["generation_response_hash_matches"] = (
                persisted.result.traces[0].generation is not None
                and persisted.result.traces[0].generation.response_hash == hashlib.sha256(ANSWER.encode("utf-8")).hexdigest()
            )
            summary["report_evaluation_fingerprint_valid"] = verify_evaluation_fingerprint(persisted.result.report) is None
            summary["metric_observations"] = [
                {"metric_id": item.metric_id, "status": item.status, "reason_code": item.reason_code}
                for item in sorted(persisted.result.report.observations, key=lambda item: item.metric_id)
            ]
            summary["aggregate_statuses"] = [
                {"metric_id": item.metric_id, "status": item.status}
                for item in sorted(persisted.result.report.aggregates, key=lambda item: item.metric_id)
            ]
            summary["actual_retrieval_query_id"] = retrieved[-1]["query_id"]
            catalog_metrics = service.list_metric_aggregates(persisted.record.artifact_id)
            summary["catalog_aggregates_match_report"] = {
                item.metric_id: (item.status, item.value, item.eligible_count, item.skipped_count,
                                 item.failed_count, item.unavailable_count)
                for item in catalog_metrics
            } == {
                item.metric_id: (item.status, item.value, item.eligible_count, item.skipped_count,
                                 item.failed_count, item.unavailable_count)
                for item in persisted.result.report.aggregates
            }
            summary["production_model_calls"] = 0
            summary["child_pid"] = os.getpid()
            summary["child_exit_code"] = 0
            summary["attempt_id"] = attempt_id
            summary["scenario"] = diagnostic_scenario
            summary["query_observations"] = list(query_observations)
            _write_json(Path(result_name), summary)
            return 0

        if mode == "retrieval_failures":
            stage = "missing_report_case"
            cases: dict[str, Any] = {}
            for name, selected_report in (
                ("missing_report", Path(fixtures["missing_report"])),
                ("report_identity_mismatch", Path(fixtures["identity_mismatch_report"])),
                ("persisted_count_mismatch", Path(fixtures["count_mismatch_report"])),
            ):
                before_retrieval, before_generation = len(retrieval_attempts), generation_calls
                before_adapter = len(query_adapter_calls)
                _svc, _store, catalog, _artifact_root, persisted, summary = run_persisted(name, selected_report=selected_report)
                if (summary["outcome_status"] != "failed" or summary["reached_stage"] != "retrieval"
                        or summary["trace_status"] != "failed" or summary["generation_calls"] != 0
                        or summary["adapter_call_count"] != 0 or len(query_adapter_calls) != before_adapter
                        or len(retrieval_attempts) - before_retrieval not in {0, 1}
                        or generation_calls != before_generation
                        or catalog.get_artifact(persisted.record.artifact_id).status != "failed"):
                    raise EvaluationSmokeError("retrieval_failure_case_not_recorded_" + name)
                if name == "persisted_count_mismatch" and summary["retrieved_record_ids"]:
                    raise EvaluationSmokeError("count_mismatch_returned_query_evidence")
                cases[name] = {"outcome_status": summary["outcome_status"], "stage": summary["reached_stage"], "error_code": summary["error_code"], "persisted": True}

            stage = "writer_reservation_case"
            lease = WriterLease.acquire(storage, plan.index_fingerprint)
            reservation = Path(storage) / ".writer-leases" / plan.index_fingerprint
            owner_before = (reservation / "owner").read_bytes()
            before_generation = generation_calls
            before_adapter = len(query_adapter_calls)
            _svc, _store, catalog, _artifact_root, persisted, summary = run_persisted("active_writer_reservation")
            if (summary["outcome_status"] != "failed" or summary["reached_stage"] != "retrieval"
                    or generation_calls != before_generation or summary["adapter_call_count"] != 0
                    or len(query_adapter_calls) != before_adapter
                    or (reservation / "owner").read_bytes() != owner_before):
                raise EvaluationSmokeError("active_writer_reservation_not_fail_closed")
            cases["active_writer_reservation"] = {"outcome_status": summary["outcome_status"], "stage": summary["reached_stage"], "owner_preserved": True, "persisted": catalog.get_artifact(persisted.record.artifact_id).status == "failed"}
            lease.release()
            if reservation.exists():
                raise EvaluationSmokeError("owned_test_lease_not_released")

            stage = "incomplete_marker_case"
            marker = Path(storage) / "indexes" / plan.index_fingerprint / ".fresh-build-incomplete"
            marker_bytes = b"test-only-incomplete-evidence\n"
            marker.write_bytes(marker_bytes)
            before_generation = generation_calls
            before_adapter = len(query_adapter_calls)
            _svc, _store, catalog, _artifact_root, persisted, summary = run_persisted("incomplete_marker")
            if (summary["outcome_status"] != "failed" or summary["reached_stage"] != "retrieval"
                    or generation_calls != before_generation or summary["adapter_call_count"] != 0
                    or len(query_adapter_calls) != before_adapter or marker.read_bytes() != marker_bytes):
                raise EvaluationSmokeError("incomplete_marker_not_preserved")
            cases["incomplete_marker"] = {"outcome_status": summary["outcome_status"], "stage": summary["reached_stage"], "marker_preserved": True, "persisted": catalog.get_artifact(persisted.record.artifact_id).status == "failed"}
            # Remove only the marker created by this disposable test after its
            # preservation assertion, so later explicit recovery cases can use
            # the same temporary index. Production recovery never does this.
            marker.unlink()
            if Path(storage, ".writer-leases", plan.index_fingerprint).exists():
                raise EvaluationSmokeError("retrieval_failure_left_writer_lease")
            _write_json(Path(result_name), {"group": mode, "assertions_passed": True, "cases": cases,
                                            "child_pid": os.getpid(), "child_exit_code": 0})
            return 0

        if mode == "generation_recovery":
            stage = "injected_generation_failure"
            failing_service, failing_store, failing_catalog, failing_root, failed, failed_summary = run_persisted(
                "generation-failure", failing_generation=True,
            )
            generation_failure_summary = {key: failed_summary.get(key) for key in (
                "outcome_status", "reached_stage", "trace_status", "trace_context_present",
                "trace_evidence_record_ids", "generation_calls", "writer_lease_released", "catalog_status",
            )}
            generation_failure_summary["retrieval_exception"] = failed_summary.get("retrieval_exception")
            prior_query_diagnostic = diagnostic
            diagnostic = {
                "generation_failure_result": generation_failure_summary,
                "query_boundary_diagnostic": prior_query_diagnostic,
            }
            if (failed_summary["outcome_status"] != "failed" or failed_summary["reached_stage"] != "generation"
                    or failed_summary["trace_status"] != "failed" or failed_summary["trace_context_present"] is not True
                    or not failed_summary["trace_evidence_record_ids"] or failed_summary["generation_calls"] != 1
                    or failed_summary["writer_lease_released"] is not True):
                raise EvaluationSmokeError("generation_failure_fabricated_success_or_lost_retrieval")
            # This is a distinct explicit service invocation after inspecting the failed result;
            # the manager/runner performs no automatic retry.
            stage = "explicit_recovery_evaluation"
            _service, _store, catalog, _artifact_root, succeeded, success_summary = run_persisted("explicit-recovery")
            if (success_summary["outcome_status"] != "succeeded" or success_summary["generation_calls"] != 1
                    or failed.record.status != "failed" or succeeded.record.status != "succeeded"
                    or failed.record.artifact_id == succeeded.record.artifact_id):
                raise EvaluationSmokeError("explicit_recovery_after_failure_invalid")
            _write_json(Path(result_name), {
                "group": mode, "assertions_passed": True,
                "attempt_id": attempt_id, "scenario": diagnostic_scenario,
                "query_observations": list(query_observations),
                "cases": {
                    "generation_failure": {"outcome_status": failed_summary["outcome_status"], "stage": failed_summary["reached_stage"], "retrieval_evidence_preserved": bool(failed_summary["trace_evidence_record_ids"]), "generation_calls": failed_summary["generation_calls"], "automatic_retry": False, "catalog_status": failed.record.status},
                    "explicit_recovery": {"outcome_status": success_summary["outcome_status"], "catalog_status": succeeded.record.status, "distinct_artifact": failed.record.artifact_id != succeeded.record.artifact_id},
                },
                "child_pid": os.getpid(), "child_exit_code": 0,
            })
            return 0

        if mode == "persistence_failures":
            cases: dict[str, Any] = {}
            stage = "artifact_publication_failure"
            service, store, catalog, artifact_root = service_for("artifact-publication-failure")
            original_publish = store._publish
            def fail_publish(_artifact_type: str, _data: bytes):
                raise OSError("injected artifact publication failure")
            store._publish = fail_publish
            try:
                service.run_evaluation(plan, suite)
                raise EvaluationSmokeError("artifact_publication_failure_was_ignored")
            except ServicePersistenceError as exc:
                if exc.error_code != "evaluation_persistence_failed" or store.list_artifacts() or catalog.list_artifacts():
                    raise EvaluationSmokeError("artifact_publication_failure_state_invalid") from exc
                cases["artifact_publication_failure"] = {"error_code": exc.error_code, "artifact_published": False, "catalog_registered": False}
            finally:
                store._publish = original_publish

            stage = "catalog_registration_failure"
            service, store, catalog, artifact_root = service_for("catalog-registration-failure")
            def fail_register(_reference: Any, _result: Any):
                raise CatalogIntegrityError("injected catalog registration conflict")
            catalog._register = fail_register
            try:
                service.run_evaluation(plan, suite)
                raise EvaluationSmokeError("catalog_registration_failure_was_ignored")
            except ServicePersistenceError as exc:
                published = store.list_artifacts()
                if (exc.error_code != "evaluation_persistence_failed" or len(published) != 1
                        or catalog.list_artifacts()):
                    raise EvaluationSmokeError("catalog_failure_did_not_preserve_or_report_artifact") from exc
                artifact_path = artifact_root / Path(published[0].relative_path)
                if not artifact_path.is_file():
                    raise EvaluationSmokeError("published_artifact_missing_after_catalog_failure")
                cases["catalog_registration_failure"] = {"error_code": exc.error_code, "artifact_published": True, "catalog_registered": False, "artifact_preserved": True}

            stage = "invalid_provenance_boundary"
            import backend.services.experiment_service as module
            real_runner = module.EvaluationRunner
            class InvalidProvenanceRunner:
                def __init__(self, *args: Any, **kwargs: Any) -> None:
                    self.delegate = real_runner(*args, **kwargs)
                def run(self, supplied_suite: Any):
                    result = self.delegate.run(supplied_suite)
                    return replace(result, report=replace(result.report, run_fingerprint="0" * 64))
            module.EvaluationRunner = InvalidProvenanceRunner
            service, store, catalog, _artifact_root = service_for("invalid-provenance")
            try:
                service.run_evaluation(plan, suite)
                raise EvaluationSmokeError("invalid_evaluation_provenance_was_persisted")
            except ServiceResultIntegrityError as exc:
                if exc.error_code != "evaluation_provenance_mismatch" or store.list_artifacts() or catalog.list_artifacts():
                    raise EvaluationSmokeError("invalid_provenance_boundary_state_invalid") from exc
                cases["invalid_evaluation_provenance"] = {"error_code": exc.error_code, "artifact_published": False, "catalog_registered": False, "pipeline_executed": bool(retrieved and generation_calls)}
            finally:
                module.EvaluationRunner = real_runner

            if Path(storage, ".writer-leases", plan.index_fingerprint).exists():
                raise EvaluationSmokeError("persistence_failure_left_writer_lease")
            if any(item.get("pid") != os.getpid() or item.get("thread") != __import__("threading").get_ident() for item in lease_events):
                raise EvaluationSmokeError("writer_lease_thread_ownership_mismatch")
            if not cases["invalid_evaluation_provenance"]["pipeline_executed"]:
                raise EvaluationSmokeError("invalid_provenance_case_did_not_execute_pipeline")
            _write_json(Path(result_name), {"group": mode, "assertions_passed": True, "cases": cases,
                                            "child_pid": os.getpid(), "child_exit_code": 0})
            return 0

        raise EvaluationSmokeError("evaluation_child_mode_invalid")
    except BaseException as exc:
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(failure_path, {
            "stage": stage,
            "attempt_id": attempt_id,
            "scenario": diagnostic_scenario,
            "exception_type": type(exc).__name__,
            "error_code": getattr(exc, "error_code", getattr(exc, "code", None)),
            "diagnostic": diagnostic,
            "query_observations": query_observations,
            "process_id": os.getpid(),
            "thread_id": threading.get_ident(),
            "lifecycle_events": lifecycle_events,
            "lifecycle_events_truncated": lifecycle_events_truncated,
            "traceback": traceback.format_exc(),
        })
        return 2


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "--evaluation-child":
        return _evaluation_child(arguments[1:])
    import argparse

    parser = argparse.ArgumentParser(description="Run an isolated genuine retrieval-to-evaluation persistence smoke.")
    parser.add_argument("--timeout", type=float, default=45, help="bounded child operation timeout in seconds (5–120)")
    args = parser.parse_args(arguments)
    try:
        result = run_smoke(timeout=args.timeout)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
        print("manual_report_evaluation_smoke_passed")
        return 0
    except EvaluationSmokeError as exc:
        print(f"manual_report_evaluation_smoke_failed: {exc.code}", file=sys.stderr)
        if exc.diagnostics_root is not None:
            print(f"diagnostic_root_retained: {exc.diagnostics_root}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
