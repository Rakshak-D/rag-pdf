# Reranking and cross-modal evidence

The hybrid query path now completes lexical and semantic retrieval for every requested modality, computes relationships between the returned text and image candidates, applies evidence adjustments, reranks text candidates when enabled, and finally assigns one-based ranks. The existing successful-build-report and non-creating index checks remain the preconditions for every stage.

`CrossEncoderReranker` is lazy: construction imports neither model clients nor weights. It loads `config.reranker_model` only when text candidates exist and reranking is enabled. Tests inject a loader/fake scorer. Candidate `(query, document)` ordering is validated, as are numeric, finite, and length-matched outputs. Loading or inference failure is a typed retrieval failure; there is no partial rerank fallback.

The legacy formula is preserved. Cross-encoder values use min-max normalization, with `0.5` for an equal-score list. When fused scores exist, the final score is `reranker_ce_weight * normalized_ce + reranker_fused_weight * normalized_fused + reranker_boost_weight * normalized_boost`; without a hybrid signal it is the normalized cross-encoder score alone. Cross-modal boost normalization uses neutral `0.0`. Ties are resolved by final score, original fused score, raw cross-encoder score, then stable input order. `rerank_k` limits final text results while the candidate pool retains enough fused candidates for reranking.

Cross-modal matching uses exact stable IDs first, then normalized path basenames and parsed identities such as `report_p1_i2`. Basename fallbacks are source-aware so identical filenames from different documents do not match accidentally. Malformed `related_image_ids` metadata produces a warning and no invented relationship. Text boosts cap the effective semantic-distance reduction and apply the legacy fused-score lift; image boosts use the one-overlap cap and never invoke the cross-encoder. Raw distance, similarity, fused score, BM25 score, and adjusted diagnostics remain separate.

Only text candidates are cross-encoder reranked. Image ordering remains the fused ordering, with boost diagnostics exposed separately. Store closure is retained on success and failure, and no result files or model caches are written. Timings and sanitized warnings are returned in the immutable result object.

This phase still does not provide context selection, answer generation, evaluation jobs, API/UI, SQLite registry, active-index promotion, or transactional index activation. Model-name identity also does not prove immutable model-weight provenance.

The next evidence-context boundary is documented in [context-selection.md](context-selection.md). It consumes the immutable hybrid result without changing raw retrieval provenance.
