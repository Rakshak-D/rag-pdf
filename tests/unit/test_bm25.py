from __future__ import annotations

import pytest

from backend.engine.bm25 import BM25Error, BM25Index


def test_bm25_ranks_positive_matches_and_preserves_alignment():
    index = BM25Index(
        ["Voyager launched in 1977", "A telescope observes stars"],
        ["a", "b"],
        [{"source": "a"}, {"source": "b"}],
    )
    result = index.query("Voyager 1977", top_k=2)
    assert result["ids"][0] == ["a"]
    assert result["documents"][0] == ["Voyager launched in 1977"]
    assert result["metadatas"][0] == [{"source": "a"}]
    assert result["scores"]
    assert result["bm25_stats"]["non_zero_docs"] == 1


def test_bm25_uses_overlap_fallback_when_scores_are_nonpositive():
    index = BM25Index(["alpha beta", "gamma"], ["a", "b"], [{}, {}])
    result = index.query("alpha", top_k=1)
    assert result["ids"][0] == ["a"]


def test_bm25_rejects_misaligned_or_empty_corpus_inputs():
    with pytest.raises(BM25Error):
        BM25Index(["one"], ["a", "b"], [{}, {}])
    with pytest.raises(BM25Error):
        BM25Index(["   "], ["a"], [{}])


def test_empty_corpus_is_explicit_and_query_is_empty():
    index = BM25Index([], [], [])
    result = index.query("anything")
    assert result["ids"] == [[]]
    assert result["scores"] == []
    assert result["bm25_stats"]["used_overlap_fallback"] is True
