# Phase 6.15D — HNSW native diagnostic build gate

- **Review date:** 2026-11-03
- **Starting RAG-PDF revision:** `af1ebe583b90ed906fe5d1b00b37b1580b0bfca5`
- **Phase 6.15C checkpoint:** `5cddbe7d24e1f6727bdd0f9c275cc5f1c1703163` (parent `af1ebe583b90ed906fe5d1b00b37b1580b0bfca5`; one file: `docs/audit/hnsw-native-diagnostic-design-615c.md`)
- **Pinned Chroma source:** [`ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda`](https://github.com/chroma-core/chroma/tree/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda)
- **Decision:** **C — defer native build approval** until a complete pinned-source audit and build-prerequisite gate are available. This is not a finding that the patch is defective.

## Scope and decision

In the audited files, the diagnostic patch is source-consistent with the three `LocalHnswSegmentReader::from_segment()` branches, preserves reader rejection and internal error classification, and carries a useful branch code plus vector-segment UUID through the normal query error chain. Both inspected compaction handlers still treat every enriched `UninitializedSegment` as watermark zero. No behavioral defect was found in these paths.

The three outcomes point to distinct next investigations: segment path/mapping lifecycle, metadata persistence and backfill/cache lifecycle, or ID-map construction and persistence. That information has potential decision value. However, the current checkout is incomplete, a whole-repository reference search could not be completed, and required build tools are unavailable. The patch changes a publicly visible Rust enum variant shape, so uninspected workspace consumers could be affected. I therefore defer build approval until source and compatibility review are complete. Build feasibility is **not established**.

## Patch and source provenance

Patch artifact:

`C:\Users\abhis\AppData\Local\Temp\rag-pdf-615b-615c-d3dd84146b944c9098ac98e1a338dd4b\hnsw-branch-diagnostic-615c.patch`

Verified SHA-256: `276D25F5BA771A33BC4902A904B8C6E2E31C632D02F5BE27FAD44B28CE969920`.

The 14-file source manifest is `source-files.sha256.json` in the same evidence workspace; its SHA-256 is `A72BE362316346B40295D4361213D70A1A5F105DC8ECA3023D240C20ED759AE9`. Each raw snapshot's SHA-256 and Git blob ID were checked against the pinned source. The two files changed by the patch are:

| File | Original SHA-256 | Pinned Git blob |
|---|---|---|
| `rust/segment/src/local_hnsw.rs` | `45568617EC3BEDCD33B3A811079E8C525EE649F26E2A90E2E07EDCA2D7C09E57` | `abd5b9885bb986699f68160903530ec7e4e034fe` |
| `rust/log/src/local_compaction_manager.rs` | `ADEFDB58B57E89F509AB92C4CDDFA8E97774290B1CF3F85DC92AABA55F92EA8B` | `41b230222b8f65c46f6981f866ef99ef389e2a98` |

The clean sparse source checkout is at the exact pinned commit and its two base blobs match those IDs. The patch applicability check passed. The patch changes exactly the two files in the table. **Applicability establishes source context only; it does not establish Rust type correctness, compilation, or runtime behavior.**

The checkout has sparse patterns for five of the 35 Rust workspace members. A whole-tree `git grep` could not read the omitted partial-clone blobs, so the reference search is exhaustive only within the available sparse source, not across the complete pinned repository. The available tree contains the reader definition/constructors and the two compaction matches described below. Since the enum is public, a complete source-tree search is required before treating the internal reference audit as complete.

## Branch mapping and patch semantics

| Diagnostic code | Existing reader condition | Review result |
|---|---|---|
| `HNSW_READER_DIRECTORY_MISSING` | `!index_folder.exists()` | Attached to the existing early return. |
| `HNSW_READER_METADATA_MISSING` | The segment path passed the first existence check, but `!pickle_file_path.exists()` | Attached to the existing final uninitialized-reader return. |
| `HNSW_READER_ID_MAP_EMPTY` | Metadata deserialized successfully and `id_map.id_to_label.is_empty()` | Attached to the existing empty-map return. |

The codes are unique literals and distinguish the three source branches. They identify which existing predicate returned the error; they do not independently prove the underlying operating-system state. Rust `Path::exists()` can return false when metadata lookup fails, not only when a path is truly absent. Also, if the segment path exists but is not a directory, the later metadata-path check may report the metadata-missing branch. A race or open error after the metadata existence check remains the existing `PickleFileOpenError` path and receives no one of these codes. These limits do not change the intended branch attribution, but they constrain how a captured code should be interpreted.

Hunk-by-hunk review found:

- The reader enum changes from the unit variant to a struct variant containing a static code and the segment UUID string.
- The three old constructors receive the corresponding code and `segment.id.to_string()`.
- The `ChromaError::code()` match changes only to destructure the new variant and still returns `ErrorCodes::Internal`.
- The reader filesystem checks, their order, metadata open/deserialization, ID-map check, HNSW load, and successful reader construction are unchanged.
- No retry, new filesystem operation, repair, rebuild, persistence, cache, or threshold behavior is added.
- The separate writer-side `UninitializedSegment` type and its code mapping remain unchanged.
- Error formatting adds the code and UUID after `Nothing found on disk`; no absolute path, user document, record content, embedding, pickle content, or SQLite value is included.

The code reports the reader branch; it does not claim that the branch's precondition arose from corruption or data loss.

## Error type, call path, and compatibility

The pinned source exposes `local_hnsw` as a public module and `LocalHnswSegmentReaderError` as a public enum. Changing `UninitializedSegment` from a unit to a struct variant is therefore a **Rust source API shape change** for downstream code that pattern-matches that variant. The repository's internal direct matches were located and updated. This is not a change to a Python-callable signature or a C/PyO3 ABI, but no downstream Rust consumer audit was performed.

For a direct KNN reader-construction failure, the pinned path is:

1. [`LocalHnswSegmentReader::from_segment()`](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/segment/src/local_hnsw.rs#L101-L185) constructs the enriched reader error.
2. [`LocalSegmentManagerError`](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/segment/src/local_segment_manager.rs#L75-L123) wraps it with `#[from]`, retains it in the display chain, and forwards `code()` from the nested error.
3. [`LocalExecutor::knn()`](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/frontend/src/executor/local.rs#L132-L196) wraps that manager error in `ExecutorError::Internal`; [`ExecutorError::code()`](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/types/src/api_types.rs#L2375-L2407) forwards the nested code.
4. [`QueryError`](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/types/src/api_types.rs#L2341-L2355) and the Python binding's [`ChromaPyError`](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/python_bindings/src/errors.rs#L13-L36) preserve the formatted error chain. `ChromaPyError` maps `ErrorCodes::Internal` to `chromadb.errors.InternalError` using the error's display text.

Thus the code and UUID remain in the displayed Python error on the direct query path, and the exception class remains `InternalError`. The human-readable message changes intentionally. The backfill and purge handlers are a separate path: they catch `UninitializedSegment` and use watermark zero, so an occurrence consumed there is not returned as the Python query error.

The error enum is not serialized in the inspected path. The workspace lockfile pins `thiserror` 1.0.69; the proposed named-field `#[error(...)]` formatting is conventional for this derive, but compilation remains unverified. No function signature, Python binding, package version declaration, or PyO3 ABI setting is changed. The patch adds no successful-query logging or event subscriber. Error-path string formatting and any future diagnostic binary can still perturb failure timing slightly.

## Compaction and log behavior

Both direct matches in [`rust/log/src/local_compaction_manager.rs`](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/log/src/local_compaction_manager.rs#L168-L180) and [the purge handler](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/log/src/local_compaction_manager.rs#L300-L314) were checked:

- Backfill's match changes to `UninitializedSegment { .. } => 0`.
- Purge's match changes to `UninitializedSegment { .. } => 0`.

The struct-pattern wildcard matches all three reason codes and any segment ID. Both handlers retain the existing zero-watermark behavior. Backfill still reads/replays logs and acquires/applies through the writer path; purge still calculates the same minimum watermark. Other `LocalSegmentManagerError` values continue through the existing error branch. The syntax is consistent with Rust struct-variant matching, but compilation was not run.

The reader error definition and constructors, its `code()` match, and the two compaction matches are all direct references found in the pinned sparse source. Similarly named writer, record, or SPANN variants in that available source are distinct and unchanged. References in the 30 omitted workspace members and external Rust consumers remain unverified.

## Outcome-to-action value

| Runtime observation | What it would justify next | What it would not establish |
|---|---|---|
| **A — directory branch** | Compare the vector-segment UUID with SQLite segment mappings; inspect persist-root/segment-path construction and segment creation/removal lifecycle. | Why the path lookup failed or whether a segment was deleted. The `exists()` predicate is not a complete OS-error diagnosis. |
| **B — metadata branch** | Inspect the segment's persisted watermark, threshold crossing, writer initialization, backfill/log replay, and cache transition at the query. | Data loss or corruption merely because the pickle path did not pass the existence check. |
| **C — empty ID-map branch** | Inspect the deserialized ID-map state, writer map construction, replayed records and watermark, and persistence timing. | Pickle corruption; this branch means deserialization succeeded and the map was empty. |
| **D — test passes without the error** | Retain the run as inconclusive and stop. | Reliability or a reason to repeat until failure. No retry is authorized by the proposal. |
| **E — error occurs without a branch code** | Verify the diagnostic wheel/extension identity and instrumentation coverage; inspect whether a different error path produced the failure. | That one of the three branches ran. |

These outcomes prioritize different investigations, so branch identity has decision value. Even a failure carrying a code would identify the reached condition only; it would not establish why the condition occurred, prove cache eviction, or demonstrate the historical native binary had the same build identity.

## Build readiness and missing prerequisites

The retained source checkout is **not complete enough to build**. Its sparse patterns include `rust/frontend`, `rust/log`, `rust/python_bindings`, `rust/segment`, and `rust/types`; the pinned root `Cargo.toml` declares 35 workspace members, of which 30 are absent. A future build requires a complete checkout of the exact commit, not the sparse review tree.

Pinned build configuration and current read-only tool lookup:

| Requirement | Evidence/status |
|---|---|
| Rust toolchain | `rust-toolchain.toml` pins Rust `1.92.0` and names `rustfmt`, `clippy`, and `llvm-tools-preview`; `rustc` and `cargo` are not on `PATH`. |
| Windows native toolchain | Target must be recorded for Windows AMD64/MSVC. `cl.exe` is not on `PATH`; `WindowsSdkDir`, `VCToolsInstallDir`, and `VisualStudioVersion` are unset. The available `link.exe` resolves to Git's `usr\bin\link.exe`, not evidence of an MSVC linker/toolchain. Exact MSVC and Windows SDK versions remain to be selected and recorded. |
| Python packaging | Pinned `pyproject.toml` uses Maturin `>=1.0,<2.0`; an exact Maturin version must be selected. Maturin is not on `PATH`. |
| PyO3 / ABI | Workspace pins PyO3 `0.24.1` with `abi3-py39`. For a CPython 3.10 Windows AMD64 wheel, expect a stable-ABI `win_amd64` wheel (commonly `cp39-abi3-win_amd64`); the actual built wheel tag and extension ABI must be verified. |
| Isolation and provenance | Use a disposable full source tree and isolated Python build/install environments; record source commit, patch/diff hash, tool versions, build logs, wheel hash, and native extension hash. Never replace the project's official Chroma wheel or mutate `rag-env`. |

No toolchain was installed, no build command was run, and build feasibility is not claimed. A later approval request should first make the missing source and toolchain inventory explicit, pin an exact Maturin release and MSVC/SDK versions, and establish the target/ABI verification procedure.

## Recommendation and bounded future gate

**Classification: C — defer native build approval.** The branch codes map to materially different investigations, and the proposed single-run plan has explicit stop rules. The patch is not known to be defective, but the incomplete source tree prevents an exhaustive Rust reference check and the current host lacks the pinned Rust toolchain, Maturin, MSVC compiler, and identified Windows SDK. Before requesting a build, complete the source-tree/API-reference audit and specify the full build environment. Patch applicability and the promising decision value are not sufficient to bypass those gaps.

If the source and prerequisite gaps are resolved and a later build/runtime is separately approved, bound the experiment to:

- Maximum budget: **one** diagnostic native build attempt and **one** runtime child invocation; **zero** retries.
- One diagnostic build from the complete pinned Chroma source plus this exact patch, in a disposable environment.
- One isolated Python environment containing only the resulting identified diagnostic wheel and required test dependencies.
- At most one fresh-process execution of the existing synthetic retrieval test that previously failed, with a unique temporary Chroma root and sequential flow.
- Zero retries, matrices, index repair/rebuild, cache overrides, user documents, or production indexes.
- Preserve original traceback, branch code/segment UUID, build and wheel hashes, process exit status, and cleanup evidence.
- Stop after either success or failure. A pass without the incident is inconclusive; a captured branch is evidence for a separate causal review, not a root-cause finding.

## Work not performed and release status

No patch was applied to RAG-PDF or the clean pinned-source checkout. No Rust formatting, compilation, Cargo test/build, Maturin build, wheel installation, Python native query, Chroma matrix, or full test suite was run. Patch applicability, source review, compilation, runtime behavior, root-cause diagnosis, and reliability qualification remain distinct gates; only applicability and source-level semantics were reviewed here.

Evidence remains under:

`C:\Users\abhis\AppData\Local\Temp\rag-pdf-615b-615c-d3dd84146b944c9098ac98e1a338dd4b`

- HNSW root cause and reliability qualification remain unresolved.
- The natural artifact-publisher incident remains unresolved.
- Two-pin dependency adoption remains deferred; the candidate requirements proposal is unchanged and unstaged.
- Chroma migration is unauthorized.
- Unattended general-purpose deployment remains **NO-GO**.

Repository preservation at this review: `requirements.txt` remains at candidate SHA-256 `EF3095DBC1A1657CCB5D5FCBA4D2DF245DD5EC9D04B6C2D237823FDFE472734F`; protected main remains `7e9522618296af6a7f25652cc8161009d9a92685`; the research fixture blob remains `25331e9324182002eb0333a5c56034e684e3c018`. Baseline verification confirmed all nine protected research files unchanged. The project's installed Chroma remains 1.5.0.
