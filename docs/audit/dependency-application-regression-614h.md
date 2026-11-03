# Phase 6.14H — Candidate Application Regression

**Review date:** 2026-11-03
**Starting revision:** 0639fc3e93bccbb12fdde8bc3687d808a2239b43
**Phase 6.14G checkpoint:** ec1ea659e63a131e453b829da65f191acc3c7df6
**Attempt ID:** b1c889d9cf38447e8d30ededa6bb2dd0
**Classification:** **A — Bounded candidate application regression passed.**

This is one new, bounded application-test session under a temporary overlay. It does not change project dependencies or establish full installation, runtime, or release qualification.

## Preserved experiment history

The prior attempts remain unchanged and retain their original classifications:

- **6.14E — C, inconclusive:** the identity preflight's path-index assumption failed before candidate module imports; no application tests ran.
- **6.14F — C, inconclusive:** Windows temporary-directory cleanup failed after SQLite remained open; no candidate identity check or pytest test ran.
- **6.14G — C, inconclusive:** the simplified guard preflight passed and candidate module paths were observed, but the identity check incorrectly required a non-null backend.__spec__.origin. The repository uses a namespace package, and no application pytest node ran.

The 6.14G report was committed and pushed separately as ec1ea659e63a131e453b829da65f191acc3c7df6, parent 0639fc3e93bccbb12fdde8bc3687d808a2239b43. This 6.14H result does not overwrite or reclassify those observations.

## Isolation and artifact provenance

The project rag-env was used as a read-only base with a task-owned overlay at:

%LOCALAPPDATA%\Temp\rag-pdf-dependency-614h-b1c889d9cf38447e8d30ededa6bb2dd0\candidate-overlay

The two copied wheels were rehashed before extraction:

| Candidate | Filename | Size | SHA-256 |
|---|---|---:|---|
| OmegaConf 2.4.0 | omegaconf-2.4.0-py3-none-any.whl | 254,152 bytes | f141a664c777582405e3cc6e0545ecc79429e79f844995b1a6e71c35946a025c |
| async-timeout 4.0.3 | async_timeout-4.0.3-py3-none-any.whl | 5,721 bytes | 7405140ff1230c310e51dc27b3145b9092d659ce68ff733fb0cefe3ee42be028 |

The copied wheel hashes matched the recorded Phase 6.14A values. The unchanged 6.14G network_guard.py had SHA-256 B754DBEFD386B40080A7DC411ECD29C5F2E00F007D900E09E9C0D77BBADAA3BF, matching the retained 6.14D/6.14G implementation. The standard-library guard preflight was **not rerun**; its successful 6.14G result remains the guard evidence.

The one corrected identity child used CPython 3.10.11, Windows AMD64. It exited 0, was reaped, and reported:

- OmegaConf 2.4.0 and async-timeout 4.0.3 modules imported from the overlay.
- Their module version attributes and selected distribution versions matched the candidates.
- The overlay preceded rag-env site-packages (sys.path positions 2 and 9); the script directory was allowed to precede the overlay.
- PyYAML 6.0.3, ANTLR 4.13.2, and Chroma 1.5.0 remained in rag-env.
- backend resolved with origin=None and exactly one namespace search location: this checkout's backend directory.
- A non-importing PathFinder lookup resolved backend.services.experiment_service to this checkout.
- No Chroma module was imported by the identity child, and the child-local network guard recorded no activity.

### Metadata-path evidence correction

The preserved identity-child JSON contains an inaccurate display path for distribution metadata: the harness called Distribution.locate_file("METADATA"), which resolved relative to the distribution root and recorded a nonexistent overlay-root METADATA path instead of the .dist-info/METADATA member. The child result is preserved unchanged; its version-selection evidence is not presented as a valid metadata-file path.

Before pytest, a separate read-only artifact audit verified each actual overlay .dist-info/METADATA file byte-for-byte against that member in the already hash-verified wheel. The audit records:

- omegaconf-2.4.0.dist-info/METADATA, version 2.4.0, SHA-256 8ec408361bf79ca9c53f5b266097692f89db8ef00ac75abeaf0679af8ceb3312.
- async_timeout-4.0.3.dist-info/METADATA, version 4.0.3, SHA-256 59055c9c32174369ed79b726faf62384d2e0fd531e4d6c35dff45e4fe537e89e.

The pytest child independently resolved the distribution's METADATA entry through dist.files, verified the real .dist-info path and contents, and recorded those paths before invoking pytest. No identity child was rerun. The supplementary record is identity-metadata-audit.json in the evidence workspace.

## Namespace-aware repository check

The corrected check accepts the actual namespace package shape:

- backend.__spec__.origin is None.
- Its only submodule_search_locations entry is the expected checkout's backend directory.
- PathFinder resolves the services package and experiment_service.py beneath that directory without importing the production service for the identity check.

The check did not modify sys.path, add an __init__.py, or rely on sys.path[0].

## One pytest session

The exact seven requested nodes were passed once, in the requested order. The launcher disabled third-party pytest plugin autoload, used no parallel workers, used a new task-owned --basetemp, and did not use fail-fast or retries.

The bounded launcher command was:

    rag-env\Scripts\python.exe -I <workspace>\run_once.py <workspace> <repository> <rag-env-python> pytest-session 300 <workspace>\pytest_bootstrap.py <repository> <overlay> <workspace>\pytest-data <seven exact node IDs>

The child invoked pytest once with:

    -q -s -p no:cacheprovider --basetemp <workspace>\pytest-data

followed by the seven exact node IDs listed below:

1. tests/unit/test_experiment_service.py::test_single_evaluation_executes_and_registers_through_catalog
2. tests/unit/test_experiment_service.py::test_generation_failure_persists_and_explicit_second_invocation_succeeds
3. tests/unit/test_experiment_service.py::test_valid_failed_cases_are_persisted_without_fabricating_success
4. tests/unit/test_experiment_service.py::test_service_publication_failure_is_explicit_and_does_not_register
5. tests/unit/test_experiment_service.py::test_service_registration_failure_preserves_orphan_for_explicit_recovery
6. tests/unit/test_evaluation_runner.py::test_runner_completes_and_aggregates_audited_metrics
7. tests/integration/test_experiment_service.py::test_service_uses_real_artifact_store_and_catalog_round_trip

**Result:** 7 passed in 7.48s; pytest exit code 0. There were zero failures, skips, or errors. The session child recorded one pytest invocation, elapsed 8.34 seconds, no timeout, and was reaped. The outer bounded runner exited 0 in 8.86 seconds and reaped its child. No retry occurred.

The complete output and structured records are retained under:

%LOCALAPPDATA%\Temp\rag-pdf-dependency-614h-b1c889d9cf38447e8d30ededa6bb2dd0\pytest-session-evidence

The identity evidence and supplementary metadata audit are under the same attempt workspace in identity-preflight-evidence/ and identity-metadata-audit.json.

## Application contracts exercised

The selected tests used the existing _service() fixture and setup_chain() helper. Static source review and the test assertions establish controlled retrieval results, real ExperimentService, EvaluationRunner, ContextFormatter, LocalGenerationAdapter with a deterministic fake client, ExperimentArtifactStore, and a real temporary SQLite ExperimentCatalog.

The session passed the existing assertions for:

- Successful service evaluation and catalog/artifact reload.
- Retrieval-stage failure persistence without context or generation fabrication.
- Generation-stage failure persistence followed by a separate explicit successful invocation.
- Controlled artifact-publication failure handling.
- Valid orphan-artifact preservation, explicit registration, and idempotent catalog recovery.
- The selected runner's M1–M19 observation/aggregate contract.

The tests preserve their existing assertions for provenance, artifact identity, safe error codes, private-error redaction, and metric observation states. They do not establish that all 19 metrics are numerically computed.

The selected test source injects retrieval results and uses temporary persistence; it does not call retrieve_for_plan(), open_existing(), or a Chroma collection query. Importing backend.engine.retrieval also imports Chroma definitions. Chroma 1.5.0's package module was present during pytest, but source review found no PersistentClient() call on these selected test paths. The session did not open a native index. The client factory was not runtime-instrumented, so the no-client conclusion is based on the reviewed call paths and controlled-retrieval fixtures.

The application tests do not directly import or exercise OmegaConf or async-timeout APIs. They establish coexistence of the candidate overlay with these selected application contracts. The separate [Phase 6.14D report](dependency-runtime-confirmation-614d.md) covers selected effdet==0.4.1 configuration operations under OmegaConf 2.4.0.

## Network and filesystem boundaries

The copied 6.14G child-local socket guard was active for both H children. It permits loopback for interpreter internals and blocks unresolved or non-loopback destinations. The pytest session recorded zero allowed-local and zero blocked-external socket events. No external request, model download, Ollama request, or production service call was observed. This Python guard is not an operating-system network sandbox.

The pytest base temporary directory and cache/temp variables were under the new attempt workspace, outside the repository. Tests used their existing temporary persistence paths. No package was installed in rag-env; no Chroma index, user document, or production data was opened.

## Disposition and remaining limits

**Phase 6.14H: A — Bounded candidate application regression passed.** The result applies only to the seven selected tests under the verified two-package overlay.

The project remains pinned to OmegaConf 2.3.0, async-timeout 5.0.1, ANTLR 4.13.2, PyYAML 6.0.3, and Chroma 1.5.0. This session does not prove a clean full-project installation, complete application runtime compatibility, or direct use of both candidate APIs in these tests. It does not qualify model inference/training, native Chroma retrieval, HNSW reliability, index migration/rollback, security, research quality, or publisher integrity.

The historical HNSW retrieval failure and natural artifact-publisher incident remain unresolved. Dependency adoption and Chroma migration are not authorized. **Unattended deployment remains NO-GO.**
