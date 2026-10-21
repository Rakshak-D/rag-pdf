"""Immutable, serializable plans for future local RAG experiments."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.config.fingerprint import (
    index_fingerprint,
    run_configuration_fingerprint,
)
from backend.config.schema import ResolvedRunConfig


RUN_PLAN_SCHEMA_VERSION = "1"
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class RunPlanIntegrityError(ValueError):
    """Raised when a serialized run plan no longer matches its contents."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class RunPlan(BaseModel):
    """Frozen plan containing only canonical scalar/JSON data."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_schema_version: str = RUN_PLAN_SCHEMA_VERSION
    document_manifest_hash: str
    resolved_config_json: str
    configuration_snapshot_hash: str
    index_fingerprint: str
    run_fingerprint: str

    @field_validator(
        "document_manifest_hash",
        "configuration_snapshot_hash",
        "index_fingerprint",
        "run_fingerprint",
    )
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("value must be exactly 64 hexadecimal characters")
        return value.lower()

    @field_validator("plan_schema_version")
    @classmethod
    def validate_plan_schema_version(cls, value: str) -> str:
        if value != RUN_PLAN_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported plan_schema_version {value!r}; "
                f"supported version is {RUN_PLAN_SCHEMA_VERSION!r}"
            )
        return value

    @field_validator("resolved_config_json")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must be non-empty")
        return value

    def resolved_config(self) -> ResolvedRunConfig:
        """Parse a new configuration object from the immutable JSON snapshot."""

        try:
            payload = json.loads(self.resolved_config_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise RunPlanIntegrityError("resolved configuration snapshot is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise RunPlanIntegrityError("resolved configuration snapshot must be a JSON object")
        try:
            return ResolvedRunConfig.model_validate(payload)
        except Exception as exc:
            raise RunPlanIntegrityError("resolved configuration snapshot failed validation") from exc

    def to_serializable_json(self) -> str:
        """Serialize the plan deterministically for storage or transport."""

        return _canonical_json(self.model_dump(mode="json"))

    @classmethod
    def from_serializable_json(cls, value: str) -> "RunPlan":
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RunPlanIntegrityError("serialized run plan is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise RunPlanIntegrityError("serialized run plan must be a JSON object")
        try:
            plan = cls.model_validate(payload)
        except Exception as exc:
            raise RunPlanIntegrityError("serialized run plan failed schema validation") from exc
        plan.verify_integrity()
        return plan

    def verify_integrity(self) -> None:
        """Recompute both fingerprints from the stored snapshot and compare."""

        if self.plan_schema_version != RUN_PLAN_SCHEMA_VERSION:
            raise RunPlanIntegrityError(
                f"unsupported plan_schema_version {self.plan_schema_version!r}; "
                f"supported version is {RUN_PLAN_SCHEMA_VERSION!r}"
            )
        actual_snapshot_hash = hashlib.sha256(self.resolved_config_json.encode("utf-8")).hexdigest()
        if actual_snapshot_hash != self.configuration_snapshot_hash:
            raise RunPlanIntegrityError(
                "configuration snapshot hash mismatch: "
                f"expected {self.configuration_snapshot_hash}, got {actual_snapshot_hash}"
            )
        config = self.resolved_config()
        try:
            config.validate_for_workbench()
        except Exception as exc:
            raise RunPlanIntegrityError("resolved configuration snapshot failed safety validation") from exc
        expected_index = index_fingerprint(config, self.document_manifest_hash)
        if expected_index != self.index_fingerprint:
            raise RunPlanIntegrityError(
                f"index fingerprint mismatch: expected {expected_index}, got {self.index_fingerprint}"
            )
        expected_run = run_configuration_fingerprint(config, expected_index)
        if expected_run != self.run_fingerprint:
            raise RunPlanIntegrityError(
                f"run fingerprint mismatch: expected {expected_run}, got {self.run_fingerprint}"
            )


def build_run_plan(
    config: ResolvedRunConfig,
    document_manifest_hash: str,
) -> RunPlan:
    """Validate safety and configuration, then build a deterministic plan."""

    if not isinstance(config, ResolvedRunConfig):
        raise TypeError("build_run_plan requires ResolvedRunConfig")

    # Re-parse before any fingerprinting to ensure a model_construct-created or
    # otherwise stale object cannot bypass the typed validation boundary.
    try:
        validated = ResolvedRunConfig.model_validate(config.model_dump(mode="python"))
    except Exception as exc:
        raise ValueError("resolved run configuration failed validation") from exc
    validated.validate_for_workbench()

    # index_fingerprint validates the manifest hash before producing output.
    index_value = index_fingerprint(validated, document_manifest_hash)
    run_value = run_configuration_fingerprint(validated, index_value)
    snapshot = _canonical_json(validated.model_dump(mode="json"))
    snapshot_hash = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
    return RunPlan(
        document_manifest_hash=document_manifest_hash,
        resolved_config_json=snapshot,
        configuration_snapshot_hash=snapshot_hash,
        index_fingerprint=index_value,
        run_fingerprint=run_value,
    )
