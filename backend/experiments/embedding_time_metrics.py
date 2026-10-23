"""Audited index-build embedding-time measurements."""

from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Real
from typing import Literal


EmbeddingTimeStatus = Literal["computed", "skipped", "failed", "unavailable"]


@dataclass(frozen=True)
class EmbeddingTimeResult:
    status: EmbeddingTimeStatus
    value: float | None = None
    reason_code: str | None = None


class EmbeddingTimeMetricAdapter:
    """Validate and sum persisted text/image index-build timings."""

    @staticmethod
    def score(text_seconds: object, image_seconds: object) -> EmbeddingTimeResult:
        if text_seconds is None or image_seconds is None:
            return EmbeddingTimeResult("unavailable", reason_code="embedding_time_unavailable")
        values = (text_seconds, image_seconds)
        if any(isinstance(value, bool) or not isinstance(value, Real) for value in values):
            return EmbeddingTimeResult("failed", reason_code="invalid_embedding_time")
        try:
            numeric = tuple(float(value) for value in values)
        except (TypeError, ValueError, OverflowError):
            return EmbeddingTimeResult("failed", reason_code="invalid_embedding_time")
        if any(not math.isfinite(value) or value < 0.0 for value in numeric):
            return EmbeddingTimeResult("failed", reason_code="invalid_embedding_time")
        total = numeric[0] + numeric[1]
        if not math.isfinite(total) or total < 0.0:
            return EmbeddingTimeResult("failed", reason_code="invalid_embedding_time")
        return EmbeddingTimeResult("computed", value=total)
