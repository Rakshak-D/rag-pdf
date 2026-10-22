"""Audited, per-invocation generation throughput calculations."""

from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Real
from typing import Literal


ThroughputStatus = Literal["computed", "skipped", "failed", "unavailable"]


@dataclass(frozen=True)
class ThroughputResult:
    """One case's reciprocal-duration contribution to M17."""

    status: ThroughputStatus
    value: float | None = None
    reason_code: str | None = None


class ThroughputMetricAdapter:
    """Validate a measured generation invocation and calculate ``1 / t``."""

    @staticmethod
    def score(
        elapsed_seconds: object | None,
        *,
        attempted: bool = True,
        timing_error: str | None = None,
    ) -> ThroughputResult:
        if not attempted:
            return ThroughputResult("skipped", reason_code="generation_not_reached")
        if timing_error is not None:
            return ThroughputResult("failed", reason_code="generation_timing_failed")
        if isinstance(elapsed_seconds, bool) or not isinstance(elapsed_seconds, Real):
            return ThroughputResult("failed", reason_code="invalid_generation_timing")
        try:
            elapsed = float(elapsed_seconds)
        except (TypeError, ValueError, OverflowError):
            return ThroughputResult("failed", reason_code="invalid_generation_timing")
        if not math.isfinite(elapsed):
            return ThroughputResult("failed", reason_code="invalid_generation_timing")
        if elapsed == 0.0:
            return ThroughputResult("skipped", reason_code="nonpositive_elapsed_time")
        if elapsed < 0.0:
            return ThroughputResult("failed", reason_code="invalid_generation_timing")
        try:
            contribution = 1.0 / elapsed
        except (ArithmeticError, OverflowError):
            return ThroughputResult("failed", reason_code="invalid_throughput")
        if not math.isfinite(contribution) or contribution <= 0.0:
            return ThroughputResult("failed", reason_code="invalid_throughput")
        return ThroughputResult("computed", contribution)
