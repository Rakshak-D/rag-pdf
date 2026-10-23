# Experiment execution service

`ExperimentService` is the synchronous application boundary between validated
plans, benchmark suites, pipeline runners, immutable artifacts and the SQLite
experiment catalog. Construction only validates injected components and paths;
it does not open an index or load a model.

`run_evaluation()` verifies the `RunPlan`, benchmark identity and query
identities before constructing an `EvaluationRunner`. Its retrieval callback
delegates to `retrieve_for_plan()` and applies the same case-specific top-k
resolution as the runner. The returned traces, outcomes, fingerprints and
benchmark order are validated before `ExperimentCatalog.save_evaluation_result`
publishes/registers the result.

`run_ablation()` performs the same preflight for a validated six-variant
`AblationPlan`, then delegates all variant sequencing and failure isolation to
`AblationRunner`. It does not rebuild or reset the shared index and validates
the returned variant identities, nested reports, comparisons and shared
manifest/index provenance before plan-bound catalog registration.

Valid evaluation or ablation results with failed cases remain inspectable and
persistable. A runner-level or persistence failure produces a stable service
error code and never fabricates a successful artifact or silently reruns work.
If JSON publication succeeds before catalog registration fails, the existing
catalog recovery operations can register the validated artifact later.

Read methods delegate directly to the catalog for artifact lookup, filtering,
metric aggregates, comparisons and plan-bound result loading. The service adds
no cache, serialization format, SQL access, HTTP endpoint, queue or background
job; orchestration remains synchronous and in memory apart from the existing
artifact and catalog writes.

The optional HTTP boundary is implemented separately in Starlette and injects
this service; the service itself remains unaware of HTTP, bearer tokens and
ASGI lifecycle concerns. See `http-api.md` for route and deployment details.
# Local job integration

Long-running callers can place the same validated inputs behind the bounded local job manager. The manager delegates back to this synchronous service and does not duplicate execution or persistence logic.
