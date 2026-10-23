from __future__ import annotations

import pytest

from backend.experiments.index_size_metrics import IndexSizeMetricAdapter


@pytest.mark.parametrize(
    ("text_records", "image_records", "expected"),
    [(4, 6, 10), (0, 5, 5), (7, 0, 7), (0, 0, 0)],
)
def test_index_size_sums_explicit_persisted_counts(text_records, image_records, expected):
    result = IndexSizeMetricAdapter.score(text_records, image_records)
    assert result.status == "computed"
    assert result.value == expected
    assert result.reason_code is None


@pytest.mark.parametrize("value", [None, -1, True, False, 1.0, "1", object()])
def test_index_size_rejects_missing_or_invalid_counts(value):
    result = IndexSizeMetricAdapter.score(value, 2)
    if value is None:
        assert result.status == "unavailable"
        assert result.reason_code == "index_size_unavailable"
    else:
        assert result.status == "failed"
        assert result.reason_code == "invalid_index_size"
    assert result.value is None


def test_missing_image_count_is_not_treated_as_zero():
    result = IndexSizeMetricAdapter.score(4, None)
    assert result.status == "unavailable"
    assert result.value is None


def test_invalid_count_result_is_privacy_safe():
    result = IndexSizeMetricAdapter.score(object(), 2)
    assert result.status == "failed"
    assert result.value is None
    assert result.reason_code == "invalid_index_size"
    assert "object" not in repr(result)
