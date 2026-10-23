"""Bounded, process-local background execution for experiment requests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from pathlib import Path
import queue
import re
import sqlite3
import threading
import time
import uuid
from typing import Any

from backend.config.paths import PathContainmentError, resolve_disjoint_paths
from backend.experiments.ablation_plans import AblationPlan
from backend.experiments.benchmark import BenchmarkSuite
from backend.experiments.run_plan import RunPlan

from .experiment_service import (
    ExperimentService,
    ExperimentServiceError,
    ServiceExecutionError,
    ServiceInputError,
    ServicePersistenceError,
    ServiceResultIntegrityError,
)


JOB_SCHEMA_VERSION = 1
_JOB_TYPES = {"evaluation", "ablation"}
_STATUSES = {"queued", "running", "succeeded", "partial", "failed", "cancelled", "interrupted"}
_RESULT_STATUSES = {"succeeded", "partial", "failed"}
_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_MAX_LIMIT = 1000


class ExperimentJobError(ValueError):
    """Base error with a stable public code."""

    def __init__(self, error_code: str) -> None:
        if not isinstance(error_code, str) or not _CODE_RE.fullmatch(error_code):
            error_code = "job_manager_error"
        self.error_code = error_code
        super().__init__(error_code)


class JobNotFoundError(ExperimentJobError):
    """The requested job does not exist."""


class JobCapacityError(ExperimentJobError):
    """The bounded pending queue is full."""


class JobStateError(ExperimentJobError):
    """The manager or job is not in a state permitting the operation."""


def _timestamp(value: object, label: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise ExperimentJobError("job_record_invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ExperimentJobError("job_record_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ExperimentJobError("job_record_invalid")
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _hash(value: object, *, optional: bool = True) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ExperimentJobError("job_record_invalid")
    return value


@dataclass(frozen=True)
class ExperimentJobRecord:
    job_id: str
    job_type: str
    status: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    benchmark_id: str
    benchmark_version: str
    run_fingerprint: str | None = None
    shared_index_fingerprint: str | None = None
    document_manifest_hash: str | None = None
    artifact_id: str | None = None
    error_code: str | None = None
    result_status: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.job_id, str) or not _ID_RE.fullmatch(self.job_id):
            raise ExperimentJobError("job_record_invalid")
        if self.job_type not in _JOB_TYPES or self.status not in _STATUSES:
            raise ExperimentJobError("job_record_invalid")
        _timestamp(self.created_at, "created_at")
        _timestamp(self.started_at, "started_at", optional=True)
        _timestamp(self.finished_at, "finished_at", optional=True)
        if not isinstance(self.benchmark_id, str) or not self.benchmark_id or len(self.benchmark_id) > 160:
            raise ExperimentJobError("job_record_invalid")
        if not isinstance(self.benchmark_version, str) or not self.benchmark_version or len(self.benchmark_version) > 64:
            raise ExperimentJobError("job_record_invalid")
        _hash(self.run_fingerprint)
        _hash(self.shared_index_fingerprint)
        _hash(self.document_manifest_hash)
        _hash(self.artifact_id)
        if self.error_code is not None and not _CODE_RE.fullmatch(self.error_code):
            raise ExperimentJobError("job_record_invalid")
        if self.result_status is not None and self.result_status not in _RESULT_STATUSES:
            raise ExperimentJobError("job_record_invalid")
        if self.status == "queued" and (self.started_at is not None or self.finished_at is not None):
            raise ExperimentJobError("job_record_invalid")
        if self.status == "running" and (self.started_at is None or self.finished_at is not None):
            raise ExperimentJobError("job_record_invalid")
        if self.status in _RESULT_STATUSES | {"cancelled", "interrupted"} and self.finished_at is None:
            raise ExperimentJobError("job_record_invalid")


@dataclass(frozen=True)
class _WorkItem:
    job_id: str
    job_type: str
    payload: RunPlan | AblationPlan
    benchmark_suite: BenchmarkSuite


class ExperimentJobManager:
    """A bounded, single-process worker pool backed by a metadata-only SQLite DB."""

    def __init__(
        self,
        service: ExperimentService,
        database_path: str | Path,
        *,
        worker_count: int = 1,
        max_pending_jobs: int = 10,
        shutdown_timeout_seconds: float = 5.0,
    ) -> None:
        if not isinstance(service, ExperimentService):
            raise ExperimentJobError("service_invalid")
        if not isinstance(database_path, (str, Path)) or not str(database_path).strip():
            raise ExperimentJobError("database_path_invalid")
        if isinstance(worker_count, bool) or not isinstance(worker_count, int) or not 0 < worker_count <= 32:
            raise ExperimentJobError("worker_count_invalid")
        if isinstance(max_pending_jobs, bool) or not isinstance(max_pending_jobs, int) or not 0 < max_pending_jobs <= 1000:
            raise ExperimentJobError("pending_limit_invalid")
        if isinstance(shutdown_timeout_seconds, bool) or not isinstance(shutdown_timeout_seconds, (int, float)) or not math.isfinite(float(shutdown_timeout_seconds)) or not 0 < float(shutdown_timeout_seconds) <= 60:
            raise ExperimentJobError("shutdown_timeout_invalid")
        raw = Path(database_path).expanduser()
        if raw.exists() and raw.is_symlink():
            raise ExperimentJobError("database_path_invalid")
        if not raw.parent.exists() or raw.parent.is_symlink() or not raw.parent.is_dir():
            raise ExperimentJobError("database_parent_invalid")
        try:
            catalog = service._catalog
            store_root = catalog._store._root
            catalog_path = catalog._path
            resolved, _ = resolve_disjoint_paths(raw, store_root, first_label="job database", second_label="artifact store")
            resolve_disjoint_paths(resolved, catalog_path, first_label="job database", second_label="catalog database")
        except (AttributeError, PathContainmentError, OSError) as exc:
            raise ExperimentJobError("database_path_invalid") from exc
        self._service = service
        self._path = resolved
        self._worker_count = worker_count
        self._max_pending_jobs = max_pending_jobs
        self._shutdown_timeout = float(shutdown_timeout_seconds)
        self._queue: queue.Queue[_WorkItem] = queue.Queue(maxsize=max_pending_jobs)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._workers: list[threading.Thread] = []
        self._started = False
        self._shutdown = False
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if version not in (0, JOB_SCHEMA_VERSION):
                    raise ExperimentJobError("job_schema_unsupported")
                if version == JOB_SCHEMA_VERSION and "experiment_jobs" not in tables:
                    raise ExperimentJobError("job_schema_invalid")
                if version == 0 and tables:
                    raise ExperimentJobError("job_schema_invalid")
                if version == 0:
                    connection.execute("""CREATE TABLE experiment_jobs (
                        job_id TEXT PRIMARY KEY, job_type TEXT NOT NULL, status TEXT NOT NULL,
                        created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
                        benchmark_id TEXT NOT NULL, benchmark_version TEXT NOT NULL,
                        run_fingerprint TEXT, shared_index_fingerprint TEXT,
                        document_manifest_hash TEXT, artifact_id TEXT, error_code TEXT,
                        result_status TEXT
                    )""")
                    connection.execute("CREATE INDEX experiment_jobs_created_idx ON experiment_jobs(created_at, job_id)")
                    connection.execute(f"PRAGMA user_version={JOB_SCHEMA_VERSION}")
        except ExperimentJobError:
            raise
        except sqlite3.Error as exc:
            raise ExperimentJobError("job_schema_invalid") from exc

    def _row(self, row: sqlite3.Row | None) -> ExperimentJobRecord:
        if row is None:
            raise JobNotFoundError("job_not_found")
        return ExperimentJobRecord(**dict(row))

    def _fetch(self, job_id: str) -> ExperimentJobRecord:
        with self._connect() as connection:
            return self._row(connection.execute("SELECT * FROM experiment_jobs WHERE job_id=?", (job_id,)).fetchone())

    def start(self) -> None:
        with self._lock:
            if self._started:
                if self._shutdown:
                    raise JobStateError("manager_shutdown")
                return
            with self._connect() as connection:
                connection.execute("UPDATE experiment_jobs SET status='interrupted', finished_at=?, error_code=? WHERE status IN ('queued','running')", (_now(), "process_restarted"))
            self._stop.clear()
            self._shutdown = False
            self._started = True
            self._workers = []
            for index in range(self._worker_count):
                worker = threading.Thread(target=self._worker, name=f"experiment-job-{index + 1}", daemon=True)
                self._workers.append(worker)
                worker.start()

    def shutdown(self) -> None:
        with self._lock:
            if not self._started or self._shutdown:
                return
            self._shutdown = True
            self._stop.set()
            with self._connect() as connection:
                connection.execute("UPDATE experiment_jobs SET status='interrupted', finished_at=?, error_code=? WHERE status IN ('queued','running')", (_now(), "shutdown_interrupted"))
            workers = tuple(self._workers)
        deadline = time.monotonic() + self._shutdown_timeout
        for worker in workers:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            worker.join(remaining)
        with self._lock:
            self._started = False

    def _claim(self, job_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("UPDATE experiment_jobs SET status='running', started_at=? WHERE job_id=? AND status='queued'", (_now(), job_id))
            return cursor.rowcount == 1

    def _finish(self, job_id: str, *, status: str, artifact_id: str | None, result_status: str | None, error_code: str | None) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE experiment_jobs SET status=?, finished_at=?, artifact_id=?, result_status=?, error_code=? WHERE job_id=? AND status='running'", (status, _now(), artifact_id, result_status, error_code, job_id))

    @staticmethod
    def _failure_code(exc: BaseException) -> str:
        if isinstance(exc, ServiceInputError):
            return "job_input_invalid"
        if isinstance(exc, ServiceResultIntegrityError):
            return "job_result_integrity_failed"
        if isinstance(exc, ServicePersistenceError):
            return "job_persistence_failed"
        if isinstance(exc, ServiceExecutionError):
            return "job_execution_failed"
        if isinstance(exc, ExperimentServiceError):
            return "job_service_failed"
        return "job_execution_failed"

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                if self._claim(item.job_id):
                    try:
                        if item.job_type == "evaluation":
                            persisted = self._service.run_evaluation(item.payload, item.benchmark_suite)
                        else:
                            persisted = self._service.run_ablation(item.payload, item.benchmark_suite)
                        record = persisted.record
                        self._finish(item.job_id, status=record.status, artifact_id=record.artifact_id, result_status=record.status, error_code=None)
                    except BaseException as exc:
                        self._finish(item.job_id, status="failed", artifact_id=None, result_status=None, error_code=self._failure_code(exc))
            finally:
                self._queue.task_done()

    def _validate_manager(self) -> None:
        with self._lock:
            if not self._started or self._shutdown:
                raise JobStateError("manager_not_started")

    def submit_evaluation(self, run_plan: RunPlan, benchmark_suite: BenchmarkSuite) -> ExperimentJobRecord:
        try:
            self._service.validate_evaluation_request(run_plan, benchmark_suite)
        except ExperimentServiceError as exc:
            raise ExperimentJobError(exc.error_code) from exc
        except Exception as exc:
            raise ExperimentJobError("job_input_invalid") from exc
        return self._submit("evaluation", run_plan, benchmark_suite, run_plan.run_fingerprint, None, None)

    def submit_ablation(self, ablation_plan: AblationPlan, benchmark_suite: BenchmarkSuite) -> ExperimentJobRecord:
        try:
            self._service.validate_ablation_request(ablation_plan, benchmark_suite)
        except ExperimentServiceError as exc:
            raise ExperimentJobError(exc.error_code) from exc
        except Exception as exc:
            raise ExperimentJobError("job_input_invalid") from exc
        return self._submit("ablation", ablation_plan, benchmark_suite, None, ablation_plan.shared_index_fingerprint, ablation_plan.document_manifest_hash)

    def _submit(self, job_type: str, payload: RunPlan | AblationPlan, suite: BenchmarkSuite, run_fingerprint: str | None, index_fingerprint: str | None, manifest: str | None) -> ExperimentJobRecord:
        self._validate_manager()
        with self._lock:
            if self._queue.full():
                raise JobCapacityError("job_capacity_exhausted")
            job_id = str(uuid.uuid4())
            created = _now()
            with self._connect() as connection:
                connection.execute("INSERT INTO experiment_jobs (job_id,job_type,status,created_at,started_at,finished_at,benchmark_id,benchmark_version,run_fingerprint,shared_index_fingerprint,document_manifest_hash,artifact_id,error_code,result_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (job_id, job_type, "queued", created, None, None, suite.benchmark_id, suite.benchmark_version, run_fingerprint, index_fingerprint, manifest, None, None, None))
            item = _WorkItem(job_id, job_type, payload, suite)
            try:
                self._queue.put_nowait(item)
            except queue.Full as exc:
                with self._connect() as connection:
                    connection.execute("DELETE FROM experiment_jobs WHERE job_id=? AND status='queued'", (job_id,))
                raise JobCapacityError("job_capacity_exhausted") from exc
        return self._fetch(job_id)

    def get_job(self, job_id: str) -> ExperimentJobRecord:
        if not isinstance(job_id, str) or not _ID_RE.fullmatch(job_id):
            raise JobNotFoundError("job_not_found")
        return self._fetch(job_id)

    def list_jobs(self, *, job_type: str | None = None, status: str | None = None, limit: int = 100, offset: int = 0) -> tuple[ExperimentJobRecord, ...]:
        if job_type is not None and job_type not in _JOB_TYPES:
            raise ExperimentJobError("job_type_invalid")
        if status is not None and status not in _STATUSES:
            raise ExperimentJobError("job_status_invalid")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 < limit <= _MAX_LIMIT:
            raise ExperimentJobError("limit_invalid")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ExperimentJobError("offset_invalid")
        clauses: list[str] = []
        args: list[Any] = []
        if job_type is not None:
            clauses.append("job_type=?"); args.append(job_type)
        if status is not None:
            clauses.append("status=?"); args.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(f"SELECT * FROM experiment_jobs{where} ORDER BY created_at, job_id LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
        return tuple(self._row(row) for row in rows)

    def cancel_job(self, job_id: str) -> ExperimentJobRecord:
        if not isinstance(job_id, str) or not _ID_RE.fullmatch(job_id):
            raise JobNotFoundError("job_not_found")
        with self._lock:
            with self._connect() as connection:
                row = connection.execute("SELECT * FROM experiment_jobs WHERE job_id=?", (job_id,)).fetchone()
                current = self._row(row)
                if current.status == "cancelled":
                    return current
                if current.status != "queued":
                    raise JobStateError("job_not_cancellable")
                connection.execute("UPDATE experiment_jobs SET status='cancelled', finished_at=?, error_code=? WHERE job_id=? AND status='queued'", (_now(), "cancelled", job_id))
        return self._fetch(job_id)


__all__ = ["ExperimentJobManager", "ExperimentJobRecord", "ExperimentJobError", "JobNotFoundError", "JobCapacityError", "JobStateError"]
