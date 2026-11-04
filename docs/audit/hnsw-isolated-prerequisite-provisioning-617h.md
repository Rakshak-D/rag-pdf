# Phase 6.17H — Isolated HNSW prerequisite provisioning

**Date:** 2026-11-04
**Classification:** `PARTIAL_PROVISIONING`

## Checkpoint and scope

Starting branch: `production-evaluation-workbench` at `3d9623beda2b77b36ea9b35aa12bd6afd2773441`. Protected `main` was `7e9522618296af6a7f25652cc8161009d9a92685`; the research baseline fixture blob was `25331e9324182002eb0333a5c56034e684e3c018`.

The only pre-existing worktree change was the unstaged candidate `requirements.txt`, SHA-256 `EF3095DBC1A1657CCB5D5FCBA4D2DF245DD5EC9D04B6C2D237823FDFE472734F`. It remains unchanged and unstaged. The nine-file baseline verifier passed before and after provisioning. No repository production or research source was modified.

All provisioning stayed in a unique task-owned directory under `%LOCALAPPDATA%\Temp` (attempt `2d363aa143434e7f8f5c28b9b1ad3abb`). Existing Phase 6.17E/G metadata and hashes were verified and reused. Phase 6.17G's raw responses independently support its four metadata findings, while its capture runner's exit code 2 remains disclosed in that report; it is not represented here as a successful runner exit.

## Download and mirror controls

The pinned Rust manifest SHA-256 was `b2a49624353173ecdacf59c158c00929300606e1963f6e4609fb483a508402d0`; the protobuf release metadata and Rustup checksum sidecar matched their retained identities. A local synthetic test verified durable transfer-log capture, rejection of over-limit writes, and process-tree cancellation. Initial C: free space was 46,196,645,888 bytes; the configured limits were 2,147,483,648 application payload bytes, 8 GiB task footprint, and 25 GiB minimum free space.

One sequential acquisition pass made nine HTTP requests for eight artifacts. Eight artifacts matched their pinned byte counts and SHA-256 values; aggregate response-body bytes written were exactly **170,257,354**. One GitHub release redirect went to `release-assets.githubusercontent.com`; its query string was not retained. Other artifact responses were HTTP 200. No retry or second artifact acquisition occurred.

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| rustc 1.92.0 MSVC x64 | 71,972,056 | `6aec5aba5384ce1041538e71ebebf671d82e29f4fe697bc7844626d94b0dbfe6` |
| cargo 1.92.0 MSVC x64 | 9,845,728 | `f4057534ec58708ed3554957582a6c8e8c6abfee011b5c8ad45c3ab27c0b3076` |
| rust-std 1.92.0 MSVC x64 | 21,806,068 | `b4e53a9c9b96a1a0618364791b7728a1c8978101ec6d1ee78fe930e1ef061994` |
| rustfmt 1.92.0 MSVC x64 | 2,656,744 | `3761b2e8a18d672b2804b0d1864dc122b7bc54343d6a109135c7843de7fa2ace` |
| clippy 1.92.0 MSVC x64 | 3,887,152 | `51a9a67931a3e50eb0c26b4c6a7ec2ef6ddd633319343a2d4767710c1e33b21f` |
| llvm-tools 1.92.0 MSVC x64 | 43,685,096 | `5a23a441ae29f023cf989fefe972bf1eafd6d7ef68b3ad8495aeee397ee330ef` |
| protoc 36.2 Windows x64 ZIP | 3,565,086 | `f0c128dc0d8492eceece83bb459a4c0e316764b929ffbf1aa416357fd644edd3` |
| rustup-init 1.29.3 MSVC x64 | 12,839,424 | `38027d51d69a5d05493627a0634dbea52d6cd793c8f072d2373d3f4163eec7db` |

Rust archives were sourced from the exact 1.92.0 manifest; the bootstrap came from the versioned official Rustup archive; protoc came from the official protobuf v36.2 release. Full URLs and per-request headers, redirects, byte counts and outcomes are retained in the external evidence workspace.

A loopback-only distribution mirror contained the unchanged verified channel manifest and checksum, the six approved archives, and checksum sidecars derived from the pinned manifest hashes. Rustup used `RUSTUP_DIST_SERVER` and `RUSTUP_UPDATE_ROOT` pointed at that mirror, with a task-local proxy that denied all external destinations. The official [Rustup environment-variable documentation](https://rust-lang.github.io/rustup/environment-variables.html) documents the distribution-server override, and its [proxy documentation](https://rust-lang.github.io/rustup/network-proxies.html) documents proxy environment support. The local mirror served the channel manifest and each of the six component archives once during installation. The proxy log contains only two synthetic deny-proxy self-tests; no unapproved Rustup request was recorded. The local server was stopped and its process reaped.

## Provisioning outcomes

### Rustup and Rust toolchain

The verified Rustup 1.29.3 bootstrap was executed once with `--default-toolchain none --profile minimal --no-modify-path --default-host x86_64-pc-windows-msvc -y`; it exited 0. The isolated Rustup reported `1.29.3 (edbc831ce 2026-11-02)`.

Exactly one toolchain installation command was issued:

```text
rustup toolchain install 1.92.0-x86_64-pc-windows-msvc --profile minimal --component rustfmt --component clippy --component llvm-tools-preview --no-self-update
```

The watchdog encountered a `DirectoryNotFoundException` while enumerating a transient Rustup temporary directory. It therefore exited before writing the install result record or capturing the install process exit code. A prompt process check found no active Rustup installer. **The install was not retried.**

Read-only checks then confirmed the installed toolchain directory and all six components (`cargo`, `clippy`, `llvm-tools`, `rust-std`, `rustc`, `rustfmt`). Explicit binaries reported:

- `rustc 1.92.0 (ded5c06cf 2025-12-08)`, host `x86_64-pc-windows-msvc`, LLVM 21.1.3.
- `rustfmt 1.8.0-stable (ded5c06cf2 2025-12-08)`.
- `clippy 0.1.92 (ded5c06cf2 2025-12-08)`.
- LLVM tool executables, including `llvm-cov.exe` and `llvm-profdata.exe`, are present.
- `cargo.exe` is present (SHA-256 `7e26012c8632b35ce0ff3b7dd7ac18482060371cbd2f0314e453e7e11edec01a`); it was not invoked.

These file and version checks establish the requested components are present. The installation command's exit code and a complete watchdog peak record remain unavailable, so the install process itself is not recorded as a cleanly completed guarded run.

### Protobuf

The single protoc ZIP matched its expected SHA-256 before extraction. Safe extraction yielded 22 files; the standard `include/google/protobuf/descriptor.proto` file is present. The extracted `bin/protoc.exe` SHA-256 is `5a1b5350308309c9729ce4484a798981f281c10909144c4bdefd6a37688e4b1f`. Its read-only version query returned `libprotoc 36.2`.

### MSVC and Windows SDK

The initial read-only inventory found the requested Visual Studio 2022 Build Tools files, MSVC 14.44.35207, Windows SDK 10.0.26100.0 headers/libraries, and SDK resource compiler. One temporary activation was attempted with:

```text
vcvarsall.bat x64 -vcvars_ver=14.44.35207 -winsdk=10.0.26100.0
```

`vcvarsall.bat` rejected `-winsdk=...`; its emitted usage shows the SDK version is a positional argument. The environment variables remained empty and `where` found none of the tools in that child. No second activation was attempted. Thus the files were present, but the requested active MSVC/SDK pairing was **not verified** in this phase.

## Resource and preservation results

The task workspace measured 1,494,837,996 bytes at final inspection, below the 8 GiB ceiling. Final available C: space was 44,972,687,360 bytes, above the 25 GiB floor. The last fully recorded post-install watchdog sample was also below both limits. Because the toolchain-install watchdog terminated on the transient-directory scan error, continuous peak accounting across that one command cannot be proven; this is a reason for the partial classification, not evidence of a limit breach.

The default `.rustup` tree remained exactly 133 files / 587,883,039 bytes with tree SHA-256 `77d00626b63f77a829dfb2ed8e962182717b01c5d5464a273ab88f5a5ca49de5`. The default `.cargo` tree remained exactly 4,224 files / 344,573,568 bytes with tree SHA-256 `88a1c59501b4740bd7c58ff119e1c5a8fd3e4d77297ef2a25e5e6a8a72c95a46`. Existing Rust 1.90.0 remains intact. The persistent PATH was not changed.

The original `rag-env` still reports Chroma 1.5.0, OmegaConf 2.3.0, async-timeout 5.0.1, antlr4-python3-runtime 4.13.2 and PyYAML 6.0.3. The candidate requirements bytes remain unchanged. No Cargo command, Rust/C++ compilation, protobuf generation, wheel build, publisher/retrieval test, production change, dependency adoption or deployment occurred.

## Decision

`PARTIAL_PROVISIONING` — Rust 1.92.0 and the requested components, protoc 36.2, and the pre-existing MSVC/SDK files are present. The Rustup install exit and continuous peak record are unavailable, and the one MSVC activation attempt failed on argument syntax. This does not establish a compilation-ready environment.

**Next operator decision:** separately authorize one corrected, read-only MSVC 14.44 / SDK 10.0.26100.0 child activation and environment verification. Any Cargo resolution or compilation requires a distinct approval.

Detailed logs, manifests, archive hashes, mirror requests, process records and the failed activation output are retained under the task-owned external evidence workspace for this attempt.
