# Phase 6.17J — Offline Cargo dependency assessment

**Date:** 2026-11-04
**Phase classification:** `ENVIRONMENT_OR_INTEGRITY_BLOCKED`
**Cargo resolver result:** offline dependency input missing; no graph produced

## Checkpoint and inputs

The assessment began on `production-evaluation-workbench` at `3fdc27e07f7cd4de982fb2238ad74d3571a5d7e9`; the remote feature branch matched. Protected `main` was `7e9522618296af6a7f25652cc8161009d9a92685`, and the protected fixture blob was `25331e9324182002eb0333a5c56034e684e3c018`. The only pre-existing worktree change was the unstaged `requirements.txt` proposal, SHA-256 `EF3095DBC1A1657CCB5D5FCBA4D2DF245DD5EC9D04B6C2D237823FDFE472734F`.

The disposable Chroma source checkout was at commit `ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda`, tree `8d683102c76eea17c4088f1c5de46a1c7cd1239a`, with clean Git status. The root and selected crate manifests were unchanged. `Cargo.lock` SHA-256 matched the pinned value `D0B2ECF987173344EA347D15D8DF203A3D30D9C237354A8B163AF40F448828D2`.

The direct Cargo executable was the file under the isolated Rust `1.92.0-x86_64-pc-windows-msvc` toolchain. Its SHA-256 was `7E26012C8632B35CE0FF3B7DD7AC18482060371CBD2F0314E453E7E11EDEC01A`, matching the 6.17H record. Its Windows version-resource fields were empty; no separate `cargo --version` command was run because the authorization allowed one Cargo invocation only.

## Task-owned offline cache

The new Cargo home was populated selectively from the existing default registry cache; the default cache was not changed. The lockfile contained 992 package rows: 950 registry rows and seven Git-source rows, with the remainder being workspace/path packages. The registry rows referred to 827 unique crate names.

| Cache evidence | Found in default cache | Copied or verified for this attempt |
|---|---:|---|
| Sparse-index records for locked crates | 59 unique crate names; these covered 101 lock rows | 59 records copied and byte-hash matched |
| Exact locked `.crate` archives | 33 of 950 registry rows | All 33 SHA-256 values matched their `Cargo.lock` checksums and copied bytes |
| Exact locked extracted source directories | 33 directories present | Their `.cargo-checksum.json` `package` value was `null`; none was treated as lock-checksum verified or copied |
| Cargo Git cache | No default `git` cache directory | No Git source checkout copied |

The lockfile contains seven Git package rows from two repositories: `chroma-core/hnswlib` at `312991cf9e640d5ccab879906a51e7612ce6fcf1` and `yoshidan/google-cloud-rust` at `b929a5ec83120c0d8788ef049ff77779a577794b`. Detailed per-package cache presence, checksums, copied-file hashes, and missing inputs are retained in the external evidence workspace.

## Single offline metadata invocation

Exactly one Cargo process started, using the direct executable from the pinned source checkout:

```text
cargo metadata --locked --offline --format-version 1 --filter-platform x86_64-pc-windows-msvc
```

The child environment set `CARGO_HOME` and `CARGO_TARGET_DIR` to task-owned paths, `CARGO_NET_OFFLINE=true`, `CARGO_NET_RETRY=0`, `RUSTUP_TOOLCHAIN=1.92.0-x86_64-pc-windows-msvc`, `RUSTUP_HOME` to the existing isolated 6.17H home, and `RUSTC` to that toolchain's direct `rustc.exe`. No default Cargo home was used by the invocation.

One launcher-side cache-copy assertion initially treated a JSON object as an array and stopped before starting Cargo. Its retained record confirms zero Cargo processes at that point. The validator was corrected to inspect the object values and verify the copied hashes; no Cargo retry occurred.

The process exited **101** after **861 ms**, without timing out. Stdout was empty (0 bytes; SHA-256 `E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855`). Stderr was 451 bytes (SHA-256 `BE681C8DCFE11EC424439EA3A734B6C45231EF2179E8A0178ACF8713EDB69776`) and reported:

```text
error: failed to get `hnswlib` as a dependency of package `chroma-index v0.1.0 (...\rust\index)`

Caused by:
  failed to load source for dependency `hnswlib`

Caused by:
  Unable to update https://github.com/chroma-core/hnswlib.git?branch=master#312991cf

Caused by:
  can't checkout from 'https://github.com/chroma-core/hnswlib.git': you are in the offline mode (--offline)
```

This is a missing local Git source under offline mode. It does not establish that the pinned revision is unavailable online or that the dependency is incompatible. Cargo produced no metadata JSON, so the reachable dependency subgraph for `chroma-segment` and `chroma-log` could not be calculated. The independent cache inventory also shows incomplete registry metadata: 849 locked registry rows had no matching local sparse-index entry, and 917 had no exact cached crate archive. The command stopped at the Git source failure; it did not establish which of those registry gaps would be next.

No registry archive was downloaded. Cargo was invoked with both `--offline` and `CARGO_NET_OFFLINE=true`; no network acquisition was authorized or performed.

## Resource and post-run preservation

The task-owned workspace measured 16,114,734 bytes before Cargo. The sampled peak during the invocation was 16,119,081 bytes; after evidence capture it was approximately 16.12 MB, far below the 2 GiB ceiling. Minimum sampled free C: space was 44,748,210,176 bytes, above the 25 GiB floor. The task-local target contained only `.rustc_info.json` (2,018 bytes); no object, library, executable, debug, or release build outputs were found. Cargo metadata did not produce a dependency graph or run a build.

The pinned checkout, manifests, and lockfile remained unchanged. The default Cargo home was unchanged by the pre/post tree comparison: 4,224 files, 344,573,568 bytes, tree SHA-256 `553C1B20AE89316447119AFC16B17C6310FDAB26D6CA2257394C0571BC8A6119` both before and after.

**The default Rustup home preservation check did not pass.** Its pre-invocation snapshot measured 164 files / 973,426,839 bytes (tree SHA-256 `36BC88B7EF0F38272FC4FE6B82F58A2B48647EAA47D32D9B72458CA15FA02DA4`); its post-run snapshot measured 133 files / 587,883,039 bytes (tree SHA-256 `9F9C2CAD79CF5DE982D35C6BA93CEFB489E09E038325D3683C37312B835A9DFC`). The 6.17H report previously recorded 133 files / 587,883,039 bytes. The pre-invocation record contains only an aggregate tree identity, not a per-file manifest, so the changed files and cause cannot be established from this attempt. No restoration or further Rustup investigation was performed. This preservation failure prevents a clean checkpoint; it is not attributed to Cargo.

The research baseline verifier passed before and after the Cargo attempt: **nine protected research files unchanged**. Repository HEAD and remote feature HEAD remained at the starting commit; protected `main`, fixture, and the requirements proposal were unchanged.

Detailed logs, cache manifests, invocation records, and hashes are retained outside the repository under `%LOCALAPPDATA%\Temp\rag-pdf-617j-offline-c591f2439d5c4ced9f20938c158d11b9`. The Cargo stdout and stderr hashes are recorded above.

## Classification and next decision

The Cargo failure itself is an offline dependency-cache gap, specifically the missing local `hnswlib` Git checkout; the registry cache is also incomplete. The overall phase is **`ENVIRONMENT_OR_INTEGRITY_BLOCKED`** because the required default Rustup preservation check failed. No complete dependency graph or finite dependency-acquisition size was established.

Do not repeat offline metadata. The next operator decision should be whether to authorize a separate read-only investigation of the unexplained default Rustup tree discrepancy. Only after that preservation concern is resolved should a separately bounded dependency-acquisition/resolution task be considered for the pinned Git and registry inputs.

## Execution accounting

- Cargo metadata invocations: **1**
- Cargo fetch/check/build/test invocations: **0**
- Network downloads: **0**
- Rust/C++ compilation attempts: **0**
- Build scripts or procedural macros executed: **0**
- Protobuf generation: **0**
- Publisher/retrieval tests: **0**

## Post-6.17K Rustup preservation reconciliation (2026-11-04)

This dated addendum supplements the original post-run preservation finding. The original Phase 6.17J classification remains **`ENVIRONMENT_OR_INTEGRITY_BLOCKED`**: its default Rustup preservation check failed at the time. Phase 6.17K remains **`EVIDENCE_INSUFFICIENT`**. Neither classification is revised.

### Findings verified after 6.17K

A fresh read-only file inventory compared the current default Rustup and Cargo trees against the retained Phase 6.17H per-file manifests. The default Rustup root had 133 files and 587,883,039 bytes; all normalized relative paths, sizes, and SHA-256 values matched the 6.17H baseline. There were zero added or missing paths, zero content differences, and zero `mtime_ns` or mode differences. The 6.17H Rustup tree identity was 77d00626b63f77a829dfb2ed8e962182717b01c5d5464a273ab88f5a5ca49de5. The existing default Cargo tree also matched its 6.17H per-file baseline: 4,224 files / 344,573,568 bytes, with zero path, content, timestamp, or mode differences.

The original default Rust 1.90.0 `rustc.exe`, `cargo.exe`, and `multirust-channel-manifest.toml` hashes match the retained 6.17H identities. The selected isolated 6.17H Rust 1.92.0 and protoc files rechecked against 6.17I comprise 17 files; each still matches its recorded SHA-256 and 6.17I post-check. This is a selected-file verification, not a fresh hash of the entire isolated toolchain.

The 6.17K report and its recorded current inventory and comparison hashes were verified before this addendum. Its classification is preserved exactly as **`EVIDENCE_INSUFFICIENT`**.

### Historical limits and preserved Cargo result

The 6.17J preflight recorded 164 files / 973,426,839 bytes, while its postflight recorded 133 files / 587,883,039 bytes. The 6.17J preflight record contains only aggregate counts, byte totals, and a tree digest; it has no per-file manifest. The postflight digest was independently reproduced from the 6.17H per-file baseline using the 6.17J digest scheme, and the new current scan matches the same baseline. This supports that the current default tree is back at the verified baseline, but it does not identify the 31-file / 385,543,800-byte aggregate delta, establish that all 31 were additions, or identify a writer or cleanup process. It does not prove that Cargo caused their disappearance or rule out temporary changes to original paths.

The single 6.17J offline `cargo metadata --locked --offline` invocation remains an exit-101 failure because the locked `hnswlib` Git revision was unavailable in the isolated offline cache. No resolved dependency graph was produced. Consequently, the selected `chroma-segment`/`chroma-log` dependency subgraph, its required artifact set, and any complete acquisition or build budget remain unknown. The cache counts reported earlier are lockfile-wide counts and must not be interpreted as the resolved two-crate subgraph.

Any future network-dependent dependency acquisition or resolution requires separate explicit authorization and a new bounded plan. No Cargo rerun or acquisition is authorized by this addendum.

### Qualification and release status

This reconciliation does not make 6.17J a successful resolution phase and does not fully explain its preservation anomaly. It establishes that the currently observed default Rustup and Cargo files match the 6.17H baseline and that the selected isolated tools remain unchanged. It does not establish compilation, native diagnostic behavior, or a dependency download budget. Phase 6.15D remains **C — native build deferred**; HNSW and publisher incidents remain unresolved; dependency adoption remains deferred; Chroma migration remains unauthorized; unattended general-purpose deployment remains **NO-GO**.
