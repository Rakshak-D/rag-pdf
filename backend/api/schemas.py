"""Strict HTTP input models converted into existing domain contracts."""

from __future__ import annotations

import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Literal
import json

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


class IndexReadinessRequest(RunPlanPreparationRequest):
    workflow: Literal["evaluation", "ablation"]


class IndexSelectionRequest(_StrictModel):
    resolved_config: dict[str, Any]
    expected_manifest_hash: str
    expected_index_fingerprint: str
    build_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    expected_revision: int = Field(ge=0, le=2_147_483_646)
    operation_id: str = Field(pattern=r"^[0-9a-f]{32}$")

    @field_validator("expected_manifest_hash", "expected_index_fingerprint")
    @classmethod
    def validate_identity_hash(cls, value: str) -> str:
        return canonical_sha256(value)


class IndexSelectionIdentity(_StrictModel):
    build_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    report_reference: str = Field(pattern=r"^build-artifacts/[0-9a-f]{32}/build-report\.json$")
    document_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    index_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    report_status: Literal["completed", "completed_with_warnings"]
    selection_revision: int = Field(ge=1, le=2_147_483_647)


class IndexSelectionResponse(_StrictModel):
    selection_status: Literal["unselected", "selected", "stale", "invalid", "legacy_configured"]
    revision: int | None = Field(ge=0, le=2_147_483_647)
    selection: IndexSelectionIdentity | None
    legacy_report_reference: str | None
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    check_level: Literal["report"]
    store_checked: Literal[False]


class AskRequest(_StrictModel):
    question: str = Field(min_length=1, max_length=4000)

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any((ord(char) < 32 and char not in "\n\r\t") or ord(char) == 127 for char in normalized):
            raise ValueError("question_invalid")
        return normalized


class AskTextEvidenceResponse(_StrictModel):
    label: str = Field(pattern=r"^T[1-9][0-9]{0,2}$")
    document_id: str = Field(pattern=r"^doc_[0-9a-f]{64}$")
    source_path: str = Field(min_length=1, max_length=1024)
    page_number: int | None = Field(default=None, ge=1, le=1_000_000)
    passage: str = Field(min_length=1, max_length=32_000)
    record_id: str | None = Field(default=None, max_length=192)
    truncated: bool

    @field_validator("source_path")
    @classmethod
    def validate_source_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if ("\\" in value or path.is_absolute() or PureWindowsPath(value).drive or
                any(part in {"", ".", ".."} for part in path.parts) or path.as_posix() != value or path.suffix.lower() != ".pdf"):
            raise ValueError("source_path_invalid")
        return value


class AskImageEvidenceResponse(_StrictModel):
    label: str = Field(pattern=r"^I[1-9][0-9]{0,2}$")
    document_id: str = Field(pattern=r"^doc_[0-9a-f]{64}$")
    source_path: str = Field(min_length=1, max_length=1024)
    page_number: int | None = Field(default=None, ge=1, le=1_000_000)
    caption: str = Field(max_length=2000)
    record_id: str | None = Field(default=None, max_length=192)
    image_reference: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    content_type: Literal["image/png", "image/jpeg", "image/webp"] | None
    size_bytes: int | None = Field(default=None, ge=1, le=8 * 1024 * 1024)

    @field_validator("source_path")
    @classmethod
    def validate_source_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if ("\\" in value or path.is_absolute() or PureWindowsPath(value).drive or
                any(part in {"", ".", ".."} for part in path.parts) or path.as_posix() != value or path.suffix.lower() != ".pdf"):
            raise ValueError("source_path_invalid")
        return value

    @model_validator(mode="after")
    def validate_image_reference(self):
        values = (self.image_reference, self.content_type, self.size_bytes)
        if any(value is None for value in values) and any(value is not None for value in values):
            raise ValueError("image_reference_incomplete")
        return self


class AskResponse(_StrictModel):
    query_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    question: str = Field(min_length=1, max_length=4000)
    outcome: Literal["answered", "insufficient_evidence"]
    answer: str | None = Field(default=None, max_length=64 * 1024)
    model_reported_insufficient_evidence: bool
    failure_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,63}$")
    model_identifier: str | None = Field(default=None, min_length=1, max_length=128)
    index_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    document_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_build_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    selected_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection_revision: int = Field(ge=1, le=2_147_483_647)
    text_evidence: list[AskTextEvidenceResponse] = Field(max_length=32)
    image_evidence: list[AskImageEvidenceResponse] = Field(max_length=16)
    referenced_citations: list[str] = Field(max_length=48)
    warnings: list[str] = Field(max_length=32)

    @field_validator("referenced_citations")
    @classmethod
    def validate_citations(cls, values: list[str]) -> list[str]:
        if any(not re.fullmatch(r"(?:T|I)[1-9][0-9]{0,2}", value) for value in values) or len(values) != len(set(values)):
            raise ValueError("citations_invalid")
        return values

    @field_validator("warnings")
    @classmethod
    def validate_warnings(cls, values: list[str]) -> list[str]:
        if any(not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", value) for value in values):
            raise ValueError("warnings_invalid")
        return values

    @model_validator(mode="after")
    def validate_outcome(self):
        if self.outcome == "answered":
            if not self.answer or self.failure_code is not None or self.model_identifier is None:
                raise ValueError("answer_outcome_invalid")
        elif (self.answer is not None or self.failure_code != "no_usable_evidence" or
              self.model_identifier is not None or self.model_reported_insufficient_evidence or
              self.text_evidence or self.image_evidence or self.referenced_citations):
            raise ValueError("insufficient_evidence_outcome_invalid")
        labels = {item.label for item in self.text_evidence} | {item.label for item in self.image_evidence}
        if not set(self.referenced_citations).issubset(labels):
            raise ValueError("citation_target_invalid")
        return self


class IndexReadinessResponse(_StrictModel):
    status: Literal["report_match", "mismatch", "unavailable", "indeterminate"]
    reason_code: str
    workflow: Literal["evaluation", "ablation"]
    document_manifest_hash: str
    expected_index_fingerprint: str
    build_id: str | None
    report_status: str | None
    reported_text_records: int | None
    reported_image_records: int | None
    observed_text_records: int | None
    observed_image_records: int | None
    store_checked: Literal[False]
    check_level: Literal["report"]


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
