# Phase 6.14G — Candidate Application Regression

**Review date:** 2026-11-03 <br>
**Starting revision:** `0639fc3e93bccbb12fdde8bc3687d808a2239b43` <br>
**Candidate overlay:** `omegaconf==2.4.0`, `async-timeout==4.0.3` <br>
**Classification:** **C — Inconclusive; candidate identity preflight stopped at repository-origin validation.**

The new standard-library guard preflight exited successfully. The one candidate identity child verified the candidate module files and metadata, but failed its repository-source check before a pytest session was started. The seven-test budget remains unused. No candidate application result was obtained.

## Preserved 6.14F finding and cleanup correction

The committed [Phase 6.14F report](dependency-application-confirmation-614f.md) remains **C — Inconclusive**. Its first cleanup error was `PermissionError [WinError 32]` at `shutil._rmtree_unsafe()` (`shutil.py:618`) when unlinking the still-open SQLite file. The second was a separate cleanup-handler failure: `TemporaryDirectory.onerror()` retried the unlink (`tempfile.py:843`), then called `_rmtree()` on the regular file (`tempfile.py:846`); `shutil.rmtree()` attempted `os.scandir()` and raised `NotADirectoryError [WinError 267]` (`shutil.py:598`). The latter followed the first error through tempfile's fallback; it was not another SQLite operation.

The 6.14G guard harness removes that unnecessary SQLite/temp-directory probe. It creates no database handle or temporary directory. Its only acquired resources are the two socketpair sockets, closed in a `finally` block and checked after close. Runner logs and child JSON are written to a separate, retained task-owned evidence directory. The actual application tests were not executed, so no SQLite application outcome is claimed. In the reviewed source, `ExperimentCatalog` closes its own connections in `finally` blocks. One selected regression also opens a test-local SQLite connection with a context manager; that context commits or rolls back but does not itself close the connection. The source contains no custom removal of that test's database directory, and its data path is under pytest's owned base-temp tree. The connection lifecycle during an actual test session remains unverified here.

The earlier path-precedence assertion was not reused. The current checks accept a script directory before the overlay, use resolved module-file containment, and verify that the overlay precedes installed site-packages.

## Candidate artifacts

The wheel files were rehashed from the retained Phase 6.14A wheelhouse and extracted into a fresh overlay. No installer ran and no package was added to `rag-env`.

| Wheel | Size | SHA-256 |
|---|---:|---|
| `omegaconf-2.4.0-py3-none-any.whl` | 254,152 bytes | `f141a664c777582405e3cc6e0545ecc79429e79f844995b1a6e71c35946a025c` |
| `async_timeout-4.0.3-py3-none-any.whl` | 5,721 bytes | `7405140ff1230c310e51dc27b3145b9092d659ce68ff733fb0cefe3ee42be028` |

The new workspace identifier is `rag-pdf-dependency-614g-63b475aa2c5f4b7e80e94e831cee2984`. It is separate from the retained Phase 6.14C, 6.14D, 6.14E, and 6.14F roots.

## Standard-library guard preflight

One child ran with CPython 3.10.11 from `rag-env`. The child reported `asyncio.run()` success, exchanged the bounded payload `614g-loopback` over Windows `socket.socketpair()`, and confirmed both sockets were closed. It classified documentation-only external addresses and `example.invalid` as non-local. Synthetic `getaddrinfo`, `create_connection`, and direct non-loopback checks were blocked before an external request. The guard recorded two allowed loopback connects for socketpair and three blocked synthetic probes; real external requests: **0**.

The child exited 0 in about 0.36 seconds, did not time out, and was reaped. Runner Popen PID was `27268`; child interpreter PID was `21572`. No SQLite or temporary-directory cleanup was part of this simplified preflight. Output is retained under `guard-preflight-evidence/` (`parent-result.json`, `child-result.json`, `guard-events.json`, `stdout.txt`, and `stderr.txt`).

## Candidate identity preflight

One fresh child ran with the intended interpreter and candidate overlay. It exited 1 in about 1.02 seconds, did not time out, and was reaped. Runner Popen PID was `22592`; child interpreter PID was `29540`.

The child verified:

- CPython 3.10.11, Windows AMD64, from the expected `rag-env` executable.
- OmegaConf 2.4.0 imported from the new overlay, with distribution metadata rooted in that overlay.
- async-timeout 4.0.3 imported from the new overlay, with distribution metadata rooted in that overlay.
- PyYAML 6.0.3 and ANTLR 4.13.2 imported from `rag-env` site-packages.
- Chroma distribution metadata remained 1.5.0 in `rag-env`.
- The synthetic path-precedence self-test passed, and the actual overlay-before-site-packages check completed before the repository check.
- The child-local guard remained installed with no allowed or blocked network events.

The child then raised `AssertionError: backend package spec is unavailable` at `identity_preflight.py:60`. Inspection confirmed the repository's `backend/` directory has no `backend/__init__.py`, so it is a namespace package; `find_spec("backend")` may have `origin=None` while exposing repository locations through `submodule_search_locations`. The preflight incorrectly required a non-null `origin`, so it did not validate the actual `backend.services.experiment_service` source path. This is an identity-harness defect, not evidence of candidate package incompatibility. The precise traceback and JSON are retained under `identity-preflight-evidence/`.

No identity retry was made. Since the repository source-origin requirement did not pass, the seven-node pytest session was **not launched**. Counts: zero passed, zero failed, zero skipped, zero errors, and seven unexecuted. No selected application contract was dynamically exercised under this overlay.

## Application source review and isolation

Static review reconfirmed the seven requested nodes use controlled retrieval objects, test-owned `tmp_path` storage, a deterministic fake generation client, the real `ExperimentService`, `EvaluationRunner`, `ContextFormatter`, artifact store, and SQLite catalog. Their bodies do not instantiate `ChromaVectorStore`, call `open_existing()`, or execute a native Chroma query. Importing the retrieval module imports Chroma definitions; it does not itself construct a client. This is source-review evidence only.

The pytest bootstrap is prepared to call `pytest.main()` once with the seven fixed node IDs, a fresh task-owned `--basetemp`, plugin autoload disabled, and a bounded subprocess timeout. The intended child command shape was `rag-env\Scripts\python.exe <workspace>\pytest_bootstrap.py <repository> <overlay> <fresh-basetemp> <seven nodes>`. **This command was not invoked.** The exact nodes left unexecuted are:

1. `tests/unit/test_experiment_service.py::test_single_evaluation_executes_and_registers_through_catalog`
2. `tests/unit/test_experiment_service.py::test_generation_failure_persists_and_explicit_second_invocation_succeeds`
3. `tests/unit/test_experiment_service.py::test_valid_failed_cases_are_persisted_without_fabricating_success`
4. `tests/unit/test_experiment_service.py::test_service_publication_failure_is_explicit_and_does_not_register`
5. `tests/unit/test_experiment_service.py::test_service_registration_failure_preserves_orphan_for_explicit_recovery`
6. `tests/unit/test_evaluation_runner.py::test_runner_completes_and_aggregates_audited_metrics`
7. `tests/integration/test_experiment_service.py::test_service_uses_real_artifact_store_and_catalog_round_trip`

No model, Ollama, external API, or Chroma service was contacted; no native index was opened; no pytest session artifacts were written. The guard is a Python socket wrapper, not an OS-level network sandbox, and it permits loopback operations for interpreter internals.

## Disposition

**Phase 6.14G: C — Inconclusive.** The guard preflight passed and candidate package identities were observed at the intended overlay paths, but the identity child stopped on an incorrect assumption about the repository's namespace package. The application tests were not run. This result does not show candidate incompatibility or compatibility.

The project remains pinned to OmegaConf 2.3.0, async-timeout 5.0.1, ANTLR 4.13.2, PyYAML 6.0.3, and Chroma 1.5.0. Full clean installation and complete application compatibility remain unproven. The HNSW reliability issue and natural artifact-publisher incident remain unresolved. Unattended deployment remains **NO-GO**.

A further attempt would require separate authorization and a fresh workspace. Its identity check should validate the actual application module spec or namespace-package search locations against the repository, then use a new single pytest-session budget only after that check passes. This report does not authorize another attempt.
