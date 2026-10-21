# Bounded in-memory evaluation runner

Phase 5.7 adds a synchronous `EvaluationRunner` for a validated `RunPlan` and a
caller-supplied `BenchmarkSuite`. It invokes the existing pipeline in order:

`retrieve_hybrid` → `ContextFormatter.format` → `LocalGenerationAdapter.generate`
→ redacted query trace → metric observations → validated `EvaluationReport`.

The runner is deliberately in memory. It does not open Chroma itself, select an
index, persist reports, launch jobs, call Ollama, or load models. Callers inject
the retrieval callable and pipeline components, so tests can use deterministic
doubles. A failure in one case is represented by a failed trace and explicit
metric observations, then the next case is attempted.

## Benchmark contracts

`BenchmarkCase` validates a bounded query, unique supported modalities, positive
top-k overrides, and normalized required page references. `BenchmarkSuite` has a
stable ID/version, unique case IDs, and at most 100 cases. The runner rejects
duplicate query identities under one plan and effective retrieval limits because
the content-addressed trace identity is intentionally allowed to repeat only
for the same execution content, not as duplicate report entries.

Retrieval failures use `build_failed_query_trace`; this records the plan and
query identity without fabricating evidence. Context and generation failures
retain only stages that actually completed. Unknown exceptions are reduced to
stable error codes; raw messages, prompts, passages, answers, captions, and
paths are not copied into traces or reports.

## Initial metrics

The runner implements only the audited legacy metrics below. It computes means
from eligible observations and preserves explicit `computed`, `skipped`,
`failed`, and `unavailable` counts through the Phase 5.6 contracts.

| ID | Formula | Unit |
| --- | --- | --- |
| M3 | `HybridRetrievalResult.total_elapsed_seconds` | seconds, lower is better |
| M5 | `100 * required pages found in retrieved pages / required pages` | percent, higher is better |
| M9 | `len(context.text_context)` for non-empty context | characters, descriptive |
| M16 | `GenerationResult.elapsed_seconds` for successful generation | seconds, lower is better |

M5 is skipped when a case has no required pages. M9 is skipped for missing or
empty text context. Failed or unreached stages receive explicit failed/skipped
observations. A measured value of `0.0` remains computed; missing data is never
converted into zero. No other metrics or metric formulas are executed in this
phase.

Latency values are runtime measurements and are not expected to be identical
between executions. Trace and evaluation fingerprints retain only their
contract-defined stable content. Those hashes identify consistency content, not
authenticity or tamper-proof execution history. Persistent evaluation jobs,
queues, databases, dashboards, APIs, and model-judge metrics remain out of
scope.
