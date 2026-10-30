"""Durable, bounded metadata and execution manager for local index builds."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import sqlite3
import threading
import uuid
from typing import Callable

from backend.config.schema import ResolvedRunConfig
from backend.engine.index_builder import IndexBuildResult, build_index
from backend.engine.manifest import build_document_manifest
from backend.experiments.run_plan import RunPlan, build_run_plan


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class BuildJob:
    job_id: str
    client_request_id: str
    request_hash: str
    manifest_hash: str
    index_fingerprint: str
    status: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    stage: str | None
    error_code: str | None
    build_id: str | None
    report_reference: str | None
    text_records: int | None
    image_records: int | None
    partial_persistence: bool | None


class BootstrapJobError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def canonical_request_identity(config: ResolvedRunConfig, manifest_hash: str, fingerprint: str, source: Path, storage: Path) -> str:
    """Domain-separated identity from validated plan inputs and trusted roots."""
    config.validate_for_workbench()
    snapshot = json.dumps(config.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    payload = {"contract": "fresh-index-build-v1", "policy": "fresh_only", "config": hashlib.sha256(snapshot.encode("utf-8")).hexdigest(), "manifest": manifest_hash, "index": fingerprint, "source_namespace": str(source.resolve(strict=False)).casefold(), "storage_namespace": str(storage.resolve(strict=False)).casefold()}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(b"rag-pdf-bootstrap-request-v1\0" + encoded.encode("utf-8")).hexdigest()


class BootstrapJobManager:
    """One-worker process-local queue with SQLite idempotency and restart fencing."""

    def __init__(self, database: str | Path, source: str | Path, storage: str | Path, *, capacity: int = 2, adapter_factory: Callable[[], tuple[object | None, object | None]] | None = None):
        self.path = Path(database)
        self.source = Path(source)
        self.storage = Path(storage)
        if isinstance(capacity, bool) or not isinstance(capacity, int) or not 1 <= capacity <= 32:
            raise BootstrapJobError("queue_capacity_invalid")
        self.capacity = capacity
        self.adapter_factory = adapter_factory or (lambda: (None, None))
        self._queue: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=capacity)
        self._completion: dict[str, threading.Event] = {}
        self._last_worker_error: str | None = None
        self._last_worker_stage: str | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._owner = self.path.parent / ".bootstrap-instance-owner"
        self._owner_token: str | None = None
        self._accepting = False
        self._initialized = False
        if not self.path.is_absolute() or not self.path.parent.is_dir() or self.path.parent.is_symlink():
            raise BootstrapJobError("job_store_path_invalid")

    @property
    def accepting(self) -> bool:
        """Whether this manager currently accepts new build submissions."""
        with self._lock:
            return self._accepting and self._owner_token is not None

    def _initialize_owned(self):
        if self._initialized:
            return
        try:
            with self._connect() as db:
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, 1):
                    raise BootstrapJobError("job_schema_unsupported")
                if version == 0:
                    db.execute("CREATE TABLE bootstrap_jobs (job_id TEXT PRIMARY KEY, client_request_id TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL, manifest_hash TEXT NOT NULL, index_fingerprint TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','interrupted')), created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, stage TEXT, error_code TEXT, build_id TEXT, report_reference TEXT, text_records INTEGER, image_records INTEGER, partial_persistence INTEGER)")
                    db.execute("PRAGMA user_version=1")
                db.execute("CREATE UNIQUE INDEX IF NOT EXISTS bootstrap_active_fingerprint ON bootstrap_jobs(index_fingerprint) WHERE status IN ('queued','running')")
                db.execute("UPDATE bootstrap_jobs SET status='interrupted', finished_at=?, error_code='server_restarted' WHERE status IN ('queued','running')", (_now(),))
            self._initialized = True
        except BootstrapJobError:
            raise
        except sqlite3.Error as exc:
            raise BootstrapJobError("job_store_unavailable") from exc

    def _connect(self):
        try:
            db = sqlite3.connect(self.path, timeout=5)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            return db
        except sqlite3.Error as exc:
            raise BootstrapJobError("job_store_unavailable") from exc

    def start(self):
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            try:
                self._owner.mkdir()
                token = uuid.uuid4().hex
                with (self._owner / "owner").open("x", encoding="ascii") as handle:
                    handle.write(token)
                    handle.flush()
                    os.fsync(handle.fileno())
            except FileExistsError as exc:
                raise BootstrapJobError("bootstrap_instance_owned") from exc
            except OSError as exc:
                raise BootstrapJobError("bootstrap_ownership_unavailable") from exc
            self._owner_token = token
            try:
                self._initialize_owned()
            except Exception:
                # Keep the reservation after an uncertain startup mutation.
                raise
            self._stop.clear()
            self._accepting = True
            self._worker = threading.Thread(target=self._run, name="bootstrap-index-builder", daemon=True)
            self._worker.start()

    def shutdown(self, timeout: float = 30) -> bool:
        with self._lock:
            self._accepting = False
            if self._owner_token is None:
                return True
            try:
                with self._connect() as db:
                    db.execute("UPDATE bootstrap_jobs SET status='interrupted',finished_at=?,error_code='server_shutdown' WHERE status='queued'", (_now(),))
            except (sqlite3.Error, BootstrapJobError):
                return False
            self._stop.set()
        worker = self._worker
        if worker is not None:
            worker.join(timeout)
            if worker.is_alive():
                return False
        owner = self._owner / "owner"
        try:
            if self._owner_token is None or owner.is_symlink() or owner.read_text(encoding="ascii") != self._owner_token:
                return False
            owner.unlink()
            self._owner.rmdir()
        except (OSError, UnicodeError):
            return False
        self._owner_token = None
        self._worker = None
        return True

    def submit(self, request_id: str, config: ResolvedRunConfig, manifest_hash: str, fingerprint: str) -> BuildJob:
        with self._lock:
            if not self._accepting or self._owner_token is None:
                raise BootstrapJobError("manager_not_accepting")
            request_hash = self._request_hash(request_id, config, manifest_hash, fingerprint)
            with self._connect() as db:
                prior = db.execute("SELECT * FROM bootstrap_jobs WHERE client_request_id=?", (request_id,)).fetchone()
                if prior:
                    if prior["request_hash"] != request_hash:
                        raise BootstrapJobError("request_id_conflict")
                    return self._record(prior)
                active = db.execute("SELECT 1 FROM bootstrap_jobs WHERE index_fingerprint=? AND status IN ('queued','running')", (fingerprint,)).fetchone()
                if active:
                    raise BootstrapJobError("fingerprint_already_active")
                if self._queue.full():
                    raise BootstrapJobError("queue_capacity_exhausted")
                job_id, created = str(uuid.uuid4()), _now()
                try:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("INSERT INTO bootstrap_jobs(job_id,client_request_id,request_hash,manifest_hash,index_fingerprint,status,created_at) VALUES(?,?,?,?,?,?,?)", (job_id, request_id, request_hash, manifest_hash, fingerprint, "queued", created))
                    db.commit()
                except sqlite3.IntegrityError as exc:
                    db.rollback()
                    raise BootstrapJobError("fingerprint_already_active") from exc
                except sqlite3.Error as exc:
                    db.rollback()
                    raise BootstrapJobError("job_admission_failed") from exc
                self._completion[job_id] = threading.Event()
                try:
                    self._queue.put_nowait((job_id, config.model_dump_json()))
                except queue.Full as exc:
                    # The durable row records that accepted work never entered
                    # the process queue; it cannot remain falsely queued.
                    with self._connect() as compensate:
                        compensate.execute("UPDATE bootstrap_jobs SET status='interrupted',finished_at=?,error_code='queue_admission_failed' WHERE job_id=? AND status='queued'", (_now(), job_id))
                    self._completion.pop(job_id, None)
                    raise BootstrapJobError("queue_capacity_exhausted") from exc
            return self.get(job_id)

    def find_idempotent(self, request_id: str, config: ResolvedRunConfig, manifest_hash: str, fingerprint: str) -> BuildJob | None:
        """Return an existing matching submission without admitting new work.

        This is used when an HTTP retry follows a successful build: the target
        index now exists, but the original idempotency key must still resolve
        to its durable job rather than enqueueing or rejecting a second build.
        """
        with self._lock:
            if not self._accepting or self._owner_token is None:
                raise BootstrapJobError("manager_not_accepting")
            request_hash = self._request_hash(request_id, config, manifest_hash, fingerprint)
            with self._connect() as db:
                prior = db.execute("SELECT * FROM bootstrap_jobs WHERE client_request_id=?", (request_id,)).fetchone()
            if prior is None:
                return None
            if prior["request_hash"] != request_hash:
                raise BootstrapJobError("request_id_conflict")
            return self._record(prior)

    def _request_hash(self, request_id: str, config: ResolvedRunConfig, manifest_hash: str, fingerprint: str) -> str:
        if (not isinstance(request_id, str) or not 16 <= len(request_id) <= 80 or
            any(not (char.isalnum() or char in "_-") for char in request_id) or
            not isinstance(config, ResolvedRunConfig) or not isinstance(manifest_hash, str) or
            len(manifest_hash) != 64 or not isinstance(fingerprint, str) or len(fingerprint) != 64):
            raise BootstrapJobError("job_input_invalid")
        try:
            config.validate_for_workbench()
            manifest = build_document_manifest(self.source)
            plan = build_run_plan(config, manifest.content_hash)
        except Exception as exc:
            raise BootstrapJobError("job_input_invalid") from exc
        if manifest.content_hash != manifest_hash or plan.index_fingerprint != fingerprint:
            raise BootstrapJobError("confirmation_changed")
        return canonical_request_identity(config, manifest_hash, fingerprint, self.source, self.storage)

    @staticmethod
    def _record(row) -> BuildJob:
        fields = dict(row)
        fields.pop("config_json", None)
        fields["partial_persistence"] = None if fields["partial_persistence"] is None else bool(fields["partial_persistence"])
        return BuildJob(**fields)

    def get(self, job_id: str) -> BuildJob:
        with self._connect() as db:
            row = db.execute("SELECT * FROM bootstrap_jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise BootstrapJobError("job_not_found")
        return self._record(row)

    def list(self, limit: int = 100, offset: int = 0) -> tuple[BuildJob, ...]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM bootstrap_jobs ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return tuple(self._record(row) for row in rows)

    def _claim_job(self, job_id: str):
        """Durably claim exactly one queued record before executing its payload."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM bootstrap_jobs WHERE job_id=? AND status='queued'", (job_id,)).fetchone()
            if row is None:
                db.rollback()
                return None
            cursor = db.execute(
                "UPDATE bootstrap_jobs SET status='running', started_at=?, stage='running' "
                "WHERE job_id=? AND status='queued'",
                (_now(), job_id),
            )
            if cursor.rowcount != 1:
                db.rollback()
                return None
            db.commit()
            return row

    def _persist_worker_failure(self, job_id: str, error_code: str, *, statuses: tuple[str, ...] = ("running",)) -> None:
        placeholders = ",".join("?" for _ in statuses)
        with self._connect() as db:
            db.execute(
                f"UPDATE bootstrap_jobs SET status='failed',finished_at=?,error_code=? "
                f"WHERE job_id=? AND status IN ({placeholders})",
                (_now(), error_code, job_id, *statuses),
            )

    def _persist_worker_result(self, job_id: str, result: IndexBuildResult, status: str) -> None:
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE bootstrap_jobs SET status=?,finished_at=?,stage=?,error_code=?,build_id=?,"
                "report_reference=?,text_records=?,image_records=?,partial_persistence=? "
                "WHERE job_id=? AND status='running'",
                (
                    status,
                    _now(),
                    "completed" if status == "succeeded" else result.failure_stage,
                    None if status == "succeeded" else "build_failed",
                    result.build_id,
                    result.report_path.name,
                    result.text_records_persisted,
                    result.image_records_persisted,
                    int(result.partial_persistence),
                    job_id,
                ),
            )
            if cursor.rowcount != 1:
                raise BootstrapJobError("job_result_state_conflict")

    def _run(self):
        while not self._stop.is_set():
            try:
                job_id, raw = self._queue.get(timeout=.1)
            except queue.Empty:
                continue
            try:
                self._last_worker_stage = "configuration"
                config = ResolvedRunConfig.model_validate_json(raw)
                self._last_worker_stage = "claim"
                row = self._claim_job(job_id)
                if row is None:
                    continue
                self._last_worker_stage = "manifest"
                manifest = build_document_manifest(self.source)
                self._last_worker_stage = "run_plan"
                plan = build_run_plan(config, manifest.content_hash)
                if manifest.content_hash != row["manifest_hash"] or plan.index_fingerprint != row["index_fingerprint"]:
                    raise BootstrapJobError("confirmation_changed")
                self._last_worker_stage = "adapter_factory"
                text_adapter, image_adapter = self.adapter_factory()
                self._last_worker_stage = "build"
                result = build_index(plan, self.source, self.storage, text_adapter=text_adapter, image_adapter=image_adapter, creation_policy="fresh_only")
                status = "succeeded" if result.status in {"completed", "completed_with_warnings"} and not result.partial_persistence else "failed"
                if status == "succeeded":
                    self._last_worker_stage = "report_validation"
                    from backend.engine.retrieval import _validate_build_report
                    report = _validate_build_report(self.storage, result.report_path, plan)
                    marker = self.storage / "indexes" / plan.index_fingerprint / ".fresh-build-incomplete"
                    if (report.build_id != result.build_id or
                        result.index_fingerprint != plan.index_fingerprint or
                        result.document_manifest_hash != row["manifest_hash"] or
                        report.index_fingerprint != plan.index_fingerprint or
                        report.document_manifest_hash != row["manifest_hash"] or
                        report.text_records_persisted != result.text_records_persisted or
                        report.image_records_persisted != result.image_records_persisted or
                        report.status not in {"completed", "completed_with_warnings"} or
                        marker.exists() or marker.is_symlink()):
                        status = "failed"
                self._last_worker_stage = "result_persistence"
                self._persist_worker_result(job_id, result, status)
            except BaseException as exc:
                self._last_worker_error = exc.code if isinstance(exc, BootstrapJobError) else type(exc).__name__
                try:
                    if self._last_worker_stage == "claim":
                        self._persist_worker_failure(job_id, "worker_claim_failed", statuses=("queued", "running"))
                    elif self._last_worker_stage == "result_persistence":
                        # The build may already have published a real index. Do not
                        # retry a possibly ambiguous terminal transaction or rebuild.
                        with self._lock:
                            self._accepting = False
                        self._stop.set()
                    else:
                        self._persist_worker_failure(
                            job_id,
                            exc.code if isinstance(exc, BootstrapJobError) else "build_failed",
                        )
                except BaseException:
                    # Preserve the durable queued/running row as unresolved and
                    # stop admission; shutdown/restart will apply the interruption policy.
                    with self._lock:
                        self._accepting = False
                    self._stop.set()
            finally:
                self._queue.task_done()
                event = self._completion.get(job_id)
                if event is not None:
                    event.set()
