from __future__ import annotations

from pathlib import Path

import pytest

from backend.config import ResolvedRunConfig
from backend.engine.context import ContextFormatter
from backend.engine.generation import LocalGenerationAdapter
from backend.engine.retrieval import HybridHit, HybridRetrievalResult, query_id_for
from backend.experiments import BenchmarkCase, BenchmarkSuite, EvaluationRunner, EvaluationRunnerError, build_run_plan
from notebooks.config import Config


class Encoder:
    def encode(self, value: str):
        return list(range(len(value)))


def setup_chain(tmp_path: Path):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    config = ResolvedRunConfig.from_legacy(Config(pdf_dir=str(pdf_dir), images_dir=str(tmp_path / "images"), database_dir=str(tmp_path / "db"), results_dir=str(tmp_path / "results"), model_comparison_results_dir=str(tmp_path / "comparison"), retrieval_results_dir=str(tmp_path / "retrieval"), reset_collections_on_start=False, max_context_tokens=500, llm_retries=0))
    plan = build_run_plan(config, "a" * 64)
    query = "What is the evidence?"
    modalities = ("text",)
    hit = HybridHit("chunk-1", 1, 0.25, {"source": "paper.pdf", "page_num": 3}, document="The answer is in the evidence.", semantic_distance=0.25, semantic_similarity=0.75, sources=("semantic",))
    result = HybridRetrievalResult(query_id_for(query, plan, modalities, 5, None), plan.index_fingerprint, plan.document_manifest_hash, modalities, 5, None, (hit,), (), "rrf", (0.5, 0.5), {"text": {"candidate_pool_size": 1}}, (), 0.0, query_text=query)
    formatter = ContextFormatter(encoder=Encoder())

    class Client:
        def chat(self, **request):
            return {"message": {"content": "supported [T1]"}}

    generation = LocalGenerationAdapter(client_factory=lambda _: Client(), encoder=Encoder())
    return plan, query, result, formatter, generation


def test_runner_completes_and_aggregates_four_metrics(tmp_path):
    plan, query, result, formatter, generation = setup_chain(tmp_path)
    suite = BenchmarkSuite("bench", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5, required_pages=("3",)),))
    runner = EvaluationRunner(plan, lambda case, supplied_plan: result, formatter, generation)
    output = runner.run(suite)
    assert output.outcomes[0].status == "succeeded"
    assert output.traces[0].status == "succeeded"
    assert {item.metric_id for item in output.report.observations} == {"M3", "M5", "M9", "M16"}
    assert output.report.aggregates[1].value == 100.0
    assert output.report.aggregates[1].eligible_count == 1


def test_runner_continues_after_retrieval_failure(tmp_path):
    plan, query, result, formatter, generation = setup_chain(tmp_path)
    second = BenchmarkCase("case-2", "another question", ("text",))
    suite = BenchmarkSuite("bench", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5), BenchmarkCase("case-2", second.query, ("text",), text_k=5)))
    calls = []

    def retrieve(case, supplied_plan):
        calls.append(case.case_id)
        if case.case_id == "case-1":
            raise RuntimeError("private server detail")
        return result.__class__(query_id_for(second.query, plan, ("text",), 5, None), result.index_fingerprint, result.document_manifest_hash, ("text",), 5, None, result.text_hits, (), result.fusion_strategy, result.effective_weights, result.diagnostics, result.modality_timings, result.total_elapsed_seconds, query_text=second.query)

    output = EvaluationRunner(plan, retrieve, formatter, generation).run(suite)
    assert calls == ["case-1", "case-2"]
    assert output.outcomes[0].status == "failed"
    assert output.outcomes[0].error_code == "retrieval_failed"
    assert output.outcomes[1].status == "succeeded"
    assert all("private server detail" not in repr(trace) for trace in output.traces)


def test_duplicate_query_identity_is_rejected(tmp_path):
    plan, query, _, formatter, generation = setup_chain(tmp_path)
    suite = BenchmarkSuite("bench", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5), BenchmarkCase("case-2", query, ("text",), text_k=5)))
    with pytest.raises(EvaluationRunnerError):
        EvaluationRunner(plan, lambda *_: None, formatter, generation).run(suite)
