"""Exercise explicit report handoff followed by isolated real retrieval.

This harness performs Chroma reads through the normal retrieval pipeline. The
operation can cause persistent Chroma side effects and is limited to temporary
synthetic storage. It is not a general-purpose integrity verifier.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import manual_report_handoff_smoke as handoff


class RetrievalSmokeError(RuntimeError):
    def __init__(self, code: str, diagnostics_path: Path | None = None) -> None:
        self.code = code
        self.diagnostics_path = diagnostics_path
        super().__init__(code)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RetrievalSmokeError("fixture_json_invalid")
    return value


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_report_variant(
    source_report: Path,
    storage: Path,
    build_id: str,
    *,
    updates: dict[str, Any] | None = None,
) -> Path:
    if re.fullmatch(r"[0-9a-f]{32}", build_id) is None:
        raise RetrievalSmokeError("negative_fixture_build_id_invalid")
    report_root = storage / "build-artifacts" / build_id
    report_root.mkdir(parents=True, exist_ok=False)
    report_path = report_root / "build-report.json"
    payload = json.loads(source_report.read_text(encoding="utf-8"))
    payload.update({
        "build_id": build_id,
        "artifact_root": str(report_root),
        "report_path": str(report_path),
    })
    if updates:
        payload.update(updates)
    _write_json(report_path, payload)
    return report_path


def _snapshot_delta(before: dict[str, tuple[Any, ...]], after: dict[str, tuple[Any, ...]]) -> dict[str, list[str]]:
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    digest_changed: list[str] = []
    size_changed: list[str] = []
    timestamp_only: list[str] = []
    directory_time_changed: list[str] = []
    for relative in sorted(set(before) & set(after)):
        old, new = before[relative], after[relative]
        if old == new:
            continue
        if old and new and old[0] == new[0] == "file":
            if old[3] != new[3]:
                digest_changed.append(relative)
            if old[1] != new[1]:
                size_changed.append(relative)
            if old[1] == new[1] and old[3] == new[3] and old[2] != new[2]:
                timestamp_only.append(relative)
        elif old and new and old[0] == new[0] == "directory" and old != new:
            directory_time_changed.append(relative)
    return {
        "added": added,
        "removed": removed,
        "digest_changed": digest_changed,
        "size_changed": size_changed,
        "timestamp_only": timestamp_only,
        "directory_mtime_changed": directory_time_changed,
    }


def _summarize_chroma_delta(delta: dict[str, list[str]], fingerprint: str) -> dict[str, Any]:
    prefix = f"indexes/{fingerprint}/chroma/"
    sqlite_name = prefix + "chroma.sqlite3"
    hnsw_names = {"data_level0.bin", "length.bin", "link_lists.bin", "header.bin", "index_metadata.pickle"}
    chroma_digest_changes = [path for path in delta["digest_changed"] if path.startswith(prefix)]
    return {
        "chroma_sqlite_digest_changed": sqlite_name in delta["digest_changed"],
        "chroma_sqlite_size_changed": sqlite_name in delta["size_changed"],
        "hnsw_digest_changed": [
            path for path in chroma_digest_changes if Path(path).name in hnsw_names
        ],
        "other_chroma_digest_changed": [
            path for path in chroma_digest_changes
            if path != sqlite_name and Path(path).name not in hnsw_names
        ],
        "chroma_size_changed": [path for path in delta["size_changed"] if path.startswith(prefix)],
        "non_chroma_size_changed": [path for path in delta["size_changed"] if not path.startswith(prefix)],
        "all_size_changed": delta["size_changed"],
        "chroma_timestamp_only": [path for path in delta["timestamp_only"] if path.startswith(prefix)],
        "chroma_added": [path for path in delta["added"] if path.startswith(prefix)],
        "chroma_removed": [path for path in delta["removed"] if path.startswith(prefix)],
        "other_added": [path for path in delta["added"] if not path.startswith(prefix)],
        "other_removed": [path for path in delta["removed"] if not path.startswith(prefix)],
        "directory_mtime_changed": delta["directory_mtime_changed"],
    }


def _metadata_child(chroma_path: Path, result_path: Path, failure_path: Path) -> int:
    stage = "metadata_child_entry"
    try:
        # Import the established runtime composition first to avoid the
        # vectorstore/run-plan package cycle when this child starts cold.
        import backend.app.runtime  # noqa: F401
        import chromadb
        from backend.vectorstore.chroma_store import TEXT_COLLECTION_NAME

        stage = "persistent_client_open"
        client = chromadb.PersistentClient(path=str(chroma_path))
        collection = client.get_collection(TEXT_COLLECTION_NAME, embedding_function=None)
        metadata = dict(collection.metadata or {})
        # Chroma 1.5 refuses to update hnsw:space once a collection exists.
        # Omitting it lets this disposable malformed fixture retain the
        # existing collection configuration while corrupting an identity key.
        metadata.pop("hnsw:space", None)
        metadata["index_fingerprint"] = "0" * 64
        stage = "test_fixture_metadata_mutation"
        collection.modify(metadata=metadata)
        updated = dict(client.get_collection(TEXT_COLLECTION_NAME, embedding_function=None).metadata or {})
        if updated.get("index_fingerprint") != "0" * 64:
            raise RetrievalSmokeError("metadata_fixture_mutation_not_observed")
        _write_json(result_path, {"mutated": True, "collection": TEXT_COLLECTION_NAME})
        return 0
    except BaseException as exc:
        _write_json(failure_path, {
            "stage": stage,
            "exception_type": type(exc).__name__,
            "traceback": traceback.format_exc(),
            "python_version": sys.version,
            "chroma_version": importlib.metadata.version("chromadb"),
        })
        return 2


def _retrieval_child(
    config_path: Path,
    source: Path,
    storage: Path,
    report_path: Path,
    evidence_path: Path,
    negative_fixtures_path: Path,
    result_path: Path,
    failure_path: Path,
) -> int:
    stage = "retrieval_child_entry"
    checkpoints: list[str] = []
    lease_events: list[dict[str, Any]] = []
    count_events: list[tuple[str, int]] = []
    query_calls: dict[str, list[str]] = {"text": [], "image": []}
    active_stage = [stage]

    def checkpoint(value: str) -> None:
        active_stage[0] = value
        checkpoints.append(value)

    try:
        import numpy as np

        from backend.config.schema import ResolvedRunConfig
        from backend.config.writer_lease import WriterLease, WriterLeaseError
        from backend.engine.manifest import build_document_manifest
        from backend.engine.retrieval import (
            RetrievalQueryError,
            RetrievalReportError,
            RetrievalReportFailed,
            RetrievalReportMismatch,
            RetrievalReportPartial,
            RetrievalStoreError,
            _validate_build_report,
            query_id_for,
            retrieve_for_plan,
        )
        from backend.experiments.run_plan import build_run_plan
        from backend.vectorstore.chroma_store import ChromaVectorStore
        import backend.engine.retrieval as retrieval_module

        config = ResolvedRunConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
        config.validate_for_workbench()
        manifest = build_document_manifest(source)
        plan = build_run_plan(config, manifest.content_hash)
        evidence = _read_json(evidence_path)
        if (manifest.content_hash != evidence.get("document_manifest_hash")
                or plan.index_fingerprint != evidence.get("index_fingerprint")):
            raise RetrievalSmokeError("retrieval_child_identity_mismatch")
        report = _validate_build_report(storage, report_path, plan)
        if report.build_id != evidence.get("build_id"):
            raise RetrievalSmokeError("retrieval_child_build_id_mismatch")

        class TextQueryAdapter:
            def __init__(self) -> None:
                self.config = config
                self.model_name = config.text_embed_model

            def embed_query(self, query: str):
                query_calls["text"].append(query)
                return np.asarray([1.0, 0.0, 0.0], dtype=np.float32)

        class ImageQueryAdapter:
            def __init__(self) -> None:
                self.config = config
                self.model_name = config.image_embed_model
                self.pretrained = config.image_embed_pretrained

            def embed_query(self, query: str):
                query_calls["image"].append(query)
                return np.asarray([0.0, 1.0, 0.0], dtype=np.float32)

        model_constructor_calls: list[str] = []

        def forbidden_model_constructor(*_args, **_kwargs):
            model_constructor_calls.append("production_embedding_constructor")
            raise RetrievalSmokeError("production_model_constructor_called")

        retrieval_module.TextEmbeddingAdapter = forbidden_model_constructor
        retrieval_module.ImageEmbeddingAdapter = forbidden_model_constructor

        original_acquire = WriterLease.acquire.__func__
        original_release = WriterLease.release
        original_count = ChromaVectorStore.count
        original_open_existing = ChromaVectorStore.open_existing.__func__
        original_query_text = ChromaVectorStore.query_text
        original_query_image = ChromaVectorStore.query_image
        original_close = ChromaVectorStore.close

        def tracked_acquire(cls, storage_root, fingerprint):
            lease = original_acquire(cls, storage_root, fingerprint)
            lease_events.append({"event": "acquired", "pid": os.getpid(), "thread": threading.get_ident()})
            return lease

        def tracked_release(lease):
            try:
                return original_release(lease)
            finally:
                lease_events.append({"event": "released", "pid": os.getpid(), "thread": threading.get_ident()})

        def tracked_count(store, modality):
            checkpoint(f"count_{modality}")
            count = original_count(store, modality)
            count_events.append((modality, count))
            checkpoint(f"count_{modality}_complete")
            return count

        def tracked_open_existing(cls, storage_root, selected_plan):
            checkpoint("open_existing")
            store = original_open_existing(cls, storage_root, selected_plan)
            checkpoint("open_existing_complete")
            return store

        def tracked_query_text(store, vector, n_results=10):
            checkpoint("query_text")
            result = original_query_text(store, vector, n_results)
            checkpoint("query_text_complete")
            return result

        def tracked_query_image(store, vector, n_results=10):
            checkpoint("query_image")
            result = original_query_image(store, vector, n_results)
            checkpoint("query_image_complete")
            return result

        def tracked_close(store):
            checkpoint("store_close")
            try:
                return original_close(store)
            finally:
                checkpoint("store_close_complete")

        WriterLease.acquire = classmethod(tracked_acquire)
        WriterLease.release = tracked_release
        ChromaVectorStore.count = tracked_count
        ChromaVectorStore.open_existing = classmethod(tracked_open_existing)
        ChromaVectorStore.query_text = tracked_query_text
        ChromaVectorStore.query_image = tracked_query_image
        ChromaVectorStore.close = tracked_close

        query = "synthetic manual retrieval marker"
        checkpoint("positive_retrieval")
        result = retrieve_for_plan(
            query,
            plan,
            storage,
            build_report_path=report_path,
            modalities=("text", "image"),
            text_adapter=TextQueryAdapter(),
            image_adapter=ImageQueryAdapter(),
        )
        expected_id = query_id_for(
            query,
            plan,
            ("text", "image"),
            min(config.text_k, report.text_records_persisted),
            min(config.image_k, report.image_records_persisted),
        )
        if result.query_id != expected_id:
            raise RetrievalSmokeError("retrieval_query_id_mismatch")
        if result.index_fingerprint != plan.index_fingerprint or result.document_manifest_hash != manifest.content_hash:
            raise RetrievalSmokeError("retrieval_result_identity_mismatch")
        if result.modalities != ("text", "image") or not result.text_hits or not result.image_hits:
            raise RetrievalSmokeError("retrieval_expected_modality_hits_missing")
        if query_calls != {"text": [query], "image": [query]}:
            raise RetrievalSmokeError("query_adapter_invocation_mismatch")
        if model_constructor_calls:
            raise RetrievalSmokeError("production_embedding_adapter_was_constructed")
        if count_events != [
            ("text", report.text_records_persisted),
            ("image", report.image_records_persisted),
        ]:
            raise RetrievalSmokeError("persisted_counts_did_not_match_report")
        text_ids = [hit.record_id for hit in result.text_hits]
        image_ids = [hit.record_id for hit in result.image_hits]
        if (not all(isinstance(value, str) and value for value in text_ids + image_ids)
                or len(text_ids) != len(set(text_ids)) or len(image_ids) != len(set(image_ids))):
            raise RetrievalSmokeError("retrieval_record_ids_invalid")
        if not any("Synthetic manual report handoff fixture." in (hit.document or "") for hit in result.text_hits):
            raise RetrievalSmokeError("retrieved_text_did_not_match_fixture")
        if not all(hit.metadata.get("image_id") == hit.record_id for hit in result.image_hits):
            raise RetrievalSmokeError("retrieved_image_metadata_identity_mismatch")
        if not all(hit.metadata.get("chunk_id") == hit.record_id for hit in result.text_hits):
            raise RetrievalSmokeError("retrieved_text_metadata_identity_mismatch")
        for hit in (*result.text_hits, *result.image_hits):
            for value in (hit.semantic_distance, hit.semantic_similarity):
                if value is None or not math.isfinite(float(value)):
                    raise RetrievalSmokeError("retrieval_score_not_finite")
            if hit.semantic_distance is None or hit.semantic_distance > 1e-5:
                raise RetrievalSmokeError("deterministic_vector_distance_unexpected")
        successful_events = list(lease_events)
        if ([event["event"] for event in successful_events] != ["acquired", "released"]
                or successful_events[0]["pid"] != successful_events[1]["pid"]
                or successful_events[0]["thread"] != successful_events[1]["thread"]):
            raise RetrievalSmokeError("normal_retrieval_lease_lifecycle_invalid")
        reservation = storage / ".writer-leases" / plan.index_fingerprint
        if reservation.exists():
            raise RetrievalSmokeError("normal_retrieval_reservation_retained")
        checkpoint("positive_retrieval_complete")
        operation_snapshot = handoff._tree_snapshot(storage)
        operation_payload = {
            "build_id": report.build_id,
            "document_manifest_hash": manifest.content_hash,
            "index_fingerprint": plan.index_fingerprint,
            "report_reference": report_path.relative_to(storage).as_posix(),
            "report_text_records": report.text_records_persisted,
            "report_image_records": report.image_records_persisted,
            "observed_text_records": count_events[0][1],
            "observed_image_records": count_events[1][1],
            "query_id": result.query_id,
            "modalities": list(result.modalities),
            "text_record_ids": text_ids,
            "image_record_ids": image_ids,
            "text_documents": [hit.document for hit in result.text_hits],
            "text_metadata_ids": [hit.metadata.get("chunk_id") for hit in result.text_hits],
            "image_metadata_ids": [hit.metadata.get("image_id") for hit in result.image_hits],
            "text_distances": [hit.semantic_distance for hit in result.text_hits],
            "image_distances": [hit.semantic_distance for hit in result.image_hits],
            "query_adapter_calls": query_calls,
            "production_model_constructor_calls": model_constructor_calls,
            "positive_lease_events": successful_events,
            "reservation_absent_after_close": True,
            "operation_snapshot": {key: list(value) for key, value in operation_snapshot.items()},
            "python_version": sys.version,
            "chroma_version": importlib.metadata.version("chromadb"),
        }

        fixtures = _read_json(negative_fixtures_path)
        negative_results: dict[str, Any] = {}
        current_counts_before = len(count_events)

        def call_retrieval(selected_storage: Path, selected_report: Path, *, text_adapter=None):
            return retrieve_for_plan(
                query,
                plan,
                selected_storage,
                build_report_path=selected_report,
                modalities=("text",),
                text_adapter=text_adapter,
            )

        def expect_failure(
            name: str,
            operation,
            expected_type: type[BaseException],
            *,
            calls_before: int | None = None,
        ) -> BaseException:
            checkpoint(name)
            before_calls = len(query_calls["text"])
            try:
                operation()
            except BaseException as exc:
                if not isinstance(exc, expected_type):
                    raise RetrievalSmokeError(f"{name}_wrong_exception_{type(exc).__name__}") from exc
                if calls_before is not None and len(query_calls["text"]) - before_calls != calls_before:
                    raise RetrievalSmokeError(f"{name}_query_invocation_count_invalid") from exc
                negative_results[name] = {
                    "status": "rejected",
                    "exception_type": type(exc).__name__,
                    "cause_types": [type(cause).__name__ for cause in _cause_chain(exc)],
                }
                return exc
            raise RetrievalSmokeError(f"{name}_unexpected_success")

        def _cause_chain(exc: BaseException) -> list[BaseException]:
            result_chain: list[BaseException] = []
            current = exc.__cause__
            while current is not None and len(result_chain) < 8:
                result_chain.append(current)
                current = current.__cause__
            return result_chain

        query_calls["text"].clear()
        count_start = len(count_events)
        expect_failure(
            "report_identity_mismatch",
            lambda: call_retrieval(Path(fixtures["storage"]), Path(fixtures["wrong_identity_report"])),
            RetrievalReportMismatch,
            calls_before=0,
        )
        if len(count_events) != count_start:
            raise RetrievalSmokeError("report_identity_mismatch_opened_store")

        for name, report_key, expected in (
            ("failed_report", "failed_report", RetrievalReportFailed),
            ("partial_report", "partial_report", RetrievalReportPartial),
        ):
            count_start = len(count_events)
            expect_failure(
                name,
                lambda key=report_key: call_retrieval(Path(fixtures["storage"]), Path(fixtures[key])),
                expected,
                calls_before=0,
            )
            if len(count_events) != count_start:
                raise RetrievalSmokeError(f"{name}_opened_store")

        count_calls_before = len(count_events)
        expect_failure(
            "missing_index_directory",
            lambda: call_retrieval(Path(fixtures["missing_storage"]), Path(fixtures["missing_index_report"])),
            RetrievalStoreError,
            calls_before=0,
        )
        if (Path(fixtures["missing_storage"]) / "indexes").exists() or len(count_events) != count_calls_before:
            raise RetrievalSmokeError("missing_index_was_created_or_counted")

        count_calls_before = len(count_events)
        expect_failure(
            "malformed_collection_metadata",
            lambda: call_retrieval(Path(fixtures["metadata_storage"]), Path(fixtures["metadata_report"])),
            RetrievalStoreError,
            calls_before=0,
        )
        if len(count_events) != count_calls_before:
            raise RetrievalSmokeError("malformed_metadata_reached_count")
        if (Path(fixtures["metadata_storage"]) / ".writer-leases" / plan.index_fingerprint).exists():
            raise RetrievalSmokeError("malformed_metadata_left_reservation")

        mismatch_count_calls = list(count_events)
        mismatch_text_calls = list(query_calls["text"])
        expect_failure(
            "persisted_count_mismatch",
            lambda: call_retrieval(Path(fixtures["storage"]), Path(fixtures["count_mismatch_report"])),
            RetrievalStoreError,
            calls_before=0,
        )
        if count_events != mismatch_count_calls + [
            ("text", report.text_records_persisted),
            ("image", report.image_records_persisted),
        ]:
            raise RetrievalSmokeError("count_mismatch_did_not_compare_both_collections")
        if query_calls["text"] != mismatch_text_calls:
            raise RetrievalSmokeError("count_mismatch_invoked_query_adapter")
        if (Path(fixtures["storage"]) / ".writer-leases" / plan.index_fingerprint).exists():
            raise RetrievalSmokeError("count_mismatch_left_reservation")

        owner = WriterLease.acquire(Path(fixtures["storage"]), plan.index_fingerprint)
        owner_path = Path(fixtures["storage"]) / ".writer-leases" / plan.index_fingerprint / "owner"
        owner_before = owner_path.read_bytes()
        try:
            conflict = expect_failure(
                "active_writer_reservation",
                lambda: call_retrieval(Path(fixtures["storage"]), Path(fixtures["valid_report"])),
                RetrievalStoreError,
                calls_before=0,
            )
            cause_types = [type(cause).__name__ for cause in _cause_chain(conflict)]
            if "WriterLeaseError" not in cause_types or owner_path.read_bytes() != owner_before:
                raise RetrievalSmokeError("writer_conflict_did_not_preserve_owner_evidence")
            if not owner_path.is_file():
                raise RetrievalSmokeError("writer_conflict_removed_owner_evidence")
        finally:
            owner.release()
        if (Path(fixtures["storage"]) / ".writer-leases" / plan.index_fingerprint).exists():
            raise RetrievalSmokeError("owned_test_reservation_not_released")

        class UnavailableTextAdapter(TextQueryAdapter):
            def embed_query(self, selected_query: str):
                query_calls["text"].append(selected_query)
                raise RuntimeError("synthetic_query_adapter_unavailable")

        calls_before_error = len(query_calls["text"])
        expect_failure(
            "query_adapter_failure",
            lambda: call_retrieval(
                Path(fixtures["storage"]),
                Path(fixtures["valid_report"]),
                text_adapter=UnavailableTextAdapter(),
            ),
            RetrievalQueryError,
            calls_before=1,
        )
        if len(query_calls["text"]) != calls_before_error + 1:
            raise RetrievalSmokeError("query_adapter_failure_not_invoked")
        if (Path(fixtures["storage"]) / ".writer-leases" / plan.index_fingerprint).exists():
            raise RetrievalSmokeError("query_error_left_reservation")

        marker_path = Path(fixtures["storage"]) / "indexes" / plan.index_fingerprint / ".fresh-build-incomplete"
        marker_path.write_text(plan.index_fingerprint, encoding="ascii")
        marker_bytes = marker_path.read_bytes()
        expect_failure(
            "incomplete_index_marker",
            lambda: call_retrieval(Path(fixtures["storage"]), Path(fixtures["valid_report"])),
            RetrievalStoreError,
            calls_before=0,
        )
        if marker_path.read_bytes() != marker_bytes:
            raise RetrievalSmokeError("incomplete_marker_was_changed")
        if (Path(fixtures["storage"]) / ".writer-leases" / plan.index_fingerprint).exists():
            raise RetrievalSmokeError("incomplete_marker_left_reservation")

        if len(count_events) < current_counts_before:
            raise RetrievalSmokeError("count_instrumentation_regressed")
        operation_payload["failure_cases"] = negative_results
        operation_payload["negative_fixture_reports_unchanged"] = all(
            _digest(Path(path)) == digest for path, digest in fixtures["report_digests"].items()
        )
        operation_payload["index_manifest_unchanged_in_negative_store"] = (
            _digest(Path(fixtures["storage"]) / "indexes" / plan.index_fingerprint / "index-manifest.json")
            == fixtures["index_manifest_digest"]
        )
        if not operation_payload["negative_fixture_reports_unchanged"] or not operation_payload["index_manifest_unchanged_in_negative_store"]:
            raise RetrievalSmokeError("negative_retrieval_mutated_report_or_manifest")
        _write_json(result_path, operation_payload)
        return 0
    except BaseException as exc:
        try:
            storage_value = locals().get("storage")
            fingerprint_value = locals().get("plan").index_fingerprint if locals().get("plan") else None
            lease_state = None
            if storage_value is not None and fingerprint_value:
                lease_state = (Path(storage_value) / ".writer-leases" / fingerprint_value).exists()
            _write_json(failure_path, {
                "stage": active_stage[0],
                "exception_type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
                "last_checkpoints": checkpoints[-30:],
                "lease_state": lease_state,
                "filesystem_snapshot": {
                    key: list(value)
                    for key, value in handoff._tree_snapshot(Path(storage_value)).items()
                } if storage_value is not None and Path(storage_value).exists() else None,
                "python_version": sys.version,
                "chroma_version": importlib.metadata.version("chromadb"),
            })
        except BaseException:
            pass
        return 2


def _run_child(
    command: list[str],
    *,
    log_path: Path,
    timeout: float,
    storage_root: Path,
    fingerprint: str,
) -> tuple[int, str]:
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    environment = handoff._clear_workbench_environment()
    environment.pop("PYTHONPATH", None)
    environment.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "ANONYMIZED_TELEMETRY": "FALSE",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
    })
    with log_path.open("wb") as log:
        child = subprocess.Popen(
            command,
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
        try:
            child.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            child.kill()
            child.wait(timeout=10)
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
            _write_json(log_path.parent / "retrieval-child-timeout.json", {
                "child_pid": child.pid,
                "child_exit_code": child.returncode,
                "child_reaped": child.poll() is not None,
                "timeout_seconds": timeout,
                "stage": "retrieval_child_timeout",
                "lease_reservation_present": (
                    storage_root / ".writer-leases" / fingerprint
                ).exists(),
                "filesystem_snapshot_after_child_exit": {
                    key: list(value) for key, value in handoff._tree_snapshot(storage_root).items()
                },
                "python_version": sys.version,
                "chroma_version": importlib.metadata.version("chromadb"),
                "child_log_tail": tail,
            })
            raise RetrievalSmokeError("retrieval_child_timeout") from exc
        except BaseException:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
            raise
        if child.poll() is None:
            raise RetrievalSmokeError("retrieval_child_not_reaped")
    tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
    return int(child.returncode), tail


def _prepare_negative_fixtures(root: Path, storage: Path, report_path: Path, build: dict[str, Any]) -> dict[str, Any]:
    fingerprint = build["index_fingerprint"]
    index_source = storage / "indexes" / fingerprint
    negative_storage = root / "negative-store"
    negative_storage.mkdir()
    negative_index_parent = negative_storage / "indexes"
    negative_index_parent.mkdir()
    shutil.copytree(index_source, negative_index_parent / fingerprint)

    wrong_fingerprint = "0" * 64
    if wrong_fingerprint == fingerprint:
        wrong_fingerprint = "f" * 64
    paths = {
        "storage": negative_storage,
        "valid_report": _write_report_variant(report_path, negative_storage, "a" * 32),
        "wrong_identity_report": _write_report_variant(
            report_path, negative_storage, "b" * 32,
            updates={"index_fingerprint": wrong_fingerprint},
        ),
        "failed_report": _write_report_variant(
            report_path, negative_storage, "c" * 32, updates={"status": "failed", "partial_persistence": False},
        ),
        "partial_report": _write_report_variant(
            report_path, negative_storage, "d" * 32, updates={"status": "completed", "partial_persistence": True},
        ),
        "count_mismatch_report": _write_report_variant(
            report_path, negative_storage, "e" * 32,
            # Text is checked first by the established retrieval pipeline;
            # mismatch the second modality so both actual counts are observed.
            updates={"image_records_persisted": int(build["reported_image_records"]) + 1},
        ),
    }

    missing_storage = root / "missing-index-store"
    missing_storage.mkdir()
    paths["missing_storage"] = missing_storage
    paths["missing_index_report"] = _write_report_variant(report_path, missing_storage, "f" * 32)

    metadata_storage = root / "metadata-mismatch-store"
    metadata_storage.mkdir()
    metadata_indexes = metadata_storage / "indexes"
    metadata_indexes.mkdir()
    shutil.copytree(index_source, metadata_indexes / fingerprint)
    paths["metadata_storage"] = metadata_storage
    paths["metadata_report"] = _write_report_variant(report_path, metadata_storage, "1" * 32)

    report_paths = [
        paths[key] for key in (
            "valid_report", "wrong_identity_report", "failed_report", "partial_report",
            "count_mismatch_report", "missing_index_report", "metadata_report",
        )
    ]
    paths["report_digests"] = {str(path): _digest(path) for path in report_paths}
    paths["index_manifest_digest"] = _digest(
        negative_storage / "indexes" / fingerprint / "index-manifest.json"
    )
    metadata_chroma_path = metadata_storage / "indexes" / fingerprint / "chroma"
    metadata_result = root / "metadata-mutation-result.json"
    metadata_failure = root / "metadata-mutation-failure.json"
    command = [
        sys.executable, str(Path(__file__).resolve()), "--metadata-child",
        str(metadata_chroma_path), str(metadata_result), str(metadata_failure),
    ]
    metadata_env = handoff._clear_workbench_environment()
    metadata_env.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "ANONYMIZED_TELEMETRY": "FALSE",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
    })
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=metadata_env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0 or not metadata_result.is_file():
        details = _read_json(metadata_failure) if metadata_failure.exists() else {}
        raise RetrievalSmokeError("metadata_negative_fixture_failed_" + str(details.get("stage", "unknown")))
    paths["metadata_fixture_mutated"] = _read_json(metadata_result).get("mutated") is True
    if not paths["metadata_fixture_mutated"]:
        raise RetrievalSmokeError("metadata_negative_fixture_not_mutated")
    return {key: str(value) if isinstance(value, Path) else value for key, value in paths.items()}


def _run_smoke(root: Path, timeout: float) -> dict[str, Any]:
    from backend.config.schema import ResolvedRunConfig
    from backend.engine.manifest import build_document_manifest
    from backend.engine.retrieval import _validate_build_report
    from backend.experiments.run_plan import build_run_plan

    parent_environment_before = {
        key: value for key, value in os.environ.items()
        if key.startswith("RAG_WORKBENCH_")
    }
    source = root / "pdf-source"
    source.mkdir()
    storage = root / "index-storage"
    storage.mkdir()
    config = handoff._make_config(source, root)
    values = config.model_dump(mode="python")
    values.update({
        "retrieval_mode": "semantic",
        "adaptive_weighting": False,
        "use_weighted_fusion": False,
    })
    config = ResolvedRunConfig.model_validate(values)
    config.validate_for_workbench()
    config_path = root / "resolved-config.json"
    config_path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    source_pdf = source / "synthetic.pdf"
    handoff._write_pdf(source_pdf, "Synthetic manual report handoff fixture.")
    manifest = build_document_manifest(source)
    plan = build_run_plan(config, manifest.content_hash)

    build_result_path = root / "build-result.json"
    build_failure_path = root / "build-failure.json"
    build_log_path = root / "build-child.log"
    build_command = [
        sys.executable,
        str(Path(handoff.__file__).resolve()),
        "--build-child",
        str(config_path),
        str(source),
        str(storage),
        str(build_result_path),
        str(build_failure_path),
    ]
    child_env = handoff._clear_workbench_environment()
    child_env.pop("PYTHONPATH", None)
    child_env.update({"PYTHONDONTWRITEBYTECODE": "1", "ANONYMIZED_TELEMETRY": "FALSE",
                      "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1"})
    with build_log_path.open("wb") as build_log:
        build_child = subprocess.run(
            build_command, cwd=ROOT, env=child_env, stdout=build_log,
            stderr=subprocess.STDOUT, timeout=max(120, timeout), check=False,
        )
    if build_child.returncode != 0 or not build_result_path.is_file():
        failure = _read_json(build_failure_path) if build_failure_path.exists() else {}
        raise RetrievalSmokeError("fresh_build_failed_" + str(failure.get("stage", "unknown")))
    build = _read_json(build_result_path)
    report_path = storage / Path(*build["report_reference"].split("/"))
    report = _validate_build_report(storage, report_path, plan)
    if (report.build_id != build.get("build_id")
            or report.text_records_persisted != build.get("reported_text_records")
            or report.image_records_persisted != build.get("reported_image_records")):
        raise RetrievalSmokeError("genuine_build_report_identity_or_counts_invalid")
    if report.text_records_persisted <= 0 or report.image_records_persisted <= 0:
        raise RetrievalSmokeError("fixture_did_not_produce_both_modalities")
    report_before = report_path.read_bytes()
    index_manifest = storage / "indexes" / plan.index_fingerprint / "index-manifest.json"
    index_manifest_before = index_manifest.read_bytes()

    storage_before_preflight = handoff._tree_snapshot(storage)
    preflight = handoff._run_preflight(
        root,
        config_path=config_path,
        source=source,
        storage=storage,
        build_id=build["build_id"],
        manifest_hash=manifest.content_hash,
        fingerprint=plan.index_fingerprint,
        label="retrieval_fixture",
        timeout=timeout,
    )
    if (preflight.get("result") != "report_compatible_for_manual_review"
            or preflight.get("build_id") != build["build_id"]
            or preflight.get("report_reference") != build["report_reference"]
            or preflight.get("document_manifest_hash") != manifest.content_hash
            or preflight.get("index_fingerprint") != plan.index_fingerprint
            or preflight.get("reported_text_records") != report.text_records_persisted
            or preflight.get("reported_image_records") != report.image_records_persisted
            or preflight.get("check_level") != "report"
            or preflight.get("store_checked") is not False):
        raise RetrievalSmokeError("explicit_preflight_rejected_or_misidentified_report")
    if storage_before_preflight != handoff._tree_snapshot(storage):
        raise RetrievalSmokeError("preflight_modified_storage")

    runtime_env, guard_ready, guard_events, token, port = handoff._runtime_environment(
        root, source=source, storage=storage, report_path=report_path, guard_label="retrieval"
    )
    runtime_log = root / "ordinary-runtime.log"
    runtime_process = runtime_log_stream = None
    try:
        runtime_process, runtime_log_stream, _ = handoff._start_runtime(runtime_env, runtime_log, timeout)
        status, manifest_response = handoff._request(port, "/api/v1/documents/manifest", token=token)
        if status != 200 or manifest_response.get("content_hash") != manifest.content_hash:
            raise RetrievalSmokeError("ordinary_runtime_manifest_handoff_failed")
        request_base = {
            "resolved_config": config.model_dump(mode="json"),
            "expected_manifest_hash": manifest.content_hash,
        }
        status, plan_response = handoff._request(port, "/api/v1/run-plans", token=token, body=request_base)
        if (status != 200
                or plan_response.get("run_plan", {}).get("index_fingerprint") != plan.index_fingerprint):
            raise RetrievalSmokeError("ordinary_runtime_plan_handoff_failed")
        status, readiness = handoff._request(
            port,
            "/api/v1/index/readiness",
            token=token,
            body={**request_base, "workflow": "evaluation"},
        )
        if (status != 200 or readiness.get("status") != "report_match"
                or readiness.get("build_id") != build["build_id"]
                or readiness.get("reported_text_records") != report.text_records_persisted
                or readiness.get("reported_image_records") != report.image_records_persisted
                or readiness.get("observed_text_records") is not None
                or readiness.get("observed_image_records") is not None
                or readiness.get("check_level") != "report"
                or readiness.get("store_checked") is not False):
            raise RetrievalSmokeError("ordinary_runtime_report_readiness_invalid")
        if guard_events.exists() or not guard_ready.is_file():
            raise RetrievalSmokeError("ordinary_runtime_opened_chroma_or_started_execution")
        status, jobs = handoff._request(port, "/api/v1/jobs", token=token)
        if status != 200 or jobs.get("jobs") != []:
            raise RetrievalSmokeError("ordinary_runtime_created_experiment_job")
        handoff._stop_runtime(runtime_process, runtime_log_stream, require_graceful=True)
        runtime_process = runtime_log_stream = None
    finally:
        if runtime_process is not None and runtime_log_stream is not None:
            handoff._stop_runtime(runtime_process, runtime_log_stream, require_graceful=False)
    if storage_before_preflight != handoff._tree_snapshot(storage):
        raise RetrievalSmokeError("report_only_runtime_modified_chroma_storage")

    negative_fixtures = _prepare_negative_fixtures(root, storage, report_path, build)
    negative_fixtures_path = root / "negative-fixtures.json"
    _write_json(negative_fixtures_path, negative_fixtures)
    evidence_path = root / "build-evidence.json"
    _write_json(evidence_path, {
        "build_id": build["build_id"],
        "document_manifest_hash": manifest.content_hash,
        "index_fingerprint": plan.index_fingerprint,
        "report_text_records": report.text_records_persisted,
        "report_image_records": report.image_records_persisted,
    })

    retrieval_before = handoff._tree_snapshot(storage)
    result_path = root / "retrieval-result.json"
    failure_path = root / "retrieval-failure.json"
    retrieval_log = root / "retrieval-child.log"
    retrieval_command = [
        sys.executable, str(Path(__file__).resolve()), "--retrieval-child",
        str(config_path), str(source), str(storage), str(report_path),
        str(evidence_path), str(negative_fixtures_path), str(result_path), str(failure_path),
    ]
    return_code, log_tail = _run_child(
        retrieval_command,
        log_path=retrieval_log,
        timeout=max(90, timeout),
        storage_root=storage,
        fingerprint=plan.index_fingerprint,
    )
    if return_code != 0 or not result_path.is_file():
        failure = _read_json(failure_path) if failure_path.exists() else {}
        failure["child_exit_code"] = return_code
        failure["child_reaped"] = True
        failure["filesystem_snapshot_after_child_exit"] = {
            key: list(value) for key, value in handoff._tree_snapshot(storage).items()
        }
        failure["lease_reservation_present_after_child_exit"] = (
            storage / ".writer-leases" / plan.index_fingerprint
        ).exists()
        _write_json(failure_path, failure)
        raise RetrievalSmokeError(
            "retrieval_child_failed_" + str(failure.get("stage", "unknown"))
            + "_" + str(failure.get("exception_type", "unknown"))
        )
    if log_tail:
        (root / "retrieval-child-log-tail.txt").write_text(log_tail[-4000:], encoding="utf-8")
    result = _read_json(result_path)
    retrieval_after_exit = handoff._tree_snapshot(storage)
    operation_snapshot = {
        key: tuple(value) for key, value in result.get("operation_snapshot", {}).items()
    }
    if not operation_snapshot:
        raise RetrievalSmokeError("post-operation_snapshot_missing")
    operation_delta = _snapshot_delta(retrieval_before, operation_snapshot)
    exit_delta = _snapshot_delta(retrieval_before, retrieval_after_exit)
    side_effects = {
        "after_wrapper_close": _summarize_chroma_delta(operation_delta, plan.index_fingerprint),
        "after_process_exit": _summarize_chroma_delta(exit_delta, plan.index_fingerprint),
    }
    if report_path.read_bytes() != report_before:
        raise RetrievalSmokeError("retrieval_rewrote_selected_build_report")
    if index_manifest.read_bytes() != index_manifest_before:
        raise RetrievalSmokeError("retrieval_rewrote_index_identity_manifest")
    if (storage / ".writer-leases" / plan.index_fingerprint).exists():
        raise RetrievalSmokeError("retrieval_child_left_writer_reservation")
    if result.get("reservation_absent_after_close") is not True:
        raise RetrievalSmokeError("retrieval_child_did_not_confirm_lease_release")
    if result.get("negative_fixture_reports_unchanged") is not True:
        raise RetrievalSmokeError("negative_case_rewrote_report_evidence")
    if parent_environment_before != {
        key: value for key, value in os.environ.items()
        if key.startswith("RAG_WORKBENCH_")
    }:
        raise RetrievalSmokeError("persistent_runtime_environment_changed")

    return {
        "build_id": build["build_id"],
        "report_reference": build["report_reference"],
        "manifest_hash": manifest.content_hash,
        "index_fingerprint": plan.index_fingerprint,
        "report_counts": {
            "text": report.text_records_persisted,
            "image": report.image_records_persisted,
        },
        "observed_counts": {
            "text": result.get("observed_text_records"),
            "image": result.get("observed_image_records"),
        },
        "text_query": {
            "query_id": result.get("query_id"),
            "record_ids": result.get("text_record_ids"),
            "fixture_text_matched": any(
                "Synthetic manual report handoff fixture." in value
                for value in result.get("text_documents", []) if isinstance(value, str)
            ),
        },
        "image_query": {
            "record_ids": result.get("image_record_ids"),
            "metadata_ids_match": result.get("image_record_ids") == result.get("image_metadata_ids"),
        },
        "failure_cases": result.get("failure_cases", {}),
        "production_model_constructor_calls": result.get("production_model_constructor_calls"),
        "chroma_side_effects": side_effects,
        "report_and_index_manifest_bytes_unchanged": True,
        "lease_released_on_retrieval_thread": result.get("positive_lease_events"),
        "python_version": result.get("python_version"),
        "chroma_version": result.get("chroma_version"),
    }


def run_smoke(*, timeout: float = 45) -> dict[str, Any]:
    if not 5 <= timeout <= 120:
        raise RetrievalSmokeError("timeout_invalid")
    root = Path(tempfile.mkdtemp(prefix="rag-manual-retrieval-"))
    try:
        result = _run_smoke(root, timeout)
    except BaseException as exc:
        diagnostics = root / "smoke-failure.json"
        if not diagnostics.exists():
            _write_json(diagnostics, {
                "exception_type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            })
        if isinstance(exc, RetrievalSmokeError):
            exc.diagnostics_path = root
            raise
        raise RetrievalSmokeError("unexpected_" + type(exc).__name__, root) from exc
    shutil.rmtree(root)
    return result


def _main(argv: list[str]) -> int:
    if argv and argv[0] == "--metadata-child":
        if len(argv) != 4:
            return 2
        return _metadata_child(Path(argv[1]), Path(argv[2]), Path(argv[3]))
    if argv and argv[0] == "--retrieval-child":
        if len(argv) != 9:
            return 2
        return _retrieval_child(*(Path(item) for item in argv[1:]))
    import argparse

    parser = argparse.ArgumentParser(description="Run an isolated real-Chroma retrieval smoke test.")
    parser.add_argument("--timeout", type=float, default=45, help="bounded child/runtime timeout in seconds (5–120)")
    args = parser.parse_args(argv)
    try:
        result = run_smoke(timeout=args.timeout)
    except RetrievalSmokeError as exc:
        print(f"manual_report_retrieval_smoke_failed: {exc.code}", file=sys.stderr)
        if exc.diagnostics_path is not None:
            print(f"temporary diagnostics retained: {exc.diagnostics_path}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    print("Explicit retrieval exercised real Chroma reads; filesystem effects are reported above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
