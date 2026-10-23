"""Bounded synchronous evaluation over supplied in-memory pipeline components."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from time import perf_counter
from collections.abc import Callable, Sequence
from typing import Literal

from backend.engine.context import ContextFormatter, EvidenceContext
from backend.engine.generation import GenerationResult, LocalGenerationAdapter
from backend.engine.retrieval import HybridRetrievalResult, query_id_for
from backend.experiments.benchmark import BenchmarkCase, BenchmarkSuite
from backend.experiments.evaluation import MetricAggregate, MetricObservation, EvaluationReport, build_evaluation_report
from backend.experiments.run_plan import RunPlan
from backend.experiments.rouge_metrics import RougeScorerAdapter, RougeBatchResult, select_reference
from backend.experiments.nltk_metrics import NltkMetricResult, NltkMetricsAdapter
from backend.experiments.bertscore_metrics import BertScoreAdapter, BertScoreResult
from backend.experiments.fcd_metrics import FcdMetricAdapter, FcdResult
from backend.experiments.faithfulness_metrics import FaithfulnessMetricAdapter, FaithfulnessResult
from backend.experiments.gt_coverage_metrics import GtCoverageMetricAdapter, GtCoverageResult
from backend.experiments.throughput_metrics import ThroughputMetricAdapter
from backend.experiments.cpu_metrics import CpuUsageMetricAdapter, CpuUsageResult, ProcessCpuMonitor
from backend.experiments.memory_metrics import ProcessRamMonitor, RamUsageMetricAdapter, RamUsageResult
from backend.experiments.retrieval_similarity_metrics import RetrievalSimilarityMetricAdapter
from backend.experiments.embedding_time_metrics import EmbeddingTimeMetricAdapter
from backend.experiments.index_size_metrics import IndexSizeMetricAdapter
from backend.experiments.tracing import TraceFailure, QueryTrace, build_failed_query_trace, build_query_trace


class EvaluationRunnerError(ValueError):
    """Raised when runner configuration is invalid."""


@dataclass(frozen=True)
class EvaluationCaseOutcome:
    case_id: str
    status: Literal["succeeded", "failed"]
    reached_stage: str
    trace_id: str
    error_code: str | None = None


@dataclass(frozen=True)
class EvaluationRunResult:
    report: EvaluationReport
    traces: tuple[QueryTrace, ...]
    outcomes: tuple[EvaluationCaseOutcome, ...]


_STABLE_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


def _safe_code(value: object, default: str) -> str:
    return value if isinstance(value, str) and _STABLE_CODE_RE.fullmatch(value) else default


def _stable_error(exc: BaseException, default: str) -> str:
    return _safe_code(getattr(exc, "error_code", None), default)


def _failure(stage: str, code: str, exc: BaseException | None = None, *, attempts: int | None = None, elapsed: float | None = None) -> TraceFailure:
    safe_attempts = attempts if isinstance(attempts, int) and not isinstance(attempts, bool) and attempts >= 0 else None
    safe_elapsed = elapsed if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool) and math.isfinite(float(elapsed)) and elapsed >= 0 else None
    return TraceFailure(stage, _stable_error(exc, code) if exc else _safe_code(code, "build_failed"), safe_attempts, safe_elapsed)


def _observation(trace_id: str, metric_id: str, name: str, status: str, value: float | None, unit: str, direction: str, reference: str, version: str, reason: str | None = None, note: str = "", *, model_identifier: str | None = None) -> MetricObservation:
    return MetricObservation(trace_id, metric_id, name, status, value, unit, direction, reference, version, model_identifier=model_identifier, reason_code=reason, methodology_note=note)


class EvaluationRunner:
    """Run at most 100 supplied benchmark cases synchronously and in memory."""

    def __init__(self, run_plan: RunPlan, retrieval: Callable[[BenchmarkCase, RunPlan], HybridRetrievalResult], context_formatter: ContextFormatter, generation_adapter: LocalGenerationAdapter, *, text_embedder: object | None = None, clock: Callable[[], float] = perf_counter, cpu_monitor_factory=None, ram_monitor_factory=None) -> None:
        if not isinstance(run_plan, RunPlan) or not callable(retrieval) or not hasattr(context_formatter, "format") or not hasattr(generation_adapter, "generate"):
            raise EvaluationRunnerError("runner requires a RunPlan and callable pipeline components")
        try:
            run_plan.verify_integrity()
        except Exception as exc:
            raise EvaluationRunnerError("RunPlan integrity verification failed") from exc
        self._plan = run_plan
        self._retrieval = retrieval
        self._context_formatter = context_formatter
        self._generation = generation_adapter
        self._text_embedder = text_embedder
        self._clock = clock
        self._cpu_monitor_factory = cpu_monitor_factory or (lambda: ProcessCpuMonitor())
        self._ram_monitor_factory = ram_monitor_factory or (lambda: ProcessRamMonitor())

    def _validate_suite(self, suite: BenchmarkSuite) -> tuple[BenchmarkCase, ...]:
        if not isinstance(suite, BenchmarkSuite):
            raise EvaluationRunnerError("run requires a BenchmarkSuite")
        config = self._plan.resolved_config()
        cases = tuple(suite.cases)
        identities = []
        for case in cases:
            text_k = case.text_k if "text" in case.modalities else None
            image_k = case.image_k if "image" in case.modalities else None
            text_k = text_k if text_k is not None else (config.text_k if "text" in case.modalities else None)
            image_k = image_k if image_k is not None else (config.image_k if "image" in case.modalities else None)
            identities.append(query_id_for(case.query, self._plan, case.modalities, text_k, image_k))
        if len(set(identities)) != len(identities):
            raise EvaluationRunnerError("benchmark contains duplicate query identities for this RunPlan")
        return cases

    @staticmethod
    def _pages(result: HybridRetrievalResult) -> set[str]:
        pages: set[str] = set()
        for hit in result.text_hits:
            metadata = hit.metadata if isinstance(hit.metadata, dict) else {}
            value = metadata.get("page_num") or metadata.get("page")
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                continue
            page = str(value).strip()
            if page:
                pages.add(page)
        return pages

    @staticmethod
    def _rouge_metrics(trace_id: str, case: BenchmarkCase, context: EvidenceContext | None, generation: GenerationResult | None, config: object, scorer: RougeScorerAdapter) -> list[MetricObservation]:
        names = (("M6", "ROUGE-1", "legacy-m6-v1", 0), ("M7", "ROUGE-2", "legacy-m7-v1", 1), ("M8", "ROUGE-L", "legacy-m8-v1", 2))
        response = generation.response.strip() if generation is not None and generation.status == "succeeded" and isinstance(generation.response, str) else ""
        if not response:
            reason = "generation_failed" if generation is not None and generation.status == "failed" else "generation_not_reached"
            result = RougeBatchResult("skipped", "no_reference", reason_code=reason)
        else:
            selection = select_reference(case.reference_text, context.text_context if context is not None else "", top_k_chunks=config.rouge_top_k_chunks, max_text_chunks=config.max_text_chunks)
            if selection.text is None:
                result = RougeBatchResult("skipped", selection.kind, reason_code="no_reference")
            else:
                result = scorer.score(selection.text, response, selection.kind)
        observations: list[MetricObservation] = []
        for metric_id, name, version, index in names:
            value = result.values[index] if result.values is not None else None
            note = "Stemmed ROUGE F-measure against a sufficiently long ground-truth answer or capped context pseudo-reference."
            observations.append(_observation(trace_id, metric_id, name, result.status, value, "f-measure", "higher_is_better", result.reference_kind, version, result.reason_code, note))
        return observations

    @staticmethod
    def _nltk_metrics(trace_id: str, case: BenchmarkCase, generation: GenerationResult | None, scorer: NltkMetricsAdapter) -> list[MetricObservation]:
        reference = case.reference_text.strip() if isinstance(case.reference_text, str) else ""
        if not reference:
            bleu = meteor = NltkMetricResult("skipped", "no_reference", reason_code="missing_ground_truth")
        elif generation is None or generation.status != "succeeded" or not isinstance(generation.response, str) or not generation.response.strip():
            reason = "generation_failed" if generation is not None and generation.status == "failed" else "generation_not_reached"
            bleu = meteor = NltkMetricResult("skipped", "ground_truth", reason_code=reason)
        else:
            bleu = scorer.score_bleu(reference, generation.response, "ground_truth")
            meteor = scorer.score_meteor(reference, generation.response, "ground_truth")
        values = (("M10", "BLEU", "legacy-m10-v1", bleu), ("M11", "METEOR", "legacy-m11-v1", meteor))
        return [_observation(trace_id, metric_id, name, result.status, result.value, "score", "higher_is_better", result.reference_kind, version, result.reason_code, "Lower-case word tokens with one explicit ground-truth reference; no context fallback.") for metric_id, name, version, result in values]

    @staticmethod
    def _bertscore_metric(trace_id: str, case: BenchmarkCase, generation: GenerationResult | None, scorer: BertScoreAdapter) -> MetricObservation:
        reference = case.reference_text.strip() if isinstance(case.reference_text, str) else ""
        if not reference:
            result = BertScoreResult("skipped", reason_code="missing_ground_truth")
            reference_kind = "no_reference"
        elif generation is None or generation.status != "succeeded" or not isinstance(generation.response, str) or not generation.response.strip():
            result = BertScoreResult("skipped", reason_code="generation_failed" if generation is not None and generation.status == "failed" else "generation_not_reached")
            reference_kind = "ground_truth"
        else:
            result = scorer.score(generation.response, reference)
            reference_kind = "ground_truth"
        return _observation(trace_id, "M12", "BERTScore F1", result.status, result.value, "score", "higher_is_better", reference_kind, "legacy-m12-v1", result.reason_code, "BERTScore F1 with English roberta-large, IDF disabled, and baseline rescaling disabled.", model_identifier=result.model_identifier)

    @staticmethod
    def _fcd_metric(trace_id: str, case: BenchmarkCase, context: EvidenceContext | None, generation: GenerationResult | None, scorer: FcdMetricAdapter) -> MetricObservation:
        if context is None:
            result = FcdResult("skipped", reason_code="context_not_reached")
        elif generation is None:
            result = FcdResult("skipped", reason_code="generation_not_reached")
        elif generation.status == "failed":
            result = FcdResult("skipped", reason_code="generation_failed")
        elif not isinstance(generation.response, str) or not generation.response.strip():
            result = FcdResult("skipped", reason_code="empty_response")
        else:
            result = scorer.score(case.query, context.text_context, generation.response)
        return _observation(trace_id, "M13", "FCD (prompt embedding distance)", result.status, result.value, "points", "lower_is_better", "retrieved_context", "legacy-m13-v1", result.reason_code, "Rounded embedding distance between the generated response snippet and the truncated legacy prompt snippet; not a factuality judgment.", model_identifier=result.model_identifier)

    @staticmethod
    def _faithfulness_metric(trace_id: str, context: EvidenceContext | None, generation: GenerationResult | None, scorer: FaithfulnessMetricAdapter) -> MetricObservation:
        if context is None:
            result = FaithfulnessResult("skipped", reason_code="context_not_reached")
        elif not isinstance(context.text_context, str) or not context.text_context.strip():
            result = FaithfulnessResult("skipped", reason_code="empty_context")
        elif generation is None:
            result = FaithfulnessResult("skipped", reason_code="generation_not_reached")
        elif generation.status == "failed":
            result = FaithfulnessResult("skipped", reason_code="generation_failed")
        elif not isinstance(generation.response, str) or not generation.response.strip():
            result = FaithfulnessResult("skipped", reason_code="empty_response")
        else:
            result = scorer.score(context.text_context, generation.response)
        return _observation(trace_id, "M14", "Faithfulness (term overlap)", result.status, result.value, "percent", "higher_is_better", "retrieved_context", "legacy-m14-v1", result.reason_code, "Lexical term-overlap heuristic; not an entailment or factuality test.")

    @staticmethod
    def _gt_coverage_metric(trace_id: str, case: BenchmarkCase, context: EvidenceContext | None, generation: GenerationResult | None, scorer: GtCoverageMetricAdapter) -> MetricObservation:
        reference = case.reference_text.strip() if isinstance(case.reference_text, str) else ""
        reference_kind = "ground_truth" if reference else "retrieved_context" if context is not None and isinstance(context.text_context, str) and context.text_context.strip() else "no_reference"
        if generation is None:
            result = GtCoverageResult("skipped", reason_code="generation_not_reached")
        elif generation.status == "failed":
            result = GtCoverageResult("skipped", reason_code="generation_failed")
        elif not isinstance(generation.response, str) or not generation.response.strip():
            result = GtCoverageResult("skipped", reason_code="empty_response")
        elif reference:
            result = scorer.score(reference, generation.response, "ground_truth")
        elif context is None or not isinstance(context.text_context, str) or not context.text_context.strip():
            result = GtCoverageResult("skipped", reason_code="no_reference")
        else:
            result = scorer.score(context.text_context, generation.response, "retrieved_context")
        return _observation(trace_id, "M15", "GT Coverage / Context Utilization", result.status, result.value, "percent", "higher_is_better", reference_kind, "legacy-m15-v1", result.reason_code, "Reference coverage using semantic sentence similarity when an embedder is injected, otherwise lexical multiset term-frequency overlap; not a factuality test.", model_identifier=result.model_identifier)

    def _metrics(self, trace_id: str, case: BenchmarkCase, retrieval: HybridRetrievalResult | None, context: EvidenceContext | None, generation: GenerationResult | None, failure: TraceFailure | None, *, generation_attempted: bool = False, generation_elapsed: object | None = None, generation_timing_error: str | None = None, cpu_measurement: object | None = None, ram_measurement: object | None = None, model_identifier: str | None = None, config: object, embedding_time_scorer: EmbeddingTimeMetricAdapter, index_size_scorer: IndexSizeMetricAdapter, rouge_scorer: RougeScorerAdapter, nltk_scorer: NltkMetricsAdapter, bertscore_scorer: BertScoreAdapter, fcd_scorer: FcdMetricAdapter, faithfulness_scorer: FaithfulnessMetricAdapter, gt_coverage_scorer: GtCoverageMetricAdapter, throughput_scorer: ThroughputMetricAdapter, cpu_scorer: CpuUsageMetricAdapter, ram_scorer: RamUsageMetricAdapter, retrieval_similarity_scorer: RetrievalSimilarityMetricAdapter) -> list[MetricObservation]:
        observations: list[MetricObservation] = []
        observations.append(self._embedding_time_metric(trace_id, retrieval, embedding_time_scorer))
        observations.append(self._index_size_metric(trace_id, retrieval, index_size_scorer))
        retrieval_available = retrieval is not None and (failure is None or failure.stage != "retrieval")
        if retrieval_available:
            observations.append(_observation(trace_id, "M3", "Retrieval latency", "computed", retrieval.total_elapsed_seconds, "seconds", "lower_is_better", "no_reference", "legacy-m3-v1", note="Existing HybridRetrievalResult.total_elapsed_seconds."))
        else:
            observations.append(_observation(trace_id, "M3", "Retrieval latency", "failed", None, "seconds", "lower_is_better", "no_reference", "legacy-m3-v1", "retrieval_failed", "Retrieval did not produce a result."))
        observations.append(self._retrieval_similarity_metric(trace_id, retrieval, config.retrieval_mode, retrieval_similarity_scorer))
        if not case.required_pages:
            observations.append(_observation(trace_id, "M5", "Top-k page coverage", "skipped", None, "percent", "higher_is_better", "retrieved_context", "legacy-m5-v1", "no_required_pages", "Coverage is undefined without required pages."))
        elif retrieval is None:
            observations.append(_observation(trace_id, "M5", "Top-k page coverage", "failed", None, "percent", "higher_is_better", "retrieved_context", "legacy-m5-v1", "retrieval_failed", "Coverage requires retrieved pages."))
        else:
            found = len(set(case.required_pages) & self._pages(retrieval))
            observations.append(_observation(trace_id, "M5", "Top-k page coverage", "computed", 100.0 * found / len(case.required_pages), "percent", "higher_is_better", "retrieved_context", "legacy-m5-v1", note="100 times required retrieved pages divided by required pages."))
        if context is not None and context.text_context:
            observations.append(_observation(trace_id, "M9", "Context length", "computed", float(len(context.text_context)), "characters", "descriptive", "retrieved_context", "legacy-m9-v1", note="Character length of non-empty formatted text context."))
        else:
            observations.append(_observation(trace_id, "M9", "Context length", "skipped", None, "characters", "descriptive", "retrieved_context", "legacy-m9-v1", "empty_context" if context is not None else "context_not_reached", "The audited metric excludes empty or missing context."))
        if generation is not None and generation.status == "succeeded":
            observations.append(_observation(trace_id, "M16", "End-to-end generation latency", "computed", generation.elapsed_seconds, "seconds", "lower_is_better", "no_reference", "legacy-m16-v1", note="Existing generation elapsed_seconds measurement.", model_identifier=model_identifier))
        elif generation_attempted:
            reason = failure.error_code if failure is not None else "generation_failed"
            if not _STABLE_CODE_RE.fullmatch(reason):
                reason = "generation_failed"
            observations.append(_observation(trace_id, "M16", "End-to-end generation latency", "failed", None, "seconds", "lower_is_better", "no_reference", "legacy-m16-v1", reason, "Failed generation is excluded from the legacy latency metric.", model_identifier=model_identifier))
        else:
            observations.append(_observation(trace_id, "M16", "End-to-end generation latency", "skipped", None, "seconds", "lower_is_better", "no_reference", "legacy-m16-v1", "generation_not_reached", "Generation was not reached."))
        observations.extend(self._rouge_metrics(trace_id, case, context, generation, config, rouge_scorer))
        observations.extend(self._nltk_metrics(trace_id, case, generation, nltk_scorer))
        observations.append(self._bertscore_metric(trace_id, case, generation, bertscore_scorer))
        observations.append(self._fcd_metric(trace_id, case, context, generation, fcd_scorer))
        observations.append(self._faithfulness_metric(trace_id, context, generation, faithfulness_scorer))
        observations.append(self._gt_coverage_metric(trace_id, case, context, generation, gt_coverage_scorer))
        observations.append(self._throughput_metric(trace_id, generation_attempted, generation_elapsed, generation_timing_error, throughput_scorer))
        observations.append(self._cpu_metric(trace_id, generation_attempted, cpu_measurement, cpu_scorer))
        observations.append(self._ram_metric(trace_id, generation_attempted, ram_measurement, ram_scorer))
        return observations

    @staticmethod
    def _throughput_metric(trace_id: str, generation_attempted: bool, elapsed_seconds: object | None, timing_error: str | None, scorer: ThroughputMetricAdapter) -> MetricObservation:
        result = scorer.score(elapsed_seconds, attempted=generation_attempted, timing_error=timing_error)
        return _observation(
            trace_id,
            "M17",
            "Generation throughput",
            result.status,
            result.value,
            "queries/second",
            "higher_is_better",
            "not_applicable",
            "legacy-m17-v1",
            result.reason_code,
            "Sequential generation-invocation throughput: one divided by the positive measured invocation duration.",
        )

    @staticmethod
    def _cpu_metric(trace_id: str, generation_attempted: bool, measurement: object | None, scorer: CpuUsageMetricAdapter) -> MetricObservation:
        result = scorer.result(measurement, attempted=generation_attempted)
        return _observation(trace_id, "M18", "Process CPU Usage", result.status, result.value, "percent", "lower_is_better", "not_applicable", "legacy-m18-v1", result.reason_code, "Average sampled CPU percentage for the current Python process during generation, normalized by logical CPU count.")

    @staticmethod
    def _ram_metric(trace_id: str, generation_attempted: bool, measurement: object | None, scorer: RamUsageMetricAdapter) -> MetricObservation:
        result = scorer.result(measurement, attempted=generation_attempted)
        return _observation(trace_id, "M19", "Process RAM Usage", result.status, result.value, "GB", "lower_is_better", "not_applicable", "legacy-m19-v1", result.reason_code, "Average sampled RSS for the current Python process during generation, converted from bytes using 1024 ** 3.")

    @staticmethod
    def _retrieval_similarity_metric(trace_id: str, retrieval: HybridRetrievalResult | None, mode: object, scorer: RetrievalSimilarityMetricAdapter) -> MetricObservation:
        result = scorer.score(retrieval, mode)
        note = f"Arithmetic mean of finite {result.signal or 'mode-specific'} hit scores with equal-weight modality blending; semantic, BM25, and fused relevance scores are not directly comparable across modes."
        return _observation(trace_id, "M4", result.metric_name, result.status, result.value, "score", "higher_is_better", "retrieved_context", "legacy-m4-v1", result.reason_code, note)

    @staticmethod
    def _embedding_time_metric(trace_id: str, retrieval: HybridRetrievalResult | None, scorer: EmbeddingTimeMetricAdapter) -> MetricObservation:
        if retrieval is None:
            result = scorer.score(None, None)
        else:
            result = scorer.score(retrieval.text_embedding_seconds, retrieval.image_embedding_seconds)
        return _observation(trace_id, "M1", "Embedding Time", result.status, result.value, "seconds", "lower_is_better", "not_applicable", "legacy-m1-v1", result.reason_code, "Sum of text and image embedding durations from the persisted index-build report; repeated per-case observations share the same build-level source.")

    @staticmethod
    def _index_size_metric(trace_id: str, retrieval: HybridRetrievalResult | None, scorer: IndexSizeMetricAdapter) -> MetricObservation:
        if retrieval is None:
            result = scorer.score(None, None)
        else:
            result = scorer.score(retrieval.text_records_persisted, retrieval.image_records_persisted)
        return _observation(trace_id, "M2", "Index Size", result.status, float(result.value) if result.value is not None else None, "vectors", "descriptive", "not_applicable", "legacy-m2-v1", result.reason_code, "Sum of persisted text and image vector counts from the validated build report; this is not storage bytes or disk usage and repeated cases share the same build-level source.")

    @staticmethod
    def _aggregate(metric_id: str, observations: Sequence[MetricObservation], *, model_identifier: str | None = None) -> MetricAggregate:
        related = [item for item in observations if item.metric_id == metric_id]
        eligible = [item.value for item in related if item.status == "computed"]
        skipped = sum(item.status == "skipped" for item in related)
        failed = sum(item.status == "failed" for item in related)
        unavailable = sum(item.status == "unavailable" for item in related)
        if metric_id == "M17" and eligible:
            try:
                denominator = sum(1.0 / float(item) for item in eligible)
                value = len(eligible) / denominator if math.isfinite(denominator) and denominator > 0 else None
            except (ArithmeticError, TypeError, ValueError, OverflowError):
                value = None
        else:
            value = sum(eligible) / len(eligible) if eligible else None
        status = "computed" if eligible and not (skipped or failed or unavailable) else "partial" if eligible else "no_eligible_values"
        sample = related[0]
        reference_kinds = {item.reference_kind for item in related}
        reference_kind = next(iter(reference_kinds)) if len(reference_kinds) == 1 else "mixed_reference"
        return MetricAggregate(metric_id, sample.metric_name, status, value, sample.unit, sample.optimization_direction, reference_kind, sample.implementation_version, len(eligible), skipped, failed, unavailable, model_identifier=model_identifier, methodology_note=sample.methodology_note)

    @staticmethod
    def _read_generation_clock(clock: Callable[[], float]) -> tuple[object | None, str | None]:
        try:
            value = clock()
        except Exception:
            return None, "generation_timing_failed"
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return value, "invalid_generation_timing"
        try:
            if not math.isfinite(float(value)):
                return value, "invalid_generation_timing"
        except (TypeError, ValueError, OverflowError):
            return value, "invalid_generation_timing"
        return value, None

    def run(self, suite: BenchmarkSuite) -> EvaluationRunResult:
        cases = self._validate_suite(suite)
        traces: list[QueryTrace] = []
        observations: list[MetricObservation] = []
        outcomes: list[EvaluationCaseOutcome] = []
        config = self._plan.resolved_config()
        rouge_scorer = RougeScorerAdapter()
        nltk_scorer = NltkMetricsAdapter()
        bertscore_scorer = BertScoreAdapter()
        fcd_scorer = FcdMetricAdapter(self._text_embedder)
        faithfulness_scorer = FaithfulnessMetricAdapter()
        gt_coverage_scorer = GtCoverageMetricAdapter(self._text_embedder)
        throughput_scorer = ThroughputMetricAdapter()
        cpu_scorer = CpuUsageMetricAdapter()
        ram_scorer = RamUsageMetricAdapter()
        retrieval_similarity_scorer = RetrievalSimilarityMetricAdapter()
        embedding_time_scorer = EmbeddingTimeMetricAdapter()
        index_size_scorer = IndexSizeMetricAdapter()
        for case in cases:
            text_k = case.text_k if "text" in case.modalities else None
            image_k = case.image_k if "image" in case.modalities else None
            text_k = text_k if text_k is not None else (config.text_k if "text" in case.modalities else None)
            image_k = image_k if image_k is not None else (config.image_k if "image" in case.modalities else None)
            retrieval_result = None; context = None; generation = None; failure = None; reached = "retrieval"
            try:
                retrieval_result = self._retrieval(case, self._plan)
                if (
                    not isinstance(retrieval_result, HybridRetrievalResult)
                    or retrieval_result.query_text != case.query
                    or retrieval_result.query_id != query_id_for(case.query, self._plan, retrieval_result.modalities, retrieval_result.effective_text_k, retrieval_result.effective_image_k)
                    or retrieval_result.index_fingerprint != self._plan.index_fingerprint
                    or retrieval_result.document_manifest_hash != self._plan.document_manifest_hash
                ):
                    raise EvaluationRunnerError("retrieval result identity mismatch")
                if retrieval_result.modalities != case.modalities:
                    raise EvaluationRunnerError("retrieval modalities do not match benchmark case")
                if retrieval_result.effective_text_k != text_k or retrieval_result.effective_image_k != image_k:
                    raise EvaluationRunnerError("retrieval limits do not match benchmark case")
            except Exception as exc:
                failure = _failure("retrieval", "retrieval_failed", exc)
                trace = build_failed_query_trace(self._plan, query=case.query, modalities=case.modalities, text_k=text_k, image_k=image_k, failure=failure)
                observations.extend(self._metrics(trace.trace_id, case, None, None, None, failure, model_identifier=None, config=config, embedding_time_scorer=embedding_time_scorer, index_size_scorer=index_size_scorer, rouge_scorer=rouge_scorer, nltk_scorer=nltk_scorer, bertscore_scorer=bertscore_scorer, fcd_scorer=fcd_scorer, faithfulness_scorer=faithfulness_scorer, gt_coverage_scorer=gt_coverage_scorer, throughput_scorer=throughput_scorer, cpu_scorer=cpu_scorer, ram_scorer=ram_scorer, retrieval_similarity_scorer=retrieval_similarity_scorer))
                traces.append(trace); outcomes.append(EvaluationCaseOutcome(case.case_id, "failed", "retrieval", trace.trace_id, failure.error_code)); continue
            try:
                context = self._context_formatter.format(retrieval_result, self._plan)
                if not isinstance(context, EvidenceContext):
                    raise EvaluationRunnerError("context formatter returned an invalid result")
                build_query_trace(retrieval_result, self._plan, context=context)
                reached = "context"
            except Exception as exc:
                failure = _failure("context", "context_failed", exc)
                trace = build_query_trace(retrieval_result, self._plan, failure=failure)
                observations.extend(self._metrics(trace.trace_id, case, retrieval_result, None, None, failure, model_identifier=None, config=config, embedding_time_scorer=embedding_time_scorer, index_size_scorer=index_size_scorer, rouge_scorer=rouge_scorer, nltk_scorer=nltk_scorer, bertscore_scorer=bertscore_scorer, fcd_scorer=fcd_scorer, faithfulness_scorer=faithfulness_scorer, gt_coverage_scorer=gt_coverage_scorer, throughput_scorer=throughput_scorer, cpu_scorer=cpu_scorer, ram_scorer=ram_scorer, retrieval_similarity_scorer=retrieval_similarity_scorer))
                traces.append(trace); outcomes.append(EvaluationCaseOutcome(case.case_id, "failed", "context", trace.trace_id, failure.error_code)); continue
            generation_attempted = True
            reached = "generation"
            cpu_monitor = None
            cpu_monitor_started = False
            cpu_measurement = None
            cpu_start_failed = False
            ram_monitor = None
            ram_monitor_started = False
            ram_measurement = None
            ram_start_failed = False
            try:
                cpu_monitor = self._cpu_monitor_factory()
                if not callable(getattr(cpu_monitor, "start", None)) or not callable(getattr(cpu_monitor, "stop", None)):
                    raise EvaluationRunnerError("cpu monitor has an invalid interface")
                cpu_monitor_started = True
                cpu_monitor.start()
            except Exception:
                cpu_start_failed = True
                cpu_measurement = CpuUsageResult("failed", reason_code="cpu_monitor_start_failed")
            try:
                ram_monitor = self._ram_monitor_factory()
                if not callable(getattr(ram_monitor, "start", None)) or not callable(getattr(ram_monitor, "stop", None)):
                    raise EvaluationRunnerError("ram monitor has an invalid interface")
                ram_monitor_started = True
                ram_monitor.start()
            except Exception:
                ram_start_failed = True
                ram_measurement = RamUsageResult("failed", reason_code="ram_monitor_start_failed")
            generation_start, generation_timing_error = self._read_generation_clock(self._clock)
            generation_end = None
            generation_elapsed = None
            generation_exception = None
            try:
                try:
                    generation = self._generation.generate(context, self._plan)
                except Exception as exc:
                    generation_exception = exc
                finally:
                    generation_end, end_error = self._read_generation_clock(self._clock)
                    if generation_timing_error is None and end_error is not None:
                        generation_timing_error = end_error
                    if cpu_monitor_started:
                        try:
                            stopped_measurement = cpu_monitor.stop()
                            if not cpu_start_failed:
                                cpu_measurement = stopped_measurement
                        except Exception:
                            if not cpu_start_failed:
                                cpu_measurement = CpuUsageResult("failed", reason_code="cpu_monitor_shutdown_failed")
                    if ram_monitor_started:
                        try:
                            stopped_measurement = ram_monitor.stop()
                            if not ram_start_failed:
                                ram_measurement = stopped_measurement
                        except Exception:
                            if not ram_start_failed:
                                ram_measurement = RamUsageResult("failed", reason_code="ram_monitor_shutdown_failed")
                if generation_timing_error is None:
                    try:
                        generation_elapsed = generation_end - generation_start
                    except (TypeError, ValueError):
                        generation_timing_error = "generation_timing_failed"
                if generation_exception is not None:
                    raise generation_exception
                if not isinstance(generation, GenerationResult):
                    raise EvaluationRunnerError("generation returned an invalid result")
                if generation.status == "failed":
                    safe_generation_code = _safe_code(generation.error_code, "generation_failed")
                    if generation.error_code is not None and generation.error_code != safe_generation_code:
                        raise EvaluationRunnerError("generation result error code is invalid")
                    failure = TraceFailure("generation", safe_generation_code, generation.attempts, generation.elapsed_seconds)
                trace = build_query_trace(retrieval_result, self._plan, context=context, generation=generation, failure=failure)
            except Exception as exc:
                generation = None
                failure = _failure("generation", "generation_failed", exc, attempts=getattr(exc, "attempts", None), elapsed=getattr(exc, "elapsed_seconds", None))
                trace = build_query_trace(retrieval_result, self._plan, context=context, failure=failure)
            observations.extend(self._metrics(trace.trace_id, case, retrieval_result, context, generation, failure, generation_attempted=generation_attempted, generation_elapsed=generation_elapsed, generation_timing_error=generation_timing_error, cpu_measurement=cpu_measurement, ram_measurement=ram_measurement, model_identifier=config.llm_model if generation_attempted else None, config=config, embedding_time_scorer=embedding_time_scorer, index_size_scorer=index_size_scorer, rouge_scorer=rouge_scorer, nltk_scorer=nltk_scorer, bertscore_scorer=bertscore_scorer, fcd_scorer=fcd_scorer, faithfulness_scorer=faithfulness_scorer, gt_coverage_scorer=gt_coverage_scorer, throughput_scorer=throughput_scorer, cpu_scorer=cpu_scorer, ram_scorer=ram_scorer, retrieval_similarity_scorer=retrieval_similarity_scorer))
            traces.append(trace)
            outcomes.append(EvaluationCaseOutcome(case.case_id, "succeeded" if trace.status == "succeeded" else "failed", reached, trace.trace_id, trace.error_code))
        m16_model = next((item.model_identifier for item in observations if item.metric_id == "M16" and item.status == "computed" and item.model_identifier is not None), None)
        m12_model = next((item.model_identifier for item in observations if item.metric_id == "M12" and item.status == "computed" and item.model_identifier is not None), None)
        m13_model = next((item.model_identifier for item in observations if item.metric_id == "M13" and item.status == "computed" and item.model_identifier is not None), None)
        m15_model = next((item.model_identifier for item in observations if item.metric_id == "M15" and item.status == "computed" and item.model_identifier is not None), None)
        aggregates = [self._aggregate(metric_id, observations, model_identifier=m16_model if metric_id == "M16" else m12_model if metric_id == "M12" else m13_model if metric_id == "M13" else m15_model if metric_id == "M15" else None) for metric_id in ("M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "M10", "M11", "M12", "M13", "M14", "M15", "M16", "M17", "M18", "M19")]
        report = build_evaluation_report(traces, observations, aggregates, run_fingerprint=self._plan.run_fingerprint, benchmark_id=suite.benchmark_id, benchmark_version=suite.benchmark_version)
        return EvaluationRunResult(report, tuple(traces), tuple(outcomes))
