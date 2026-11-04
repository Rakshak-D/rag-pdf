# Phase 6.17D — Isolated native prerequisite provisioning

- **Review date:** 2026-11-04
- **Starting RAG-PDF revision:** `cfee0d9d4065f82c2d315ee0a25be5cc550632c8`
- **Pinned Chroma source:** `ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda`
- **Pinned Chroma tree:** `8d683102c76eea17c4088f1c5de46a1c7cd1239a`
- **Diagnostic patch SHA-256:** `276D25F5BA771A33BC4902A904B8C6E2E31C632D02F5BE27FAD44B28CE969920`
- **Outcome:** `RESOURCE_BOUND_UNENFORCEABLE`
- **Scope:** Provisioning stopped at the official artifact metadata gate; no toolchain or protobuf artifact was acquired

## Result

The repository, protected references, existing Chroma source checkout, diagnostic patch, baseline and requirements-proposal hashes passed preflight. A task-owned evidence workspace was created. The one bounded official metadata-probe sequence did not produce retained output or its expected result file, and its child process had exited by the time it was checked. The probe status, response lengths, checksum metadata and cumulative metadata bytes therefore cannot be established. No retry was made.

Because the 2 GiB cumulative-transfer limit includes metadata, the transfer budget could not be certified. Provisioning stopped before requesting any Rustup bootstrap, Rust component archive, or protobuf ZIP. No installer, Rustup setup, MSVC activation or compiler was invoked. This result does not establish that the official sources are unavailable; it establishes that this attempt did not retain enough metadata evidence to enforce the authorized budget.

## Starting identities and preflight

The local branch was `production-evaluation-workbench` at `cfee0d9d4065f82c2d315ee0a25be5cc550632c8`; the remote feature ref matched. Protected `main` was `7e9522618296af6a7f25652cc8161009d9a92685`; the protected `tests/fixtures/research-baseline.json` blob was `25331e9324182002eb0333a5c56034e684e3c018`.

The only worktree change was unstaged `requirements.txt`, SHA-256 `EF3095DBC1A1657CCB5D5FCBA4D2DF245DD5EC9D04B6C2D237823FDFE472734F`; nothing was staged. The nine-file research baseline verifier passed at preflight. The existing Chroma checkout remained at commit `ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda` and tree `8d683102c76eea17c4088f1c5de46a1c7cd1239a`, with clean tracked status. The diagnostic patch hash matched the expected value.

The existing interpreter reported CPython 3.10.11 AMD64, Chroma 1.5.0, OmegaConf 2.3.0 and async-timeout 5.0.1. Rust 1.90.0 was present before provisioning. Its `rustc.exe`, `cargo.exe` and channel-manifest SHA-256 values were recorded in the task-owned initial inventory.

## Approved limits and evidence workspace

The authorized ceilings were 2 GiB cumulative new downloads including metadata, 8 GiB additional local disk footprint, and at least 25 GiB free disk at all times. The unique task workspace was `%LOCALAPPDATA%\Temp\rag-pdf-617d-cf9ef3f148e04b85ad58549dedd0f537`, with separate `rustup-home`, `cargo-home`, `protoc`, `downloads`, `logs`, `evidence` and `tmp` directories.

At workspace creation, C: had 43,044,773,888 bytes free. The recorded stop snapshot had 42,964,496,384 bytes free; a later workspace audit measured 42,948,026,368 bytes. Both measurements were above the 25 GiB floor. The workspace contained 848,878 bytes of host-inventory and stop-evidence JSON. No artifact archive was downloaded. These observations do not establish the cost of an installation that was not attempted.

The direct metadata probe was coded to request a single `bytes=0-0` range with TLS verification, a 20-second per-connection timeout, no retry, and no body consumption when a server ignored the range. The intended metadata URLs were:

- `https://static.rust-lang.org/rustup/release-stable.toml`
- `https://static.rust-lang.org/rustup/release-stable.toml.sha256`
- `https://static.rust-lang.org/dist/channel-rust-1.92.0.toml`
- `https://static.rust-lang.org/dist/channel-rust-1.92.0.toml.sha256`
- `https://api.github.com/repos/protocolbuffers/protobuf/releases/tags/v36.2`

The command wrapper returned no captured stdout; `official-metadata-range-probes.json` was not created. It is unknown which of these requests reached a response. The exact metadata-body transfer total is unavailable. No full manifest body or artifact body was intentionally requested. The official browser metadata opens also returned tool-level inaccessible errors and supplied no source content.

## Rust 1.92.0 acquisition gate

The pinned channel is `1.92.0-x86_64-pc-windows-msvc`, with the minimal profile plus `rustfmt`, `clippy` and `llvm-tools-preview`. The official bootstrap candidate would be `https://static.rust-lang.org/rustup/dist/x86_64-pc-windows-msvc/rustup-init.exe`.

No bootstrap version, advertised size or official checksum was retained. The channel manifest and sidecar checksum were not retained, so the exact required component URLs, lengths and hashes were not enumerated. The bootstrap help was not run, no installation command was issued, and no Rust 1.92.0 toolchain was installed. Rustup attempt count: **0**. Rust 1.92.0 is **not provisioned or verified**.

## Protobuf 36.2 acquisition gate

The requested artifact identity was `protoc-36.2-win64.zip` from `https://github.com/protocolbuffers/protobuf/releases/download/v36.2/protoc-36.2-win64.zip`; the task supplied expected size 3,565,086 bytes and SHA-256 `f0c128dc0d8492eceece83bb459a4c0e316764b929ffbf1aa416357fd644edd3`. Those values were not independently verified against retained official release metadata in this attempt. The ZIP was not requested or downloaded; its checksum was not tested; nothing was extracted and `protoc` was not run. Protobuf artifact download count: **0**. Protoc 36.2 is **not provisioned or verified**.

## MSVC, SDK and preservation

The prior read-only inventory found candidate MSVC 14.44.35207 and 14.50.35717 files, plus Windows SDK 10.0.26100.0 files. Phase 6.17D selected no fallback and did not activate `vcvarsall.bat`; consequently the requested MSVC 14.44 / SDK pairing and resolved tool paths are **not verified**.

The initial and stop inventories of the default `%USERPROFILE%\.rustup` and `%USERPROFILE%\.cargo` trees matched in path, file count, size and timestamps. SHA-256 values for the existing Rust 1.90.0 `rustc.exe`, `cargo.exe` and channel manifest also matched. No persistent PATH or system setting was changed. No Cargo command, compiler, linker, protobuf generation, test, build or installer was run. The candidate requirements file hash remains unchanged.

## Classification and next decision

**`RESOURCE_BOUND_UNENFORCEABLE`** — the cumulative metadata-transfer accounting could not be retained, so the artifact identity and size gate was not satisfied. No provisioning continued.

The next operator decision should be limited to whether to authorize a new, separately bounded metadata-preflight attempt using a capture-safe request runner that retains each response status, headers, byte count and checksum evidence. That decision would not authorize artifact downloads or installation. Cargo resolution and compilation are outside this task and remain unauthorized.

Phase 6.17C remains `SOURCE_INPUTS_COMPLETE_PREREQUISITES_BLOCKED`. Phase 6.15D remains **C — native build deferred**. HNSW and publisher incidents remain unresolved; dependency adoption remains deferred; Chroma migration remains unauthorized; unattended deployment remains **NO-GO**.

## Prior checkpoints

- [Phase 6.17A source reference audit](hnsw-full-rust-reference-audit-617a.md)
- [Phase 6.17B native type-check approval gate](hnsw-native-typecheck-approval-gate-617b.md)
- [Phase 6.17C prerequisite closure](hnsw-native-prerequisite-closure-617c.md)
