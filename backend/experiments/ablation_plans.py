"""Deterministic, non-destructive plans for the six research ablations."""

from __future__ import annotations

from dataclasses import dataclass
import copy
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from backend.config.schema import ResolvedRunConfig

from .run_plan import RunPlan, build_run_plan


ABLATION_SCHEMA_VERSION = "1"
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_VARIANT_IDS = (
    "1_Dense_only",
    "2_BM25_only",
    "3_Hybrid_static",
    "4_Hybrid_adaptive",
    "5_Hybrid_reranker",
    "6_Full_pipeline",
)

# This is the checked-in research assignment, represented without importing or
# executing the notebook at runtime. Tuples keep the production definition
# immutable and make its serialized order explicit.
_ABLATION_OVERRIDES: tuple[tuple[str, tuple[tuple[str, Any], ...]], ...] = (
    (
        "1_Dense_only",
        (
            ("retrieval_mode", "semantic"),
            ("use_reranker", False),
            ("adaptive_weighting", False),
            ("use_weighted_fusion", False),
            ("use_filtering", False),
            ("use_percentile_filtering", False),
        ),
    ),
    (
        "2_BM25_only",
        (
            ("retrieval_mode", "bm25"),
            ("use_reranker", False),
            ("adaptive_weighting", False),
            ("use_weighted_fusion", False),
            ("use_filtering", False),
            ("use_percentile_filtering", False),
        ),
    ),
    (
        "3_Hybrid_static",
        (
            ("retrieval_mode", "hybrid"),
            ("adaptive_weighting", False),
            ("use_weighted_fusion", False),
            ("use_reranker", False),
            ("use_filtering", False),
            ("use_percentile_filtering", False),
        ),
    ),
    (
        "4_Hybrid_adaptive",
        (
            ("retrieval_mode", "hybrid"),
            ("adaptive_weighting", True),
            ("use_weighted_fusion", False),
            ("use_reranker", False),
            ("use_filtering", False),
            ("use_percentile_filtering", False),
        ),
    ),
    (
        "5_Hybrid_reranker",
        (
            ("retrieval_mode", "hybrid"),
            ("adaptive_weighting", False),
            ("use_weighted_fusion", False),
            ("use_reranker", True),
            ("use_filtering", False),
            ("use_percentile_filtering", False),
        ),
    ),
    (
        "6_Full_pipeline",
        (
            ("retrieval_mode", "hybrid"),
            ("adaptive_weighting", True),
            ("use_weighted_fusion", False),
            ("use_reranker", True),
            ("use_filtering", True),
            ("use_percentile_filtering", True),
            ("percentile_cutoff", 80),
        ),
    ),
)


class AblationPlanError(ValueError):
    """Raised when an ablation plan is invalid or fails integrity checks."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _manifest(value: object) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise AblationPlanError("document_manifest_hash must be a SHA-256 hash")
    return value.lower()


def _expected_override_map() -> dict[str, dict[str, Any]]:
    return {variant_id: dict(overrides) for variant_id, overrides in _ABLATION_OVERRIDES}


def _override_tuple(value: Mapping[str, Any], variant_id: str) -> tuple[tuple[str, Any], ...]:
    expected = _expected_override_map().get(variant_id)
    if expected is None or dict(value) != expected:
        raise AblationPlanError(f"overrides do not match the canonical definition for {variant_id}")
    return tuple((key, copy.deepcopy(expected[key])) for key in expected)


@dataclass(frozen=True)
class AblationVariantPlan:
    variant_id: str
    overrides: tuple[tuple[str, Any], ...]
    run_plan: RunPlan

    def __post_init__(self) -> None:
        if self.variant_id not in _VARIANT_IDS:
            raise AblationPlanError("unknown ablation variant")
        if not isinstance(self.run_plan, RunPlan):
            raise AblationPlanError("variant must contain a RunPlan")
        if dict(self.overrides) != _expected_override_map()[self.variant_id]:
            raise AblationPlanError("variant overrides do not match the canonical definition")
        self.run_plan.verify_integrity()

    @property
    def configuration_snapshot_hash(self) -> str:
        return self.run_plan.configuration_snapshot_hash

    @property
    def index_fingerprint(self) -> str:
        return self.run_plan.index_fingerprint

    @property
    def run_fingerprint(self) -> str:
        return self.run_plan.run_fingerprint

    def resolved_config(self) -> ResolvedRunConfig:
        return self.run_plan.resolved_config()


@dataclass(frozen=True)
class AblationPlan:
    schema_version: str
    document_manifest_hash: str
    shared_index_fingerprint: str
    variants: tuple[AblationVariantPlan, ...]

    def __post_init__(self) -> None:
        if self.schema_version != ABLATION_SCHEMA_VERSION:
            raise AblationPlanError("unsupported ablation schema version")
        manifest = _manifest(self.document_manifest_hash)
        if not isinstance(self.shared_index_fingerprint, str) or not _SHA256_RE.fullmatch(self.shared_index_fingerprint):
            raise AblationPlanError("shared_index_fingerprint must be a SHA-256 hash")
        if len(self.variants) != len(_VARIANT_IDS):
            raise AblationPlanError("an ablation plan must contain exactly six variants")
        if tuple(item.variant_id for item in self.variants) != _VARIANT_IDS:
            raise AblationPlanError("ablation variants are missing, duplicated, or out of order")
        for variant in self.variants:
            if variant.run_plan.document_manifest_hash != manifest:
                raise AblationPlanError("all variants must use the same document manifest")
            if variant.run_plan.index_fingerprint != self.shared_index_fingerprint:
                raise AblationPlanError("variants do not share the declared index fingerprint")
            config = variant.resolved_config()
            if config.reset_collections_on_start:
                raise AblationPlanError("ablation plans must disable collection reset")
            if config.workbench_policy.allow_destructive_index_reset or config.workbench_policy.allow_collection_delete:
                raise AblationPlanError("ablation plans must use a non-destructive safety policy")

    def to_serializable_json(self) -> str:
        payload = {
            "ablation_schema_version": self.schema_version,
            "document_manifest_hash": self.document_manifest_hash,
            "shared_index_fingerprint": self.shared_index_fingerprint,
            "variants": [
                {
                    "variant_id": variant.variant_id,
                    "overrides": dict(variant.overrides),
                    "run_plan": json.loads(variant.run_plan.to_serializable_json()),
                }
                for variant in self.variants
            ],
        }
        return _canonical_json(payload)

    @classmethod
    def from_serializable_json(
        cls,
        value: str,
        base_config: ResolvedRunConfig,
        document_manifest_hash: str,
    ) -> "AblationPlan":
        if not isinstance(value, str):
            raise AblationPlanError("serialized ablation plan must be a string")
        try:
            payload = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise AblationPlanError("serialized ablation plan is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise AblationPlanError("serialized ablation plan must be an object")
        if set(payload) != {"ablation_schema_version", "document_manifest_hash", "shared_index_fingerprint", "variants"}:
            raise AblationPlanError("serialized ablation plan has unexpected fields")
        manifest = _manifest(document_manifest_hash)
        if payload.get("document_manifest_hash") != manifest:
            raise AblationPlanError("serialized manifest does not match the accepted manifest")
        variants_payload = payload.get("variants")
        if not isinstance(variants_payload, list) or len(variants_payload) != len(_VARIANT_IDS):
            raise AblationPlanError("serialized ablation plan must contain six variants")
        variants: list[AblationVariantPlan] = []
        for expected_id, item in zip(_VARIANT_IDS, variants_payload):
            if not isinstance(item, dict) or set(item) != {"variant_id", "overrides", "run_plan"}:
                raise AblationPlanError("serialized variant has invalid fields")
            if item.get("variant_id") != expected_id:
                raise AblationPlanError("serialized variants are missing, duplicated, or out of order")
            overrides = item.get("overrides")
            if not isinstance(overrides, dict):
                raise AblationPlanError("serialized overrides must be an object")
            normalized_overrides = _override_tuple(overrides, expected_id)
            run_plan_payload = item.get("run_plan")
            if not isinstance(run_plan_payload, dict):
                raise AblationPlanError("serialized variant run_plan must be an object")
            try:
                run_plan = RunPlan.from_serializable_json(_canonical_json(run_plan_payload))
            except Exception as exc:
                raise AblationPlanError("serialized variant RunPlan failed integrity validation") from exc
            variants.append(AblationVariantPlan(expected_id, normalized_overrides, run_plan))
        try:
            plan = cls(payload["ablation_schema_version"], manifest, payload["shared_index_fingerprint"], tuple(variants))
        except Exception as exc:
            if isinstance(exc, AblationPlanError):
                raise
            raise AblationPlanError("serialized ablation plan failed validation") from exc
        expected = create_ablation_plan(base_config, manifest)
        if plan.to_serializable_json() != expected.to_serializable_json():
            raise AblationPlanError("serialized ablation plan does not match the accepted base configuration")
        return plan


def create_ablation_plan(base_config: ResolvedRunConfig, document_manifest_hash: str) -> AblationPlan:
    """Create six fresh, validated, non-destructive RunPlans without execution."""

    if not isinstance(base_config, ResolvedRunConfig):
        raise AblationPlanError("base_config must be a ResolvedRunConfig")
    manifest = _manifest(document_manifest_hash)
    try:
        base_values = base_config.model_dump(mode="python")
        # Explicitly replace, rather than inherit, destructive controls.
        base_values["reset_collections_on_start"] = False
        base_values["workbench_policy"] = {
            "allow_destructive_index_reset": False,
            "allow_collection_delete": False,
        }
        variants: list[AblationVariantPlan] = []
        for variant_id, raw_overrides in _ABLATION_OVERRIDES:
            values = copy.deepcopy(base_values)
            overrides = dict(raw_overrides)
            values.update(copy.deepcopy(overrides))
            config = ResolvedRunConfig.model_validate(values)
            config.validate_for_workbench()
            run_plan = build_run_plan(config, manifest)
            run_plan.verify_integrity()
            variants.append(AblationVariantPlan(variant_id, tuple((key, copy.deepcopy(value)) for key, value in raw_overrides), run_plan))
        fingerprints = {variant.index_fingerprint for variant in variants}
        if len(fingerprints) != 1:
            raise AblationPlanError("canonical ablation overlays are not index-compatible")
        return AblationPlan(ABLATION_SCHEMA_VERSION, manifest, next(iter(fingerprints)), tuple(variants))
    except AblationPlanError:
        raise
    except Exception as exc:
        raise AblationPlanError("could not construct validated ablation plans") from exc


build_ablation_plan = create_ablation_plan
load_ablation_plan = AblationPlan.from_serializable_json
