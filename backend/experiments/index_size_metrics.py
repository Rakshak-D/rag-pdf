"""Audited persisted-index vector-count measurements."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


IndexSizeStatus = Literal["computed", "skipped", "failed", "unavailable"]


@dataclass(frozen=True)
class IndexSizeResult:
    status: IndexSizeStatus
    value: int | None = None
    reason_code: str | None = None


class IndexSizeMetricAdapter:
    """Validate and sum persisted text and image record counts."""

    @staticmethod
    def score(text_records: object, image_records: object) -> IndexSizeResult:
        if text_records is None or image_records is None:
            return IndexSizeResult("unavailable", reason_code="index_size_unavailable")
        values = (text_records, image_records)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
            return IndexSizeResult("failed", reason_code="invalid_index_size")
        if any(value < 0 for value in values):
            return IndexSizeResult("failed", reason_code="invalid_index_size")
        return IndexSizeResult("computed", value=text_records + image_records)
