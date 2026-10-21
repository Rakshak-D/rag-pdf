from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.config import LegacyConfigSchema, ResolvedRunConfig, WorkbenchSafetyPolicy
from notebooks.config import Config


def test_schema_covers_every_legacy_dataclass_field():
    legacy_names = {field.name for field in dataclasses.fields(Config)}
    assert set(LegacyConfigSchema.model_fields) == legacy_names
    assert len(legacy_names) == 74


def test_defaults_match_an_actual_legacy_config_instance():
    legacy = Config()
    typed = LegacyConfigSchema()
    for field in dataclasses.fields(Config):
        assert getattr(typed, field.name) == getattr(legacy, field.name)


def test_conversion_round_trip_does_not_call_legacy_validate(monkeypatch):
    legacy = Config(llm_models=["custom-model"], llm_model="custom-model")

    def fail_validate(_self):
        raise AssertionError("compatibility conversion must not call Config.validate")

    monkeypatch.setattr(Config, "validate", fail_validate)
    typed = LegacyConfigSchema.from_legacy(legacy)
    restored = typed.to_legacy()

    for field in dataclasses.fields(Config):
        assert getattr(restored, field.name) == getattr(legacy, field.name)
    assert typed.fusion_type == "rrf"


def test_fusion_type_is_derived_from_weighted_fusion_flag():
    assert LegacyConfigSchema(use_weighted_fusion=False).fusion_type == "rrf"
    assert LegacyConfigSchema(use_weighted_fusion=True).fusion_type == "weighted_sum"


def test_unexpected_fields_are_rejected():
    with pytest.raises(ValidationError, match="unexpected_setting"):
        LegacyConfigSchema(unexpected_setting=1)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("chunk_max_tokens", 0, "chunk_max_tokens"),
        ("chunk_overlap_tokens", 384, "chunk_overlap_tokens"),
        ("text_k", 0, "text_k"),
        ("llm_temperature", 3, "llm_temperature"),
        ("percentile_cutoff", 101, "percentile_cutoff"),
        ("img_white_pixel_threshold", 256, "img_white_pixel_threshold"),
        ("img_max_aspect_ratio", 0.05, "img_max_aspect_ratio"),
    ],
)
def test_invalid_scalar_and_relationship_values_are_rejected(field, value, message):
    with pytest.raises(ValidationError, match=message):
        LegacyConfigSchema(**{field: value})


def test_invalid_mode_and_mode_dependent_flags_are_rejected():
    with pytest.raises(ValidationError, match="retrieval_mode"):
        LegacyConfigSchema(retrieval_mode="dense", adaptive_weighting=False)
    with pytest.raises(ValidationError, match="adaptive_weighting"):
        LegacyConfigSchema(retrieval_mode="semantic", adaptive_weighting=True)


@pytest.mark.parametrize(
    "overrides",
    [
        {"bm25_weight": 0.8, "semantic_weight": 0.3},
        {"adaptive_weights_keyword": (0.8, 0.3)},
        {"reranker_ce_weight": 0.5},
        {"bm25_signal_max_weight": 0.5},
    ],
)
def test_weight_groups_are_validated(overrides):
    with pytest.raises(ValidationError, match="sum to 1.0"):
        LegacyConfigSchema(**overrides)


def test_validation_has_no_filesystem_side_effects(tmp_path):
    paths = {
        name: str(tmp_path / name)
        for name in (
            "pdf_dir", "images_dir", "database_dir", "results_dir",
            "model_comparison_results_dir", "retrieval_results_dir",
        )
    }
    LegacyConfigSchema(**paths)
    assert not any(Path(path).exists() for path in paths.values())


def test_workbench_policy_keeps_research_reset_default_explicit():
    resolved = ResolvedRunConfig.from_legacy(Config())
    assert resolved.reset_collections_on_start is True
    assert resolved.workbench_policy.allow_destructive_index_reset is False
    with pytest.raises(ValueError, match="allow_destructive_index_reset"):
        resolved.validate_for_workbench()

    allowed = ResolvedRunConfig.from_legacy(
        Config(),
        workbench_policy=WorkbenchSafetyPolicy(allow_destructive_index_reset=True),
    )
    allowed.validate_for_workbench()


def test_serialization_round_trip_preserves_tuples_and_lists():
    typed = LegacyConfigSchema()
    restored = LegacyConfigSchema.model_validate(typed.to_serializable_dict())
    assert restored.adaptive_weights_keyword == (0.7, 0.3)
    assert isinstance(restored.adaptive_weights_keyword, tuple)
    assert restored.llm_models == ["gemma4:e2b"]
