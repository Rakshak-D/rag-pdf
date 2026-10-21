# Evaluation metrics audit

Evidence: metric functions in `notebooks/rag_pipeline.py` (`4069–5010`) and generation/telemetry records (`2946–3133`, `5032–5164`). Results are averages over non-skipped records unless noted. A displayed zero conflates “true zero,” “no eligible inputs,” and several caught failures.

## Retrieval and context metrics

| Label | Implementation and inputs | Units / interpretation | Limitations |
|---|---|---|---|
| M1 Embedding time | `compute_embedding_time()` sums text and image embedding wall time from model calls | seconds; lower is faster | excludes model initialization; image failures and mode-dependent skipped dense work change comparability |
| M2 Index size | Chroma text+image counts, or BM25 chunk corpus size in BM25-only | vectors or documents | not bytes/storage size; unit changes by mode |
| M3 Retrieval latency | arithmetic mean of per-question `retrieve()` wall times | seconds | mean only; no p50/p95; includes both modalities and stage behavior |
| M4 Retrieval similarity | arithmetic mean of `out.cosine_sim_text/image`; label changes to BM25 relevance/fused relevance by mode | score with mode-dependent semantics | the function is a mean of whatever signal was supplied; report label can say cosine when the values are not cosine similarity |
| M5 Top-k page coverage | for questions with `required_context.pages`, `|required ∩ retrieved pages| / |required|`, mean ×100 over evaluated questions | percent; page recall-like measure | page presence is not chunk relevance; questions without page labels are skipped |
| M9 Context length | mean `len(text_context)` over non-empty contexts | characters, despite token-budget context | image tokens and actual tokenizer overhead excluded |

`BM25Index` also records raw score max/mean/min/std, non-zero docs and corpus coverage. `compute_hybrid_stats()` averages adaptive weights, query-type counts, BM25 signals, overlap Jaccard/percentage, and semantic fallback counts. `compute_fusion_effectiveness()` reports the percentage of final candidate docs supported by both BM25 and semantic signals. It is explicitly signal agreement, not answer-quality lift.

## Reference-based answer metrics

| Label | Exact implementation | Prerequisites / limitation |
|---|---|---|
| M6 ROUGE-1 | `rouge_scorer.RougeScorer(["rouge1"], use_stemmer=True)`, F-measure per response against `_get_reference_text()`; mean | Uses ground truth only when stripped length >20; otherwise top `min(rouge_top_k_chunks,max_text_chunks)` context as pseudo-reference. Therefore the label “answer quality” is conditional. |
| M7 ROUGE-2 | same but `rouge2` F-measure | same pseudo-reference fallback; short/small benchmark sensitivity |
| M8 ROUGE-L | same but `rougeL` F-measure | LCS overlap is not factuality or completeness |
| M10 BLEU | NLTK sentence BLEU, one reference, `SmoothingFunction.method4`, lower-case word tokens; mean | Requires nonempty `reference_text` only; no context fallback; small reference set and smoothing make cross-run interpretation fragile |
| M11 METEOR | NLTK METEOR over token lists, one reference; mean | Requires ground truth; NLTK resources; word-level matching is not a factual judge |
| M12 BERTScore | `bert_score.score(candidates,references,lang="en",device=CUDA/CPU)`; mean F1 | Requires ground truth and BERTScore model; exceptions return 0.0; model/version/device must be recorded for reproducibility |

## Grounding, consistency and coverage

- M13 FCD (`compute_fcd`) averages the per-query value created by `LocalLLM.generate_response`: `100*(1-cosine(response[:2000], build_prompt(query,context)[:2000]))` using the shared text embedder. It is an embedding distance to a prompt snippet, not a factual-consistency classifier or entailment score. Lower is intended to mean closer, but prompt instructions/query text are included in the reference vector and can affect the value.
- M14 Faithfulness splits responses into sentences and marks one grounded when at least 30% of unique alphabetic content terms of length ≥3 overlap the context vocabulary, then averages grounded-sentence percentages. It is a lexical heuristic; it does not detect contradiction, attribution, negation, or unsupported numbers. The report’s “lower hallucination risk” wording is stronger than this implementation supports.
- M15 GT Coverage uses sentence embeddings when a text embedder is provided: every ground-truth sentence is covered if its maximum cosine similarity to any response sentence is ≥0.60; percentage is averaged. Without an embedder, it uses multiset term-frequency intersection. If ground truth is absent, it falls back to context and logs “Context Utilization (fallback)” but still returns the same numeric field `m15_gt_coverage`; downstream consumers must carry the reference kind.

## Performance and resource metrics

M16 is mean positive per-query end-to-end wall time from before `generate_response()` through telemetry stop. M17 is `number_of_positive_queries / sum(e2e_times)` in queries/second; it is not concurrent throughput and excludes failed/zero-time records. M18 averages sampled process CPU percent. M19 averages sampled RSS in GB; peak and delta are logged but not used in the aggregate. GPU aggregate is average GPU utilization percent from NVML index 0; peak utilization, active percent, duty cycle and VRAM are collected per query but only some are exported. Sampling interval is 0.05 seconds and is not synchronized with model kernels.

## Methodology risks and required evidence

The benchmark has 14 hard multimodal questions, but no test runner or statistical confidence intervals were found. The code reports arithmetic means and often skips missing ground truth/context. It does not record per-metric eligible counts in the structured summary for every metric, although verbose logs print some. FCD, faithfulness and M15 must be renamed in the UI as embedding/lexical heuristics unless independently validated. Preserve formulas for baseline compatibility; add metric provenance (`reference_kind`, implementation version, eligible/skipped count, model ID) before refactoring.

The in-memory provenance and status contracts for future evaluators are documented in [tracing-and-evaluation-contracts.md](../architecture/tracing-and-evaluation-contracts.md); this audit remains an unchanged description of the notebook formulas.
