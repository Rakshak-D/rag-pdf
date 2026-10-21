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

    def __init__(self, run_plan: RunPlan, retrieval: Callable[[BenchmarkCase, RunPlan], HybridRetrievalResult], context_formatter: ContextFormatter, generation_adapter: LocalGenerationAdapter, *, clock: Callable[[], float] = perf_counter) -> None:
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
        self._clock = clock

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
        for hit in (*result.text_hits, *result.image_hits):
            metadata = hit.metadata if isinstance(hit.metadata, dict) else {}
            value = metadata.get("page_num")
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                continue
            page = str(value).strip()
            if page:
                pages.add(page)
        return pages

    def _metrics(self, trace_id: str, case: BenchmarkCase, retrieval: HybridRetrievalResult | None, context: EvidenceContext | None, generation: GenerationResult | None, failure: TraceFailure | None, *, generation_attempted: bool = False, model_identifier: str | None = None) -> list[MetricObservation]:
        observations: list[MetricObservation] = []
        retrieval_available = retrieval is not None and (failure is None or failure.stage != "retrieval")
        if retrieval_available:
            observations.append(_observation(trace_id, "M3", "Retrieval latency", "computed", retrieval.total_elapsed_seconds, "seconds", "lower_is_better", "no_reference", "legacy-m3-v1", note="Existing HybridRetrievalResult.total_elapsed_seconds."))
        else:
            observations.append(_observation(trace_id, "M3", "Retrieval latency", "failed", None, "seconds", "lower_is_better", "no_reference", "legacy-m3-v1", "retrieval_failed", "Retrieval did not produce a result."))
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
        return observations

    @staticmethod
    def _aggregate(metric_id: str, observations: Sequence[MetricObservation], *, model_identifier: str | None = None) -> MetricAggregate:
        related = [item for item in observations if item.metric_id == metric_id]
        eligible = [item.value for item in related if item.status == "computed"]
        skipped = sum(item.status == "skipped" for item in related)
        failed = sum(item.status == "failed" for item in related)
        unavailable = sum(item.status == "unavailable" for item in related)
        value = sum(eligible) / len(eligible) if eligible else None
        status = "computed" if eligible and not (skipped or failed or unavailable) else "partial" if eligible else "no_eligible_values"
        sample = related[0]
        return MetricAggregate(metric_id, sample.metric_name, status, value, sample.unit, sample.optimization_direction, sample.reference_kind, sample.implementation_version, len(eligible), skipped, failed, unavailable, model_identifier=model_identifier, methodology_note=sample.methodology_note)

    def run(self, suite: BenchmarkSuite) -> EvaluationRunResult:
        cases = self._validate_suite(suite)
        traces: list[QueryTrace] = []
        observations: list[MetricObservation] = []
        outcomes: list[EvaluationCaseOutcome] = []
        config = self._plan.resolved_config()
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
                observations.extend(self._metrics(trace.trace_id, case, None, None, None, failure, model_identifier=None))
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
                observations.extend(self._metrics(trace.trace_id, case, retrieval_result, None, None, failure, model_identifier=None))
                traces.append(trace); outcomes.append(EvaluationCaseOutcome(case.case_id, "failed", "context", trace.trace_id, failure.error_code)); continue
            generation_attempted = True
            reached = "generation"
            try:
                generation = self._generation.generate(context, self._plan)
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
            observations.extend(self._metrics(trace.trace_id, case, retrieval_result, context, generation, failure, generation_attempted=generation_attempted, model_identifier=config.llm_model if generation_attempted else None))
            traces.append(trace)
            outcomes.append(EvaluationCaseOutcome(case.case_id, "succeeded" if trace.status == "succeeded" else "failed", reached, trace.trace_id, trace.error_code))
        m16_model = next((item.model_identifier for item in observations if item.metric_id == "M16" and item.status == "computed" and item.model_identifier is not None), None)
        aggregates = [self._aggregate(metric_id, observations, model_identifier=m16_model if metric_id == "M16" else None) for metric_id in ("M3", "M5", "M9", "M16")]
        report = build_evaluation_report(traces, observations, aggregates, run_fingerprint=self._plan.run_fingerprint, benchmark_id=suite.benchmark_id, benchmark_version=suite.benchmark_version)
        return EvaluationRunResult(report, tuple(traces), tuple(outcomes))
