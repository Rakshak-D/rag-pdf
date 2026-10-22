import math

import pytest

from backend.experiments.cpu_metrics import CPU_SAMPLE_INTERVAL, CpuUsageMetricAdapter, CpuUsageResult, ProcessCpuMonitor


def test_samples_are_normalized_and_averaged():
    result = CpuUsageMetricAdapter.from_samples([20.0, 40.0], cpu_count=4)
    assert result.status == "computed"
    assert result.value == 7.5


def test_zero_cpu_is_a_computed_value():
    result = CpuUsageMetricAdapter.from_samples([0.0, 0.0], cpu_count=2)
    assert result.status == "computed" and result.value == 0.0


def test_empty_samples_are_unavailable():
    result = CpuUsageMetricAdapter.from_samples([])
    assert result.status == "unavailable" and result.value is None


@pytest.mark.parametrize("samples", [[True], [float("nan")], [float("inf")], [-1.0], [401.0], ["20"]])
def test_invalid_samples_fail_without_coercion(samples):
    result = CpuUsageMetricAdapter.from_samples(samples, cpu_count=4)
    assert result.status == "failed" and result.value is None


def test_invalid_sample_is_not_hidden_by_valid_samples():
    result = CpuUsageMetricAdapter.from_samples([20.0, float("nan")], cpu_count=2)
    assert result.status == "failed"
    assert result.reason_code == "invalid_cpu_sample"


def test_unreached_generation_is_skipped():
    result = CpuUsageMetricAdapter.result(None, attempted=False)
    assert result.status == "skipped" and result.reason_code == "generation_not_reached"


def test_monitor_primes_process_and_uses_legacy_interval():
    class Process:
        def __init__(self):
            self.calls = []
        def cpu_percent(self, interval=None):
            self.calls.append(interval)
            return 20.0

    process = Process()
    monitor = ProcessCpuMonitor(process_factory=lambda: process, interval=CPU_SAMPLE_INTERVAL, cpu_count=2)
    monitor.start()
    monitor._stop_event.set()
    result = monitor.stop()
    assert process.calls[0] is None
    assert monitor._interval == 0.05
    assert result.status in {"unavailable", "computed"}


def test_monitor_shutdown_is_bounded_and_stable():
    class StuckThread:
        def is_alive(self):
            return True
        def join(self, timeout):
            assert timeout == 0.01

    monitor = ProcessCpuMonitor(join_timeout=0.01)
    monitor._thread = StuckThread()
    result = monitor.stop()
    assert result == CpuUsageResult("failed", reason_code="cpu_monitor_shutdown_failed")
