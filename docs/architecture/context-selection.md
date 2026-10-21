# Evidence context selection

`ContextFormatter.format()` consumes a `HybridRetrievalResult` and its matching immutable `RunPlan`. It verifies plan identity before deriving the resolved configuration, then selects ranked text and image evidence without loading models, calling an LLM, or writing files.

Text candidates are validated, whitespace/case-normalized for exact deduplication, and compared with deterministic five-word shingles. A Jaccard overlap of at least 0.70 removes the later near-duplicate. Configured distance filtering uses the effective distance when present, otherwise the raw semantic distance; BM25-only candidates with no distance remain eligible. Percentile filtering uses finite distances only. If filtering removes every valid candidate, the ranked valid candidates provide a bounded fallback.

Formatting includes stable labels, source basenames, page values, stable record IDs, and the original immutable `HybridHit`. The complete serialized string—including labels and separators—is encoded with the configured `tiktoken` encoding. Whole entries are preferred; an oversized entry is deterministically truncated with a marker when possible, and entries whose minimal header cannot fit are skipped. The returned token count never exceeds `max_context_tokens`.

Images are selected in retrieval order up to `max_images`. Their candidate paths must resolve through `resolve_owned_path()` beneath the configured `images_dir` and point to existing regular files. Traversal, symlink escapes, missing paths, and malformed paths are skipped with stable warning codes; files are never opened or modified. Image payload cost is not included in the text token count because it depends on the future generation model.

The context fingerprint is a versioned SHA-256 over query/index/manifest/run identity, formatted text, and selected IDs. It excludes timestamps and filesystem enumeration state. Diagnostics report candidate counts, duplicate/filter/budget decisions, token usage, image rejections, and warnings. Generation, context-window negotiation, API/UI, persistence, and evaluation remain out of scope.
