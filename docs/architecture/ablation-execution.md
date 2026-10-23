# Six-variant ablation execution

`AblationRunner` is the in-memory execution coordinator for the six validated
ablation plans. It consumes an `AblationPlan`, one validated `BenchmarkSuite`,
the existing build report and already-opened pipeline components. It does not
create an index, reset a collection, persist a result, or start a job.

The coordinator runs the variants sequentially in their canonical order:

1. `1_Dense_only`
2. `2_BM25_only`
3. `3_Hybrid_static`
4. `4_Hybrid_adaptive`
5. `5_Hybrid_reranker`
6. `6_Full_pipeline`

For each variant it creates an `EvaluationRunner` with that variant's verified
`RunPlan`. Its retrieval callback delegates to `retrieve_for_plan()`, passing
the case query, requested modalities, effective case or plan top-k values,
the shared storage root and build-report path, and the injected adapters.
Context formatting, generation, tracing and M1--M19 metric calculation remain
owned by the existing runner.

## Results and failure isolation

`AblationVariantResult` retains a complete `EvaluationRunResult` whenever the
runner produced a valid report. A variant is `succeeded` when every case
succeeded, `partial` when at least one case succeeded and another failed, and
`failed` when no case succeeded or the runner failed before producing a valid
result. A runner-level exception is represented by the stable code
`ablation_variant_failed`; exception text is never copied into the result.

`AblationRunResult` preserves the shared document-manifest and index
identities, benchmark identity, all six ordered variant results, and one
`AblationMetricComparison` per registered metric. A comparison retains the
original `MetricAggregate` objects, including values, counts, units, status,
reference provenance and implementation versions. Missing variants or
aggregates are `incomplete`. Inconsistent semantic metadata is
`not_comparable`.

M4 is always `not_comparable` when all six aggregates are present because
semantic similarity, BM25 relevance and fused relevance are different score
families. Its raw aggregate values remain available for inspection; no winner
or cross-mode normalized score is invented. A computed zero remains a value,
while skipped, failed, unavailable and missing values remain non-numeric.

The coordinator is intentionally synchronous and in memory. It is not a
persistence layer, evaluation queue, API, dashboard, or authenticity/audit
store. The existing content fingerprints provide consistency checks, not
proof of execution or tamper resistance.

Completed ablation results can be passed through the canonical result
serialization contract in `result-serialization.md`. Serialization preserves
all six nested reports and comparison rows but does not persist them or prove
who created them. Deserialization requires the accepted validated ablation
plan so run, manifest and index provenance cannot be silently substituted.

The optional `ExperimentArtifactStore` can publish a completed result after
that serialization round trip. It does not execute variants, rebuild indexes,
or provide a database or job queue.
