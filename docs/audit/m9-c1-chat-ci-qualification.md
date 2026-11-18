# M9-C1 deterministic chat CI qualification

## Scope and baseline

This report qualifies three existing test modules for the product pull-request
CI job. The change expands pytest selection only; it adds no application
behavior and does not claim that a fake model client proves genuine model
generation.

- Product base: `0d4c7122e421f340b56ece610b6512accada809d`
- CI job: `Backend tests and Python static analysis`
- Before: 13 selected backend pytest modules
- After: the same 13 modules plus the three qualified modules below
- Python: 3.10.22
- Pinned CI runtime/test requirements: `scripts/requirements-browser-ci.txt`
- Pinned Ruff requirement: `scripts/requirements-product-ci.txt` (Ruff 0.17.5)
- Existing Ruff baseline: 177 findings; baseline data and comparator are unchanged

The qualification environment was a disposable Python 3.10.22 container with
the repository mounted read-only. The pinned requirements were installed
before qualification, then the container was disconnected from its network.
The three candidate modules did not attempt external requests. Initial
full-selection runs without a tokenizer cache attempted the blocked `tiktoken`
request described below. After warming the tokenizer cache as setup, the full
selection passed with networking disabled. No Ollama process, model, or model
download was used. No index build or existing user storage was accessed. API
tests used pytest temporary directories for source, state, and storage.

## Candidate results

| Module | Cases | Qualification result | Dependencies and boundary |
|---|---:|---|---|
| `tests/unit/test_general_chat.py` | 22 | 22 passed; 0 failed; 0 skipped; 6.50 s; no warning | Uses a fake Ollama client. Exercises model discovery/availability, request-scoped settings, server-owned prompt and history validation, missing/unavailable model, timeout, concurrency, safe errors, and lazy model construction. No real client request is made. |
| `tests/unit/test_generation_contracts.py` | 2 | 2 passed; 0 failed; 0 skipped; 2.87 s; no warning | Uses deterministic fake generation, tokenizer, time, and logging behavior. Its source loader reads the existing `notebooks/rag_pipeline.py` to isolate the generation method; it does not edit that protected research file. Tests distinguish failure results and verify bounded retry behavior. |
| `tests/integration/test_general_chat_api.py` | 3 | 3 passed; 0 failed; 0 skipped; 7.98 s; 1 known deprecation warning | Uses Starlette `TestClient`, the existing pinned `httpx`/Starlette dependencies, an injected fake Ollama client, and temporary runtime directories. Covers authentication, no retrieval call, request validation, stable unavailable/missing-model errors, response sanitization, and configured-model identity. The warning is Starlette's use of the deprecated `anyio.abc.BlockingPortal` alias. |

All three modules were then run together five consecutive times in the same
network-disconnected environment. Each run passed all 27 cases, with no skips
or failures. Durations were 9.92 s, 11.32 s, 11.39 s, 11.21 s, and 11.79 s.
Each combined run reported the same Starlette/AnyIO deprecation warning from the
API test module. No intermittent concurrency failure was observed.

The test dependencies are available from the existing pinned CI requirement
files; no new dependency was added. These tests do not establish that Ollama is
installed, reachable, correctly configured, or able to generate a genuine
answer. They establish deterministic service and API contracts around injected
fake clients and generation failures.

## Full backend selection verification

The original 13-module selection and the expanded 16-module selection were
also run in the same network-disconnected container. The original selection
reported 161 passed, 2 failed, and 2 skipped; the expanded selection reported
188 passed, 2 failed, and 2 skipped. In both runs, the same two existing cases
in `tests/unit/test_ingestion_chunking.py` failed because `tiktoken` attempted
to retrieve `cl100k_base.tiktoken` from
`openaipublic.blob.core.windows.net` when no local encoding cache was present.
The request was blocked by the disconnected environment. These failures
reproduce in the unchanged baseline selection and are not caused by the three
new modules. The expanded run also reported the Starlette/AnyIO deprecation
warning noted above.

The three candidate modules passed individually and in all five combined
qualification runs without attempting network access. However, the full
backend selection initially attempted an external test-time request when the
tokenizer cache was empty. To keep the test execution offline, the backend CI
job now warms the `cl100k_base` cache in its setup step, before pytest, under
`runner.temp`. The pinned `tiktoken==0.12.0` package checks the asset against
its embedded SHA-256 (`223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7`).
The cache is job-local and ephemeral. Dependency installation and this
integrity-checked tokenizer setup may use network; the pytest runs themselves
are validated below with networking disabled. The initial cache-free run made
no successful external connection; the deliberate setup fetch later retrieved
the asset and verified its SHA-256.

## CI selection change

The workflow retains its original 13 modules:

1. `tests/unit/test_api_schemas.py`
2. `tests/unit/test_local_gateway.py`
3. `tests/unit/test_bm25.py`
4. `tests/unit/test_context_contracts.py`
5. `tests/unit/test_fusion_engine.py`
6. `tests/unit/test_index_inventory.py`
7. `tests/unit/test_index_writer_lease.py`
8. `tests/unit/test_ingestion_chunking.py`
9. `tests/unit/test_manifest.py`
10. `tests/unit/test_model_lifecycle.py`
11. `tests/unit/test_paths_and_fingerprint.py`
12. `tests/unit/test_retrieval_contracts.py`
13. `tests/unit/test_run_plans.py`

It adds:

14. `tests/unit/test_general_chat.py`
15. `tests/unit/test_generation_contracts.py`
16. `tests/integration/test_general_chat_api.py`

The intended pytest selection is therefore 16 modules. The Ruff comparator
command, its recorded 177-finding baseline, Python version, dependency pins,
and the other three CI jobs are unchanged. The backend job adds only the
job-local tokenizer cache setup needed to prevent a test-time fetch by two
existing ingestion tests. After warming the cache and disconnecting the
container from networking, the complete expanded selection passed: 190 passed,
2 skipped, 0 failed in 39.22 seconds, with the single Starlette/AnyIO
deprecation warning described above. The final offline test run made no network
requests.

## Static validation

- `scripts/tests/test_ruff_baseline.py`: 10 passed.
- `scripts/check_ruff_baseline.py`: passed with baseline source
  `c1cba315e8df4130f281fd95ba5794663eb30c9f`, Ruff 0.17.5, 177 current
  findings, 0 new findings, and 0 removed findings. Its cache was directed to a
  temporary container path because the source checkout was mounted read-only.
- The workflow parsed successfully with the already pinned PyYAML dependency;
  `actionlint` was not installed locally.
- `git diff --check` passed.

## PR #11 documentation coordination

PR #11 remains a separate open draft and changes
`docs/operations/product-pr-ci.md`. This change does not edit that file. The
operational CI documentation still describes the earlier 13-module backend
selection until the documentation change can be reconciled after PR #11 is
integrated. Keep this PR as a draft until that synchronization is completed;
the actual workflow selection and this report are the current evidence for the
expanded list.
