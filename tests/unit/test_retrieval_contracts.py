from __future__ import annotations

import hashlib
import re
from types import SimpleNamespace

import pytest

from tests.support.source_loader import load_class_methods


class ArrayStub:
    def __init__(self, values):
        self.values = [float(value) for value in values]

    def __sub__(self, other):
        if isinstance(other, ArrayStub):
            return ArrayStub([a - b for a, b in zip(self.values, other.values)])
        return ArrayStub([a - other for a in self.values])

    def __truediv__(self, other):
        if isinstance(other, ArrayStub):
            return ArrayStub([a / b for a, b in zip(self.values, other.values)])
        return ArrayStub([a / other for a in self.values])

    def __mul__(self, other):
        return ArrayStub([value * other for value in self.values])

    def tolist(self):
        return self.values

    def __len__(self):
        return len(self.values)


class NumpyStub:
    @staticmethod
    def array(values):
        return ArrayStub(values)

    @staticmethod
    def min(values):
        return min(values.values if isinstance(values, ArrayStub) else values)

    @staticmethod
    def max(values):
        return max(values.values if isinstance(values, ArrayStub) else values)

    @staticmethod
    def mean(values):
        return sum(values) / len(values)


def retrieval_class():
    return load_class_methods(
        "RetrievalRag",
        "_classify_query_type",
        "_normalize_scores",
        "_bm25_distances",
        "_calculate_overlap",
        "_fuse_scores",
        "_rrf_fusion",
        "_get_adaptive_weights",
        globals_override={
            "np": NumpyStub,
            "re": re,
            "hashlib": hashlib,
            "cfg": SimpleNamespace(
                adaptive_weights_keyword=(0.7, 0.3),
                adaptive_weights_semantic=(0.2, 0.8),
                adaptive_weights_balanced_strong_bm25=(0.5, 0.5),
                adaptive_weights_balanced_weak_bm25=(0.3, 0.7),
                bm25_strong_max_score_threshold=10.0,
                bm25_strong_std_threshold=2.0,
                bm25_weak_max_score_threshold=3.0,
                bm25_signal_max_weight=0.6,
                bm25_signal_std_weight=0.4,
                bm25_signal_spec_weight=0.0,
                bm25_signal_lex_weight=0.0,
                rrf_k_constant=20,
                fusion_type="rrf",
            ),
        },
    )


def bare_retriever(**values):
    instance = object.__new__(retrieval_class())
    defaults = {
        "bm25_weight": 0.5,
        "semantic_weight": 0.5,
        "adaptive_weighting": True,
    }
    defaults.update(values)
    for key, value in defaults.items():
        setattr(instance, key, value)
    return instance


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("mirror diameter", "keyword"),
        ("What was the 1977 launch date for Voyager?", "keyword"),
        ("Explain why the paths diverge after Saturn", "semantic"),
        ("Which mission evidence connects the trajectory, imaging, timing, and spacecraft geometry across the documents", "balanced"),
    ],
)
def test_query_type_classification_contract(query, expected):
    instance = bare_retriever()
    assert instance._classify_query_type(query) == expected


def test_score_normalization_contract():
    instance = bare_retriever()

    assert instance._normalize_scores([]) == []
    assert instance._normalize_scores([5, 5]) == [0.5, 0.5]
    assert instance._normalize_scores([1, 2, 3]) == [0.0, 0.5, 1.0]


def test_bm25_score_to_distance_contract():
    cls = retrieval_class()

    assert cls._bm25_distances([]) == [[]]
    assert cls._bm25_distances([-1, 0]) == [[1.0, 1.0]]
    distances = cls._bm25_distances([10, 5])
    assert distances[0] == pytest.approx([1 - 10 / 12, 1 - 5 / 12])


def test_overlap_statistics_contract():
    instance = bare_retriever()
    result = instance._calculate_overlap(["a", "a", "b"], ["b", "c"])

    assert result["intersection_size"] == 1
    assert result["union_size"] == 3
    assert result["jaccard_similarity"] == pytest.approx(1 / 3)
    assert result["overlap_percentage"] == 50.0
    assert set(result["intersection_ids"]) == {"b"}


def retrieval_result(documents, ids, scores=None, distances=None):
    return {
        "documents": [documents],
        "ids": [ids],
        "metadatas": [[{"id": doc_id} for doc_id in ids]],
        **({"scores": scores} if scores is not None else {}),
        **({"distances": [distances]} if distances is not None else {}),
    }


def test_weighted_fusion_ranks_shared_and_single_retriever_documents():
    instance = bare_retriever(bm25_weight=0.6, semantic_weight=0.4)
    bm25 = retrieval_result(["text-a", "text-b"], ["a", "b"], scores=[10, 5])
    semantic = retrieval_result(["text-b", "text-c"], ["b", "c"], distances=[0.1, 0.9])

    result, stats = instance._fuse_scores(bm25, semantic)

    assert result["ids"][0] == ["a", "b", "c"]
    assert result["fused_scores"][0] == pytest.approx([0.6, 0.4, 0.0])
    assert result["metadatas"][0][2] == {"id": "c"}
    # The implementation classifies fusion stats from normalized contributions;
    # the lowest BM25 score normalizes to zero, so the shared doc is counted
    # as semantic-only even though it appears in both input lists.
    assert stats == {
        "bm25_contribution_mean": pytest.approx(0.2),
        "semantic_contribution_mean": pytest.approx(0.13333333333333333),
        "bm25_only_docs": 2,
        "semantic_only_docs": 2,
        "both_signals_docs": 0,
    }


def test_rrf_fusion_uses_rank_contributions_and_orders_results():
    instance = bare_retriever(bm25_weight=0.5, semantic_weight=0.5)
    bm25 = retrieval_result(["text-a", "text-b"], ["a", "b"], scores=[10, 5])
    semantic = retrieval_result(["text-b", "text-c"], ["b", "c"], distances=[0.1, 0.9])

    result, stats = instance._rrf_fusion(bm25, semantic, k_rrf=2)

    assert result["ids"][0] == ["b", "a", "c"]
    assert result["fused_scores"][0] == pytest.approx([
        0.5 / 4 + 0.5 / 3,
        0.5 / 3,
        0.5 / 4,
    ])
    assert stats == {"bm25_only_docs": 1, "semantic_only_docs": 1, "both_signals_docs": 1}


def test_adaptive_weighting_falls_back_to_semantic_for_nonpositive_bm25():
    instance = bare_retriever()
    bm25 = {"bm25_stats": {"max_score": 0.0, "std_score": 0.0, "corpus_coverage": 0.0, "non_zero_docs": 0}}

    weights = instance._get_adaptive_weights("short lookup", bm25)

    assert weights[0:2] == (0.0, 1.0)
    assert weights[2]["fallback_to_semantic"] is True
    assert weights[2]["fallback_reason"] == "no_positive_bm25_scores"
