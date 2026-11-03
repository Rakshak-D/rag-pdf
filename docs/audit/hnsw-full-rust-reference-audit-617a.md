# Phase 6.17A — Complete pinned Rust reference audit

- **Audit date:** 2026-11-03
- **RAG-PDF starting revision:** `c0a4525eb9ee46def0f9d1d05a93ff7a213ca779`
- **Pinned Chroma source:** [`ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda`](https://github.com/chroma-core/chroma/tree/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda)
- **Pinned Git tree:** `8d683102c76eea17c4088f1c5de46a1c7cd1239a`
- **Scope:** Read-only Rust-source reference audit; no compilation, Cargo command, build, installation, or runtime test
- **Classification:** **REFERENCE_AUDIT_COMPLETE_NO_NEW_INTERNAL_USERS**

## Summary

The complete pinned `rust/` source contains no additional internal consumer of `LocalHnswSegmentReaderError::UninitializedSegment` beyond the reader implementation and the two already identified matches in `local_compaction_manager.rs`. The proposed unit-to-struct-variant change would require updating those two matches; no further internal Rust source edit was identified. The manager error wrapper and the log error wrapper propagate the error but do not pattern-match this variant.

This closes the incomplete-workspace reference-search gap recorded in Phase 6.15D. It does not establish Rust type correctness, compilation, ABI/package compatibility, or compatibility for external Rust consumers. Phase 6.15D remains **C — defer native build approval**.

## Repository preflight

At the start, the local branch was `production-evaluation-workbench` at `c0a4525eb9ee46def0f9d1d05a93ff7a213ca779`, with parent `b9a564bd4516ee160b469e7d3b29ef7d2bebf3f8`. The remote feature ref matched the starting SHA. Protected `main` was `7e9522618296af6a7f25652cc8161009d9a92685`; `tests/fixtures/research-baseline.json` resolved to blob `25331e9324182002eb0333a5c56034e684e3c018`.

The only initial worktree change was unstaged `requirements.txt`, SHA-256 `EF3095DBC1A1657CCB5D5FCBA4D2DF245DD5EC9D04B6C2D237823FDFE472734F`. No files were staged. That deferred two-pin dependency proposal was preserved byte-for-byte.

The reviewed project records were:

- [Phase 6.15C diagnostic design](hnsw-native-diagnostic-design-615c.md)
- [Phase 6.15D native-build gate](hnsw-native-build-gate-615d.md)
- [Phase 6.15B cache/persistence lifecycle audit](hnsw-reader-cache-persistence-615b.md)

The 6.15D classification remains C. This source audit does not authorize a native build or runtime experiment.

## Pinned-source retrieval and completeness

Source was retrieved from the official `chroma-core/chroma` Git remote into a fresh temporary workspace outside RAG-PDF and `rag-env`:

```text
git -c protocol.version=2 fetch --depth=1 --filter=blob:none origin ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda
git sparse-checkout init --cone
git sparse-checkout set rust Cargo.toml Cargo.lock
git checkout --detach ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda
```

The workspace is `C:\Users\abhis\AppData\Local\Temp\rag-pdf-617a-82eaff1a11ef4bb387f776b84b211513`. The fetch was shallow and blob-filtered; the resulting Git pack was 3.01 MiB. No submodule update, release-binary retrieval, dependency retrieval, or build command was run. The sum of tracked Rust source bodies under `rust/` is 8,284,236 bytes, below the 64 MiB source-body limit.

Completeness was checked against both the fetched Git object database and GitHub's immutable tree API:

| Check | Result |
|---|---|
| Commit | `ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda` |
| Tree SHA | `8d683102c76eea17c4088f1c5de46a1c7cd1239a` |
| Recursive tree entries | 2,759; GitHub API response was not truncated |
| Tracked `.rs` files under `rust/` | 558 |
| Aggregate pinned `.rs` body size | 8,284,236 bytes |
| Checked-out Rust paths and pinned path set | Exact set match |
| Per-file content-to-Git-blob checks | 558/558 matched; zero mismatches |
| Root manifests | `Cargo.toml` and `Cargo.lock` both matched their pinned blob IDs |
| Disposable checkout status | Clean; no diagnostic patch applied |

The root manifest blobs are `Cargo.toml` `7e990d11b44fb5968f65986acf0e0f16de8d555d` and `Cargo.lock` `b224b3be953adc2dcfbb5f9850c8a1e174206202`. All `.rs` blobs in the pinned repository tree are under `rust/`; no Rust source file outside that directory was omitted.

## Search method

The audit searched all 558 pinned Rust files for the exact reader error type, `UninitializedSegment`, and `local_hnsw` module paths. It separately inspected imports, enum definitions, constructors, matches, `#[from]` error wrappers, `code()` forwarding, public module exposure, and relevant `use`/`pub use`/alias locations. No alias or re-export introduces an additional reader-error consumer. The search includes ordinary source-level macro invocations that spell these identifiers in Rust text.

The exact identifier `LocalHnswSegmentReaderError` appears in only these three source files:

| File | Line(s) | Symbol/variant | Usage type | Impact of proposed variant-shape change | Confidence |
|---|---:|---|---|---|---|
| `rust/segment/src/local_hnsw.rs` | 34, 59–72, 107–359 | Reader error enum, `ChromaError` mapping, reader method result types, and three `UninitializedSegment` constructors at 122, 169, 173 | Definition and internal implementation | The definition, three constructors, and the variant arm in `code()` must carry/match the new fields. Other reader method signatures remain unchanged. | High for pinned source text |
| `rust/segment/src/local_segment_manager.rs` | 14–16, 78, 88 | Import, `LocalSegmentManagerError::LocalHnswSegmentReaderError(#[from] ...)`, and nested error-code forwarding | Error propagation; not a variant pattern match | No additional edit indicated. The wrapper accepts and forwards the enum value without destructuring `UninitializedSegment`. | High for pinned source text |
| `rust/log/src/local_compaction_manager.rs` | 8, 95, 175–177, 307–309 | Import, `HnswReaderError(#[from] ...)`, backfill match, purge match | Error propagation plus two direct variant matches | The two matches must use a struct pattern such as `UninitializedSegment { .. }`; the wrappers need no shape-specific change. These are the two already identified consumers. | High for pinned source text |

`rust/segment/src/lib.rs:5` declares `pub mod local_hnsw`, so the public enum remains reachable through the public module path. The patch changes the Rust source API shape of a public enum variant. This audit covers internal source in the pinned repository only; external crates using that public variant were not searched.

## Same-named variants excluded from the reader-error result

The plain variant spelling `UninitializedSegment` also appears on distinct error types. Those occurrences are not uses of `LocalHnswSegmentReaderError` and are unaffected by this particular reader-enum change:

| Distinct type | Definition | Why it is unrelated |
|---|---|---|
| `LocalHnswSegmentWriterError` | `rust/segment/src/local_hnsw.rs:425–435` | Separate writer error enum in the same module; its variant and code mapping at line 467 are not changed by the proposed reader patch. |
| `RecordSegmentReaderCreationError` | `rust/segment/src/blockfile_record.rs:710–712` | Separate record-segment error used by record/metadata/worker code, with its own constructors and matches across the workspace. |
| `SpannSegmentReaderError` | `rust/segment/src/distributed_spann.rs:415–431` | Separate SPANN reader error with its own consumer in `rust/worker/src/execution/orchestration/spann_knn.rs:199`. |

The full token search found no ambiguous unqualified `Self::UninitializedSegment` use for the HNSW reader enum. Matches on the record, writer, and SPANN enums are not additional consumers of the reader enum.

## Compatibility implications and limits

The existing diagnostic patch changes only `rust/segment/src/local_hnsw.rs` and `rust/log/src/local_compaction_manager.rs`. The whole-workspace source search supports that internal file scope: no additional direct constructor or pattern-match site needs to be added based on pinned Rust text. The `LocalSegmentManagerError` and `CompactionManagerError` wrappers use `#[from]` and retain propagation rather than matching the changed variant.

This result does not show that the patch compiles or that a struct-variant match is type-correct. No Rust compiler, Cargo, Maturin, or native build was run. It does not audit downstream Rust crates, prove ABI/package compatibility, or map the installed Chroma extension to this exact source commit. Text search also cannot inspect macro-expanded code that is generated without the identifier appearing in source, build-time generated Rust outside the tracked tree, or consumers outside this repository. These are not observed extra consumers; they are residual scope limits.

Because the variant is public, preserving the unit variant shape and carrying diagnostics through a separate internal error/context mechanism could be considered as a separate design review if downstream source compatibility matters. No alternative was implemented or evaluated here, and the existing patch was not changed.

## Classification and next decision

**Classification: `REFERENCE_AUDIT_COMPLETE_NO_NEW_INTERNAL_USERS`.** The complete pinned Rust source search found no additional internal reader-error consumer beyond the reader implementation, the segment-manager propagation wrapper, and the two previously known compaction matches. No new internal match requiring a patch edit was found.

The next engineering decision is whether to request a separate Rust type-check/build review of the existing diagnostic patch, taking account of the public enum API shape. This audit alone is not build approval. External Rust consumers, Rust type correctness, compilation, ABI/package validation, exact installed-binary provenance, and runtime behavior remain unverified. Phase 6.15D stays **C — native-build approval deferred**; the HNSW root cause remains unknown and unattended deployment remains **NO-GO**.

No source, test, diagnostic patch, installed package, dependency, Chroma index, or production behavior was changed. No build, installation, or runtime experiment occurred.
