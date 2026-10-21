from __future__ import annotations

from pathlib import Path

import pytest

from backend.config import ResolvedRunConfig
from backend.engine.context import ContextFormatter
from backend.engine.generation import GenerationResult, LocalGenerationAdapter
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


@pytest.mark.parametrize("error_code", [None, "BadCode", "9starts", "bad code", "x" * 65])
def test_generation_exceptions_are_failed_generation_stage_with_safe_m16(tmp_path, error_code):
    plan, query, result, formatter, _ = setup_chain(tmp_path)

    class FailingGeneration:
        def generate(self, context, supplied_plan):
            error = RuntimeError("private client details")
            if error_code is not None:
                error.error_code = error_code
            raise error

    suite = BenchmarkSuite("bench", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5),))
    output = EvaluationRunner(plan, lambda *_: result, formatter, FailingGeneration()).run(suite)
    outcome = output.outcomes[0]
    assert outcome.status == "failed"
    assert outcome.reached_stage == "generation"
    assert output.traces[0].failed_stage == "generation"
    assert outcome.error_code == "generation_failed"
    m16 = next(item for item in output.report.observations if item.metric_id == "M16")
    assert m16.status == "failed" and m16.value is None
    assert m16.model_identifier == plan.resolved_config().llm_model
    assert all("private client details" not in repr(item) for item in output.traces)


def test_invalid_generation_result_is_not_counted_or_leaked(tmp_path):
    plan, query, result, formatter, _ = setup_chain(tmp_path)

    class InvalidGeneration:
        def generate(self, context, supplied_plan):
            return object()

    suite = BenchmarkSuite("bench", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5),))
    output = EvaluationRunner(plan, lambda *_: result, formatter, InvalidGeneration()).run(suite)
    assert output.traces[0].failed_stage == "generation"
    assert output.traces[0].generation is None
    assert next(item for item in output.report.observations if item.metric_id == "M16").status == "failed"


def test_returned_failed_generation_result_is_explicit_and_model_is_recorded(tmp_path):
    plan, query, result, formatter, _ = setup_chain(tmp_path)

    class FailedGeneration:
        def generate(self, context, supplied_plan):
            return GenerationResult(context.query_id, context.query_text, context.index_fingerprint, context.document_manifest_hash, context.run_fingerprint, context.context_fingerprint, supplied_plan.resolved_config().llm_model, "failed", None, None, 1, context.text_context_tokens, 10, 0, 0, (), 0.2, "a" * 64)

    suite = BenchmarkSuite("bench", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5),))
    output = EvaluationRunner(plan, lambda *_: result, formatter, FailedGeneration()).run(suite)
    assert output.traces[0].failed_stage == "generation"
    m16 = next(item for item in output.report.observations if item.metric_id == "M16")
    assert m16.status == "failed" and m16.model_identifier == plan.resolved_config().llm_model
    assert next(item for item in output.report.aggregates if item.metric_id == "M16").model_identifier is None


def test_context_output_is_validated_before_generation(tmp_path):
    plan, query, result, _, generation = setup_chain(tmp_path)

    class BadFormatter:
        def format(self, retrieval, supplied_plan):
            return object()

    class ShouldNotRun:
        def __init__(self):
            self.called = False
        def generate(self, context, supplied_plan):
            self.called = True
            raise AssertionError("generation should not run")

    adapter = ShouldNotRun()
    suite = BenchmarkSuite("bench", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5),))
    output = EvaluationRunner(plan, lambda *_: result, BadFormatter(), adapter).run(suite)
    assert not adapter.called
    assert output.outcomes[0].reached_stage == "context"
    assert output.traces[0].failed_stage == "context"
    assert next(item for item in output.report.observations if item.metric_id == "M16").reason_code == "generation_not_reached"
