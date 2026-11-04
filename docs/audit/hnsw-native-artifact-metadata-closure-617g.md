# Phase 6.17G — Pinned HNSW toolchain artifact metadata closure

## Scope and checkpoint

This was a metadata-only investigation of the three artifact metadata gaps retained from Phase 6.17E. It made exactly four sequential HTTP requests. It did not download executable archives, provision tools, install packages, invoke Cargo, compile code, or run tests.

- Starting repository HEAD and remote feature HEAD: `ba0188ad054ca7e7d4268f3226836868c4de4254`
- Branch: `production-evaluation-workbench`
- Protected `main`: `7e9522618296af6a7f25652cc8161009d9a92685`
- Protected fixture blob: `25331e9324182002eb0333a5c56034e684e3c018`
- Deferred `requirements.txt` candidate SHA-256: `EF3095DBC1A1657CCB5D5FCBA4D2DF245DD5EC9D04B6C2D237823FDFE472734F`
- The nine-file research baseline passed before and after this task.

The prior manifest and protobuf evidence were reused without another request. The Rust 1.92.0 channel manifest SHA-256 is `b2a49624353173ecdacf59c158c00929300606e1963f6e4609fb483a508402d0`; it was already verified against its official checksum sidecar in [Phase 6.17E](hnsw-native-official-metadata-preflight-617e.md). The existing protobuf v36.2 release metadata was also reused. Phase 6.17E remains `METADATA_CAPTURE_COMPLETE_BOUNDS_INCOMPLETE` as a historical result for that run; this report closes its three identified metadata gaps without changing its observations.

## Capture and request accounting

A new task-owned evidence workspace was created outside the repository at `%LOCALAPPDATA%\Temp\rag-pdf-617g-937d528c4d8f4514a41468ec7588f447`. The retained-manifest identity and the `rustfmt-preview` and `clippy-preview` target entries were checked before network access. The runner wrote and fsynced its start record and JSONL, tested and read back the JSONL capture record, then ran synchronously in the foreground. Each HTTP request had a flushed start and outcome record. Redirects were not followed.

| Ordinal | Method and artifact | Status | Advertised `Content-Length` | Body bytes consumed | Elapsed | Redirects |
|---:|---|---:|---:|---:|---:|---:|
| 1 | HEAD rustfmt 1.92.0 Windows MSVC x64 | 200 | 2,656,744 | 0 | 1.141 s | 0 |
| 2 | HEAD Clippy 1.92.0 Windows MSVC x64 | 200 | 3,887,152 | 0 | 1.234 s | 0 |
| 3 | GET Rustup 1.29.3 checksum sidecar | 200 | 65 | 65 | 1.203 s | 0 |
| 4 | HEAD Rustup 1.29.3 Windows MSVC bootstrap | 200 | 12,839,424 | 0 | 16.266 s | 0 |

All four outcomes were recorded in ordinal order. The longest request was below the 20-second limit. There were no retries or concurrent requests. Total application-level response-body consumption was **65 bytes**, below the 1 KiB per-response and 8 KiB cumulative limits. These are bytes read by the application and do not measure HTTP headers, TLS framing, proxy/network buffering, or total wire traffic.

### Artifact identities

| Artifact | Exact official URL | Published SHA-256 | Advertised bytes | Evidence |
|---|---|---|---:|---|
| Rustfmt 1.92.0, `x86_64-pc-windows-msvc` | `https://static.rust-lang.org/dist/2025-12-11/rustfmt-1.92.0-x86_64-pc-windows-msvc.tar.xz` | `3761b2e8a18d672b2804b0d1864dc122b7bc54343d6a109135c7843de7fa2ace` | 2,656,744 | URL/hash from the retained checksum-verified channel manifest; size from captured HTTP 200 HEAD. |
| Clippy 1.92.0, `x86_64-pc-windows-msvc` | `https://static.rust-lang.org/dist/2025-12-11/clippy-1.92.0-x86_64-pc-windows-msvc.tar.xz` | `51a9a67931a3e50eb0c26b4c6a7ec2ef6ddd633319343a2d4767710c1e33b21f` | 3,887,152 | URL/hash from the retained checksum-verified channel manifest; size from captured HTTP 200 HEAD. |
| Rustup 1.29.3 bootstrap, `x86_64-pc-windows-msvc` | `https://static.rust-lang.org/rustup/archive/1.29.3/x86_64-pc-windows-msvc/rustup-init.exe` | `38027d51d69a5d05493627a0634dbea52d6cd793c8f072d2373d3f4163eec7db` | 12,839,424 | SHA-256 from the exact versioned `.exe.sha256` sidecar (HTTP 200, 65-byte body); size from captured HTTP 200 HEAD of the exact executable URL. The executable was not downloaded. |

The Rustup sidecar body contained the 64-hex SHA-256 value and was tied to the executable by the exact versioned sidecar URL. Its body SHA-256 is `d473f79bffe135e27b42aed2da0cf7065a9afaf71ec5bd2ada642a9b85fe17f0`.

## Advertised-size assessment

The previously verified advertised subtotal was **150,874,034 bytes**: rustc, Cargo, rust-std, llvm-tools-preview and the protobuf 36.2 ZIP. The three newly measured archive sizes total **19,383,320 bytes**. The resulting known advertised archive subtotal is **170,257,354 bytes** (about 162.3 MiB), versus the proposed 2 GiB ceiling of 2,147,483,648 bytes. On advertised artifact lengths alone, the ceiling appears feasible with approximately 1,977,226,294 bytes of headroom.

This is preliminary artifact-budget evidence, not a guaranteed transfer bound. Rustup may retrieve additional metadata or artifacts; redirects or transport overhead may differ during provisioning. Installation expansion, temporary storage, and actual transfer behavior remain unmeasured. This phase does not certify the separate 8 GiB additional-disk limit or 25 GiB free-space floor.

## Runner summary discrepancy

The foreground capture runner exited with code **2** and its `summary.json` reported `TARGET_METADATA_INCOMPLETE`. That summary classification is inconsistent with the complete raw records. In the runner's post-request aggregation, the generic `method == "HEAD"` branch precedes its ordinal-4 Rustup-specific branch. The Rustup sidecar validation therefore recorded the executable checksum in one summary entry, while the executable HEAD recorded its size in a second entry. The summary's completion predicate expected three artifact entries and consequently marked the complete set incomplete.

I did not rerun or repair the runner. I independently checked the flushed four request-start/outcome pairs, the four successful metadata-validation records, the exact URLs and HTTP statuses, the three positive advertised lengths, the retained channel-manifest hashes, and the 65-byte sidecar body and digest. The raw evidence contains all three artifact sizes and all three published SHA-256 identities. The runner's nonzero status and summary defect are retained and disclosed; the classification below is based on this independent raw-evidence reconciliation.

## Evidence files

All detailed evidence remains outside the repository in the workspace identified above. SHA-256 and size:

| File | Bytes | SHA-256 |
|---|---:|---|
| `run-start.json` | 2,123 | `796d81d1c432c52b155fe426839c9e8b15d578d401b008871b2a0319b63a96cd` |
| `logs/requests.jsonl` | 5,622 | `3098989e95519edbd06ceb9caa7bc9dc56528a55d482d9e08d2a52424857d8e1` |
| `logs/errors.log` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `metadata/rustup-init.exe.sha256.body` | 65 | `d473f79bffe135e27b42aed2da0cf7065a9afaf71ec5bd2ada642a9b85fe17f0` |
| `summary.json` | 2,309 | `6b49b38a3ad9dfe0133e3cdb41b924eeb5f785ba18bbbd10cddac6414576fd79` |
| `independent-validation.json` | 2,913 | `ecdf16c85f34ebe25fada59402b2ba55fe57eebc91fdabffba0a0ac21f6fe377` |
| `capture_artifact_metadata.py` | 20,128 | `a92143dc98196041560c87cd6a8efa14d0b65ff847069fac0d069668ac905e1e` |

The independently verified validation record has attempt ID `937d528c4d8f4514a41468ec7588f447`. The retained 6.17E evidence and reports were not modified.

## Classification and next decision

**`TARGET_METADATA_COMPLETE`**

The four authorized requests captured the previously missing Rustfmt and Clippy advertised sizes and Rustup 1.29.3 checksum and executable size. The runner's summary/exit discrepancy is explicitly preserved above; the complete request records and artifact identities were independently verified without another network request.

A separate isolated-provisioning request can now be reviewed using these artifact identities and advertised sizes. That approval must retain bounded transfer and live disk-space controls, account for possible Rustup-initiated metadata or artifact requests, and stop on any unexpected source, version, transfer, or free-space condition. No provisioning is authorized by this metadata result.

No archive was downloaded; no Rust, protobuf, or Cargo tool was installed or invoked. No compilation, test, notebook editing or execution, dependency change, Chroma action, or deployment occurred. Phase 6.15D remains C — native build deferred. The HNSW and publisher incidents remain unresolved; dependency adoption remains deferred; Chroma migration is unauthorized; unattended deployment remains **NO-GO**.
