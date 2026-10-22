from __future__ import annotations

import math

from backend.experiments.bertscore_metrics import (
    MODEL_ID,
    MODEL_LAYERS,
    BertScoreAdapter,
    CachedBertModel,
)


class FakeScorer:
    def __init__(self, **kwargs):
        self.settings = kwargs
        self.calls = []

    def score(self, candidates, references, **kwargs):
        self.calls.append((candidates, references, kwargs))
        return ([0.1], [0.2], [0.75])


def test_cache_only_settings_and_candidate_reference_order():
    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        scorer = FakeScorer(**kwargs)
        captured["scorer"] = scorer
        return scorer

    adapter = BertScoreAdapter(
        model_resolver=lambda: CachedBertModel("C:/private/cache/snapshot", "roberta-large@abc123"),
        scorer_factory=factory,
        device_selector=lambda: "cpu",
    )
    result = adapter.score("generated answer", "ground truth")
    assert result.status == "computed" and result.value == 0.75
    assert captured["model_type"] == "C:/private/cache/snapshot"
    assert captured["num_layers"] == MODEL_LAYERS
    assert captured["lang"] == "en"
    assert captured["idf"] is False
    assert captured["rescale_with_baseline"] is False
    assert captured["device"] == "cpu"
    assert captured["batch_size"] == 8
    assert captured["scorer"].calls == [(["generated answer"], ["ground truth"], {"batch_size": 8, "verbose": False})]
    assert result.model_identifier == "roberta-large@abc123"


def test_model_is_loaded_once_and_missing_cache_is_unavailable():
    calls = []
    adapter = BertScoreAdapter(model_resolver=lambda: calls.append(1) or None)
    first = adapter.score("answer", "reference")
    second = adapter.score("answer", "reference")
    assert first.status == second.status == "unavailable"
    assert first.reason_code == "bertscore_model_not_cached"
    assert calls == [1]


def test_invalid_scores_and_zero_are_handled_without_coercion():
    def make(value):
        return BertScoreAdapter(
            model_resolver=lambda: CachedBertModel("cache", MODEL_ID),
            scorer_factory=lambda **_: type("S", (), {"score": lambda self, *args, **kwargs: ([], [], [value])})(),
        )

    assert make(0.0).score("answer", "reference").status == "computed"
    for value in (math.nan, math.inf, -0.1, 1.1, "bad", True):
        result = make(value).score("answer", "reference")
        assert result.status == "failed"
        assert result.reason_code == "bertscore_invalid_score"


def test_empty_inputs_skip_before_resolving_model():
    calls = []
    adapter = BertScoreAdapter(model_resolver=lambda: calls.append(1) or None)
    result = adapter.score("", "reference")
    assert result.status == "skipped" and result.reason_code == "missing_text"
    assert calls == []


def test_legacy_defaults_are_explicit():
    assert MODEL_ID == "roberta-large"
    assert MODEL_LAYERS == 17
