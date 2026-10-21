from __future__ import annotations

from types import SimpleNamespace

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
