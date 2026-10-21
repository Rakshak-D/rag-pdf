from __future__ import annotations

from pathlib import Path

import pytest

from backend.config import ResolvedRunConfig
from backend.engine.context import ContextFormatter
from backend.engine.generation import LocalGenerationAdapter
from backend.engine.retrieval import HybridHit, HybridRetrievalResult, query_id_for
from backend.experiments import TraceContractError, TraceEvidence, TraceFailure, TraceStageTiming, build_query_trace, build_run_plan
from notebooks.config import Config


class Encoder:
    def encode(self, value: str):
        return list(range(len(value)))


def chain(tmp_path: Path):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    config = ResolvedRunConfig.from_legacy(Config(pdf_dir=str(pdf_dir), images_dir=str(tmp_path / "images"), database_dir=str(tmp_path / "db"), results_dir=str(tmp_path / "results"), model_comparison_results_dir=str(tmp_path / "comparison"), retrieval_results_dir=str(tmp_path / "retrieval"), reset_collections_on_start=False, max_context_tokens=500, llm_retries=0))
    plan = build_run_plan(config, "a" * 64)
    query = "What is the evidence?"
    modalities = ("text",)
    hit = HybridHit("chunk-1", 1, 0.25, {"source": "paper.pdf", "page_num": 3}, document="The answer is in the evidence.", semantic_distance=0.25, semantic_similarity=0.75, sources=("semantic",))
    retrieval = HybridRetrievalResult(query_id_for(query, plan, modalities, 5, None), plan.index_fingerprint, plan.document_manifest_hash, modalities, 5, None, (hit,), (), "rrf", (0.5, 0.5), {"text": {"candidate_pool_size": 1}}, (), 0.0, query_text=query)
    context = ContextFormatter(encoder=Encoder()).format(retrieval, plan)
    class Client:
        def chat(self, **request):
            return {"message": {"content": "The answer is supported [T1]."}}
    generation = LocalGenerationAdapter(client_factory=lambda _: Client(), encoder=Encoder()).generate(context, plan)
    return plan, retrieval, context, generation


def test_trace_captures_stages_without_raw_content(tmp_path):
    plan, retrieval, context, generation = chain(tmp_path)
    retrieved = build_query_trace(retrieval, plan)
    ready = build_query_trace(retrieval, plan, context=context)
    succeeded = build_query_trace(retrieval, plan, context=context, generation=generation, timings=(TraceStageTiming("retrieval", 0.1),))
    assert retrieved.status == "retrieved"
    assert ready.status == "context_ready"
    assert succeeded.status == "succeeded"
    assert succeeded.context_fingerprint == context.context_fingerprint
    assert succeeded.prompt_fingerprint == generation.prompt_fingerprint
    assert succeeded.evidence[0].record_id == "chunk-1"
    assert succeeded.context.selected_text[0][0] == "T1"
    assert succeeded.generation.response_hash
    assert "The answer is in the evidence" not in repr(succeeded)
    assert "The answer is supported" not in repr(succeeded)


def test_trace_fingerprint_excludes_timings_and_rejects_identity_mismatch(tmp_path):
    plan, retrieval, context, generation = chain(tmp_path)
    first = build_query_trace(retrieval, plan, context=context, generation=generation, timings=(TraceStageTiming("retrieval", 0.1),))
    second = build_query_trace(retrieval, plan, context=context, generation=generation, timings=(TraceStageTiming("retrieval", 99.0),))
    assert first.trace_fingerprint == second.trace_fingerprint
    bad = retrieval.__class__("wrong", retrieval.index_fingerprint, retrieval.document_manifest_hash, retrieval.modalities, retrieval.effective_text_k, retrieval.effective_image_k, retrieval.text_hits, retrieval.image_hits, retrieval.fusion_strategy, retrieval.effective_weights, retrieval.diagnostics, retrieval.modality_timings, retrieval.total_elapsed_seconds, query_text=retrieval.query_text)
    with pytest.raises(TraceContractError):
        build_query_trace(bad, plan)


def test_trace_failure_is_explicit_and_sanitized(tmp_path):
    plan, retrieval, _, _ = chain(tmp_path)
    trace = build_query_trace(retrieval, plan, failure=TraceFailure("generation", "inference_failed", attempts=2, elapsed_seconds=0.2))
    assert trace.status == "failed"
    assert trace.failed_stage == "generation"
    assert trace.error_code == "inference_failed"
    with pytest.raises(TraceContractError):
        TraceFailure("generation", "raw server error")


@pytest.mark.parametrize("record_id", [r"C:\Users\abhis\secret\chunk.txt", "/home/abhis/secret/chunk", "../private", "x" * 300])
def test_path_like_record_ids_are_redacted_in_trace(tmp_path, record_id):
    plan, retrieval, context, _ = chain(tmp_path)
    hit = HybridHit(record_id, 1, 0.25, {"source": "paper.pdf", "page_num": 3}, document="evidence", semantic_distance=0.25, semantic_similarity=0.75, sources=("semantic",))
    changed = retrieval.__class__(query_id_for(retrieval.query_text, plan, retrieval.modalities, 5, None), retrieval.index_fingerprint, retrieval.document_manifest_hash, retrieval.modalities, 5, None, (hit,), (), retrieval.fusion_strategy, retrieval.effective_weights, retrieval.diagnostics, retrieval.modality_timings, retrieval.total_elapsed_seconds, query_text=retrieval.query_text)
    trace = build_query_trace(changed, plan)
    assert record_id not in repr(trace)
    assert "secret" not in repr(trace)
    traced_context = ContextFormatter(encoder=Encoder()).format(changed, plan)
    context_trace = build_query_trace(changed, plan, context=traced_context)
    assert record_id not in repr(context_trace)


def test_direct_trace_evidence_rejects_unsafe_ids():
    with pytest.raises(TraceContractError):
        TraceEvidence("text", r"C:\private\chunk", 1, "source", "1", (), 0.0)
