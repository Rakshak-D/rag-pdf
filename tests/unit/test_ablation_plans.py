from __future__ import annotations

import json

import pytest

from backend.config import ResolvedRunConfig, WorkbenchSafetyPolicy
from backend.experiments.ablation_plans import (
    AblationPlan,
    AblationPlanError,
    build_ablation_plan,
)
from notebooks.config import Config
from tests.support.source_loader import load_assignment


def make_config(*, unsafe: bool = False) -> ResolvedRunConfig:
    return ResolvedRunConfig.from_legacy(
        Config(),
        workbench_policy=WorkbenchSafetyPolicy(
            allow_destructive_index_reset=unsafe,
            allow_collection_delete=unsafe,
        ),
    )


def test_six_plans_match_source_assignments_and_share_index_identity():
    base = make_config(unsafe=True)
    plan = build_ablation_plan(base, "a" * 64)
    source = load_assignment("_ABLATION_CONFIGS")

    assert [variant.variant_id for variant in plan.variants] == [label for label, _ in source]
    assert [dict(variant.overrides) for variant in plan.variants] == [overrides for _, overrides in source]
    assert len({variant.run_fingerprint for variant in plan.variants}) == 6
    assert len({variant.index_fingerprint for variant in plan.variants}) == 1
    assert all(variant.run_plan.document_manifest_hash == "a" * 64 for variant in plan.variants)
    assert all(not variant.resolved_config().reset_collections_on_start for variant in plan.variants)
    assert all(not variant.resolved_config().workbench_policy.allow_destructive_index_reset for variant in plan.variants)
    assert all(not variant.resolved_config().workbench_policy.allow_collection_delete for variant in plan.variants)


def test_plan_isolated_from_base_and_between_variant_configurations():
    base = make_config()
    plan = build_ablation_plan(base, "b" * 64)
    original_run_fingerprints = tuple(item.run_fingerprint for item in plan.variants)
    base.text_k = base.text_k + 1
    assert tuple(item.run_fingerprint for item in plan.variants) == original_run_fingerprints

    first = plan.variants[0].resolved_config()
    second = plan.variants[1].resolved_config()
    first.llm_models.append("local-test-model")
    assert "local-test-model" not in second.llm_models


def test_serialization_is_deterministic_and_round_trips_against_base():
    base = make_config()
    plan = build_ablation_plan(base, "c" * 64)
    serialized = plan.to_serializable_json()
    assert serialized == plan.to_serializable_json()
    restored = AblationPlan.from_serializable_json(serialized, base, "c" * 64)
    assert restored.to_serializable_json() == serialized
    assert tuple(item.configuration_snapshot_hash for item in restored.variants) == tuple(item.configuration_snapshot_hash for item in plan.variants)


@pytest.mark.parametrize("mutation", ["schema", "order", "override", "snapshot", "manifest", "unsafe"])
def test_serialized_plan_tampering_is_rejected(mutation):
    base = make_config()
    plan = build_ablation_plan(base, "d" * 64)
    payload = json.loads(plan.to_serializable_json())
    if mutation == "schema":
        payload["ablation_schema_version"] = "999"
    elif mutation == "order":
        payload["variants"][0], payload["variants"][1] = payload["variants"][1], payload["variants"][0]
    elif mutation == "override":
        payload["variants"][0]["overrides"]["retrieval_mode"] = "bm25"
    elif mutation == "snapshot":
        payload["variants"][0]["run_plan"]["resolved_config_json"] = payload["variants"][1]["run_plan"]["resolved_config_json"]
    elif mutation == "manifest":
        payload["document_manifest_hash"] = "e" * 64
    else:
        payload["variants"][0]["run_plan"]["resolved_config_json"] = payload["variants"][0]["run_plan"]["resolved_config_json"].replace('"reset_collections_on_start":false', '"reset_collections_on_start":true')
    with pytest.raises(AblationPlanError):
        AblationPlan.from_serializable_json(json.dumps(payload), base, "d" * 64)


def test_invalid_base_and_manifest_fail_without_execution():
    with pytest.raises(AblationPlanError):
        build_ablation_plan(object(), "a" * 64)
    with pytest.raises(AblationPlanError):
        build_ablation_plan(make_config(), "not-a-hash")
