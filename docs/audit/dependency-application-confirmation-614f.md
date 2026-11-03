# Phase 6.14F — Corrected Candidate Application Regression

**Review date:** 2026-11-02
**Starting revision:** `195dff1f72a2763002830ff0ef2e1e611a1df4af`
**Candidate overlay:** `omegaconf==2.4.0`, `async-timeout==4.0.3`
**Classification:** **C — Inconclusive; the standard-library guard preflight did not exit cleanly.**

The corrected path-precedence self-test passed. The separately authorized standard-library guard child exercised asyncio, Windows socketpair, synthetic network denials, temporary file I/O, and SQLite I/O, but exited with code 1 during temporary-directory cleanup because the test script left its SQLite connection open. The child was reaped and its traceback was retained. The candidate identity child and seven-node pytest session were not started.

## Scope and preserved history

Phase 6.14E remains **C — Inconclusive**. Its original identity child stopped before importing candidate modules because it incorrectly required the overlay to be `sys.path[0]`. The full account and retained failure evidence remain in the [Phase 6.14E report](dependency-application-regression-614e.md); that report was committed as `195dff1` and pushed before this phase began. This report does not rewrite that result or reuse its workspace.

The Phase 6.14F workspace identifier is `rag-pdf-dependency-614f-4aa1dd9b19a34e0bb4ed55800b9f899f`. It is separate from the retained Phase 6.14C, 6.14D, and 6.14E evidence roots. The temporary scripts, extracted overlay, and child outputs are outside the repository.

## Candidate artifacts

The wheels were rehashed and archive-checked from the retained Phase 6.14A wheelhouse before extraction. No installer ran and no files in `rag-env` were modified.

| Wheel | Size | SHA-256 | Result |
|---|---:|---|---|
| `omegaconf-2.4.0-py3-none-any.whl` | 254,152 bytes | `f141a664c777582405e3cc6e0545ecc79429e79f844995b1a6e71c35946a025c` | Hash and archive metadata verified; extracted to the new overlay |
| `async_timeout-4.0.3-py3-none-any.whl` | 5,721 bytes | `7405140ff1230c310e51dc27b3145b9092d659ce68ff733fb0cefe3ee42be028` | Hash and archive metadata verified; extracted to the new overlay |

The overlay contained exactly the two package trees and matching distribution metadata directories. Candidate module imports and their runtime origins were not checked in this phase. A first generated extraction script had a syntax error before it opened either wheel; after correcting that temporary script, the single preparation completed with the expected hashes and contents.

## Corrected path-precedence validation

The replacement check does not require `sys.path[0]` to be the overlay. Its pure path self-test used the synthetic order `script workspace`, `overlay`, `repository`, `rag-env site-packages`; it accepted the script directory at index 0 and the overlay at index 1. It also rejected a module origin outside the overlay and rejected an overlay placed after a competing site-packages directory. This check did not import the candidate packages or project code.

## Standard-library network-guard preflight

One child ran under CPython 3.10.11 from the existing `rag-env` interpreter. The child-local Phase 6.14D guard was installed; no socket behavior was changed in the parent process. The child reported:

- `asyncio.run()` completed a no-network coroutine.
- Windows `socket.socketpair()` exchanged `614f-loopback`, and both sockets were closed.
- IPv4 and IPv6 loopback were classified as local; documentation-only external IPs and `example.invalid` were classified as non-local without resolution.
- Synthetic `getaddrinfo`, `create_connection`, and direct non-loopback checks were blocked before an external request. The recorded real external request count was zero.
- Temporary file reads/writes and a local SQLite create/insert/select operation completed.
- Guard observations contained two allowed loopback connects for socketpair and three blocked synthetic probes.

The child nevertheless exited with code 1. The traceback identifies two cleanup failures at different operations. First, `shutil._rmtree_unsafe()` at `shutil.py:618` called `os.unlink()` on `local.sqlite` and received `PermissionError [WinError 32]` because the script used a SQLite connection context manager but did not close the connection. Then `TemporaryDirectory`'s `onerror()` handler retried `os.unlink()` at `tempfile.py:843`; after that also raised `PermissionError`, its fallback at `tempfile.py:846` called `_rmtree()` with the regular SQLite file path. `shutil.rmtree()` then attempted `os.scandir()` on that file and raised `NotADirectoryError [WinError 267]` at `shutil.py:598`. Thus the second exception is specifically from the cleanup fallback treating the failed file unlink as a directory; it is not a second SQLite operation or evidence of a separate database defect. This is a preflight harness cleanup defect, not a candidate dependency or application failure.

The child result JSON recorded its check values but left its exception field null because the temporary script did not capture exceptions into that field. The complete cleanup traceback is available in the child stderr. The parent record contains Popen PID `19812`, child-reported interpreter PID `34908`, exit code 1, no timeout, and `reaped: true`. The retained outputs are under the workspace's `guard-preflight` directory (`parent-result.json`, `child-result.json`, `guard-events.json`, `stdout.txt`, and `stderr.txt`).

Although the functional guard checks themselves reached their expected results, the required child-cleanup/normal-exit preflight did not pass. No second preflight was run. The guard is a child-local Python socket wrapper, not an OS-level network sandbox; the reused design permits loopback connections needed by local runtime operations. No local service or external service was contacted in this preflight.

## Application regression selection and stop

Static source inspection reconfirmed that the seven requested tests use `tmp_path`, controlled retrieval results, fake deterministic generation clients, and the real service, runner, formatter, artifact store, and SQLite catalog. Their source paths contain no native `ChromaVectorStore` construction, `open_existing()` call, or retrieval query. The retrieval module imports Chroma definitions, but that alone does not open an index. This source review is not dynamic execution evidence.

The identity preflight was **not launched**, so candidate module paths, candidate metadata ownership in the child, stale `sys.modules` state, and application import origins were not verified. The seven-node pytest process was **not launched**. Test counts are therefore zero passed, zero failed, zero skipped, zero errors, and seven unexecuted. No pytest retry, OmegaConf 2.3 baseline run, Chroma operation, model request, or application service invocation occurred.

The exact authorized selection remains unexecuted:

1. `tests/unit/test_experiment_service.py::test_single_evaluation_executes_and_registers_through_catalog`
2. `tests/unit/test_experiment_service.py::test_generation_failure_persists_and_explicit_second_invocation_succeeds`
3. `tests/unit/test_experiment_service.py::test_valid_failed_cases_are_persisted_without_fabricating_success`
4. `tests/unit/test_experiment_service.py::test_service_publication_failure_is_explicit_and_does_not_register`
5. `tests/unit/test_experiment_service.py::test_service_registration_failure_preserves_orphan_for_explicit_recovery`
6. `tests/unit/test_evaluation_runner.py::test_runner_completes_and_aggregates_audited_metrics`
7. `tests/integration/test_experiment_service.py::test_service_uses_real_artifact_store_and_catalog_round_trip`

No claim is made that either candidate package was imported or exercised by RAG-PDF tests. The prior [Phase 6.14D report](dependency-runtime-confirmation-614d.md) separately records bounded `effdet` configuration execution under OmegaConf 2.4.0; it does not fill this application-regression gap.

## Disposition

**Phase 6.14F: C — Inconclusive.** The path-order defect was corrected and its logic self-test passed, but a cleanup defect prevented the standard-library preflight from meeting its clean-exit requirement. The failure is not evidence of candidate incompatibility. No candidate application result was obtained.

The existing project environment remains on OmegaConf 2.3.0, async-timeout 5.0.1, ANTLR 4.13.2, and Chroma 1.5.0. The candidate pins are not adopted. Full clean installation and complete application compatibility remain unproven. Chroma HNSW reliability and the natural artifact-publisher incident remain unresolved. Unattended deployment remains **NO-GO**.

A future separately authorized attempt would need a new workspace and a corrected standard-library preflight that explicitly closes SQLite connections before temporary cleanup, captures child exceptions, and exits normally. It would then need to pass candidate module-origin/metadata checks before using a fresh single pytest-session budget. This report does not authorize that attempt.
