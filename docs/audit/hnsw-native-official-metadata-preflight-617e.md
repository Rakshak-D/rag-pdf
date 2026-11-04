# Phase 6.17E — Official native-toolchain metadata preflight

## Scope and checkpoint

This was one foreground, sequential, read-only metadata investigation. It did not download executable archives, install tools, invoke Cargo or a compiler, generate protobuf code, or run tests.

- Repository checkpoint: `d3926835ae0ec26740a15baaa70d41cbbba19a5c`
- Feature branch: `production-evaluation-workbench`; remote feature HEAD matched the checkpoint before the run.
- Protected `main`: `7e9522618296af6a7f25652cc8161009d9a92685`
- Protected research fixture blob: `25331e9324182002eb0333a5c56034e684e3c018`
- Existing unstaged `requirements.txt` candidate SHA-256: `EF3095DBC1A1657CCB5D5FCBA4D2DF245DD5EC9D04B6C2D237823FDFE472734F`
- Existing Chroma source and diagnostic patch identities matched the Phase 6.17C/D records.
- The pre-run nine-file research baseline verifier passed.

The prior findings remain unchanged: [Phase 6.17C](hnsw-native-prerequisite-closure-617c.md) was `SOURCE_INPUTS_COMPLETE_PREREQUISITES_BLOCKED`; [Phase 6.17D](hnsw-native-prerequisite-provisioning-617d.md) was `RESOURCE_BOUND_UNENFORCEABLE`. This report does not provision or authorize the native build.

## Capture setup and execution

The one runner was stored outside the repository in a new task-owned workspace under `%LOCALAPPDATA%\Temp\rag-pdf-617e-15fa42d773f94ed6aa7ea3a113d992a9`. It used Python's standard library, default TLS certificate validation and default proxy discovery (no proxy was reported in the run metadata). Redirects were recorded and not followed.

Before network access, the runner verified the log directory was writable, wrote and fsynced `run-start.json`, initialized the JSONL request log and error log, wrote/fsynced a JSONL self-test record, and read it back successfully. A local syntax check passed. A separate foreground Python sentinel exited 37 and PowerShell captured 37, confirming parent exit-code capture. The metadata runner then ran once in the foreground using `rag-env` Python and exited 0.

Attempt ID: `66912533b4784b21815c82cdb16b8846`.

The interpreter was `.\rag-env\Scripts\python.exe` (CPython 3.10.11, Windows AMD64). The single runner invocation was `& .\rag-env\Scripts\python.exe -u "$env:LOCALAPPDATA\Temp\rag-pdf-617e-15fa42d773f94ed6aa7ea3a113d992a9\scripts\capture_metadata.py"` from the repository root; the foreground PowerShell parent reported exit code 0.

Limits recorded by the runner were 18 requests, 4,194,304 application-level response-body bytes total, 2,097,152 bytes per response body, 20 seconds per request, zero retries, and zero concurrency. The runner attempted and completed 9 requests. Every observed request duration was below 20 seconds; the longest was 11.860 seconds. It consumed 1,064,137 response-body bytes in total:

| Metadata response | Status | Body bytes consumed |
|---|---:|---:|
| Rust 1.92.0 channel checksum sidecar | 200 | 91 |
| Rust 1.92.0 channel manifest | 200 | 1,037,027 |
| Rustup stable checksum sidecar | 404 | 0 |
| Rustup stable release pointer | 200 | 40 |
| Protobuf v36.2 GitHub release API metadata | 200 | 26,979 |
| Four Rust artifact HEAD responses | 200 | 0 |

The `requests.jsonl` has a parseable self-test record and one fsynced `request_started` and `request_outcome` pair for each attempted request. All actual response bodies were within the per-response and cumulative limits. HTTP error bodies were not read. No redirects occurred. These counts describe bytes returned by Python `response.read()`; they do not measure HTTP headers, TLS framing, transport buffering, or other wire traffic. `urllib` used a 20-second blocking-operation timeout, and the metadata body reader checked the request elapsed deadline; all observed requests completed within the stated interval.

## Verified metadata

The channel manifest was retrieved from [Rust's official 1.92.0 channel manifest](https://static.rust-lang.org/dist/channel-rust-1.92.0.toml). Its locally computed SHA-256 was `b2a49624353173ecdacf59c158c00929300606e1963f6e4609fb483a508402d0`, matching the retrieved official 91-byte checksum sidecar. The manifest's component URLs and SHA-256 values are therefore recorded as published metadata. HEAD `Content-Length` values are advertised sizes, not proof that a later complete GET will succeed or transfer exactly that many bytes.

| Artifact | Official URL | Published SHA-256 | Advertised bytes | Verification status and unknowns |
|---|---|---|---:|---|
| rustc 1.92.0, Windows MSVC x64 | `https://static.rust-lang.org/dist/2025-12-11/rustc-1.92.0-x86_64-pc-windows-msvc.tar.xz` | `6aec5aba5384ce1041538e71ebebf671d82e29f4fe697bc7844626d94b0dbfe6` | 71,972,056 | Official channel-manifest hash; HEAD returned 200 with this `Content-Length`. Archive not downloaded or hash-verified. |
| cargo 1.92.0, Windows MSVC x64 | `https://static.rust-lang.org/dist/2025-12-11/cargo-1.92.0-x86_64-pc-windows-msvc.tar.xz` | `f4057534ec58708ed3554957582a6c8e8c6abfee011b5c8ad45c3ab27c0b3076` | 9,845,728 | Official channel-manifest hash; HEAD returned 200 with this `Content-Length`. Archive not downloaded or hash-verified. |
| rust-std 1.92.0, Windows MSVC x64 | `https://static.rust-lang.org/dist/2025-12-11/rust-std-1.92.0-x86_64-pc-windows-msvc.tar.xz` | `b4e53a9c9b96a1a0618364791b7728a1c8978101ec6d1ee78fe930e1ef061994` | 21,806,068 | Official channel-manifest hash; HEAD returned 200 with this `Content-Length`. Archive not downloaded or hash-verified. |
| rustfmt 1.92.0 (`rustfmt-preview` manifest package) | `https://static.rust-lang.org/dist/2025-12-11/rustfmt-1.92.0-x86_64-pc-windows-msvc.tar.xz` | `3761b2e8a18d672b2804b0d1864dc122b7bc54343d6a109135c7843de7fa2ace` | Unknown | Manifest says available and supplies URL/hash. No HEAD was issued because the runner looked for `pkg.rustfmt`, while the actual manifest key is `pkg.rustfmt-preview`. No retry. |
| clippy 1.92.0 (`clippy-preview` manifest package) | `https://static.rust-lang.org/dist/2025-12-11/clippy-1.92.0-x86_64-pc-windows-msvc.tar.xz` | `51a9a67931a3e50eb0c26b4c6a7ec2ef6ddd633319343a2d4767710c1e33b21f` | Unknown | Manifest says available and supplies URL/hash. No HEAD was issued because the runner looked for `pkg.clippy`, while the actual manifest key is `pkg.clippy-preview`. No retry. |
| llvm-tools-preview 1.92.0, Windows MSVC x64 | `https://static.rust-lang.org/dist/2025-12-11/llvm-tools-1.92.0-x86_64-pc-windows-msvc.tar.xz` | `5a23a441ae29f023cf989fefe972bf1eafd6d7ef68b3ad8495aeee397ee330ef` | 43,685,096 | Official channel-manifest hash; HEAD returned 200 with this `Content-Length`. Archive not downloaded or hash-verified. |
| Rustup Windows MSVC bootstrap | Not resolved | Unknown | Unknown | `release-stable.toml` returned only `schema-version = '1'` and `version = '1.29.3'` (40 bytes). The requested `release-stable.toml.sha256` URL returned 404. No versioned release manifest or bootstrap HEAD was requested. |
| `protoc-36.2-win64.zip` | `https://github.com/protocolbuffers/protobuf/releases/download/v36.2/protoc-36.2-win64.zip` | `f0c128dc0d8492eceece83bb459a4c0e316764b929ffbf1aa416357fd644edd3` | 3,565,086 | GitHub API returned release tag `v36.2` and one exact asset with matching official API `digest` and `size`. This verifies release metadata only; the ZIP was not accessed or downloaded. |

The four Rust artifact sizes supported by successful HEAD responses total **147,308,948 bytes**. Including the protobuf size from official release API metadata, the known advertised-size subtotal is **150,874,034 bytes**. These figures exclude the unknown rustfmt, clippy, and Rustup bootstrap sizes and are not a complete installation transfer estimate.

## Request outcomes

All requests used HTTPS. The Rust and protobuf metadata endpoints returned without redirects. The Rustup sidecar request to `https://static.rust-lang.org/rustup/release-stable.toml.sha256` returned HTTP 404 with a recorded `Content-Length` of 324 bytes; the error body was not consumed. This establishes that this requested sidecar URL was unavailable in this observation, not that Rustup artifacts are permanently unavailable.

The runner successfully parsed the verified Rust channel manifest, but its component lookup used `rustfmt` and `clippy` instead of the manifest package identifiers `rustfmt-preview` and `clippy-preview`. Consequently it did not issue those two planned artifact HEAD requests. The actual manifest URLs and published hashes are retained above, while their sizes remain unknown. The one authorized runner execution is spent; no corrective request or second runner was made.

The Rustup stable endpoint is a small version pointer, not the versioned bootstrap manifest. With its checksum-sidecar request returning 404, this run did not establish the immutable bootstrap URL, checksum, or size. No alternative URL or mirror was tried.

## Evidence retention

Detailed logs, the metadata bodies permitted by the budget, and the runner are retained under `%LOCALAPPDATA%\Temp\rag-pdf-617e-15fa42d773f94ed6aa7ea3a113d992a9`. Evidence file byte lengths and SHA-256 values:

| File | Bytes | SHA-256 |
|---|---:|---|
| `run-start.json` | 1,296 | `b9ab5f7d29ed232d16f2fa9a1bcab3ccfe23c12d1623a7b26b13f1580ced365e` |
| `summary.json` | 3,684 | `c33c736b93dd6bb2b087f5ca8a243791c181ee667bdef14e427cd13116894d49` |
| `logs/requests.jsonl` | 26,833 | `3725d0ff166548390a88c48d5127132ee299d2f7044e21a26d082840e4faac03` |
| `logs/errors.log` | 215 | `6dd294986d204579120e03db0531aa8f6f179e9e7b88b35b4285fd578bec676f` |
| `metadata/rust-1.92-channel-checksum.body` | 91 | `31e37a9e72f3ec3426cd670d19b373b75443255640b907e492efc3d51bbe1ff0` |
| `metadata/rust-1.92-channel-manifest.body` | 1,037,027 | `b2a49624353173ecdacf59c158c00929300606e1963f6e4609fb483a508402d0` |
| `metadata/rustup-release-manifest.body` | 40 | `05c8e3276d2e6fbf05f434c52acbc7255f084a3fb09bd32f2926edec7fb9cffe` |
| `metadata/protobuf-release-api.body` | 26,979 | `ef40c955001e8700f0dcd6bac818027223144c5e35dbcf679fcac702d9f491eb` |
| `scripts/capture_metadata.py` | — | `594d5de554c8c886d5aadf149a5d3b651d570dbb9223a34c69646ee85ae51380` |

The runner exit code was 0. The one error-log record is the captured Rustup checksum-sidecar 404. No full wheel or executable archive body was requested or read. Total application-level response-body consumption was 1,064,137 bytes; transport-level wire bytes were not measured.

## Final repository-integrity gate

The pre-run research baseline check passed. The required final invocation of `scripts/verify_baseline.py` on 2026-11-04 failed with hash drift in seven protected notebooks. The subsequent read-only Git status showed all seven as modified; a read-only diff summary showed 20 insertions and 14,587 deletions across them. The observed current hashes were:

| Protected file | Expected SHA-256 | Observed SHA-256 |
|---|---|---|
| `notebooks/01_load_document.ipynb` | `5ffe68e510c64755ff36c8a3231a90f585324f20450df1b1fc02489a928a55ae` | `f4addbf3a8d246f6b2dcad74ec0e58c77289b9f8373b95e49c3bb581b2345e04` |
| `notebooks/02_chunking.ipynb` | `8414892471cdbad35c1b1b216eb995da9edba397a3825680a4430a7f9228fb9e` | `f4831a24ac6caf7da91e620a20b384093b8c7099806e4c4e5befc8a084905986` |
| `notebooks/03_embedding.ipynb` | `0a104834a3c69b5a40da35017add46869df4f58853be68bb7789dfc50c115b54` | `a61460a474f55a645823250891602469e64e60721b7567be850efcbc5fb886fb` |
| `notebooks/04_vector_store.ipynb` | `0dc83e6655bdcecb4dc6db343cd0abab03a96a89530c46420d4e7a8e02a67f3f` | `8aa210cccbf5c9ed7b349ac939735dfdeb41a1676fd49b732d1e752a8c2c567e` |
| `notebooks/05_retrieval.ipynb` | `a142a01e9feaceef1412338c947d2f3a854103b3b132aa627200c52d8e4009ba` | `d8b7ea3ba83701097382eef3ba9eae016796c7f952579ff04cefc34ac52f7e1d` |
| `notebooks/06_llm.ipynb` | `8bb95c112aea6da14c00b413e30a73dbc6e63cf3f4af713dc377290bf5d6a384` | `92bd56e58233dcad287f870bafb1b0a423aecf4539fa6587dd1b0ced4a0b02e3` |
| `notebooks/rag_pipeline.ipynb` | `00fe5b48088819c08f18c93e8dbc656b17f5b3e700cc861cad48cc635a3e9e6e` | `26f7d32ef378b3672438223bb6bc4a26c84150de5a64cf753008981cac1d57b9` |

The verifier source reads the files and compares their hashes; it contains no notebook-writing operation. The cause and timing of the notebook changes are unknown. No notebook was restored or edited. The candidate `requirements.txt` still has the expected SHA-256 and remains unstaged. Because the final baseline gate failed, this report was not staged, committed, or pushed; no remote write was made. The worktree therefore contains unexpected tracked notebook modifications in addition to the expected requirements proposal and this report.

## Classification and decision

**`METADATA_CAPTURE_COMPLETE_BOUNDS_INCOMPLETE`**

Capture integrity and the stated observed request/body budgets passed. The exact protobuf release metadata and four Rust component advertised lengths are established. The complete Rust component size set and Rustup bootstrap identity/size are not established, so the future cumulative 2 GiB provisioning bound cannot be certified from this run. A successful HEAD or release listing is not a complete-download guarantee.

No separate bounded provisioning request can yet be supported by this evidence. Any additional metadata collection would require a new explicit request that corrects the manifest package-name selection and obtains the versioned Rustup release metadata from an official source. No installation, build, test, protobuf generation, dependency change, or runtime experiment occurred here.

The deferred requirements proposal and original environment were preserved. HNSW and publisher incidents remain unresolved; Phase 6.15D remains C (native build deferred); dependency adoption remains deferred; Chroma migration remains unauthorized; unattended deployment remains **NO-GO**.

## Post-recovery notebook integrity addendum (2026-11-04)

After 6.17E recorded seven notebook hash mismatches, a separate Phase 6.17F forensic capture preserved byte-for-byte copies of all seven modified files before restoration. The forensic manifest SHA-256 is `3d1df7c9c193ffca4e3d24990ba6de11775b0440e7e1f859f7570dfb681538cd`; every preserved copy matches its recorded capture hash. The operator subsequently restored the notebooks from the pinned `HEAD`. I rechecked all seven current notebook hashes against the protected baseline, and `scripts/verify_baseline.py` now reports: `Baseline verified: 9 research files unchanged`.

The process that wrote the modified notebook bytes remains unidentified. Their timing does not establish a causal link to the metadata runner. This recovery does not alter the original 6.17E observations or classification: `METADATA_CAPTURE_COMPLETE_BOUNDS_INCOMPLETE`. The rustfmt/clippy advertised sizes and immutable Rustup bootstrap identity/size remain unresolved, so the complete 2 GiB provisioning bound is still unestablished.
