# Phase 6.14E — Candidate Application Regression

**Review date:** 2026-11-02<br>
**Starting revision:** `6395d02b80f0f83d3f74ef69c8bf36d4aa351dae`<br>
**Candidate:** `omegaconf==2.4.0`, `async-timeout==4.0.3` in a temporary overlay<br>
**Classification:** **C — Inconclusive; the overlay identity preflight did not pass.**

No selected pytest node was run. The test-session budget remains unused. The preflight failed on an over-strict path-order assertion before importing candidate modules or application source, so this report does not claim RAG-PDF application regression compatibility under the candidate overlay.

## Purpose and boundaries

This phase was intended to run seven existing service, evaluation, and persistence tests with the proposed packages overlaid on the existing CPython 3.10.11 `rag-env`. It did not change `requirements.txt`, `.python-version`, production code, research files, or the project environment. The preflight ran no test and opened no Chroma client or index. No Chroma smoke, full suite, or baseline OmegaConf 2.3.0 run was performed.

Stage A committed the prior Phase 6.14D report as `6395d02b80f0f83d3f74ef69c8bf36d4aa351dae`, parent `82ea8df647f01ca9b2e6feac532c70089b9cbd2a`; that commit changed only `docs/audit/dependency-runtime-confirmation-614d.md`. The Stage A commit was pushed before this stage began.

## Candidate artifacts and overlay preparation

The wheels were read from the retained Phase 6.14A wheelhouse and rehashed before extraction into a new task-owned directory. ZIP integrity, member paths, top-level package roots, and distribution metadata were checked. No package installer ran.

| Wheel | Size | SHA-256 | Preparation result |
|---|---:|---|---|
| `omegaconf-2.4.0-py3-none-any.whl` | 254,152 bytes | `f141a664c777582405e3cc6e0545ecc79429e79f844995b1a6e71c35946a025c` | Verified; extracted to temporary overlay |
| `async_timeout-4.0.3-py3-none-any.whl` | 5,721 bytes | `7405140ff1230c310e51dc27b3145b9092d659ce68ff733fb0cefe3ee42be028` | Verified; extracted to temporary overlay |

The overlay contained only the two expected package trees and their matching `.dist-info` directories. Its task-owned root was `%LOCALAPPDATA%\Temp\rag-pdf-dependency-614e-7466bcd0c12c4e2cb1c750286470a664\candidate-overlay`.

## Preflight outcome and stop

The task-owned Phase 6.14E workspace identifier is `rag-pdf-dependency-614e-7466bcd0c12c4e2cb1c750286470a664`. No pytest candidate-attempt ID exists because the pytest process was never launched. The single overlay identity preflight ran in CPython 3.10.11, Windows AMD64. Its runner launched `python.exe <workspace>\identity_preflight.py` with a 90-second timeout. The child exited with code 1 in about 0.55 seconds, did not time out, and its runner completed and reaped the Popen process. The child reported PID 30888; the runner recorded Popen PID 31268. Those identities are retained separately.

The preflight child received `PYTHONNOUSERSITE=1`, bytecode writes disabled, `PYTHONPATH` ordered as overlay/workspace/repository, task-owned temporary/cache paths, offline model-cache flags, and proxy variables removed. These controls applied only to that child. They do not represent a pytest-session execution.

The preflight stopped at this assertion:

```text
AssertionError: candidate overlay is not first on sys.path
```

The script required the overlay to be `sys.path[0]`. The recorded path order placed the child script's workspace at index 0 and the candidate overlay at index 1; the overlay still preceded the repository and `rag-env` site-packages. This was a preflight assertion defect, not evidence of a dependency incompatibility. However, the child stopped before importing OmegaConf, async-timeout, PyYAML, ANTLR, Chroma, or the application modules. The final module-path checks therefore were not made.

The exit-time metadata observation found candidate distribution metadata for OmegaConf 2.4.0 and async-timeout 4.0.3 in the overlay, and the existing metadata for PyYAML 6.0.3, ANTLR 4.13.2, and Chroma 1.5.0 in `rag-env`. It recorded no module paths for those packages because none had been imported. The child-local network guard installed, but the preflight stopped before its asyncio/socketpair and synthetic blocked-destination checks; its event lists were empty. No external DNS request or connection was made.

Because the candidate module ownership and guard checks were not fully established, the one authorized pytest session was **not started**. There were no pytest passes, failures, skips, or errors; all seven selected nodes remain unexecuted in this phase. No retry or second preflight was made.

Retained diagnostic files are under `%LOCALAPPDATA%\Temp\rag-pdf-dependency-614e-7466bcd0c12c4e2cb1c750286470a664\logs\`, including `preflight-parent-result.json`, the child result, site observation, stdout, and stderr. These temporary files and the extracted overlay are outside the repository.

## Exact regression selection reviewed, not executed

Static source inspection confirmed all seven requested test functions exist. Their setup uses test-owned `tmp_path` storage and controlled retrieval results; none calls `retrieve_for_plan()` to query Chroma.

| Exact pytest node | Source-level boundary reviewed | Execution status |
|---|---|---|
| `tests/unit/test_experiment_service.py::test_single_evaluation_executes_and_registers_through_catalog` | `_service()` supplies controlled retrieval, real `ExperimentService`, `EvaluationRunner`, `ContextFormatter`, deterministic fake generation client, artifact store, and SQLite catalog. | Not run |
| `tests/unit/test_experiment_service.py::test_generation_failure_persists_and_explicit_second_invocation_succeeds` | Controlled generation failure and separate explicit invocation; verifies failure and success artifacts remain distinct. | Not run |
| `tests/unit/test_experiment_service.py::test_valid_failed_cases_are_persisted_without_fabricating_success` | Injected `RetrievalQueryError`; asserts context and generation are not reached. | Not run |
| `tests/unit/test_experiment_service.py::test_service_publication_failure_is_explicit_and_does_not_register` | Real artifact-store path with a module-local `os.link` fault proxy and test-owned files. | Not run |
| `tests/unit/test_experiment_service.py::test_service_registration_failure_preserves_orphan_for_explicit_recovery` | Real artifact store/catalog; registration fault before SQLite work, then explicit orphan registration. | Not run |
| `tests/unit/test_evaluation_runner.py::test_runner_completes_and_aggregates_audited_metrics` | Real runner with a supplied deterministic retrieval object and fake generation client; checks the M1–M19 observation set. | Not run |
| `tests/integration/test_experiment_service.py::test_service_uses_real_artifact_store_and_catalog_round_trip` | Test-level service round-trip using the same controlled `_service()` fixture and real filesystem/SQLite persistence. | Not run |

These tests import the retrieval module and its `ChromaVectorStore` class definition, which imports the Chroma package. Static call paths show no `ChromaVectorStore` construction, `open_existing()`, native query, or persistent index access in the selected test bodies. This was not dynamically verified by a pytest session.

The selected fixture injects the generation client's `chat()` response and token encoder. The generation adapter's default Ollama client factory is not selected by that fixture. The tests use temporary artifact directories and SQLite databases. No production model constructor or user document is part of the reviewed path.

## Candidate package behavior and classification

The inspected selected application/test modules contain no direct `omegaconf` or `async_timeout` import. The Chroma package is imported through the retrieval module, but no client is constructed in the reviewed tests. Since the pytest session did not run, this phase did not establish whether another transitive import loads either candidate module during those tests. It also did not dynamically verify generation, context, metric, artifact, catalog, or provenance assertions under the overlay.

**Classification C — Inconclusive.** The preflight harness assertion prevented the required module-identity and network-guard checks from completing. The failure is not attributable to OmegaConf, async-timeout, Chroma, or an application contract. No candidate-attributable application incompatibility was observed, and no application regression pass was established.

The project remains pinned to `omegaconf==2.3.0`, `async-timeout==5.0.1`, `antlr4-python3-runtime==4.13.2`, and `chromadb==1.5.0`. Full-manifest clean installation, complete application compatibility, and security qualification remain unproven. A future separately authorized run would need to correct the path assertion to require the overlay to precede the installed site-packages, then complete the identity/network preflight before using a fresh one-session test budget. This report does not authorize that run or adoption of the candidate pins.

The historical Chroma HNSW reliability issue and natural artifact-publisher incident remain unresolved. **Unattended deployment remains NO-GO.**
