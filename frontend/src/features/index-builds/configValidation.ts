export const REQUIRED_RESOLVED_CONFIG_FIELDS = [
  "pdf_dir", "images_dir", "database_dir", "results_dir", "model_comparison_results_dir", "retrieval_results_dir",
  "reset_collections_on_start", "metrics_verbose", "llm_retries", "llm_retry_backoff_sec", "llm_fail_fast",
  "text_embed_model", "image_embed_model", "image_embed_pretrained", "reranker_model", "llm_model", "llm_models",
  "tiktoken_encoding", "chunk_max_tokens", "chunk_overlap_tokens", "chunk_max_vertical_gap", "chunk_min_text_len",
  "text_embed_batch_size", "image_embed_batch_size", "text_collection_name", "image_collection_name", "text_k", "image_k",
  "rerank_k", "rrf_k_constant", "bm25_weight", "semantic_weight", "adaptive_weights_keyword", "adaptive_weights_semantic",
  "adaptive_weights_balanced_strong_bm25", "adaptive_weights_balanced_weak_bm25", "bm25_strong_max_score_threshold",
  "bm25_strong_std_threshold", "bm25_weak_max_score_threshold", "image_caption_image_weight", "max_text_chunks",
  "max_context_tokens", "max_images", "text_distance_threshold", "image_distance_threshold", "use_filtering",
  "use_percentile_filtering", "percentile_cutoff", "llm_temperature", "llm_max_tokens", "llm_think_mode", "retrieval_mode",
  "use_reranker", "adaptive_weighting", "use_weighted_fusion", "rouge_top_k_chunks", "top_k_accuracy_k",
  "reranker_ce_weight", "reranker_fused_weight", "reranker_boost_weight", "use_cross_modal_boost", "cross_modal_max_boost",
  "cross_modal_boost_per_overlap", "bm25_signal_max_weight", "bm25_signal_std_weight", "bm25_signal_spec_weight",
  "bm25_signal_lex_weight", "img_variance_threshold", "img_white_pixel_threshold", "img_white_ratio_threshold",
  "img_min_width", "img_min_height", "img_min_aspect_ratio", "img_max_aspect_ratio", "workbench_policy",
] as const;

function hasDuplicateKeys(text: string): boolean {
  let index = 0;
  const whitespace = () => { while (/\s/.test(text[index] ?? "")) index += 1; };
  const string = (): string => {
    const start = index++;
    while (index < text.length) {
      const char = text[index++]!;
      if (char === "\\") index += 1;
      else if (char === '"') break;
    }
    return JSON.parse(text.slice(start, index)) as string;
  };
  const value = (): boolean => {
    whitespace();
    if (text[index] === '"') { string(); return false; }
    if (text[index] === "[") {
      index += 1; whitespace();
      while (text[index] !== "]") {
        if (value()) return true;
        whitespace();
        if (text[index] === ",") { index += 1; whitespace(); continue; }
        break;
      }
      index += 1; return false;
    }
    if (text[index] === "{") {
      index += 1; whitespace();
      const keys = new Set<string>();
      while (text[index] !== "}") {
        const key = string(); whitespace(); index += 1; whitespace();
        if (keys.has(key)) return true;
        keys.add(key);
        if (value()) return true;
        whitespace();
        if (text[index] === ",") { index += 1; whitespace(); continue; }
        break;
      }
      index += 1; return false;
    }
    while (index < text.length && !/[\s,\]}]/.test(text[index]!)) index += 1;
    return false;
  };
  return value();
}

export interface ConfigValidation {
  value: Record<string, unknown> | null;
  errors: string[];
}

export function validateResolvedConfig(text: string): ConfigValidation {
  if (!text.trim()) return { value: null, errors: ["Enter the complete resolved configuration JSON object."] };
  let parsed: unknown;
  try { parsed = JSON.parse(text) as unknown; }
  catch { return { value: null, errors: ["Enter valid JSON. The draft has not been changed."] }; }
  if (hasDuplicateKeys(text)) return { value: null, errors: ["Duplicate JSON object keys are not accepted."] };
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return { value: null, errors: ["The configuration root must be a JSON object."] };
  }
  const value = parsed as Record<string, unknown>;
  const missing = REQUIRED_RESOLVED_CONFIG_FIELDS.filter((field) => !(field in value));
  const extra = Object.keys(value).filter((field) => !(REQUIRED_RESOLVED_CONFIG_FIELDS as readonly string[]).includes(field));
  const errors: string[] = [];
  if (missing.length) errors.push(`Missing required fields: ${missing.join(", ")}.`);
  if (extra.length) errors.push(`Unsupported fields: ${extra.join(", ")}.`);
  const policy = value.workbench_policy;
  if (typeof policy !== "object" || policy === null || Array.isArray(policy) ||
    !("allow_destructive_index_reset" in policy) || !("allow_collection_delete" in policy)) {
    errors.push("workbench_policy must include both safety flags.");
  } else {
    const safety = policy as Record<string, unknown>;
    if (safety.allow_destructive_index_reset !== false || safety.allow_collection_delete !== false || value.reset_collections_on_start !== false) {
      errors.push("Fresh bootstrap builds require reset and collection-delete settings to be false.");
    }
  }
  return { value: errors.length ? null : value, errors };
}
