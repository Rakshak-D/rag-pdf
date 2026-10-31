"""Exercise a synthetic build, explicit preflight, and manual runtime handoff.

This is a local smoke harness, not an activation command. It creates all state
under one temporary directory and launches the real ordinary application with
temporary process environment variables.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class SmokeError(RuntimeError):
    """A bounded diagnostic code; never includes a local path or token."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


_GUARD_SOURCE = r'''from pathlib import Path
import os
import signal

if hasattr(signal, "SIGBREAK"):
    signal.signal(signal.SIGBREAK, lambda *_: signal.raise_signal(signal.SIGINT))

def _record(name):
    event_path = os.environ.get("RAG_HANDOFF_GUARD_EVENTS")
    if event_path:
        with open(event_path, "a", encoding="ascii") as stream:
            stream.write(name + "\n")

def _blocked(name):
    def call(*args, **kwargs):
        _record(name)
        raise RuntimeError("manual_handoff_test_guard")
    return call

def _blocked_method(name):
    def method(cls, *args, **kwargs):
        _record(name)
        raise RuntimeError("manual_handoff_test_guard")
    return classmethod(method)

import backend.app.runtime
import chromadb
chromadb.PersistentClient = _blocked("chromadb.PersistentClient")
chromadb.Client = _blocked("chromadb.Client")

from backend.config.writer_lease import WriterLease
WriterLease.acquire = _blocked_method("WriterLease.acquire")

from backend.vectorstore import ChromaVectorStore
ChromaVectorStore.open = _blocked_method("ChromaVectorStore.open")
ChromaVectorStore.open_existing = _blocked_method("ChromaVectorStore.open_existing")
ChromaVectorStore.create_new = _blocked_method("ChromaVectorStore.create_new")

from backend.embeddings.text import TextEmbeddingAdapter
from backend.embeddings.image import ImageEmbeddingAdapter
from backend.engine.reranking import CrossEncoderReranker
TextEmbeddingAdapter.__init__ = _blocked("TextEmbeddingAdapter.__init__")
ImageEmbeddingAdapter.__init__ = _blocked("ImageEmbeddingAdapter.__init__")
CrossEncoderReranker.__init__ = _blocked("CrossEncoderReranker.__init__")

import backend.engine.index_builder as index_builder
index_builder.build_index = _blocked("build_index")

import backend.engine.retrieval as retrieval
for _name in ("retrieve_for_plan", "retrieve_semantic", "retrieve_hybrid", "_retrieve_bm25"):
    if hasattr(retrieval, _name):
        setattr(retrieval, _name, _blocked("retrieval." + _name))

from backend.engine.generation import LocalGenerationAdapter
LocalGenerationAdapter.generate = _blocked("LocalGenerationAdapter.generate")

from backend.services.experiment_service import ExperimentService
ExperimentService.run_evaluation = _blocked("ExperimentService.run_evaluation")
ExperimentService.run_ablation = _blocked("ExperimentService.run_ablation")
from backend.services.job_manager import ExperimentJobManager
ExperimentJobManager.submit_evaluation = _blocked("ExperimentJobManager.submit_evaluation")
ExperimentJobManager.submit_ablation = _blocked("ExperimentJobManager.submit_ablation")

Path(os.environ["RAG_HANDOFF_GUARD_READY"]).write_text("ready", encoding="ascii")
'''


def _expect(condition: bool, code: str) -> None:
    if not condition:
        raise SmokeError(code)


def _clear_workbench_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for key in tuple(environment):
        if key.startswith("RAG_WORKBENCH_") or key.startswith("RAG_HANDOFF_GUARD_"):
            environment.pop(key, None)
    return environment


def _install_guard(root: Path, label: str) -> tuple[dict[str, str], Path, Path]:
    guard_dir = root / f"guard-{label}"
    guard_dir.mkdir()
    (guard_dir / "sitecustomize.py").write_text(_GUARD_SOURCE, encoding="utf-8")
    ready = root / f"guard-{label}-ready.txt"
    events = root / f"guard-{label}-events.txt"
    environment = _clear_workbench_environment()
    environment["PYTHONPATH"] = os.pathsep.join((str(guard_dir), str(ROOT)))
    environment["RAG_HANDOFF_GUARD_READY"] = str(ready)
    environment["RAG_HANDOFF_GUARD_EVENTS"] = str(events)
    environment["ANONYMIZED_TELEMETRY"] = "FALSE"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["HF_HUB_OFFLINE"] = "1"
    environment["TRANSFORMERS_OFFLINE"] = "1"
    environment["HF_DATASETS_OFFLINE"] = "1"
    return environment, ready, events


def _write_pdf(path: Path, text: str) -> None:
    import io

    import pymupdf
    from PIL import Image, ImageDraw

    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (100, 80), "#336699")
    draw = ImageDraw.Draw(image)
    for x in range(0, 100, 5):
        draw.line((x, 0, 99 - x, 79), fill="#f0c040", width=3)
    image_stream = io.BytesIO()
    image.save(image_stream, format="PNG")

    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    page.insert_image(pymupdf.Rect(72, 140, 172, 220), stream=image_stream.getvalue())
    temporary = path.with_name(path.name + ".tmp")
    document.save(temporary)
    document.close()
    temporary.replace(path)


def _build_child(config_path: Path, source: Path, storage: Path, result_path: Path, failure_path: Path) -> int:
    stage = "child_entry"
    try:
        import numpy as np

        from backend.config.schema import ResolvedRunConfig
        from backend.embeddings import EmbeddingBatchResult, ImageEmbeddingBatchResult
        from backend.engine.index_builder import build_index
        from backend.engine.manifest import build_document_manifest
        from backend.engine.retrieval import _validate_build_report
        from backend.experiments.run_plan import build_run_plan

        config = ResolvedRunConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
        stage = "plan_construction"
        manifest = build_document_manifest(source)
        plan = build_run_plan(config, manifest.content_hash)

        class SyntheticTextAdapter:
            def __init__(self, selected_config):
                self.config = selected_config
                self.model_name = selected_config.text_embed_model

            def embed_documents(self, documents):
                values = np.tile(np.asarray([[1.0, 0.0, 0.0]], dtype=np.float32), (len(documents), 1))
                elapsed = 0.001
                return EmbeddingBatchResult(
                    values, elapsed, len(documents), values.shape[0], values.shape[1],
                    elapsed / max(1, len(documents)),
                )

        class SyntheticImageAdapter:
            def __init__(self, selected_config):
                self.config = selected_config
                self.model_name = selected_config.image_embed_model
                self.pretrained = selected_config.image_embed_pretrained

            def embed_images(self, image_objects):
                objects = tuple(dict(item) for item in image_objects)
                values = np.tile(np.asarray([[0.0, 1.0, 0.0]], dtype=np.float32), (len(objects), 1))
                elapsed = 0.001
                return ImageEmbeddingBatchResult(
                    values, objects, elapsed, len(objects), values.shape[0], values.shape[1],
                    elapsed / max(1, len(objects)), 0, 0, (),
                )

        stage = "fresh_only_build"
        result = build_index(
            plan,
            source,
            storage,
            text_adapter=SyntheticTextAdapter(config),
            image_adapter=SyntheticImageAdapter(config),
            creation_policy="fresh_only",
        )
        if result.status not in {"completed", "completed_with_warnings"}:
            raise SmokeError("genuine_build_failed")
        stage = "report_validation"
        report = _validate_build_report(storage, result.report_path, plan)
        if (storage / "indexes" / plan.index_fingerprint / ".fresh-build-incomplete").exists():
            raise SmokeError("build_incomplete_marker_remains")
        payload = {
            "status": result.status,
            "build_id": result.build_id,
            "document_manifest_hash": plan.document_manifest_hash,
            "index_fingerprint": plan.index_fingerprint,
            "report_reference": result.report_path.relative_to(storage).as_posix(),
            "reported_text_records": report.text_records_persisted,
            "reported_image_records": report.image_records_persisted,
            "report_status": report.status,
        }
        result_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        return 0
    except BaseException as exc:
        failure_path.write_text(
            json.dumps({
                "stage": stage,
                "exception_type": type(exc).__name__,
                "traceback": traceback.format_exc(),
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        return 2


def _make_config(source: Path, root: Path):
    from backend.config.schema import ResolvedRunConfig
    from notebooks.config import Config

    legacy = Config(
        pdf_dir=str(source),
        images_dir=str(root / "legacy-images"),
        database_dir=str(root / "legacy-database"),
        results_dir=str(root / "legacy-results"),
        model_comparison_results_dir=str(root / "legacy-comparison-results"),
        retrieval_results_dir=str(root / "legacy-retrieval-results"),
        text_embed_model="manual-handoff-synthetic-text-v1",
        image_embed_model="manual-handoff-synthetic-image-v1",
        image_embed_pretrained="manual-handoff-synthetic-v1",
        reset_collections_on_start=False,
    )
    config = ResolvedRunConfig.from_legacy(legacy)
    config.validate_for_workbench()
    return config


def _tree_snapshot(root: Path) -> dict[str, tuple[object, ...]]:
    snapshot: dict[str, tuple[object, ...]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        if path.is_symlink():
            snapshot[relative] = ("symlink", info.st_size, info.st_mtime_ns)
        elif path.is_dir():
            snapshot[relative] = ("directory", info.st_mtime_ns)
        elif path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            snapshot[relative] = ("file", info.st_size, info.st_mtime_ns, digest.hexdigest())
        else:
            snapshot[relative] = ("other", info.st_size, info.st_mtime_ns)
    return snapshot


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(port: int, path: str, *, token: str | None = None, body: dict | None = None) -> tuple[int, dict]:
    headers = {}
    data = None
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        response = opener.open(request, timeout=5)
    except urllib.error.HTTPError as exc:
        response = exc
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        raise SmokeError("runtime_request_unavailable") from exc
    with response:
        raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise SmokeError("runtime_response_too_large")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            if response.status in {404, 405}:
                return response.status, {}
            raise SmokeError("runtime_response_invalid") from exc
        if not isinstance(payload, dict):
            raise SmokeError("runtime_response_invalid")
        return response.status, payload


def _wait_for_health(process: subprocess.Popen, port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SmokeError("ordinary_runtime_exited_before_health")
        try:
            status, payload = _request(port, "/health")
            if status == 200 and payload == {"status": "ok", "api_version": "v1"}:
                return
        except SmokeError:
            pass
        time.sleep(0.1)
    raise SmokeError("ordinary_runtime_start_timeout")


def _start_runtime(environment: dict[str, str], log_path: Path, timeout: float):
    port = int(environment["RAG_WORKBENCH_API_PORT"])
    log_stream = log_path.open("wb")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "backend.app"],
            cwd=ROOT,
            env=environment,
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
    except BaseException:
        log_stream.close()
        raise
    try:
        _wait_for_health(process, port, timeout)
    except BaseException:
        _stop_runtime(process, log_stream, require_graceful=False)
        raise
    return process, log_stream, port


def _stop_runtime(process: subprocess.Popen, log_stream, *, require_graceful: bool) -> bool:
    graceful = process.poll() is not None and process.returncode == 0
    if process.poll() is None:
        try:
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                process.send_signal(signal.SIGINT)
            process.wait(timeout=20)
            log_stream.flush()
            try:
                log_text = Path(log_stream.name).read_text(encoding="utf-8", errors="replace")
            except OSError:
                log_text = ""
            graceful = (
                process.returncode == 0
                and "Application shutdown complete" in log_text
                and "Finished server process" in log_text
            )
        except (OSError, ValueError, subprocess.TimeoutExpired):
            graceful = False
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    if process.poll() is None:
        log_stream.close()
        raise SmokeError("ordinary_runtime_process_cleanup_failed")
    log_stream.close()
    if require_graceful and not graceful:
        raise SmokeError("ordinary_runtime_graceful_shutdown_failed")
    return graceful


def _runtime_environment(
    root: Path,
    *,
    source: Path,
    storage: Path,
    report_path: Path,
    guard_label: str,
) -> tuple[dict[str, str], Path, Path, str, int]:
    runtime_root = root / f"runtime-{guard_label}"
    database_root = runtime_root / "databases"
    database_root.mkdir(parents=True)
    artifact_root = runtime_root / "artifacts"
    environment, guard_ready, guard_events = _install_guard(root, guard_label)
    token = "handoff-smoke-" + __import__("secrets").token_urlsafe(32)
    port = _free_port()
    environment.update({
        "RAG_WORKBENCH_API_TOKEN": token,
        "RAG_WORKBENCH_DATA_DIR": str(source),
        "RAG_WORKBENCH_ARTIFACT_STORE_DIR": str(artifact_root),
        "RAG_WORKBENCH_CATALOG_DB_PATH": str(database_root / "catalog.sqlite3"),
        "RAG_WORKBENCH_JOBS_DB_PATH": str(database_root / "jobs.sqlite3"),
        "RAG_WORKBENCH_INDEX_STORAGE_ROOT": str(storage),
        "RAG_WORKBENCH_BUILD_REPORT_PATH": str(report_path),
        "RAG_WORKBENCH_API_HOST": "127.0.0.1",
        "RAG_WORKBENCH_API_PORT": str(port),
        "RAG_WORKBENCH_WORKER_COUNT": "1",
        "RAG_WORKBENCH_MAX_PENDING_JOBS": "4",
        "RAG_WORKBENCH_MAX_REQUEST_BODY_BYTES": str(8 * 1024 * 1024),
    })
    return environment, guard_ready, guard_events, token, port


def _run_preflight(
    root: Path,
    *,
    config_path: Path,
    source: Path,
    storage: Path,
    build_id: str,
    manifest_hash: str,
    fingerprint: str,
    label: str,
    timeout: float,
) -> dict:
    environment, guard_ready, guard_events = _install_guard(root, f"preflight-{label}")
    command = [
        sys.executable,
        "scripts/report_handoff_preflight.py",
        "--config", str(config_path),
        "--source", str(source),
        "--storage-root", str(storage),
        "--build-id", build_id,
        "--expected-manifest-hash", manifest_hash,
        "--expected-index-fingerprint", fingerprint,
    ]
    try:
        result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise SmokeError(f"preflight_timeout_{label}") from exc
    _expect(guard_ready.is_file(), f"preflight_guard_not_installed_{label}")
    _expect(not guard_events.exists(), f"preflight_attempted_forbidden_operation_{label}")
    if result.returncode != 0:
        match = re.search(r"error_code: ([a-z][a-z0-9_]*)", result.stderr)
        code = match.group(1) if match else "unknown"
        raise SmokeError(f"preflight_rejected_{label}_{code}")
    if str(root) in result.stdout or str(root) in result.stderr:
        raise SmokeError(f"preflight_disclosed_temporary_path_{label}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SmokeError(f"preflight_response_invalid_{label}") from exc
    _expect(isinstance(payload, dict), f"preflight_response_invalid_{label}")
    return payload


def _write_report_copy(
    source_report: Path,
    storage: Path,
    build_id: str,
    *,
    manifest_hash: str,
    index_fingerprint: str,
    status: str = "completed",
    partial: bool = False,
) -> Path:
    storage.mkdir(parents=True, exist_ok=True)
    report_root = storage / "build-artifacts" / build_id
    report_root.mkdir(parents=True)
    report_path = report_root / "build-report.json"
    payload = json.loads(source_report.read_text(encoding="utf-8"))
    payload["build_id"] = build_id
    payload["artifact_root"] = str(report_root)
    payload["report_path"] = str(report_path)
    payload["document_manifest_hash"] = manifest_hash
    payload["index_fingerprint"] = index_fingerprint
    payload["status"] = status
    payload["partial_persistence"] = partial
    report_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return report_path


def _run_smoke(root: Path, timeout: float) -> list[str]:
    from backend.config.writer_lease import WriterLease
    from backend.engine.manifest import build_document_manifest
    from backend.engine.retrieval import _validate_build_report
    from backend.experiments.run_plan import build_run_plan
    from backend.vectorstore.chroma_store import _IndexStoreManifest

    outcomes: list[str] = []
    source = root / "pdf-source"
    source.mkdir()
    storage = root / "index-storage"
    storage.mkdir()
    config = _make_config(source, root)
    config_path = root / "resolved-config.json"
    config_path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    source_pdf = source / "synthetic.pdf"
    _write_pdf(source_pdf, "Synthetic manual report handoff fixture.")
    manifest = build_document_manifest(source)
    plan = build_run_plan(config, manifest.content_hash)

    build_result_path = root / "build-result.json"
    build_failure_path = root / "build-failure.json"
    build_log_path = root / "build-child.log"
    child_command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--build-child",
        str(config_path),
        str(source),
        str(storage),
        str(build_result_path),
        str(build_failure_path),
    ]
    child_environment = _clear_workbench_environment()
    child_environment.pop("PYTHONPATH", None)
    child_environment["PYTHONDONTWRITEBYTECODE"] = "1"
    child_environment["ANONYMIZED_TELEMETRY"] = "FALSE"
    child_environment["HF_HUB_OFFLINE"] = "1"
    child_environment["TRANSFORMERS_OFFLINE"] = "1"
    child_environment["HF_DATASETS_OFFLINE"] = "1"
    try:
        with build_log_path.open("wb") as log_stream:
            child = subprocess.run(
                child_command,
                cwd=ROOT,
                env=child_environment,
                stdout=log_stream,
                stderr=subprocess.STDOUT,
                timeout=max(timeout, 120),
                check=False,
            )
    except subprocess.TimeoutExpired as exc:
        raise SmokeError("fresh_build_child_timeout") from exc
    if child.returncode != 0 or not build_result_path.is_file():
        failure = {}
        if build_failure_path.is_file():
            try:
                failure = json.loads(build_failure_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        raise SmokeError(
            "fresh_build_child_failed_"
            + str(failure.get("stage", "unknown"))
            + "_"
            + str(failure.get("exception_type", "unknown"))
        )
    build = json.loads(build_result_path.read_text(encoding="utf-8"))
    _expect(build.get("status") in {"completed", "completed_with_warnings"}, "fresh_build_not_successful")
    _expect(build.get("document_manifest_hash") == manifest.content_hash, "fresh_build_manifest_mismatch")
    _expect(build.get("index_fingerprint") == plan.index_fingerprint, "fresh_build_fingerprint_mismatch")
    _expect(re.fullmatch(r"[0-9a-f]{32}", build.get("build_id", "")) is not None, "fresh_build_id_invalid")
    expected_reference = f"build-artifacts/{build['build_id']}/build-report.json"
    _expect(build.get("report_reference") == expected_reference, "fresh_build_report_location_invalid")
    report_path = storage / Path(*expected_reference.split("/"))
    _expect(report_path.is_file(), "fresh_build_report_missing")
    report = _validate_build_report(storage, report_path, plan)
    _expect(report.build_id == build["build_id"], "fresh_build_report_id_mismatch")
    _expect(report.text_records_persisted == build["reported_text_records"], "fresh_build_text_count_mismatch")
    _expect(report.image_records_persisted == build["reported_image_records"], "fresh_build_image_count_mismatch")
    fingerprint_index = storage / "indexes" / plan.index_fingerprint
    _expect(not (fingerprint_index / ".fresh-build-incomplete").exists(), "fresh_build_marker_remains")
    _expect(not (storage / ".writer-leases" / plan.index_fingerprint).exists(), "fresh_build_reservation_remains")
    outcomes.append(
        "fresh-only synthetic Chroma build: passed "
        f"(build_id={build['build_id']}, text_records={report.text_records_persisted}, "
        f"image_records={report.image_records_persisted})"
    )

    storage_before_preflight = _tree_snapshot(storage)
    positive_runtime_root = root / "runtime-positive"
    _expect(not positive_runtime_root.exists(), "preflight_runtime_state_precreated")
    preflight = _run_preflight(
        root,
        config_path=config_path,
        source=source,
        storage=storage,
        build_id=build["build_id"],
        manifest_hash=manifest.content_hash,
        fingerprint=plan.index_fingerprint,
        label="built_report",
        timeout=timeout,
    )
    _expect(preflight.get("result") == "report_compatible_for_manual_review", "preflight_result_invalid")
    _expect(preflight.get("build_id") == build["build_id"], "preflight_build_id_mismatch")
    _expect(preflight.get("report_reference") == expected_reference, "preflight_report_reference_mismatch")
    _expect(preflight.get("document_manifest_hash") == manifest.content_hash, "preflight_manifest_mismatch")
    _expect(preflight.get("index_fingerprint") == plan.index_fingerprint, "preflight_fingerprint_mismatch")
    _expect(preflight.get("reported_text_records") == report.text_records_persisted, "preflight_text_count_mismatch")
    _expect(preflight.get("reported_image_records") == report.image_records_persisted, "preflight_image_count_mismatch")
    _expect(preflight.get("check_level") == "report" and preflight.get("store_checked") is False, "preflight_verification_level_invalid")
    _expect(preflight.get("evidence_time") == "point_in_time", "preflight_freshness_label_missing")
    _expect(storage_before_preflight == _tree_snapshot(storage), "preflight_modified_index_storage")
    guard_ready = root / "guard-preflight-built_report-ready.txt"
    guard_events = root / "guard-preflight-built_report-events.txt"
    _expect(guard_ready.is_file() and not guard_events.exists(), "preflight_chroma_or_lease_attempted")
    _expect(not positive_runtime_root.exists(), "preflight_started_ordinary_runtime")
    outcomes.append("explicit report handoff preflight: passed without storage mutation")

    token_snapshot = {key: value for key, value in os.environ.items() if key.startswith("RAG_WORKBENCH_")}
    missing_report = storage / "build-artifacts" / ("0" * 32) / "build-report.json"
    missing_env, missing_ready, missing_events, missing_token, _ = _runtime_environment(
        root, source=source, storage=storage, report_path=missing_report, guard_label="missing-report"
    )
    missing_result = subprocess.run(
        [sys.executable, "-m", "backend.app"],
        cwd=ROOT,
        env=missing_env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    _expect(missing_result.returncode == 2, "missing_report_startup_not_rejected")
    _expect("build_report_missing" in missing_result.stderr, "missing_report_error_code_missing")
    _expect(str(root) not in missing_result.stderr and missing_token not in missing_result.stderr, "missing_report_error_disclosed_private_value")
    _expect(missing_ready.is_file() and not missing_events.exists(), "missing_report_guard_failed")
    _expect(not (root / "runtime-missing-report" / "artifacts").exists(), "missing_report_startup_created_artifacts")
    outcomes.append("ordinary runtime refuses missing selected report: passed")

    runtime_environment, runtime_guard_ready, runtime_guard_events, token, port = _runtime_environment(
        root, source=source, storage=storage, report_path=report_path, guard_label="positive"
    )
    runtime_log = root / "runtime-positive.log"
    process = log_stream = None
    try:
        process, log_stream, actual_port = _start_runtime(runtime_environment, runtime_log, timeout)
        _expect(actual_port == port, "runtime_port_mismatch")
        _expect(_request(port, "/health")[0] == 200, "ordinary_runtime_health_failed")
        _expect(_request(port, "/api/v1/documents/manifest")[0] == 401, "unauthenticated_manifest_not_rejected")
        _expect(_request(port, "/api/v1/documents/manifest", token="wrong-token")[0] == 401, "wrong_token_not_rejected")

        status, manifest_response = _request(port, "/api/v1/documents/manifest", token=token)
        _expect(status == 200, "authenticated_manifest_failed")
        _expect(manifest_response.get("content_hash") == manifest.content_hash, "runtime_manifest_identity_mismatch")
        _expect(manifest_response.get("entry_count") == 1, "runtime_manifest_count_mismatch")

        preparation_body = {
            "resolved_config": config.model_dump(mode="json"),
            "expected_manifest_hash": manifest.content_hash,
        }
        status, plan_response = _request(port, "/api/v1/run-plans", token=token, body=preparation_body)
        _expect(status == 200, "authenticated_run_plan_failed")
        prepared_plan = plan_response.get("run_plan", {})
        _expect(prepared_plan.get("index_fingerprint") == plan.index_fingerprint, "runtime_run_plan_fingerprint_mismatch")
        _expect(prepared_plan.get("document_manifest_hash") == manifest.content_hash, "runtime_run_plan_manifest_mismatch")

        readiness_body = {**preparation_body, "workflow": "evaluation"}
        status, readiness = _request(port, "/api/v1/index/readiness", token=token, body=readiness_body)
        _expect(status == 200, "authenticated_report_readiness_failed")
        _expect(readiness.get("status") == "report_match", "runtime_report_not_compatible")
        _expect(readiness.get("build_id") == build["build_id"], "runtime_readiness_build_id_mismatch")
        _expect(readiness.get("reported_text_records") == report.text_records_persisted, "runtime_readiness_text_count_mismatch")
        _expect(readiness.get("reported_image_records") == report.image_records_persisted, "runtime_readiness_image_count_mismatch")
        _expect(readiness.get("observed_text_records") is None and readiness.get("observed_image_records") is None, "runtime_claimed_observed_store_counts")
        _expect(readiness.get("check_level") == "report" and readiness.get("store_checked") is False, "runtime_claimed_store_check")
        _expect(_request(port, "/api/v1/bootstrap/status", token=token)[0] == 404, "ordinary_runtime_exposed_bootstrap_route")

        changed_config = config.model_copy(update={"chunk_overlap_tokens": config.chunk_overlap_tokens + 1})
        wrong_plan_body = {
            "resolved_config": changed_config.model_dump(mode="json"),
            "expected_manifest_hash": manifest.content_hash,
            "workflow": "evaluation",
        }
        status, wrong_plan = _request(port, "/api/v1/index/readiness", token=token, body=wrong_plan_body)
        _expect(status == 200, "wrong_plan_readiness_transport_failed")
        _expect(wrong_plan.get("status") == "mismatch", "wrong_plan_received_report_match")
        _expect(wrong_plan.get("reason_code") == "identity_mismatch", "wrong_plan_reason_code_invalid")
        _expect(wrong_plan.get("expected_index_fingerprint") != plan.index_fingerprint, "wrong_plan_fingerprint_not_recomputed")
        status, jobs = _request(port, "/api/v1/jobs", token=token)
        _expect(status == 200 and jobs.get("jobs") == [], "unexpected_experiment_jobs")
        status, artifacts = _request(port, "/api/v1/artifacts", token=token)
        _expect(status == 200 and artifacts.get("artifacts") == [], "unexpected_experiment_artifacts")

        # Report readiness deliberately does not inspect writer reservations or
        # incomplete markers. Demonstrate that layer boundary without touching
        # the Chroma database or removing the fixture marker.
        marker = fingerprint_index / ".fresh-build-incomplete"
        marker.write_text(plan.index_fingerprint, encoding="ascii")
        lease = WriterLease.acquire(storage, plan.index_fingerprint)
        lease_owner = storage / ".writer-leases" / plan.index_fingerprint / "owner"
        try:
            _expect(lease_owner.is_file(), "synthetic_writer_reservation_missing")
            status, marker_readiness = _request(port, "/api/v1/index/readiness", token=token, body=readiness_body)
            _expect(status == 200 and marker_readiness.get("status") == "report_match", "report_only_marker_boundary_changed")
            _expect(marker_readiness.get("store_checked") is False, "marker_case_claimed_store_check")
            _expect(lease_owner.is_file() and marker.is_file(), "report_readiness_changed_marker_or_reservation")
        finally:
            lease.release()
        _expect(marker.is_file(), "report_readiness_removed_incomplete_marker")
        _expect(not (storage / ".writer-leases" / plan.index_fingerprint).exists(), "smoke_writer_reservation_not_released")
        outcomes.append("ordinary runtime authentication, plan identity, and report-only readiness: passed")

        # The synthetic source changes after the handoff. The runtime must
        # reject the old confirmed manifest rather than silently recomputing it.
        _write_pdf(source_pdf, "Changed synthetic source after preflight.")
        status, changed_readiness = _request(port, "/api/v1/index/readiness", token=token, body=readiness_body)
        _expect(status == 409, "changed_manifest_not_rejected")
        _expect(changed_readiness.get("error", {}).get("code") == "manifest_changed", "changed_manifest_error_code_invalid")
        _expect(not runtime_guard_events.exists(), "runtime_readiness_attempted_forbidden_operation")
        _expect(runtime_guard_ready.is_file(), "runtime_guard_not_installed")
        clean_shutdown = _stop_runtime(process, log_stream, require_graceful=True)
        process = log_stream = None
        _expect(clean_shutdown, "ordinary_runtime_did_not_shutdown_cleanly")
    finally:
        if process is not None and log_stream is not None:
            _stop_runtime(process, log_stream, require_graceful=False)
    outcomes.append("manifest change after preflight rejected by ordinary runtime: passed")

    # This separate report fixture starts as a valid copy and is preflighted.
    # Subsequent structured replacements show that runtime readiness rereads
    # the chosen report and still has no durable content pin.
    source = root / "negative-report-source"
    source.mkdir()
    _write_pdf(source / "synthetic.pdf", "Negative report fixture source.")
    config = _make_config(source, root / "negative-config-roots")
    negative_config_path = root / "negative-resolved-config.json"
    negative_config_path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    negative_manifest = build_document_manifest(source)
    negative_plan = build_run_plan(config, negative_manifest.content_hash)
    negative_storage = root / "negative-report-storage"
    negative_storage.mkdir()
    negative_index = negative_storage / "indexes" / negative_plan.index_fingerprint
    (negative_index / "chroma").mkdir(parents=True)
    index_manifest = _IndexStoreManifest(
        index_fingerprint=negative_plan.index_fingerprint,
        document_manifest_hash=negative_manifest.content_hash,
    )
    (negative_index / "index-manifest.json").write_text(index_manifest.canonical_json(), encoding="utf-8")
    replacement_build_id = "e" * 32
    replacement_report = _write_report_copy(
        report_path,
        negative_storage,
        replacement_build_id,
        manifest_hash=negative_manifest.content_hash,
        index_fingerprint=negative_plan.index_fingerprint,
    )
    replacement_before = replacement_report.read_bytes()
    preflight_copy = _run_preflight(
        root,
        config_path=negative_config_path,
        source=source,
        storage=negative_storage,
        build_id=replacement_build_id,
        manifest_hash=negative_manifest.content_hash,
        fingerprint=negative_plan.index_fingerprint,
        label="replacement_copy",
        timeout=timeout,
    )
    _expect(preflight_copy.get("result") == "report_compatible_for_manual_review", "replacement_copy_preflight_failed")
    _expect(preflight_copy.get("reported_text_records") == report.text_records_persisted, "replacement_copy_counts_changed")

    # A benign same-identity/count report replacement is not pinned by the
    # current report validator; the runtime correctly reports compatibility,
    # but this is not a content-integrity guarantee.
    replacement_payload = json.loads(replacement_report.read_text(encoding="utf-8"))
    replacement_payload["total_elapsed_seconds"] = float(replacement_payload.get("total_elapsed_seconds", 0.0)) + 1.0
    replacement_report.write_text(json.dumps(replacement_payload, sort_keys=True), encoding="utf-8")
    _expect(replacement_report.read_bytes() != replacement_before, "report_replacement_fixture_unchanged")

    negative_environment, negative_guard_ready, negative_guard_events, negative_token, negative_port = _runtime_environment(
        root,
        source=source,
        storage=negative_storage,
        report_path=replacement_report,
        guard_label="replacement",
    )
    negative_process = negative_log_stream = None
    try:
        negative_process, negative_log_stream, _ = _start_runtime(
            negative_environment, root / "runtime-replacement.log", timeout
        )
        request_body = {
            "resolved_config": config.model_dump(mode="json"),
            "expected_manifest_hash": negative_manifest.content_hash,
            "workflow": "evaluation",
        }
        status, reread = _request(negative_port, "/api/v1/index/readiness", token=negative_token, body=request_body)
        _expect(status == 200 and reread.get("status") == "report_match", "same_identity_report_replacement_not_reread")
        _expect(reread.get("reported_text_records") == report.text_records_persisted, "same_identity_replacement_counts_changed")

        replacement_payload["status"] = "failed"
        replacement_payload["partial_persistence"] = False
        replacement_report.write_text(json.dumps(replacement_payload, sort_keys=True), encoding="utf-8")
        status, failed = _request(negative_port, "/api/v1/index/readiness", token=negative_token, body=request_body)
        _expect(status == 200 and failed.get("status") == "mismatch", "failed_replacement_report_not_rejected")
        _expect(failed.get("reason_code") == "build_failed", "failed_replacement_reason_invalid")

        replacement_payload["status"] = "completed"
        replacement_payload["partial_persistence"] = True
        replacement_report.write_text(json.dumps(replacement_payload, sort_keys=True), encoding="utf-8")
        status, partial = _request(negative_port, "/api/v1/index/readiness", token=negative_token, body=request_body)
        _expect(status == 200 and partial.get("status") == "mismatch", "partial_replacement_report_not_rejected")
        _expect(partial.get("reason_code") == "partial_persistence", "partial_replacement_reason_invalid")
        _expect(not negative_guard_events.exists() and negative_guard_ready.is_file(), "negative_readiness_attempted_forbidden_operation")
        _expect(negative_token not in json.dumps([reread, failed, partial]), "negative_response_disclosed_token")
        _stop_runtime(negative_process, negative_log_stream, require_graceful=True)
        negative_process = negative_log_stream = None
    finally:
        if negative_process is not None and negative_log_stream is not None:
            _stop_runtime(negative_process, negative_log_stream, require_graceful=False)
    outcomes.append("post-preflight report replacement, failed, and partial outcomes: passed")

    _expect(token_snapshot == {key: value for key, value in os.environ.items() if key.startswith("RAG_WORKBENCH_")}, "persistent_runtime_environment_changed")
    _expect((root / "runtime-positive" / "databases" / "jobs.sqlite3").is_file(), "runtime_jobs_database_not_isolated")
    _expect((root / "runtime-positive" / "databases" / "catalog.sqlite3").is_file(), "runtime_catalog_database_not_isolated")
    outcomes.append("temporary-only runtime configuration and cleanup: passed")
    return outcomes


def run_smoke(*, timeout: float = 45) -> list[str]:
    if not 5 <= timeout <= 120:
        raise SmokeError("timeout_invalid")
    with tempfile.TemporaryDirectory(prefix="rag-manual-handoff-") as temporary:
        root = Path(temporary)
        try:
            return _run_smoke(root, timeout)
        except SmokeError:
            raise
        except BaseException as exc:
            # Preserve bounded diagnostics only in the disposable test root.
            (root / "smoke-failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
            raise SmokeError("unexpected_" + type(exc).__name__) from exc


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "--build-child":
        if len(arguments) != 6:
            return 2
        _, config, source, storage, result, failure = arguments
        return _build_child(Path(config), Path(source), Path(storage), Path(result), Path(failure))
    import argparse

    parser = argparse.ArgumentParser(description="Run an isolated real-Chroma manual report handoff smoke test.")
    parser.add_argument("--timeout", type=float, default=45, help="bounded runtime readiness wait in seconds (5–120)")
    args = parser.parse_args(arguments)
    try:
        for outcome in run_smoke(timeout=args.timeout):
            print(outcome)
        print("Report-level evidence only; Chroma was not opened during preflight or runtime readiness.")
        return 0
    except SmokeError as exc:
        print(f"manual_report_handoff_smoke_failed: {exc.code}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
