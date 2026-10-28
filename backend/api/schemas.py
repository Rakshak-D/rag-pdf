"""Strict HTTP input models converted into existing domain contracts."""

from __future__ import annotations

import re
from typing import Any, Literal
import json

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.config.schema import ResolvedRunConfig
from backend.experiments.ablation_plans import create_ablation_plan
from backend.experiments.benchmark import BenchmarkCase, BenchmarkSuite
from backend.experiments.run_plan import RunPlan


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def canonical_sha256(value: str) -> str:
    """Validate the API's canonical SHA-256 text form and normalize casing."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise ValueError("value must be exactly 64 hexadecimal characters")
    return value.lower()


class RunPlanRequest(_StrictModel):
    plan_schema_version: str
    document_manifest_hash: str
    resolved_config_json: str
    configuration_snapshot_hash: str
    index_fingerprint: str
    run_fingerprint: str

    def to_domain(self) -> RunPlan:
        payload = self.model_dump(mode="json")
        return RunPlan.from_serializable_json(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


class RunPlanPreparationRequest(_StrictModel):
    """Inputs for preparing a plan against the live configured PDF corpus."""

    resolved_config: dict[str, Any]
    expected_manifest_hash: str

    @field_validator("expected_manifest_hash")
    @classmethod
    def validate_manifest_hash(cls, value: str) -> str:
        # This is the same accepted hexadecimal SHA-256 form as RunPlan and
        # index_fingerprint; the domain builder remains authoritative.
        return canonical_sha256(value)


class ManifestEntryResponse(_StrictModel):
    relative_path: str
    size_bytes: int
    sha256: str
    document_id: str


class ManifestResponse(_StrictModel):
    schema_version: str
    content_hash: str
    entry_count: int
    empty: bool
    entries: tuple[ManifestEntryResponse, ...]


class ManifestSummaryResponse(_StrictModel):
    schema_version: str
    content_hash: str
    entry_count: int


class RunPlanPreparationResponse(_StrictModel):
    run_plan: RunPlanRequest
    manifest: ManifestSummaryResponse


class BenchmarkCaseRequest(_StrictModel):
    case_id: str
    query: str
    modalities: list[Literal["text", "image"]] = Field(default_factory=lambda: ["text", "image"])
    text_k: int | None = None
    image_k: int | None = None
    required_pages: list[str | int] = Field(default_factory=list)
    reference_text: str | None = None

    def to_domain(self) -> BenchmarkCase:
        return BenchmarkCase(
            self.case_id,
            self.query,
            tuple(self.modalities),
            self.text_k,
            self.image_k,
            tuple(self.required_pages),
            self.reference_text,
        )


class BenchmarkSuiteRequest(_StrictModel):
    benchmark_id: str
    benchmark_version: str
    cases: list[BenchmarkCaseRequest]

    def to_domain(self) -> BenchmarkSuite:
        return BenchmarkSuite(self.benchmark_id, self.benchmark_version, tuple(case.to_domain() for case in self.cases))


class EvaluationRequest(_StrictModel):
    run_plan: RunPlanRequest
    benchmark_suite: BenchmarkSuiteRequest

    def to_domain(self) -> tuple[RunPlan, BenchmarkSuite]:
        return self.run_plan.to_domain(), self.benchmark_suite.to_domain()


class AblationRequest(_StrictModel):
    base_config: dict[str, Any]
    document_manifest_hash: str
    benchmark_suite: BenchmarkSuiteRequest

    def to_domain(self):
        config = ResolvedRunConfig.model_validate(self.base_config)
        if config.reset_collections_on_start or config.workbench_policy.allow_destructive_index_reset or config.workbench_policy.allow_collection_delete:
            raise ValueError("destructive configuration is not accepted")
        suite = self.benchmark_suite.to_domain()
        return create_ablation_plan(config, self.document_manifest_hash), suite


def parse_json_object(value: str) -> dict[str, Any]:
    """Parse bounded JSON while rejecting duplicate keys and non-finite constants."""

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, item in items:
            if key in output:
                raise ValueError("duplicate JSON object key")
            output[key] = item
        return output

    def constant(value: str) -> Any:
        raise ValueError("non-finite JSON number")

    parsed = json.loads(value, object_pairs_hook=pairs, parse_constant=constant)
    if not isinstance(parsed, dict):
        raise ValueError("request JSON must be an object")
    return parsed


__all__ = [
    "RunPlanRequest",
    "BenchmarkCaseRequest",
    "BenchmarkSuiteRequest",
    "EvaluationRequest",
    "AblationRequest",
    "parse_json_object",
]
