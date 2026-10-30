"""Trusted configuration and composition for the bootstrap-only API."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import ipaddress
import logging
import os
from pathlib import Path
import re
from typing import Mapping

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool

from backend.config.paths import PathContainmentError, resolve_disjoint_paths
from backend.services.bootstrap_jobs import BootstrapJobManager
from backend.api.experiments import ExperimentSecurityMiddleware
from backend.api.bootstrap import build_bootstrap_routes


_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8010
_DEFAULT_BODY_LIMIT = 2 * 1024 * 1024
_MAX_BODY_LIMIT = 16 * 1024 * 1024
_ENV = {
    "api_token": "RAG_WORKBENCH_BOOTSTRAP_API_TOKEN",
    "source_root": "RAG_WORKBENCH_BOOTSTRAP_PDF_SOURCE_DIR",
    "storage_root": "RAG_WORKBENCH_BOOTSTRAP_INDEX_STORAGE_ROOT",
    "state_dir": "RAG_WORKBENCH_BOOTSTRAP_STATE_DIR",
    "host": "RAG_WORKBENCH_BOOTSTRAP_API_HOST",
    "port": "RAG_WORKBENCH_BOOTSTRAP_API_PORT",
    "max_request_body_bytes": "RAG_WORKBENCH_BOOTSTRAP_MAX_REQUEST_BODY_BYTES",
    "queue_capacity": "RAG_WORKBENCH_BOOTSTRAP_QUEUE_CAPACITY",
}
_LOGGER = logging.getLogger(__name__)


class BootstrapConfigurationError(ValueError):
    """Stable setting error that never includes a local path or secret."""

    def __init__(self, code: str):
        self.code = code if re.fullmatch(r"[a-z][a-z0-9_]{1,63}", code or "") else "bootstrap_configuration_invalid"
        super().__init__(self.code)


def _path(value: object, code: str) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise BootstrapConfigurationError(code)
    result = Path(value).expanduser()
    if not result.is_absolute():
        raise BootstrapConfigurationError(code)
    if ".." in result.parts:
        raise BootstrapConfigurationError(code)
    current = Path(result.anchor)
    for part in result.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise BootstrapConfigurationError("bootstrap_path_symlink")
    return result


def _host(value: object) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise BootstrapConfigurationError("bootstrap_host_invalid")
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError as exc:
        raise BootstrapConfigurationError("bootstrap_host_must_be_loopback") from exc
    if not parsed.is_loopback:
        raise BootstrapConfigurationError("bootstrap_host_must_be_loopback")
    return str(parsed)


def _integer(raw: str, maximum: int, code: str) -> int:
    if not isinstance(raw, str) or not re.fullmatch(r"[0-9]{1,10}", raw):
        raise BootstrapConfigurationError(code)
    number = int(raw)
    if number < 1 or number > maximum:
        raise BootstrapConfigurationError(code)
    return number


@dataclass(frozen=True)
class BootstrapSettings:
    api_token: str = field(repr=False)
    source_root: Path
    storage_root: Path
    state_dir: Path
    host: str = _DEFAULT_HOST
    port: int = _DEFAULT_PORT
    max_request_body_bytes: int = _DEFAULT_BODY_LIMIT
    queue_capacity: int = 2

    def __post_init__(self) -> None:
        if (not isinstance(self.api_token, str) or len(self.api_token) < 32 or len(self.api_token) > 4096
                or any(char.isspace() for char in self.api_token)):
            raise BootstrapConfigurationError("bootstrap_token_invalid")
        for name in ("source_root", "storage_root", "state_dir"):
            object.__setattr__(self, name, _path(getattr(self, name), "bootstrap_path_invalid"))
        object.__setattr__(self, "host", _host(self.host))
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not 1 <= self.port <= 65535:
            raise BootstrapConfigurationError("bootstrap_port_invalid")
        if (isinstance(self.max_request_body_bytes, bool) or not isinstance(self.max_request_body_bytes, int)
                or not 1 <= self.max_request_body_bytes <= _MAX_BODY_LIMIT):
            raise BootstrapConfigurationError("bootstrap_request_limit_invalid")
        if isinstance(self.queue_capacity, bool) or not isinstance(self.queue_capacity, int) or not 1 <= self.queue_capacity <= 32:
            raise BootstrapConfigurationError("bootstrap_queue_capacity_invalid")
        self.validate_paths()

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "BootstrapSettings":
        values = os.environ if environ is None else environ
        required = ("api_token", "source_root", "storage_root", "state_dir")
        if any(not values.get(_ENV[name], "").strip() for name in required):
            raise BootstrapConfigurationError("bootstrap_required_setting_missing")
        return cls(
            api_token=values[_ENV["api_token"]],
            source_root=Path(values[_ENV["source_root"]]),
            storage_root=Path(values[_ENV["storage_root"]]),
            state_dir=Path(values[_ENV["state_dir"]]),
            host=values.get(_ENV["host"], _DEFAULT_HOST),
            port=_integer(values.get(_ENV["port"], str(_DEFAULT_PORT)), 65535, "bootstrap_port_invalid"),
            max_request_body_bytes=_integer(values.get(_ENV["max_request_body_bytes"], str(_DEFAULT_BODY_LIMIT)), _MAX_BODY_LIMIT, "bootstrap_request_limit_invalid"),
            queue_capacity=_integer(values.get(_ENV["queue_capacity"], "2"), 32, "bootstrap_queue_capacity_invalid"),
        )

    @property
    def database_path(self) -> Path:
        return self.state_dir / "bootstrap-jobs.sqlite3"

    def validate_paths(self) -> None:
        source = self.source_root
        storage = self.storage_root
        state = self.state_dir
        if not source.is_dir():
            raise BootstrapConfigurationError("bootstrap_source_directory_missing")
        if storage.exists() and not storage.is_dir():
            raise BootstrapConfigurationError("bootstrap_storage_path_invalid")
        if not storage.parent.is_dir():
            raise BootstrapConfigurationError("bootstrap_storage_parent_missing")
        if not state.is_dir():
            raise BootstrapConfigurationError("bootstrap_state_directory_missing")
        try:
            resolve_disjoint_paths(source, storage, first_label="source", second_label="index storage")
            resolve_disjoint_paths(source, state, first_label="source", second_label="bootstrap state")
            resolve_disjoint_paths(storage, state, first_label="index storage", second_label="bootstrap state")
        except PathContainmentError as exc:
            raise BootstrapConfigurationError("bootstrap_paths_overlap") from exc
        for candidate in (self.database_path, Path(str(self.database_path) + "-wal"), Path(str(self.database_path) + "-shm")):
            if candidate.is_symlink():
                raise BootstrapConfigurationError("bootstrap_path_symlink")
        if self.database_path.exists() and not self.database_path.is_file():
            raise BootstrapConfigurationError("bootstrap_state_path_invalid")


@dataclass(frozen=True)
class BootstrapRuntime:
    settings: BootstrapSettings = field(repr=False)
    app: Starlette = field(repr=False)
    job_manager: BootstrapJobManager = field(repr=False)


def build_bootstrap_runtime(settings: BootstrapSettings | None = None, *, adapter_factory=None) -> BootstrapRuntime:
    """Compose the bootstrap API without opening SQLite, Chroma, or models."""
    selected = settings if settings is not None else BootstrapSettings.from_environment()
    if not isinstance(selected, BootstrapSettings):
        raise BootstrapConfigurationError("bootstrap_settings_invalid")
    selected.validate_paths()
    manager = BootstrapJobManager(
        selected.database_path,
        selected.source_root,
        selected.storage_root,
        capacity=selected.queue_capacity,
        adapter_factory=adapter_factory,
    )

    @asynccontextmanager
    async def lifespan(_app):
        await run_in_threadpool(manager.start)
        try:
            yield
        finally:
            # A false return deliberately retains the manager's state reservation.
            completed = await run_in_threadpool(manager.shutdown)
            app.state.shutdown_complete = bool(completed)
            if not completed:
                _LOGGER.error("bootstrap shutdown incomplete; instance reservation retained")

    app = Starlette(routes=build_bootstrap_routes(), lifespan=lifespan)
    app.state.bootstrap_settings = selected
    app.state.bootstrap_job_manager = manager
    app.state.bootstrap_source_root = selected.source_root
    app.state.bootstrap_storage_root = selected.storage_root
    app.state.max_request_body_bytes = selected.max_request_body_bytes
    app.state.max_response_body_bytes = min(selected.max_request_body_bytes, 1024 * 1024)
    app.add_middleware(ExperimentSecurityMiddleware, api_token=selected.api_token)
    return BootstrapRuntime(selected, app, manager)


def run_server(runtime: BootstrapRuntime) -> None:
    if not isinstance(runtime, BootstrapRuntime):
        raise BootstrapConfigurationError("bootstrap_runtime_invalid")
    try:
        import uvicorn
    except Exception as exc:
        raise BootstrapConfigurationError("asgi_server_unavailable") from exc
    # This composition owns one SQLite queue and one worker; multiple ASGI workers
    # would compete for the same exclusive state reservation.
    uvicorn.run(runtime.app, host=runtime.settings.host, port=runtime.settings.port,
                workers=1, access_log=False, log_level="info")


def main() -> int:
    try:
        run_server(build_bootstrap_runtime())
    except BootstrapConfigurationError as exc:
        print(f"Bootstrap configuration error: {exc.code}", file=__import__("sys").stderr)
        return 2
    except Exception:
        print("Bootstrap startup failed: bootstrap_startup_failed", file=__import__("sys").stderr)
        return 2
    return 0


__all__ = ["BootstrapSettings", "BootstrapConfigurationError", "BootstrapRuntime", "build_bootstrap_runtime", "run_server", "main"]
