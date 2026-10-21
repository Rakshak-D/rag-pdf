from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.engine.cross_modal import apply_cross_modal_boosts
from backend.engine.retrieval import HybridHit


def config(**overrides):
    values = dict(use_cross_modal_boost=True, cross_modal_max_boost=0.3, cross_modal_boost_per_overlap=0.1)
    values.update(overrides)
    return SimpleNamespace(**values)


def test_exact_and_extracted_image_identity_matching_preserves_raw_scores():
    text = HybridHit(1 and "chunk", 1, 0.5, {"source": "report.pdf", "related_image_ids": ["report_p1_i2"]}, document="evidence", semantic_distance=0.5, semantic_similarity=0.5)
    image = HybridHit("report_p1_i2", 1, 0.4, {"image_id": "report_p1_i2", "image_path": "C:/artifacts/report/page_1_img_2.png", "source": "report.pdf"}, semantic_distance=0.2, semantic_similarity=0.8)
    text_out, image_out, warnings, diagnostics = apply_cross_modal_boosts((text,), (image,), config())
    assert not warnings
    assert diagnostics == {"matched_text_hits": 1, "matched_image_hits": 1}
    assert text_out[0].cross_modal_boost == 0.1
    assert text_out[0].semantic_distance is None or text_out[0].semantic_distance == 0.5
    assert text_out[0].effective_distance == 0.45
    assert image_out[0].effective_distance == pytest.approx(0.18)


def test_malformed_relationships_and_same_filename_different_sources_do_not_boost():
    text = HybridHit("chunk", 1, 0.5, {"source": "one.pdf", "related_image_ids": "not-a-list"}, document="evidence")
    image = HybridHit("img", 1, 0.4, {"image_path": "C:/two/page_1_img_1.png", "source": "two.pdf"})
    text_out, image_out, warnings, _ = apply_cross_modal_boosts((text,), (image,), config())
    assert warnings
    assert text_out[0].cross_modal_boost == 0.0
    assert image_out[0].cross_modal_boost == 0.0


def test_disabled_boost_is_identity():
    text = HybridHit("chunk", 1, 0.5, {"related_image_ids": ["img"]}, document="evidence")
    image = HybridHit("img", 1, 0.4, {"image_id": "img"})
    assert apply_cross_modal_boosts((text,), (image,), config(use_cross_modal_boost=False))[:2] == ((text,), (image,))
