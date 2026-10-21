# Evidence context selection

`ContextFormatter.format()` consumes a `HybridRetrievalResult` and its matching immutable `RunPlan`. It verifies plan identity before deriving the resolved configuration, then selects ranked text and image evidence without loading models, calling an LLM, or writing files.

Text candidates are validated, whitespace/case-normalized for exact deduplication, and compared with deterministic five-word shingles. A Jaccard overlap of at least 0.70 removes the later near-duplicate. Configured distance filtering uses the effective distance when present, otherwise the raw semantic distance; BM25-only candidates with no distance remain eligible. Percentile filtering uses finite distances only. If filtering removes every valid candidate, the ranked valid candidates provide a bounded fallback.

Distance handling distinguishes missing values from malformed values. `None` means that a candidate has no semantic distance; it remains eligible and increments a missing-distance diagnostic. A non-`None` boolean, non-numeric, NaN, or infinite effective/raw distance is invalid, never falls back to the other distance field, and is rejected even when filtering is disabled. Invalid and configured-filter rejections have separate stable counters and warning codes.

Formatting includes stable labels, source basenames, page values, stable record IDs, and the original immutable `HybridHit`. The complete serialized string—including labels and separators—is encoded with the configured `tiktoken` encoding. Whole entries are preferred; an oversized entry is deterministically truncated with a marker when possible, and entries whose minimal header cannot fit are skipped. The returned token count never exceeds `max_context_tokens`.

Source basenames, page values, and displayed record IDs are whitespace-normalized, control-character-safe, bounded header values with deterministic fallbacks. Only their display forms are sanitized; the structured record ID, metadata, hit, and evidence passage remain unchanged. Captions are retained only when the metadata value is a string; malformed caption values become an empty structured caption rather than being stringified into prompt text. Warnings use stable codes and do not include local paths or record IDs.

Images are selected in retrieval order up to `max_images`. Their candidate paths must resolve through `resolve_owned_path()` beneath the configured `images_dir` and point to existing regular files. Traversal, symlink escapes, missing paths, and malformed paths are skipped with stable warning codes; files are never opened or modified. Image payload cost is not included in the text token count because it depends on the future generation model.

The context fingerprint is schema version 2: a canonical UTF-8 JSON SHA-256 over
the query ID and text, index/manifest/run identity, retrieval modalities and
limits, complete formatted text, selected text labels/IDs/source/page/text and
truncation state, and selected image labels/IDs/source/page/caption plus the
path relative to the owned image root. It excludes timestamps, diagnostics,
absolute paths, and filesystem enumeration state. The formatter and generation
adapter use the same fingerprint builder; generation recomputes it before
constructing the Ollama client. This is a consistency check, not a signature:
a caller able to rewrite both evidence and its unkeyed hash can still create a
self-consistent replacement.

The formatter verifies the existing retrieval query-ID formula against the
original query, requested modalities, and retrieval limits. It preserves those
identity fields in `EvidenceContext`. Generation also checks that the formatted
text exactly reconstructs the selected labelled text records, including valid
truncation markers. Image-only contexts are valid when their images pass
revalidation; a non-empty text context without selected text is rejected.

Diagnostics report candidate counts, duplicate/filter/budget decisions, token
usage, image rejections, and warnings. See [generation.md](generation.md) for
the handoff. Context-window negotiation, API/UI, persistence, and evaluation
remain out of scope.
