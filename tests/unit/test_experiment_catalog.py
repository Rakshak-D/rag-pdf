from __future__ import annotations

import hashlib
import sqlite3
import threading

import pytest

from backend.config import ResolvedRunConfig
from backend.experiments import (
    CatalogIntegrityError,
    CatalogNotFoundError,
    AblationRunResult,
    AblationVariantResult,
    EvaluationCaseOutcome,
    EvaluationRunResult,
    ExperimentArtifactStore,
    ExperimentCatalog,
    ExperimentCatalogError,
    MetricAggregate,
    MetricObservation,
    build_ablation_plan,
    build_evaluation_report,
    build_failed_query_trace,
)
from backend.experiments.ablation_runner import _comparison
from notebooks.config import Config
from tests.unit.test_artifact_store import _failed_ablation
from tests.unit.test_experiment_serialization import _evaluation
from backend.experiments.tracing import TraceFailure


def _plan():
    base = ResolvedRunConfig.from_legacy(Config(reset_collections_on_start=False))
    return build_ablation_plan(base, "b" * 64)


def _ablation_with_reports(plan) -> AblationRunResult:
    variants = []
    for planned in plan.variants:
        trace = build_failed_query_trace(
            planned.run_plan,
            query="private question",
            modalities=("text",),
            text_k=5,
            image_k=None,
            failure=TraceFailure("retrieval", "retrieval_failed"),
        )
        observations = tuple(
            MetricObservation(trace.trace_id, f"M{number}", f"Metric {number}", "skipped", None, "score", "descriptive", "no_reference", "test-v1", reason_code="not_reached")
            for number in range(1, 20)
        )
        aggregates = tuple(
            MetricAggregate(f"M{number}", f"Metric {number}", "no_eligible_values", None, "score", "descriptive", "no_reference", "test-v1", 0, 1, 0, 0)
            for number in range(1, 20)
        )
        report = build_evaluation_report(
            (trace,), observations, aggregates,
            run_fingerprint=planned.run_plan.run_fingerprint,
            benchmark_id="synthetic", benchmark_version="v1",
        )
        evaluation = EvaluationRunResult(
            report,
            (trace,),
            (EvaluationCaseOutcome("case-1", "failed", "retrieval", trace.trace_id, "retrieval_failed"),),
        )
        variants.append(AblationVariantResult(planned.variant_id, planned.run_plan.run_fingerprint, "failed", evaluation))
    variant_tuple = tuple(variants)
    return AblationRunResult(
        "1", "partial", plan.document_manifest_hash, plan.shared_index_fingerprint,
        "synthetic", "v1", variant_tuple,
        tuple(_comparison(f"M{number}", variant_tuple) for number in range(1, 20)),
    )


def _serialize_registrations(catalog, register, *, count=4, same_result=True):
    """Hold the first writer reservation while other connections attempt BEGIN."""
    first_reserved = threading.Event()
    release_first = threading.Event()
    attempts_condition = threading.Condition()
    other_attempts = 0
    original_connect = catalog._connect

    class ControlledConnection:
        def __init__(self, connection):
            self._connection = connection

        def execute(self, sql, parameters=()):
            nonlocal other_attempts
            if sql.strip().upper() == "BEGIN IMMEDIATE":
                if threading.current_thread().name == "catalog-register-0":
                    cursor = self._connection.execute(sql, parameters)
                    first_reserved.set()
                    if not release_first.wait(5):
                        raise AssertionError("test did not release first catalog writer")
                    return cursor
                with attempts_condition:
                    other_attempts += 1
                    attempts_condition.notify_all()
            return self._connection.execute(sql, parameters)

        def __getattr__(self, name):
            return getattr(self._connection, name)

    catalog._connect = lambda: ControlledConnection(original_connect())
    barrier = threading.Barrier(count)
    results = [None] * count
    errors = [None] * count

    def invoke(index):
        try:
            barrier.wait(timeout=5)
            results[index] = register(index)
        except BaseException as exc:
            errors[index] = exc

    threads = [threading.Thread(target=invoke, args=(index,), name=f"catalog-register-{index}") for index in range(count)]
    try:
        for thread in threads:
            thread.start()
        assert first_reserved.wait(5), "no writer acquired the registration reservation"
        with attempts_condition:
            assert attempts_condition.wait_for(lambda: other_attempts == count - 1, timeout=5), "other writers did not reach the reservation boundary"
    finally:
        release_first.set()
        for thread in threads:
            if thread.ident is not None:
                thread.join(timeout=10)
        catalog._connect = original_connect
    assert all(not thread.is_alive() for thread in threads), "registration thread did not terminate"
    assert errors == [None] * count
    if same_result:
        assert all(record == results[0] for record in results)
        return results[0]
    return tuple(results)


def test_catalog_registers_and_loads_evaluation_metadata(tmp_path):
    store = ExperimentArtifactStore(tmp_path / "artifacts")
    catalog = ExperimentCatalog(tmp_path / "catalog.sqlite", store)

    record = catalog.save_evaluation_result(_evaluation(tmp_path))

    assert record.artifact_type == "evaluation_result"
    assert record.relative_path == f"evaluation_result/{record.artifact_id[:2]}/{record.artifact_id}.json"
    assert catalog.save_evaluation_result(_evaluation(tmp_path)) == record
    assert catalog.load_evaluation_result(record.artifact_id).report.evaluation_fingerprint == record.evaluation_fingerprint
    assert len(catalog.list_metric_aggregates(record.artifact_id)) == 19
    assert catalog.list_artifacts(status="failed") == (record,)


def test_catalog_registers_ablation_variants_and_comparisons(tmp_path):
    plan = _plan()
    store = ExperimentArtifactStore(tmp_path / "artifacts")
    catalog = ExperimentCatalog(tmp_path / "catalog.sqlite", store)

    record = catalog.save_ablation_result(_failed_ablation(plan), ablation_plan=plan)

    assert record.artifact_type == "ablation_result"
    assert len(catalog.list_comparisons(record.artifact_id)) == 19
    assert catalog.load_ablation_result(record.artifact_id, ablation_plan=plan).status == "failed"
    assert catalog.list_artifacts(artifact_type="ablation_result") == (record,)


def test_catalog_recovery_is_idempotent_and_requires_registered_type(tmp_path):
    result = _evaluation(tmp_path)
    store = ExperimentArtifactStore(tmp_path / "artifacts")
    reference = store.save_evaluation_result(result)
    catalog = ExperimentCatalog(tmp_path / "catalog.sqlite", store)

    assert catalog.register_existing_evaluation(reference.artifact_id).artifact_id == reference.artifact_id
    assert catalog.register_existing_evaluation(reference.artifact_id).artifact_id == reference.artifact_id
    with pytest.raises(CatalogNotFoundError):
        catalog.get_artifact("0" * 64)
    with pytest.raises(CatalogNotFoundError):
        catalog.register_existing_evaluation("0" * 64)


def test_catalog_rejects_unsafe_database_paths_and_future_schema(tmp_path):
    store_root = tmp_path / "artifacts"
    store = ExperimentArtifactStore(store_root)
    with pytest.raises(ExperimentCatalogError):
        ExperimentCatalog(store_root / "catalog.sqlite", store)

    database = tmp_path / "catalog.sqlite"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA user_version=99")
    connection.commit()
    connection.close()
    with pytest.raises(ExperimentCatalogError):
        ExperimentCatalog(database, store)


def test_catalog_detects_metadata_tampering_without_rewriting_it(tmp_path):
    store = ExperimentArtifactStore(tmp_path / "artifacts")
    catalog = ExperimentCatalog(tmp_path / "catalog.sqlite", store)
    record = catalog.save_evaluation_result(_evaluation(tmp_path))

    connection = sqlite3.connect(tmp_path / "catalog.sqlite")
    connection.execute("UPDATE artifacts SET benchmark_id='tampered' WHERE artifact_id=?", (record.artifact_id,))
    connection.commit()

    with pytest.raises(CatalogIntegrityError):
        catalog.load_evaluation_result(record.artifact_id)
    with pytest.raises(CatalogIntegrityError):
        catalog.register_existing_evaluation(record.artifact_id)

    connection.execute("UPDATE artifacts SET benchmark_id='synthetic' WHERE artifact_id=?", (record.artifact_id,))
    connection.execute("UPDATE metric_aggregates SET metric_name='tampered' WHERE artifact_id=? AND metric_id='M1'", (record.artifact_id,))
    connection.commit()
    with pytest.raises(CatalogIntegrityError):
        catalog.register_existing_evaluation(record.artifact_id)
    connection.close()


def test_concurrent_recovery_of_orphaned_evaluation_is_serialized_and_idempotent(tmp_path):
    result = _evaluation(tmp_path)
    store = ExperimentArtifactStore(tmp_path / "artifacts")
    reference = store.save_evaluation_result(result)
    catalog = ExperimentCatalog(tmp_path / "catalog.sqlite", store)
    with pytest.raises(CatalogNotFoundError):
        catalog.get_artifact(reference.artifact_id)

    record = _serialize_registrations(
        catalog,
        lambda _index: catalog.register_existing_evaluation(reference.artifact_id),
    )
    connection = sqlite3.connect(tmp_path / "catalog.sqlite")
    try:
        assert connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM metric_aggregates WHERE artifact_id=?", (record.artifact_id,)).fetchone()[0] == 19
    finally:
        connection.close()
    assert len(catalog.list_metric_aggregates(record.artifact_id)) == 19


def test_concurrent_ablation_registration_preserves_canonical_related_rows(tmp_path):
    plan = _plan()
    result = _ablation_with_reports(plan)
    store = ExperimentArtifactStore(tmp_path / "artifacts")
    reference = store.save_ablation_result(result, ablation_plan=plan)
    catalog = ExperimentCatalog(tmp_path / "catalog.sqlite", store)

    record = _serialize_registrations(
        catalog,
        lambda _index: catalog.register_existing_ablation(reference.artifact_id, ablation_plan=plan),
    )
    connection = sqlite3.connect(tmp_path / "catalog.sqlite")
    try:
        assert connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM ablation_variants WHERE artifact_id=?", (record.artifact_id,)).fetchone()[0] == 6
        assert connection.execute("SELECT COUNT(*) FROM metric_aggregates WHERE artifact_id=?", (record.artifact_id,)).fetchone()[0] == 6 * 19
        assert connection.execute("SELECT COUNT(*) FROM ablation_comparisons WHERE artifact_id=?", (record.artifact_id,)).fetchone()[0] == 19
        stored_variants = connection.execute("SELECT ordinal, variant_id, run_fingerprint FROM ablation_variants WHERE artifact_id=? ORDER BY ordinal", (record.artifact_id,)).fetchall()
    finally:
        connection.close()
    assert [(row[0], row[1], row[2]) for row in stored_variants] == [
        (index, variant.variant_id, variant.run_plan.run_fingerprint)
        for index, variant in enumerate(plan.variants, 1)
    ]
    assert len(catalog.list_metric_aggregates(record.artifact_id, variant_id=plan.variants[0].variant_id)) == 19
    assert len(catalog.list_comparisons(record.artifact_id)) == 19
    assert catalog.register_existing_ablation(reference.artifact_id, ablation_plan=plan) == record


def test_concurrent_distinct_artifact_registrations_keep_all_rows(tmp_path):
    first = _evaluation(tmp_path)
    second_report = build_evaluation_report(
        first.traces,
        first.report.observations,
        first.report.aggregates,
        run_fingerprint=first.report.run_fingerprint,
        benchmark_id="different-synthetic",
        benchmark_version=first.report.benchmark_version,
    )
    second = EvaluationRunResult(second_report, first.traces, first.outcomes)
    store = ExperimentArtifactStore(tmp_path / "artifacts")
    first_ref = store.save_evaluation_result(first)
    second_ref = store.save_evaluation_result(second)
    assert first_ref.artifact_id != second_ref.artifact_id
    catalog = ExperimentCatalog(tmp_path / "catalog.sqlite", store)
    records = _serialize_registrations(
        catalog,
        lambda index: catalog.register_existing_evaluation(first_ref.artifact_id if index % 2 == 0 else second_ref.artifact_id),
        count=2,
        same_result=False,
    )
    assert {record.artifact_id for record in records} == {first_ref.artifact_id, second_ref.artifact_id}
    connection = sqlite3.connect(tmp_path / "catalog.sqlite")
    try:
        assert connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM metric_aggregates").fetchone()[0] == 2 * 19
    finally:
        connection.close()


def test_failed_registration_rolls_back_all_related_rows(tmp_path):
    result = _evaluation(tmp_path)
    store = ExperimentArtifactStore(tmp_path / "artifacts")
    catalog = ExperimentCatalog(tmp_path / "catalog.sqlite", store)
    connection = sqlite3.connect(tmp_path / "catalog.sqlite")
    connection.execute(
        "CREATE TRIGGER reject_metrics BEFORE INSERT ON metric_aggregates "
        "BEGIN SELECT RAISE(ABORT, 'controlled failure'); END"
    )
    connection.commit()
    connection.close()

    with pytest.raises(ExperimentCatalogError, match="catalog registration failed"):
        catalog.save_evaluation_result(result)

    reference, = store.list_artifacts("evaluation_result")
    path = tmp_path / "artifacts" / reference.relative_path
    published_bytes = path.read_bytes()
    assert hashlib.sha256(published_bytes).hexdigest() == reference.artifact_id
    assert store.list_artifacts("evaluation_result") == (reference,)
    assert store.load_evaluation_result(reference.artifact_id).report.evaluation_fingerprint == result.report.evaluation_fingerprint
    connection = sqlite3.connect(tmp_path / "catalog.sqlite")
    try:
        assert connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM metric_aggregates").fetchone()[0] == 0
    finally:
        connection.close()

    connection = sqlite3.connect(tmp_path / "catalog.sqlite")
    connection.execute("DROP TRIGGER reject_metrics")
    connection.commit()
    connection.close()

    recovered = catalog.register_existing_evaluation(reference.artifact_id)
    loaded = store.load_evaluation_result(reference.artifact_id)
    assert recovered == catalog._artifact_record(reference, loaded)
    assert tuple(catalog.list_metric_aggregates(reference.artifact_id)) == catalog._metrics(reference.artifact_id, loaded)
    assert catalog.register_existing_evaluation(reference.artifact_id) == recovered
    assert path.read_bytes() == published_bytes
    connection = sqlite3.connect(tmp_path / "catalog.sqlite")
    try:
        assert connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM metric_aggregates WHERE artifact_id=?", (reference.artifact_id,)).fetchone()[0] == 19
    finally:
        connection.close()


def test_ablation_recovers_after_late_catalog_registration_failure(tmp_path):
    plan = _plan()
    result = _ablation_with_reports(plan)
    store = ExperimentArtifactStore(tmp_path / "artifacts")
    catalog = ExperimentCatalog(tmp_path / "catalog.sqlite", store)
    connection = sqlite3.connect(tmp_path / "catalog.sqlite")
    connection.execute(
        "CREATE TRIGGER reject_comparisons BEFORE INSERT ON ablation_comparisons "
        "BEGIN SELECT RAISE(ABORT, 'controlled late failure'); END"
    )
    connection.commit()
    connection.close()

    with pytest.raises(ExperimentCatalogError, match="catalog registration failed"):
        catalog.save_ablation_result(result, ablation_plan=plan)

    reference, = store.list_artifacts("ablation_result")
    path = tmp_path / "artifacts" / reference.relative_path
    published_bytes = path.read_bytes()
    assert hashlib.sha256(published_bytes).hexdigest() == reference.artifact_id
    assert store.list_artifacts("ablation_result") == (reference,)
    restored_before_recovery = store.load_ablation_result(reference.artifact_id, ablation_plan=plan)
    assert restored_before_recovery == result
    connection = sqlite3.connect(tmp_path / "catalog.sqlite")
    try:
        assert connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM ablation_variants").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM metric_aggregates").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM ablation_comparisons").fetchone()[0] == 0
    finally:
        connection.close()

    connection = sqlite3.connect(tmp_path / "catalog.sqlite")
    connection.execute("DROP TRIGGER reject_comparisons")
    connection.commit()
    connection.close()

    recovered = catalog.register_existing_ablation(reference.artifact_id, ablation_plan=plan)
    restored = catalog.load_ablation_result(reference.artifact_id, ablation_plan=plan)
    assert restored == result
    assert recovered == catalog._artifact_record(reference, restored)
    assert tuple(catalog.list_metric_aggregates(reference.artifact_id)) == catalog._metrics(reference.artifact_id, restored)
    assert catalog.register_existing_ablation(reference.artifact_id, ablation_plan=plan) == recovered
    assert path.read_bytes() == published_bytes

    connection = sqlite3.connect(tmp_path / "catalog.sqlite")
    try:
        assert connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM ablation_variants WHERE artifact_id=?", (reference.artifact_id,)).fetchone()[0] == 6
        assert connection.execute("SELECT COUNT(*) FROM metric_aggregates WHERE artifact_id=?", (reference.artifact_id,)).fetchone()[0] == 6 * 19
        assert connection.execute("SELECT COUNT(*) FROM ablation_comparisons WHERE artifact_id=?", (reference.artifact_id,)).fetchone()[0] == 19
        rows = connection.execute(
            "SELECT ordinal, variant_id, run_fingerprint FROM ablation_variants WHERE artifact_id=? ORDER BY ordinal",
            (reference.artifact_id,),
        ).fetchall()
    finally:
        connection.close()
    assert [(row[0], row[1], row[2]) for row in rows] == [
        (ordinal, variant.variant_id, variant.run_plan.run_fingerprint)
        for ordinal, variant in enumerate(plan.variants, 1)
    ]


def test_catalog_foreign_keys_and_filters_are_parameterized(tmp_path):
    store = ExperimentArtifactStore(tmp_path / "artifacts")
    catalog = ExperimentCatalog(tmp_path / "catalog.sqlite", store)
    record = catalog.save_evaluation_result(_evaluation(tmp_path))
    connection = catalog._connect()
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    connection.close()

    assert catalog.list_artifacts(benchmark_id="synthetic; DROP TABLE artifacts") == ()
    with pytest.raises(ExperimentCatalogError):
        catalog.list_artifacts(limit=True)
    with pytest.raises(ExperimentCatalogError):
        catalog.list_artifacts(offset=-1)
    assert catalog.list_artifacts(limit=1, offset=0) == (record,)
