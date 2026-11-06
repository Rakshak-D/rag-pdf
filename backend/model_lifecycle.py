"""Serialized, resource-admitted scopes for transient model use.

The default registry policy remains cached.  Transient mode is an explicit
local-runtime opt-in and releases only registry/build-owned adapter models
after one successful model operation.
"""

from __future__ import annotations

from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
import functools
import gc
import os
from pathlib import Path
import threading
from typing import Any, Callable, Iterator


_MIB = 1024 * 1024
_DEFAULT_RAM_RESERVE = 1024 * _MIB
_DEFAULT_COMMIT_RESERVE = 1024 * _MIB
_DEFAULT_CUDA_RESERVE = 512 * _MIB
_POLICIES = {"cached", "transient_serialized"}


class _PerformanceInformation(ctypes.Structure):
    """Windows PSAPI structure used to measure commit headroom."""

    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("CommitTotal", ctypes.c_size_t),
        ("CommitLimit", ctypes.c_size_t),
        ("CommitPeak", ctypes.c_size_t),
        ("PhysicalTotal", ctypes.c_size_t),
        ("PhysicalAvailable", ctypes.c_size_t),
        ("SystemCache", ctypes.c_size_t),
        ("KernelTotal", ctypes.c_size_t),
        ("KernelPaged", ctypes.c_size_t),
        ("KernelNonpaged", ctypes.c_size_t),
        ("PageSize", ctypes.c_size_t),
        ("HandleCount", ctypes.c_ulong),
        ("ProcessCount", ctypes.c_ulong),
        ("ThreadCount", ctypes.c_ulong),
    ]


class ModelLifecycleError(RuntimeError):
    """Stable resource/lifecycle error without host or loader details."""

    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(error_code)


def find_model_lifecycle_error(exc: BaseException) -> ModelLifecycleError | None:
    """Find a bounded stable lifecycle error through wrapped domain errors."""
    current: BaseException | None = exc
    seen: set[int] = set()
    for _ in range(16):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        if isinstance(current, ModelLifecycleError):
            return current
        current = current.__cause__ or current.__context__
    return None


def validate_model_lifecycle_policy(value: object) -> str:
    if not isinstance(value, str) or value not in _POLICIES:
        raise ModelLifecycleError("model_lifecycle_policy_invalid")
    return value


@dataclass(frozen=True)
class ResourceMeasurements:
    available_ram_bytes: int
    commit_headroom_bytes: int
    cuda_free_bytes: int | None


def _windows_commit_headroom() -> int:
    status = _PerformanceInformation()
    status.dwLength = ctypes.sizeof(status)
    psapi = ctypes.WinDLL("Psapi.dll")
    query = psapi.GetPerformanceInfo
    query.argtypes = [ctypes.POINTER(_PerformanceInformation), ctypes.c_ulong]
    query.restype = ctypes.c_int
    if not query(ctypes.byref(status), status.dwLength) or status.PageSize <= 0:
        raise OSError("memory status unavailable")
    return max(0, int(status.CommitLimit - status.CommitTotal) * int(status.PageSize))


def _proc_commit_headroom() -> int:
    values: dict[str, int] = {}
    with Path("/proc/meminfo").open("r", encoding="ascii") as stream:
        for line in stream:
            name, separator, raw = line.partition(":")
            if separator and name in {"CommitLimit", "Committed_AS"}:
                fields = raw.split()
                if fields and fields[0].isdigit():
                    values[name] = int(fields[0]) * 1024
    if set(values) != {"CommitLimit", "Committed_AS"}:
        raise OSError("commit data unavailable")
    return max(0, values["CommitLimit"] - values["Committed_AS"])


def read_resource_measurements(device: str) -> ResourceMeasurements:
    """Read only bounded host/process-device capacity fields."""
    if device not in {"cpu", "cuda"}:
        raise ModelLifecycleError("model_resource_measurement_unavailable")
    try:
        import psutil

        available_ram = int(psutil.virtual_memory().available)
        commit_headroom = _windows_commit_headroom() if os.name == "nt" else _proc_commit_headroom()
        cuda_free: int | None = None
        if device == "cuda":
            import torch

            if not torch.cuda.is_available():
                raise OSError("CUDA measurement unavailable")
            cuda_free = int(torch.cuda.mem_get_info()[0])
        if available_ram < 0 or commit_headroom < 0 or (cuda_free is not None and cuda_free < 0):
            raise OSError("invalid resource measurement")
        return ResourceMeasurements(available_ram, commit_headroom, cuda_free)
    except ModelLifecycleError:
        raise
    except Exception as exc:
        raise ModelLifecycleError("model_resource_measurement_unavailable") from exc


class ModelResourceAdmission:
    """Check generic post-admission reserve floors, not model-size estimates."""

    def __init__(
        self,
        *,
        observer: Callable[[str], ResourceMeasurements] = read_resource_measurements,
        min_available_ram_bytes: int = _DEFAULT_RAM_RESERVE,
        min_commit_headroom_bytes: int = _DEFAULT_COMMIT_RESERVE,
        min_cuda_free_bytes: int = _DEFAULT_CUDA_RESERVE,
    ) -> None:
        for value in (min_available_ram_bytes, min_commit_headroom_bytes, min_cuda_free_bytes):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ModelLifecycleError("model_resource_policy_invalid")
        if not callable(observer):
            raise ModelLifecycleError("model_resource_policy_invalid")
        self._observer = observer
        self.min_available_ram_bytes = min_available_ram_bytes
        self.min_commit_headroom_bytes = min_commit_headroom_bytes
        self.min_cuda_free_bytes = min_cuda_free_bytes

    def check(self, device: str) -> ResourceMeasurements:
        try:
            values = self._observer(device)
        except ModelLifecycleError:
            raise
        except Exception as exc:
            raise ModelLifecycleError("model_resource_measurement_unavailable") from exc
        if (not isinstance(values, ResourceMeasurements)
                or isinstance(values.available_ram_bytes, bool)
                or not isinstance(values.available_ram_bytes, int)
                or isinstance(values.commit_headroom_bytes, bool)
                or not isinstance(values.commit_headroom_bytes, int)
                or values.available_ram_bytes < 0 or values.commit_headroom_bytes < 0
                or (device == "cuda" and (isinstance(values.cuda_free_bytes, bool)
                    or not isinstance(values.cuda_free_bytes, int) or values.cuda_free_bytes < 0))):
            raise ModelLifecycleError("model_resource_measurement_unavailable")
        if (values.available_ram_bytes < self.min_available_ram_bytes
                or values.commit_headroom_bytes < self.min_commit_headroom_bytes
                or (device == "cuda" and values.cuda_free_bytes < self.min_cuda_free_bytes)):
            raise ModelLifecycleError("model_resource_capacity_insufficient")
        return values


class ModelLifecycleCoordinator:
    """One bounded model-use lease shared by a runtime's model adapters."""

    def __init__(
        self,
        admission: ModelResourceAdmission | None = None,
        *,
        acquire_timeout_seconds: float = 30.0,
    ) -> None:
        if (isinstance(acquire_timeout_seconds, bool)
                or not isinstance(acquire_timeout_seconds, (int, float))
                or not 0 < float(acquire_timeout_seconds) <= 300):
            raise ModelLifecycleError("model_resource_policy_invalid")
        if admission is not None and not isinstance(admission, ModelResourceAdmission):
            raise ModelLifecycleError("model_resource_policy_invalid")
        self._admission = admission or ModelResourceAdmission()
        self._gate = threading.Lock()
        self._timeout = float(acquire_timeout_seconds)
        self._local = threading.local()
        self._state_lock = threading.Lock()
        self._active_uses = 0
        self._residency_state = "clear"

    @property
    def active_uses(self) -> int:
        with self._state_lock:
            return self._active_uses

    @property
    def residency_state(self) -> str:
        with self._state_lock:
            return self._residency_state

    def is_active_for(self, adapter: object) -> bool:
        active = getattr(self._local, "active", {})
        return id(adapter) in active

    def note_model_loaded(self, adapter: object) -> None:
        if not self.is_active_for(adapter):
            raise ModelLifecycleError("model_scoped_use_required")
        loaded = getattr(self._local, "loaded", None)
        if loaded is None:
            loaded = set()
            self._local.loaded = loaded
        loaded.add(id(adapter))

    def note_load_uncertain(self, adapter: object) -> None:
        if not self.is_active_for(adapter):
            raise ModelLifecycleError("model_scoped_use_required")
        uncertain = getattr(self._local, "uncertain", None)
        if uncertain is None:
            uncertain = set()
            self._local.uncertain = uncertain
        uncertain.add(id(adapter))

    @staticmethod
    def _mark_adapter_error(adapter: object, code: str) -> None:
        marker = getattr(adapter, "_mark_lifecycle_error", None)
        if callable(marker):
            marker(code)

    @contextmanager
    def operation(
        self,
        adapter: object,
        *,
        device_resolver: Callable[[], str],
        release: Callable[[], None],
    ) -> Iterator[None]:
        active = getattr(self._local, "active", None)
        if active is None:
            active = {}
            self._local.active = active
        loaded = getattr(self._local, "loaded", None)
        if loaded is None:
            loaded = set()
            self._local.loaded = loaded
        uncertain = getattr(self._local, "uncertain", None)
        if uncertain is None:
            uncertain = set()
            self._local.uncertain = uncertain
        adapter_key = id(adapter)
        if adapter_key in active:
            active[adapter_key] += 1
            try:
                yield
            finally:
                active[adapter_key] -= 1
            return
        if not self._gate.acquire(timeout=self._timeout):
            raise ModelLifecycleError("model_resource_busy")
        body_error: BaseException | None = None
        try:
            with self._state_lock:
                if self._residency_state != "clear":
                    raise ModelLifecycleError("model_resource_state_unknown")
            try:
                device = device_resolver()
                self._admission.check(device)
            except ModelLifecycleError:
                raise
            except Exception as exc:
                raise ModelLifecycleError("model_resource_measurement_unavailable") from exc
            active[adapter_key] = 1
            loaded.discard(adapter_key)
            uncertain.discard(adapter_key)
            with self._state_lock:
                self._active_uses = 1
                self._residency_state = "in_use"
            try:
                yield
            except BaseException as exc:
                body_error = exc
            was_loaded = adapter_key in loaded
            load_was_uncertain = adapter_key in uncertain
            try:
                release()
            except BaseException as exc:
                with self._state_lock:
                    self._residency_state = "unknown"
                self._mark_adapter_error(adapter, "model_release_failed")
                raise ModelLifecycleError("model_release_failed") from exc
            if body_error is not None:
                if was_loaded or load_was_uncertain:
                    # An exception traceback may retain operation locals. The
                    # adapter reference is cleared, but recovery is unverified.
                    with self._state_lock:
                        self._residency_state = "unknown"
                    self._mark_adapter_error(adapter, "model_release_unverified")
                    raise ModelLifecycleError("model_release_unverified") from body_error
                with self._state_lock:
                    self._residency_state = "clear"
                raise body_error
            with self._state_lock:
                self._residency_state = "clear"
        finally:
            active.pop(adapter_key, None)
            loaded.discard(adapter_key)
            uncertain.discard(adapter_key)
            with self._state_lock:
                self._active_uses = 0
            self._gate.release()


def lifecycle_scoped(component: str):
    """Wrap a whole adapter operation so its frame is gone before release."""
    if component not in {"text", "image", "reranker"}:
        raise ValueError("model component is invalid")

    def decorate(function):
        @functools.wraps(function)
        def wrapped(self, *args, **kwargs):
            lifecycle = getattr(self, "_lifecycle", None)
            if lifecycle is None:
                return function(self, *args, **kwargs)
            with lifecycle.operation(
                self,
                device_resolver=self._resource_device,
                release=self._release_loaded_model,
            ):
                return function(self, *args, **kwargs)
        return wrapped
    return decorate


__all__ = [
    "ModelLifecycleCoordinator",
    "ModelLifecycleError",
    "ModelResourceAdmission",
    "ResourceMeasurements",
    "find_model_lifecycle_error",
    "lifecycle_scoped",
    "read_resource_measurements",
    "validate_model_lifecycle_policy",
]
