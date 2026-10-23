"""Pure experiment planning models."""

from .run_plan import (
    RUN_PLAN_SCHEMA_VERSION,
    RunPlan,
    RunPlanIntegrityError,
    build_run_plan,
)
from .tracing import (
    QueryTrace,
    TraceContext,
    TraceEvidence,
    TraceFailure,
    TraceGeneration,
    TraceStageTiming,
    TraceContractError,
    build_query_trace,
    build_failed_query_trace,
)
from .evaluation import (
    EVALUATION_SCHEMA_VERSION,
    EvaluationContractError,
    EvaluationReport,
    MetricAggregate,
    MetricObservation,
    build_evaluation_report,
)
from .benchmark import BenchmarkCase, BenchmarkSuite, BenchmarkContractError
from .evaluation_runner import EvaluationCaseOutcome, EvaluationRunResult, EvaluationRunner, EvaluationRunnerError
from .ablation_plans import (
    ABLATION_SCHEMA_VERSION,
    AblationPlan,
    AblationPlanError,
    AblationVariantPlan,
    build_ablation_plan,
    create_ablation_plan,
    load_ablation_plan,
)

__all__ = [
    "RUN_PLAN_SCHEMA_VERSION",
    "RunPlan",
    "RunPlanIntegrityError",
    "build_run_plan",
    "QueryTrace",
    "TraceContext",
    "TraceEvidence",
    "TraceFailure",
    "TraceGeneration",
    "TraceStageTiming",
    "TraceContractError",
    "build_query_trace",
    "build_failed_query_trace",
    "EVALUATION_SCHEMA_VERSION",
    "EvaluationContractError",
    "EvaluationReport",
    "MetricAggregate",
    "MetricObservation",
    "build_evaluation_report",
    "BenchmarkCase",
    "BenchmarkSuite",
    "BenchmarkContractError",
    "EvaluationCaseOutcome",
    "EvaluationRunResult",
    "EvaluationRunner",
    "EvaluationRunnerError",
    "ABLATION_SCHEMA_VERSION",
    "AblationPlan",
    "AblationPlanError",
    "AblationVariantPlan",
    "build_ablation_plan",
    "create_ablation_plan",
    "load_ablation_plan",
]
