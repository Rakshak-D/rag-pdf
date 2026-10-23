from __future__ import annotations

from pathlib import Path

from backend.experiments import BenchmarkCase, BenchmarkSuite, ExperimentArtifactStore, ExperimentCatalog
from backend.services import ExperimentService
from tests.unit.test_experiment_service import _service


def test_service_uses_real_artifact_store_and_catalog_round_trip(tmp_path: Path):
    service, plan, query = _service(tmp_path)
    suite = BenchmarkSuite("integration-bench", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5),))

    persisted = service.run_evaluation(plan, suite)

    assert (tmp_path / "artifacts").exists()
    assert (tmp_path / "catalog.sqlite").exists()
    assert service.load_evaluation_result(persisted.record.artifact_id).report.benchmark_id == "integration-bench"
    assert isinstance(service._catalog, ExperimentCatalog)
    assert isinstance(service._catalog._store, ExperimentArtifactStore)
