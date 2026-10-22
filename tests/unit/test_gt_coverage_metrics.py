from __future__ import annotations

import math

from backend.experiments.gt_coverage_metrics import GtCoverageMetricAdapter


class Embedder:
    model_name = "text-test"

    def __init__(self, vectors):
        self.vectors = iter(vectors)
        self.calls = []

    def encode_text(self, text):
        self.calls.append(text)
        return next(self.vectors)


def test_semantic_path_encodes_reference_then_response_and_uses_max_similarity():
    # Reference has two sentences; each is covered by one response vector.
    embedder = Embedder([[1.0, 0.0], [0.0, 1.0], [0.6, 0.8], [0.0, 1.0]])
    adapter = GtCoverageMetricAdapter(embedder, sentence_splitter=lambda text: [part.strip() for part in text.split("|")])
    result = adapter.score("reference one|reference two", "response one|response two", "ground_truth")
    assert result.status == "computed" and result.value == 100.0
    assert embedder.calls == ["reference one", "reference two", "response one", "response two"]
    assert result.model_identifier == "text-test"


def test_semantic_threshold_is_inclusive_and_score_uses_reference_denominator():
    inclusive = Embedder([[1.0, 0.0], [0.6, 0.8]])
    result = GtCoverageMetricAdapter(inclusive, sentence_splitter=lambda text: [text]).score("r1", "a", "ground_truth")
    assert result.status == "computed" and result.value == 100.0

    embedder = Embedder([[1.0, 0.0], [0.0, 1.0], [1.0, 0.0], [-1.0, 0.0]])
    result = GtCoverageMetricAdapter(embedder, sentence_splitter=lambda text: text.split("|")).score("r1|r2", "a|b", "ground_truth")
    assert result.status == "computed" and result.value == 50.0


def test_semantic_invalid_vectors_and_failures_are_not_zero_or_lexical_fallback():
    for vectors, reason in [([[math.nan], [1.0]], "gt_coverage_invalid_embedding"), ([[1.0], [1.0, 2.0]], "gt_coverage_embedding_dimension_mismatch")]:
        result = GtCoverageMetricAdapter(Embedder(vectors), sentence_splitter=lambda text: [text]).score("alpha", "alpha", "ground_truth")
        assert result.status == "failed" and result.reason_code == reason and result.value is None
    zero = GtCoverageMetricAdapter(Embedder([[0.0], [1.0]]), sentence_splitter=lambda text: [text]).score("alpha", "alpha", "ground_truth")
    assert zero.status == "failed" and zero.reason_code == "gt_coverage_zero_embedding"

    class Broken:
        model_name = "text-test"
        def encode_text(self, text):
            raise RuntimeError("private answer")

    result = GtCoverageMetricAdapter(Broken(), sentence_splitter=lambda text: [text]).score("alpha", "alpha", "ground_truth")
    assert result.status == "failed" and result.reason_code == "gt_coverage_failed"
    assert "private" not in repr(result)


def test_lexical_fallback_preserves_multiset_frequency_and_zero():
    adapter = GtCoverageMetricAdapter(stopwords=set(), sentence_splitter=lambda text: [text])
    result = adapter.score("alpha alpha beta gamma", "alpha beta beta", "ground_truth")
    assert result.status == "computed" and result.value == 50.0
    assert GtCoverageMetricAdapter(stopwords={"alpha", "beta"}).score("alpha beta", "alpha", "ground_truth").value == 0.0


def test_missing_text_and_empty_sentence_sequences_are_skipped():
    assert GtCoverageMetricAdapter().score("", "answer", "ground_truth").reason_code == "missing_text"
    result = GtCoverageMetricAdapter(Embedder([]), sentence_splitter=lambda text: []).score("reference", "response", "ground_truth")
    assert result.status == "skipped" and result.reason_code == "empty_sentences"
