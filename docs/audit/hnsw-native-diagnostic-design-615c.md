# Phase 6.15C — HNSW native diagnostic patch design

- **Review date:** 2026-11-03
- **RAG-PDF starting checkpoint:** `8d6b015c3115c0b295c75117793d10549e7ef968`
- **Phase 6.15B checkpoint:** `af1ebe583b90ed906fe5d1b00b37b1580b0bfca5`
- **Pinned Chroma source:** [`ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda`](https://github.com/chroma-core/chroma/tree/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda)
- **Patch status:** Reviewable, uncompiled source patch proposal; no build or runtime execution
- **HNSW incident status:** Root cause remains unknown

## Decision summary

The diagnostic patch distinguishes the three existing reader initialization failures while preserving their rejection behavior and `InternalError` classification. It changes only the reader error construction and the two compaction handlers that match the old unit variant. It does not instrument successful queries, add filesystem work, change persistence, or add recovery behavior.

**The branch distinction would materially improve the next engineering decision.** A missing directory points to segment-directory lifecycle; a missing metadata pickle points to persisted-state and cache-transition investigation; an empty ID map points to writer initialization or persisted-map state. I recommend seeking **separate approval** for one disposable diagnostic build and one bounded runtime attempt because those outcomes lead to different follow-up investigations. This report does not authorize that build or attempt. If no owner will act differently on those outcomes, defer the build.

Branch identity would not establish why the condition occurred. A passing run would capture no branch and would remain inconclusive about the intermittent incident.

## Source provenance and original hashes

The source was fetched from immutable raw-file URLs at the pinned commit and cross-checked against a sparse Git checkout at that exact commit. Git blob IDs for the original raw files matched the pinned checkout. The sparse checkout covered `rust/segment`, `rust/log`, `rust/frontend`, `rust/types`, and `rust/python_bindings`; it is retained under the evidence root.

| Pinned source file | Original SHA-256 |
|---|---|
| [`rust/segment/src/local_hnsw.rs`](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/segment/src/local_hnsw.rs) | `45568617EC3BEDCD33B3A811079E8C525EE649F26E2A90E2E07EDCA2D7C09E57` |
| [`rust/segment/src/local_segment_manager.rs`](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/segment/src/local_segment_manager.rs) | `69D52D110468F0BC93B3460CBF6F8F4134184D504C9746D9A44DF7FB0078EF4C` |
| [`rust/log/src/local_compaction_manager.rs`](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/log/src/local_compaction_manager.rs) | `ADEFDB58B57E89F509AB92C4CDDFA8E97774290B1CF3F85DC92AABA55F92EA8B` |
| [`rust/frontend/src/executor/local.rs`](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/frontend/src/executor/local.rs) | `628B87F5FDBC6C00A7C2E859A7DE585B4B90BFEC856A977C97131924C636401C` |
| [`rust/types/src/api_types.rs`](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/types/src/api_types.rs) | `A7ECEB7C1C193A29779038203527930514F01A436050A7BB97C92F3B90B85D6D` |
| [`rust/python_bindings/src/errors.rs`](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/python_bindings/src/errors.rs) | `9C9BBDC843506FEEFCF0013BD4A283DD8165301424651A5CF4D79B9E6630194E` |

The complete 14-file source inventory, including immutable URL, byte length, SHA-256, and Git blob ID for each file, is retained as `source-files.sha256.json` in the evidence workspace (manifest SHA-256 `A72BE362316346B40295D4361213D70A1A5F105DC8ECA3023D240C20ED759AE9`). Other inspected build and propagation files were fetched from the same immutable commit. The source `Cargo.lock` pins `thiserror` 1.0.69 for this workspace; the segment crate uses the workspace dependency. The proposed `thiserror` annotation uses ordinary named-field formatting, already supported by that version. The pinned [`rust-toolchain.toml`](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust-toolchain.toml) specifies Rust 1.92.0.

The source commit is verified. As noted in the corrected [Phase 6.15A report](hnsw-native-diagnostic-feasibility-615a.md), the installed `.pyd` has not been cryptographically mapped to a reproducible source build. A diagnostic binary would need its own explicit source/build provenance.

## Proposed branch codes and change locations

The patch uses these stable identifiers:

| Existing condition in `LocalHnswSegmentReader::from_segment()` | Diagnostic code |
|---|---|
| Vector segment directory does not exist | `HNSW_READER_DIRECTORY_MISSING` |
| Directory exists but `index_metadata.pickle` does not | `HNSW_READER_METADATA_MISSING` |
| Pickle deserializes but `id_to_label` is empty | `HNSW_READER_ID_MAP_EMPTY` |

In `rust/segment/src/local_hnsw.rs`, the reader's `UninitializedSegment` variant becomes a struct variant carrying a static reason code and `segment.id.to_string()`. Its display remains recognizable as `Nothing found on disk`, followed by the code and vector-segment UUID. It adds no absolute path, collection content, record ID, embedding, pickle content, or SQLite value.

The same file updates the `ChromaError::code()` pattern to match the struct variant and supplies the matching code at each of the three existing branches. The directory, metadata, and ID-map conditions are unchanged. The separate writer-side `UninitializedSegment` variant is untouched.

In `rust/log/src/local_compaction_manager.rs`, the backfill and purge handlers update their patterns to `UninitializedSegment { .. }`. They continue treating this reader condition as watermark zero exactly as before. This is required so the diagnostic fields do not alter log replay or purge control flow.

`rust/segment/src/local_segment_manager.rs` needs no source change: `LocalSegmentManagerError` already wraps reader errors through `#[from]` and delegates its Chroma error code. `LocalExecutor` wraps the error in `ExecutorError::Internal`; `ExecutorError::code()` forwards the nested code. `ChromaPyError` maps `ErrorCodes::Internal` to Python `InternalError` and formats the same error chain. The proposed patch leaves all three mappings unchanged. See the pinned [manager](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/segment/src/local_segment_manager.rs#L75-L148), [executor error mapping](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/types/src/api_types.rs#L2375-L2407), [Python mapping](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/python_bindings/src/errors.rs#L13-L36), and [production caller](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/frontend/src/executor/local.rs#L132-L197).

The pinned-source symbol search found the reader error definition and construction sites in `local_hnsw.rs`, plus exactly two direct `UninitializedSegment` matches in the compaction backfill and purge handlers. Other similarly named variants belong to record, SPANN, or writer errors and are unchanged.

## Patch artifact and validation

Unified patch artifact:

`C:\Users\abhis\AppData\Local\Temp\rag-pdf-615b-615c-d3dd84146b944c9098ac98e1a338dd4b\hnsw-branch-diagnostic-615c.patch`

SHA-256: `276D25F5BA771A33BC4902A904B8C6E2E31C632D02F5BE27FAD44B28CE969920`

The patch changes exactly two pinned source files: `rust/segment/src/local_hnsw.rs` and `rust/log/src/local_compaction_manager.rs`. It was generated from the original bytes at the pinned raw-file URLs. `git apply --check` passed in a clean sparse checkout whose `HEAD` was verified as `ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda`.

That check verifies patch paths and context only. No patch was applied to the source checkout, and the patch was not formatted, compiled, unit-tested, linked, packaged, or runtime-tested. It is a design artifact, not a build-tested correction.

## Error and behavior compatibility

- Every branch remains a reader initialization error; no branch returns success.
- `LocalHnswSegmentReaderError::code()` remains `ErrorCodes::Internal`.
- `LocalSegmentManagerError` and `ExecutorError` continue forwarding the internal classification.
- Python continues raising `chromadb.errors.InternalError` with the existing reader and executor prefixes and the appended diagnostic code/UUID.
- Backfill and purge continue catching the enriched variant and retaining their existing watermark-zero behavior.
- No retry, repair, index rebuild, cache change, persistence-threshold change, or new filesystem operation is added.
- The added string formatting happens only on these error branches. No synchronous logging or subscriber is introduced. The custom binary and added error formatting may still affect failure timing slightly; any instrumented observation must be treated as potentially timing-sensitive.

## Optional cache and persistence observations

Branch-coded errors are the minimum first patch. They identify which reader precondition failed, but they cannot show why that state occurred. If a separate investigation requires the cache/persistence timeline, a separate patch approval could add a fixed-size in-memory ring, flushed only when the matching error occurs. Possible bounded events are:

| Event | Capture point | Bounded fields |
|---|---|---|
| `hnsw.index_pool.lookup` | Reader/writer lookup result | Segment UUID, role (`reader`/`writer`), `hit` or `miss` |
| `hnsw.index_pool.evicted` | Pool eviction callback | Segment UUID, monotonic timestamp |
| `hnsw.backfill.completed` | After compaction backfill applies its chunk | Segment UUID, metadata watermark, HNSW watermark, replay count |
| `hnsw.index.persist.completed` | After index and ID-map persistence plus SQLite watermark update | Segment UUID, persisted sequence watermark |

These events must contain no per-record fields, embeddings, document content, unrestricted paths, or pickle data. A bounded ring should avoid synchronous logging on successful queries; flushing would occur only on the matching diagnostic error. Even then, event order is a captured timeline and may be perturbed by instrumentation; it is not by itself proof of root cause. This optional instrumentation is not part of the patch artifact.

## Separate build and runtime approval gate

A future disposable build should, at minimum:

1. Start from the verified Chroma source commit and `Cargo.lock`; apply the patch in a clean isolated source tree and record its diff hash.
2. Use the source-pinned Rust 1.92.0 toolchain and record the actual `rustc`/`cargo`, MSVC compiler, Windows SDK, and target architecture. The repository does not pin the Windows toolchain versions.
3. Use the source packaging configuration: Maturin build backend `>=1.0,<2.0`, PyO3 0.24.1 with `abi3-py39`; select and record an exact Maturin version. Verify the resulting Windows wheel tag and extension ABI.
4. Build outside RAG-PDF and `rag-env`. Preserve build logs and hash the wheel and native extension. Record that this is a diagnostic binary from modified source, not an official Chroma release artifact.
5. Install only into a separately approved disposable environment after source, artifact, ABI, and environment identity review. Do not replace the project's installed Chroma package.

If a runtime attempt is separately approved, cap it at **one** fresh child execution of the existing isolated synthetic retrieval case that previously failed, using one unique temporary Chroma root and sequential query flow. Retries, matrices, cache overrides, repair, and rebuilding the index are outside that budget. If it passes without producing the error, stop as inconclusive. If it fails, preserve the original Python traceback and diagnostic code/segment UUID, then stop for review before any additional run.

## What the diagnostic would establish

If captured at the failing query, a code would establish which `from_segment()` condition produced the reader error for the named vector segment, conditional on the diagnostic binary matching the reviewed patch. It would not establish why that directory, pickle, or ID map had that state; prove a cache eviction; establish persistence durability; confirm the installed production binary had identical behavior; or resolve the intermittent incident. One successful run would not establish reliability.

## Evidence, preservation, and release status

Evidence workspace:

`C:\Users\abhis\AppData\Local\Temp\rag-pdf-615b-615c-d3dd84146b944c9098ac98e1a338dd4b`

It contains the pinned raw source files, source manifest inputs, sparse source checkout, patch snapshot, applicability-check snapshot, and patch artifact. All are outside the RAG-PDF repository and `rag-env`.

No native build, package install, Chroma query, retrieval matrix, index access, production code change, or dependency change occurred. The requirements proposal remains unstaged and unchanged. The Phase 6.15B conclusion remains **plausible cache/persistence lifecycle hypothesis; causality unproven**.

- HNSW root cause and reliability qualification remain unresolved.
- The natural artifact-publisher integrity incident remains unresolved.
- Two-pin dependency adoption remains deferred; clean installation remains unqualified.
- Chroma migration is not authorized.
- Unattended general-purpose deployment remains **NO-GO**.
