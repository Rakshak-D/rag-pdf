# Product pull request CI

`.github/workflows/product-pr-ci.yml` runs on every pull request targeting
`feat/product-rebuild-06-user-first-simplification` and on pushes to that
branch. It has no path filters, uses read-only repository permissions, and does
not require Ollama, models, a GPU, credentials, or deployment services.

The Ubuntu jobs use Python 3.10.22 and Node.js 22.23.4. Frontend dependencies
are installed from the committed lockfile with `npm ci`. The backend installs
the pinned lightweight set in `scripts/requirements-browser-ci.txt` and the
exact Ruff pin in `scripts/requirements-product-ci.txt`.

The required backend pytest selection is fixed at these 13 modules:

- `tests/unit/test_api_schemas.py`
- `tests/unit/test_local_gateway.py`
- `tests/unit/test_bm25.py`
- `tests/unit/test_context_contracts.py`
- `tests/unit/test_fusion_engine.py`
- `tests/unit/test_index_inventory.py`
- `tests/unit/test_index_writer_lease.py`
- `tests/unit/test_ingestion_chunking.py`
- `tests/unit/test_manifest.py`
- `tests/unit/test_model_lifecycle.py`
- `tests/unit/test_paths_and_fingerprint.py`
- `tests/unit/test_retrieval_contracts.py`
- `tests/unit/test_run_plans.py`

This deterministic baseline covers API and local-gateway contracts, lexical
retrieval, fusion, ingestion/chunking, source and index contracts, manifests,
fingerprints, leases, run plans, and resource/model lifecycle decisions. Its
fixtures use synthetic inputs, temporary directories, or fakes; it does not
load models or contact Ollama or external APIs.

The remaining 51 unit modules and all 30 integration modules are outside this
initial required gate. They include broader application/pipeline integration,
research and evaluation scenarios, model/embedding/reranking implementations,
image and database workflows, process and platform-specific behavior, and
other specialized contracts. This exclusion is a scope statement, not a claim
that those tests fail or are unnecessary. They remain available for their
appropriate dedicated environments and workflows.

Ruff checks `backend/` and `scripts/` with the Python 3.10 target and the
`E4`, `E7`, `E9`, and `F` rule families for import, syntax, statement, and
common name/undefined-name errors. Frontend TypeScript checking is the frontend
static-analysis gate; this milestone does not add a separate lint framework.

## Ruff baseline debt

The unchanged product base `c1cba315e8df4130f281fd95ba5794663eb30c9f`, scanned
with Ruff 0.17.5 and the full CI scope, contains 177 pre-existing findings in
30 files:

| Rule | Findings |
| --- | ---: |
| E402 | 26 |
| E701 | 26 |
| E702 | 94 |
| E731 | 3 |
| F401 | 19 |
| F841 | 9 |
| **Total** | **177** |

The auditable per-finding inventory is [`scripts/ruff-baseline.json`](../../scripts/ruff-baseline.json).
It records each repository-relative path, rule, diagnostic message, source
context fingerprint, occurrence count, and original location, along with the
source commit and exact Ruff configuration. The baseline was generated from an
archive of that unchanged commit, not from the CI branch.

The CI lint step runs focused comparator tests and
[`scripts/check_ruff_baseline.py`](../../scripts/check_ruff_baseline.py). The
comparator executes the pinned Ruff version over all of `backend/` and
`scripts/`, then compares a multiset keyed by path, rule, message, and a SHA-256
fingerprint of normalized source context. Line numbers are retained for review
but are not used as identity. A new finding or an increased occurrence count
fails CI. A removed or changed finding is reported and also requires a reviewed
baseline reconciliation; it cannot silently replace another old finding.

A future lint cleanup should fix source findings in its separately authorized
code change, regenerate the baseline from the reviewed parent commit with the
pinned Ruff version, inspect the exact reduction, and update the provenance and
inventory in the same review. Do not manually edit diagnostic records or
weaken the configured scope/rules.

A green lint check means **no new Ruff findings relative to the recorded
baseline**; the 177 existing findings remain visible debt until they are
intentionally fixed and reconciled.
The initial CI suite also excludes the remaining 51 unit modules and 30
integration modules described above; these exclusions are not covered by the
green check. Authenticated browser end-to-end scenarios and the M9-A proof
checker that depends on a Windows-specific Playwright path also remain outside
this initial gate.

To reproduce the backend checks from the repository root in an isolated Python
environment, install both requirement files and run the pytest module list
above followed by the comparator tests and baseline check:

```sh
python -m unittest discover -s scripts/tests -p test_ruff_baseline.py -v
python scripts/check_ruff_baseline.py
```

From `frontend/`, reproduce the frontend checks with:

```sh
npm ci --ignore-scripts --no-audit --no-fund
npm test -- --reporter=dot
npm run typecheck
npm run build
```
