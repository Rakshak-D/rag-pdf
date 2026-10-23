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

The runner marks generation as reached immediately before calling the injected
generation adapter. Exceptions, malformed results, and provenance-mismatched
results therefore produce a generation-stage failure and a failed M16
observation. Generation that is never reached keeps M16 skipped. Error codes
must use the trace contract's lowercase stable-code grammar; arbitrary
exception attributes and messages are replaced with stage-specific safe codes.

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

Trace evidence applies the same conservative record-ID boundary to retrieval
hits and selected-context summaries. Normal IDs such as `chunk-1` remain
readable; path-like or malformed IDs become stable SHA-256-derived redactions.
Signal labels are constrained to stable codes. These hashes are consistency
identifiers, not authenticity proofs.

## Initial metrics

The runner implements only the audited legacy metrics below. It computes means
from eligible observations and preserves explicit `computed`, `skipped`,
`failed`, and `unavailable` counts through the Phase 5.6 contracts.

| ID | Formula | Unit |
| --- | --- | --- |
| M1 | `text_embedding_seconds + image_embedding_seconds` from the successful index-build report | seconds, lower is better |
| M2 | `text_records_persisted + image_records_persisted` from the successful index-build report | vectors, descriptive |
| M3 | `HybridRetrievalResult.total_elapsed_seconds` | seconds, lower is better |
| M4 | Mean mode-specific retrieval score, with equal-weight blending of valid text/image modality means | score, higher is better |
| M5 | `100 * required pages found in retrieved pages / required pages` | percent, higher is better |
| M9 | `len(context.text_context)` for non-empty context | characters, descriptive |
| M6 | Stemmed ROUGE-1 F-measure against the selected reference | f-measure, higher is better |
| M7 | Stemmed ROUGE-2 F-measure against the selected reference | f-measure, higher is better |
| M8 | Stemmed ROUGE-L F-measure against the selected reference | f-measure, higher is better |
| M10 | NLTK sentence BLEU with one ground-truth reference and `SmoothingFunction().method4` | score, higher is better |
| M11 | NLTK METEOR with one ground-truth reference | score, higher is better |
| M12 | BERTScore F1 using cached English `roberta-large` representations | score, higher is better |
| M13 | `round((1 - cosine(response_snippet, prompt_snippet)) * 100, 2)` | points, lower is better |
| M14 | Percentage of response sentences with at least 30% unique context-term overlap | percent, higher is better |
| M15 | Ground-truth sentence coverage or lexical multiset coverage | percent, higher is better |
| M16 | `GenerationResult.elapsed_seconds` for successful generation | seconds, lower is better |
| M17 | positive generation-invocation count divided by the sum of positive invocation durations | queries/second, higher is better |
| M18 | arithmetic mean of normalized process-CPU samples during generation | percent, lower is better |
| M19 | arithmetic mean of per-case sampled process RSS averages | GB, lower is better |

M5 is skipped when a case has no required pages. M9 is skipped for missing or
empty text context. Failed or unreached stages receive explicit failed/skipped
observations. A measured value of `0.0` remains computed; missing data is never
converted into zero. M17, M18, and M19 are the sequential generation,
process-CPU, and process-RSS metrics described below.

M4 selects the score that matches the validated retrieval mode: semantic mode
uses `HybridHit.semantic_similarity` and is labeled Cosine Similarity, BM25
mode uses `HybridHit.bm25_score` and is labeled BM25 Relevance, and hybrid mode
uses the returned raw `HybridHit.fused_score` and is labeled Fused Relevance.
It first averages eligible finite scores within each requested modality, then
blends available modality means with equal weight. A single available modality
is used explicitly; an empty or unsupported signal is never replaced with
zero. Missing scores are skipped, invalid scores fail the observation, and
unsupported modes are unavailable. These score families have different scales
and meanings and must not be compared across retrieval modes without additional
normalization and validation.

M1 is an index-build-level measurement. It sums the text and image embedding
durations already persisted in `build-report.json`; it does not measure query
embedding, retrieval, or generation latency and never re-runs an embedder.
Retrieval surfaces the optional telemetry to the runner, so all cases evaluated
against one build may repeat the same M1 value. Missing telemetry is
`unavailable`, malformed or negative/non-finite telemetry is `failed`, and an
explicit pair of zero durations remains a computed zero. The aggregate uses the
existing arithmetic mean over eligible per-case observations.

M2 is also a build-level measurement: it sums the validated persisted text and
image record counts and reports the result in indexed vectors. It is not storage
bytes, disk usage, document size, retrieved-hit count, or model memory. Cases
using the same build may therefore repeat the same M2 value. Missing counts are
`unavailable`, invalid counts are `failed`, and two explicitly recorded zero
counts produce a computed zero; the aggregate otherwise uses the normal
arithmetic mean and explicit status counts.

For execution, callers can inject `retrieve_for_plan` as the runner's retrieval
callable (with a closure supplying the owned storage root and successful build
report path). The runner still does not open Chroma or select storage paths; the
mode-aware retrieval boundary validates the `RunPlan` and performs that
mode-specific work.

For M6–M8, a stripped `BenchmarkCase.reference_text` is used as ground truth
only when it is longer than 20 characters. Otherwise the formatted context is
used as a `context_pseudoreference`, capped at
`min(rouge_top_k_chunks, max_text_chunks)` chunks split on blank lines. Empty
context has no eligible reference. One lazy stemmed scorer computes all three
ROUGE variants. Missing responses/references are skipped, unavailable scorer
dependencies are explicit unavailable observations, and scorer failures are
explicit failed observations. A valid score of `0.0` remains computed.

ROUGE overlap is not a factuality, entailment, completeness, or hallucination
judge. Reference text exists only during transient metric calculation and is
not copied into traces or reports. Aggregates use `mixed_reference` when their
observations contain more than one reference kind.

M10 and M11 use only an explicit, non-empty `BenchmarkCase.reference_text`;
they never use the retrieved context as a fallback. Each input is lower-cased,
tokenized with `nltk.word_tokenize`, and falls back to the local regular
expression `[A-Za-z0-9']+` only when tokenizer resources raise `LookupError`.
BLEU is computed exactly as
`sentence_bleu([reference_tokens], hypothesis_tokens,
smoothing_function=SmoothingFunction().method4)`. METEOR is computed as
`meteor_score([reference_tokens], hypothesis_tokens)`. The reference tokens are
passed first in both calls. A short ground-truth string is valid for these
metrics (unlike the ROUGE ground-truth threshold). Missing ground truth or
generation is skipped, unavailable scoring components are reported as
unavailable, scoring errors are failed, and a finite score of `0.0` remains a
computed result. No NLTK resource is downloaded automatically; METEOR may be
unavailable when local WordNet resources are absent. BLEU and METEOR are
lexical-overlap measures, not factuality, entailment, completeness, or
hallucination judges.

M12 uses only a non-empty explicit ground-truth reference and generated
response. It preserves the audited BERTScore call with candidate first and
reference second, English `roberta-large`, the legacy 17-layer setting,
`idf=False`, and `rescale_with_baseline=False`. CUDA is selected only when
Torch reports it available; otherwise CPU is used. The execution batch size is
bounded at 8 for memory control and is not part of the metric definition.

The adapter resolves the model through the existing Hugging Face cache with
`local_files_only=True` and passes the resolved local directory to the pinned
BERTScore scorer. It never downloads, pulls, or silently substitutes another
model. Missing dependencies, cached weights, or tokenizer files produce an
explicit `unavailable` observation; load and scoring errors are `failed`, and
valid zero scores remain `computed`. The scorer is initialized once per
evaluation run and reused. A model identifier may include a cached revision
when available, but model names do not prove immutable weight provenance or
execution authenticity. BERTScore is a semantic reference-similarity measure,
not a factuality, entailment, completeness, or hallucination judge.

M13 preserves the legacy FCD calculation but names its target precisely: it
compares the generated answer's first 2,000 characters with the first 2,000
characters of the exact legacy prompt built from the query and formatted text
context. Both snippets are encoded through an injected text embedder's
`embed_query()` method, in response-then-prompt order. The cosine distance is
converted to points and rounded to two decimals per case before aggregation.
No reference answer is used. An absent embedder is explicitly unavailable;
embedding failures are failed observations and missing stages are skipped.
The runner never constructs a default embedder, so model loading and cache
policy remain the caller's responsibility. Lower FCD means greater embedding
similarity to a prompt containing instructions, query, and context; it is not
a factuality classifier, entailment or contradiction detector, or proof of
grounding. Model names do not prove immutable weight provenance.

M14 is the audited, model-free lexical faithfulness heuristic. It splits the
generated response with locally available NLTK sentence tokenization and
falls back to `re.split(r'(?<=[.!?])\\s+', text)` without downloading data.
Context and sentence terms are unique lowercase alphabetic tokens of at least
three characters after English stopword removal. A sentence is grounded when
its context-term overlap is at least 0.30; sentences with no remaining terms
are excluded from the denominator. The per-case score is the grounded-sentence
percentage, and the report aggregate is the arithmetic mean of eligible case
percentages. Missing/empty stages are skipped, calculation failures are failed,
and a genuine zero remains computed. Stopwords use the local NLTK corpus when
available and otherwise the audited fallback set. M14 has no model identifier.

This lexical overlap cannot detect contradictions, negation, entailment
failures, attribution errors, or unsupported numbers. A high score is not
proof of factual accuracy or low hallucination risk.

M15 preserves both audited modes of ground-truth coverage. A stripped explicit
benchmark reference is preferred; otherwise non-empty formatted retrieved
context is used as a `retrieved_context` fallback. With the caller-supplied
text embedder, reference and response sentences are encoded separately with
`encode_text()`. Each reference sentence is covered when its maximum cosine
similarity to any response sentence is at least `0.60`, and the score is the
percentage of covered reference sentences. If no embedder is supplied, M15
uses the lexical fallback: lowercase alphabetic terms of at least three
characters are stopword-filtered and counted with a multiset intersection
divided by the number of filtered reference tokens. Repeated terms therefore
retain their legacy frequency contribution. Local NLTK resources are used when
available and never downloaded; the audited fallback stopword set and regex
sentence splitter keep this mode offline. Semantic inference failures are
failed observations and do not switch to lexical scoring. Computed zero,
skipped missing stages, and no-reference cases remain distinct. Aggregates
average computed case percentages and preserve mixed reference provenance.

M15 measures how much reference content is represented in the response under
the selected algorithm. It is not a factuality, entailment, completeness, or
hallucination test; high coverage cannot detect contradictions or unsupported
claims. Semantic model identifiers are caller-supplied provenance only and do
not prove immutable model weights.

M17 preserves the audited sequential throughput formula: the number of positive
generation invocation durations divided by their summed duration. The runner's
injected clock is sampled immediately before and after the injected
`generation_adapter.generate()` call, so retrieval, context preparation, and
metrics calculated afterward are excluded. Each eligible case contributes
`1 / elapsed_seconds` queries per second; the aggregate is their harmonic mean,
which is equivalent to the audited ratio. Only finite positive durations are
eligible. A call that raises or returns a failed or malformed result can still
contribute when its invocation timing is valid, while a case that never reaches
generation is skipped. Zero, invalid, backward-clock, and clock-failure cases
are explicit skipped or failed observations rather than computed zeroes.
M16 remains independent and continues to use `GenerationResult.elapsed_seconds`.
M17 is sequential invocation throughput, not concurrent or total application
throughput, and its timing is not stored as hidden trace telemetry.

M18 preserves the audited sampled process-CPU metric. A bounded monitor primes
the current Python process's `psutil.Process.cpu_percent(interval=None)` counter,
samples at the legacy 0.05-second interval during the generation invocation,
and divides every valid raw sample by `max(os.cpu_count() or 1, 1)`. Each case
uses the arithmetic mean of its normalized samples, and the report aggregate is
the arithmetic mean of eligible case percentages. Missing samples are reported
as `unavailable`, while invalid samples and monitor startup/shutdown failures
are explicit failures; a measured zero remains computed. Monitoring is
stopped in a bounded, event-driven shutdown path and cannot alter generation or
trace outcomes. Valid samples may still produce M18 when generation returns a
failed result or raises. M18 measures current-process CPU, not model-only CPU,
GPU utilization, or whole-system CPU consumption. It does not collect RSS or
GPU telemetry.

M19 preserves the audited sampled process-RSS metric. A bounded monitor samples
`memory_info().rss` from the current Python process every 0.05 seconds during
generation and converts each byte count with `rss / (1024 ** 3)`, retaining the
legacy `GB` label and binary conversion. Each case computes the arithmetic mean
of its valid converted samples, and the report aggregate is the arithmetic mean
of those eligible per-case averages. Missing samples are `unavailable`, while
invalid RSS values and monitor startup/shutdown failures are explicit failures;
a measured zero remains computed. Valid samples may still produce M19 when
generation returns a failed result or raises. RAM-monitor failures do not alter
generation, M17, M18, or other metric outcomes. M19 measures current-process
RSS, not total machine memory, model-weight size, CUDA VRAM, or model-only
memory, and raw samples are not stored in traces or reports. M16–M18 retain
their existing formulas; no additional RAM or GPU telemetry is collected.

Latency values are runtime measurements and are not expected to be identical
between executions. Trace and evaluation fingerprints retain only their
contract-defined stable content. Those hashes identify consistency content, not
authenticity or tamper-proof execution history. Persistent evaluation jobs,
queues, databases, dashboards, APIs, and model-judge metrics remain out of
scope.

Report construction checks aggregate core metadata—name, unit, direction,
reference kind, and formula version—against every related observation.
Conflicting model identifiers are rejected, and the runner attaches the
configured generation model only to M16 observations when generation was
actually attempted. A model name is not immutable model-weight provenance.
