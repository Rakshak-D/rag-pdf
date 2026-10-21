"""Immutable evaluation observations and report contracts.

This module validates supplied metric results. It deliberately does not compute
metrics, aggregate values, run models, or persist reports.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from collections.abc import Sequence
from typing import Literal

from backend.experiments.tracing import QueryTrace, _HASH_RE


EVALUATION_SCHEMA_VERSION = "1"
_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_REFERENCE_KINDS = {"ground_truth", "context_pseudoreference", "retrieved_context", "no_reference", "not_applicable"}
_AGGREGATE_REFERENCE_KINDS = _REFERENCE_KINDS | {"mixed_reference"}
_DIRECTIONS = {"higher_is_better", "lower_is_better", "descriptive", "not_applicable"}


class EvaluationContractError(ValueError):
    """Raised when supplied evaluation observations or reports are inconsistent."""


def _id(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise EvaluationContractError(f"{label} is invalid")
    return value


def _trace_id(value: object) -> str:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise EvaluationContractError("trace_id must be a SHA-256 hexadecimal string")
    return value.lower()


def _model(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 160 or any(ord(char) < 32 for char in value):
        raise EvaluationContractError("model_identifier is invalid")
    return value


def _value(value: object, *, label: str, required: bool) -> float | None:
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise EvaluationContractError(f"{label} must be a finite numeric value or None")
    return float(value)


def _count(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EvaluationContractError(f"{label} must be a non-negative integer")
    return value


@dataclass(frozen=True)
class MetricObservation:
    trace_id: str
    metric_id: str
    metric_name: str
    status: Literal["computed", "skipped", "failed", "unavailable"]
    value: float | None
    unit: str
    optimization_direction: str
    reference_kind: str
    implementation_version: str
    model_identifier: str | None = None
    reason_code: str | None = None
    methodology_note: str | None = None

    def __post_init__(self) -> None:
        _trace_id(self.trace_id)
        _id(self.metric_id, label="metric_id")
        if not isinstance(self.metric_name, str) or not self.metric_name.strip() or len(self.metric_name) > 160:
            raise EvaluationContractError("metric_name must be non-empty and bounded")
        if self.status not in {"computed", "skipped", "failed", "unavailable"}:
            raise EvaluationContractError("metric observation status is invalid")
        computed = self.status == "computed"
        _value(self.value, label="metric value", required=computed)
        if not computed and self.value is not None:
            raise EvaluationContractError("non-computed observations must have value=None")
        if not isinstance(self.unit, str) or len(self.unit) > 80:
            raise EvaluationContractError("metric unit is invalid")
        if self.optimization_direction not in _DIRECTIONS:
            raise EvaluationContractError("optimization direction is invalid")
        if self.reference_kind not in _REFERENCE_KINDS:
            raise EvaluationContractError("reference kind is invalid")
        _id(self.implementation_version, label="implementation_version")
        if self.model_identifier is not None:
            _model(self.model_identifier)
        if self.status != "computed":
            if self.reason_code is None:
                raise EvaluationContractError("non-computed observations require a reason_code")
            _id(self.reason_code, label="reason_code")
        elif self.reason_code is not None:
            _id(self.reason_code, label="reason_code")


@dataclass(frozen=True)
class MetricAggregate:
    metric_id: str
    metric_name: str
    status: Literal["computed", "partial", "no_eligible_values"]
    value: float | None
    unit: str
    optimization_direction: str
    reference_kind: str
    implementation_version: str
    eligible_count: int
    skipped_count: int
    failed_count: int
    unavailable_count: int
    model_identifier: str | None = None
    methodology_note: str | None = None

    def __post_init__(self) -> None:
        _id(self.metric_id, label="aggregate metric_id")
        if not isinstance(self.metric_name, str) or not self.metric_name.strip():
            raise EvaluationContractError("aggregate metric_name must be non-empty")
        if self.status not in {"computed", "partial", "no_eligible_values"}:
            raise EvaluationContractError("aggregate status is invalid")
        eligible = _count(self.eligible_count, label="eligible_count")
        excluded = sum(_count(getattr(self, name), label=name) for name in ("skipped_count", "failed_count", "unavailable_count"))
        if self.status == "computed" and (self.value is None or excluded != 0 or eligible == 0):
            raise EvaluationContractError("computed aggregate requires eligible values and no excluded values")
        if self.status == "partial" and (self.value is None or eligible == 0 or excluded == 0):
            raise EvaluationContractError("partial aggregate requires eligible and excluded values")
        if self.status == "no_eligible_values" and (self.value is not None or eligible != 0 or excluded == 0):
            raise EvaluationContractError("no_eligible_values aggregate has invalid counts or value")
        _value(self.value, label="aggregate value", required=self.status != "no_eligible_values")
        if self.optimization_direction not in _DIRECTIONS or self.reference_kind not in _AGGREGATE_REFERENCE_KINDS:
            raise EvaluationContractError("aggregate provenance fields are invalid")
        _id(self.implementation_version, label="implementation_version")
        if self.model_identifier is not None:
            _model(self.model_identifier)


@dataclass(frozen=True)
class EvaluationReport:
    schema_version: str
    evaluation_fingerprint: str
    run_fingerprint: str
    benchmark_id: str
    benchmark_version: str
    trace_ids: tuple[str, ...]
    observations: tuple[MetricObservation, ...]
    aggregates: tuple[MetricAggregate, ...]


def _report_payload(report: EvaluationReport) -> dict:
    return {
        "schema": report.schema_version,
        "run": report.run_fingerprint,
        "benchmark": [report.benchmark_id, report.benchmark_version],
        "traces": report.trace_ids,
        "observations": [item.__dict__ for item in report.observations],
        "aggregates": [item.__dict__ for item in report.aggregates],
    }


def build_evaluation_report(
    traces: Sequence[QueryTrace],
    observations: Sequence[MetricObservation],
    aggregates: Sequence[MetricAggregate],
    *,
    run_fingerprint: str,
    benchmark_id: str,
    benchmark_version: str,
) -> EvaluationReport:
    """Validate and assemble a report from precomputed values only."""

    if not _HASH_RE.fullmatch(run_fingerprint):
        raise EvaluationContractError("run_fingerprint must be a SHA-256 hash")
    if not isinstance(benchmark_id, str) or not 1 <= len(benchmark_id) <= 120 or not benchmark_id.strip():
        raise EvaluationContractError("benchmark_id must be non-empty and bounded")
    if not isinstance(benchmark_version, str) or not 1 <= len(benchmark_version) <= 80 or not benchmark_version.strip():
        raise EvaluationContractError("benchmark_version must be non-empty and bounded")
    trace_tuple = tuple(traces)
    trace_ids = tuple(trace.trace_id for trace in trace_tuple)
    if any(not isinstance(trace, QueryTrace) for trace in trace_tuple) or len(set(trace_ids)) != len(trace_ids):
        raise EvaluationContractError("traces must be valid and have unique trace IDs")
    if any(trace.run_fingerprint != run_fingerprint for trace in trace_tuple):
        raise EvaluationContractError("every trace must belong to the report run")
    observation_tuple = tuple(observations)
    if any(not isinstance(item, MetricObservation) for item in observation_tuple):
        raise EvaluationContractError("observations must be MetricObservation values")
    trace_set = set(trace_ids)
    if any(item.trace_id not in trace_set for item in observation_tuple):
        raise EvaluationContractError("observation refers to a trace outside the report")
    observation_keys = [(item.trace_id, item.metric_id) for item in observation_tuple]
    if len(set(observation_keys)) != len(observation_keys):
        raise EvaluationContractError("duplicate trace/metric observations are not allowed")
    aggregate_tuple = tuple(aggregates)
    if any(not isinstance(item, MetricAggregate) for item in aggregate_tuple):
        raise EvaluationContractError("aggregates must be MetricAggregate values")
    aggregate_ids = [item.metric_id for item in aggregate_tuple]
    if len(set(aggregate_ids)) != len(aggregate_ids):
        raise EvaluationContractError("duplicate aggregate metric IDs are not allowed")
    for aggregate in aggregate_tuple:
        related = [item for item in observation_tuple if item.metric_id == aggregate.metric_id]
        if related:
            for field in ("metric_name", "unit", "optimization_direction", "implementation_version"):
                if any(getattr(item, field) != getattr(aggregate, field) for item in related):
                    raise EvaluationContractError(f"aggregate {aggregate.metric_id} metadata disagrees with observations")
            observation_reference_kinds = {item.reference_kind for item in related}
            expected_reference_kind = next(iter(observation_reference_kinds)) if len(observation_reference_kinds) == 1 else "mixed_reference"
            if aggregate.reference_kind != expected_reference_kind:
                raise EvaluationContractError(f"aggregate {aggregate.metric_id} reference kind disagrees with observations")
            model_values = {item.model_identifier for item in related if item.model_identifier is not None}
            if len(model_values) > 1:
                raise EvaluationContractError(f"aggregate {aggregate.metric_id} has conflicting model identifiers")
            computed_models = {item.model_identifier for item in related if item.status == "computed"}
            if aggregate.model_identifier is not None and any(value != aggregate.model_identifier for value in computed_models):
                raise EvaluationContractError(f"aggregate {aggregate.metric_id} model identifier disagrees with computed observations")
        counts = {
            "eligible_count": sum(item.status == "computed" for item in related),
            "skipped_count": sum(item.status == "skipped" for item in related),
            "failed_count": sum(item.status == "failed" for item in related),
            "unavailable_count": sum(item.status == "unavailable" for item in related),
        }
        for key, actual in counts.items():
            if getattr(aggregate, key) != actual:
                raise EvaluationContractError(f"aggregate {aggregate.metric_id} {key} disagrees with observations")
    report = EvaluationReport(EVALUATION_SCHEMA_VERSION, "0" * 64, run_fingerprint.lower(), benchmark_id, benchmark_version, trace_ids, observation_tuple, aggregate_tuple)
    fingerprint = hashlib.sha256(json.dumps(_report_payload(report), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    return EvaluationReport(report.schema_version, fingerprint, report.run_fingerprint, report.benchmark_id, report.benchmark_version, report.trace_ids, report.observations, report.aggregates)
