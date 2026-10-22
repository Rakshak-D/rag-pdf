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
| M3 | `HybridRetrievalResult.total_elapsed_seconds` | seconds, lower is better |
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
| M16 | `GenerationResult.elapsed_seconds` for successful generation | seconds, lower is better |

M5 is skipped when a case has no required pages. M9 is skipped for missing or
empty text context. Failed or unreached stages receive explicit failed/skipped
observations. A measured value of `0.0` remains computed; missing data is never
converted into zero. No other metrics or metric formulas are executed in this
phase; M13–M15 and M17–M19 remain outside this runner.

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
