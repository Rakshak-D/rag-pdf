"""Offline, bounded process RSS sampling for the audited M19 metric."""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
import threading
from numbers import Real
from typing import Literal


RamStatus = Literal["computed", "skipped", "failed", "unavailable"]
RAM_SAMPLE_INTERVAL = 0.05
RAM_JOIN_TIMEOUT = 0.5
RAM_BYTES_PER_GB = 1024 ** 3
_SAFE_REASONS = {
    "generation_not_reached",
    "ram_monitor_unavailable",
    "ram_samples_unavailable",
    "invalid_rss_sample",
    "ram_monitor_start_failed",
    "ram_monitor_shutdown_failed",
    "ram_monitor_sampling_failed",
    "ram_monitor_failed",
}


@dataclass(frozen=True)
class RamUsageResult:
    status: RamStatus
    value: float | None = None
    reason_code: str | None = None


def _valid_rss_bytes(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    try:
        rss = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return rss if math.isfinite(rss) and rss >= 0.0 else None


def _valid_ram_gb(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return amount if math.isfinite(amount) and amount >= 0.0 else None


class ProcessRamMonitor:
    """Sample current-process RSS without collecting unrelated telemetry."""

    def __init__(self, *, process_factory=None, interval: float = RAM_SAMPLE_INTERVAL, join_timeout: float = RAM_JOIN_TIMEOUT) -> None:
        self._process_factory = process_factory
        self._interval = interval
        self._join_timeout = join_timeout
        self._process = None
        self._stop_event = threading.Event()
        self._thread = None
        self._samples: list[float] = []
        self._startup_result: RamUsageResult | None = None
        self._invalid_sample = False
        self._sampling_error = False

    def start(self) -> None:
        try:
            if self._process_factory is None:
                try:
                    import psutil
                except ImportError:
                    self._startup_result = RamUsageResult("unavailable", reason_code="ram_monitor_unavailable")
                    return
                self._process = psutil.Process(os.getpid())
            else:
                self._process = self._process_factory()
            if self._process is None or not callable(getattr(self._process, "memory_info", None)):
                self._startup_result = RamUsageResult("failed", reason_code="ram_monitor_start_failed")
                return

            def sample() -> None:
                while not self._stop_event.wait(self._interval):
                    try:
                        info = self._process.memory_info()
                        raw = getattr(info, "rss", None)
                        rss = _valid_rss_bytes(raw)
                        if rss is None:
                            self._invalid_sample = True
                            continue
                        value = rss / RAM_BYTES_PER_GB
                        if not math.isfinite(value) or value < 0.0:
                            self._invalid_sample = True
                        else:
                            self._samples.append(value)
                    except Exception:
                        self._sampling_error = True
                        continue

            self._thread = threading.Thread(target=sample, daemon=True)
            self._thread.start()
        except Exception:
            self._startup_result = RamUsageResult("failed", reason_code="ram_monitor_start_failed")

    def stop(self) -> RamUsageResult:
        self._stop_event.set()
        if self._thread is not None:
            try:
                self._thread.join(timeout=self._join_timeout)
                still_alive = self._thread.is_alive()
            except Exception:
                return RamUsageResult("failed", reason_code="ram_monitor_shutdown_failed")
            if still_alive:
                return RamUsageResult("failed", reason_code="ram_monitor_shutdown_failed")
        if self._startup_result is not None:
            return self._startup_result
        if self._invalid_sample:
            return RamUsageResult("failed", reason_code="invalid_rss_sample")
        if not self._samples:
            if self._sampling_error:
                return RamUsageResult("failed", reason_code="ram_monitor_sampling_failed")
            return RamUsageResult("unavailable", reason_code="ram_samples_unavailable")
        average = sum(self._samples) / len(self._samples)
        if not math.isfinite(average) or average < 0.0:
            return RamUsageResult("failed", reason_code="invalid_rss_sample")
        return RamUsageResult("computed", average)


class RamUsageMetricAdapter:
    """Validate one process-RSS monitor result for M19."""

    @staticmethod
    def result(measurement: object | None, *, attempted: bool) -> RamUsageResult:
        if not attempted:
            return RamUsageResult("skipped", reason_code="generation_not_reached")
        if not isinstance(measurement, RamUsageResult):
            return RamUsageResult("failed", reason_code="ram_monitor_failed")
        if measurement.status == "computed":
            value = _valid_ram_gb(measurement.value)
            return RamUsageResult("computed", value) if value is not None else RamUsageResult("failed", reason_code="invalid_rss_sample")
        if measurement.status in {"skipped", "failed", "unavailable"}:
            fallback = "generation_not_reached" if measurement.status == "skipped" else "ram_samples_unavailable" if measurement.status == "unavailable" else "ram_monitor_failed"
            reason = measurement.reason_code if measurement.reason_code in _SAFE_REASONS else fallback
            return RamUsageResult(measurement.status, reason_code=reason)
        return RamUsageResult("failed", reason_code="ram_monitor_failed")

    @staticmethod
    def from_samples(samples: object) -> RamUsageResult:
        if not isinstance(samples, (list, tuple)) or not samples:
            return RamUsageResult("unavailable", reason_code="ram_samples_unavailable")
        values: list[float] = []
        for raw in samples:
            rss = _valid_rss_bytes(raw)
            if rss is None:
                return RamUsageResult("failed", reason_code="invalid_rss_sample")
            values.append(rss / RAM_BYTES_PER_GB)
        average = sum(values) / len(values)
        return RamUsageResult("computed", average) if math.isfinite(average) and average >= 0.0 else RamUsageResult("failed", reason_code="invalid_rss_sample")
