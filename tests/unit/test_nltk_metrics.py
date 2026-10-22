from __future__ import annotations

import math

import pytest

from backend.experiments.nltk_metrics import NltkMetricsAdapter


class Smoothing:
    method4 = object()


def test_bleu_uses_lowercase_tokens_reference_first_and_smoothing():
    calls = []

    def bleu(references, hypothesis, *, smoothing_function):
        calls.append((references, hypothesis, smoothing_function))
        return 0.25

    adapter = NltkMetricsAdapter(tokenizer=lambda text: text.split(), bleu_function=bleu, smoothing_factory=Smoothing)
    result = adapter.score_bleu("Reference TEXT", "Generated TEXT")
    assert result.status == "computed" and result.value == 0.25
    assert calls == [([['reference', 'text']], ['generated', 'text'], Smoothing.method4)]


def test_meteor_is_independent_and_preserves_zero():
    adapter = NltkMetricsAdapter(tokenizer=lambda text: text.split(), meteor_function=lambda refs, hyp: 0.0)
    result = adapter.score_meteor("reference", "hypothesis")
    assert result.status == "computed" and result.value == 0.0


def test_missing_text_is_skipped_and_invalid_scores_fail():
    adapter = NltkMetricsAdapter(tokenizer=lambda text: text.split(), bleu_function=lambda *args, **kwargs: math.nan, smoothing_factory=Smoothing)
    assert adapter.score_bleu("", "answer").reason_code == "missing_text"
    assert adapter.score_bleu("reference", "answer").reason_code == "invalid_score"


def test_tokenizer_lookup_error_uses_local_regex_fallback():
    def missing(_text):
        raise LookupError("resource missing")

    adapter = NltkMetricsAdapter(tokenizer=missing, bleu_function=lambda refs, hyp, **_: 0.5, smoothing_factory=Smoothing)
    result = adapter.score_bleu("A-1 reference", "A-1 answer")
    assert result.status == "computed"


def test_meteor_lookup_error_is_unavailable_without_download():
    def meteor(_refs, _hyp):
        raise LookupError("wordnet missing")

    adapter = NltkMetricsAdapter(tokenizer=lambda text: text.split(), meteor_function=meteor)
    result = adapter.score_meteor("reference", "answer")
    assert result.status == "unavailable"
    assert result.reason_code == "meteor_resource_unavailable"
