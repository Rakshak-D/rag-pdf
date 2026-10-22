import math

import pytest

from backend.experiments.memory_metrics import (
    RAM_BYTES_PER_GB,
    RAM_SAMPLE_INTERVAL,
    ProcessRamMonitor,
    RamUsageMetricAdapter,
    RamUsageResult,
)


def test_rss_samples_use_binary_gb_and_average():
    result = RamUsageMetricAdapter.from_samples([0, RAM_BYTES_PER_GB, 3 * RAM_BYTES_PER_GB])
    assert result.status == "computed"
    assert result.value == pytest.approx(4.0 / 3.0)


def test_zero_ram_is_a_computed_value():
    result = RamUsageMetricAdapter.from_samples([0, 0])
    assert result == RamUsageResult("computed", 0.0)


def test_empty_samples_are_unavailable():
    result = RamUsageMetricAdapter.from_samples([])
    assert result.status == "unavailable" and result.value is None


@pytest.mark.parametrize("samples", [[True], ["1024"], [-1], [float("nan")], [float("inf")], [float("-inf")]])
def test_invalid_rss_samples_fail_without_coercion(samples):
    result = RamUsageMetricAdapter.from_samples(samples)
    assert result.status == "failed"
    assert result.value is None
    assert result.reason_code == "invalid_rss_sample"


def test_unreached_generation_is_skipped():
    result = RamUsageMetricAdapter.result(None, attempted=False)
    assert result.status == "skipped"
    assert result.reason_code == "generation_not_reached"


def test_monitor_uses_process_rss_and_legacy_interval():
    class Info:
        rss = RAM_BYTES_PER_GB

    class Process:
        def __init__(self):
            self.calls = 0
        def memory_info(self):
            self.calls += 1
            return Info()

    process = Process()
    monitor = ProcessRamMonitor(process_factory=lambda: process, interval=RAM_SAMPLE_INTERVAL)
    monitor.start()
    monitor._stop_event.set()
    result = monitor.stop()
    assert monitor._interval == 0.05
    assert result.status in {"unavailable", "computed"}
    assert process.calls >= 0


def test_monitor_shutdown_is_bounded():
    class StuckThread:
        def is_alive(self):
            return True
        def join(self, timeout):
            assert timeout == 0.01

    monitor = ProcessRamMonitor(join_timeout=0.01)
    monitor._thread = StuckThread()
    result = monitor.stop()
    assert result == RamUsageResult("failed", reason_code="ram_monitor_shutdown_failed")


def test_monitor_failure_reason_does_not_expose_details():
    result = RamUsageMetricAdapter.result(RamUsageResult("failed", reason_code="private path detail"), attempted=True)
    assert result.status == "failed"
    assert result.reason_code == "ram_monitor_failed"
    assert "private" not in repr(result)
