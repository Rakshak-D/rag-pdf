from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from tests.support.source_loader import load_class_methods, load_functions


class NumpyContextStub:
    @staticmethod
    def percentile(values, cutoff):
        ordered = sorted(values)
        position = (len(ordered) - 1) * cutoff / 100
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        fraction = position - lower
        return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def formatter_class():
    deserialize_metadata = load_functions("deserialize_metadata")["deserialize_metadata"]
    return load_class_methods(
        "ContextFormatter",
        "__init__",
        "_flatten_results",
        "_select_text_chunks",
        "_format_text_context",
        globals_override={
            "np": NumpyContextStub,
            "hashlib": hashlib,
            "deserialize_metadata": deserialize_metadata,
            "cfg": SimpleNamespace(
                max_text_chunks=3,
                max_images=1,
                text_distance_threshold=1.0,
                image_distance_threshold=0.75,
                use_filtering=True,
                use_percentile_filtering=True,
                percentile_cutoff=80,
                max_context_tokens=1024,
            ),
        },
    )


def item(doc_id, text, distance, page=1):
    return {
        "doc_id": doc_id,
        "text": text,
        "metadata": {"source": "fixture.pdf", "page_num": page},
        "distance": distance,
        "rank": 1,
    }


def results(items, search_mode="semantic"):
    return {
        "documents": [[entry["text"] for entry in items]],
        "metadatas": [[entry["metadata"] for entry in items]],
        "distances": [[entry["distance"] for entry in items]],
        "ids": [[entry["doc_id"] for entry in items]],
        "retrieval_metrics": {"search_mode": search_mode},
    }


def make_formatter(**kwargs):
    instance = object.__new__(formatter_class())
    instance.__init__(**kwargs)
    return instance


def test_exact_and_near_duplicate_text_are_removed_in_rank_order():
    words = [f"word{i}" for i in range(20)]
    original = " ".join(words)
    near_duplicate = " ".join(words[:-1] + ["replacement"])
    formatter = make_formatter(max_text_chunks=5, use_filtering=False)

    selected = formatter._select_text_chunks(results([
        item("one", original, 0.8),
        item("duplicate", original, 0.1),
        item("near", near_duplicate, 0.2),
        item("other", "a distinct text passage with different evidence", 0.3),
    ]))

    assert [entry["doc_id"] for entry in selected] == ["one", "other"]


def test_distance_filtering_falls_back_to_nearest_when_all_candidates_rejected():
    formatter = make_formatter(
        max_text_chunks=1,
        text_distance_threshold=0.5,
        use_filtering=True,
        use_percentile_filtering=False,
    )

    selected = formatter._select_text_chunks(results([
        item("far", "far evidence", 0.9),
        item("nearest", "nearest evidence", 0.8),
    ]))

    assert [entry["doc_id"] for entry in selected] == ["nearest"]


def test_percentile_filtering_requires_more_than_two_unique_candidates():
    formatter = make_formatter(
        max_text_chunks=5,
        use_filtering=True,
        use_percentile_filtering=True,
        percentile_cutoff=80,
    )

    selected = formatter._select_text_chunks(results([
        item("low", "low evidence", 0.1),
        item("middle", "middle evidence", 0.2),
        item("high", "high evidence", 0.9),
    ]))

    assert [entry["doc_id"] for entry in selected] == ["low", "middle"]


def test_hybrid_preserves_ranked_order_while_semantic_mode_sorts_distance():
    items = [item("ranked-first", "first evidence", 0.9), item("ranked-second", "second evidence", 0.1)]
    hybrid = make_formatter(text_distance_threshold=1.0, use_percentile_filtering=False)
    semantic = make_formatter(text_distance_threshold=1.0, use_percentile_filtering=False)

    assert [entry["doc_id"] for entry in hybrid._select_text_chunks(results(items, "hybrid_rrf"))] == [
        "ranked-first", "ranked-second"
    ]
    assert [entry["doc_id"] for entry in semantic._select_text_chunks(results(items, "semantic_only"))] == [
        "ranked-second", "ranked-first"
    ]


def test_formatted_context_contains_source_and_page_metadata():
    formatter = make_formatter(max_context_tokens=1024)
    context = formatter._format_text_context([
        item("one", "A documented passage", 0.1, page=7),
    ])

    assert "A documented passage" in context
    assert "Source: fixture.pdf, page 7" in context
    # The production formatter uses max_context_tokens * 4 characters as an
    # approximate budget; it does not verify tokenizer-token boundaries.
    assert formatter._max_context_chars == 4096
