# Phase 6.14K — CUDA Wheel Artifact Feasibility

**Review date:** 2026-11-03
**Starting checkpoint:** `c625e2f97573213c670f804c7e9f22c578aabf7c`
**Phase 6.14J parent:** `c343abedc6c1f17e722cf55503601879b170b115`
**Target:** CPython 3.10.11, Windows AMD64, CUDA 12.1
**Classification:** **C — Inconclusive for the complete 8 GiB manifest budget**
**Installation/download of complete wheels:** No

The three exact official CUDA 12.1 wheel links were resolved from PyTorch's simple indexes. One bounded `Range: bytes=0-0` request to each canonical artifact URL returned HTTP 206 with a valid total size and the official index's SHA-256 fragment. The three wheels total 2,459,569,626 advertised bytes (about 2.29 GiB). This establishes limited source access and size metadata for those artifacts without fetching their complete bodies. It does not bound the other artifacts selected for the 265-pin manifest, so the complete 8 GiB download gate remains unverified.

## Checkpoint and Stage A closeout

Stage A committed the reviewed 6.14J report as `c625e2f97573213c670f804c7e9f22c578aabf7c`, parent `c343abedc6c1f17e722cf55503601879b170b115`. The commit contains only [dependency-adoption-qualification-614j.md](dependency-adoption-qualification-614j.md) and is pushed to `origin/production-evaluation-workbench`.

The existing two-pin `requirements.txt` proposal remains unstaged and unchanged. No dependency installation was performed in 6.14J or 6.14K.

## Review of the 6.14J 403 observations

The retained 6.14J evidence directory, `%LOCALAPPDATA%\Temp\rag-pdf-dependency-614j-jg3k3wyx`, contains the manifest audit and a short source/resource summary. It does **not** contain raw HTTP response logs, redirect traces, or complete request headers. The captured 6.14J command output identifies a curl HEAD request and a separate Python GET with `Range: bytes=0-0`; both received HTTP 403 at these URLs:

| Artifact | URL used for the prior HEAD and range GET |
|---|---|
| torch | `https://download.pytorch.org/whl/cu121/torch/torch-2.5.1%2Bcu121-cp310-cp310-win_amd64.whl` |
| torchvision | `https://download.pytorch.org/whl/cu121/torchvision/torchvision-0.20.1%2Bcu121-cp310-cp310-win_amd64.whl` |
| torchaudio | `https://download.pytorch.org/whl/cu121/torchaudio/torchaudio-2.5.1%2Bcu121-cp310-cp310-win_amd64.whl` |

The HEAD output showed `Content-Type: application/xml`, `Server: AmazonS3`, `X-Cache: Error from cloudfront`, and a CloudFront `Via` header; no redirect response was shown by `curl -L`. The prior Python range errors recorded status 403 but did not retain response headers. The prior probe did not record proxy configuration. A separate guessed `download-r2.pytorch.org` path also included the extra package directory and returned 404.

Therefore, the prior 403/404 results apply to noncanonical paths. They do not demonstrate that the official artifacts are blocked. The committed 6.14J classification remains C because it did not establish a complete download bound or attempt a clean installation; its size/access wording is clarified here against the actual official hrefs.

## Local cache and wheelhouse inspection

The inspection was read-only. `pip cache info` reported a 195.7 MB HTTP cache with 764 HTTP files and a zero-byte locally built wheel cache. `pip cache list` showed no matching CUDA wheel entries. A scan of the pip HTTP cache found 220 `.body` files totaling 87,017,608 bytes; no ZIP body had `torch==2.5.1+cu121`, `torchvision==0.20.1+cu121`, or `torchaudio==2.5.1+cu121` metadata.

The separate retained Phase 6.14A wheelhouse contains four complete, previously verified PyPI wheels. Their current local size and SHA-256 still match the Phase 6.14A manifest:

| Wheel | Bytes | SHA-256 | Relation to this phase |
|---|---:|---|---|
| `async_timeout-4.0.3-py3-none-any.whl` | 5,721 | `7405140ff1230c310e51dc27b3145b9092d659ce68ff733fb0cefe3ee42be028` | Candidate pin wheel; previously verified against PyPI metadata. |
| `omegaconf-2.4.0-py3-none-any.whl` | 254,152 | `f141a664c777582405e3cc6e0545ecc79429e79f844995b1a6e71c35946a025c` | Candidate pin wheel; previously verified against PyPI metadata. |
| `antlr4_python3_runtime-4.13.2-py3-none-any.whl` | 144,462 | `fe3835eb8d33daece0e799090eda89719dbccee7aa39ef94eed3818cafa5a7e8` | Existing unchanged pin. |
| `pyyaml-6.0.3-cp310-cp310-win_amd64.whl` | 158,561 | `bdb2c67c6c1390b63c6ff89f210c8fd09d9a1217a465701eac7316313c915e4c` | Existing unchanged pin. |

These four local files total 562,896 bytes. They are not the CUDA wheels and do not represent a complete project wheelhouse. No complete CUDA wheel was present in the inspected pip cache or the retained Phase 6.14A wheelhouse, so verified cached bytes for the three CUDA artifacts are zero.

## Official simple-index metadata

The official [PyTorch 2.5.1 instructions](https://pytorch.org/get-started/previous-versions/) direct Windows CUDA 12.1 wheel users to `https://download.pytorch.org/whl/cu121`. Each official simple-index page returned HTTP 200, with no index-page redirect. The pages linked to exact CPython 3.10 Windows AMD64 artifacts hosted at `download-r2.pytorch.org`.

| Index page | Exact filename | Published SHA-256 fragment | Index response |
|---|---|---|---|
| [`torch`](https://download.pytorch.org/whl/cu121/torch/) | `torch-2.5.1+cu121-cp310-cp310-win_amd64.whl` | `9b22d6d98aa56f9317902dec0e066814a6edba1aada90110ceea2bb0678df22f` | 200; 56,750-byte HTML |
| [`torchvision`](https://download.pytorch.org/whl/cu121/torchvision/) | `torchvision-0.20.1+cu121-cp310-cp310-win_amd64.whl` | `4cb1e44c1f7a4992f6d38a15633a1e694c093f1c52f3a036b1a719968031507a` | 200; 74,160-byte HTML |
| [`torchaudio`](https://download.pytorch.org/whl/cu121/torchaudio/) | `torchaudio-2.5.1+cu121-cp310-cp310-win_amd64.whl` | `68c08cf69f7f39608f4c8d6b87fa054d7c801a19060e89d66003efbceef15641` | 200; 97,255-byte HTML |

The published hrefs are:

- `https://download-r2.pytorch.org/whl/cu121/torch-2.5.1%2Bcu121-cp310-cp310-win_amd64.whl#sha256=9b22d6d98aa56f9317902dec0e066814a6edba1aada90110ceea2bb0678df22f`
- `https://download-r2.pytorch.org/whl/cu121/torchvision-0.20.1%2Bcu121-cp310-cp310-win_amd64.whl#sha256=4cb1e44c1f7a4992f6d38a15633a1e694c093f1c52f3a036b1a719968031507a`
- `https://download-r2.pytorch.org/whl/cu121/torchaudio-2.5.1%2Bcu121-cp310-cp310-win_amd64.whl#sha256=68c08cf69f7f39608f4c8d6b87fa054d7c801a19060e89d66003efbceef15641`

The resolved host is the official PyTorch index's linked artifact host. The hash fragments are published index metadata; the complete wheel files were not downloaded, so the bytes were not locally hashed. The filenames' `cp310-cp310-win_amd64` tags match the requested CPython 3.10 Windows AMD64 target.

## One bounded range request per artifact

Each exact published artifact URL received one GET with `Range: bytes=0-0`. The configured Python proxy map and proxy environment-variable checks were empty. No credentials, alternate user agents, proxy bypass, mirror, or retry was used. The links resolved directly with no redirects. Each response was HTTP 206, `Content-Length: 1`, `Content-Type: binary/octet-stream`, `Server: cloudflare`, and no `Location` header.

| Artifact | `Content-Range` | Advertised total bytes | Probe result |
|---|---|---:|---|
| torch | `bytes 0-0/2449372784` | 2,449,372,784 | Exact URL and published SHA matched the index entry. |
| torchvision | `bytes 0-0/6063665` | 6,063,665 | Exact URL and published SHA matched the index entry. |
| torchaudio | `bytes 0-0/4133177` | 4,133,177 | Exact URL and published SHA matched the index entry. |

The original probe recorder compared the URL basename before percent-decoding `%2B`, so it marked the filename check false and closed the response without reading the requested one-byte body. A no-network post-processing check decoded the path, matched the exact expected filenames and official index checksum fragments, and validated the unambiguous `206` `Content-Range` and one-byte `Content-Length`. The total-size metadata is therefore established by the response headers. The raw probe JSON and the separate offline validation are both retained; no network retry occurred. The script consumed zero response-body bytes. At most the requested one-byte payload per response could have been sent; actual transport-buffer delivery was not measured.

These responses establish advertised sizes and limited range accessibility at the recorded time. They do not establish successful complete downloads or independently verify the full artifact hashes.

## Download budget and storage

The three advertised CUDA wheel sizes sum to **2,459,569,626 bytes** (about **2.29 GiB**). The 8 GiB cap is 8,589,934,592 bytes, leaving 6,130,364,966 bytes (about 5.71 GiB) for all other selected artifacts if none of these CUDA wheels is already cached. That remaining amount is not a verified upper bound for the rest of the environment.

At the 6.14K storage check, C: had 40,058,150,912 bytes free (37.31 GiB), above the required 20 GiB minimum. Current free space passes the stated threshold. Total package bytes, temporary download/cache duplication, and installed-size overhead for the remaining manifest are unknown. The 265-pin manifest has not been resolved to a complete exact Windows wheel set, and some transitive artifacts may also be selected. No unknown size is treated as zero.

The three index HTML bodies totaled 228,165 bytes. The range probe read zero artifact response-body bytes; each response advertised a one-byte range. No full wheel, package stack, or model data was downloaded. No full `pip download`, resolver, environment creation, installation, `pip check`, or application test was run.

## Classification and recommendation

**C — Inconclusive for complete-manifest feasibility.** The official index publishes checksums and exact compatible filenames; the canonical artifact URLs returned valid 206 range metadata and sizes. The earlier 403 responses were for noncanonical paths, not evidence that the official source is unusable. However, the other packages' exact selections, artifact sources, sizes, and aggregate download cannot be bounded under 8 GiB from the evidence collected here.

**Do not authorize the clean installation yet.** A later preflight should inventory the exact Windows artifacts and source for the remaining 265-pin resolution, verify available cached files by hash, and sum all new package downloads and temporary overhead. Authorize a fresh installation only if that verified total is within 8 GiB and at least 20 GiB remains free. This phase does not authorize that installation.

The Stage B evidence is retained at `%LOCALAPPDATA%\Temp\rag-pdf-dependency-614k-ls0pr009`, including the three capped index HTML responses, parsed link/checksum observations, raw range-probe results, and offline URL/range validation. The project remains on its existing dependency versions; no Chroma index or user data was accessed. No security approval is implied. HNSW reliability and the natural artifact-publisher incident remain unresolved, Chroma migration is not authorized, and unattended deployment remains **NO-GO**.
