# Phase 6.14A — Isolated Python Dependency Consistency Investigation

**Review date:** 2026-11-02

**Source revision:** `019e54d3c152e3e291e953428fbd1f37e83e2521`

**Outcome:** **A — Candidate correction verified in the tested subset.**

**Full pinned manifest:** **Not proven consistent.**

This is a dependency consistency investigation only. It does not address the HNSW retrieval incident, the artifact-publisher incident, or deployment readiness. No repository dependency pin or installed package in `rag-env` was changed. This report is local and uncommitted.

## Findings

The one read-only `pip check` run in the existing project environment reproduced the two reported conflicts:

```text
langchain-classic 1.0.1 has requirement async-timeout<5.0.0,>=4.0.0; python_version < "3.11", but you have async-timeout 5.0.1.
omegaconf 2.3.0 has requirement antlr4-python3-runtime==4.9.*, but you have antlr4-python3-runtime 4.13.2.
```

The package metadata and isolated subset support this proposed exact pin change:

```text
async-timeout==4.0.3
omegaconf==2.4.0
```

Keep `antlr4-python3-runtime==4.13.2`, `PyYAML==6.0.3`, and all other pins unchanged in any separately reviewed proposal. This candidate passed metadata checks, an offline isolated install, `pip check` for the four-package subset, and focused package-operation checks. It does **not** establish a clean resolution or runtime compatibility for the complete project environment.

## Existing environment and conflicts

The project environment reports CPython 3.10.11, Windows AMD64, and Chroma 1.5.0. The project manifest specifies Python 3.10.11 in `.python-version`. The single `pip check` execution above returned exit code 1. It was not repeated.

Installed metadata establishes these active constraints on Python 3.10:

| Installed distribution | Constraint relevant to the conflict |
|---|---|
| `langchain-classic==1.0.1` | `async-timeout>=4.0.0,<5.0.0` when Python is below 3.11 |
| `aiohttp==3.13.3` | `async-timeout>=4.0,<6.0` when Python is below 3.11 |
| `langchain-community==0.4.1` | Requires `langchain-classic>=1.0.0,<2.0.0` |
| `effdet==0.4.1` | Requires `omegaconf>=2.0` |
| `omegaconf==2.3.0` | Requires `antlr4-python3-runtime==4.9.*` |

A scan of installed distribution metadata found no other active constraints on these four conflicting distributions beyond the edges above. This describes the installed environment's metadata; it is not a guarantee about every future resolver result.

`requirements.txt` has 265 unique, exact `==` pins, with no environment markers or pip options. Its encoding is UTF-16. The full list was inspected statically, but was not passed to a resolver or installed in a clean environment.

## Ownership and repository usage

The tracked source search found no direct imports of `langchain_classic`, `langchain_community`, `omegaconf`, `async_timeout`, `antlr4`, `effdet`, or `unstructured`. The relevant repository import found is `langchain_core.documents.Document` in `backend/engine/ingestion.py` and its ingestion test. Tracked Python files, notebooks, and configuration files contained no direct imports of the conflict modules.

The package graph still matters even without direct application imports:

- `langchain-community` requires `langchain-classic`; removing only the `langchain-classic` line from the direct pins would not remove the distribution or its `async-timeout` constraint.
- `aiohttp` also accepts `async-timeout==4.0.3`, so the candidate satisfies both active ranges.
- `effdet` requires OmegaConf and its installed configuration modules import `OmegaConf`. `effdet` is directly pinned. `unstructured` metadata also lists `effdet` and `unstructured-inference` for optional PDF, image, and local-inference extras. The repository's tracked ingestion path currently uses its own PyMuPDF code and does not directly import these modules.
- `async-timeout` and ANTLR have no direct imports in tracked application code. Their package-level consumers and constraints remain relevant.

Static evidence does not establish that these packages are safe to remove from every notebook, optional model path, plugin entry point, or future workflow. No direct pin removal is recommended in this investigation.

For the main retrieval/evaluation code and the current index-builder source, no direct import of the four conflict-related modules was found. Runtime plugin loading and optional third-party paths were not exhaustively exercised. The candidate therefore preserves the package set and adjusts only the two exact versions.

## Candidate comparison

### `async-timeout`

The official PyPI release metadata for `async-timeout==4.0.3` says Python `>=3.7`; it provides a universal `py3-none-any` wheel and declares only a `typing-extensions` extra dependency for Python below 3.8. That conditional dependency does not apply to Python 3.10. The release's wheel matches both installed constraints. The PyPI release page reports no Trusted Publishing attestation, so the digest check verifies bytes against PyPI's published hash but is not a build attestation. [PyPI: async-timeout 4.0.3](https://pypi.org/project/async-timeout/4.0.3/)

### OmegaConf and ANTLR alternatives

OmegaConf 2.3.1 retains `antlr4-python3-runtime==4.9.*`, so it does not resolve the mismatch. The official PyPI release for ANTLR runtime 4.9.3 lists only a source archive, not a wheel. That source-build path was not used because this investigation does not authorize source builds. [PyPI: OmegaConf 2.3.1](https://pypi.org/project/omegaconf/2.3.1/), [PyPI: ANTLR runtime 4.9.3](https://pypi.org/project/antlr4-python3-runtime/4.9.3/)

OmegaConf 2.4.0 is an official stable release requiring Python `>=3.10`. Its runtime metadata requires PyYAML `>=5.1` and no longer declares an ANTLR runtime dependency; this is compatible with the project's existing PyYAML 6.0.3 and ANTLR 4.13.2 pins. The upstream issue concerning support for newer ANTLR versions was closed for the 2.4.0 milestone. [PyPI: OmegaConf 2.4.0](https://pypi.org/project/omegaconf/2.4.0/), [OmegaConf 2.4.0 release notes](https://github.com/hydra-ecosystem/omegaconf/releases/tag/v2.4.0), [OmegaConf issue #1218](https://github.com/hydra-ecosystem/omegaconf/issues/1218)

OmegaConf 2.4.0 is a behavior-changing release. The release notes call out native tuples becoming immutable `TupleConfig` values, conversion warnings, and an upgrade guide. The isolated check also confirmed `OmegaConf.create(None)` returns `None` in 2.4.0. The test exercised common configuration operations and the patterns seen in `effdet` configuration source, but did not import or run `effdet`, the model stack, or the RAG application under the candidate version. Those compatibility questions remain open.

The PyPI 2.4.0 wheel has a verified Trusted Publishing attestation identifying the `hydra-ecosystem/omegaconf` GitHub Actions workflow and commit `acd86748f73da70a9820bd4c3f3b6c090cf74967`. PyPI also flags a change of release provenance relative to the previous release. The attestation identity and project release notes were reviewed, but the publishing workflow contents were not independently audited. The attestation supports artifact origin, not a security or application-compatibility approval. As of this review, 2.4.1 release candidates were prereleases; this investigation selected the exact stable 2.4.0 release rather than substituting a prerelease.

## Verified artifacts

Files were selected from the exact PyPI release JSON metadata and fetched from the official `files.pythonhosted.org` URLs. Each complete file's size, archive structure, wheel `METADATA` name/version, and local SHA-256 were checked against the release metadata before installation. All local and published digests matched.

| Distribution | Wheel | Size | Published and local SHA-256 | Notes |
|---|---|---:|---|---|
| `async-timeout==4.0.3` | `async_timeout-4.0.3-py3-none-any.whl` | 5,721 B | `7405140ff1230c310e51dc27b3145b9092d659ce68ff733fb0cefe3ee42be028` | No Trusted Publishing attestation reported by PyPI |
| `omegaconf==2.4.0` | `omegaconf-2.4.0-py3-none-any.whl` | 254,152 B | `f141a664c777582405e3cc6e0545ecc79429e79f844995b1a6e71c35946a025c` | Trusted Publishing attestation; provenance change noted above |
| `antlr4-python3-runtime==4.13.2` | `antlr4_python3_runtime-4.13.2-py3-none-any.whl` | 144,462 B | `fe3835eb8d33daece0e799090eda89719dbccee7aa39ef94eed3818cafa5a7e8` | Existing pin retained; no Trusted Publishing attestation reported |
| `PyYAML==6.0.3` | `pyyaml-6.0.3-cp310-cp310-win_amd64.whl` | 158,561 B | `bdb2c67c6c1390b63c6ff89f210c8fd09d9a1217a465701eac7316313c915e4c` | Existing pin retained; Windows AMD64 CPython 3.10 wheel |

The initial local archive check used a case-sensitive name comparison and stopped on the capitalized `PyYAML` metadata name. No wheel was installed during that attempt. The check was corrected to normalize distribution names, then all four already-downloaded files were reverified against PyPI, including archive integrity and metadata. No unverified wheel was installed.

## Isolated installation and focused checks

One new environment was created under a unique task-owned directory in the operating system temporary area, outside the repository. Its environment name is `candidate-cp310`; the exact interpreter is recorded in the retained external environment manifest. It was created from CPython 3.10.11 on Windows AMD64. `pyvenv.cfg` explicitly has `include-system-site-packages = false`. The candidate interpreter's `sys.path` contained no `rag-env` path.

The four verified wheels were installed offline from the local wheelhouse with `--no-index --no-deps --only-binary=:all:`. The candidate freeze was:

```text
antlr4-python3-runtime==4.13.2
async-timeout==4.0.3
omegaconf==2.4.0
pip==23.0.1
PyYAML==6.0.3
setuptools==65.5.0
```

`pip check` in this minimal candidate environment returned exit code 0 and `No broken requirements found.` This only establishes consistency for the installed subset, not for the 265-pin project manifest.

Focused checks passed for:

- `async_timeout.timeout()` around a no-delay asyncio operation.
- ANTLR runtime import and `InputStream` operation at 4.13.2.
- OmegaConf YAML parse/serialize, interpolation, merge, structured configuration and type validation, and read-only toggling.
- Configuration operations matching patterns in the installed `effdet` source: empty configuration creation, tuple-valued image/anchor settings, nested list/dict settings, `None` values, and read-only toggling.
- OmegaConf 2.4 behavior observations: tuple values report as `TupleConfig`; `OmegaConf.create(None)` returns `None`.

No Chroma client or index was created or opened. No model package stack, project module, network service, or real user data was used in the isolated checks.

The retained temporary evidence includes the verified wheel manifest, candidate environment freeze, installation log, `pip check` output, focused-check script, and focused-check JSON. It is stored outside the repository under the task-owned temporary workspace; none of these artifacts is part of this checkpoint.

## Security and source-build limits

An exact-version OSV query for the four tested distributions returned no matching advisories on 2026-11-02. This is a narrow database query, not a complete vulnerability audit or a safety guarantee. The wheel checks establish byte identity against PyPI metadata. Only OmegaConf 2.4.0 reported a Trusted Publishing attestation among the four selected wheel artifacts; the other artifact pages did not report one. No source distribution was built. The ANTLR 4.9.3 sdist route remains unevaluated and unauthorized.

The candidate remains subject to the project's normal security review. A clean subset `pip check`, package import, and hash match do not establish that the full installed environment is secure or reproducible.

## Full-manifest limits and recommendation

The candidate pair resolves both observed metadata conflicts while preserving the rest of the pinned package set. The `chromadb==1.5.0` pin is unchanged; its installed metadata requires PyYAML `>=6.0`, which remains pinned at 6.0.3. No Chroma persistence, retrieval, HNSW, or project integration test was run.

The following are **not established**:

- Resolution of all 265 exact requirements together on a clean CPython 3.10.11 Windows AMD64 environment.
- Compatibility of OmegaConf 2.4.0 with all installed consumers, especially full `effdet` and any optional inference paths.
- Import/runtime compatibility of the complete project after changing the two pins.
- A complete security audit or production qualification.

Before adopting these candidate pins, a separately approved change should resolve the entire manifest without installing into the current `rag-env`, run `pip check` on that complete fresh environment, and exercise the project's focused configuration, index-builder, retrieval, and evaluation tests. Any workflow using `effdet` or unstructured inference should receive an explicit OmegaConf 2.4 compatibility test. Preserve the current Chroma pin during that work and keep HNSW reliability as a separate gate.

Removing direct pins is not recommended from this evidence: it would not remove `langchain-classic` while `langchain-community` remains, nor OmegaConf while `effdet` remains, and would weaken exact environment reproducibility without demonstrating that every optional workflow is preserved.

**Recommendation:** carry `async-timeout==4.0.3` and `omegaconf==2.4.0` forward as the preferred minimal candidate for a separately reviewed full-manifest qualification. This is not authorization to edit pins or modify the current environment.

## Repository and release status

The source revision remained `019e54d3c152e3e291e953428fbd1f37e83e2521`; protected main and the research fixture were unchanged. `requirements.txt`, `.python-version`, production modules, research files, and `rag-env` package versions were not modified. The baseline verifier passed and confirmed all nine protected research files unchanged.

This work does not resolve the historical HNSW or artifact-publisher incidents. Unattended deployment remains **NO-GO**.
