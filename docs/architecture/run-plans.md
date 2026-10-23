# Immutable run plans

Phase 2.2 adds `backend/experiments/run_plan.py` as a pure planning boundary.
It does not execute ingestion, indexing, retrieval, generation, evaluation, or
any persistence operation.

## Identity boundaries

`LegacyConfigSchema` is the typed compatibility view of all 74 fields in
`notebooks.config.Config`. `ResolvedRunConfig` is the validated execution
configuration plus `WorkbenchSafetyPolicy`. The compatibility default
`reset_collections_on_start=True` is preserved, but `build_run_plan()` rejects
it unless `allow_destructive_index_reset=True` is explicitly supplied. The
collection-delete policy remains false by default and no deletion operation is
implemented.

The plan stores only canonical JSON plus hashes:

- `document_manifest_hash` identifies the exact caller-supplied document
  manifest;
- `index_fingerprint` is produced by the existing index allowlist in
  `backend/config/fingerprint.py` and identifies document-derived chunks and
  vectors;
- `run_fingerprint` includes the index fingerprint plus the behavior-affecting
  run configuration; and
- `configuration_snapshot_hash` binds the complete serialized resolved
  configuration, including safety policy, without making that policy part of
  experiment identity.

No timestamp, random ID, model object, client, path handle, or runtime result
is included. A later persistence layer may assign a unique run ID separately.

## Run identity field classification

All legacy fields are classified; the included set is derived from the actual
dataclass so a future legacy field addition is visible in tests. The following
fields are excluded from both index and run fingerprints because they are
operational or output/storage concerns:

`pdf_dir`, `images_dir`, `database_dir`, `results_dir`,
`model_comparison_results_dir`, `retrieval_results_dir`, `metrics_verbose`,
`text_collection_name`, `image_collection_name`, and
`reset_collections_on_start`.

Every other legacy field contributes to run identity, including:

`llm_retries`, `llm_retry_backoff_sec`, `llm_fail_fast`, `text_embed_model`,
`image_embed_model`, `image_embed_pretrained`, `reranker_model`, `llm_model`,
`llm_models`, `tiktoken_encoding`, `chunk_max_tokens`,
`chunk_overlap_tokens`, `chunk_max_vertical_gap`, `chunk_min_text_len`,
`text_embed_batch_size`, `image_embed_batch_size`, `text_k`, `image_k`,
`rerank_k`, `rrf_k_constant`, `bm25_weight`, `semantic_weight`,
`adaptive_weights_keyword`, `adaptive_weights_semantic`,
`adaptive_weights_balanced_strong_bm25`, `adaptive_weights_balanced_weak_bm25`,
`bm25_strong_max_score_threshold`, `bm25_strong_std_threshold`,
`bm25_weak_max_score_threshold`, `image_caption_image_weight`,
`max_text_chunks`, `max_context_tokens`, `max_images`,
`text_distance_threshold`, `image_distance_threshold`, `use_filtering`,
`use_percentile_filtering`, `percentile_cutoff`, `llm_temperature`,
`llm_max_tokens`, `llm_think_mode`, `retrieval_mode`, `use_reranker`,
`adaptive_weighting`, `use_weighted_fusion`, `rouge_top_k_chunks`,
`top_k_accuracy_k`, `reranker_ce_weight`, `reranker_fused_weight`,
`reranker_boost_weight`, `use_cross_modal_boost`, `cross_modal_max_boost`,
`cross_modal_boost_per_overlap`, `bm25_signal_max_weight`,
`bm25_signal_std_weight`, `bm25_signal_spec_weight`,
`bm25_signal_lex_weight`, `img_variance_threshold`,
`img_white_pixel_threshold`, `img_white_ratio_threshold`, `img_min_width`,
`img_min_height`, `img_min_aspect_ratio`, and `img_max_aspect_ratio`.

The run fingerprint uses canonical UTF-8 JSON with sorted keys. It therefore
changes for retrieval, reranker, context, generation, evaluation, retry, and
other behavior-affecting settings, while preserving index-fingerprint
stability for runtime/output-only changes. The safety policy is not experiment
semantics and is not included in the run fingerprint.

## Integrity and limits

`RunPlan.verify_integrity()` reparses the stored configuration snapshot,
recomputes the index and run fingerprints, and raises
`RunPlanIntegrityError` on manifest, fingerprint, or snapshot corruption.
`RunPlan.from_serializable_json()` performs the same verification before
returning a plan, so ordinary deserialization cannot be mistaken for a
verified plan. The supported `plan_schema_version` is checked during model
validation and again during integrity verification; unknown versions are not
interpreted as the current schema.
`resolved_config()` always returns a newly parsed configuration object, so a
caller cannot mutate the stored snapshot through the returned object.

These are consistency checks, not cryptographic authenticity. The hashes are
unkeyed: a party able to rewrite a plan and recompute every related hash can
create a self-consistent replacement. The current plan is not signed or
tamper-proof. If later persistence/security requirements need stronger
authenticity, use a trusted signature or an independently trusted stored digest.
The plan also does not prove immutable model weights, successful index
construction, reproducible model outputs, or execution-engine behavior.

Mapping inputs to `canonical_index_definition()` and `index_fingerprint()` are
intentionally legacy-only. A mapping containing `workbench_policy` is rejected
by the strict compatibility schema rather than having that field silently
dropped; the run-plan builder passes the validated typed object instead.

## Six-configuration ablation plans

`backend.experiments.ablation_plans` adds a declarative layer for the six
research assignments, in their source order: dense-only, BM25-only, static
hybrid, adaptive hybrid, hybrid with reranking, and the full filtered pipeline.
Each assignment starts from a deep copy of one validated `ResolvedRunConfig`,
applies only its canonical notebook-derived overrides, disables collection reset,
and installs a non-destructive `WorkbenchSafetyPolicy`. It then creates a fresh
validated `RunPlan` through `build_run_plan()`; no index, corpus, model, or
retrieval resource is opened.

The variants share the document manifest and, because these six overlays change
retrieval behavior rather than index-defining settings, share an index
fingerprint. Their complete resolved snapshots, snapshot hashes, and run
fingerprints remain distinct where run behavior differs. The plan serializer
uses canonical JSON and includes the ordered overrides plus each serialized
`RunPlan`. Loading requires the accepted base configuration and manifest: it
revalidates every stored RunPlan, checks the exact six IDs and overrides,
rebuilds the expected plans, and rejects mismatches, unsafe settings, corrupted
hashes, or reordered variants.

This is a consistency contract, not an authenticity signature. Plan creation
and loading do not execute an ablation, rebuild an index, persist results, or
launch a job. A later execution phase must explicitly consume the verified
plans and supply its own retrieval and generation components.

The six canonical variants map to the supported retrieval modes as follows:
`1_Dense_only` uses semantic retrieval, `2_BM25_only` uses lexical retrieval,
and variants 3 through 6 use hybrid retrieval with their respective adaptive,
reranker, and filtering overlays. This mapping identifies a future execution
path only; creating or loading an ablation plan does not run retrieval,
rebuild an index, or execute an experiment.
## Six-variant ablation execution

The immutable `AblationPlan` layer defines six fresh, validated, non-destructive
`RunPlan` snapshots in the canonical research order. `AblationRunner` consumes
that plan and a validated benchmark suite when execution is explicitly
requested. It reuses the shared verified index and build report, delegates
mode dispatch to `retrieve_for_plan()`, and delegates all downstream work to
`EvaluationRunner`.

Each variant retains its own run fingerprint and report while carrying the
same document-manifest and shared index identities. The coordinator executes
sequentially, preserves ordinary per-case failures, and records runner-level
failures with stable codes so later variants can still run. It does not rebuild
or reset the index. A plan by itself remains declarative and does not execute
an experiment.
