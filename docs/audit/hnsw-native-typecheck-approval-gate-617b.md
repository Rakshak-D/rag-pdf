# Phase 6.17B — Native HNSW diagnostic type-check approval gate

- **Review date:** 2026-11-03
- **RAG-PDF starting revision:** `5e104a8729bfcb322a945bdfc3f6904ab01c7fad`
- **Pinned Chroma source:** `ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda`
- **Pinned source tree:** `8d683102c76eea17c4088f1c5de46a1c7cd1239a`
- **Patch SHA-256:** `276D25F5BA771A33BC4902A904B8C6E2E31C632D02F5BE27FAD44B28CE969920`
- **Classification:** **APPROVAL_PREREQUISITES_INCOMPLETE**
- **Scope:** Read-only build-input and environment review; no compilation or native execution

## Decision summary

The smallest useful Rust type-check target is the library targets for `chroma-segment` and `chroma-log`. The changed reader implementation is in the first crate; the changed compaction patterns are in the second, which depends on `chroma-segment`. A single `cargo check` selecting both libraries would type-check the direct changed source and their selected transitive Rust dependency graph without building tests, examples, the Python extension, or the full workspace.

The current host cannot safely run that check under the source-pinned toolchain: Chroma pins Rust 1.92.0, while the only installed toolchain inventory found is Rust 1.90.0. Rust/Cargo binaries exist inside that toolchain directory but neither is on `PATH`; `rustup` is not on `PATH`. `protoc` is also absent from `PATH`, while `chroma-types` has a build script that compiles protobuf definitions and the lockfile contains no `protoc-bin-vendored` package. MSVC and Windows SDK files are present on disk, but no developer environment is active and the exact compiler/SDK pair is unselected. Dependency-cache completeness, downloads, disk use, and bounded execution time are not established.

**Recommendation:** retain the existing two-file patch for a disposable, never-distributed diagnostic artifact. Its public Rust source API break is contained to downstream Rust source consumers; it does not by itself change the Python callable API or PyO3 ABI. Do not compile until the missing toolchain, protobuf compiler, dependency-source/cache, and resource gates are separately resolved and approved.

## Integrity review

The local and remote RAG-PDF feature refs were both `5e104a8729bfcb322a945bdfc3f6904ab01c7fad`. Protected `main` was `7e9522618296af6a7f25652cc8161009d9a92685`; the protected fixture blob was `25331e9324182002eb0333a5c56034e684e3c018`. The only initial worktree change was unstaged `requirements.txt`, SHA-256 `EF3095DBC1A1657CCB5D5FCBA4D2DF245DD5EC9D04B6C2D237823FDFE472734F`; nothing was staged.

The existing source checkout at `C:\Users\abhis\AppData\Local\Temp\rag-pdf-617a-82eaff1a11ef4bb387f776b84b211513` was at the exact Chroma commit/tree and had clean Git status. The existing patch at `C:\Users\abhis\AppData\Local\Temp\rag-pdf-615b-615c-d3dd84146b944c9098ac98e1a338dd4b\hnsw-branch-diagnostic-615c.patch` had the expected SHA-256. Its only paths are:

- `rust/segment/src/local_hnsw.rs`
- `rust/log/src/local_compaction_manager.rs`

The reviewed prior evidence is [Phase 6.15C](hnsw-native-diagnostic-design-615c.md), [Phase 6.15D](hnsw-native-build-gate-615d.md), and the complete-source [Phase 6.17A audit](hnsw-full-rust-reference-audit-617a.md). The 617A whole-workspace search is reused; it was not repeated.

## Pinned build inputs inspected

| Input | Pinned identity / observation |
|---|---|
| Root `Cargo.toml` | Git blob `7e990d11b44fb5968f65986acf0e0f16de8d555d`; SHA-256 `8CDF0224CB1560D18497C0BA46B1C0F3577D624663B42AE421F7668B21F7198F`; workspace resolver 2, 35 members |
| Root `Cargo.lock` | Git blob `b224b3be953adc2dcfbb5f9850c8a1e174206202`; SHA-256 `D0B2ECF987173344EA347D15D8DF203A3D30D9C237354A8B163AF40F448828D2` |
| `rust-toolchain.toml` | Pins Rust `1.92.0`; requests `rustfmt`, `clippy`, and `llvm-tools-preview` components |
| `rust/segment/Cargo.toml` | Package `chroma-segment`; no crate feature table; ordinary library dependencies include `chroma-index`, `chroma-types`, `chroma-sqlite`, and other workspace crates |
| `rust/log/Cargo.toml` | Package `chroma-log`; no crate feature table; depends directly on `chroma-segment`, `chroma-types`, `chroma-sysdb`, `chroma-sqlite`, and others; requests `chroma-tracing/grpc` and `chroma-error/sqlx` |
| `rust/index/Cargo.toml` | `default = []`; `usearch` is optional; `hnswlib` is a non-optional dependency used by the segment dependency graph |
| `rust/types/Cargo.toml` and `rust/types/build.rs` | `tonic-prost-build = "0.14"`; build script calls `compile_protos` for files under `idl/chromadb/proto` and includes `debug.proto` when debug assertions are enabled |
| `rust/python_bindings/Cargo.toml` | Python binding is a `cdylib`; depends on both changed crates and PyO3 |
| Root `pyproject.toml` | Maturin backend `>=1.0,<2.0`, manifest `rust/python_bindings/Cargo.toml`, feature `pyo3/extension-module` |
| `Dockerfile.windows` | Not a Rust builder definition; it downloads/installs the VC redistributable and installs the Python project |

The current sparse source checkout includes `rust/` and the Cargo root manifests, but not the root `idl/` proto inputs. A later compile workspace must include those exact pinned build inputs. No Cargo config file was found under `.cargo`; the root has `.config/nextest.toml`, which configures test runs rather than this library check.

## Is the two-crate scope sufficient?

Yes, for the patch's direct type-level changes. `local_hnsw.rs` is part of `chroma-segment`; `local_compaction_manager.rs` is part of `chroma-log`. `chroma-log` depends on `chroma-segment`, and the complete Phase 6.17A audit found only the two known direct HNSW-reader-variant matches in the log crate. Selecting both package libraries makes the scope explicit and avoids tests, examples, and unrelated workspace members.

Proposed command for a **separately approved future check only**:

```text
cargo check --locked --target x86_64-pc-windows-msvc --package chroma-segment --package chroma-log --lib
```

Run from a clean full source copy at the pinned commit with the exact patch applied and its hash recorded. Do not add `--workspace`, `--all-targets`, `--all-features`, or test commands. The package manifests have no crate-level feature sets to enumerate; normal/default dependency features and explicit dependency features remain active. The resolved feature graph must be recorded by the approved execution rather than inferred from the command alone.

This scope covers the changed library source and the selected dependency graph. It does not check the Python binding crate, build/link the extension wheel, validate the Python exception at runtime, or test the diagnostic branch.

## Build scripts, macros, and native prerequisites

`rust/types/build.rs` is a relevant transitive build script for both selected crates through `chroma-types`. It compiles protobuf inputs during a normal check; the pinned sparse checkout currently omits those inputs. The build dependency is `tonic-prost-build` 0.14. The lockfile has no `protoc-bin-vendored` package, and read-only `PATH` inspection found no `protoc.exe`; the exact `protoc` version, source, and availability are therefore unresolved prerequisites.

The selected graph also reaches the pinned `hnswlib` Git dependency. The root manifest names the Chroma hnswlib repository and branch; `Cargo.lock` resolves it to commit `312991cf9e640d5ccab879906a51e7612ce6fcf1`. Its lock entry depends on `cc`; the dependency source itself was not fetched or inspected. The SQLx/SQLite path includes `libsqlite3-sys`, whose locked dependencies include `cc`, `pkg-config`, and `vcpkg`. These establish potential native C/C++ build and discovery steps. Cargo may execute third-party build scripts and compile native code even for `cargo check`.

Rust derive/procedural macro dependencies such as `thiserror`, `serde`, `async-trait`, and protobuf generation are part of the selected graph. Cargo must compile and execute the relevant build scripts and proc-macro host artifacts. `--locked` prevents lockfile changes; it does not disable build scripts, native compilation, Git/registry access, or downloads.

## Windows and toolchain inventory

Read-only inspection found:

- `rust-toolchain.toml` requires Rust 1.92.0.
- The only toolchain directory found under `%USERPROFILE%\.rustup\toolchains` is `stable-x86_64-pc-windows-msvc`; its local channel manifest identifies Rust **1.90.0**. It has the x86_64 Windows MSVC standard library, but not the pinned 1.92.0 toolchain or requested extra components.
- `rustc.exe` and `cargo.exe` exist inside that 1.90.0 toolchain directory but are not on `PATH`. `rustup.exe` is not on `PATH`. Neither executable was launched.
- `cl.exe` is not on `PATH`. MSVC Build Tools files exist for versions `14.44.35207` (VS 2022 Build Tools) and `14.50.35717` (VS 18 Build Tools). No exact version was selected or activated.
- Windows SDK directories exist, including `10.0.26100.0` and older versions. `WindowsSdkDir`, `VCToolsInstallDir`, and `VisualStudioVersion` are unset; the compiler/SDK pairing and environment are not validated.
- `protoc.exe` and `maturin.exe` are not on `PATH`.
- `CARGO_HOME` and `RUSTUP_HOME` are unset. Cargo cache completeness and artifact availability were not established.

The target-specific readiness gate is therefore not met. The presence of tool files and SDK directories is not proof that the pinned Rust compiler, linker, headers, libraries, or build scripts work together.

## Public Rust API compatibility choices

### Option 1 — Retain the current patch for a disposable diagnostic binary (recommended)

The patch changes the public `LocalHnswSegmentReaderError::UninitializedSegment` from a unit variant to a struct variant containing the branch code and segment UUID. It updates the three local constructors/code mapping and the two compaction patterns. `LocalSegmentManagerError` and `CompactionManagerError` continue to wrap the error; the normal chain preserves the enriched display and `Internal` classification.

This breaks Rust source code that directly constructs or exhaustively matches that public variant. The complete 617A audit covers internal Chroma source only; external Rust source consumers remain unknown. For a one-off binary confined to a task-owned environment and never published, adopted, or distributed, that source-compatibility risk is acceptably bounded. The patch does not change a Python-callable signature or the PyO3 ABI by itself; Python would continue to receive the existing `InternalError` with an enriched message, subject to compile/runtime verification. Do not use this diagnostic package as a replacement for the official Chroma distribution.

### Option 2 — Preserve the public unit-variant shape

The unit variant carries no branch reason or segment UUID, so the existing error-display propagation cannot expose equivalent branch diagnostics while leaving that value unchanged. A separate private context object could preserve the original public enum, but the context would need to travel from `LocalHnswSegmentReader::from_segment()` through the segment manager and the executor/Python error conversion. That requires a new parallel result/error path or side channel and wider changes than the existing two-file patch. Adding new public enum variants would retain the old variant but still risk breaking exhaustive downstream matches. Tracing at the branch would not guarantee that the reason reaches the Python exception or retained diagnostic output. No alternative was implemented; its type correctness and compatibility are unproven.

For a disposable investigation, Option 1 is the smaller controlled design. For any generally distributed Chroma package, public API compatibility would require separate review.

## Proposed future execution boundary

This plan is for a later approval only. No current authorization is requested or implied.

1. Verify the exact source commit/tree, `Cargo.lock` SHA-256, two-file patch SHA-256, and a clean disposable full source copy. Include the pinned `idl/chromadb/proto` inputs and record the applied diff hash.
2. Before invocation, provision and verify Rust 1.92.0 for `x86_64-pc-windows-msvc`, the exact MSVC compiler and Windows SDK, and a pinned `protoc` executable compatible with the pinned build script. Do not allow rustup or Cargo to install components implicitly during the check.
3. Establish whether the exact locked registry crates and hnswlib Git commit are already available in a task-owned cache. If not, separately approve the official sources and explicit network policy. A complete dependency download size and cache inventory were not established here; no finite download/disk bound is claimed.
4. Use a new task-owned `CARGO_HOME`, `CARGO_TARGET_DIR`, and build working directory outside RAG-PDF and `rag-env`. Define timeout, maximum network bytes, and minimum free disk from a measured preflight before approval. Current evidence does not support numeric limits.
5. If all preconditions and limits are approved, execute the single `cargo check --locked --target x86_64-pc-windows-msvc --package chroma-segment --package chroma-log --lib` command once. Capture the exact command, environment identities, output, exit code, elapsed time, dependency sources, build-script execution, and produced target-directory size. No automatic retry or constraint changes.
6. Stop on any missing source/tool, unexpected download, resource-limit approach, lockfile modification, build-script/native compiler failure, or source/patch identity mismatch. Preserve logs and partial task-owned outputs; do not install or run a resulting artifact.

A successful check would establish Rust type-checking for the selected libraries and resolved default feature graph on the pinned Windows target, under that exact source and patch. It would not establish final linking, PyO3 integration, wheel ABI, Python import/error behavior, native branch execution, or the incident root cause.

For a later runtime diagnostic, a full Python extension wheel build remains necessary: the Python-binding crate is a `cdylib` PyO3 crate and the project packages it through Maturin. That build, wheel installation into an isolated environment, and one bounded retrieval attempt each require separate approval. The type-check request grants none of them.

## Classification and approval question

**Classification: `APPROVAL_PREREQUISITES_INCOMPLETE`.** The minimum crate scope and command can be identified, and the current public-API tradeoff is manageable for a private disposable binary. However, the exact pinned compiler is absent from the inspected toolchain inventory, `protoc` is unavailable on `PATH`, MSVC/SDK activation is unverified, and dependency/cache/download/disk/time bounds remain unknown.

The precise future question is: **After the pinned Rust 1.92.0/MSVC target, exact MSVC/SDK pair, `protoc`, source inputs, dependency sources, and measured resource caps are identified, do you separately authorize one isolated `cargo check --locked --target x86_64-pc-windows-msvc --package chroma-segment --package chroma-log --lib` attempt, with no install or runtime execution?**

## Work not performed and release status

Native compilation attempts: **0**. Cargo executions: **0**. Toolchain installations: **0**. Dependency downloads: **0**. Publisher/retrieval test invocations: **0**. Production code changes: **0**. No package, cache, Chroma index, or dependency proposal was changed.

Phase 6.15D remains **C — native build deferred**. HNSW reliability and root cause remain unresolved; the natural publisher incident remains unresolved; dependency adoption remains deferred; Chroma migration remains unauthorized; unattended deployment remains **NO-GO**.
