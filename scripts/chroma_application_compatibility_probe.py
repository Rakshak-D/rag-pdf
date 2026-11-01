"""One-attempt-per-version application-boundary Chroma compatibility probe.

This test-only runner uses disposable temporary storage and the real
ChromaVectorStore / retrieve_for_plan implementations. It does not start a
Chroma server, load a production model, or open an existing user index.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import types
import uuid
from typing import Any, Callable


SCHEMA_VERSION = 2
TEXT_IDS = ["text-1", "text-2"]
IMAGE_IDS = ["image-1"]
TEXT_QUERY = "synthetic deterministic retrieval query"
EXPECTED_TEXT_VECTOR = [1.0, 0.0, 0.0]
EXPECTED_IMAGE_VECTOR = [0.0, 0.0, 1.0]
APPLICATION_STORAGE = "index-storage"
OWNERSHIP_MARKER = ".phase-613c-probe-owned"
OWNERSHIP_MARKER_VALUE = "rag-pdf-phase-6.13c-disposable-fixture-v1\n"
EXPECTED_QUERY_MODALITIES = ["text", "image", "text"]
_RESERVED_EVENT_FIELDS = {"stage", "monotonic_ns", "pid", "thread_id"}
_NETWORK_ATTEMPTS: list[str] = []


class ProbeInstrumentationError(RuntimeError):
    """Observer failure that invalidates an otherwise executed probe attempt."""


def _json_write(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _safe_exception(exc: BaseException) -> dict[str, Any]:
    def safe_message(value: BaseException, limit: int) -> str:
        try:
            return str(value)[:limit]
        except BaseException:
            return "<exception message unavailable>"

    chain: list[dict[str, str]] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        chain.append({"type": type(current).__name__, "message": safe_message(current, 1000)})
        current = current.__cause__
    return {
        "type": type(exc).__name__,
        "message": safe_message(exc, 2000),
        "explicit_cause_chain": chain,
    }


def _event(events: list[dict[str, Any]], stage: str, **fields: Any) -> None:
    collision = _RESERVED_EVENT_FIELDS.intersection(fields)
    if collision:
        raise TypeError("reserved event fields supplied as payload: " + ", ".join(sorted(collision)))
    events.append({
        "stage": stage,
        "monotonic_ns": time.monotonic_ns(),
        "pid": os.getpid(),
        "thread_id": threading.get_ident(),
        **fields,
    })


def _record_query_event(events: list[dict[str, Any]], event_stage: str, observation: dict[str, Any]) -> None:
    """Record query details while keeping envelope fields authoritative."""
    fields = {key: value for key, value in observation.items() if key not in _RESERVED_EVENT_FIELDS}
    _event(events, event_stage, **fields)


def _record_diagnostic_error(
    errors: list[dict[str, Any]],
    stage: str,
    exc: BaseException,
) -> None:
    errors.append({"origin": "instrumentation", "stage": stage, "exception": _safe_exception(exc)})


def _safe_event_record(
    events: list[dict[str, Any]],
    instrumentation_errors: list[dict[str, Any]],
    stage: str,
    *,
    recorder: Callable[..., None] | None = None,
    **fields: Any,
) -> None:
    try:
        (recorder or _event)(events, stage, **fields)
    except BaseException as exc:
        _record_diagnostic_error(instrumentation_errors, stage, exc)


def _safe_traceback_text(
    exc: BaseException,
    instrumentation_errors: list[dict[str, Any]],
    *,
    stage: str,
) -> str:
    try:
        return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    except BaseException as formatting_error:
        _record_diagnostic_error(instrumentation_errors, stage, formatting_error)
        return "<traceback formatting failed; original exception object retained>"


def _safe_query_event(
    events: list[dict[str, Any]],
    instrumentation_errors: list[dict[str, Any]],
    event_stage: str,
    observation: dict[str, Any],
    *,
    recorder: Callable[[list[dict[str, Any]], str, dict[str, Any]], None] | None = None,
) -> None:
    try:
        (recorder or _record_query_event)(events, event_stage, observation)
    except BaseException as exc:
        _record_diagnostic_error(instrumentation_errors, event_stage, exc)


def _response_evidence(response: Any) -> tuple[list[str], list[float], bool]:
    """Extract bounded identity/distance evidence without changing the result."""
    try:
        ids_outer = response.get("ids")
        distances_outer = response.get("distances")
        ids = list(ids_outer[0]) if isinstance(ids_outer, (list, tuple)) and ids_outer else []
        raw_distances = list(distances_outer[0]) if isinstance(distances_outer, (list, tuple)) and distances_outer else []
        distances = [float(value) for value in raw_distances]
        finite = len(ids) == len(distances) and all(math.isfinite(value) for value in distances)
        if not all(isinstance(value, str) for value in ids):
            return [], distances, False
        return ids[:64], distances[:64], finite
    except BaseException:
        return [], [], False


def _invoke_observed_query(
    query_callable: Callable[[], Any],
    *,
    events: list[dict[str, Any]],
    instrumentation_errors: list[dict[str, Any]],
    query_calls: list[dict[str, Any]],
    ordinal: int,
    modality: str,
    collection_name: str,
    collection_id: str,
    n_results: Any,
    recorder: Callable[[list[dict[str, Any]], str, dict[str, Any]], None] | None = None,
) -> Any:
    """Call the underlying collection query once; observer errors stay separate."""
    call = {
        "ordinal": ordinal,
        "modality": modality,
        "collection_name": collection_name,
        "collection_id": collection_id,
        "method_entered": True,
        "completed": False,
        "n_results": n_results,
    }
    query_calls.append(call)
    _safe_query_event(events, instrumentation_errors, "query_started", call, recorder=recorder)
    try:
        response = query_callable()
    except BaseException as exc:
        call["exception"] = _safe_exception(exc)
        _safe_query_event(events, instrumentation_errors, "query_failed", call, recorder=recorder)
        raise
    call["completed"] = True
    returned_ids, distances, finite = _response_evidence(response)
    call["returned_ids"] = returned_ids
    call["distances"] = distances
    call["distances_finite"] = finite
    _safe_query_event(events, instrumentation_errors, "query_completed", call, recorder=recorder)
    return response


def _validate_writer_result(
    evidence: Any,
    *,
    attempt_id: str,
    chroma_version: str,
    process: dict[str, Any],
) -> tuple[bool, str]:
    if not isinstance(evidence, dict):
        return False, "writer_result_missing_or_malformed"
    required = (
        evidence.get("schema_version") == SCHEMA_VERSION
        and evidence.get("attempt_id") == attempt_id
        and evidence.get("role") == "writer"
        and evidence.get("status") == "passed"
        and evidence.get("final_classification") == "passed"
        and evidence.get("evidence_complete") is True
        and evidence.get("writer_completed") is True
        and evidence.get("lease_released") is True
        and evidence.get("chroma_version") == chroma_version
        and type(evidence.get("pid")) is int
        and evidence.get("pid") == process.get("python_pid")
        and evidence.get("python_version") == "3.10.11"
        and evidence.get("reported_text_records") == 2
        and evidence.get("reported_image_records") == 1
        and evidence.get("writer_reservation_present_after_close") is False
        and evidence.get("fresh_build_marker_present_after_finalize") is False
        and isinstance(evidence.get("report_sha256"), str)
        and len(evidence["report_sha256"]) == 64
        and isinstance(evidence.get("index_manifest_sha256"), str)
        and len(evidence["index_manifest_sha256"]) == 64
        and evidence.get("network_attempts") == []
        and evidence.get("instrumentation_errors") == []
        and process.get("exit_code") == 0
        and process.get("timed_out") is False
        and process.get("worker_failure") is None
    )
    return (True, "passed") if required else (False, "writer_evidence_incomplete_or_contradictory")


def _validate_reader_result(
    evidence: Any,
    *,
    attempt_id: str,
    chroma_version: str,
    writer_process: dict[str, Any],
    reader_process: dict[str, Any],
) -> tuple[bool, str]:
    if not isinstance(evidence, dict):
        return False, "reader_result_missing_or_malformed"
    calls = evidence.get("query_calls")
    requests = evidence.get("query_requests")
    reports = evidence.get("report_checks")
    counts = evidence.get("count_observations")
    closes = evidence.get("close_observations")
    lease_conflicts = evidence.get("exclusive_lease_conflicts")
    collections = evidence.get("collection_metadata")
    calls_are_objects = isinstance(calls, list) and all(isinstance(call, dict) for call in calls)
    requests_are_objects = isinstance(requests, list) and all(isinstance(item, dict) for item in requests)
    reports_are_objects = isinstance(reports, list) and all(isinstance(item, dict) for item in reports)
    counts_are_objects = isinstance(counts, list) and all(isinstance(item, dict) for item in counts)
    closes_are_objects = isinstance(closes, list) and all(isinstance(item, dict) for item in closes)
    conflicts_are_objects = isinstance(lease_conflicts, list) and all(isinstance(item, dict) for item in lease_conflicts)
    collections_are_objects = isinstance(collections, dict) and all(
        isinstance(collections.get(modality), dict) for modality in ("text", "image")
    )
    call_modalities = [call.get("modality") for call in calls] if calls_are_objects else []
    request_modalities = [item.get("modality") for item in requests] if requests_are_objects else []
    writer_evidence = writer_process.get("worker_result")
    expected_n_results = [2, 1, 2]
    report_evidence_valid = (
        reports_are_objects
        and len(reports) == 3
        and all(
            item.get("status") == "completed"
            and item.get("build_id") == writer_evidence.get("build_id")
            and item.get("index_fingerprint") == evidence.get("index_fingerprint")
            and item.get("document_manifest_hash") == evidence.get("document_manifest_hash")
            and (item.get("text_count"), item.get("image_count")) == (2, 1)
            for item in reports
        )
    ) if isinstance(writer_evidence, dict) else False
    count_evidence_valid = (
        counts_are_objects
        and len(counts) == 6
        and all(item.get("count") == (2 if item.get("modality") == "text" else 1)
                for item in counts)
        and [item.get("modality") for item in counts] == ["text", "image"] * 3
        and [item.get("open_ordinal") for item in counts] == [1, 1, 2, 2, 3, 3]
    )
    close_evidence_valid = (
        closes_are_objects
        and len(closes) == 3
        and all(
            item.get("released") is True and item.get("reservation_after") is False
            for item in closes
        )
    )
    conflict_evidence_valid = (
        conflicts_are_objects and len(lease_conflicts) == 3
        and all(item.get("error") == "writer_lease_busy" for item in lease_conflicts)
    )
    collection_metadata_valid = False
    if collections_are_objects and isinstance(writer_evidence, dict):
        collection_metadata_valid = True
        for modality in ("text", "image"):
            item = collections[modality]
            metadata = item.get("metadata")
            expected_metadata = {
                "store_schema_version": "1",
                "index_fingerprint": writer_evidence.get("index_fingerprint"),
                "document_manifest_hash": writer_evidence.get("document_manifest_hash"),
                "index_schema_version": "1",
                "modality": modality,
                "collection_name": f"{modality}_vectors",
                "hnsw:space": "cosine",
            }
            if item.get("name") != f"{modality}_vectors" or not isinstance(metadata, dict) or any(
                metadata.get(key) != value for key, value in expected_metadata.items()
            ):
                collection_metadata_valid = False
                break
    required = (
        evidence.get("schema_version") == SCHEMA_VERSION
        and evidence.get("attempt_id") == attempt_id
        and evidence.get("role") == "reader"
        and evidence.get("status") == "passed"
        and evidence.get("final_classification") == "passed"
        and evidence.get("evidence_complete") is True
        and evidence.get("chroma_version") == chroma_version
        and type(evidence.get("pid")) is int
        and evidence.get("pid") == reader_process.get("python_pid")
        and evidence.get("python_version") == "3.10.11"
        and evidence.get("writer_process_completed") is True
        and writer_process.get("exit_code") == 0
        and writer_process.get("timed_out") is False
        and reader_process.get("exit_code") == 0
        and reader_process.get("timed_out") is False
        and reader_process.get("worker_failure") is None
        and evidence.get("report_identity_validated") is True
        and evidence.get("open_existing_completed") == 3
        and evidence.get("collection_metadata_verified") is True
        and collection_metadata_valid
        and evidence.get("text_count") == 2
        and evidence.get("image_count") == 1
        and evidence.get("lease_released") is True
        and evidence.get("writer_reservation_present_after_retrieval") is False
        and close_evidence_valid
        and conflict_evidence_valid
        and count_evidence_valid
        and report_evidence_valid
        and evidence.get("instrumentation_errors") == []
        and call_modalities == EXPECTED_QUERY_MODALITIES
        and calls_are_objects
        and len(calls) == 3
        and all(
            call.get("ordinal") == index
            and call.get("method_entered") is True
            and call.get("completed") is True
            and call.get("distances_finite") is True
            and call.get("collection_name") == f"{call.get('modality')}_vectors"
            and call.get("collection_id") == collections[call.get("modality")].get("id")
            and call.get("returned_ids") == (TEXT_IDS if call.get("modality") == "text" else IMAGE_IDS)
            and call.get("n_results") == expected_n_results[index - 1]
            and call.get("distances") == ([0.0, 1.0] if call.get("modality") == "text" else [0.0])
            for index, call in enumerate(calls, 1)
        )
        and requests_are_objects
        and request_modalities == EXPECTED_QUERY_MODALITIES
        and len(requests) == 3
        and all(item.get("ordinal") == index and item.get("n_results") == expected_n_results[index - 1]
                and item.get("store_method_entered") is True
                and item.get("store_method_completed") is True
                and item.get("collection_name") == f"{item.get('modality')}_vectors"
                and item.get("collection_id") == collections[item.get("modality")].get("id")
                for index, item in enumerate(requests, 1))
        and isinstance(writer_evidence, dict)
        and evidence.get("index_fingerprint") == writer_evidence.get("index_fingerprint")
        and evidence.get("run_fingerprint") == writer_evidence.get("run_fingerprint")
        and evidence.get("document_manifest_hash") == writer_evidence.get("document_manifest_hash")
        and evidence.get("build_id") == writer_evidence.get("build_id")
        and evidence.get("report_sha256_before") == writer_evidence.get("report_sha256")
        and evidence.get("index_manifest_sha256_before") == writer_evidence.get("index_manifest_sha256")
        and evidence.get("network_attempts") == []
        and evidence.get("report_sha256_before") == evidence.get("report_sha256_after")
        and evidence.get("index_manifest_sha256_before") == evidence.get("index_manifest_sha256_after")
    )
    return (True, "passed") if required else (False, "reader_evidence_incomplete_or_contradictory")


def _classify_reader_failure(
    evidence: Any,
    *,
    attempt_id: str,
    chroma_version: str,
    process: dict[str, Any],
) -> tuple[str, str]:
    if process.get("timed_out") or process.get("launch_error") or process.get("exit_code") is None:
        return "C", "child_orchestration_failure"
    if process.get("worker_result") is not None:
        return "C", "contradictory_success_and_failure_evidence"
    if not isinstance(evidence, dict):
        return "C", "failure_evidence_missing_or_malformed"
    calls = evidence.get("query_calls")
    errors = evidence.get("instrumentation_errors")
    closes = evidence.get("close_observations")
    valid_failure = (
        evidence.get("schema_version") == SCHEMA_VERSION
        and evidence.get("attempt_id") == attempt_id
        and evidence.get("role") == "reader"
        and evidence.get("status") == "failed"
        and evidence.get("chroma_version") == chroma_version
        and process.get("exit_code") != 0
        and type(evidence.get("pid")) is int
        and evidence.get("pid") == process.get("python_pid")
        and isinstance(evidence.get("traceback"), str)
        and bool(evidence.get("traceback"))
        and evidence.get("evidence_complete") is True
        and isinstance(calls, list)
        and all(isinstance(call, dict) for call in calls)
        and isinstance(errors, list)
    )
    if not valid_failure:
        return "C", "failure_evidence_incomplete_or_contradictory"
    classification = evidence.get("final_classification")
    if isinstance(classification, str) and classification.startswith("instrumentation_invalid"):
        return "C", classification
    if errors:
        return "C", "instrumentation_errors_present"
    query_failure_preserved = classification == "application_query_failure" and any(
        call.get("method_entered") is True and isinstance(call.get("exception"), dict)
        for call in calls
    )
    cleanup_verified = (
        isinstance(closes, list)
        and bool(closes)
        and all(isinstance(item, dict) and item.get("released") is True for item in closes)
        and evidence.get("writer_reservation_present_after_retrieval") is False
    )
    if query_failure_preserved and cleanup_verified and errors == [] and evidence.get("network_attempts") == []:
        return "B", "underlying_chroma_query_failed_after_verified_cleanup"
    return "C", "application_boundary_not_reached_or_unproven"


def _all_trials_completed(trials: list[dict[str, Any]]) -> bool:
    return bool(trials) and all(
        trial.get("completed") is True and trial.get("classification") == "A"
        for trial in trials
    )


def _install_network_guard() -> None:
    """Reject and record non-loopback DNS/connect attempts in this child."""
    import ipaddress

    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect

    def guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            address = ipaddress.ip_address(host)
        except (TypeError, ValueError):
            if isinstance(host, str) and host.lower() == "localhost":
                return real_getaddrinfo(host, *args, **kwargs)
            _NETWORK_ATTEMPTS.append("dns")
            raise OSError("external network access blocked by the test probe")
        if not address.is_loopback:
            _NETWORK_ATTEMPTS.append("dns")
            raise OSError("external network access blocked by the test probe")
        return real_getaddrinfo(host, *args, **kwargs)

    def guarded_connect(sock: socket.socket, address: Any) -> Any:
        if isinstance(address, tuple) and address:
            host = address[0]
            try:
                loopback = ipaddress.ip_address(host).is_loopback
            except (TypeError, ValueError):
                loopback = isinstance(host, str) and host.lower() == "localhost"
            if not loopback:
                _NETWORK_ATTEMPTS.append("connect")
                raise OSError("external network access blocked by the test probe")
        return real_connect(sock, address)

    socket.getaddrinfo = guarded_getaddrinfo  # type: ignore[assignment]
    socket.socket.connect = guarded_connect  # type: ignore[assignment]


def _install_source_namespace_shims(project_root: Path) -> None:
    """Skip eager convenience exports; keep the imported implementation real.

    The engine package initializer eagerly imports PDF ingestion and the
    builder (which require the omitted LangChain stack). These namespace
    packages expose the repository's actual submodules without executing the
    unrelated initializer. No retrieval, vector-store, report, or lease code
    is replaced.
    """
    for name in ("backend.engine", "backend.experiments"):
        module = types.ModuleType(name)
        module.__path__ = [str(project_root.joinpath(*name.split(".")))]  # type: ignore[attr-defined]
        module.__package__ = name
        sys.modules[name] = module


def _minimal_pdf() -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length 71 >>\nstream\nBT /F1 12 Tf 72 720 Td (Synthetic retrieval fixture) Tj ET\nendstream",
    ]
    content = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(content))
        content.extend(f"{number} 0 obj\n".encode("ascii"))
        content.extend(obj + b"\nendobj\n")
    xref_offset = len(content)
    content.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    content.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        content.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    content.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(content)


def _make_config_and_plan(root: Path, project_root: Path) -> tuple[Any, Any, str]:
    sys.path.insert(0, str(project_root))
    _install_source_namespace_shims(project_root)
    from backend.config import ResolvedRunConfig
    from backend.engine.manifest import build_document_manifest
    from backend.experiments.run_plan import build_run_plan
    from notebooks.config import Config

    source = root / "pdf-source"
    source.mkdir(parents=True, exist_ok=True)
    pdf = source / "synthetic.pdf"
    if not pdf.exists():
        pdf.write_bytes(_minimal_pdf())

    paths = {
        "pdf_dir": source,
        "images_dir": root / "image-source",
        "database_dir": root / "legacy-database",
        "results_dir": root / "legacy-results",
        "model_comparison_results_dir": root / "legacy-model-results",
        "retrieval_results_dir": root / "legacy-retrieval-results",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    legacy = Config(**{key: str(value) for key, value in paths.items()})
    legacy.reset_collections_on_start = False
    legacy.retrieval_mode = "semantic"
    legacy.adaptive_weighting = False
    legacy.use_weighted_fusion = False
    legacy.text_embed_model = "synthetic-text-query-adapter"
    legacy.image_embed_model = "synthetic-image-query-adapter"
    legacy.image_embed_pretrained = "synthetic-image-fixture-v1"
    resolved = ResolvedRunConfig.from_legacy(legacy)
    resolved.validate_for_workbench()
    manifest = build_document_manifest(source)
    plan = build_run_plan(resolved, manifest.content_hash)
    plan.verify_integrity()
    return resolved, plan, manifest.content_hash


def _write_schema_valid_report(storage: Path, plan: Any) -> tuple[Path, str, bytes]:
    build_id = uuid.uuid4().hex
    relative_dir = Path("build-artifacts") / build_id
    artifact_dir = storage / relative_dir
    artifact_dir.mkdir(parents=True, exist_ok=False)
    relative_report = relative_dir / "build-report.json"
    report_path = storage / relative_report
    payload = {
        "report_schema_version": "1",
        "build_id": build_id,
        "artifact_root": relative_dir.as_posix(),
        "report_path": relative_report.as_posix(),
        "index_fingerprint": plan.index_fingerprint,
        "document_manifest_hash": plan.document_manifest_hash,
        "status": "completed",
        "partial_persistence": False,
        "text_records_persisted": len(TEXT_IDS),
        "image_records_persisted": len(IMAGE_IDS),
    }
    _json_write(report_path, payload)
    report_bytes = report_path.read_bytes()
    return report_path, build_id, report_bytes


def _writer(root: Path, project_root: Path, expected_version: str, attempt_id: str) -> int:
    stage = "worker_start"
    events: list[dict[str, Any]] = []
    instrumentation_errors: list[dict[str, Any]] = []
    store = None
    lease = None
    try:
        _install_network_guard()
        import chromadb
        _install_source_namespace_shims(project_root)
        from backend.config.writer_lease import WriterLease
        from backend.vectorstore import ChromaVectorStore

        if chromadb.__version__ != expected_version:
            raise RuntimeError(f"unexpected Chroma version {chromadb.__version__}")
        config, plan, manifest_hash = _make_config_and_plan(root, project_root)
        storage = root / APPLICATION_STORAGE
        storage.mkdir(parents=True, exist_ok=False)
        (root / "run-plan.json").write_text(plan.to_serializable_json(), encoding="utf-8")
        (root / "resolved-config.json").write_text(config.model_dump_json(indent=2), encoding="utf-8")
        _safe_event_record(events, instrumentation_errors, "fixture_identities_created",
                           chroma_version=chromadb.__version__, manifest_hash=manifest_hash,
                           index_fingerprint=plan.index_fingerprint, run_fingerprint=plan.run_fingerprint)

        stage = "writer_lease_acquire"
        lease = WriterLease.acquire(storage, plan.index_fingerprint)
        _safe_event_record(events, instrumentation_errors, "writer_lease_acquired", marker_present=True)
        stage = "fresh_only_store_create"
        store = ChromaVectorStore.create_new(storage, plan, _lease=lease)
        _safe_event_record(events, instrumentation_errors, "fresh_only_store_created",
                           manifest=store.manifest.model_dump(mode="json"))

        stage = "upsert_text"
        store.upsert_text(
            TEXT_IDS,
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
            ["synthetic text record one", "synthetic text record two"],
            [{"fixture": "synthetic", "ordinal": 1}, {"fixture": "synthetic", "ordinal": 2}],
        )
        _safe_event_record(events, instrumentation_errors, "text_records_persisted", ids=TEXT_IDS)
        stage = "upsert_image"
        store.upsert_image(
            IMAGE_IDS,
            [EXPECTED_IMAGE_VECTOR],
            [{"fixture": "synthetic", "image_id": "image-1", "image_path": "synthetic/image-1.png"}],
        )
        _safe_event_record(events, instrumentation_errors, "image_records_persisted", ids=IMAGE_IDS)
        counts = {"text": store.count("text"), "image": store.count("image")}
        if counts != {"text": 2, "image": 1}:
            raise AssertionError("fresh application store counts differ from the fixture")

        report_path, build_id, report_bytes = _write_schema_valid_report(storage, plan)
        report_digest = hashlib.sha256(report_bytes).hexdigest()
        manifest_path = store.index_root / "index-manifest.json"
        manifest_bytes = manifest_path.read_bytes()
        manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
        stage = "wrapper_close_before_finalize"
        store.close()
        store = None
        stage = "test_fixture_finalize"
        ChromaVectorStore._finalize_new(storage, plan, lease)
        lease.release()
        lease = None
        if _NETWORK_ATTEMPTS:
            raise RuntimeError("child attempted non-loopback network access")
        if instrumentation_errors:
            raise ProbeInstrumentationError("writer observer reported instrumentation errors")

        result = {
            "schema_version": SCHEMA_VERSION,
            "attempt_id": attempt_id,
            "status": "passed",
            "final_classification": "passed",
            "evidence_complete": True,
            "role": "writer",
            "stage": "writer_completed",
            "writer_completed": True,
            "pid": os.getpid(),
            "thread_id": threading.get_ident(),
            "python_version": sys.version.split()[0],
            "chroma_version": chromadb.__version__,
            "index_fingerprint": plan.index_fingerprint,
            "run_fingerprint": plan.run_fingerprint,
            "document_manifest_hash": manifest_hash,
            "build_id": build_id,
            "report_reference": report_path.relative_to(storage).as_posix(),
            "report_sha256": report_digest,
            "index_manifest_sha256": manifest_digest,
            "reported_text_records": counts["text"],
            "reported_image_records": counts["image"],
            "writer_reservation_present_after_close": (storage / ".writer-leases" / plan.index_fingerprint).exists(),
            "fresh_build_marker_present_after_finalize": (store.index_root if store else storage / "indexes" / plan.index_fingerprint).joinpath(".fresh-build-incomplete").exists(),
            "lease_released": not (storage / ".writer-leases" / plan.index_fingerprint).exists(),
            "instrumentation_errors": instrumentation_errors,
            "network_attempts": list(_NETWORK_ATTEMPTS),
            "events": events,
        }
        _json_write(root / "writer-result.json", result)
        return 0
    except BaseException as exc:
        cleanup_errors: list[dict[str, str]] = []
        if store is not None:
            try:
                store.close()
                store = None
            except BaseException as cleanup_exc:
                cleanup_errors.append(_safe_exception(cleanup_exc))
        if lease is not None:
            try:
                lease.release()
                lease = None
            except BaseException as cleanup_exc:
                cleanup_errors.append(_safe_exception(cleanup_exc))
        failure_traceback = _safe_traceback_text(exc, instrumentation_errors, stage="writer_traceback_format")
        traceback_capture_ok = not any(item.get("stage") == "writer_traceback_format" for item in instrumentation_errors)
        failure = {
            "schema_version": SCHEMA_VERSION,
            "attempt_id": attempt_id,
            "status": "failed",
            "final_classification": "instrumentation_invalid" if instrumentation_errors else "application_or_chroma_failure",
            "evidence_complete": bool(events) and traceback_capture_ok,
            "role": "writer",
            "stage": stage,
            "pid": os.getpid(),
            "thread_id": threading.get_ident(),
            "python_version": sys.version.split()[0],
            "exception": _safe_exception(exc),
            "traceback": failure_traceback,
            "cleanup_errors": cleanup_errors,
            "instrumentation_errors": instrumentation_errors,
            "writer_reservation_present": bool(lease is not None),
            "network_attempts": list(_NETWORK_ATTEMPTS),
            "events": events,
        }
        try:
            _json_write(root / "writer-failure.json", failure)
        except BaseException as write_exc:
            print(f"failure evidence write error: {type(write_exc).__name__}", file=sys.stderr)
        raise


def _reader(root: Path, project_root: Path, expected_version: str, attempt_id: str) -> int:
    stage = "worker_start"
    events: list[dict[str, Any]] = []
    instrumentation_errors: list[dict[str, Any]] = []
    query_calls: list[dict[str, Any]] = []
    query_events: list[dict[str, Any]] = []
    query_requests: list[dict[str, Any]] = []
    report_checks: list[dict[str, Any]] = []
    count_events: list[dict[str, Any]] = []
    close_events: list[dict[str, Any]] = []
    exclusive_conflicts: list[dict[str, Any]] = []
    collection_metadata: dict[str, dict[str, Any]] = {}
    collection_modality_by_id: dict[str, str] = {}
    collection_query_original: Any = None
    collection_query_class: Any = None
    open_count = 0
    query_ordinal = 0
    active_open = [0]
    try:
        _install_network_guard()
        import numpy as np
        import chromadb
        from backend.config.writer_lease import WriterLease, WriterLeaseError
        _install_source_namespace_shims(project_root)
        from backend.engine.manifest import build_document_manifest
        from backend.engine.retrieval import retrieve_for_plan
        from backend.experiments.run_plan import RunPlan, build_run_plan
        from backend.vectorstore import ChromaVectorStore
        import backend.engine.retrieval as retrieval_module

        if chromadb.__version__ != expected_version:
            raise RuntimeError(f"unexpected Chroma version {chromadb.__version__}")
        storage = root / APPLICATION_STORAGE
        source = root / "pdf-source"
        writer_process = json.loads((root / "writer-process.json").read_text(encoding="utf-8"))
        writer_valid, writer_reason = _validate_writer_result(
            writer_process.get("worker_result"),
            attempt_id=attempt_id,
            chroma_version=expected_version,
            process=writer_process,
        )
        if not writer_valid:
            raise AssertionError(f"writer evidence invalid: {writer_reason}")
        writer_record = json.loads((root / "writer-result.json").read_text(encoding="utf-8"))
        if writer_record.get("attempt_id") != attempt_id or writer_record.get("chroma_version") != expected_version:
            raise AssertionError("writer record does not match this attempt")
        serialized_plan = (root / "run-plan.json").read_text(encoding="utf-8")
        plan = RunPlan.from_serializable_json(serialized_plan)
        config = plan.resolved_config()
        config.validate_for_workbench()
        manifest = build_document_manifest(source)
        recomputed_plan = build_run_plan(config, manifest.content_hash)
        if (
            manifest.content_hash != writer_record.get("document_manifest_hash")
            or recomputed_plan.index_fingerprint != plan.index_fingerprint
            or recomputed_plan.run_fingerprint != plan.run_fingerprint
            or writer_record.get("index_fingerprint") != plan.index_fingerprint
        ):
            raise AssertionError("reader source or RunPlan identity differs from the writer")
        report_path = storage / Path(*writer_record["report_reference"].split("/"))
        report_bytes_before = report_path.read_bytes()
        report_digest_before = hashlib.sha256(report_bytes_before).hexdigest()
        manifest_path = storage / "indexes" / plan.index_fingerprint / "index-manifest.json"
        manifest_bytes_before = manifest_path.read_bytes()
        manifest_digest_before = hashlib.sha256(manifest_bytes_before).hexdigest()
        if report_digest_before != writer_record.get("report_sha256"):
            raise AssertionError("selected report differs from the writer's preserved report")
        if manifest_digest_before != writer_record.get("index_manifest_sha256"):
            raise AssertionError("application index manifest differs from the writer's manifest")
        _safe_event_record(events, instrumentation_errors, "run_plan_and_source_revalidated",
                           index_fingerprint=plan.index_fingerprint,
                           document_manifest_hash=manifest.content_hash,
                           run_fingerprint=plan.run_fingerprint)

        original_report_validation = retrieval_module._validate_build_report
        original_open = ChromaVectorStore.open_existing.__func__
        original_count = ChromaVectorStore.count
        original_query_text = ChromaVectorStore.query_text
        original_query_image = ChromaVectorStore.query_image
        original_close = ChromaVectorStore.close

        def observed_collection_query(collection: Any, *args: Any, **kwargs: Any):
            collection_id = str(collection.id)
            modality = collection_modality_by_id.get(collection_id, "unknown")
            ordinal = len(query_calls) + 1
            request = query_requests[-1] if query_requests else {}
            if request.get("ordinal") != ordinal or request.get("modality") != modality:
                _record_diagnostic_error(
                    instrumentation_errors,
                    "collection_query_correlation",
                    RuntimeError("public collection query did not match the active retrieval request"),
                )
            n_results = kwargs.get("n_results", args[4] if len(args) > 4 else None)
            return _invoke_observed_query(
                lambda: collection_query_original(collection, *args, **kwargs),
                events=events,
                instrumentation_errors=instrumentation_errors,
                query_calls=query_calls,
                ordinal=ordinal,
                modality=modality,
                collection_name=str(collection.name),
                collection_id=collection_id,
                n_results=n_results,
            )

        def install_collection_query_observer(store: Any) -> None:
            nonlocal collection_query_class, collection_query_original
            for modality in ("text", "image"):
                collection = store._collections[modality]
                collection_id = str(collection.id)
                collection_modality_by_id[collection_id] = modality
                collection_metadata[modality] = {
                    "name": str(collection.name),
                    "id": collection_id,
                    "metadata": dict(collection.metadata or {}),
                }
            collection_class = type(store._collections["text"])
            if collection_query_class is None:
                collection_query_class = collection_class
                collection_query_original = collection_class.query
                collection_class.query = observed_collection_query
            elif collection_query_class is not collection_class:
                raise AssertionError("Chroma collection class changed during one reader process")

        def observed_report_validation(storage_root: Any, selected_report: Any, run_plan: Any):
            report = original_report_validation(storage_root, selected_report, run_plan)
            item = {
                "build_id": report.build_id,
                "index_fingerprint": report.index_fingerprint,
                "document_manifest_hash": report.document_manifest_hash,
                "text_count": report.text_records_persisted,
                "image_count": report.image_records_persisted,
                "status": report.status,
            }
            report_checks.append(item)
            _safe_event_record(events, instrumentation_errors, "retrieval_report_validated", **item)
            return report

        def observed_open(cls: Any, storage_root: Any, run_plan: Any):
            nonlocal open_count
            stage_name = "open_existing_started"
            _safe_event_record(events, instrumentation_errors, stage_name,
                               open_ordinal=open_count + 1,
                               index_fingerprint=run_plan.index_fingerprint)
            store = original_open(cls, storage_root, run_plan)
            open_count += 1
            active_open[0] = open_count
            lease_path = Path(storage_root) / ".writer-leases" / run_plan.index_fingerprint
            if not lease_path.is_dir() or store._writer_lease is None:
                store.close()
                raise AssertionError("open_existing did not retain its cooperating writer reservation")
            store._writer_lease.verify(storage_root, run_plan.index_fingerprint)
            try:
                contender = WriterLease.acquire(storage_root, run_plan.index_fingerprint)
            except WriterLeaseError as exc:
                if str(exc) != "writer_lease_busy":
                    store.close()
                    raise
                exclusive_conflicts.append({"open_ordinal": open_count, "error": str(exc)})
            else:
                contender.release()
                store.close()
                raise AssertionError("a second cooperating lease unexpectedly entered an open index")
            try:
                install_collection_query_observer(store)
            except BaseException as exc:
                _record_diagnostic_error(instrumentation_errors, "collection_query_observer_install", exc)
                try:
                    store.close()
                except BaseException as close_exc:
                    _record_diagnostic_error(instrumentation_errors, "collection_query_observer_cleanup", close_exc)
                raise ProbeInstrumentationError("could not install the public query observer") from exc
            _safe_event_record(events, instrumentation_errors, "open_existing_completed",
                               open_ordinal=open_count, reservation_present=True,
                               collection_info=collection_metadata)
            return store

        def observed_count(store: Any, modality: str) -> int:
            value = original_count(store, modality)
            item = {"open_ordinal": active_open[0], "modality": modality, "count": value}
            count_events.append(item)
            _safe_event_record(events, instrumentation_errors, "persisted_count_observed", **item)
            return value

        def query_observer(modality: str, original: Any, store: Any, vector: Any, n_results: int):
            nonlocal query_ordinal
            query_ordinal += 1
            collection = store._collections[modality]
            item = {
                "ordinal": query_ordinal,
                "open_ordinal": active_open[0],
                "modality": modality,
                "collection_name": collection.name,
                "collection_id": str(collection.id),
                "index_fingerprint": store.manifest.index_fingerprint,
                "n_results": n_results,
                "store_method_entered": True,
                "store_method_completed": False,
            }
            query_events.append(item)
            query_requests.append(item)
            _safe_query_event(events, instrumentation_errors, "store_query_started", item)
            try:
                result = original(store, vector, n_results)
            except BaseException as exc:
                item["store_method_completed"] = False
                item["exception"] = _safe_exception(exc)
                _safe_query_event(events, instrumentation_errors, "store_query_failed", item)
                raise
            item["store_method_completed"] = True
            _safe_query_event(events, instrumentation_errors, "store_query_completed", item)
            return result

        def observed_query_text(store: Any, vector: Any, n_results: int = 10):
            return query_observer("text", original_query_text, store, vector, n_results)

        def observed_query_image(store: Any, vector: Any, n_results: int = 10):
            return query_observer("image", original_query_image, store, vector, n_results)

        def observed_close(store: Any) -> None:
            fingerprint = store.manifest.index_fingerprint
            marker = Path(store.storage_root) / ".writer-leases" / fingerprint
            before = marker.exists()
            try:
                original_close(store)
            except BaseException as exc:
                close_events.append({"fingerprint": fingerprint, "released": False,
                                     "exception": _safe_exception(exc)})
                _safe_event_record(events, instrumentation_errors, "store_close_failed",
                                   reservation_present=marker.exists(), exception=_safe_exception(exc))
                raise
            after = marker.exists()
            item = {"fingerprint": fingerprint, "reservation_before": before,
                    "reservation_after": after, "released": before and not after}
            close_events.append(item)
            _safe_event_record(events, instrumentation_errors, "store_closed", **item)

        retrieval_module._validate_build_report = observed_report_validation
        ChromaVectorStore.open_existing = classmethod(observed_open)
        ChromaVectorStore.count = observed_count
        ChromaVectorStore.query_text = observed_query_text
        ChromaVectorStore.query_image = observed_query_image
        ChromaVectorStore.close = observed_close

        model_constructor_calls: list[str] = []

        def forbidden_model_constructor(*_args: Any, **_kwargs: Any):
            model_constructor_calls.append("production_embedding_constructor")
            raise AssertionError("production embedding constructor was invoked")

        retrieval_module.TextEmbeddingAdapter = forbidden_model_constructor
        retrieval_module.ImageEmbeddingAdapter = forbidden_model_constructor

        class TextAdapter:
            def __init__(self) -> None:
                self.config = config
                self.model_name = config.text_embed_model
                self.calls: list[str] = []

            def embed_query(self, query: str):
                self.calls.append(query)
                return np.asarray(EXPECTED_TEXT_VECTOR, dtype=np.float32)

        class ImageAdapter:
            def __init__(self) -> None:
                self.config = config
                self.model_name = config.image_embed_model
                self.pretrained = config.image_embed_pretrained
                self.calls: list[str] = []

            def embed_query(self, query: str):
                self.calls.append(query)
                return np.asarray(EXPECTED_IMAGE_VECTOR, dtype=np.float32)

        text_adapter = TextAdapter()
        image_adapter = ImageAdapter()
        stage = "retrieve_first_text"
        first_text = retrieve_for_plan(
            TEXT_QUERY, plan, storage, build_report_path=report_path,
            modalities=("text",), text_adapter=text_adapter, text_k=2,
        )
        stage = "retrieve_image"
        image = retrieve_for_plan(
            TEXT_QUERY, plan, storage, build_report_path=report_path,
            modalities=("image",), image_adapter=image_adapter, image_k=1,
        )
        stage = "retrieve_repeated_text"
        repeated_text = retrieve_for_plan(
            TEXT_QUERY, plan, storage, build_report_path=report_path,
            modalities=("text",), text_adapter=text_adapter, text_k=2,
        )

        if collection_query_class is None or collection_query_original is None:
            raise AssertionError("public Chroma collection query observer was not installed")
        collection_query_class.query = collection_query_original
        collection_query_class = None
        collection_query_original = None
        if instrumentation_errors:
            raise ProbeInstrumentationError("query observer reported instrumentation errors")

        def summarize(result: Any, modality: str) -> list[dict[str, Any]]:
            hits = result.text_hits if modality == "text" else result.image_hits
            return [
                {"id": hit.record_id, "distance": hit.semantic_distance,
                 "similarity": hit.semantic_similarity}
                for hit in hits
            ]

        first_text_hits = summarize(first_text, "text")
        image_hits = summarize(image, "image")
        repeated_hits = summarize(repeated_text, "text")
        if [item["id"] for item in first_text_hits] != TEXT_IDS:
            raise AssertionError("first application text query returned unexpected records")
        if [item["id"] for item in image_hits] != IMAGE_IDS:
            raise AssertionError("application image query returned unexpected records")
        if [item["id"] for item in repeated_hits] != TEXT_IDS:
            raise AssertionError("repeated application text query returned unexpected records")
        distances = [item["distance"] for item in first_text_hits + image_hits + repeated_hits]
        if not all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in distances):
            raise AssertionError("application retrieval returned a non-finite distance")
        if [round(float(item["distance"]), 6) for item in first_text_hits] != [0.0, 1.0]:
            raise AssertionError("first text query distances differ from the fixture")
        if [round(float(item["distance"]), 6) for item in image_hits] != [0.0]:
            raise AssertionError("image query distance differs from the fixture")
        if [item["distance"] for item in first_text_hits] != [item["distance"] for item in repeated_hits]:
            raise AssertionError("repeated text query distance differs from the first query")
        if first_text.query_id != repeated_text.query_id or first_text.query_id == image.query_id:
            raise AssertionError("application query identities do not match their modality inputs")
        if any(result.index_fingerprint != plan.index_fingerprint for result in (first_text, image, repeated_text)):
            raise AssertionError("retrieval result fingerprint differs from the verified RunPlan")
        if any(result.document_manifest_hash != manifest.content_hash for result in (first_text, image, repeated_text)):
            raise AssertionError("retrieval result manifest differs from the current source")
        if any((result.text_records_persisted, result.image_records_persisted) != (2, 1)
               for result in (first_text, image, repeated_text)):
            raise AssertionError("retrieval did not return report-matched persisted counts")
        if text_adapter.calls != [TEXT_QUERY, TEXT_QUERY] or image_adapter.calls != [TEXT_QUERY]:
            raise AssertionError("trusted deterministic query adapters were not invoked as expected")
        if model_constructor_calls:
            raise AssertionError("a production embedding constructor was invoked")
        if len(report_checks) != 3 or len(count_events) != 6 or open_count != 3 or len(query_events) != 3:
            raise AssertionError("retrieval did not traverse the expected report/open/count/query boundaries")
        if [item["modality"] for item in query_events] != ["text", "image", "text"]:
            raise AssertionError("application query sequence differs from the requested order")
        if len(exclusive_conflicts) != 3 or not all(item["released"] for item in close_events):
            raise AssertionError("exclusive reservation or wrapper-close release check failed")
        if len(query_calls) != 3 or [item["modality"] for item in query_calls] != EXPECTED_QUERY_MODALITIES:
            raise AssertionError("the public Chroma Collection.query method was not entered three times")
        if any(not item["method_entered"] or not item["completed"] for item in query_calls):
            raise AssertionError("a public Chroma Collection.query call did not complete")
        if any(not item["distances_finite"] for item in query_calls):
            raise AssertionError("a public Chroma query returned invalid distance evidence")
        if [item["returned_ids"] for item in query_calls] != [TEXT_IDS, IMAGE_IDS, TEXT_IDS]:
            raise AssertionError("public Chroma query returned unexpected persisted record IDs")
        if any(item["text_count"] != 2 or item["image_count"] != 1 for item in report_checks):
            raise AssertionError("retrieval report validation did not preserve both counts")
        for modality in ("text", "image"):
            info = collection_metadata[modality]
            metadata = info["metadata"]
            expected_values = {
                "store_schema_version": "1",
                "index_fingerprint": plan.index_fingerprint,
                "document_manifest_hash": manifest.content_hash,
                "index_schema_version": "1",
                "modality": modality,
                "collection_name": info["name"],
                "hnsw:space": "cosine",
            }
            if info["name"] != f"{modality}_vectors" or any(
                metadata.get(key) != value for key, value in expected_values.items()
            ):
                raise AssertionError("application collection metadata differs from the RunPlan")
        if report_path.read_bytes() != report_bytes_before or manifest_path.read_bytes() != manifest_bytes_before:
            raise AssertionError("retrieval changed the selected report or application identity manifest")
        if (storage / ".writer-leases" / plan.index_fingerprint).exists():
            raise AssertionError("retrieval left the cooperating writer reservation present")
        if _NETWORK_ATTEMPTS:
            raise AssertionError("child attempted non-loopback network access")

        result = {
            "schema_version": SCHEMA_VERSION,
            "attempt_id": attempt_id,
            "status": "passed",
            "final_classification": "passed",
            "evidence_complete": True,
            "role": "reader",
            "stage": "reader_completed",
            "pid": os.getpid(),
            "thread_id": threading.get_ident(),
            "python_version": sys.version.split()[0],
            "chroma_version": chromadb.__version__,
            "index_fingerprint": plan.index_fingerprint,
            "run_fingerprint": plan.run_fingerprint,
            "document_manifest_hash": manifest.content_hash,
            "writer_process_completed": True,
            "report_identity_validated": len(report_checks) == 3,
            "open_existing_completed": open_count,
            "collection_metadata_verified": True,
            "text_count": 2,
            "image_count": 1,
            "collection_metadata": collection_metadata,
            "lease_released": all(item["released"] for item in close_events),
            "query_requests": query_requests,
            "query_calls": query_calls,
            "instrumentation_errors": instrumentation_errors,
            "build_id": writer_record["build_id"],
            "report_reference": writer_record["report_reference"],
            "report_sha256_before": report_digest_before,
            "report_sha256_after": hashlib.sha256(report_path.read_bytes()).hexdigest(),
            "index_manifest_sha256_before": manifest_digest_before,
            "index_manifest_sha256_after": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "report_checks": report_checks,
            "collection_open_count": open_count,
            "count_observations": count_events,
            "exclusive_lease_conflicts": exclusive_conflicts,
            "close_observations": close_events,
            "query_observations": query_events,
            "queries": {
                "first_text": {"query_id": first_text.query_id, "hits": first_text_hits},
                "image": {"query_id": image.query_id, "hits": image_hits},
                "repeated_text": {"query_id": repeated_text.query_id, "hits": repeated_hits},
            },
            "production_model_constructor_calls": model_constructor_calls,
            "network_attempts": list(_NETWORK_ATTEMPTS),
            "writer_reservation_present_after_retrieval": (storage / ".writer-leases" / plan.index_fingerprint).exists(),
            "events": events,
        }
        stage = "publish_reader_evidence"
        _json_write(root / "reader-result.json", result)
        return 0
    except BaseException as exc:
        exception_types = {item["type"] for item in _safe_exception(exc)["explicit_cause_chain"]}
        failure_traceback = _safe_traceback_text(exc, instrumentation_errors, stage="reader_traceback_format")
        traceback_capture_ok = not any(
            item.get("stage") == "reader_traceback_format" for item in instrumentation_errors
        )
        if instrumentation_errors or isinstance(exc, (ProbeInstrumentationError, AssertionError)) or stage == "publish_reader_evidence":
            failure_classification = "instrumentation_invalid"
        elif any("exception" in call for call in query_calls):
            failure_classification = "application_query_failure"
        elif stage.startswith("retrieve_") and exception_types.intersection({
            "RetrievalInputError", "RetrievalReportError", "RetrievalReportUnavailable",
            "RetrievalReportMismatch", "RetrievalReportFailed", "RetrievalReportPartial",
            "RetrievalStoreError", "RetrievalQueryError",
        }):
            failure_classification = "application_boundary_failure"
        else:
            failure_classification = "application_orchestration_failure"
        failure = {
            "schema_version": SCHEMA_VERSION,
            "attempt_id": attempt_id,
            "status": "failed",
            "final_classification": failure_classification,
            "evidence_complete": bool(events) and traceback_capture_ok,
            "role": "reader",
            "stage": stage,
            "pid": os.getpid(),
            "thread_id": threading.get_ident(),
            "python_version": sys.version.split()[0],
            "chroma_version": getattr(sys.modules.get("chromadb"), "__version__", "unknown"),
            "exception": _safe_exception(exc),
            "traceback": failure_traceback,
            "report_identity_validated": bool(report_checks) and all(item.get("status") == "completed" for item in report_checks),
            "open_existing_completed": open_count,
            "collection_metadata": collection_metadata,
            "count_observations": count_events,
            "query_requests": query_requests,
            "query_calls": query_calls,
            "instrumentation_errors": instrumentation_errors,
            "close_observations": close_events,
            "writer_reservation_present_after_retrieval": any(
                not item.get("released") for item in close_events
            ),
            "network_attempts": list(_NETWORK_ATTEMPTS),
            "events": events,
        }
        try:
            _json_write(root / "reader-failure.json", failure)
        except BaseException as write_exc:
            print(f"failure evidence write error: {type(write_exc).__name__}", file=sys.stderr)
        raise


def _file_snapshot(root: Path) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    if not root.exists():
        return snapshot
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            snapshot[path.relative_to(root).as_posix()] = {"kind": "symlink"}
        elif path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            stat_result = path.stat()
            snapshot[path.relative_to(root).as_posix()] = {
                "kind": "file", "size": stat_result.st_size,
                "mtime_ns": stat_result.st_mtime_ns, "sha256": digest.hexdigest(),
            }
    return snapshot


def _diff(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    added = sorted(after.keys() - before.keys())
    removed = sorted(before.keys() - after.keys())
    size_changed: list[str] = []
    digest_changed: list[str] = []
    timestamp_only: list[str] = []
    for name in sorted(before.keys() & after.keys()):
        left, right = before[name], after[name]
        if left.get("size") != right.get("size"):
            size_changed.append(name)
        if left.get("sha256") != right.get("sha256"):
            digest_changed.append(name)
        elif left.get("mtime_ns") != right.get("mtime_ns"):
            timestamp_only.append(name)
    return {
        "added": added, "removed": removed,
        "size_changed": size_changed, "digest_changed": digest_changed,
        "timestamp_only": timestamp_only,
    }


def _run_child(
    interpreter: Path,
    mode: str,
    root: Path,
    project_root: Path,
    version: str,
    attempt_id: str,
) -> dict[str, Any]:
    process_dir = root / f"process-{mode}"
    process_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = process_dir / "stdout.txt"
    stderr_path = process_dir / "stderr.txt"
    env = os.environ.copy()
    for key in list(env):
        upper = key.upper()
        if (
            upper.startswith(("RAG_WORKBENCH_", "OPENAI_", "ANTHROPIC_", "HUGGINGFACE_", "HF_TOKEN"))
            or any(marker in upper for marker in ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL"))
            or upper in {"AWS_PROFILE", "GOOGLE_APPLICATION_CREDENTIALS", "AZURE_CLIENT_ID"}
            or upper in {
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"
            }
        ):
            env.pop(key, None)
    env.pop("PYTHONHOME", None)
    env["PYTHONPATH"] = str(project_root)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["ANONYMIZED_TELEMETRY"] = "FALSE"
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    env["HF_DATASETS_OFFLINE"] = "1"
    env["HF_HOME"] = str(root / "empty-hf-home")
    env["TRANSFORMERS_CACHE"] = str(root / "empty-transformers-cache")
    env["NLTK_DATA"] = str(root / "empty-nltk-data")
    command = [
        str(interpreter), str(Path(__file__).resolve()), "--worker", mode,
        str(root), str(project_root), version, attempt_id,
    ]
    started = time.monotonic()
    try:
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = subprocess.Popen(command, cwd=str(project_root), env=env, stdout=stdout,
                                       stderr=stderr, creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            launcher_pid = process.pid
            timed_out = False
            try:
                return_code = process.wait(timeout=150)
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    subprocess.run(["taskkill", "/PID", str(launcher_pid), "/T", "/F"],
                                   capture_output=True, timeout=10, check=False)
                except OSError:
                    pass
                try:
                    return_code = process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    return_code = process.wait(timeout=10)
    except OSError as exc:
        return {"mode": mode, "launch_error": _safe_exception(exc), "launcher_pid": None,
                "python_pid": None, "exit_code": None, "timed_out": False}
    result_path = root / f"{mode}-result.json"
    failure_path = root / f"{mode}-failure.json"
    result = None
    failure = None
    for path, label in ((result_path, "result"), (failure_path, "failure")):
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                loaded = {"evidence_parse_error": True}
            if label == "result":
                result = loaded
            else:
                failure = loaded
    return {
        "mode": mode,
        "launcher_pid": launcher_pid,
        "python_pid": (result or failure or {}).get("pid"),
        "exit_code": return_code,
        "timed_out": timed_out,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "worker_result": result,
        "worker_failure": failure,
        "stdout_file": stdout_path.name,
        "stderr_file": stderr_path.name,
    }


def _interpreter_identity(interpreter: Path, expected_version: str, temp_root: Path) -> dict[str, Any]:
    code = (
        "import importlib.metadata as m, json, platform, site, sys; "
        "names=('chromadb','pydantic','rank-bm25','nltk','joblib'); "
        "print(json.dumps({'python':sys.version.split()[0],'machine':platform.machine(),"
        "'prefix':sys.prefix,'base_prefix':sys.base_prefix,'user_site_enabled':site.ENABLE_USER_SITE,"
        "'sys_path':sys.path,'packages':{n:m.version(n) for n in names}}))"
    )
    process = subprocess.run([str(interpreter), "-c", code], capture_output=True, text=True,
                             timeout=30, check=False, env={**os.environ, "PYTHONNOUSERSITE": "1"})
    if process.returncode != 0:
        raise RuntimeError(f"environment identity probe failed for Chroma {expected_version}: {process.stderr[-1000:]}")
    identity = json.loads(process.stdout)
    prefix = Path(identity["prefix"]).resolve()
    prefix.relative_to(temp_root)
    venv_cfg = prefix / "pyvenv.cfg"
    cfg_text = venv_cfg.read_text(encoding="utf-8").lower()
    expected_packages = {
        "chromadb": expected_version,
        "pydantic": "2.14.2",
        "rank-bm25": "0.2.2",
        "nltk": "3.9.2",
        "joblib": "1.5.3",
    }
    if (
        identity.get("python") != "3.10.11"
        or identity.get("machine") != "AMD64"
        or identity.get("base_prefix") == identity.get("prefix")
        or identity.get("user_site_enabled") is not False
        or identity.get("packages") != expected_packages
        or any("rag-env" in item.lower() for item in identity.get("sys_path", []))
        or "include-system-site-packages = false" not in cfg_text
    ):
        raise RuntimeError(f"isolated environment identity mismatch for Chroma {expected_version}")
    pip_check = subprocess.run([str(interpreter), "-m", "pip", "check"], capture_output=True, text=True,
                               timeout=30, check=False, env={**os.environ, "PYTHONNOUSERSITE": "1"})
    if pip_check.returncode != 0:
        raise RuntimeError(f"pip check failed for Chroma {expected_version}: {pip_check.stdout} {pip_check.stderr}")
    identity["pip_check"] = "passed"
    identity["venv_system_site_packages"] = False
    identity["rag_env_on_sys_path"] = False
    return identity


def _validate_temp_path(path: Path, temp_root: Path) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    resolved.relative_to(temp_root)
    return resolved


def _validate_worker_root(
    root: Path,
    project_root: Path,
    attempt_id: str,
    expected_version: str,
) -> tuple[Path, Path]:
    """Require a parent-created, marked disposable root before worker writes."""
    temp_root = Path(tempfile.gettempdir()).resolve()
    if root.is_symlink():
        raise RuntimeError("worker root must not be a symlink")
    resolved_root = _validate_temp_path(root, temp_root)
    if not resolved_root.is_dir():
        raise RuntimeError("worker root must already exist")
    marker = resolved_root / OWNERSHIP_MARKER
    if marker.is_symlink() or not marker.is_file():
        raise RuntimeError("worker root lacks its disposable ownership marker")
    marker_lines = marker.read_text(encoding="ascii").splitlines()
    expected_lines = [OWNERSHIP_MARKER_VALUE.strip(), attempt_id, expected_version]
    if marker_lines != expected_lines:
        raise RuntimeError("worker root ownership marker is invalid")
    expected_project = Path(__file__).resolve().parents[1]
    resolved_project = project_root.resolve(strict=True)
    if resolved_project != expected_project:
        raise RuntimeError("worker project root does not match this probe's repository")
    return resolved_root, resolved_project


def _run(args: argparse.Namespace) -> int:
    if os.name != "nt":
        raise RuntimeError("application compatibility probe is Windows-specific")
    temp_root = Path(tempfile.gettempdir()).resolve()
    project_root = Path(__file__).resolve().parents[1]
    baseline = _validate_temp_path(Path(args.baseline_python), temp_root)
    candidate = _validate_temp_path(Path(args.candidate_python), temp_root)
    output_root = _validate_temp_path(Path(args.output_root), temp_root)
    if not baseline.is_file() or not candidate.is_file():
        raise FileNotFoundError("both temporary environment interpreters must exist")
    output_root.mkdir(parents=True, exist_ok=False)
    environments = {
        "1.5.0": _interpreter_identity(baseline, "1.5.0", temp_root),
        "1.5.9": _interpreter_identity(candidate, "1.5.9", temp_root),
    }
    matrix: list[dict[str, Any]] = []
    for version, interpreter in (("1.5.0", baseline), ("1.5.9", candidate)):
        trial_root = output_root / f"chroma-{version}"
        trial_root.mkdir(parents=True, exist_ok=False)
        attempt_id = uuid.uuid4().hex
        marker = trial_root / OWNERSHIP_MARKER
        with marker.open("x", encoding="ascii", newline="") as stream:
            stream.write(OWNERSHIP_MARKER_VALUE + attempt_id + "\n" + version + "\n")
        writer = _run_child(interpreter, "writer", trial_root, project_root, version, attempt_id)
        _json_write(trial_root / "writer-process.json", writer)
        storage = trial_root / APPLICATION_STORAGE
        writer_snapshot = _file_snapshot(storage)
        writer_ok, writer_reason = _validate_writer_result(
            writer.get("worker_result"),
            attempt_id=attempt_id,
            chroma_version=version,
            process=writer,
        )
        entry: dict[str, Any] = {
            "version": version,
            "attempts": 1,
            "attempt_id": attempt_id,
            "writer": writer,
            "writer_validation": writer_reason,
            "snapshot_after_writer_exit": writer_snapshot,
            "completed": False,
            "classification": "C",
        }
        if writer_ok:
            reader = _run_child(interpreter, "reader", trial_root, project_root, version, attempt_id)
            reader_snapshot = _file_snapshot(storage)
            reader_ok, reader_reason = _validate_reader_result(
                reader.get("worker_result"),
                attempt_id=attempt_id,
                chroma_version=version,
                writer_process=writer,
                reader_process=reader,
            )
            entry.update({
                "reader": reader,
                "reader_validation": reader_reason,
                "snapshot_after_reader_exit": reader_snapshot,
                "writer_to_reader_diff": _diff(writer_snapshot, reader_snapshot),
                "completed": reader_ok,
                "classification": "A" if reader_ok else "C",
            })
            if not reader_ok:
                failure = reader.get("worker_failure") or {}
                entry["failure_stage"] = failure.get("stage", "reader_orchestration")
                entry["failure_traceback_preserved"] = isinstance(failure.get("traceback"), str)
                entry["failure_classification"] = failure.get("final_classification", "unknown")
                classification, reason = _classify_reader_failure(
                    reader.get("worker_failure"),
                    attempt_id=attempt_id,
                    chroma_version=version,
                    process=reader,
                )
                entry["classification"] = classification
                entry["classification_reason"] = reason
                if classification == "C":
                    entry["stop_confirmation_reason"] = "instrumentation_or_child_orchestration_invalid"
        else:
            failure = writer.get("worker_failure") or {}
            entry["failure_stage"] = failure.get("stage", "writer_orchestration")
            entry["failure_traceback_preserved"] = isinstance(failure.get("traceback"), str)
            entry["failure_classification"] = failure.get("final_classification", "unknown")
            entry["stop_confirmation_reason"] = "writer_orchestration_or_evidence_invalid"
        _json_write(trial_root / "trial-summary.json", entry)
        matrix.append(entry)
        if entry.get("stop_confirmation_reason"):
            break

    trial_classes = [item.get("classification") for item in matrix]
    if len(matrix) == 2 and all(value == "A" for value in trial_classes):
        final_classification = "A"
    elif len(matrix) == 2 and all(value in ("A", "B") for value in trial_classes) and "B" in trial_classes:
        final_classification = "B"
    else:
        final_classification = "C"

    summary = {
        "schema_version": SCHEMA_VERSION,
        "experiment": "Phase 6.13C application-boundary compatibility",
        "final_classification": final_classification,
        "attempt_budget": {"1.5.0": 1, "1.5.9": 1, "total": 2},
        "environments": environments,
        "fixture": {
            "kind": "application ChromaVectorStore fixture with schema-valid synthetic build report",
            "builder_report": False,
            "pdf": "synthetic valid one-page PDF",
            "text_ids": TEXT_IDS,
            "image_ids": IMAGE_IDS,
            "text_count": 2,
            "image_count": 1,
            "embedding_dimension": 3,
            "query_order": ["text", "image", "text"],
            "retrieval_entrypoint": "backend.engine.retrieval.retrieve_for_plan",
        },
        "trials": matrix,
        "confirmation_stopped_early": len(matrix) < 2,
    }
    _json_write(output_root / "matrix-summary.json", summary)
    print(json.dumps({"output_root": output_root.name,
                      "trials": [{"version": x["version"], "completed": x["completed"],
                                  "classification": x.get("classification"),
                                  "failure_stage": x.get("failure_stage")} for x in matrix]}, indent=2))
    return 0 if _all_trials_completed(matrix) else 1


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        if len(sys.argv) != 7:
            raise SystemExit("invalid worker arguments")
        _, _, mode, root_text, project_text, version, attempt_id = sys.argv
        try:
            root, project_root = _validate_worker_root(
                Path(root_text), Path(project_text), attempt_id, version
            )
        except (OSError, ValueError, RuntimeError) as exc:
            raise SystemExit(f"unsafe worker roots: {type(exc).__name__}") from exc
        if mode == "writer":
            return _writer(root, project_root, version, attempt_id)
        if mode == "reader":
            return _reader(root, project_root, version, attempt_id)
        raise SystemExit("unknown worker mode")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-python", required=True)
    parser.add_argument("--candidate-python", required=True)
    parser.add_argument("--output-root", required=True)
    return _run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
