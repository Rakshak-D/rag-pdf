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


def test_runner_completes_and_aggregates_audited_metrics(tmp_path):
    plan, query, result, formatter, generation = setup_chain(tmp_path)
    suite = BenchmarkSuite("bench", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5, required_pages=("3",)),))
    runner = EvaluationRunner(plan, lambda case, supplied_plan: result, formatter, generation)
    output = runner.run(suite)
    assert output.outcomes[0].status == "succeeded"
    assert output.traces[0].status == "succeeded"
    assert {item.metric_id for item in output.report.observations} == {"M3", "M5", "M6", "M7", "M8", "M9", "M10", "M11", "M12", "M13", "M14", "M15", "M16", "M17"}
    rouge1 = next(item for item in output.report.observations if item.metric_id == "M6")
    assert rouge1.status == "computed" and rouge1.reference_kind == "context_pseudoreference"
    assert "supported [T1]" not in repr(output.report)
    assert output.report.aggregates[1].value == 100.0
    assert output.report.aggregates[1].eligible_count == 1


def test_nltk_metrics_require_explicit_ground_truth_without_context_fallback(tmp_path):
    plan, query, result, formatter, generation = setup_chain(tmp_path)
    suite = BenchmarkSuite("bench", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5, reference_text="short"),))
    output = EvaluationRunner(plan, lambda *_: result, formatter, generation).run(suite)
    for metric_id in ("M10", "M11"):
        observation = next(item for item in output.report.observations if item.metric_id == metric_id)
        assert observation.reference_kind == "ground_truth"
        assert observation.status in {"computed", "unavailable", "failed"}
        assert observation.reason_code != "missing_ground_truth"


def test_nltk_metrics_missing_reference_are_skipped_even_with_context(tmp_path):
    plan, query, result, formatter, generation = setup_chain(tmp_path)
    suite = BenchmarkSuite("bench", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5),))
    output = EvaluationRunner(plan, lambda *_: result, formatter, generation).run(suite)
    for metric_id in ("M10", "M11"):
        observation = next(item for item in output.report.observations if item.metric_id == metric_id)
        assert observation.status == "skipped"
        assert observation.value is None
        assert observation.reference_kind == "no_reference"
        assert observation.reason_code == "missing_ground_truth"


def test_bertscore_missing_reference_is_skipped_without_model_loading(tmp_path):
    plan, query, result, formatter, generation = setup_chain(tmp_path)
    suite = BenchmarkSuite("bench", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5),))
    output = EvaluationRunner(plan, lambda *_: result, formatter, generation).run(suite)
    observation = next(item for item in output.report.observations if item.metric_id == "M12")
    assert observation.status == "skipped"
    assert observation.reference_kind == "no_reference"
    assert observation.value is None
    assert observation.model_identifier is None


def test_fcd_without_injected_embedder_is_unavailable(tmp_path):
    plan, query, result, formatter, generation = setup_chain(tmp_path)
    suite = BenchmarkSuite("bench", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5),))
    output = EvaluationRunner(plan, lambda *_: result, formatter, generation).run(suite)
    observation = next(item for item in output.report.observations if item.metric_id == "M13")
    assert observation.status == "unavailable"
    assert observation.reason_code == "fcd_embedder_unavailable"
    assert observation.value is None


def test_fcd_injected_embedder_is_rounded_and_aggregated(tmp_path):
    plan, query, result, formatter, generation = setup_chain(tmp_path)

    class Embedder:
        model_name = "text-test"
        def embed_query(self, text):
            return [1.0, 0.0] if text.startswith("supported") else [0.6, 0.8]

    suite = BenchmarkSuite("bench", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5),))
    output = EvaluationRunner(plan, lambda *_: result, formatter, generation, text_embedder=Embedder()).run(suite)
    observation = next(item for item in output.report.observations if item.metric_id == "M13")
    aggregate = next(item for item in output.report.aggregates if item.metric_id == "M13")
    assert observation.status == "computed" and observation.value == 40.0
    assert aggregate.value == 40.0 and aggregate.eligible_count == 1
    assert aggregate.model_identifier == "text-test"


def test_faithfulness_is_lexical_and_has_no_model_identifier(tmp_path):
    plan, query, result, formatter, generation = setup_chain(tmp_path)
    suite = BenchmarkSuite("bench", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5),))
    output = EvaluationRunner(plan, lambda *_: result, formatter, generation).run(suite)
    observation = next(item for item in output.report.observations if item.metric_id == "M14")
    aggregate = next(item for item in output.report.aggregates if item.metric_id == "M14")
    assert observation.status == "computed"
    assert observation.reference_kind == "retrieved_context"
    assert observation.model_identifier is None
    assert aggregate.model_identifier is None
    assert "factuality" in (observation.methodology_note or "").lower()


def test_gt_coverage_uses_lexical_fallback_without_embedder_and_m13_stays_unavailable(tmp_path):
    plan, query, result, formatter, generation = setup_chain(tmp_path)
    suite = BenchmarkSuite("bench", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5, reference_text="The answer is in the evidence."),))
    output = EvaluationRunner(plan, lambda *_: result, formatter, generation).run(suite)
    m15 = next(item for item in output.report.observations if item.metric_id == "M15")
    m13 = next(item for item in output.report.observations if item.metric_id == "M13")
    assert m15.status == "computed" and m15.reference_kind == "ground_truth" and m15.model_identifier is None
    assert m13.status == "unavailable"


def test_m5_uses_text_pages_and_page_fallback_only(tmp_path):
    plan, query, result, formatter, generation = setup_chain(tmp_path)
    image = HybridHit("image-1", 1, 0.1, {"page_num": 99}, caption="image", sources=("semantic",))
    text_with_fallback = HybridHit("chunk-2", 1, 0.2, {"page": 7}, document="page fallback", sources=("semantic",))
    changed = result.__class__(result.query_id, result.index_fingerprint, result.document_manifest_hash, result.modalities, result.effective_text_k, result.effective_image_k, (text_with_fallback,), (image,), result.fusion_strategy, result.effective_weights, result.diagnostics, result.modality_timings, result.total_elapsed_seconds, query_text=query)
    suite = BenchmarkSuite("bench", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5, required_pages=("7",)),))
    output = EvaluationRunner(plan, lambda *_: changed, formatter, generation).run(suite)
    assert next(item for item in output.report.observations if item.metric_id == "M5").value == 100.0
    suite_image_page = BenchmarkSuite("bench2", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5, required_pages=("99",)),))
    output_image_page = EvaluationRunner(plan, lambda *_: changed, formatter, generation).run(suite_image_page)
    assert next(item for item in output_image_page.report.observations if item.metric_id == "M5").value == 0.0


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


def test_m17_times_only_the_generation_invocation_and_uses_harmonic_aggregate(tmp_path):
    plan, query, result, formatter, generation = setup_chain(tmp_path)
    second_query = "What is the second finding?"
    second = result.__class__(query_id_for(second_query, plan, ("text",), 5, None), result.index_fingerprint, result.document_manifest_hash, ("text",), 5, None, result.text_hits, (), result.fusion_strategy, result.effective_weights, result.diagnostics, result.modality_timings, result.total_elapsed_seconds, query_text=second_query)
    suite = BenchmarkSuite("bench", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5), BenchmarkCase("case-2", second_query, ("text",), text_k=5)))
    ticks = iter((10.0, 12.0, 20.0, 24.0))
    output = EvaluationRunner(plan, lambda case, _: result if case.case_id == "case-1" else second, formatter, generation, clock=lambda: next(ticks)).run(suite)
    values = [item.value for item in output.report.observations if item.metric_id == "M17"]
    aggregate = next(item for item in output.report.aggregates if item.metric_id == "M17")
    assert values == [0.5, 0.25]
    assert aggregate.value == pytest.approx(2.0 / 6.0)
    assert aggregate.reference_kind == "not_applicable"
    assert aggregate.model_identifier is None


def test_m17_clock_end_is_sampled_immediately_after_generation(tmp_path):
    plan, query, result, formatter, _ = setup_chain(tmp_path)
    events = []

    class Generation:
        def generate(self, context, supplied_plan):
            events.append("generate")
            return LocalGenerationAdapter(client_factory=lambda _: None, encoder=Encoder()).generate(context, supplied_plan)

    ticks = iter((1.0, 3.0))

    def clock():
        events.append("clock")
        return next(ticks)

    suite = BenchmarkSuite("bench", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5),))
    EvaluationRunner(plan, lambda *_: result, formatter, Generation(), clock=clock).run(suite)
    assert events[:3] == ["clock", "generate", "clock"]


def test_m17_times_failed_generation_but_not_retrieval_or_context(tmp_path):
    plan, query, result, formatter, _ = setup_chain(tmp_path)

    class FailingGeneration:
        def generate(self, context, supplied_plan):
            raise RuntimeError("private detail")

    ticks = iter((100.0, 102.0))
    suite = BenchmarkSuite("bench", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5),))
    output = EvaluationRunner(plan, lambda *_: result, formatter, FailingGeneration(), clock=lambda: next(ticks)).run(suite)
    m17 = next(item for item in output.report.observations if item.metric_id == "M17")
    assert m17.status == "computed" and m17.value == 0.5
    assert output.outcomes[0].reached_stage == "generation"


def test_m17_skips_when_generation_is_not_reached(tmp_path):
    plan, query, result, formatter, generation = setup_chain(tmp_path)
    suite = BenchmarkSuite("bench", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5),))
    output = EvaluationRunner(plan, lambda *_: (_ for _ in ()).throw(RuntimeError("no retrieval")), formatter, generation).run(suite)
    m17 = next(item for item in output.report.observations if item.metric_id == "M17")
    assert m17.status == "skipped" and m17.reason_code == "generation_not_reached" and m17.value is None
