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

On the unmodified `c1cba315e8df4130f281fd95ba5794663eb30c9f` baseline,
Ruff 0.17.5 reports 177 findings in 30 files: E402 (26), E701 (26), E702
(94), E731 (3), F401 (19), and F841 (9). The required lint step is left active
and unsuppressed, so this baseline currently fails that job. Resolving those
source findings requires a separately authorized code change; this CI-only
change does not alter application or support scripts to obtain a green result.

To reproduce the backend checks from the repository root in an isolated Python
environment, install both requirement files and run the pytest module list
above followed by:

```sh
python -m ruff check --target-version py310 --select E4,E7,E9,F backend scripts
```

From `frontend/`, reproduce the frontend checks with:

```sh
npm ci --ignore-scripts --no-audit --no-fund
npm test -- --reporter=dot
npm run typecheck
npm run build
```
