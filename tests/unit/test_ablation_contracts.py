from __future__ import annotations

from tests.support.source_loader import load_assignment


def test_ablation_contract_has_six_existing_configurations():
    configs = load_assignment("_ABLATION_CONFIGS")

    assert len(configs) == 6
    assert [label for label, _ in configs] == [
        "1_Dense_only",
        "2_BM25_only",
        "3_Hybrid_static",
        "4_Hybrid_adaptive",
        "5_Hybrid_reranker",
        "6_Full_pipeline",
    ]
    assert configs[0][1]["retrieval_mode"] == "semantic"
    assert configs[1][1]["retrieval_mode"] == "bm25"
    assert configs[2][1] == {
        "retrieval_mode": "hybrid",
        "adaptive_weighting": False,
        "use_weighted_fusion": False,
        "use_reranker": False,
        "use_filtering": False,
        "use_percentile_filtering": False,
    }
    assert configs[3][1]["adaptive_weighting"] is True
    assert configs[4][1]["use_reranker"] is True
    assert configs[5][1]["adaptive_weighting"] is True
    assert configs[5][1]["use_reranker"] is True
    assert configs[5][1]["use_filtering"] is True
    assert configs[5][1]["use_percentile_filtering"] is True
    assert configs[5][1]["percentile_cutoff"] == 80
