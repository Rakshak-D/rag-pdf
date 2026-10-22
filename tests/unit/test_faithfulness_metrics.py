from __future__ import annotations

from backend.experiments.faithfulness_metrics import FALLBACK_STOPWORDS, FaithfulnessMetricAdapter


def test_sentence_splitter_strips_and_falls_back_without_downloads():
    adapter = FaithfulnessMetricAdapter(sentence_splitter=lambda text: [" first. ", "", "second!"])
    assert adapter.score("first second", "first. second!").value == 100.0


def test_stopwords_and_terms_use_sets_and_exact_threshold():
    adapter = FaithfulnessMetricAdapter(stopwords={"the", "and"}, sentence_splitter=lambda text: [text])
    result = adapter.score("alpha beta gamma", "delta and the")
    assert result.status == "computed" and result.value == 0.0
    assert "would" in FALLBACK_STOPWORDS

    inclusive = FaithfulnessMetricAdapter(stopwords=set(), sentence_splitter=lambda text: [text])
    assert inclusive.score("one two three four five six seven eight nine ten", "one two three eleven twelve thirteen fourteen fifteen sixteen seventeen").value == 100.0


def test_empty_terms_and_no_countable_sentences_are_computed_zero():
    adapter = FaithfulnessMetricAdapter(stopwords={"the", "and"}, sentence_splitter=lambda text: ["the and", "!!!"])
    assert adapter.score("the and", "the and").status == "computed"
    assert adapter.score("the and", "the and").value == 0.0


def test_regex_terms_require_three_alphabetic_characters_and_ignore_duplicates():
    adapter = FaithfulnessMetricAdapter(stopwords=set(), sentence_splitter=lambda text: [text])
    result = adapter.score("alpha beta gamma delta epsilon", "alpha alpha other thing more stuff")
    assert result.value == 0.0


def test_invalid_sentence_and_missing_inputs_are_explicit():
    assert FaithfulnessMetricAdapter().score("", "answer").reason_code == "missing_text"
    result = FaithfulnessMetricAdapter(sentence_splitter=lambda text: [object()]).score("context", "answer")
    assert result.status == "failed" and result.value is None
