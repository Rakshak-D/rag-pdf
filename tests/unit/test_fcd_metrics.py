from __future__ import annotations

import math

from backend.experiments.fcd_metrics import FcdMetricAdapter, build_legacy_prompt


class Embedder:
    model_name = "text-test"

    def __init__(self, values):
        self.values = iter(values)
        self.calls = []

    def embed_query(self, text):
        self.calls.append(text)
        return next(self.values)


def test_prompt_matches_legacy_template_and_embedding_order():
    query = "What is asked?"
    context = "Evidence text."
    expected = (
        "Use ONLY the context and images provided below to answer the question. Do not use any outside knowledge. If the context is insufficient, clearly state what is missing.\n\n"
        "If images are provided, treat them as direct evidence alongside the text — describe what they show and how they support or relate to the answer.\n\n"
        "Be thorough but natural. Write in clear prose, not bullet points or numbered sections. Aim for 150–250 words.\n\n"
        "Context:\nEvidence text.\n\nQuestion: What is asked?\n\nAnswer:"
    )
    assert build_legacy_prompt(query, context) == expected
    embedder = Embedder([[1.0, 0.0], [1.0, 0.0]])
    result = FcdMetricAdapter(embedder).score(query, context, "answer")
    assert result.status == "computed" and result.value == 0.0
    assert embedder.calls == ["answer", expected[:2000]]


def test_response_and_prompt_are_truncated_at_2000_characters():
    embedder = Embedder([[1.0, 0.0], [1.0, 0.0]])
    FcdMetricAdapter(embedder).score("q", "c" * 2500, "r" * 2500)
    assert len(embedder.calls[0]) == 2000
    assert len(embedder.calls[1]) == 2000


def test_distance_is_rounded_per_case_and_model_identity_is_safe():
    embedder = Embedder([[1.0, 0.0], [0.6, 0.8]])
    result = FcdMetricAdapter(embedder).score("q", "c", "r")
    assert result.status == "computed" and result.value == 40.0
    assert result.model_identifier == "text-test"


def test_invalid_embeddings_are_explicit_failures_without_zero_repair():
    cases = [([[1.0], [1.0, 2.0]], "fcd_embedding_dimension_mismatch"), ([[math.nan], [1.0]], "fcd_invalid_embedding"), ([[0.0], [1.0]], "fcd_zero_embedding"), ([["bad"], [1.0]], "fcd_invalid_embedding")]
    for vectors, reason in cases:
        result = FcdMetricAdapter(Embedder(vectors)).score("q", "c", "r")
        assert result.status == "failed" and result.reason_code == reason and result.value is None


def test_missing_embedder_is_unavailable_and_empty_response_is_skipped():
    assert FcdMetricAdapter(None).score("q", "c", "answer").reason_code == "fcd_embedder_unavailable"
    embedder = Embedder([])
    result = FcdMetricAdapter(embedder).score("q", "c", "   ")
    assert result.status == "skipped" and result.reason_code == "empty_response"
    assert embedder.calls == []


def test_embedder_errors_do_not_leak_private_text():
    class Bad:
        model_name = "safe-model"
        def embed_query(self, text):
            raise RuntimeError("secret prompt and C:/private/file.pdf")

    result = FcdMetricAdapter(Bad()).score("private question", "private context", "answer")
    assert result.status == "failed" and result.reason_code == "fcd_failed"
    assert "private" not in repr(result).lower()
