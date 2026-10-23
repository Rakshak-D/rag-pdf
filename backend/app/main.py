"""Authenticated Starlette application for the experiment service."""

from __future__ import annotations

from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool

from backend.services.experiment_service import ExperimentService
from backend.services.job_manager import ExperimentJobManager

from backend.api.experiments import build_routes, ExperimentSecurityMiddleware


def create_app(service: ExperimentService, *, api_token: str, max_request_body_bytes: int = 24 * 1024 * 1024, job_manager: ExperimentJobManager | None = None) -> Starlette:
    """Create an explicitly configured local experiment API.

    The factory never constructs pipeline components or opens storage. An
    external ASGI server is responsible for deployment and lifecycle policy.
    """

    if not isinstance(service, ExperimentService):
        raise ValueError("service is invalid")
    if not isinstance(api_token, str) or not api_token or len(api_token) > 4096 or any(char.isspace() for char in api_token):
        raise ValueError("api_token is invalid")
    if isinstance(max_request_body_bytes, bool) or not isinstance(max_request_body_bytes, int) or not 0 < max_request_body_bytes <= 64 * 1024 * 1024:
        raise ValueError("max_request_body_bytes is invalid")
    if job_manager is not None and not isinstance(job_manager, ExperimentJobManager):
        raise ValueError("job_manager is invalid")

    @asynccontextmanager
    async def lifespan(_app):
        if job_manager is not None:
            await run_in_threadpool(job_manager.start)
        try:
            yield
        finally:
            if job_manager is not None:
                await run_in_threadpool(job_manager.shutdown)

    app = Starlette(routes=build_routes(service, max_request_body_bytes=max_request_body_bytes, job_manager=job_manager), lifespan=lifespan)
    app.state.experiment_service = service
    app.state.max_request_body_bytes = max_request_body_bytes
    app.state.max_response_body_bytes = max_request_body_bytes
    app.state.job_manager = job_manager
    app.add_middleware(ExperimentSecurityMiddleware, api_token=api_token)
    return app


__all__ = ["create_app"]
