import math

import pytest

from backend.experiments.throughput_metrics import ThroughputMetricAdapter


def test_positive_duration_returns_reciprocal():
    result = ThroughputMetricAdapter.score(2.0)
    assert result.status == "computed"
    assert result.value == 0.5
    assert math.isfinite(result.value)


@pytest.mark.parametrize("duration", [0.0, -1.0, True, "2", float("nan"), float("inf"), float("-inf")])
def test_invalid_or_nonpositive_duration_has_explicit_status(duration):
    result = ThroughputMetricAdapter.score(duration)
    if duration == 0.0:
        assert result.status == "skipped"
        assert result.reason_code == "nonpositive_elapsed_time"
    else:
        assert result.status == "failed"
        assert result.value is None


def test_generation_not_reached_is_skipped_without_a_timing():
    result = ThroughputMetricAdapter.score(None, attempted=False)
    assert result.status == "skipped"
    assert result.reason_code == "generation_not_reached"


def test_timing_collection_failure_is_failed_and_has_no_model_identifier():
    result = ThroughputMetricAdapter.score(2.0, timing_error="clock_failed")
    assert result.status == "failed"
    assert result.value is None
    assert not hasattr(result, "model_identifier")


def test_overflowing_reciprocal_is_failed():
    result = ThroughputMetricAdapter.score(1e-309)
    assert result.status == "failed"
    assert result.reason_code == "invalid_throughput"
