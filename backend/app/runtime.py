"""Explicit local composition root for the experiment API.

Importing this module is side-effect free. Runtime directories and SQLite
files are touched only by :func:`build_runtime`, after all paths are checked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import os
from pathlib import Path
import re
from typing import Mapping

from backend.config.paths import PathContainmentError, resolve_disjoint_paths
from backend.engine.context import ContextFormatter
from backend.engine.generation import LocalGenerationAdapter
from backend.experiments.artifact_store import ExperimentArtifactStore
from backend.experiments.catalog import ExperimentCatalog
from backend.services.experiment_service import ExperimentService
from backend.services.job_manager import ExperimentJobManager
from backend.services.model_registry import ModelAdapterRegistry
from .main import create_app


_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8000
_DEFAULT_BODY_LIMIT = 24 * 1024 * 1024
_MAX_BODY_LIMIT = 64 * 1024 * 1024
_ENV = {
    "api_token": "RAG_WORKBENCH_API_TOKEN",
    "data_dir": "RAG_WORKBENCH_DATA_DIR",
    "artifact_store_dir": "RAG_WORKBENCH_ARTIFACT_STORE_DIR",
    "catalog_db_path": "RAG_WORKBENCH_CATALOG_DB_PATH",
    "jobs_db_path": "RAG_WORKBENCH_JOBS_DB_PATH",
    "index_storage_root": "RAG_WORKBENCH_INDEX_STORAGE_ROOT",
    "build_report_path": "RAG_WORKBENCH_BUILD_REPORT_PATH",
    "host": "RAG_WORKBENCH_API_HOST",
    "port": "RAG_WORKBENCH_API_PORT",
    "max_request_body_bytes": "RAG_WORKBENCH_MAX_REQUEST_BODY_BYTES",
    "worker_count": "RAG_WORKBENCH_WORKER_COUNT",
    "max_pending_jobs": "RAG_WORKBENCH_MAX_PENDING_JOBS",
}


class RuntimeConfigurationError(ValueError):
    """A stable runtime configuration error that never embeds setting values."""

    def __init__(self, error_code: str) -> None:
        self.error_code = error_code if re.fullmatch(r"[a-z][a-z0-9_]{1,63}", error_code or "") else "runtime_configuration_invalid"
        super().__init__(self.error_code)


def _absolute_path(value: object, code: str = "runtime_path_invalid") -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise RuntimeConfigurationError(code)
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise RuntimeConfigurationError(code)
    return candidate


def _positive_int(value: object, *, maximum: int, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= maximum:
        raise RuntimeConfigurationError(code)
    return value


def _loopback_host(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 255:
        raise RuntimeConfigurationError("api_host_invalid")
    if value.lower() == "localhost":
        return "localhost"
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise RuntimeConfigurationError("api_host_must_be_loopback") from exc
    if not address.is_loopback:
        raise RuntimeConfigurationError("api_host_must_be_loopback")
    return str(address)


@dataclass(frozen=True)
class LocalRuntimeSettings:
    """Validated paths and server limits for one local API process."""

    api_token: str = field(repr=False)
    data_dir: Path
    artifact_store_dir: Path
    catalog_db_path: Path
    jobs_db_path: Path
    index_storage_root: Path
    build_report_path: Path
    host: str = _DEFAULT_HOST
    port: int = _DEFAULT_PORT
    max_request_body_bytes: int = _DEFAULT_BODY_LIMIT
    worker_count: int = 1
    max_pending_jobs: int = 10

    def __post_init__(self) -> None:
        if not isinstance(self.api_token, str) or not self.api_token or len(self.api_token) > 4096 or any(char.isspace() for char in self.api_token):
            raise RuntimeConfigurationError("api_token_invalid")
        for name in ("data_dir", "artifact_store_dir", "catalog_db_path", "jobs_db_path", "index_storage_root", "build_report_path"):
            object.__setattr__(self, name, _absolute_path(getattr(self, name)))
        object.__setattr__(self, "host", _loopback_host(self.host))
        _positive_int(self.port, maximum=65535, code="api_port_invalid")
        _positive_int(self.max_request_body_bytes, maximum=_MAX_BODY_LIMIT, code="request_limit_invalid")
        _positive_int(self.worker_count, maximum=32, code="worker_count_invalid")
        _positive_int(self.max_pending_jobs, maximum=1000, code="pending_limit_invalid")

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "LocalRuntimeSettings":
        """Load settings from the documented ``RAG_WORKBENCH_`` variables."""

        values = os.environ if environ is None else environ
        required = ("api_token", "data_dir", "artifact_store_dir", "catalog_db_path", "jobs_db_path", "index_storage_root", "build_report_path")
        missing = [key for key in required if not values.get(_ENV[key], "").strip()]
        if missing:
            raise RuntimeConfigurationError("required_setting_missing")

        def integer(key: str, default: int, maximum: int, code: str) -> int:
            raw = values.get(_ENV[key], str(default))
            if not isinstance(raw, str) or not re.fullmatch(r"[0-9]{1,10}", raw):
                raise RuntimeConfigurationError(code)
            return _positive_int(int(raw), maximum=maximum, code=code)

        return cls(
            api_token=values[_ENV["api_token"]],
            data_dir=Path(values[_ENV["data_dir"]]),
            artifact_store_dir=Path(values[_ENV["artifact_store_dir"]]),
            catalog_db_path=Path(values[_ENV["catalog_db_path"]]),
            jobs_db_path=Path(values[_ENV["jobs_db_path"]]),
            index_storage_root=Path(values[_ENV["index_storage_root"]]),
            build_report_path=Path(values[_ENV["build_report_path"]]),
            host=values.get(_ENV["host"], _DEFAULT_HOST),
            port=integer("port", _DEFAULT_PORT, 65535, "api_port_invalid"),
            max_request_body_bytes=integer("max_request_body_bytes", _DEFAULT_BODY_LIMIT, _MAX_BODY_LIMIT, "request_limit_invalid"),
            worker_count=integer("worker_count", 1, 32, "worker_count_invalid"),
            max_pending_jobs=integer("max_pending_jobs", 10, 1000, "pending_limit_invalid"),
        )


@dataclass(frozen=True)
class LocalRuntime:
    """The explicitly composed, not-yet-started local application."""

    settings: LocalRuntimeSettings = field(repr=False)
    app: object = field(repr=False)
    artifact_store: ExperimentArtifactStore = field(repr=False)
    catalog: ExperimentCatalog = field(repr=False)
    model_registry: ModelAdapterRegistry = field(repr=False)
    generation_adapter: LocalGenerationAdapter = field(repr=False)
    context_formatter: ContextFormatter = field(repr=False)
    service: ExperimentService = field(repr=False)
    job_manager: ExperimentJobManager = field(repr=False)


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise RuntimeConfigurationError("runtime_path_symlink")


def _resolve(path: Path) -> Path:
    _reject_symlink_components(path)
    try:
        return path.resolve(strict=False)
    except OSError as exc:
        raise RuntimeConfigurationError("runtime_path_invalid") from exc


def _validate_paths(settings: LocalRuntimeSettings) -> tuple[Path, ...]:
    paths = tuple(
        _resolve(getattr(settings, name))
        for name in ("data_dir", "artifact_store_dir", "catalog_db_path", "jobs_db_path", "index_storage_root", "build_report_path")
    )
    data, artifacts, catalog_db, jobs_db, index_root, report = paths

    if not data.is_dir() or not index_root.is_dir():
        raise RuntimeConfigurationError("runtime_directory_missing")
    if not report.is_file():
        raise RuntimeConfigurationError("build_report_missing")
    if report.name != "build-report.json":
        raise RuntimeConfigurationError("build_report_path_invalid")
    try:
        relative_report = report.relative_to(index_root)
    except ValueError as exc:
        raise RuntimeConfigurationError("build_report_path_invalid") from exc
    if len(relative_report.parts) != 3 or relative_report.parts[0] != "build-artifacts" or not relative_report.parts[1]:
        raise RuntimeConfigurationError("build_report_path_invalid")

    artifact_parent = artifacts.parent
    if not artifact_parent.is_dir():
        raise RuntimeConfigurationError("artifact_parent_missing")
    if artifacts.exists() and not artifacts.is_dir():
        raise RuntimeConfigurationError("artifact_store_path_invalid")
    for db in (catalog_db, jobs_db):
        if not db.parent.is_dir() or (db.exists() and not db.is_file()):
            raise RuntimeConfigurationError("database_path_invalid")

    # Every independent root/file is disjoint. The build report is intentionally
    # contained by the index storage root and is validated separately above.
    independent = (("data", data), ("artifact store", artifacts), ("catalog database", catalog_db), ("jobs database", jobs_db), ("index storage", index_root))
    try:
        for index, (label_a, path_a) in enumerate(independent):
            for label_b, path_b in independent[index + 1:]:
                resolve_disjoint_paths(path_a, path_b, first_label=label_a, second_label=label_b)
    except PathContainmentError as exc:
        raise RuntimeConfigurationError("runtime_paths_overlap") from exc
    return paths


def build_runtime(
    settings: LocalRuntimeSettings | None = None,
    *,
    context_formatter: ContextFormatter | None = None,
    generation_adapter: LocalGenerationAdapter | None = None,
    model_registry: ModelAdapterRegistry | None = None,
) -> LocalRuntime:
    """Compose the existing API components without starting workers or models.

    The artifact directory may be created if its parent already exists. Index,
    source-data, build-report, and SQLite parent directories must already exist.
    """

    selected = settings if settings is not None else LocalRuntimeSettings.from_environment()
    if not isinstance(selected, LocalRuntimeSettings):
        raise RuntimeConfigurationError("runtime_settings_invalid")
    if model_registry is not None and not isinstance(model_registry, ModelAdapterRegistry):
        raise RuntimeConfigurationError("model_registry_invalid")
    if context_formatter is not None and not hasattr(context_formatter, "format"):
        raise RuntimeConfigurationError("context_formatter_invalid")
    if generation_adapter is not None and not hasattr(generation_adapter, "generate"):
        raise RuntimeConfigurationError("generation_adapter_invalid")
    _validate_paths(selected)

    try:
        selected.artifact_store_dir.mkdir(exist_ok=True)
        artifact_store = ExperimentArtifactStore(selected.artifact_store_dir)
        catalog = ExperimentCatalog(selected.catalog_db_path, artifact_store)
        registry = model_registry if model_registry is not None else ModelAdapterRegistry()
        formatter = context_formatter if context_formatter is not None else ContextFormatter()
        generator = generation_adapter if generation_adapter is not None else LocalGenerationAdapter()
        service = ExperimentService(
            catalog,
            selected.index_storage_root,
            selected.build_report_path,
            formatter,
            generator,
            model_registry=registry,
        )
        jobs = ExperimentJobManager(
            service,
            selected.jobs_db_path,
            worker_count=selected.worker_count,
            max_pending_jobs=selected.max_pending_jobs,
        )
        app = create_app(service, api_token=selected.api_token, max_request_body_bytes=selected.max_request_body_bytes, job_manager=jobs)
    except RuntimeConfigurationError:
        raise
    except Exception as exc:
        # Component errors often contain local paths. Keep the CLI boundary safe.
        raise RuntimeConfigurationError("runtime_composition_failed") from exc
    return LocalRuntime(selected, app, artifact_store, catalog, registry, generator, formatter, service, jobs)


def run_server(runtime: LocalRuntime) -> None:
    """Run one already-composed app using the pinned local Uvicorn server."""

    if not isinstance(runtime, LocalRuntime):
        raise RuntimeConfigurationError("runtime_invalid")
    try:
        import uvicorn
    except Exception as exc:
        raise RuntimeConfigurationError("asgi_server_unavailable") from exc
    uvicorn.run(runtime.app, host=runtime.settings.host, port=runtime.settings.port, access_log=False, log_level="info")


def main() -> int:
    """CLI entry point; configuration diagnostics contain codes only."""

    try:
        runtime = build_runtime()
        run_server(runtime)
    except RuntimeConfigurationError as exc:
        print(f"Local runtime configuration error: {exc.error_code}", file=__import__("sys").stderr)
        return 2
    return 0


__all__ = ["LocalRuntimeSettings", "LocalRuntime", "RuntimeConfigurationError", "build_runtime", "run_server", "main"]
