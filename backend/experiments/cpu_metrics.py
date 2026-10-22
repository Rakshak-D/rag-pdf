"""Offline, bounded process CPU sampling for the audited M18 metric."""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
import threading
from numbers import Real
from typing import Literal


CpuStatus = Literal["computed", "skipped", "failed", "unavailable"]
CPU_SAMPLE_INTERVAL = 0.05
MONITOR_JOIN_TIMEOUT = 0.5
_SAFE_REASONS = {
    "generation_not_reached",
    "cpu_monitor_unavailable",
    "cpu_samples_unavailable",
    "invalid_cpu_sample",
    "cpu_monitor_start_failed",
    "cpu_monitor_shutdown_failed",
    "cpu_monitor_sampling_failed",
    "cpu_monitor_failed",
}


@dataclass(frozen=True)
class CpuUsageResult:
    status: CpuStatus
    value: float | None = None
    reason_code: str | None = None


def _valid_normalized_sample(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    try:
        sample = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return sample if math.isfinite(sample) and 0.0 <= sample <= 100.0 else None


class ProcessCpuMonitor:
    """Sample the current process without collecting unrelated telemetry."""

    def __init__(self, *, process_factory=None, interval: float = CPU_SAMPLE_INTERVAL, join_timeout: float = MONITOR_JOIN_TIMEOUT, cpu_count=None) -> None:
        self._process_factory = process_factory
        self._interval = interval
        self._join_timeout = join_timeout
        self._cpu_count = cpu_count
        self._process = None
        self._stop_event = threading.Event()
        self._thread = None
        self._samples: list[float] = []
        self._startup_result: CpuUsageResult | None = None
        self._invalid_sample = False
        self._sampling_error = False

    def _logical_cpu_count(self) -> int:
        value = self._cpu_count() if callable(self._cpu_count) else (self._cpu_count if self._cpu_count is not None else os.cpu_count())
        return max(value or 1, 1) if isinstance(value, int) and not isinstance(value, bool) else 1

    def start(self) -> None:
        try:
            if self._process_factory is None:
                try:
                    import psutil
                except ImportError:
                    self._startup_result = CpuUsageResult("unavailable", reason_code="cpu_monitor_unavailable")
                    return
                self._process = psutil.Process(os.getpid())
            else:
                self._process = self._process_factory()
            if self._process is None or not callable(getattr(self._process, "cpu_percent", None)):
                self._startup_result = CpuUsageResult("failed", reason_code="cpu_monitor_start_failed")
                return
            self._process.cpu_percent(interval=None)
            denominator = self._logical_cpu_count()

            def sample() -> None:
                while not self._stop_event.wait(self._interval):
                    try:
                        raw = self._process.cpu_percent(interval=None)
                        if isinstance(raw, bool) or not isinstance(raw, Real):
                            self._invalid_sample = True
                            continue
                        valid = _valid_normalized_sample(float(raw) / denominator)
                        if valid is None:
                            self._invalid_sample = True
                        else:
                            self._samples.append(valid)
                    except Exception:
                        self._sampling_error = True
                        continue

            self._thread = threading.Thread(target=sample, daemon=True)
            self._thread.start()
        except Exception:
            self._startup_result = CpuUsageResult("failed", reason_code="cpu_monitor_start_failed")

    def stop(self) -> CpuUsageResult:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._join_timeout)
            if self._thread.is_alive():
                return CpuUsageResult("failed", reason_code="cpu_monitor_shutdown_failed")
        if self._startup_result is not None:
            return self._startup_result
        if self._invalid_sample:
            return CpuUsageResult("failed", reason_code="invalid_cpu_sample")
        if not self._samples:
            if self._sampling_error:
                return CpuUsageResult("failed", reason_code="cpu_monitor_sampling_failed")
            return CpuUsageResult("unavailable", reason_code="cpu_samples_unavailable")
        average = sum(self._samples) / len(self._samples)
        if not math.isfinite(average) or not 0.0 <= average <= 100.0:
            return CpuUsageResult("failed", reason_code="invalid_cpu_sample")
        return CpuUsageResult("computed", average)


class CpuUsageMetricAdapter:
    """Validate monitor output for one attempted generation invocation."""

    @staticmethod
    def result(measurement: object | None, *, attempted: bool) -> CpuUsageResult:
        if not attempted:
            return CpuUsageResult("skipped", reason_code="generation_not_reached")
        if not isinstance(measurement, CpuUsageResult):
            return CpuUsageResult("failed", reason_code="cpu_monitor_failed")
        if measurement.status == "computed":
            value = _valid_normalized_sample(measurement.value)
            return CpuUsageResult("computed", value) if value is not None else CpuUsageResult("failed", reason_code="invalid_cpu_sample")
        if measurement.status in {"skipped", "failed", "unavailable"}:
            fallback = "generation_not_reached" if measurement.status == "skipped" else "cpu_samples_unavailable" if measurement.status == "unavailable" else "cpu_monitor_failed"
            reason = measurement.reason_code if measurement.reason_code in _SAFE_REASONS else fallback
            return CpuUsageResult(measurement.status, reason_code=reason)
        return CpuUsageResult("failed", reason_code="cpu_monitor_failed")

    @staticmethod
    def from_samples(samples: object, *, cpu_count: object = None) -> CpuUsageResult:
        if not isinstance(samples, (list, tuple)) or not samples:
            return CpuUsageResult("unavailable", reason_code="cpu_samples_unavailable")
        denominator = cpu_count if isinstance(cpu_count, int) and not isinstance(cpu_count, bool) and cpu_count > 0 else max(os.cpu_count() or 1, 1)
        normalized: list[float] = []
        for raw in samples:
            if isinstance(raw, bool) or not isinstance(raw, Real):
                return CpuUsageResult("failed", reason_code="invalid_cpu_sample")
            try:
                value = float(raw) / denominator
            except (TypeError, ValueError, OverflowError, ZeroDivisionError):
                return CpuUsageResult("failed", reason_code="invalid_cpu_sample")
            valid = _valid_normalized_sample(value)
            if valid is None:
                return CpuUsageResult("failed", reason_code="invalid_cpu_sample")
            normalized.append(valid)
        average = sum(normalized) / len(normalized)
        return CpuUsageResult("computed", average) if math.isfinite(average) else CpuUsageResult("failed", reason_code="invalid_cpu_sample")
