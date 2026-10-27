from __future__ import annotations

from pathlib import Path
from threading import Barrier, Event, Thread

import numpy as np

from backend.embeddings.image import ImageEmbeddingAdapter
from backend.embeddings.text import TextEmbeddingAdapter
from backend.engine.reranking import CrossEncoderReranker
from backend.experiments import build_ablation_plan
from backend.services import ModelAdapterRegistry, ModelRuntimeError
from tests.unit.test_evaluation_runner import setup_chain


class _Model:
    def encode(self, **kwargs):
        return np.array([1.0, 0.0], dtype=np.float32)


def test_compatible_text_configuration_is_cached_without_loading(tmp_path: Path, monkeypatch):
    plan, _, _, _, _ = setup_chain(tmp_path)
    config = plan.resolved_config()
    created = []

    def factory(value):
        created.append(value.text_embed_model)
        return TextEmbeddingAdapter(value, model_loader=lambda *_: _Model())

    registry = ModelAdapterRegistry(text_factory=factory)
    first = registry.get_text_adapter(config)
    second = registry.get_text_adapter(config.model_copy(deep=True))
    assert first is second
    assert created == [config.text_embed_model]
    assert registry.snapshot().components[0].state == "unloaded"


def test_preload_is_explicit_and_reuses_successful_load(tmp_path: Path, monkeypatch):
    plan, _, _, _, _ = setup_chain(tmp_path)
    config = plan.resolved_config()
    calls = []

    def factory(value):
        def loader(*_):
            calls.append("load")
            return _Model()
        return TextEmbeddingAdapter(value, model_loader=loader)

    registry = ModelAdapterRegistry(text_factory=factory)
    snapshot = registry.preload(config, components=("text",))
    assert calls == ["load"]
    assert snapshot.components[0].state == "ready"
    assert snapshot.components[0].explicitly_warmed is True
    registry.preload(config, components=("text",))
    assert calls == ["load"]


def test_concurrent_text_load_is_single_flight(tmp_path: Path):
    plan, _, _, _, _ = setup_chain(tmp_path)
    config = plan.resolved_config()
    barrier = Barrier(5)
    calls = []

    def loader(*_):
        calls.append(1)
        return _Model()

    adapter = TextEmbeddingAdapter(config, model_loader=loader)
    workers = [Thread(target=lambda: (barrier.wait(), adapter.load())) for _ in range(5)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert len(calls) == 1
    assert adapter.runtime_state == "ready"


def test_failed_load_has_stable_status_and_can_retry(tmp_path: Path):
    plan, _, _, _, _ = setup_chain(tmp_path)
    config = plan.resolved_config()
    calls = []

    def loader(*_):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("private loader detail")
        return _Model()

    registry = ModelAdapterRegistry(text_factory=lambda value: TextEmbeddingAdapter(value, model_loader=loader))
    try:
        registry.preload(config, components=("text",))
    except ModelRuntimeError as exc:
        assert exc.error_code == "text_model_load_failed"
    assert "private loader detail" not in repr(registry.snapshot())
    assert registry.preload(config, components=("text",)).components[0].state == "ready"


def test_image_and_reranker_operational_settings_are_part_of_cache_identity(tmp_path: Path):
    plan, _, _, _, _ = setup_chain(tmp_path)
    config = plan.resolved_config()
    image_factory = lambda value: ImageEmbeddingAdapter(value, model_loader=lambda *_: None)
    reranker_factory = lambda value: CrossEncoderReranker(value, loader=lambda _: None)
    registry = ModelAdapterRegistry(image_factory=image_factory, reranker_factory=reranker_factory)
    first_image = registry.get_image_adapter(config)
    second_image = registry.get_image_adapter(config.model_copy(update={"image_caption_image_weight": 0.5}))
    assert first_image is not second_image
    first_reranker = registry.get_reranker(config)
    second_reranker = registry.get_reranker(config.model_copy(update={"reranker_ce_weight": 0.7, "reranker_fused_weight": 0.25}))
    assert first_reranker is not second_reranker


def test_canonical_ablation_variants_reuse_compatible_wrappers(tmp_path: Path):
    plan, _, _, _, _ = setup_chain(tmp_path)
    base = plan.resolved_config()
    ablation = build_ablation_plan(base, plan.document_manifest_hash)
    created = {"text": 0, "image": 0, "reranker": 0}

    def text_factory(value):
        created["text"] += 1
        return TextEmbeddingAdapter(value, model_loader=lambda *_: _Model())

    def image_factory(value):
        created["image"] += 1
        return ImageEmbeddingAdapter(value, model_loader=lambda *_: None)

    def reranker_factory(value):
        created["reranker"] += 1
        return CrossEncoderReranker(value, loader=lambda _: None)

    registry = ModelAdapterRegistry(text_factory=text_factory, image_factory=image_factory, reranker_factory=reranker_factory)
    bundles = [registry.get_bundle(item.run_plan.resolved_config()) for item in ablation.variants]
    assert len({id(bundle.text_adapter) for bundle in bundles}) == 1
    assert len({id(bundle.image_adapter) for bundle in bundles}) == 1
    assert created["text"] == 1
    assert created["image"] == 1


def test_snapshot_is_side_effect_free_and_deterministically_ordered(tmp_path: Path):
    plan, _, _, _, _ = setup_chain(tmp_path)
    registry = ModelAdapterRegistry(text_factory=lambda value: TextEmbeddingAdapter(value, model_loader=lambda *_: _Model()))
    assert registry.snapshot().components == ()
    registry.get_text_adapter(plan.resolved_config())
    snapshot = registry.snapshot()
    assert [item.component_kind for item in snapshot.components] == ["text"]
    assert snapshot.components[0].state == "unloaded"


def test_registry_factory_is_single_flight_under_contention(tmp_path: Path):
    plan, _, _, _, _ = setup_chain(tmp_path)
    config = plan.resolved_config()
    barrier = Barrier(6)
    entered = Event()
    release = Event()
    calls = []

    def factory(value):
        calls.append(1)
        entered.set()
        assert release.wait(2)
        return TextEmbeddingAdapter(value, model_loader=lambda *_: _Model())

    registry = ModelAdapterRegistry(text_factory=factory)
    results = []
    errors = []

    def worker():
        try:
            barrier.wait()
            results.append(registry.get_text_adapter(config))
        except BaseException as exc:
            errors.append(exc)

    threads = [Thread(target=worker) for _ in range(5)]
    for thread in threads:
        thread.start()
    barrier.wait()
    assert entered.wait(2)
    release.set()
    for thread in threads:
        thread.join(2)
    assert errors == []
    assert len(calls) == 1
    assert len({id(item) for item in results}) == 1


def test_snapshot_during_loading_is_safe_and_does_not_load(tmp_path: Path):
    plan, _, _, _, _ = setup_chain(tmp_path)
    config = plan.resolved_config()
    entered = Event()
    release = Event()

    def loader(*_):
        entered.set()
        assert release.wait(2)
        return _Model()

    registry = ModelAdapterRegistry(text_factory=lambda value: TextEmbeddingAdapter(value, model_loader=loader))
    adapter = registry.get_text_adapter(config)
    thread = Thread(target=adapter.load)
    thread.start()
    assert entered.wait(2)
    snapshot = registry.snapshot()
    assert snapshot.components[0].state == "loading"
    release.set()
    thread.join(2)
    assert registry.snapshot().components[0].state == "ready"
