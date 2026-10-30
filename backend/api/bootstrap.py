"""Authenticated HTTP contract for first-run, fresh-only index builds."""

from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from backend.api.experiments import ApiRequestError, _read_json, _response_error
from backend.config.paths import PathContainmentError, resolve_disjoint_paths
from backend.config.schema import ResolvedRunConfig, WorkbenchSafetyPolicy
from backend.engine.manifest import ManifestSourceChangedError, ManifestValidationError, build_document_manifest
from backend.experiments.run_plan import RunPlan, build_run_plan
from backend.services.bootstrap_jobs import BootstrapJobError, BootstrapJobManager, BuildJob
from backend.services.index_inventory import IndexInventoryInputError


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class BootstrapPreviewRequest(_StrictModel):
    resolved_config: dict[str, Any]


class BootstrapJobRequest(_StrictModel):
    resolved_config: dict[str, Any]
    expected_manifest_hash: str
    expected_index_fingerprint: str
    client_request_id: str

    @field_validator("expected_manifest_hash", "expected_index_fingerprint")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        try:
            return RunPlan.validate_sha256(value)
        except Exception as exc:
            raise ValueError("invalid sha256") from exc

    @field_validator("client_request_id")
    @classmethod
    def validate_request_id(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", value or ""):
            raise ValueError("invalid client request id")
        return value


class BootstrapPreviewResponse(_StrictModel):
    document_count: int = Field(ge=1, le=1_000_000)
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    index_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    existing_index: bool
    fresh_build_eligible: bool
    reason_code: str = Field(max_length=64, pattern=r"^[a-z][a-z0-9_]*$")


class BootstrapJobView(_StrictModel):
    job_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    client_request_id: str = Field(min_length=16, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    status: str = Field(pattern=r"^(queued|running|succeeded|failed|interrupted)$")
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    index_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: str = Field(max_length=64)
    started_at: str | None = Field(default=None, max_length=64)
    finished_at: str | None = Field(default=None, max_length=64)
    error_code: str | None = Field(default=None, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    build_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    report_reference: str | None = Field(default=None, max_length=128)
    text_records: int | None = Field(default=None, ge=0)
    image_records: int | None = Field(default=None, ge=0)
    partial_persistence: bool | None = None


class BootstrapJobsResponse(_StrictModel):
    jobs: list[BootstrapJobView] = Field(max_length=100)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=1_000_000)


class BootstrapInventoryRequest(_StrictModel):
    resolved_config: dict[str, Any]
    expected_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=512)


class BootstrapInventoryCandidateView(_StrictModel):
    candidate_id: str = Field(max_length=64, pattern=r"^(?:[0-9a-f]{32}|entry-[0-9a-f]{16})$")
    build_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    report_reference: str | None = Field(
        default=None, max_length=96,
        pattern=r"^build-artifacts/[0-9a-f]{32}/build-report\.json$",
    )
    compatibility: Literal["report_match", "mismatch", "failed", "incomplete", "invalid", "unavailable"]
    reason_code: str = Field(max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    expected_index_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    document_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reported_index_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    reported_document_manifest_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    report_status: Literal["completed", "completed_with_warnings", "failed"] | None = None
    reported_text_records: int | None = Field(default=None, ge=0)
    reported_image_records: int | None = Field(default=None, ge=0)
    check_level: Literal["report"]
    store_checked: Literal[False]


class BootstrapInventoryResponse(_StrictModel):
    expected_index_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    document_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidates: list[BootstrapInventoryCandidateView] = Field(max_length=100)
    total_candidates: int | None = Field(default=None, ge=0, le=512)
    offset: int = Field(ge=0, le=512)
    limit: int = Field(ge=1, le=100)
    complete: bool
    reason_code: str = Field(max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    check_level: Literal["report"]
    store_checked: Literal[False]


def _safe_components(path: Path) -> None:
    if not path.is_absolute():
        raise ApiRequestError("bootstrap_path_invalid", 503)
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            if current.is_symlink():
                raise ApiRequestError("bootstrap_path_invalid", 503)
        except OSError as exc:
            raise ApiRequestError("bootstrap_path_unavailable", 503) from exc


def _validated_config(value: dict[str, Any], source_root: Path) -> ResolvedRunConfig:
    if set(value) != set(ResolvedRunConfig.model_fields):
        raise ApiRequestError("configuration_invalid", 400)
    policy = value.get("workbench_policy")
    if not isinstance(policy, dict) or set(policy) != set(WorkbenchSafetyPolicy.model_fields):
        raise ApiRequestError("configuration_invalid", 400)
    try:
        # The request parser rejects duplicate keys and non-finite numbers;
        # re-encoding with allow_nan=False keeps this boundary explicit.
        config = ResolvedRunConfig.model_validate_json(json.dumps(value, allow_nan=False), strict=True)
    except Exception as exc:
        raise ApiRequestError("configuration_invalid", 400) from exc
    if (config.reset_collections_on_start or config.workbench_policy.allow_destructive_index_reset
            or config.workbench_policy.allow_collection_delete):
        raise ApiRequestError("destructive_configuration_forbidden", 400)
    try:
        config.validate_for_workbench()
        configured_source = Path(config.pdf_dir).expanduser()
        if not configured_source.is_absolute():
            raise ValueError
        _safe_components(configured_source)
        if os.path.normcase(str(configured_source.resolve(strict=False))) != os.path.normcase(str(source_root.resolve(strict=False))):
            raise ApiRequestError("configuration_source_mismatch", 400)
    except ApiRequestError:
        raise
    except Exception as exc:
        raise ApiRequestError("configuration_invalid", 400) from exc
    return config


def _request_model(model_type, payload: dict[str, Any]):
    try:
        return model_type.model_validate(payload)
    except Exception as exc:
        raise ApiRequestError("request_invalid", 400) from exc


def _trusted_roots(request: Request) -> tuple[Path, Path]:
    source = Path(request.app.state.bootstrap_source_root)
    storage = Path(request.app.state.bootstrap_storage_root)
    _safe_components(source)
    _safe_components(storage)
    if not source.is_dir() or (storage.exists() and not storage.is_dir()):
        raise ApiRequestError("bootstrap_path_unavailable", 503)
    try:
        resolve_disjoint_paths(source, storage, first_label="source", second_label="index storage")
    except PathContainmentError as exc:
        raise ApiRequestError("bootstrap_paths_overlap", 503) from exc
    return source, storage


def _trusted_state(request: Request) -> None:
    state = Path(request.app.state.bootstrap_settings.state_dir)
    database = Path(request.app.state.bootstrap_settings.database_path)
    reservation = state / ".bootstrap-instance-owner"
    _safe_components(state)
    for candidate in (database, Path(str(database) + "-wal"), Path(str(database) + "-shm")):
        _safe_components(candidate)
    _safe_components(reservation)
    if not state.is_dir() or (database.exists() and not database.is_file()):
        raise ApiRequestError("bootstrap_state_unavailable", 503)


def _inspect(config: ResolvedRunConfig, source: Path, storage: Path) -> dict[str, Any]:
    try:
        manifest = build_document_manifest(source)
    except ManifestSourceChangedError as exc:
        raise ApiRequestError("manifest_source_changed", 409) from exc
    except ManifestValidationError as exc:
        raise ApiRequestError("manifest_invalid", 409) from exc
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError) as exc:
        raise ApiRequestError("manifest_source_unavailable", 503) from exc
    if not manifest.entries:
        raise ApiRequestError("manifest_empty", 409)
    try:
        plan = build_run_plan(config, manifest.content_hash)
        plan.verify_integrity()
    except Exception as exc:
        raise ApiRequestError("run_plan_invalid", 400) from exc
    index_path = storage / "indexes" / plan.index_fingerprint
    _safe_components(index_path)
    try:
        index_path.lstat()
        exists = True
    except FileNotFoundError:
        exists = False
    except OSError as exc:
        raise ApiRequestError("index_path_unavailable", 503) from exc
    return {
        "document_count": len(manifest.entries),
        "manifest_hash": manifest.content_hash,
        "index_fingerprint": plan.index_fingerprint,
        "run_fingerprint": plan.run_fingerprint,
        "existing_index": exists,
        "fresh_build_eligible": not exists,
        "reason_code": "index_already_exists" if exists else "fresh_build_available",
    }


def _job_payload(job: BuildJob) -> dict[str, Any]:
    build_id = job.build_id if isinstance(job.build_id, str) and re.fullmatch(r"[0-9a-f]{32}", job.build_id) else None
    report_reference = None
    if job.status == "succeeded" and build_id is not None and job.report_reference == "build-report.json":
        report_reference = f"build-artifacts/{build_id}/build-report.json"
    allowed_errors = {
        "server_restarted", "server_shutdown", "queue_admission_failed", "worker_claim_failed",
        "confirmation_changed", "build_failed", "job_result_state_conflict", "job_result_persistence_failed",
        "writer_lease_busy", "fresh_index_exists", "manifest_source_changed", "manifest_invalid",
        "build_report_invalid", "build_report_mismatch", "build_report_unavailable",
    }
    error_code = job.error_code if job.error_code in allowed_errors else ("build_failed" if job.error_code else None)
    return {
        "job_id": job.job_id,
        "client_request_id": job.client_request_id,
        "status": job.status,
        "manifest_hash": job.manifest_hash,
        "index_fingerprint": job.index_fingerprint,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "error_code": error_code,
        "build_id": build_id,
        "report_reference": report_reference,
        "text_records": job.text_records,
        "image_records": job.image_records,
        "partial_persistence": job.partial_persistence,
    }


def _bounded_json(request: Request, payload: dict[str, Any], *, status_code: int = 200) -> JSONResponse:
    try:
        validated = BootstrapPreviewResponse.model_validate(payload) if "document_count" in payload else None
        if validated is not None:
            payload = validated.model_dump(mode="json")
        elif "job" in payload:
            payload = {"job": BootstrapJobView.model_validate(payload["job"]).model_dump(mode="json")}
        elif "jobs" in payload:
            payload = BootstrapJobsResponse.model_validate(payload).model_dump(mode="json")
        elif "candidates" in payload:
            payload = BootstrapInventoryResponse.model_validate(payload).model_dump(mode="json")
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > request.app.state.max_response_body_bytes:
            raise ApiRequestError("response_body_too_large", 413)
    except ApiRequestError:
        raise
    except Exception as exc:
        raise ApiRequestError("bootstrap_response_invalid", 503) from exc
    return JSONResponse(payload, status_code=status_code)
def _manager(request: Request) -> BootstrapJobManager:
    manager = getattr(request.app.state, "bootstrap_job_manager", None)
    if manager is None:
        raise ApiRequestError("bootstrap_service_unavailable", 503)
    return manager


async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "api_version": "v1"})


async def status(request: Request) -> JSONResponse:
    try:
        _trusted_state(request)
        settings = request.app.state.bootstrap_settings
        return JSONResponse({
            "mode": "bootstrap", "status": "running", "accepting_jobs": _manager(request).accepting,
            "worker_count": 1, "queue_capacity": settings.queue_capacity,
        })
    except Exception as exc:
        return _map_bootstrap_error(exc)


async def preview(request: Request) -> JSONResponse:
    try:
        _trusted_state(request)
        payload = await _read_json(request, request.app.state.max_request_body_bytes)
        model = _request_model(BootstrapPreviewRequest, payload)
        source, storage = _trusted_roots(request)
        config = _validated_config(model.resolved_config, source)
        result = await run_in_threadpool(_inspect, config, source, storage)
        return _bounded_json(request, result)
    except Exception as exc:
        return _map_bootstrap_error(exc)


async def index_inventory(request: Request) -> JSONResponse:
    """Return bounded report-level inventory without depending on job admission."""

    try:
        payload = await _read_json(request, request.app.state.max_request_body_bytes)
        model = _request_model(BootstrapInventoryRequest, payload)
        source, storage = _trusted_roots(request)
        config = _validated_config(model.resolved_config, source)
        service = getattr(request.app.state, "index_inventory_service", None)
        limiter = getattr(request.app.state, "index_inventory_scan_semaphore", None)
        if service is None or limiter is None:
            raise ApiRequestError("index_inventory_unavailable", 503)
        async with limiter:
            result = await run_in_threadpool(
                service.discover,
                config,
                model.expected_manifest_hash,
                limit=model.limit,
                offset=model.offset,
            )
        response_payload = asdict(result)
        response_payload["candidates"] = list(response_payload["candidates"])
        return _bounded_json(request, response_payload)
    except Exception as exc:
        return _map_bootstrap_error(exc)


async def submit_job(request: Request) -> JSONResponse:
    try:
        _trusted_state(request)
        payload = await _read_json(request, request.app.state.max_request_body_bytes)
        model = _request_model(BootstrapJobRequest, payload)
        source, storage = _trusted_roots(request)
        config = _validated_config(model.resolved_config, source)
        result = await run_in_threadpool(_inspect, config, source, storage)
        if result["manifest_hash"] != model.expected_manifest_hash:
            raise ApiRequestError("manifest_changed", 409)
        if result["index_fingerprint"] != model.expected_index_fingerprint:
            raise ApiRequestError("index_fingerprint_changed", 409)
        manager = _manager(request)
        if result["existing_index"]:
            job = await run_in_threadpool(
                manager.find_idempotent,
                model.client_request_id,
                config,
                model.expected_manifest_hash,
                model.expected_index_fingerprint,
            )
            if job is None:
                raise ApiRequestError("index_already_exists", 409)
        else:
            job = await run_in_threadpool(
                manager.submit,
                model.client_request_id,
                config,
                model.expected_manifest_hash,
                model.expected_index_fingerprint,
            )
        response = _bounded_json(request, {"job": _job_payload(job)}, status_code=202)
        response.headers["Location"] = f"/api/v1/bootstrap/jobs/{job.job_id}"
        return response
    except Exception as exc:
        return _map_bootstrap_error(exc)


async def list_jobs(request: Request) -> JSONResponse:
    try:
        _trusted_state(request)
        limit = _pagination(request.query_params.get("limit"), 50, 100)
        offset = _pagination(request.query_params.get("offset"), 0, 1_000_000)
        jobs = await run_in_threadpool(_manager(request).list, limit, offset)
        return _bounded_json(request, {"jobs": [_job_payload(job) for job in jobs], "limit": limit, "offset": offset})
    except Exception as exc:
        return _map_bootstrap_error(exc)


def _pagination(value: str | None, default: int, maximum: int) -> int:
    if value is None:
        return default
    if not value.isdecimal() or len(value) > 10:
        raise ApiRequestError("query_invalid", 400)
    number = int(value)
    if number > maximum:
        raise ApiRequestError("query_invalid", 400)
    return number


async def get_job(request: Request) -> JSONResponse:
    try:
        _trusted_state(request)
        job_id = request.path_params.get("job_id", "")
        if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", job_id):
            raise ApiRequestError("job_not_found", 404)
        job = await run_in_threadpool(_manager(request).get, job_id)
        return _bounded_json(request, {"job": _job_payload(job)})
    except Exception as exc:
        return _map_bootstrap_error(exc)


def _map_bootstrap_error(exc: BaseException) -> JSONResponse:
    if isinstance(exc, ApiRequestError):
        return _response_error(exc.code, exc.status_code)
    if isinstance(exc, IndexInventoryInputError):
        code = exc.code
        if code in {"manifest_changed", "manifest_source_changed", "manifest_empty", "manifest_invalid"}:
            return _response_error(code, 409)
        if code in {
            "manifest_unavailable", "inventory_path_unavailable", "inventory_path_invalid",
            "source_root_unavailable", "storage_root_invalid", "storage_root_unavailable",
            "inventory_roots_overlap",
        }:
            return _response_error(code, 503)
        if re.fullmatch(r"[a-z][a-z0-9_]{1,63}", code or ""):
            return _response_error(code, 400)
        return _response_error("index_inventory_invalid", 400)
    if isinstance(exc, BootstrapJobError):
        code = exc.code
        if code == "job_not_found":
            return _response_error(code, 404)
        if code == "queue_capacity_exhausted":
            return _response_error(code, 429)
        if code in {"request_id_conflict", "fingerprint_already_active", "confirmation_changed", "job_input_invalid"}:
            return _response_error(code, 409 if code != "job_input_invalid" else 400)
        if code in {"manager_not_accepting", "job_store_unavailable", "job_admission_failed", "bootstrap_instance_owned", "bootstrap_ownership_unavailable"}:
            return _response_error(code, 503)
        if code == "job_schema_unsupported":
            return _response_error(code, 503)
        return _response_error("bootstrap_service_error", 503)
    if isinstance(exc, sqlite3.Error):
        return _response_error("job_store_unavailable", 503)
    # Keep failures from file, SQLite, and validation internals bounded.
    return _response_error("bootstrap_service_error", 503)


def build_bootstrap_routes() -> list[Route]:
    return [
        Route("/health", health, methods=["GET"]),
        Route("/api/v1/bootstrap/status", status, methods=["GET"]),
        Route("/api/v1/bootstrap/preview", preview, methods=["POST"]),
        Route("/api/v1/bootstrap/index-inventory", index_inventory, methods=["POST"]),
        Route("/api/v1/bootstrap/jobs", submit_job, methods=["POST"]),
        Route("/api/v1/bootstrap/jobs", list_jobs, methods=["GET"]),
        Route("/api/v1/bootstrap/jobs/{job_id}", get_job, methods=["GET"]),
    ]


__all__ = [
    "BootstrapInventoryRequest",
    "BootstrapInventoryResponse",
    "BootstrapPreviewRequest",
    "BootstrapJobRequest",
    "build_bootstrap_routes",
]
