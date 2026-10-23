from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from backend.config import ResolvedRunConfig
from backend.experiments import (
    AblationRunResult,
    AblationVariantResult,
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    ArtifactStoreError,
    ExperimentArtifactStore,
    build_ablation_plan,
    deserialize_evaluation_result,
)
from backend.experiments.ablation_runner import _comparison
from backend.experiments.serialization import serialize_evaluation_result
from notebooks.config import Config
from tests.unit.test_experiment_serialization import _evaluation


def _failed_ablation(plan) -> AblationRunResult:
    variants = tuple(AblationVariantResult(item.variant_id, item.run_fingerprint, "failed", None, "ablation_variant_failed") for item in plan.variants)
    return AblationRunResult("1", "failed", plan.document_manifest_hash, plan.shared_index_fingerprint, "synthetic", "v1", variants, tuple(_comparison(f"M{number}", variants) for number in range(1, 20)))


def test_evaluation_save_load_is_content_addressed_and_listed(tmp_path):
    result = _evaluation(tmp_path)
    store = ExperimentArtifactStore(tmp_path / "artifacts")
    reference = store.save_evaluation_result(result)
    serialized = serialize_evaluation_result(result).encode("utf-8")
    assert reference.artifact_id == hashlib.sha256(serialized).hexdigest()
    assert store.save_evaluation_result(result) == reference
    assert store.load_evaluation_result(reference.artifact_id).report.evaluation_fingerprint == result.report.evaluation_fingerprint
    assert store.list_artifacts() == (reference,)
    assert store.list_artifacts("evaluation_result") == (reference,)
    with pytest.raises(ArtifactNotFoundError):
        store.load_evaluation_result("0" * 64)


def test_ablation_save_load_is_bound_to_the_supplied_plan(tmp_path):
    base = ResolvedRunConfig.from_legacy(Config(reset_collections_on_start=False))
    plan = build_ablation_plan(base, "c" * 64)
    result = _failed_ablation(plan)
    store = ExperimentArtifactStore(tmp_path / "artifacts")
    reference = store.save_ablation_result(result, ablation_plan=plan)
    restored = store.load_ablation_result(reference.artifact_id, ablation_plan=plan)
    assert restored.status == "failed"
    assert restored.variants[0].error_code == "ablation_variant_failed"
    with pytest.raises(ArtifactStoreError):
        store.load_ablation_result(reference.artifact_id, ablation_plan=object())


def test_corruption_is_rejected_and_never_repaired(tmp_path):
    result = _evaluation(tmp_path)
    store = ExperimentArtifactStore(tmp_path / "artifacts")
    reference = store.save_evaluation_result(result)
    path = tmp_path / "artifacts" / Path(reference.relative_path)
    path.write_bytes(b"corrupt")
    with pytest.raises(ArtifactIntegrityError):
        store.load_evaluation_result(reference.artifact_id)
    assert path.read_bytes() == b"corrupt"
    with pytest.raises(ArtifactStoreError):
        store.load_evaluation_result("../" + "0" * 60)


def test_size_limit_is_validated_before_publication(tmp_path):
    with pytest.raises(ArtifactStoreError):
        ExperimentArtifactStore(tmp_path, max_artifact_size=0)
    store = ExperimentArtifactStore(tmp_path / "artifacts", max_artifact_size=10)
    with pytest.raises(ArtifactStoreError):
        store.save_evaluation_result(_evaluation(tmp_path))
    assert not (tmp_path / "artifacts").exists()
