# Phase 6.14J — Dependency Adoption Qualification

**Review date:** 2026-11-03
**Starting revision:** `c343abedc6c1f17e722cf55503601879b170b115`
**Target:** CPython 3.10.11, Windows AMD64
**Classification:** **C — Inconclusive or blocked**
**Installation attempted:** No

The candidate manifest itself passed its byte-level and parser checks. The clean-environment installation did not start because the experiment could not establish the exact CUDA wheel sizes or bound total package downloads below the authorized 8 GiB limit. The official PyTorch index lists the required Windows wheels, but requests for artifact size metadata from this execution host returned HTTP 403. This is an access and resource-bounding limitation, not evidence that the candidate versions are incompatible.

## Repository and manifest checkpoint

The local and remote feature branch were both at `c343abedc6c1f17e722cf55503601879b170b115`. Protected main was `7e9522618296af6a7f25652cc8161009d9a92685`, and `tests/fixtures/research-baseline.json` remained blob `25331e9324182002eb0333a5c56034e684e3c018`.

The only initial worktree change was the existing unstaged `requirements.txt` proposal. There were no staged files or other untracked files.

| Manifest | SHA-256 |
|---|---|
| Committed original | `019954C0721908D17DF10D5109A9C7E5BAD127A6061E7F7B363580740375BFF2` |
| Existing candidate worktree file | `EF3095DBC1A1657CCB5D5FCBA4D2DF245DD5EC9D04B6C2D237823FDFE472734F` |

The candidate remains UTF-16 LE with BOM and CRLF line endings. It contains 265 declarations, each a unique exact pin in the original order. A byte comparison against the committed file confirmed that the only changes are:

```diff
- async-timeout==5.0.1
+ async-timeout==4.0.3

- omegaconf==2.3.0
+ omegaconf==2.4.0
```

The following pins remain unchanged: `antlr4-python3-runtime==4.13.2`, `PyYAML==6.0.3`, `chromadb==1.5.0`, `torch==2.5.1+cu121`, `torchaudio==2.5.1+cu121`, and `torchvision==0.20.1+cu121`. `git diff --text -- requirements.txt` shows only those two replacements; an independent byte comparison matched the exact expected candidate SHA.

## Requirements-file parsing

The existing interpreter is CPython 3.10.11 and its pip version is 26.0.1. A read-only call to pip's requirements-file parser read the original UTF-16 file directly and returned all 265 entries. The ordered normalized names, specifiers, extras, and markers matched the decoded manifest exactly. No UTF-8 temporary manifest was needed. This establishes parsing by the selected pip parser; it does not establish successful dependency resolution or installation.

## Prior candidate evidence

These committed results were reviewed and were not rerun:

| Phase | Recorded result | Limit |
|---|---|---|
| [6.14A](dependency-consistency-614a.md) | Candidate four-wheel subset installed and passed its subset `pip check` and focused checks. | Not the full project manifest. |
| [6.14B](dependency-manifest-feasibility-614b.md) | Candidate metadata graph had no active conflict among the 265 declared pins; baseline had the two known conflicts. | Installed/candidate metadata comparison, not clean resolution or installation. |
| [6.14C](dependency-runtime-compatibility-614c.md) | Original baseline and candidate runtime attempts were inconclusive. | No valid effdet configuration comparison. |
| [6.14D](dependency-runtime-confirmation-614d.md) | Actual installed `effdet==0.4.1` configuration and FPN operations passed with the candidate overlay. | No clean installation, model construction, training, or inference. |
| [6.14E](dependency-application-regression-614e.md) | Inconclusive due to an overlay path-precedence assertion. | Application tests did not run. |
| [6.14F](dependency-application-confirmation-614f.md) | Inconclusive due to a Windows cleanup error. | Candidate application tests did not run. |
| [6.14G](dependency-application-validation-614g.md) | Inconclusive because a namespace-package origin assertion failed. | Application tests did not run. |
| [6.14H](dependency-application-regression-614h.md) | Seven selected RAG-PDF application tests passed under a verified temporary overlay. | No clean install; these tests did not directly exercise OmegaConf or async-timeout APIs. |

The earlier results support the proposed two-pin candidate within their stated bounds. None substitutes for the fresh full-environment installation requested in this phase.

Phase 6.14A recorded verified PyPI wheel identities for the two changed distributions: `async_timeout-4.0.3-py3-none-any.whl` SHA-256 `7405140ff1230c310e51dc27b3145b9092d659ce68ff733fb0cefe3ee42be028`, and `omegaconf-2.4.0-py3-none-any.whl` SHA-256 `f141a664c777582405e3cc6e0545ecc79429e79f844995b1a6e71c35946a025c`. These are prior-phase records; this phase did not download or rehash those wheels.

## Official package sources and platform artifacts

The repository manifest contains no package-index configuration, and the existing pip configuration locations were empty. The [official PyTorch 2.5.1 installation instructions](https://pytorch.org/get-started/previous-versions/) prescribe the CUDA 12.1 wheel index `https://download.pytorch.org/whl/cu121` for the 2.5.1 / 0.20.1 / 2.5.1 package family. The official index pages list these target-compatible filenames:

- [`torch` CUDA 12.1 index](https://download.pytorch.org/whl/cu121/torch/): `torch-2.5.1+cu121-cp310-cp310-win_amd64.whl`
- [`torchvision` CUDA 12.1 index](https://download.pytorch.org/whl/cu121/torchvision/): `torchvision-0.20.1+cu121-cp310-cp310-win_amd64.whl`
- [`torchaudio` CUDA 12.1 index](https://download.pytorch.org/whl/cu121/torchaudio/): `torchaudio-2.5.1+cu121-cp310-cp310-win_amd64.whl`

The remaining declared distributions would require their selected compatible artifacts from PyPI or their separately documented official sources. No 265-package source-to-artifact inventory was produced. Pip does not assign priority simply because an index is listed first; combining PyPI and a PyTorch index with `--extra-index-url` would require auditing the actual selected source for every resolved distribution. No final installation source command was selected or executed.

The official index listing establishes that filenames for the three required wheels are advertised. Direct HEAD and one-byte-range metadata requests to their artifact URLs from this execution host each returned HTTP 403. No artifact body was read. As a result, the exact wheel sizes, hashes, and retrievability from this host were not verified. The exact files were not downloaded.

## Resource gate and stop decision

At preflight, C: had `40,868,319,232` bytes free (about 38.06 GiB), above the required 20 GiB minimum. The authorized new-download limit is 8 GiB. Although free disk met its threshold, the total selected artifact download could not be bounded: the large CUDA wheel sizes were unavailable from the artifact endpoints, and no complete resolver/artifact report existed to sum the 265 selected downloads. A full resolver or installation was not started because it could retrieve substantial metadata or package artifacts before proving compliance with the download cap.

The process stopped before creating a fresh virtual environment, downloading package artifacts, or installing packages. No `pip install`, `pip download`, or pip dry-run resolver command ran. No source build was attempted. Whether every declared pin has an available Windows CPython 3.10 binary wheel was not established. The task-owned evidence directory is:

`%LOCALAPPDATA%\Temp\rag-pdf-dependency-614j-jg3k3wyx`

It contains the manifest/parser audit and source/resource-gate record. No installation command was run.

## Installation, consistency, and application results

There was no isolated candidate environment, so:

- full installation: **not attempted**;
- candidate environment `pip check`: **not run**;
- exact installed versions for the 265 candidate declarations: **not available**;
- complete installed distribution manifest: **not produced**;
- fresh `effdet` configuration check: **not run**;
- seven application tests in a clean environment: **not run**.

The project `rag-env` remains unchanged at `omegaconf==2.3.0`, `async-timeout==5.0.1`, `antlr4-python3-runtime==4.13.2`, `PyYAML==6.0.3`, and `chromadb==1.5.0`. The read-only manifest parser did not install or alter packages.

## Security and reproducibility limits

Phase 6.14A recorded a narrow exact-version OSV lookup for its four tested distributions with no matching advisories at that review time. That was not a complete vulnerability audit and did not cover a newly installed 265-package graph. This phase performed no new advisory scan, clean-environment security qualification, or artifact-hash verification for the PyTorch CUDA wheels. A successful `pip check`, were one available, would establish dependency metadata consistency only; it would not establish security or exploitability.

Exact version pins do not by themselves create a hash-locked wheelhouse or establish that the same artifacts remain available from configured indexes. Clean installation reproducibility remains unproven. The existing Chroma 1.5.0 security and HNSW reliability questions are independent of this dependency proposal.

## Classification and recommendation

**C — Inconclusive or blocked.** The candidate was neither installed nor shown incompatible. The resource threshold for free disk passed, but the authorized download budget could not be evidenced and artifact retrieval metadata was blocked from this host. There is no full-environment `pip check` or clean-runtime result.

**Recommendation: defer the two-pin adoption commit.** Preserve the current unstaged proposal. A later qualification should first establish a source-safe way to obtain the exact CUDA wheels and all selected artifact sizes/hashes, bound the total download to at most 8 GiB while retaining at least 20 GiB free, and then perform the single authorized fresh-environment install and follow-up checks. Do not weaken the version pins, substitute CPU wheels, or infer incompatibility from the HTTP 403 responses.

HNSW reliability and the natural artifact-publisher incident remain unresolved independent release blockers. This dependency study grants no migration or deployment approval. **Unattended deployment remains NO-GO.**
