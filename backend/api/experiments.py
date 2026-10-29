"""Starlette routes for authenticated experiment execution and reads."""

from __future__ import annotations

import hmac
import json
from dataclasses import asdict
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from backend.config.schema import ResolvedRunConfig
from backend.engine.manifest import (
    DocumentManifest,
    ManifestSourceChangedError,
    ManifestValidationError,
    build_document_manifest,
)
from backend.experiments.ablation_plans import create_ablation_plan
from backend.experiments.run_plan import build_run_plan
from backend.experiments.artifact_store import ArtifactIntegrityError, ArtifactNotFoundError
from backend.experiments.catalog import CatalogIntegrityError, CatalogNotFoundError, ExperimentCatalogError
from backend.experiments.serialization import SerializationError, serialize_ablation_result, serialize_evaluation_result
from backend.services.experiment_service import (
    ExperimentService,
    ExperimentServiceError,
    PersistedAblationRun,
    PersistedEvaluationRun,
    ServiceExecutionError,
    ServiceInputError,
    ServicePersistenceError,
    ServiceResultIntegrityError,
)
from backend.services.job_manager import (
    ExperimentJobError,
    ExperimentJobManager,
    JobCapacityError,
    JobNotFoundError,
    JobStateError,
)
from backend.services.index_readiness import IndexReadinessInputError

from .schemas import (
    AblationRequest,
    EvaluationRequest,
    ManifestResponse,
    IndexReadinessRequest,
    IndexReadinessResponse,
    RunPlanPreparationRequest,
    RunPlanPreparationResponse,
    RunPlanRequest,
    canonical_sha256,
    parse_json_object,
)


class ApiRequestError(ValueError):
    def __init__(self, code: str, status_code: int = 400) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


_SQLITE_INTEGER_MAX = 9_223_372_036_854_775_807


def _parse_pagination_value(raw: str | None, default: int) -> int:
    """Parse a bounded non-negative decimal value safe for SQLite bindings."""
    if raw is None:
        return default
    if not raw.isdecimal() or len(raw) > 19:
        raise ApiRequestError("query_invalid", 400)
    value = int(raw)
    if value > _SQLITE_INTEGER_MAX:
        raise ApiRequestError("query_invalid", 400)
    return value


class ExperimentSecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, api_token: str) -> None:
        super().__init__(app)
        self._api_token = api_token

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/api/v1/"):
            header = request.headers.get("authorization", "")
            supplied = header[7:] if header.startswith("Bearer ") else ""
            if not supplied or not hmac.compare_digest(supplied, self._api_token):
                return _response_error("authentication_required", 401)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response


def _response_error(code: str, status_code: int) -> JSONResponse:
    response = JSONResponse({"error": {"code": code}}, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _map_error(exc: BaseException) -> JSONResponse:
    if isinstance(exc, ApiRequestError):
        return _response_error(exc.code, exc.status_code)
    if isinstance(exc, (CatalogNotFoundError, ArtifactNotFoundError)):
        return _response_error("artifact_not_found", 404)
    if isinstance(exc, (CatalogIntegrityError, ArtifactIntegrityError, SerializationError, ServiceResultIntegrityError)):
        return _response_error("artifact_integrity_error", 409)
    if isinstance(exc, ServiceInputError):
        return _response_error("request_invalid", 400)
    if isinstance(exc, ServicePersistenceError):
        return _response_error("persistence_failed", 503)
    if isinstance(exc, ServiceExecutionError):
        return _response_error("execution_failed", 500)
    if isinstance(exc, ExperimentCatalogError):
        return _response_error("request_invalid", 400)
    if isinstance(exc, ExperimentServiceError):
        return _response_error("service_error", 500)
    if isinstance(exc, JobNotFoundError):
        return _response_error("job_not_found", 404)
    if isinstance(exc, JobCapacityError):
        return _response_error("job_capacity_exhausted", 429)
    if isinstance(exc, JobStateError):
        return _response_error(exc.error_code, 409 if exc.error_code == "job_not_cancellable" else 503)
    if isinstance(exc, ExperimentJobError):
        return _response_error(exc.error_code, 400)
    return _response_error("internal_error", 500)


async def _read_json(request: Request, max_bytes: int) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise ApiRequestError("unsupported_content_type", 415)
    chunks: list[bytes] = []
    total = 0
    try:
        async for chunk in request.stream():
            total += len(chunk)
            if total > max_bytes:
                raise ApiRequestError("request_body_too_large", 413)
            chunks.append(chunk)
        text = b"".join(chunks).decode("utf-8", "strict")
        return parse_json_object(text)
    except ApiRequestError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise ApiRequestError("request_invalid", 400)


def _model(model_type, payload: dict[str, Any]):
    try:
        return model_type.model_validate(payload)
    except Exception as exc:
        raise ApiRequestError("request_invalid", 400) from exc


def _domain(model, method: str):
    try:
        return getattr(model, method)()
    except Exception as exc:
        raise ApiRequestError("request_invalid", 400) from exc


def _artifact(record) -> dict[str, Any]:
    return {
        "artifact_id": record.artifact_id,
        "artifact_type": record.artifact_type,
        "relative_path": record.relative_path,
        "byte_length": record.byte_length,
        "serialization_schema_version": record.serialization_schema_version,
        "benchmark_id": record.benchmark_id,
        "benchmark_version": record.benchmark_version,
        "status": record.status,
        "run_fingerprint": record.run_fingerprint,
        "evaluation_fingerprint": record.evaluation_fingerprint,
        "document_manifest_hash": record.document_manifest_hash,
        "shared_index_fingerprint": record.shared_index_fingerprint,
        "case_count": record.case_count,
        "succeeded_case_count": record.succeeded_case_count,
        "failed_case_count": record.failed_case_count,
    }


def _metric(item) -> dict[str, Any]:
    return {name: getattr(item, name) for name in ("artifact_id", "variant_id", "metric_id", "metric_name", "status", "value", "unit", "optimization_direction", "reference_kind", "implementation_version", "eligible_count", "skipped_count", "failed_count", "unavailable_count", "model_identifier", "methodology_note")}


def _comparison(item) -> dict[str, Any]:
    return {"artifact_id": item.artifact_id, "metric_id": item.metric_id, "state": item.state, "reason_code": item.reason_code}


def _evaluation_submission(value: PersistedEvaluationRun) -> dict[str, Any]:
    return {"artifact": _artifact(value.record), "result": {"benchmark_id": value.result.report.benchmark_id, "benchmark_version": value.result.report.benchmark_version, "status": value.record.status, "run_fingerprint": value.result.report.run_fingerprint, "evaluation_fingerprint": value.result.report.evaluation_fingerprint}}


def _ablation_submission(value: PersistedAblationRun) -> dict[str, Any]:
    return {"artifact": _artifact(value.record), "result": {"status": value.result.status, "benchmark_id": value.result.benchmark_id, "benchmark_version": value.result.benchmark_version, "document_manifest_hash": value.result.document_manifest_hash, "shared_index_fingerprint": value.result.shared_index_fingerprint, "variants": [{"variant_id": item.variant_id, "status": item.status, "error_code": item.error_code} for item in value.result.variants], "comparisons": [{"metric_id": item.metric_id, "state": item.state, "reason_code": item.reason_code} for item in value.result.metric_comparisons]}}


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "api_version": "v1"})


async def _current_manifest(request: Request) -> DocumentManifest:
    source_root = getattr(request.app.state, "document_source_root", None)
    if source_root is None:
        raise ApiRequestError("manifest_source_unconfigured", 503)
    try:
        return await run_in_threadpool(build_document_manifest, source_root)
    except ManifestSourceChangedError as exc:
        raise ApiRequestError("manifest_source_changed", 409) from exc
    except ManifestValidationError as exc:
        raise ApiRequestError("manifest_invalid", 409) from exc
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError) as exc:
        raise ApiRequestError("manifest_source_unavailable", 503) from exc


async def inspect_document_manifest(request: Request) -> JSONResponse:
    try:
        manifest = await _current_manifest(request)
        response_model = ManifestResponse(
            schema_version=manifest.schema_version,
            content_hash=manifest.content_hash,
            entry_count=len(manifest.entries),
            empty=not manifest.entries,
            entries=tuple(entry.model_dump(mode="json") for entry in manifest.entries),
        )
        payload = response_model.model_dump(mode="json")
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > request.app.state.max_response_body_bytes:
            raise ApiRequestError("manifest_response_too_large", 413)
        response = JSONResponse(payload)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    except Exception as exc:
        return _map_error(exc)


async def index_readiness(request: Request) -> JSONResponse:
    try:
        payload = await _read_json(request, request.app.state.max_request_body_bytes)
        model = _model(IndexReadinessRequest, payload)
        if set(model.resolved_config) != set(ResolvedRunConfig.model_fields):
            raise ApiRequestError("configuration_invalid", 400)
        try:
            config = ResolvedRunConfig.model_validate(model.resolved_config)
        except Exception as exc:
            raise ApiRequestError("configuration_invalid", 400) from exc
        service = getattr(request.app.state, "index_readiness_service", None)
        if service is None:
            raise ApiRequestError("index_readiness_unconfigured", 503)
        try:
            result = await run_in_threadpool(service.check, config, model.expected_manifest_hash, model.workflow)
        except IndexReadinessInputError as exc:
            raise ApiRequestError(exc.code, 409 if exc.code in {"manifest_empty", "manifest_changed"} else 400) from exc
        except ManifestSourceChangedError as exc:
            raise ApiRequestError("manifest_source_changed", 409) from exc
        except ManifestValidationError as exc:
            raise ApiRequestError("manifest_invalid", 409) from exc
        except OSError as exc:
            raise ApiRequestError("manifest_source_unavailable", 503) from exc
        return JSONResponse(IndexReadinessResponse.model_validate(asdict(result)).model_dump(mode="json"))
    except Exception as exc:
        return _map_error(exc)


async def prepare_run_plan(request: Request) -> JSONResponse:
    try:
        payload = await _read_json(request, request.app.state.max_request_body_bytes)
        supplied_hash = payload.get("expected_manifest_hash")
        if supplied_hash is not None:
            try:
                canonical_sha256(supplied_hash)
            except ValueError as exc:
                raise ApiRequestError("manifest_hash_invalid", 400) from exc
        model = _model(RunPlanPreparationRequest, payload)
        if set(model.resolved_config) != set(ResolvedRunConfig.model_fields):
            raise ApiRequestError("configuration_invalid", 400)
        try:
            config = ResolvedRunConfig.model_validate(model.resolved_config)
        except Exception as exc:
            raise ApiRequestError("configuration_invalid", 400) from exc
        if (
            config.reset_collections_on_start
            or config.workbench_policy.allow_destructive_index_reset
            or config.workbench_policy.allow_collection_delete
        ):
            raise ApiRequestError("destructive_configuration_forbidden", 400)
        try:
            config.validate_for_workbench()
        except Exception as exc:
            raise ApiRequestError("configuration_invalid", 400) from exc

        manifest = await _current_manifest(request)
        if not manifest.entries:
            raise ApiRequestError("manifest_empty", 409)
        if manifest.content_hash != model.expected_manifest_hash:
            raise ApiRequestError("manifest_changed", 409)
        try:
            plan = build_run_plan(config, manifest.content_hash)
            plan.verify_integrity()
        except Exception as exc:
            raise ApiRequestError("run_plan_invalid", 400) from exc

        response_model = RunPlanPreparationResponse(
            run_plan=RunPlanRequest.model_validate(plan.model_dump(mode="json")),
            manifest={
                "schema_version": manifest.schema_version,
                "content_hash": manifest.content_hash,
                "entry_count": len(manifest.entries),
            },
        )
        response_payload = response_model.model_dump(mode="json")
        encoded = json.dumps(response_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > request.app.state.max_response_body_bytes:
            raise ApiRequestError("response_body_too_large", 413)
        response = JSONResponse(response_payload)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    except Exception as exc:
        return _map_error(exc)


async def runtime_models(request: Request) -> JSONResponse:
    try:
        registry = getattr(request.app.state, "model_registry", None)
        if registry is None:
            return _response_error("runtime_registry_unconfigured", 503)
        snapshot = await run_in_threadpool(registry.snapshot)
        return JSONResponse({"components": [{
            "component_kind": item.component_kind,
            "model_identifier": item.model_identifier,
            "state": item.state,
            "error_code": item.error_code,
            "explicitly_warmed": item.explicitly_warmed,
        } for item in snapshot.components]})
    except Exception as exc:
        return _map_error(exc)


async def submit_evaluation(request: Request) -> JSONResponse:
    service: ExperimentService = request.app.state.experiment_service
    try:
        payload = await _read_json(request, request.app.state.max_request_body_bytes)
        model = _model(EvaluationRequest, payload)
        run_plan, suite = _domain(model, "to_domain")
        result = await run_in_threadpool(service.run_evaluation, run_plan, suite)
        return JSONResponse(_evaluation_submission(result), status_code=201)
    except Exception as exc:
        return _map_error(exc)


async def submit_ablation(request: Request) -> JSONResponse:
    service: ExperimentService = request.app.state.experiment_service
    try:
        payload = await _read_json(request, request.app.state.max_request_body_bytes)
        model = _model(AblationRequest, payload)
        plan, suite = _domain(model, "to_domain")
        result = await run_in_threadpool(service.run_ablation, plan, suite)
        return JSONResponse(_ablation_submission(result), status_code=201)
    except Exception as exc:
        return _map_error(exc)


def _job_record(record) -> dict[str, Any]:
    return {name: getattr(record, name) for name in (
        "job_id", "job_type", "status", "created_at", "started_at", "finished_at",
        "benchmark_id", "benchmark_version", "run_fingerprint", "shared_index_fingerprint",
        "document_manifest_hash", "artifact_id", "error_code", "result_status",
    )}


def _require_job_manager(request: Request) -> ExperimentJobManager:
    manager = getattr(request.app.state, "job_manager", None)
    if manager is None:
        raise ApiRequestError("job_execution_unconfigured", 503)
    return manager


async def submit_evaluation_job(request: Request) -> JSONResponse:
    try:
        manager = _require_job_manager(request)
        model = _model(EvaluationRequest, await _read_json(request, request.app.state.max_request_body_bytes))
        run_plan, suite = _domain(model, "to_domain")
        record = await run_in_threadpool(manager.submit_evaluation, run_plan, suite)
        response = JSONResponse({"job": _job_record(record)}, status_code=202)
        response.headers["Location"] = f"/api/v1/jobs/{record.job_id}"
        return response
    except Exception as exc:
        return _map_error(exc)


async def submit_ablation_job(request: Request) -> JSONResponse:
    try:
        manager = _require_job_manager(request)
        model = _model(AblationRequest, await _read_json(request, request.app.state.max_request_body_bytes))
        plan, suite = _domain(model, "to_domain")
        record = await run_in_threadpool(manager.submit_ablation, plan, suite)
        response = JSONResponse({"job": _job_record(record)}, status_code=202)
        response.headers["Location"] = f"/api/v1/jobs/{record.job_id}"
        return response
    except Exception as exc:
        return _map_error(exc)


async def get_job(request: Request) -> JSONResponse:
    try:
        manager = _require_job_manager(request)
        record = await run_in_threadpool(manager.get_job, request.path_params["job_id"])
        return JSONResponse({"job": _job_record(record)})
    except Exception as exc:
        return _map_error(exc)


async def list_jobs(request: Request) -> JSONResponse:
    try:
        manager = _require_job_manager(request)
        query = request.query_params
        kwargs: dict[str, Any] = {}
        for name in ("job_type", "status"):
            if name in query:
                kwargs[name] = query[name]
        for name, default in (("limit", 100), ("offset", 0)):
            kwargs[name] = _parse_pagination_value(query.get(name), default)
        values = await run_in_threadpool(manager.list_jobs, **kwargs)
        return JSONResponse({"jobs": [_job_record(item) for item in values]})
    except Exception as exc:
        return _map_error(exc)


async def cancel_job(request: Request) -> JSONResponse:
    try:
        manager = _require_job_manager(request)
        record = await run_in_threadpool(manager.cancel_job, request.path_params["job_id"])
        return JSONResponse({"job": _job_record(record)})
    except Exception as exc:
        return _map_error(exc)


def _query_filters(request: Request) -> dict[str, Any]:
    query = request.query_params
    values: dict[str, Any] = {}
    for name in ("artifact_type", "benchmark_id", "benchmark_version", "run_fingerprint", "index_fingerprint", "status"):
        if name in query:
            values[name] = query[name]
    for name, default in (("limit", 100), ("offset", 0)):
        values[name] = _parse_pagination_value(query.get(name), default)
    return values


async def list_artifacts(request: Request) -> JSONResponse:
    try:
        records = await run_in_threadpool(request.app.state.experiment_service.list_artifacts, **_query_filters(request))
        return JSONResponse({"artifacts": [_artifact(item) for item in records]})
    except Exception as exc:
        return _map_error(exc)


async def get_artifact(request: Request) -> JSONResponse:
    try:
        record = await run_in_threadpool(request.app.state.experiment_service.get_artifact, request.path_params["artifact_id"])
        return JSONResponse(_artifact(record))
    except Exception as exc:
        return _map_error(exc)


async def get_metrics(request: Request) -> JSONResponse:
    try:
        variant_id = request.query_params.get("variant_id")
        values = await run_in_threadpool(request.app.state.experiment_service.list_metric_aggregates, request.path_params["artifact_id"], variant_id=variant_id)
        return JSONResponse({"metrics": [_metric(item) for item in values]})
    except Exception as exc:
        return _map_error(exc)


async def get_comparisons(request: Request) -> JSONResponse:
    try:
        record = await run_in_threadpool(request.app.state.experiment_service.get_artifact, request.path_params["artifact_id"])
        if record.artifact_type != "ablation_result":
            raise ApiRequestError("artifact_type_not_supported", 409)
        values = await run_in_threadpool(request.app.state.experiment_service.list_comparisons, request.path_params["artifact_id"])
        return JSONResponse({"comparisons": [_comparison(item) for item in values]})
    except Exception as exc:
        return _map_error(exc)


def _ablation_plan_from_query(request: Request, record) -> Any:
    raw_config = request.query_params.get("base_config")
    manifest = request.query_params.get("document_manifest_hash")
    if raw_config is None or manifest is None or len(raw_config) > 2 * 1024 * 1024:
        raise ApiRequestError("ablation_plan_required", 400)
    try:
        config = ResolvedRunConfig.model_validate(parse_json_object(raw_config))
        plan = create_ablation_plan(config, manifest)
    except Exception as exc:
        raise ApiRequestError("ablation_plan_invalid", 400) from exc
    if record.document_manifest_hash != plan.document_manifest_hash or record.shared_index_fingerprint != plan.shared_index_fingerprint:
        raise ApiRequestError("ablation_plan_mismatch", 409)
    return plan


async def load_result(request: Request) -> Response:
    service: ExperimentService = request.app.state.experiment_service
    try:
        record = await run_in_threadpool(service.get_artifact, request.path_params["artifact_id"])
        if record.artifact_type == "evaluation_result":
            value = await run_in_threadpool(service.load_evaluation_result, record.artifact_id)
            serialized = serialize_evaluation_result(value)
        else:
            plan = _ablation_plan_from_query(request, record)
            value = await run_in_threadpool(service.load_ablation_result, record.artifact_id, ablation_plan=plan)
            serialized = serialize_ablation_result(value)
        data = serialized.encode("utf-8")
        if len(data) > request.app.state.max_response_body_bytes:
            raise ApiRequestError("response_body_too_large", 413)
        response = Response(data, media_type="application/json")
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    except Exception as exc:
        return _map_error(exc)


def build_routes(service: ExperimentService, *, max_request_body_bytes: int, job_manager: ExperimentJobManager | None = None) -> list[Route]:
    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/api/v1/documents/manifest", inspect_document_manifest, methods=["GET"]),
        Route("/api/v1/index/readiness", index_readiness, methods=["POST"]),
        Route("/api/v1/run-plans", prepare_run_plan, methods=["POST"]),
        Route("/api/v1/evaluations", submit_evaluation, methods=["POST"]),
        Route("/api/v1/ablations", submit_ablation, methods=["POST"]),
        Route("/api/v1/runtime/models", runtime_models, methods=["GET"]),
        Route("/api/v1/jobs/evaluations", submit_evaluation_job, methods=["POST"]),
        Route("/api/v1/jobs/ablations", submit_ablation_job, methods=["POST"]),
        Route("/api/v1/jobs", list_jobs, methods=["GET"]),
        Route("/api/v1/jobs/{job_id}/cancel", cancel_job, methods=["POST"]),
        Route("/api/v1/jobs/{job_id}", get_job, methods=["GET"]),
        Route("/api/v1/artifacts", list_artifacts, methods=["GET"]),
        Route("/api/v1/artifacts/{artifact_id}/metrics", get_metrics, methods=["GET"]),
        Route("/api/v1/artifacts/{artifact_id}/comparisons", get_comparisons, methods=["GET"]),
        Route("/api/v1/artifacts/{artifact_id}/result", load_result, methods=["GET"]),
        Route("/api/v1/artifacts/{artifact_id}", get_artifact, methods=["GET"]),
    ]
    return routes


__all__ = ["ExperimentSecurityMiddleware", "build_routes"]
