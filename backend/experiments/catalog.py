"""SQLite metadata catalog for immutable experiment artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3
from collections.abc import Iterable

from backend.config.paths import PathContainmentError, resolve_disjoint_paths

from .ablation_plans import AblationPlan
from .ablation_runner import AblationRunResult
from .artifact_store import ArtifactNotFoundError, ExperimentArtifactStore, StoredArtifact
from .evaluation_runner import EvaluationRunResult
from .evaluation import MetricAggregate


CATALOG_SCHEMA_VERSION = 1
_ARTIFACT_TYPES = {"evaluation_result", "ablation_result"}
_STATUSES = {"succeeded", "partial", "failed"}
_AGGREGATE_STATUSES = {"computed", "partial", "no_eligible_values"}
_COMPARISON_STATES = {"comparable", "incomplete", "not_comparable"}
_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_FIELD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$")
_MAX_LIMIT = 1000


class ExperimentCatalogError(ValueError):
    """Base error for invalid catalog requests or database state."""


class CatalogNotFoundError(ExperimentCatalogError):
    """Raised when a requested catalog record does not exist."""


class CatalogIntegrityError(ExperimentCatalogError):
    """Raised when catalog metadata disagrees with immutable artifact content."""


def _hash(value: object, label: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ExperimentCatalogError(f"{label} is invalid")
    return value


def _field(value: object, label: str, *, empty: bool = False, maximum: int = 160) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value) or any(ord(c) < 32 for c in value):
        raise ExperimentCatalogError(f"{label} is invalid")
    return value


def _count(value: object, label: str, *, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ExperimentCatalogError(f"{label} is invalid")
    return value


def _number(value: object, label: str, *, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExperimentCatalogError(f"{label} is invalid")
    import math
    if not math.isfinite(float(value)):
        raise ExperimentCatalogError(f"{label} is invalid")
    return float(value)


@dataclass(frozen=True)
class CatalogArtifactRecord:
    artifact_id: str
    artifact_type: str
    relative_path: str
    byte_length: int
    serialization_schema_version: str
    benchmark_id: str
    benchmark_version: str
    status: str
    run_fingerprint: str | None
    evaluation_fingerprint: str | None
    document_manifest_hash: str | None
    shared_index_fingerprint: str | None
    case_count: int | None
    succeeded_case_count: int | None
    failed_case_count: int | None

    def __post_init__(self) -> None:
        if self.artifact_type not in _ARTIFACT_TYPES:
            raise ExperimentCatalogError("artifact type is invalid")
        _hash(self.artifact_id, "artifact_id")
        expected = f"{self.artifact_type}/{self.artifact_id[:2]}/{self.artifact_id}.json"
        if self.relative_path != expected or "\\" in self.relative_path:
            raise ExperimentCatalogError("artifact relative path is invalid")
        if isinstance(self.byte_length, bool) or not isinstance(self.byte_length, int) or self.byte_length <= 0:
            raise ExperimentCatalogError("artifact byte length is invalid")
        _field(self.serialization_schema_version, "serialization schema version")
        _field(self.benchmark_id, "benchmark_id", maximum=120)
        _field(self.benchmark_version, "benchmark_version", maximum=80)
        if self.status not in _STATUSES:
            raise ExperimentCatalogError("artifact status is invalid")
        _hash(self.run_fingerprint, "run_fingerprint", optional=True)
        _hash(self.evaluation_fingerprint, "evaluation_fingerprint", optional=True)
        _hash(self.document_manifest_hash, "document_manifest_hash", optional=True)
        _hash(self.shared_index_fingerprint, "shared_index_fingerprint", optional=True)
        _count(self.case_count, "case_count", optional=True)
        _count(self.succeeded_case_count, "succeeded_case_count", optional=True)
        _count(self.failed_case_count, "failed_case_count", optional=True)


@dataclass(frozen=True)
class CatalogMetricRecord:
    artifact_id: str
    variant_id: str
    metric_id: str
    metric_name: str
    status: str
    value: float | None
    unit: str
    optimization_direction: str
    reference_kind: str
    implementation_version: str
    eligible_count: int
    skipped_count: int
    failed_count: int
    unavailable_count: int
    model_identifier: str | None = None
    methodology_note: str | None = None

    def __post_init__(self) -> None:
        _hash(self.artifact_id, "artifact_id")
        if self.variant_id != "" and not _FIELD_ID_RE.fullmatch(self.variant_id):
            raise ExperimentCatalogError("variant_id is invalid")
        for value, label in ((self.metric_id, "metric_id"), (self.implementation_version, "implementation_version")):
            if not isinstance(value, str) or not re.fullmatch(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$", value):
                raise ExperimentCatalogError(f"{label} is invalid")
        _field(self.metric_name, "metric_name")
        if self.status not in _AGGREGATE_STATUSES:
            raise ExperimentCatalogError("aggregate status is invalid")
        try:
            MetricAggregate(
                metric_id=self.metric_id,
                metric_name=self.metric_name,
                status=self.status,
                value=self.value,
                unit=self.unit,
                optimization_direction=self.optimization_direction,
                reference_kind=self.reference_kind,
                implementation_version=self.implementation_version,
                eligible_count=self.eligible_count,
                skipped_count=self.skipped_count,
                failed_count=self.failed_count,
                unavailable_count=self.unavailable_count,
                model_identifier=self.model_identifier,
                methodology_note=self.methodology_note,
            )
        except Exception as exc:
            raise ExperimentCatalogError("aggregate metadata is invalid") from exc
        if self.model_identifier is not None:
            _field(self.model_identifier, "model_identifier")
        if self.methodology_note is not None:
            _field(self.methodology_note, "methodology_note", empty=True, maximum=2000)


@dataclass(frozen=True)
class CatalogComparisonRecord:
    artifact_id: str
    metric_id: str
    state: str
    reason_code: str | None

    def __post_init__(self) -> None:
        _hash(self.artifact_id, "artifact_id")
        if not re.fullmatch(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$", self.metric_id):
            raise ExperimentCatalogError("metric_id is invalid")
        if self.state not in _COMPARISON_STATES:
            raise ExperimentCatalogError("comparison state is invalid")
        if self.reason_code is not None and not re.fullmatch(r"^[a-z][a-z0-9_.-]{0,63}$", self.reason_code):
            raise ExperimentCatalogError("comparison reason_code is invalid")


_ARTIFACT_COLUMNS = (
    "artifact_id", "artifact_type", "relative_path", "byte_length", "serialization_schema_version",
    "benchmark_id", "benchmark_version", "status", "run_fingerprint", "evaluation_fingerprint",
    "document_manifest_hash", "shared_index_fingerprint", "case_count", "succeeded_case_count", "failed_case_count",
)
_VARIANT_COLUMNS = ("artifact_id", "ordinal", "variant_id", "run_fingerprint", "status", "error_code", "evaluation_fingerprint", "case_count", "succeeded_case_count", "failed_case_count")
_METRIC_COLUMNS = ("artifact_id", "variant_id", "metric_id", "metric_name", "status", "value", "unit", "optimization_direction", "reference_kind", "implementation_version", "eligible_count", "skipped_count", "failed_count", "unavailable_count", "model_identifier", "methodology_note")


class ExperimentCatalog:
    """Synchronous SQLite metadata index backed by an artifact store."""

    def __init__(self, database_path: str | Path, artifact_store: ExperimentArtifactStore) -> None:
        if not isinstance(artifact_store, ExperimentArtifactStore):
            raise ExperimentCatalogError("artifact_store is invalid")
        if not isinstance(database_path, (str, Path)) or not str(database_path).strip():
            raise ExperimentCatalogError("database path is required")
        raw = Path(database_path).expanduser()
        if raw.exists() and raw.is_symlink():
            raise ExperimentCatalogError("database symlinks are not supported")
        parent = raw.parent
        if not parent.exists() or not parent.is_dir() or parent.is_symlink():
            raise ExperimentCatalogError("database parent must be an existing regular directory")
        current = parent
        while current != current.parent:
            if current.is_symlink():
                raise ExperimentCatalogError("database path components must not be symlinks")
            current = current.parent
        store_root = getattr(artifact_store, "_root", None)
        if not isinstance(store_root, Path):
            raise ExperimentCatalogError("artifact store root is unavailable")
        try:
            _, db_path = resolve_disjoint_paths(store_root, raw, first_label="artifact store", second_label="database")
        except PathContainmentError as exc:
            raise ExperimentCatalogError("database path must be outside the artifact store") from exc
        if raw.exists() and not raw.is_file():
            raise ExperimentCatalogError("database path must be a regular file")
        self._path = db_path
        self._store = artifact_store
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        try:
            if self._path.is_symlink():
                raise ExperimentCatalogError("database symlinks are not supported")
            conn = sqlite3.connect(str(self._path), timeout=5.0)
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA synchronous=FULL")
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error as exc:
            raise ExperimentCatalogError("could not open catalog database") from exc

    def _initialize(self) -> None:
        conn = self._connect()
        try:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version not in {0, CATALOG_SCHEMA_VERSION}:
                raise ExperimentCatalogError("unsupported catalog schema version")
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required = {"artifacts", "ablation_variants", "metric_aggregates", "ablation_comparisons"}
            if version == 0 and tables:
                raise ExperimentCatalogError("catalog schema is malformed")
            if version == 0:
                with conn:
                    conn.executescript(
                        """
                        CREATE TABLE artifacts (
                            artifact_id TEXT PRIMARY KEY,
                            artifact_type TEXT NOT NULL,
                            relative_path TEXT NOT NULL UNIQUE,
                            byte_length INTEGER NOT NULL,
                            serialization_schema_version TEXT NOT NULL,
                            benchmark_id TEXT NOT NULL,
                            benchmark_version TEXT NOT NULL,
                            status TEXT NOT NULL,
                            run_fingerprint TEXT,
                            evaluation_fingerprint TEXT,
                            document_manifest_hash TEXT,
                            shared_index_fingerprint TEXT,
                            case_count INTEGER,
                            succeeded_case_count INTEGER,
                            failed_case_count INTEGER
                        );
                        CREATE TABLE ablation_variants (
                            artifact_id TEXT NOT NULL,
                            ordinal INTEGER NOT NULL,
                            variant_id TEXT NOT NULL,
                            run_fingerprint TEXT NOT NULL,
                            status TEXT NOT NULL,
                            error_code TEXT,
                            evaluation_fingerprint TEXT,
                            case_count INTEGER,
                            succeeded_case_count INTEGER,
                            failed_case_count INTEGER,
                            PRIMARY KEY (artifact_id, variant_id),
                            UNIQUE (artifact_id, ordinal),
                            FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id) ON DELETE RESTRICT
                        );
                        CREATE TABLE metric_aggregates (
                            artifact_id TEXT NOT NULL,
                            variant_id TEXT NOT NULL,
                            metric_id TEXT NOT NULL,
                            metric_name TEXT NOT NULL,
                            status TEXT NOT NULL,
                            value REAL,
                            unit TEXT NOT NULL,
                            optimization_direction TEXT NOT NULL,
                            reference_kind TEXT NOT NULL,
                            implementation_version TEXT NOT NULL,
                            eligible_count INTEGER NOT NULL,
                            skipped_count INTEGER NOT NULL,
                            failed_count INTEGER NOT NULL,
                            unavailable_count INTEGER NOT NULL,
                            model_identifier TEXT,
                            methodology_note TEXT,
                            PRIMARY KEY (artifact_id, variant_id, metric_id),
                            FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id) ON DELETE RESTRICT
                        );
                        CREATE TABLE ablation_comparisons (
                            artifact_id TEXT NOT NULL,
                            metric_id TEXT NOT NULL,
                            state TEXT NOT NULL,
                            reason_code TEXT,
                            PRIMARY KEY (artifact_id, metric_id),
                            FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id) ON DELETE RESTRICT
                        );
                        CREATE INDEX artifacts_lookup ON artifacts (artifact_type, benchmark_id, benchmark_version, status, artifact_id);
                        CREATE INDEX metrics_lookup ON metric_aggregates (artifact_id, variant_id, metric_id);
                        PRAGMA user_version=1;
                        """
                    )
            elif tables != required:
                raise ExperimentCatalogError("catalog schema is incompatible")
            else:
                self._validate_schema(conn)
        except ExperimentCatalogError:
            raise
        except sqlite3.Error as exc:
            raise ExperimentCatalogError("catalog schema initialization failed") from exc
        finally:
            conn.close()

    @staticmethod
    def _validate_schema(conn: sqlite3.Connection) -> None:
        expected = {
            "artifacts": set(_ARTIFACT_COLUMNS),
            "ablation_variants": set(_VARIANT_COLUMNS),
            "metric_aggregates": set(_METRIC_COLUMNS),
            "ablation_comparisons": {"artifact_id", "metric_id", "state", "reason_code"},
        }
        for table, columns in expected.items():
            actual = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if actual != columns:
                raise ExperimentCatalogError("catalog schema is incompatible")
        if int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
            raise ExperimentCatalogError("catalog foreign-key enforcement is unavailable")

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> CatalogArtifactRecord:
        return CatalogArtifactRecord(*(row[column] for column in _ARTIFACT_COLUMNS))

    @staticmethod
    def _metric_from_row(row: sqlite3.Row) -> CatalogMetricRecord:
        return CatalogMetricRecord(*(row[column] for column in _METRIC_COLUMNS))

    def _artifact_record(self, reference: StoredArtifact, result: EvaluationRunResult | AblationRunResult) -> CatalogArtifactRecord:
        if isinstance(result, EvaluationRunResult):
            report = result.report
            traces = result.traces
            artifact_type = "evaluation_result"
        else:
            report = None
            first_evaluation = next((variant.evaluation_result for variant in result.variants if variant.evaluation_result), None)
            traces = first_evaluation.traces if first_evaluation else ()
            artifact_type = "ablation_result"
        run = report.run_fingerprint if report else None
        evaluation = report.evaluation_fingerprint if report else None
        benchmark_id = report.benchmark_id if report else result.benchmark_id
        benchmark_version = report.benchmark_version if report else result.benchmark_version
        manifests = {trace.document_manifest_hash for trace in traces}
        indexes = {trace.index_fingerprint for trace in traces}
        if len(manifests) > 1 or len(indexes) > 1:
            raise CatalogIntegrityError("artifact traces have inconsistent provenance")
        outcomes = result.outcomes if isinstance(result, EvaluationRunResult) else (first_evaluation.outcomes if first_evaluation else ())
        succeeded = sum(item.status == "succeeded" for item in outcomes)
        failed = sum(item.status == "failed" for item in outcomes)
        status = "succeeded" if outcomes and failed == 0 else "failed" if outcomes and succeeded == 0 else "partial"
        if isinstance(result, AblationRunResult):
            status = result.status
            manifest = result.document_manifest_hash
            index = result.shared_index_fingerprint
        else:
            manifest = next(iter(manifests), None)
            index = next(iter(indexes), None)
        return CatalogArtifactRecord(reference.artifact_id, artifact_type, reference.relative_path, reference.byte_length, reference.serialization_schema_version, benchmark_id, benchmark_version, status, run, evaluation, manifest, index, len(outcomes) if outcomes else None, succeeded if outcomes else None, failed if outcomes else None)

    @staticmethod
    def _metrics(reference_id: str, result: EvaluationRunResult | AblationRunResult) -> tuple[CatalogMetricRecord, ...]:
        rows: list[CatalogMetricRecord] = []
        reports: Iterable[tuple[str, object]]
        if isinstance(result, EvaluationRunResult):
            reports = (("", result.report),)
        else:
            reports = tuple((variant.variant_id, variant.evaluation_result.report) for variant in result.variants if variant.evaluation_result is not None)
        for variant_id, report in reports:
            for aggregate in report.aggregates:
                rows.append(CatalogMetricRecord(reference_id, variant_id, aggregate.metric_id, aggregate.metric_name, aggregate.status, aggregate.value, aggregate.unit, aggregate.optimization_direction, aggregate.reference_kind, aggregate.implementation_version, aggregate.eligible_count, aggregate.skipped_count, aggregate.failed_count, aggregate.unavailable_count, aggregate.model_identifier, aggregate.methodology_note))
        return tuple(sorted(rows, key=lambda item: (item.variant_id, item.metric_id)))

    @staticmethod
    def _variants(reference_id: str, result: AblationRunResult) -> tuple[tuple[object, ...], ...]:
        rows = []
        for ordinal, variant in enumerate(result.variants, 1):
            evaluation = variant.evaluation_result
            outcomes = evaluation.outcomes if evaluation else ()
            rows.append((reference_id, ordinal, variant.variant_id, variant.run_fingerprint, variant.status, variant.error_code, evaluation.report.evaluation_fingerprint if evaluation else None, len(outcomes) if evaluation else None, sum(item.status == "succeeded" for item in outcomes) if evaluation else None, sum(item.status == "failed" for item in outcomes) if evaluation else None))
        return tuple(rows)

    @staticmethod
    def _comparisons(reference_id: str, result: AblationRunResult) -> tuple[tuple[object, ...], ...]:
        return tuple(sorted(((reference_id, item.metric_id, item.state, item.reason_code) for item in result.metric_comparisons), key=lambda item: item[1]))

    def _register(self, reference: StoredArtifact, result: EvaluationRunResult | AblationRunResult) -> CatalogArtifactRecord:
        expected = self._artifact_record(reference, result)
        metrics = self._metrics(reference.artifact_id, result)
        variants = self._variants(reference.artifact_id, result) if isinstance(result, AblationRunResult) else ()
        comparisons = self._comparisons(reference.artifact_id, result) if isinstance(result, AblationRunResult) else ()
        conn = self._connect()
        try:
            # Reserve the SQLite writer before checking for an existing row.
            # This serializes the idempotency check with registration writes
            # across connections sharing this local database.
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT * FROM artifacts WHERE artifact_id=?", (reference.artifact_id,)).fetchone()
            if existing is not None:
                stored = self._record_from_row(existing)
                if stored != expected or self._load_related(conn, reference.artifact_id, stored.artifact_type) != (metrics, variants, comparisons):
                    raise CatalogIntegrityError("catalog metadata conflicts with artifact")
                conn.commit()
                return stored
            conn.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", tuple(expected.__dict__.values()))
            if variants:
                conn.executemany("INSERT INTO ablation_variants VALUES (?,?,?,?,?,?,?,?,?,?)", variants)
            if metrics:
                conn.executemany("INSERT INTO metric_aggregates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [tuple(item.__dict__.values()) for item in metrics])
            if comparisons:
                conn.executemany("INSERT INTO ablation_comparisons VALUES (?,?,?,?)", comparisons)
            conn.commit()
            return expected
        except CatalogIntegrityError:
            conn.rollback()
            raise
        except sqlite3.Error as exc:
            conn.rollback()
            raise ExperimentCatalogError("catalog registration failed") from exc
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _load_related(conn: sqlite3.Connection, artifact_id: str, artifact_type: str):
        metrics = tuple(CatalogMetricRecord(*(row[column] for column in _METRIC_COLUMNS)) for row in conn.execute("SELECT * FROM metric_aggregates WHERE artifact_id=? ORDER BY variant_id, metric_id", (artifact_id,)))
        variants = tuple(tuple(row[column] for column in _VARIANT_COLUMNS) for row in conn.execute("SELECT * FROM ablation_variants WHERE artifact_id=? ORDER BY ordinal", (artifact_id,))) if artifact_type == "ablation_result" else ()
        comparisons = tuple(tuple(row[column] for column in ("artifact_id", "metric_id", "state", "reason_code")) for row in conn.execute("SELECT * FROM ablation_comparisons WHERE artifact_id=? ORDER BY metric_id", (artifact_id,))) if artifact_type == "ablation_result" else ()
        return metrics, variants, comparisons

    def save_evaluation_result(self, result: EvaluationRunResult) -> CatalogArtifactRecord:
        reference = self._store.save_evaluation_result(result)
        loaded = self._store.load_evaluation_result(reference.artifact_id)
        return self._register(reference, loaded)

    def save_ablation_result(self, result: AblationRunResult, *, ablation_plan: AblationPlan) -> CatalogArtifactRecord:
        reference = self._store.save_ablation_result(result, ablation_plan=ablation_plan)
        loaded = self._store.load_ablation_result(reference.artifact_id, ablation_plan=ablation_plan)
        return self._register(reference, loaded)

    def register_existing_evaluation(self, artifact_id: str) -> CatalogArtifactRecord:
        _hash(artifact_id, "artifact_id")
        try:
            result = self._store.load_evaluation_result(artifact_id)
        except ArtifactNotFoundError as exc:
            raise CatalogNotFoundError("artifact was not found in the artifact store") from exc
        reference = self._find_store_reference(artifact_id, "evaluation_result")
        return self._register(reference, result)

    def register_existing_ablation(self, artifact_id: str, *, ablation_plan: AblationPlan) -> CatalogArtifactRecord:
        _hash(artifact_id, "artifact_id")
        if not isinstance(ablation_plan, AblationPlan):
            raise ExperimentCatalogError("ablation plan is invalid")
        try:
            result = self._store.load_ablation_result(artifact_id, ablation_plan=ablation_plan)
        except ArtifactNotFoundError as exc:
            raise CatalogNotFoundError("artifact was not found in the artifact store") from exc
        reference = self._find_store_reference(artifact_id, "ablation_result")
        return self._register(reference, result)

    def _find_store_reference(self, artifact_id: str, artifact_type: str) -> StoredArtifact:
        _hash(artifact_id, "artifact_id")
        for item in self._store.list_artifacts(artifact_type):
            if item.artifact_id == artifact_id:
                return item
        raise CatalogNotFoundError("artifact was not found in the artifact store")

    def get_artifact(self, artifact_id: str) -> CatalogArtifactRecord:
        _hash(artifact_id, "artifact_id")
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            raise CatalogNotFoundError("catalog artifact was not found")
        return self._record_from_row(row)

    def _cross_check(self, record: CatalogArtifactRecord, result: EvaluationRunResult | AblationRunResult) -> None:
        reference = StoredArtifact(record.artifact_type, record.artifact_id, record.relative_path, record.byte_length, record.serialization_schema_version)
        expected = self._artifact_record(reference, result)
        if expected != record:
            raise CatalogIntegrityError("catalog metadata disagrees with artifact")
        metrics = self._metrics(record.artifact_id, result)
        conn = self._connect()
        try:
            actual = self._load_related(conn, record.artifact_id, record.artifact_type)
        finally:
            conn.close()
        variants = self._variants(record.artifact_id, result) if isinstance(result, AblationRunResult) else ()
        comparisons = self._comparisons(record.artifact_id, result) if isinstance(result, AblationRunResult) else ()
        if actual != (metrics, variants, comparisons):
            raise CatalogIntegrityError("catalog related metadata disagrees with artifact")

    def load_evaluation_result(self, artifact_id: str) -> EvaluationRunResult:
        record = self.get_artifact(artifact_id)
        if record.artifact_type != "evaluation_result":
            raise CatalogIntegrityError("catalog artifact type does not match loader")
        try:
            result = self._store.load_evaluation_result(artifact_id)
        except Exception as exc:
            raise CatalogIntegrityError("registered evaluation artifact failed validation") from exc
        self._cross_check(record, result)
        return result

    def load_ablation_result(self, artifact_id: str, *, ablation_plan: AblationPlan) -> AblationRunResult:
        if not isinstance(ablation_plan, AblationPlan):
            raise ExperimentCatalogError("ablation plan is invalid")
        record = self.get_artifact(artifact_id)
        if record.artifact_type != "ablation_result":
            raise CatalogIntegrityError("catalog artifact type does not match loader")
        try:
            result = self._store.load_ablation_result(artifact_id, ablation_plan=ablation_plan)
        except Exception as exc:
            raise CatalogIntegrityError("registered ablation artifact failed validation") from exc
        self._cross_check(record, result)
        return result

    def list_artifacts(self, *, artifact_type: str | None = None, benchmark_id: str | None = None, benchmark_version: str | None = None, run_fingerprint: str | None = None, index_fingerprint: str | None = None, status: str | None = None, limit: int = 100, offset: int = 0) -> tuple[CatalogArtifactRecord, ...]:
        if artifact_type is not None and artifact_type not in _ARTIFACT_TYPES:
            raise ExperimentCatalogError("artifact type is invalid")
        if benchmark_id is not None:
            _field(benchmark_id, "benchmark_id", maximum=120)
        if benchmark_version is not None:
            _field(benchmark_version, "benchmark_version", maximum=80)
        _hash(run_fingerprint, "run_fingerprint", optional=True)
        _hash(index_fingerprint, "index_fingerprint", optional=True)
        if status is not None and status not in _STATUSES:
            raise ExperimentCatalogError("artifact status is invalid")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 < limit <= _MAX_LIMIT:
            raise ExperimentCatalogError("limit is invalid")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ExperimentCatalogError("offset is invalid")
        clauses = []
        params: list[object] = []
        for column, value in (("artifact_type", artifact_type), ("benchmark_id", benchmark_id), ("benchmark_version", benchmark_version), ("run_fingerprint", run_fingerprint), ("shared_index_fingerprint", index_fingerprint), ("status", status)):
            if value is not None:
                clauses.append(f"{column}=?")
                params.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        conn = self._connect()
        try:
            rows = conn.execute(f"SELECT * FROM artifacts{where} ORDER BY artifact_type, artifact_id LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
        except sqlite3.Error as exc:
            raise ExperimentCatalogError("catalog listing failed") from exc
        finally:
            conn.close()
        return tuple(self._record_from_row(row) for row in rows)

    def list_metric_aggregates(self, artifact_id: str, *, variant_id: str | None = None) -> tuple[CatalogMetricRecord, ...]:
        _hash(artifact_id, "artifact_id")
        if variant_id is not None and variant_id != "" and not _FIELD_ID_RE.fullmatch(variant_id):
            raise ExperimentCatalogError("variant_id is invalid")
        self.get_artifact(artifact_id)
        conn = self._connect()
        try:
            if variant_id is None:
                rows = conn.execute("SELECT * FROM metric_aggregates WHERE artifact_id=? ORDER BY variant_id, metric_id", (artifact_id,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM metric_aggregates WHERE artifact_id=? AND variant_id=? ORDER BY metric_id", (artifact_id, variant_id)).fetchall()
        finally:
            conn.close()
        return tuple(self._metric_from_row(row) for row in rows)

    def list_comparisons(self, artifact_id: str) -> tuple[CatalogComparisonRecord, ...]:
        _hash(artifact_id, "artifact_id")
        self.get_artifact(artifact_id)
        conn = self._connect()
        try:
            rows = conn.execute("SELECT artifact_id, metric_id, state, reason_code FROM ablation_comparisons WHERE artifact_id=? ORDER BY metric_id", (artifact_id,)).fetchall()
        finally:
            conn.close()
        return tuple(CatalogComparisonRecord(*(row[column] for column in ("artifact_id", "metric_id", "state", "reason_code"))) for row in rows)


__all__ = [
    "CATALOG_SCHEMA_VERSION",
    "ExperimentCatalog",
    "CatalogArtifactRecord",
    "CatalogMetricRecord",
    "CatalogComparisonRecord",
    "ExperimentCatalogError",
    "CatalogNotFoundError",
    "CatalogIntegrityError",
]
