from __future__ import annotations

import pytest

from tests.support.source_loader import load_functions


def test_retrieval_similarity_is_the_mean_of_supplied_signals():
    compute = load_functions("compute_retrieval_similarity", globals_override={
        "statistics": __import__("statistics"),
        "LOGGER": None,
    })["compute_retrieval_similarity"]

    assert compute([0.2, 0.4, 0.8]) == pytest.approx(0.4666666666666667)
    assert compute([]) == 0.0


def test_context_fcd_aggregate_reads_existing_per_query_values_only():
    compute = load_functions("compute_fcd", globals_override={
        "statistics": __import__("statistics"),
        "LOGGER": None,
    })["compute_fcd"]

    rows = [
        {"response": {"factual_consistency_distance": 20.0}},
        {"response": {"factual_consistency_distance": 40.0}},
        {"response": {"factual_consistency_distance": None}},
        {"response": "not-a-dict"},
    ]
    assert compute(rows) == 30.0
    assert compute([]) == 0.0
