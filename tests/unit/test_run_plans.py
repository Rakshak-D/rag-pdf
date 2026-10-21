from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from backend.config import (
    LegacyConfigSchema,
    ResolvedRunConfig,
    WorkbenchSafetyPolicy,
    RUN_CONFIG_FIELDS,
    RUN_CONFIG_EXCLUDED_FIELDS,
    index_fingerprint,
    run_configuration_fingerprint,
)
from backend.experiments import RunPlan, RunPlanIntegrityError, build_run_plan
from notebooks.config import Config


MANIFEST_HASH = "a" * 64


def safe_config(**overrides) -> ResolvedRunConfig:
    values = Config(**overrides)
    return ResolvedRunConfig.from_legacy(
        values,
        workbench_policy=WorkbenchSafetyPolicy(allow_destructive_index_reset=True),
    )


def test_run_field_classification_covers_all_legacy_fields():
    from dataclasses import fields

    legacy_fields = {field.name for field in fields(Config)}
    assert set(RUN_CONFIG_FIELDS) | RUN_CONFIG_EXCLUDED_FIELDS == legacy_fields
    assert not set(RUN_CONFIG_FIELDS) & RUN_CONFIG_EXCLUDED_FIELDS


def test_unsafe_research_default_is_rejected_before_plan_creation():
    config = ResolvedRunConfig.from_legacy(Config())
    with pytest.raises(ValueError, match="allow_destructive_index_reset"):
        build_run_plan(config, MANIFEST_HASH)


def test_explicitly_authorized_configuration_produces_safe_plan():
    plan = build_run_plan(safe_config(), MANIFEST_HASH)
    assert isinstance(plan, RunPlan)
    assert plan.document_manifest_hash == MANIFEST_HASH
    plan.verify_integrity()


def test_plan_is_immutable_snapshot_of_source_configuration():
    config = safe_config(llm_temperature=0.25)
    plan = build_run_plan(config, MANIFEST_HASH)
    original = plan.to_serializable_json()

    config.llm_temperature = 1.5
    config.llm_models.append("another-model")

    assert plan.to_serializable_json() == original
    assert plan.resolved_config().llm_temperature == 0.25
    assert plan.resolved_config().llm_models == ["gemma4:e2b"]


def test_plan_serialization_is_stable_and_reconstructable():
    plan = build_run_plan(safe_config(), MANIFEST_HASH)
    serialized = plan.to_serializable_json()
    restored = RunPlan.from_serializable_json(serialized)
    assert restored.to_serializable_json() == serialized
    restored.verify_integrity()


def test_index_and_run_fingerprints_are_stable_for_identical_inputs():
    first = build_run_plan(safe_config(), MANIFEST_HASH)
    second = build_run_plan(safe_config(), MANIFEST_HASH)
    assert first.index_fingerprint == second.index_fingerprint
    assert first.run_fingerprint == second.run_fingerprint


def test_index_setting_changes_both_fingerprints():
    first = build_run_plan(safe_config(), MANIFEST_HASH)
    changed = build_run_plan(safe_config(chunk_max_tokens=256), MANIFEST_HASH)
    assert first.index_fingerprint != changed.index_fingerprint
    assert first.run_fingerprint != changed.run_fingerprint


@pytest.mark.parametrize("field", ["text_k", "llm_temperature", "llm_max_tokens", "rouge_top_k_chunks"])
def test_retrieval_generation_and_evaluation_settings_change_only_run_fingerprint(field):
    values = {"text_k": 6, "llm_temperature": 1.0, "llm_max_tokens": 512, "rouge_top_k_chunks": 4}
    first = build_run_plan(safe_config(), MANIFEST_HASH)
    changed = build_run_plan(safe_config(**{field: values[field]}), MANIFEST_HASH)
    assert first.index_fingerprint == changed.index_fingerprint
    assert first.run_fingerprint != changed.run_fingerprint


@pytest.mark.parametrize("field", ["database_dir", "results_dir", "metrics_verbose", "text_collection_name"])
def test_excluded_operational_settings_change_neither_fingerprint(field):
    values = {
        "database_dir": "different-db",
        "results_dir": "different-results",
        "metrics_verbose": False,
        "text_collection_name": "different-text",
    }
    first = build_run_plan(safe_config(), MANIFEST_HASH)
    changed = build_run_plan(safe_config(**{field: values[field]}), MANIFEST_HASH)
    assert first.index_fingerprint == changed.index_fingerprint
    assert first.run_fingerprint == changed.run_fingerprint


def test_manifest_change_changes_both_fingerprints():
    first = build_run_plan(safe_config(), MANIFEST_HASH)
    changed = build_run_plan(safe_config(), "b" * 64)
    assert first.index_fingerprint != changed.index_fingerprint
    assert first.run_fingerprint != changed.run_fingerprint


def test_integrity_verification_detects_manifest_index_and_run_tampering():
    plan = build_run_plan(safe_config(), MANIFEST_HASH)
    for field, value, message in (
        ("document_manifest_hash", "b" * 64, "index fingerprint mismatch"),
        ("index_fingerprint", "b" * 64, "index fingerprint mismatch"),
        ("run_fingerprint", "b" * 64, "run fingerprint mismatch"),
    ):
        payload = plan.model_dump(mode="json")
        payload[field] = value
        tampered = RunPlan.model_validate(payload)
        with pytest.raises(RunPlanIntegrityError, match=message):
            tampered.verify_integrity()


def test_integrity_verification_detects_corrupt_configuration_snapshot():
    plan = build_run_plan(safe_config(), MANIFEST_HASH)
    payload = plan.model_dump(mode="json")
    snapshot = json.loads(payload["resolved_config_json"])
    snapshot["unknown_field"] = True
    payload["resolved_config_json"] = json.dumps(snapshot)
    tampered = RunPlan.model_validate(payload)
    with pytest.raises(RunPlanIntegrityError, match="snapshot"):
        tampered.verify_integrity()


def test_integrity_verification_detects_valid_but_tampered_safety_policy():
    plan = build_run_plan(safe_config(), MANIFEST_HASH)
    payload = plan.model_dump(mode="json")
    snapshot = json.loads(payload["resolved_config_json"])
    snapshot["workbench_policy"]["allow_collection_delete"] = True
    payload["resolved_config_json"] = json.dumps(snapshot)
    tampered = RunPlan.model_validate(payload)
    with pytest.raises(RunPlanIntegrityError, match="snapshot hash"):
        tampered.verify_integrity()


def test_invalid_hashes_and_invalid_mapping_contracts_are_rejected():
    with pytest.raises(ValueError, match="document_manifest_hash"):
        build_run_plan(safe_config(), "not-a-sha256")

    resolved_mapping = safe_config().to_serializable_dict()
    with pytest.raises(ValidationError, match="workbench_policy"):
        LegacyConfigSchema(**resolved_mapping)
    with pytest.raises(ValidationError, match="workbench_policy"):
        index_fingerprint(resolved_mapping, MANIFEST_HASH)


def test_fingerprint_helpers_validate_inputs_without_side_effects(tmp_path):
    config = safe_config(database_dir=str(tmp_path / "database"))
    assert not (tmp_path / "database").exists()
    index_value = index_fingerprint(config, MANIFEST_HASH)
    assert run_configuration_fingerprint(config, index_value)
    assert not (tmp_path / "database").exists()
