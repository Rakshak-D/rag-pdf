# Local experiment API runtime

The API remains dependency-injected: [`create_app()`](../../backend/app/main.py) still accepts an already configured `ExperimentService`. The separate [`backend.app.runtime`](../../backend/app/runtime.py) composition root loads local settings, validates paths, creates the artifact store, SQLite catalog and job manager, and assembles the existing service and Starlette app. It does not create a global app at import time.

## Configuration

Set these environment variables before launch. All filesystem paths must be absolute. Required path parents must already exist; the runtime will create only the artifact-store directory itself. It never creates the source-data root, index root, build-report tree, or arbitrary SQLite parent directories.

| Variable | Required | Meaning/default |
|---|---:|---|
| `RAG_WORKBENCH_API_TOKEN` | Yes | Bearer token; no default. Must be non-empty, at most 4096 characters, and contain no whitespace. Keep it in a local secret manager or uncommitted shell environment. |
| `RAG_WORKBENCH_DATA_DIR` | Yes | Existing local PDF/source-data root. It must be separate from runtime stores. Runtime construction does not scan or modify it; the authenticated manifest inspection route hashes the current PDF files when requested and does not extract or copy them. |
| `RAG_WORKBENCH_ARTIFACT_STORE_DIR` | Yes | Content-addressed result artifacts. May be created when its parent exists. |
| `RAG_WORKBENCH_CATALOG_DB_PATH` | Yes | Catalog SQLite file; parent directory must exist. |
| `RAG_WORKBENCH_JOBS_DB_PATH` | Yes | Job metadata SQLite file; parent directory must exist. |
| `RAG_WORKBENCH_INDEX_STORAGE_ROOT` | Yes | Existing root containing the prebuilt index and build artifacts. It is never created, reset, or repaired by this runtime. |
| `RAG_WORKBENCH_BUILD_REPORT_PATH` | Yes | Existing report at `INDEX_STORAGE_ROOT/build-artifacts/<build-id>/build-report.json`. The report’s successful status, schema, fingerprints, and counts are revalidated by retrieval against each submitted plan. |
| `RAG_WORKBENCH_API_HOST` | No | `127.0.0.1`; only loopback IPs or `localhost` are accepted. |
| `RAG_WORKBENCH_API_PORT` | No | `8000`; integer from 1 through 65535. |
| `RAG_WORKBENCH_MAX_REQUEST_BODY_BYTES` | No | `25165824` (24 MiB); must be within the existing `create_app()` limit of 1 through 64 MiB. This also bounds serialized result responses. |
| `RAG_WORKBENCH_WORKER_COUNT` | No | `1`; integer from 1 through 32. |
| `RAG_WORKBENCH_MAX_PENDING_JOBS` | No | `10`; integer from 1 through 1000. |

The data root, artifact store, catalog file, jobs file, and index root must be pairwise disjoint. Symlinked path components are rejected. The build report is intentionally inside the index root and must have the required build-artifact layout. Place runtime state in the ignored `/local-runtime/` directory (for example, separate `artifacts/`, `state/`, and `index/` siblings) and keep source PDFs elsewhere. Never put private credentials, user PDFs, model caches, or generated results in tracked project directories.

The authenticated index readiness operation inspects only this configured
build report and current source manifest. It does not open Chroma or verify
persisted collections. A matching report is a point-in-time compatibility
observation; job execution still validates its own resources. The configured
build report remains required at startup, and this does not add index discovery.
For an isolated real-server check using synthetic report metadata, see
[local integration smoke](local-integration-smoke.md).

Example PowerShell configuration (replace paths with existing local locations; do not put a real token in a script committed to source control):

```powershell
$env:RAG_WORKBENCH_API_TOKEN = '<locally supplied secret>'
$env:RAG_WORKBENCH_DATA_DIR = 'C:\LocalData\rag-pdfs'
$env:RAG_WORKBENCH_ARTIFACT_STORE_DIR = 'C:\RAKSHAK\MY CODE\AI\rag-pdf\local-runtime\artifacts'
$env:RAG_WORKBENCH_CATALOG_DB_PATH = 'C:\RAKSHAK\MY CODE\AI\rag-pdf\local-runtime\state\catalog.sqlite3'
$env:RAG_WORKBENCH_JOBS_DB_PATH = 'C:\RAKSHAK\MY CODE\AI\rag-pdf\local-runtime\state\jobs.sqlite3'
$env:RAG_WORKBENCH_INDEX_STORAGE_ROOT = 'C:\LocalData\rag-index'
$env:RAG_WORKBENCH_BUILD_REPORT_PATH = 'C:\LocalData\rag-index\build-artifacts\<build-id>\build-report.json'
```

Create `local-runtime` and its `state` parent yourself before launch. The runtime does not make arbitrary storage or database parents.

## Prepare and launch

Build the index and its successful build report using the [first-run bootstrap](index-bootstrap.md) or the existing indexing workflow before submitting evaluations. This launcher does not accept document uploads or build indexes. A configured path is not proof that the report is valid or that a plan-matching index exists: the existing retrieval boundary checks the report, manifest, index fingerprint, persisted collection counts, and index contents. If these checks fail, execution returns the established failure contract; no synthetic result is created.

Start from the repository root with its pinned environment:

```powershell
.\rag-env\Scripts\python.exe -m backend.app
```

The command loads settings, composes the dependencies once, then runs the Starlette app with the pinned Uvicorn installation. `/health` is liveness only. It does not establish that the index, build report, model adapters, Ollama, or catalog are ready. Model status is read-only.

The model registry and `LocalGenerationAdapter` are constructed lazily. Runtime composition and app startup do not download or load embedding/reranker weights, call Ollama, or run inference. The job manager starts and stops through the existing Starlette lifespan. Jobs are process-local; do not start multiple runtime processes against one jobs database. Logs and errors use stable codes, and Uvicorn access logging is disabled by the launcher. Do not enable request-body or secret logging in local wrappers.

This is a local development boundary, not an internet-facing deployment. There is no document upload or index-build API, no distributed job queue, no model lifecycle service, and no production identity provider. Keep the bearer token private and maintain operating-system/network controls even though the default bind is loopback.

The React/TypeScript operational overview and frontend development workflow are documented in [`frontend/README.md`](../../frontend/README.md). The frontend uses the same-origin API paths and Vite's local development proxy; it does not add backend routes or runtime capabilities.

For a separate first-run service that can build an index before a report exists, see [bootstrap HTTP operations](bootstrap-http-operations.md). It uses a distinct loopback-only launcher and state directory; it does not relax this runtime's required build-report startup contract.
