# Phase 6.14B — Dependency Manifest Feasibility

**Review date:** 2026-11-02<br>
**Source revision:** `49506f9628a8a91529d4e13e545fd03af30f5961`<br>
**Phase 6.14A checkpoint:** `49506f9628a8a91529d4e13e545fd03af30f5961` (parent `019e54d3c152e3e291e953428fbd1f37e83e2521`)<br>
**Classification:** **A — Metadata consistency verified for the complete declared set.**<br>
**Clean installation:** Not established. **Application runtime compatibility:** Not established.

This report evaluates only whether the proposed `async-timeout` and OmegaConf versions satisfy the declared package metadata graph for CPython 3.10.11 on Windows AMD64. It does not change dependency pins or qualify the project for release.

## Question and candidate

The candidate differs from the current manifest only in these two lines:

```diff
- async-timeout==5.0.1
+ async-timeout==4.0.3

- omegaconf==2.3.0
+ omegaconf==2.4.0
```

The following pins, and all other declared pins, remain unchanged in this comparison:

```text
antlr4-python3-runtime==4.13.2
PyYAML==6.0.3
chromadb==1.5.0
```

No repository dependency file was edited.

## Declared manifest and observed environment

`requirements.txt` contains **265 declarations and 265 unique normalized distribution names**. All declarations use exact `==` pins. The file contains no duplicate normalized names, markers, extras, direct URLs, or pip index options. `.python-version` specifies Python 3.10.11.

The target and metadata marker values were CPython 3.10.11, Windows (`sys_platform=win32`, `platform_system=Windows`), AMD64. The existing `rag-env` is Windows AMD64 with CPython 3.10.11. At inspection, metadata was available for all 265 declared distributions and each installed version matched its declared pin. There were no missing declared distributions or installed-version mismatches.

Five installed distributions were not directly declared: `iniconfig==2.3.1`, `pip==26.0.1`, `pluggy==1.7.0`, `pytest==9.1.1`, and `setuptools==82.0.0`. `pip` is reachable through installed metadata because `pip-review==1.3.0` requires it. The others are present in the current environment but are not direct manifest entries. Their presence does not make them declared project requirements.

The baseline and candidate comparisons used installed distribution metadata for unchanged pins. For the two candidate replacements, they used the `METADATA` from the exact wheels verified in Phase 6.14A. Marker evaluation was limited to the stated target; dependencies conditional on unrequested extras were not treated as active. This is a complete metadata comparison of the 265 declared pins against the metadata available for that environment, not a clean resolver run.

## Baseline and candidate metadata results

| Comparison | Declared distributions with metadata | Missing active dependency metadata | Active unsatisfied constraints | Metadata parse errors |
|---|---:|---:|---:|---:|
| Current 265-pin baseline | 265 of 265 | 0 | 2 | 0 |
| Candidate, changing only the two pins | 265 of 265 | 0 | 0 | 0 |

The metadata traversal evaluated applicable requirements for the target Python and Windows markers, including active transitive edges. Conditional requirements for extras not requested by the manifest were not treated as active. The candidate removes the OmegaConf 2.3 ANTLR constraint; it does not remove the declared ANTLR pin. The changed active edges are:

| Owner | Target requirement | Baseline target | Candidate target | Result |
|---|---|---|---|---|
| `langchain-classic==1.0.1` | `async-timeout>=4.0.0,<5.0.0` on Python below 3.11 | `5.0.1`, rejected | `4.0.3`, accepted | Candidate satisfies the active marker |
| `aiohttp==3.13.3` | `async-timeout>=4.0,<6.0` on Python below 3.11 | `5.0.1`, accepted | `4.0.3`, accepted | Both satisfy the active marker |
| `omegaconf==2.3.0` | `antlr4-python3-runtime==4.9.*` | `4.13.2`, rejected | Not declared by OmegaConf 2.4.0 | Candidate no longer conflicts |
| `effdet==0.4.1` | `omegaconf>=2.0` | `2.3.0`, accepted | `2.4.0`, accepted | Candidate satisfies the declared range |

The scan found no other active installed-metadata constraint rejecting either proposed version, and no metadata parse errors. A separate check of installed but undeclared distributions found no active constraints on `async-timeout`, OmegaConf, or ANTLR that reject the candidate. The baseline therefore reproduces the two known conflicts; the candidate is metadata-consistent across the evaluated declared set. This does not account for different metadata in a clean artifact resolution or metadata absent from the current environment.

Optional dependency metadata was evaluated as conditional: no extras are requested by the manifest. For example, `unstructured==0.18.31` declares `effdet` and `unstructured-inference` under optional PDF, image, and local-inference extras. Those extra-only edges were not made mandatory by this comparison.

## Candidate artifact evidence

Phase 6.14A fetched the following exact wheels from official PyPI file URLs, verified archive structure and normalized distribution metadata, and matched local SHA-256 values to PyPI's published hashes:

| Candidate | Wheel | Verified SHA-256 | Phase 6.14A size |
|---|---|---|---:|
| `async-timeout==4.0.3` | `async_timeout-4.0.3-py3-none-any.whl` | `7405140ff1230c310e51dc27b3145b9092d659ce68ff733fb0cefe3ee42be028` | 5,721 B |
| `omegaconf==2.4.0` | `omegaconf-2.4.0-py3-none-any.whl` | `f141a664c777582405e3cc6e0545ecc79429e79f844995b1a6e71c35946a025c` | 254,152 B |

Those exact wheels were used in the prior four-distribution isolated subset with the unchanged ANTLR 4.13.2 and PyYAML 6.0.3 wheels. The subset install and `pip check` passed, and its focused package-operation checks passed. This phase downloaded no artifacts and installed no packages. The subset result is evidence for those four distributions only.

PyPI records the `async-timeout` wheel without a Trusted Publishing attestation. OmegaConf 2.4.0 has a PyPI publishing attestation, and PyPI flags a change of release provenance compared with prior releases. Hash equality establishes artifact identity against PyPI's published digest; it is not a security audit or independent build review. See the [Phase 6.14A artifact and subset record](dependency-consistency-614a.md), [async-timeout 4.0.3 on PyPI](https://pypi.org/project/async-timeout/4.0.3/), and [OmegaConf 2.4.0 on PyPI](https://pypi.org/project/omegaconf/2.4.0/).

## Package source and clean-resolution limits

The manifest pins CUDA-local versions:

```text
torch==2.5.1+cu121
torchaudio==2.5.1+cu121
torchvision==0.20.1+cu121
```

The manifest has no index configuration, and repository documentation does not record a package index for these pins. The official PyTorch previous-versions instructions identify the CUDA 12.1 wheel index for the matching PyTorch 2.5.1 family. The official index contains Windows CPython 3.10 AMD64 wheel names matching these local-version pins. The exact `+cu121` versions are not served as standard PyPI releases. These sources make a future source-aware resolution conceivable, but the repository itself does not make that source configuration reproducible. No CUDA wheel was downloaded in this phase.

No `pip --dry-run` resolver command was run. There is therefore no resolver command or resolver report to cite. A full `--ignore-installed` resolution would need to consult the package indexes and may retrieve substantial CUDA distribution metadata or artifacts. It was not attempted because this phase does not authorize downloading the complete CUDA/PyTorch stack merely to answer a metadata question. No source build was attempted. The official PyTorch instructions are at [Previous PyTorch Versions](https://docs.pytorch.org/get-started/previous-versions/), with the [CUDA 12.1 torch index](https://download.pytorch.org/whl/cu121/torch/), [torchvision index](https://download.pytorch.org/whl/cu121/torchvision/), and [torchaudio index](https://download.pytorch.org/whl/cu121/torchaudio/).

Accordingly:

- **Metadata consistency:** candidate passed for all 265 declared pins using installed metadata for unchanged distributions and verified candidate wheel metadata.
- **Package availability:** the two candidate wheels were verified in Phase 6.14A, and all 265 declared distributions were present in the observed environment. A clean-environment artifact audit of all 265 releases was not performed.
- **Reproducible installation:** not established. The manifest does not capture the official CUDA wheel source, and no clean resolver or full install was performed.
- **Runtime application compatibility:** not established. No project modules or full model/inference paths were run under the candidate pair.
- **Security qualification:** not established by metadata consistency, subset `pip check`, hashes, or the narrow exact-version OSV lookup documented in Phase 6.14A.

## OmegaConf and dependent-package risk

`effdet==0.4.1` is a directly pinned distribution with an active `omegaconf>=2.0` requirement. Its installed config modules import OmegaConf. Source inspection found concrete use of mutable list behavior: `effdet.config.fpn_config` creates an OmegaConf object and appends generated nodes with `p.nodes.append(...)`. Model configuration also uses tuple-valued image/anchor settings, nested configuration values, and read-only toggling. Other code reads these settings as sequences and iterates nested FPN node mappings.

The Phase 6.14A subset tested selected OmegaConf operations and patterns, and recorded 2.4.0 behavior changes including native tuples becoming immutable `TupleConfig` values and `OmegaConf.create(None)` returning `None`. It did **not** import or execute `effdet` under 2.4.0. In particular, the actual `effdet` FPN `ListConfig.append` and model-config factory paths remain unverified. No source evidence found in the inspected effdet config modules for `OmegaConf.merge`, `OmegaConf.to_container`, or `OmegaConf.to_yaml`; this limited search is not proof about all consumers or runtime plugins. OmegaConf 2.4.0's [release notes](https://github.com/hydra-ecosystem/omegaconf/releases/tag/v2.4.0) call out Python 3.10+ and tuple/config behavior changes. Importing OmegaConf or passing a package-subset test does not establish full effdet compatibility.

## Conclusion and next qualification step

**Classification A — Metadata consistency verified for the complete declared set.** Under the recorded Python 3.10.11 Windows AMD64 target and the metadata sources described above, the candidate pair satisfies all active version constraints discovered across the 265 declared exact pins. It removes the two baseline metadata conflicts without changing other pins.

This classification does not mean clean installation is reproducible, runtime compatibility is proven, or the package set is secure. Full-project installation and runtime qualification remain open.

The minimum next engineering action before proposing adoption is a separately approved, disposable CPython 3.10.11 Windows resolution/install using explicit official PyPI and PyTorch CUDA 12.1 sources, followed by `pip check` and a focused `effdet` config-contract test that exercises `get_fpn_config()` node appends and the model-config tuple/read-only paths. Keep the existing `rag-env` and all production pins untouched until that qualification and an independent requirements change review.

No candidate pin is approved by this report. The project remains pinned to Chroma 1.5.0. The historical HNSW retrieval incident and natural artifact-publisher integrity incident remain unresolved; Phase 6.12F does not establish general operational reliability. Known Chroma security concerns remain outside this dependency-metadata evaluation. Unattended deployment remains **NO-GO**.
