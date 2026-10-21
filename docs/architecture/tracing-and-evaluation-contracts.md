# Query trace and evaluation contracts

The bounded in-memory `EvaluationRunner` is described in
[`evaluation-runner.md`](evaluation-runner.md). It consumes these contracts
after retrieval, context selection, and generation, and uses the failed-query
trace factory when retrieval does not produce a result. It never fabricates
evidence for an unreached stage.

This phase adds immutable in-memory contracts; it does not execute metrics,
run evaluation jobs, or persist traces and reports.

`build_query_trace()` starts with a verified `HybridRetrievalResult` and its
`RunPlan`. It validates the existing query-ID formula, run/index/manifest
identity, and—when present—the canonical context fingerprint. A trace may stop
after retrieval (`retrieved`), after context selection (`context_ready`), or
after generation (`succeeded`). A typed `TraceFailure`, or a failed
non-fail-fast generation result, produces `failed`. A retrieval failure before
a retrieval result exists remains the responsibility of a future orchestrator.

Trace evidence is a redacted typed summary of each text and image hit. It keeps
modality, stable ID, rank, safe source basename/page, signal sources, raw and
adjusted score fields, boosts, and reranker diagnostics. It does not copy
passages, captions, prompts, answers, image bytes, absolute paths, or arbitrary
metadata. Context summaries retain selected labels/IDs, truncation flags,
token/candidate counts, whitelisted scalar diagnostics, and stable warning
codes. Generation summaries retain model/settings, statuses, token counts,
image hashes, response hash, and the generation `prompt_fingerprint`.

Record IDs are passed through one conservative boundary. Bounded identifiers
such as `chunk-1` remain readable; path-like, traversal, or malformed values
are replaced by stable SHA-256-derived redactions in both evidence and context
summaries. Raw exception messages, arbitrary signal labels, and local path
components are not copied into trace fields.

Trace fingerprints are canonical SHA-256 identifiers over schema, identity,
redacted evidence, context/generation summaries, statuses, response hash, and
stable warnings. Timings are retained separately and excluded from the
fingerprint. Repeated identical executions can therefore share an identifier;
the hash is a content-consistency identifier, not an authenticity signature or
tamper-proof audit record.

## Evaluation contracts

`MetricObservation` represents one supplied value for one trace. Statuses are
`computed`, `skipped`, `failed`, and `unavailable`. A computed `0.0` is a real
measurement; all other statuses require `value=None` and a stable reason code.
Observations record the metric ID/name, unit, optimization direction,
reference kind (`ground_truth`, `context_pseudoreference`,
`retrieved_context`, `no_reference`, or `not_applicable`), implementation
version, optional model identifier, and methodology note.

`MetricAggregate` accepts an already-computed value and explicit eligible,
skipped, failed, and unavailable counts. `computed` requires eligible values
and no exclusions; `partial` requires both eligible and excluded values;
`no_eligible_values` requires no value and at least one excluded observation.
The contracts never calculate means or replace missing measurements with zero.
`build_evaluation_report()` checks trace membership, duplicate observations and
aggregates, run identity, count agreement, and core aggregate metadata
agreement, then creates a deterministic
evaluation fingerprint from canonical report data. It excludes timestamps and
mutable state and, like the trace hash, is not a cryptographic authenticity
mechanism.

The existing notebook formulas and metric IDs remain the methodology source of
truth described in `docs/audit/evaluation-metrics.md`. These contracts record
their provenance and limitations but do not port or execute ROUGE, BLEU,
METEOR, BERTScore, FCD, faithfulness, coverage, latency, or resource metrics.
