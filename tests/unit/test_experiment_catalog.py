from __future__ import annotations

import sqlite3

import pytest

from backend.config import ResolvedRunConfig
from backend.experiments import (
    CatalogIntegrityError,
    CatalogNotFoundError,
    ExperimentArtifactStore,
    ExperimentCatalog,
    ExperimentCatalogError,
    build_ablation_plan,
)
from notebooks.config import Config
from tests.unit.test_artifact_store import _failed_ablation
from tests.unit.test_experiment_serialization import _evaluation


def _plan():
    base = ResolvedRunConfig.from_legacy(Config(reset_collections_on_start=False))
    return build_ablation_plan(base, "b" * 64)


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
    connection.close()

    with pytest.raises(CatalogIntegrityError):
        catalog.load_evaluation_result(record.artifact_id)


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
