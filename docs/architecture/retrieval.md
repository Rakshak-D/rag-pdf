# Verified semantic retrieval

`backend.engine.retrieval.retrieve_semantic()` is a synchronous, local-only
query boundary over one existing index. The caller supplies a verified
`RunPlan`, an owned storage root, and the exact
`storage_root/build-artifacts/<build-id>/build-report.json` produced by an
index build. The report is validated before a query adapter is constructed or
loaded. Its schema, build ID and artifact location, success status, partial
persistence flag, fingerprints, and persisted collection counts must agree
with the plan and store.

`ChromaVectorStore.open_existing()` is used for this path. It verifies the
same source/storage and reserved-hierarchy protections as the creating path,
but rejects a missing or incomplete index and never creates collections or an
index manifest. This is a non-creating query path; it is not a filesystem
read-only guarantee from Chroma.

For a non-empty text collection, the existing `TextEmbeddingAdapter.embed_query`
creates the query vector and preserves its BGE prefix behavior. For a
non-empty image collection, `ImageEmbeddingAdapter.embed_query` performs
text-to-image semantic search. Empty collections return empty modality results
without loading their model. Text and image adapters are independent and
their result lists are never fused or globally ranked.

Each text hit contains its stable chunk ID, one-based rank, raw Chroma cosine
distance, `1 - distance` cosine similarity, document text, and validated
metadata. Image hits contain the corresponding image ID, rank, distance,
similarity, and metadata. Similarity is not a probability. Known JSON-encoded
metadata fields such as `bbox` and related-image IDs are decoded when valid;
malformed encoded values remain as their stored scalar rather than being
invented or silently discarded. Result arrays must remain aligned and all
distances must be finite.

The effective modality k is the positive caller value or the configured
`text_k` / `image_k` default, clamped to the persisted count. A query failure
raises a typed retrieval error and the store is closed in a `finally` block.
No query logs or output directories are created.

Matching a successful report and collection counts does not cryptographically
prove that vector contents remained unchanged after the build. This phase
does not provide immutable index versions, an active-index pointer, or an
activation transaction. BM25, hybrid fusion, reciprocal-rank fusion,
reranking, context selection, generation, evaluation, APIs, and UI remain
outside this boundary.
