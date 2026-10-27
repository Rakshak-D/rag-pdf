from __future__ import annotations

from types import SimpleNamespace
from threading import Barrier, Event, Lock, Thread

import numpy as np
import pytest

from backend.engine.reranking import (
    CrossEncoderReranker,
    RerankerInferenceError,
    RerankerInputError,
    normalize_scores,
)
from backend.engine.retrieval import HybridHit


def config(**overrides):
    values = dict(reranker_model="fake-reranker", reranker_ce_weight=0.65, reranker_fused_weight=0.30, reranker_boost_weight=0.05)
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeModel:
    def __init__(self, scores):
        self.scores = scores
        self.pairs = []

    def predict(self, pairs):
        self.pairs.extend(pairs)
        return self.scores[:len(pairs)]


def hit(identifier, text, fused):
    return HybridHit(identifier, 1, fused, {"id": identifier}, document=text, boosted_fused_score=fused)


def test_loading_is_lazy_and_pair_order_is_preserved():
    calls = []
    model = FakeModel([0.1, 0.9])
    reranker = CrossEncoderReranker(config(), loader=lambda name: calls.append(name) or model)
    assert calls == []
    output = reranker.rerank_items("query", [hit("a", "first", 0.2), hit("b", "second", 0.8)], top_k=2)
    assert calls == ["fake-reranker"]
    assert model.pairs == [["query", "first"], ["query", "second"]]
    assert [item.record_id for item in output] == ["b", "a"]
    reranker.rerank_items("query", [hit("a", "first", 0.2)], top_k=1)
    assert calls == ["fake-reranker"]


def test_normalization_and_malformed_outputs_are_explicit():
    assert normalize_scores([]) == []
    assert normalize_scores([4, 4]) == [0.5, 0.5]
    assert normalize_scores([1, 2, 3]) == [0.0, 0.5, 1.0]
    with pytest.raises(RerankerInferenceError, match="wrong number"):
        CrossEncoderReranker(config(), loader=lambda _: FakeModel([1.0])).score_pairs("q", ["a", "b"])
    with pytest.raises(RerankerInferenceError, match="non-finite"):
        CrossEncoderReranker(config(), loader=lambda _: FakeModel([float("nan")])).score_pairs("q", ["a"])
    with pytest.raises(RerankerInputError):
        CrossEncoderReranker(config(), loader=lambda _: FakeModel([])).score_pairs("", ["a"])


def test_numpy_float32_and_single_score_column_are_accepted():
    model = FakeModel(np.asarray([[np.float32(0.1)], [np.float32(0.9)]], dtype=np.float32))
    reranker = CrossEncoderReranker(config(), loader=lambda _: model)
    scores = reranker.score_pairs("q", ["a", "b"])
    assert scores == pytest.approx([0.1, 0.9])


@pytest.mark.parametrize("scores", [np.asarray([[1.0, 2.0]]), np.asarray([True]), np.asarray(["1.0"]), np.asarray([np.inf])])
def test_ambiguous_or_invalid_numpy_outputs_are_rejected(scores):
    with pytest.raises(RerankerInferenceError):
        CrossEncoderReranker(config(), loader=lambda _: FakeModel(scores)).score_pairs("q", ["a"] if len(scores) == 1 else ["a", "b"])


def test_equal_scores_use_legacy_neutral_normalization_and_stable_ties():
    model = FakeModel([1.0, 1.0])
    reranker = CrossEncoderReranker(config(), loader=lambda _: model)
    output = reranker.rerank_items("q", [hit("a", "a", 0.5), hit("b", "b", 0.5)], top_k=2)
    assert [item.record_id for item in output] == ["a", "b"]
    assert all(item.reranker_normalized_score == 0.5 for item in output)


def test_ties_use_original_fused_then_raw_cross_encoder_then_input_order():
    model = FakeModel([1.0, 1.0, 1.0])
    reranker = CrossEncoderReranker(config(), loader=lambda _: model)
    output = reranker.rerank_items("q", [hit("a", "a", 0.1), hit("b", "b", 0.9), hit("c", "c", 0.9)], top_k=3)
    assert [item.record_id for item in output] == ["b", "c", "a"]
    assert [item.fused_score for item in output] == [0.9, 0.9, 0.1]

    model = FakeModel([0.4, 0.2, 0.3])
    reranker = CrossEncoderReranker(config(reranker_ce_weight=0.0, reranker_fused_weight=1.0, reranker_boost_weight=0.0), loader=lambda _: model)
    output = reranker.rerank_items("q", [hit("a", "a", 0.5), hit("b", "b", 0.5), hit("c", "c", 0.5)], top_k=3)
    assert [item.record_id for item in output] == ["a", "c", "b"]


def test_concurrent_load_calls_loader_once_and_failed_load_retries():
    barrier = Barrier(5)
    entered = Event()
    release = Event()
    calls = []
    model = FakeModel([0.5])

    def loader(name):
        calls.append(name)
        entered.set()
        assert release.wait(2)
        return model

    reranker = CrossEncoderReranker(config(), loader=loader)
    errors = []

    def worker():
        try:
            barrier.wait()
            assert reranker.load() is model
        except BaseException as exc:
            errors.append(exc)

    threads = [Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    barrier.wait()
    assert entered.wait(2)
    release.set()
    for thread in threads:
        thread.join(2)
    assert errors == []
    assert len(calls) == 1
    assert reranker.runtime_state == "ready"

    failed_calls = []

    def retry_loader(name):
        failed_calls.append(name)
        if len(failed_calls) == 1:
            raise RuntimeError("private reranker loader detail")
        return model

    retry = CrossEncoderReranker(config(), loader=retry_loader)
    with pytest.raises(Exception):
        retry.load()
    assert retry.runtime_state == "failed"
    assert retry.runtime_error_code == "reranker_model_load_failed"
    assert retry._model is None
    assert retry.load() is model
    assert retry.runtime_state == "ready"


def test_concurrent_predict_calls_are_serialized():
    entered = Event()
    release = Event()
    state_lock = Lock()
    active = 0
    overlaps = []

    class SerializedModel(FakeModel):
        def predict(self, pairs):
            nonlocal active
            with state_lock:
                active += 1
                if active > 1:
                    overlaps.append(active)
            entered.set()
            assert release.wait(2)
            with state_lock:
                active -= 1
            return super().predict(pairs)

    reranker = CrossEncoderReranker(config(), loader=lambda _: SerializedModel([0.5]))
    barrier = Barrier(3)
    errors = []

    def worker():
        try:
            barrier.wait()
            reranker.score_pairs("query", ["candidate"])
        except BaseException as exc:
            errors.append(exc)

    first = Thread(target=worker); second = Thread(target=worker)
    first.start(); second.start(); barrier.wait()
    assert entered.wait(2)
    release.set()
    first.join(2); second.join(2)
    assert errors == []
    assert overlaps == []
