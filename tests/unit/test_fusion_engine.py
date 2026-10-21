from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.engine.fusion import FusionEngine


def config(**overrides):
    values = dict(
        adaptive_weighting=True, bm25_weight=0.5, semantic_weight=0.5,
        adaptive_weights_keyword=(0.7, 0.3), adaptive_weights_semantic=(0.2, 0.8),
        adaptive_weights_balanced_strong_bm25=(0.5, 0.5), adaptive_weights_balanced_weak_bm25=(0.3, 0.7),
        bm25_strong_max_score_threshold=10.0, bm25_strong_std_threshold=2.0,
        bm25_weak_max_score_threshold=3.0, bm25_signal_max_weight=0.6,
        bm25_signal_std_weight=0.4, bm25_signal_spec_weight=0.0, bm25_signal_lex_weight=0.0,
        rrf_k_constant=2,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def result(ids, *, scores=None, distances=None):
    return {"ids": [ids], "documents": [[f"doc-{x}" for x in ids]], "metadatas": [[{"id": x} for x in ids]], **({"scores": scores} if scores is not None else {}), **({"distances": [distances]} if distances is not None else {})}


def test_fusion_contracts_match_research_definitions():
    engine = FusionEngine(config())
    assert engine.classify_query_type("mirror diameter") == "keyword"
    assert engine.classify_query_type("Explain why the paths diverge after Saturn") == "semantic"
    assert engine.classify_query_type("Which mission evidence connects the trajectory, imaging, timing, and spacecraft geometry across the documents") == "balanced"
    assert engine.normalize_scores([]) == []
    assert engine.normalize_scores([5, 5]) == [0.5, 0.5]
    assert engine.bm25_distances([10, 5])[0] == pytest.approx([1 - 10 / 12, 1 - 5 / 12])
    overlap = engine.calculate_overlap(["a", "a", "b"], ["b", "c"])
    assert overlap["intersection_size"] == 1
    assert overlap["union_size"] == 3

    fused, stats = engine.fuse(result(["a", "b"], scores=[10, 5]), result(["b", "c"], distances=[0.1, 0.9]), 0.6, 0.4, 20)
    assert fused["ids"][0] == ["a", "b", "c"]
    assert fused["fused_scores"][0] == pytest.approx([0.6, 0.4, 0.0])
    assert stats["bm25_contribution_mean"] == pytest.approx(0.2)

    rrf, rrf_stats = engine.rrf(result(["a", "b"], scores=[10, 5]), result(["b", "c"], distances=[0.1, 0.9]), 0.5, 0.5, 20, 2)
    assert rrf["ids"][0] == ["b", "a", "c"]
    assert rrf_stats == {"bm25_only_docs": 1, "semantic_only_docs": 1, "both_signals_docs": 1}


def test_adaptive_weighting_has_semantic_fallback():
    engine = FusionEngine(config())
    weights = engine.adaptive_weights("short lookup", {"bm25_stats": {"max_score": 0, "std_score": 0, "corpus_coverage": 0, "non_zero_docs": 0}})
    assert weights[:2] == (0.0, 1.0)
    assert weights[2]["fallback_reason"] == "no_positive_bm25_scores"
