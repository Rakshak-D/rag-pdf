import math

import pytest

from backend.engine.retrieval import HybridHit, HybridRetrievalResult
from backend.experiments.retrieval_similarity_metrics import RetrievalSimilarityMetricAdapter


def result_for(*, modalities=("text", "image"), text=(), image=()):
    return HybridRetrievalResult(
        "q" * 64,
        "i" * 64,
        "m" * 64,
        modalities,
        3 if "text" in modalities else None,
        2 if "image" in modalities else None,
        tuple(text),
        tuple(image),
        "rrf",
        (0.5, 0.5),
        {},
        tuple((modality, 0.0) for modality in modalities),
        0.0,
    )


def hit(record_id, *, fused=0.0, bm25=None, semantic=None):
    return HybridHit(record_id, 1, fused, {}, bm25_score=bm25, semantic_similarity=semantic)


def test_hybrid_blends_modality_means_equally():
    result = result_for(text=(hit("t1", fused=0.2), hit("t2", fused=0.4)), image=(hit("i1", fused=0.8),))
    measured = RetrievalSimilarityMetricAdapter.score(result, "hybrid")
    assert measured.status == "computed"
    assert measured.value == pytest.approx((0.3 + 0.8) / 2)
    assert measured.metric_name == "Fused Relevance"
    assert measured.signal == "fused_score"


@pytest.mark.parametrize(
    ("mode", "field", "expected_name", "expected"),
    [("semantic", "semantic", "Cosine Similarity", 0.7), ("bm25", "bm25", "BM25 Relevance", 0.5)],
)
def test_mode_selects_the_corresponding_signal(mode, field, expected_name, expected):
    kwargs = {field: expected}
    result = result_for(modalities=("text",), text=(hit("t1", **kwargs),))
    measured = RetrievalSimilarityMetricAdapter.score(result, mode)
    assert measured.status == "computed"
    assert measured.value == expected
    assert measured.metric_name == expected_name


def test_single_modality_and_zero_are_computed():
    result = result_for(modalities=("text",), text=(hit("t1", fused=0.0),))
    measured = RetrievalSimilarityMetricAdapter.score(result, "hybrid")
    assert measured.status == "computed" and measured.value == 0.0
    assert measured.modality_means == (("text", 0.0),)


@pytest.mark.parametrize("mode", ["unknown", None])
def test_unsupported_mode_is_explicitly_unavailable(mode):
    measured = RetrievalSimilarityMetricAdapter.score(result_for(), mode)
    assert measured.status == "unavailable"
    assert measured.value is None
    assert measured.reason_code == "unsupported_retrieval_mode"


def test_empty_or_missing_scores_are_not_zero():
    measured = RetrievalSimilarityMetricAdapter.score(result_for(modalities=("text",), text=()), "hybrid")
    assert measured.status == "skipped" and measured.value is None
    missing = RetrievalSimilarityMetricAdapter.score(result_for(modalities=("text",), text=(hit("t1", fused=None),)), "hybrid")
    assert missing.status == "skipped" and missing.value is None


@pytest.mark.parametrize("score", [True, "0.5", float("nan"), float("inf"), float("-inf")])
def test_invalid_scores_fail_without_coercion(score):
    measured = RetrievalSimilarityMetricAdapter.score(result_for(modalities=("text",), text=(hit("t1", fused=score),)), "hybrid")
    assert measured.status == "failed"
    assert measured.value is None
    assert measured.reason_code == "invalid_retrieval_score"
