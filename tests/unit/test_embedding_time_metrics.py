from __future__ import annotations

import math

import pytest

from backend.experiments.embedding_time_metrics import EmbeddingTimeMetricAdapter


@pytest.mark.parametrize(
    ("text_seconds", "image_seconds", "expected"),
    [(1, 2.5, 3.5), (0.0, 0.0, 0.0), (3, 0.25, 3.25)],
)
def test_embedding_time_sums_valid_persisted_durations(text_seconds, image_seconds, expected):
    result = EmbeddingTimeMetricAdapter.score(text_seconds, image_seconds)
    assert result.status == "computed"
    assert result.value == expected
    assert result.reason_code is None


@pytest.mark.parametrize("value", [None, True, "1.0", -1.0, float("nan"), float("inf"), -float("inf")])
def test_embedding_time_rejects_missing_or_invalid_duration(value):
    result = EmbeddingTimeMetricAdapter.score(value, 1.0)
    if value is None:
        assert result.status == "unavailable"
        assert result.reason_code == "embedding_time_unavailable"
    else:
        assert result.status == "failed"
        assert result.reason_code == "invalid_embedding_time"
    assert result.value is None


def test_missing_image_timing_is_not_treated_as_zero():
    result = EmbeddingTimeMetricAdapter.score(2.0, None)
    assert result.status == "unavailable"
    assert result.value is None


def test_adapter_does_not_expose_input_or_exception_details():
    result = EmbeddingTimeMetricAdapter.score(object(), 1.0)
    assert result.status == "failed"
    assert result.value is None
    assert result.reason_code == "invalid_embedding_time"
    assert "object" not in repr(result)


def test_overflowing_sum_is_invalid():
    result = EmbeddingTimeMetricAdapter.score(float("1e308"), float("1e308"))
    assert result.status == "failed"
    assert result.reason_code == "invalid_embedding_time"
    assert result.value is None
