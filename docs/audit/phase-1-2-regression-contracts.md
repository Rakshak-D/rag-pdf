# Phase 1.2 regression contracts

This pass characterizes current retrieval, context-selection, generation-error,
and ablation-definition behavior without changing the research implementation.
The tests load selected definitions from `notebooks/rag_pipeline.py` through the
test-only source loader and provide deterministic lightweight collaborators.

Covered contracts include query classification, score normalization, BM25
pseudo-distance conversion, overlap statistics, weighted fusion, RRF, adaptive
semantic fallback, duplicate/context filtering, percentile and fallback
selection, ranked-order behavior, formatted source/page metadata, exhausted
Ollama calls, fail-fast retries, and the six existing ablation definitions.

The context formatter currently enforces `max_context_tokens * 4` as an
approximate character budget. These tests do not establish tokenizer-accurate
budgeting, retrieval quality, answer quality, factuality, or faithfulness.

The generation tests never construct `LocalLLM` through its production
constructor, start Ollama, call HTTP, load models, or calculate FCD. They call
the actual `generate_response()` method on an isolated object with a fake
`ollama.chat`. The synthetic `No answer (LLM error).` result is a failure
outcome, not a successful model answer; future production code must represent
it as a typed failed state rather than score it as valid output.

The ablation test reads `_ABLATION_CONFIGS` directly from the research source.
It does not execute `run_ablation()`. That function's mutable global-config and
configuration-isolation risks remain future implementation work.
