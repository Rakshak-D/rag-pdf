from __future__ import annotations

from pathlib import Path

import pytest

from backend.config import ResolvedRunConfig
from backend.engine.context import ContextFormatter, ContextImagePathError, ContextInputError, ContextTokenizationError
from backend.engine.retrieval import HybridHit, HybridRetrievalResult
from backend.engine.manifest import build_document_manifest
from backend.experiments import build_run_plan
from notebooks.config import Config


def make_plan(tmp_path: Path, **overrides):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir(parents=True)
    (pdf_dir / "source.pdf").write_bytes(b"source")
    values = dict(pdf_dir=str(pdf_dir), images_dir=str(tmp_path / "images"), database_dir=str(tmp_path / "db"), results_dir=str(tmp_path / "results"), model_comparison_results_dir=str(tmp_path / "comparison"), retrieval_results_dir=str(tmp_path / "retrieval"), reset_collections_on_start=False, use_filtering=False, use_percentile_filtering=False, max_text_chunks=3, max_images=2, max_context_tokens=100)
    values.update(overrides)
    config = ResolvedRunConfig.from_legacy(Config(**values))
    return build_run_plan(config, build_document_manifest(pdf_dir).content_hash), config


def result(plan, text_hits=(), image_hits=()):
    return HybridRetrievalResult("query-id", plan.index_fingerprint, plan.document_manifest_hash, ("text", "image"), 5, 5, tuple(text_hits), tuple(image_hits), "rrf", (0.5, 0.5), {}, (), 0.0)


def text_hit(identifier, text, *, source="source.pdf", page=1, distance=None, fused=0.5):
    return HybridHit(identifier, 1, fused, {"source": source, "page_num": page}, document=text, semantic_distance=distance)


def image_hit(identifier, path, *, source="source.pdf", page=1, distance=None):
    return HybridHit(identifier, 1, 0.5, {"source": source, "page_num": page, "image_id": identifier, "image_path": str(path)}, semantic_distance=distance)


def test_matching_identity_and_fingerprint_are_deterministic_without_mutation(tmp_path):
    plan, config = make_plan(tmp_path)
    original = text_hit("a", "Alpha   evidence", distance=0.2)
    context1 = ContextFormatter(encoder=lambda text: list(range(len(text)))).format(result(plan, [original]), plan)
    context2 = ContextFormatter(encoder=lambda text: list(range(len(text)))).format(result(plan, [original]), plan)
    assert context1.text_context == "[T1] Source: source.pdf, page 1 (ID: a)\nAlpha   evidence"
    assert context1.context_fingerprint == context2.context_fingerprint
    assert context1.selected_text[0].hit == original
    assert original.cross_modal_boost == 0.0


def test_exact_and_near_duplicates_keep_ranked_first_and_filter_fallback(tmp_path):
    plan, _ = make_plan(tmp_path, max_context_tokens=500)
    hits = [text_hit("a", "one two three four five six", distance=0.9), text_hit("b", "ONE two three four five six", distance=0.1), text_hit("c", "one two three four five seven", distance=0.8)]
    context = ContextFormatter(encoder=lambda text: list(range(len(text)))).format(result(plan, hits), plan)
    assert [item.record_id for item in context.selected_text] == ["a", "c"]

    plan, _ = make_plan(tmp_path / "fallback", use_filtering=True, use_percentile_filtering=False, text_distance_threshold=0.01, max_context_tokens=500)
    context = ContextFormatter(encoder=lambda text: list(range(len(text)))).format(result(plan, [text_hit("a", "best", distance=0.8), text_hit("b", "second", distance=0.9)]), plan)
    assert context.selected_text[0].record_id == "a"
    assert context.diagnostics["text_filter_fallback"] is True


def test_exact_token_budget_truncates_complete_formatted_context(tmp_path):
    plan, _ = make_plan(tmp_path, max_context_tokens=80, max_text_chunks=2)
    context = ContextFormatter(encoder=lambda text: list(range(len(text)))).format(result(plan, [text_hit("a", "long evidence " * 30)]), plan)
    assert context.text_context_tokens <= 80
    assert context.diagnostics["text_entries_truncated"] == 1
    assert "[T1]" in context.text_context


def test_bm25_only_text_and_invalid_text_are_safe(tmp_path):
    plan, _ = make_plan(tmp_path)
    invalid = HybridHit("bad", 1, 0.1, {}, document="   ")
    lexical = text_hit("lex", "keyword evidence", distance=None)
    context = ContextFormatter(encoder=lambda text: list(range(len(text)))).format(result(plan, [invalid, lexical]), plan)
    assert [item.record_id for item in context.selected_text] == ["lex"]
    assert context.diagnostics["valid_text_candidates"] == 1
    assert "text_candidate_invalid" in context.warnings


def test_image_paths_are_contained_and_missing_images_do_not_remove_text(tmp_path):
    plan, config = make_plan(tmp_path, max_context_tokens=500)
    image_root = Path(config.images_dir)
    image_root.mkdir()
    valid = image_root / "valid.png"
    valid.write_bytes(b"not opened")
    hits = [image_hit("good", valid), image_hit("missing", image_root / "missing.png"), image_hit("escape", tmp_path / "outside.png")]
    context = ContextFormatter(encoder=lambda text: list(range(len(text)))).format(result(plan, [text_hit("t", "text")], hits), plan)
    assert [item.record_id for item in context.selected_images] == ["good"]
    assert context.selected_images[0].resolved_path == valid.resolve()
    assert context.selected_text[0].record_id == "t"
    assert "image_file_missing" in context.warnings
    assert "image_path_outside_owned_root" in context.warnings


def test_mismatched_provenance_and_tokenizer_failures_are_typed(tmp_path):
    plan, _ = make_plan(tmp_path)
    bad_result = result(plan, [text_hit("a", "text")])
    bad_result = HybridRetrievalResult(bad_result.query_id, "0" * 64, bad_result.document_manifest_hash, bad_result.modalities, bad_result.effective_text_k, bad_result.effective_image_k, bad_result.text_hits, bad_result.image_hits, bad_result.fusion_strategy, bad_result.effective_weights, bad_result.diagnostics, bad_result.modality_timings, bad_result.total_elapsed_seconds)
    with pytest.raises(ContextInputError):
        ContextFormatter(encoder=lambda text: []).format(bad_result, plan)

    with pytest.raises(ContextTokenizationError):
        ContextFormatter(encoder=lambda text: (_ for _ in ()).throw(RuntimeError("bad encoder"))).format(result(plan, [text_hit("a", "text")]), plan)
