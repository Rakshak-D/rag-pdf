from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os
from threading import Barrier, Event, Lock, Thread
import time
from types import SimpleNamespace

import numpy as np
import pytest

import backend.model_lifecycle as model_lifecycle
from backend.embeddings.text import TextEmbeddingAdapter, TextEmbeddingLoadError
from backend.engine.reranking import CrossEncoderReranker
from backend.engine.index_builder import IndexBuildError, build_index
from backend.model_lifecycle import (
    ModelLifecycleError,
    ModelResourceAdmission,
    ResourceMeasurements,
)
from backend.services.model_registry import ModelAdapterRegistry, ModelRuntimeError
from notebooks.config import Config
from backend.config import ResolvedRunConfig


@pytest.fixture(autouse=True)
def _cpu_resource_device(monkeypatch):
    monkeypatch.setattr("backend.embeddings.text._default_device", lambda: "cpu")


def _config(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    return ResolvedRunConfig.from_legacy(Config(
        pdf_dir=str(source),
        images_dir=str(tmp_path / "images"),
        database_dir=str(tmp_path / "database"),
        results_dir=str(tmp_path / "results"),
        model_comparison_results_dir=str(tmp_path / "comparison"),
        retrieval_results_dir=str(tmp_path / "retrieval"),
        text_embed_model="test/text-model",
        reset_collections_on_start=False,
    ))


@dataclass(frozen=True)
class _Document:
    page_content: str


class _Model:
    def __init__(self, on_encode=None):
        self.on_encode = on_encode

    def encode(self, **kwargs):
        if self.on_encode is not None:
            self.on_encode()
        value = kwargs["sentences"]
        if isinstance(value, list):
            return np.tile(np.asarray([[1.0, 2.0, 3.0]], dtype=np.float32), (len(value), 1))
        return np.asarray([1.0, 2.0, 3.0], dtype=np.float32)


def _admission(*, ram=8, commit=8, cuda=8, observer=None):
    mib = 1024 * 1024
    if observer is None:
        observer = lambda _device: ResourceMeasurements(ram * mib, commit * mib, cuda * mib)
    return ModelResourceAdmission(
        observer=observer,
        min_available_ram_bytes=2 * mib,
        min_commit_headroom_bytes=2 * mib,
        min_cuda_free_bytes=1 * mib,
    )


def _registry(tmp_path, loader, admission=None):
    return ModelAdapterRegistry(
        text_factory=lambda config: TextEmbeddingAdapter(config, model_loader=loader),
        lifecycle_policy="transient_serialized",
        resource_admission=admission or _admission(),
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows PSAPI structure contract")
def test_windows_commit_headroom_uses_pointer_sized_page_size(monkeypatch):
    assert dict(model_lifecycle._PerformanceInformation._fields_)["PageSize"] is ctypes.c_size_t

    class FakeFunction:
        def __call__(self, pointer, length):
            assert length == ctypes.sizeof(model_lifecycle._PerformanceInformation)
            info = ctypes.cast(
                pointer, ctypes.POINTER(model_lifecycle._PerformanceInformation)
            ).contents
            info.CommitLimit = 100
            info.CommitTotal = 25
            info.PageSize = 4096
            return 1

    fake_function = FakeFunction()
    monkeypatch.setattr(
        model_lifecycle.ctypes,
        "WinDLL",
        lambda name: SimpleNamespace(GetPerformanceInfo=fake_function),
    )
    assert model_lifecycle._windows_commit_headroom() == 75 * 4096


def test_transient_registry_starts_unloaded_and_releases_after_each_operation(tmp_path):
    config = _config(tmp_path)
    loads = []

    def loader(name, device):
        loads.append((name, device))
        return _Model()

    registry = _registry(tmp_path, loader)
    adapter = registry.get_text_adapter(config)
    assert adapter.runtime_state == "unloaded"
    with pytest.raises(ModelLifecycleError, match="model_scoped_use_required"):
        adapter.load()

    first = adapter.embed_documents([_Document("same input")]).embeddings.copy()
    assert adapter.runtime_state == "unloaded"
    assert adapter._model is None
    second = adapter.embed_documents([_Document("same input")]).embeddings.copy()
    assert adapter.runtime_state == "unloaded"
    assert len(loads) == 2
    assert loads[0][0] == loads[1][0] == "test/text-model"
    assert first.shape == second.shape == (1, 3)
    np.testing.assert_array_equal(first, second)
    assert registry.snapshot().components[0].state == "unloaded"
    assert registry._lifecycle.active_uses == 0
    assert registry._lifecycle.residency_state == "clear"


def test_two_active_consumers_are_serialized_and_model_is_not_released_mid_inference(tmp_path):
    config = _config(tmp_path)
    entered = Event()
    continue_inference = Event()
    tracker_lock = Lock()
    active = 0
    peak = 0
    adapter_holder = {}

    def infer():
        nonlocal active, peak
        with tracker_lock:
            active += 1
            peak = max(peak, active)
        entered.set()
        assert continue_inference.wait(3)
        adapter = adapter_holder["adapter"]
        assert adapter._model is not None
        assert adapter.runtime_state == "ready"
        assert adapter_holder["registry"]._lifecycle.active_uses == 1
        with tracker_lock:
            active -= 1

    registry = _registry(tmp_path, lambda *_: _Model(infer))
    adapter = registry.get_text_adapter(config)
    adapter_holder.update(adapter=adapter, registry=registry)
    errors = []

    def consume():
        try:
            adapter.embed_query("query")
        except BaseException as exc:
            errors.append(exc)

    first = Thread(target=consume)
    second = Thread(target=consume)
    first.start()
    assert entered.wait(3)
    second.start()
    time.sleep(0.05)
    assert registry._lifecycle.active_uses == 1
    assert adapter._model is not None
    continue_inference.set()
    first.join(3)
    second.join(3)
    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert peak == 1
    assert adapter._model is None
    assert registry._lifecycle.active_uses == 0


def test_ask_and_evaluation_adapters_share_one_model_residency_gate(tmp_path):
    config = _config(tmp_path)
    tracker_lock = Lock()
    active = 0
    peak = 0

    def enter_model_call():
        nonlocal active, peak
        with tracker_lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.08)
        with tracker_lock:
            active -= 1

    class CrossEncoder:
        device = "cpu"

        def predict(self, pairs):
            enter_model_call()
            return np.ones(len(pairs), dtype=np.float32)

    registry = ModelAdapterRegistry(
        text_factory=lambda value: TextEmbeddingAdapter(value, model_loader=lambda *_: _Model(enter_model_call)),
        reranker_factory=lambda value: CrossEncoderReranker(value, loader=lambda _name: CrossEncoder()),
        lifecycle_policy="transient_serialized",
        resource_admission=_admission(),
    )
    text = registry.get_text_adapter(config)
    reranker = registry.get_reranker(config)
    barrier = Barrier(3)
    errors = []

    def text_call():
        try:
            barrier.wait()
            text.embed_query("ask query")
        except BaseException as exc:
            errors.append(exc)

    def reranker_call():
        try:
            barrier.wait()
            reranker.score_pairs("evaluation query", ["candidate"])
        except BaseException as exc:
            errors.append(exc)

    workers = [Thread(target=text_call), Thread(target=reranker_call)]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(3)
    assert all(not worker.is_alive() for worker in workers)
    assert errors == []
    assert peak == 1
    assert text.runtime_state == reranker.runtime_state == "unloaded"
    assert registry._lifecycle.active_uses == 0


@pytest.mark.parametrize(
    "measurement,expected",
    [
        (ResourceMeasurements(1, 8, 8), "model_resource_capacity_insufficient"),
        (None, "model_resource_measurement_unavailable"),
    ],
)
def test_admission_fails_closed_before_model_load(tmp_path, measurement, expected):
    config = _config(tmp_path)
    loads = []
    admission = _admission(observer=lambda _device: measurement)
    registry = _registry(tmp_path, lambda *_: loads.append("load") or _Model(), admission)
    adapter = registry.get_text_adapter(config)
    with pytest.raises(ModelLifecycleError) as error:
        adapter.embed_query("query")
    assert error.value.error_code == expected
    assert loads == []
    assert adapter.runtime_state == "unloaded"
    assert registry._lifecycle.residency_state == "clear"


def test_untrustworthy_resource_observer_is_a_stable_failure(tmp_path):
    admission = _admission(observer=lambda _device: (_ for _ in ()).throw(OSError("private detail")))
    with pytest.raises(ModelLifecycleError, match="model_resource_measurement_unavailable"):
        admission.check("cpu")


@pytest.mark.parametrize("cuda_free", [None, -1, "unavailable"])
def test_cuda_admission_rejects_missing_or_inconsistent_measurements(cuda_free):
    mib = 1024 * 1024
    measurements = ResourceMeasurements(8 * mib, 8 * mib, cuda_free)
    admission = _admission(observer=lambda _device: measurements)
    with pytest.raises(ModelLifecycleError, match="model_resource_measurement_unavailable"):
        admission.check("cuda")


def test_transient_preload_is_disabled_and_does_not_warm_models(tmp_path):
    config = _config(tmp_path)
    loads = []
    registry = _registry(tmp_path, lambda *_: loads.append(1) or _Model())
    with pytest.raises(ModelRuntimeError) as error:
        registry.preload(config, components=("text",))
    assert error.value.error_code == "model_preload_disabled_by_policy"
    assert loads == []
    assert registry.snapshot().components == ()


def test_load_failure_does_not_report_ready_or_allow_an_unsafe_retry(tmp_path):
    config = _config(tmp_path)

    def fail_loader(*_args):
        raise OSError("private model loader detail")

    registry = _registry(tmp_path, fail_loader)
    adapter = registry.get_text_adapter(config)
    with pytest.raises(ModelLifecycleError, match="model_release_unverified"):
        adapter.embed_query("query")
    status = registry.snapshot().components[0]
    assert status.state == "failed"
    assert status.error_code == "model_release_unverified"
    assert registry._lifecycle.residency_state == "unknown"
    assert "private model loader detail" not in repr(status)


def test_release_failure_marks_adapter_failed_and_blocks_future_acquisition(tmp_path, monkeypatch):
    config = _config(tmp_path)
    registry = _registry(tmp_path, lambda *_: _Model())
    adapter = registry.get_text_adapter(config)

    def fail_release():
        raise OSError("private release detail")

    monkeypatch.setattr(adapter, "_release_loaded_model", fail_release)
    with pytest.raises(ModelLifecycleError, match="model_release_failed"):
        adapter.embed_query("query")
    status = registry.snapshot().components[0]
    assert status.state == "failed"
    assert status.error_code == "model_release_failed"
    assert registry._lifecycle.residency_state == "unknown"
    with pytest.raises(ModelLifecycleError, match="model_resource_state_unknown"):
        adapter.embed_query("query")


def test_custom_registry_adapter_must_support_scoped_ownership(tmp_path):
    config = _config(tmp_path)
    registry = ModelAdapterRegistry(
        text_factory=lambda _config: type("Unmanaged", (), {
            "load": lambda self: None,
            "embed_query": lambda self, _query: np.ones(1),
            "encode_text": lambda self, _text, **_kwargs: np.ones(1),
        })(),
        lifecycle_policy="transient_serialized",
        resource_admission=_admission(),
    )
    with pytest.raises(ModelRuntimeError, match="text_adapter_lifecycle_unsupported"):
        registry.get_text_adapter(config)


def test_transient_builder_rejects_caller_owned_adapters_without_touching_storage(tmp_path):
    storage = tmp_path / "never-created"
    with pytest.raises(IndexBuildError, match="model_lifecycle_adapter_ownership_unverified"):
        build_index(
            object(), tmp_path / "source", storage,
            text_adapter=object(),
            model_lifecycle_policy="transient_serialized",
        )
    assert not storage.exists()
