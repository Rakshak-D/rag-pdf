# Phase 6.17C — Native HNSW source prerequisite closure

- **Review date:** 2026-11-03
- **RAG-PDF starting revision:** `2b73cb5b2ef83f4db89464f119cc66feee8dfebd`
- **Pinned Chroma source commit:** `ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda`
- **Pinned Chroma tree:** `8d683102c76eea17c4088f1c5de46a1c7cd1239a`
- **Classification:** `SOURCE_INPUTS_COMPLETE_PREREQUISITES_BLOCKED`
- **Scope:** Read-only source input retrieval and bounded local prerequisite inventory

## Decision

The previously omitted pinned protobuf source inputs are now present in the existing sparse Chroma checkout and match all nine Git tree blob identities. The eight `.proto` files total 47,333 bytes; the nine `idl/` files including `idl/makefile` total 48,771 bytes. All 558 previously audited Rust source files were independently checked against their pinned Git blobs and remain unchanged.

The source inputs are complete for the inspected workspace paths, but a native type-check is not ready to run. Rust 1.92.0 and `protoc` are absent from the inspected tool locations. Two MSVC installations and a Windows SDK are present, but no compiler/SDK pair is selected or activated. The default Cargo registry cache is incomplete against the lockfile inventory, and no Cargo Git cache exists; the pinned `hnswlib` source revision is not cached there. The selected dependency graph, additional downloads, output size and temporary storage requirements have not been measured. A complete `Cargo.lock` does not supply those measurements.

**Next approval request:** authorize a separate, bounded prerequisite-provisioning and verification task for the exact Rust 1.92.0 Windows MSVC target and a pinned `protoc`, using one of the existing MSVC installations with the existing SDK. That task should record provenance and resource use and must not invoke Cargo. After those prerequisites are verified, request separate authorization for Cargo dependency-graph resolution and resource measurement. No type-check or build is authorized by this report.

## Repository and artifact integrity

At the start, branch `production-evaluation-workbench` was at `2b73cb5b2ef83f4db89464f119cc66feee8dfebd`; the remote feature ref matched. Protected `main` was `7e9522618296af6a7f25652cc8161009d9a92685`. The protected `tests/fixtures/research-baseline.json` blob was `25331e9324182002eb0333a5c56034e684e3c018`.

The only RAG-PDF worktree change was the unstaged candidate `requirements.txt`, SHA-256 `EF3095DBC1A1657CCB5D5FCBA4D2DF245DD5EC9D04B6C2D237823FDFE472734F`; nothing was staged. The 6.17A and 6.17B conclusions were retained; the 558-file reference search was not repeated.

The existing disposable source checkout remained at commit `ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda`, tree `8d683102c76eea17c4088f1c5de46a1c7cd1239a`. Its sparse checkout was expanded only to include `idl/`. The tracked source worktree was clean after retrieval. The existing diagnostic patch remained unchanged at SHA-256 `276D25F5BA771A33BC4902A904B8C6E2E31C632D02F5BE27FAD44B28CE969920`.

## Pinned `idl/` inputs

The nine entries below were independently listed from the pinned Git tree. For every file, its checked-out byte length and computed Git blob SHA-1 matched the tree entry.

| Pinned path | Git blob | Bytes |
|---|---|---:|
| `idl/chromadb/proto/chroma.proto` | `a13abd54b0d545b5311b447ee2ec53b2dbd53ab7` | 10,491 |
| `idl/chromadb/proto/compactor.proto` | `db5ae95d21a038eef6138d3e3205e7f37b01d879` | 594 |
| `idl/chromadb/proto/coordinator.proto` | `d4706e1fc99f7a5350860ee11d67683951005d57` | 21,856 |
| `idl/chromadb/proto/debug.proto` | `6cd6bc73184114805028a7bad4032c253349c998` | 288 |
| `idl/chromadb/proto/garbage_collector.proto` | `afa3ca4e4fc20f7dc292d090b9aa216ff9b32ddd` | 295 |
| `idl/chromadb/proto/heapservice.proto` | `cd3d7f163f2fb28717582075c0eb35b39ead36f8` | 2,999 |
| `idl/chromadb/proto/logservice.proto` | `fa8f62cf8bd83ca47774dc64cb8baf3d7cb39729` | 6,262 |
| `idl/chromadb/proto/query_executor.proto` | `8bba83424cd8337baa1db37f9f9ea682531408ab` | 4,548 |
| `idl/makefile` | `18c746e4f41170683ac09498922d756d26dfbdbf` | 1,438 |
| **Total** | **9 entries** | **48,771** |

The source-body total is below the 1 MiB limit. Git transport bytes were not instrumented, so no network-byte total is claimed. Retrieval used the existing blob-filtered checkout and added only `idl/`; no history, dependencies, submodules, binaries or generated artifacts were requested.

The 558 `.rs` files under `rust/` were each rehashed as Git blobs against the pinned tree: 8,284,236 bytes verified, with no mismatch. The complete source tree identity remained unchanged.

## Protobuf build-script inputs

`rust/types/build.rs` has SHA-256 `3301ef062b4f069a40cc42ef9844c0e210a6abe50c9f3753cfc187faee21df95`. Its pinned source lists seven protobuf files at lines 3–10 and calls `compile_protos` with include root `idl/` at line 26. It adds `debug.proto` at line 21 when the build script is compiled with debug assertions. Thus a normal debug-profile type-check may require that eighth `.proto` input as well. `idl/makefile` is tracked input but is not named by this Rust build script. No protobuf code was generated.

The 6.17B report records `tonic-prost-build` 0.14 as the `chroma-types` build dependency and no `protoc-bin-vendored` package in the lockfile. The local `protoc` inventory below found no executable in the checked locations.

## Read-only prerequisite inventory

### Rust

`rust-toolchain.toml` pins channel `1.92.0` and requests `rustfmt`, `clippy`, and `llvm-tools-preview`; the target under consideration is `x86_64-pc-windows-msvc`. No `1.92.0` toolchain directory exists under the default `%USERPROFILE%\.rustup\toolchains`. The only installed directory is `stable-x86_64-pc-windows-msvc`; its local channel manifest identifies rustc `1.90.0 (1159e78c4 2025-09-14)`. Its x86_64 Windows MSVC standard-library directory and rustc/cargo executable files are present, but neither tool is on `PATH`; neither was executed, so the Cargo executable version is not independently reported. `rustup` is not on `PATH`. The older toolchain is not a substitute for the pinned version.

### MSVC and Windows SDK

Read-only Visual Studio installation enumeration found:

| Installation | MSVC files observed |
|---|---|
| Visual Studio 2022 Build Tools | `14.44.35207`; `cl.exe`, `link.exe`, `lib.exe`, `vcvars64.bat`, and x64 VC runtime files present |
| Visual Studio 18 Build Tools | `14.50.35717`; `cl.exe`, `link.exe`, `lib.exe`, `vcvars64.bat`, and x64 VC runtime files present |

The files are not proof of a working build environment. `cl.exe` is not on `PATH`; no setup script was activated. `VCToolsInstallDir`, `WindowsSdkDir` and `VisualStudioVersion` are unset, so the selected compiler/SDK pairing is unverified.

Windows SDK `10.0.26100.0` has the inspected x64 Windows/UCRT headers, `kernel32.lib`, `ucrt.lib`, and `rc.exe`. No Rust check established that either MSVC installation is compatible with this SDK or the pinned Rust target.

### Protobuf compiler

`protoc` is not on `PATH`. It was also absent from the checked known locations: Program Files Protocol Buffers, the Chocolatey bin directory, the user Scoop protobuf path, and the project virtual environment scripts directory. No version or provenance is available because no `protoc` executable was found. This was a bounded known-location inventory, not a recursive drive search.

### Cargo cache and pinned Git dependency

`CARGO_HOME` is unset, so the inspected default was `%USERPROFILE%\.cargo`. Its registry cache, index and source directories contained approximately 154.8 MB across 4,207 files at inspection time. A filename-only comparison found 33 of the lockfile's 950 registry-sourced package entries with matching `.crate` archive names. This does not verify archive hashes or identify the subset selected by the two target crates.

The lockfile has 992 package entries overall, including 950 registry sources and 7 Git sources. Those are lockfile-wide counts, not a resolved `chroma-segment`/`chroma-log` dependency graph. `%USERPROFILE%\.cargo\git` was absent, so no Cargo Git checkouts or object databases were present there. `Cargo.lock:4381–4385` pins `hnswlib` 0.8.2 to Git revision `312991cf9e640d5ccab879906a51e7612ce6fcf1`; that source was not cached in the inspected Cargo Git location.

### Disk and resource bound

At inventory time, the C: volume had approximately 41,926,922,240 bytes (39.04 GiB) free. This is a point-in-time free-space observation, not a build budget. Known source-body bytes are 48,771 for `idl/` and 8,284,236 for the audited Rust sources. The existing registry cache is about 154.8 MB.

No measured bound exists for the future check. Unknowns include the exact dependency graph selected for the two crates and target, missing registry/Git artifacts it would need, download bytes, build-script/native tool requirements, build output size, temporary storage, and execution time. Cargo resolution would be needed to establish the selected graph; that operation is expressly outside this phase and requires separate authorization. A complete lockfile and current free-space number do not establish that the check fits any resource limit.

## Classification and next gate

**`SOURCE_INPUTS_COMPLETE_PREREQUISITES_BLOCKED`** — all requested pinned source inputs are complete and verified, but the pinned Rust toolchain and protobuf compiler are missing, the MSVC/SDK pair is not selected, and the dependency/resource plan is unmeasured.

The next operator decision should authorize only a bounded prerequisite-provisioning and verification scope: obtain or verify official Rust 1.92.0 components for `x86_64-pc-windows-msvc` and a pinned `protoc`, then select and validate one existing MSVC/SDK pairing. Record provenance, bytes and disk use. Do not run Cargo as part of that request. Once prerequisites are verified, dependency-graph resolution and resource measurement require a separate approval. Do not propose `cargo check` while those gates remain open.

## Work not performed and release status

Cargo executions: **0**. Native compilation attempts: **0**. Rust/toolchain installations: **0**. Dependency downloads: **0**. Protobuf generation: **0**. Publisher/retrieval tests: **0**. Production changes: **0**. The patch, `requirements.txt`, `rag-env`, and Chroma installation were not modified.

Phase 6.15D remains **C — native build deferred**. The HNSW and natural publisher incidents remain unresolved; dependency adoption remains deferred; Chroma migration remains unauthorized; unattended deployment remains **NO-GO**.

## Prior checkpoints

- [Phase 6.17A complete Rust reference audit](hnsw-full-rust-reference-audit-617a.md)
- [Phase 6.17B native type-check approval gate](hnsw-native-typecheck-approval-gate-617b.md)
