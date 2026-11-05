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
| `RAG_WORKBENCH_BUILD_REPORT_PATH` | No | Optional legacy report at `INDEX_STORAGE_ROOT/build-artifacts/<build-id>/build-report.json`. When omitted, the runtime starts without an index selection; evaluations and retrieval requiring an index return `index_not_selected` until an explicit selection is made. |
| `RAG_WORKBENCH_API_HOST` | No | `127.0.0.1`; only loopback IPs or `localhost` are accepted. |
| `RAG_WORKBENCH_API_PORT` | No | `8000`; integer from 1 through 65535. |
| `RAG_WORKBENCH_MAX_REQUEST_BODY_BYTES` | No | `25165824` (24 MiB); must be within the existing `create_app()` limit of 1 through 64 MiB. This also bounds serialized result responses. |
| `RAG_WORKBENCH_WORKER_COUNT` | No | `1`; integer from 1 through 32. |
| `RAG_WORKBENCH_MAX_PENDING_JOBS` | No | `10`; integer from 1 through 1000. |

The data root, artifact store, catalog file, jobs file, and index root must be pairwise disjoint. Symlinked path components are rejected. The build report is intentionally inside the index root and must have the required build-artifact layout. Place runtime state in the ignored `/local-runtime/` directory (for example, separate `artifacts/`, `state/`, and `index/` siblings) and keep source PDFs elsewhere. Never put private credentials, user PDFs, model caches, or generated results in tracked project directories.

The authenticated index readiness operation inspects the current explicit
selection (or the optional legacy report) against the submitted plan and
source manifest. It does not open Chroma or verify persisted collections. A
matching report is a point-in-time compatibility observation; execution still
validates its own resources. For an isolated real-server check using synthetic
report metadata, see [local integration smoke](local-integration-smoke.md).

Example PowerShell configuration (replace paths with existing local locations; do not put a real token in a script committed to source control):

```powershell
$env:RAG_WORKBENCH_API_TOKEN = '<locally supplied secret>'
$env:RAG_WORKBENCH_DATA_DIR = 'C:\LocalData\rag-pdfs'
$env:RAG_WORKBENCH_ARTIFACT_STORE_DIR = 'C:\RAKSHAK\MY CODE\AI\rag-pdf\local-runtime\artifacts'
$env:RAG_WORKBENCH_CATALOG_DB_PATH = 'C:\RAKSHAK\MY CODE\AI\rag-pdf\local-runtime\state\catalog.sqlite3'
$env:RAG_WORKBENCH_JOBS_DB_PATH = 'C:\RAKSHAK\MY CODE\AI\rag-pdf\local-runtime\state\jobs.sqlite3'
$env:RAG_WORKBENCH_INDEX_STORAGE_ROOT = 'C:\LocalData\rag-index'
# Optional compatibility setting for an existing manually configured runtime.
$env:RAG_WORKBENCH_BUILD_REPORT_PATH = 'C:\LocalData\rag-index\build-artifacts\<build-id>\build-report.json'
```

Create `local-runtime` and its `state` parent yourself before launch. The runtime does not make arbitrary storage or database parents.

## Prepare and launch

Build the index and its successful build report using the [first-run bootstrap](index-bootstrap.md) or the existing indexing workflow. The ordinary runtime can start before selection so health, source-manifest inspection, artifacts, and job metadata remain available. An index is never chosen automatically.

With a running bootstrap service, open **Index Builds**, enter the complete resolved configuration, preview it, and inspect compatible reports. Connect separately to the ordinary runtime and choose one compatible report. The authenticated ordinary API exposes `GET /api/v1/index/selection` and `POST /api/v1/index/selection`. The server recomputes the source manifest and RunPlan, derives the report path from the validated build ID under its trusted index root, checks the report and records its SHA-256, then atomically stores a compare-and-swap selection record at `JOBS_DB_PATH`'s parent as `active-index-selection.json`.

The selection record persists across a clean runtime restart. Its report reference, manifest, fingerprint, configuration/RunPlan identities, storage-root identity and revision are revalidated on read and before execution. A source or report change marks it stale; no other candidate is selected automatically. Preview the current source/configuration and explicitly select a currently compatible report to replace a stale selection. A malformed or unsafe state record is shown as invalid and blocks selection writes; preserve the record and use an operator-reviewed offline recovery, rather than deleting index data or allowing silent replacement.

Each accepted evaluation or ablation pins the validated report identity while job admission is synchronized with selection updates. Later selection changes cannot redirect queued work. The execution path checks the pinned report and current source before retrieval. Legacy environments may continue to set `RAG_WORKBENCH_BUILD_REPORT_PATH`; that path is used only when no durable selection record exists.

Selection is report-level only (`check_level: "report"`, `store_checked: false`). It does not open Chroma, verify collections, load models, warm Ollama, or establish query readiness. The retrieval boundary remains responsible for persisted collection and report checks. If these checks fail, execution returns the established failure contract; no synthetic result is created.

## Ask with selected evidence

The authenticated ordinary runtime exposes `POST /api/v1/ask` with a strict
JSON body such as `{"question":"What does the report conclude?"}`. The
question is limited to 4,000 characters and 512 configured tokenizer tokens;
the existing `RAG_WORKBENCH_MAX_REQUEST_BODY_BYTES` limit also applies. Ask
requires a valid durable selection. A legacy report environment variable by
itself is not enough to construct a user-facing Ask plan: select a compatible
report through Index Builds first. No index is selected automatically.

The server captures the selected RunPlan and report identity, revalidates the
source manifest and report, and passes that pinned report to the existing
retrieval engine. It then uses `ContextFormatter` for the configured context
budget and the existing `LocalGenerationAdapter` for Ollama. Retrieval and
generation run in the Starlette thread pool, with two simultaneous questions
per runtime; additional requests receive `ask_capacity_exhausted`. Changing
the active selection during a question does not redirect that request. A
browser disconnect does not guarantee that an already-running Ollama request
stops: the current Ollama client has no configured request deadline, and Ask
does not claim cancellable inference.

The response contains bounded text and image evidence tied to manifest
document IDs, safe relative source paths, available page numbers, and the
context labels supplied to generation (`T1`, `I1`, and so on). Returned
citation links target only those supplied labels. A valid label shows which
evidence was provided; it does not prove the answer is factually supported.
No usable selected evidence returns `outcome: "insufficient_evidence"` and
does not call Ollama. Other failures use stable error codes; Ask creates no
benchmark, evaluation artifact, or job.

When generated text begins with an explicit configured-evidence abstention
phrase, the response marks `model_reported_insufficient_evidence`; this is a
bounded presentation hint based on the model's wording, not an independent
semantic check or a guarantee that the model abstained correctly.

Images are fetched separately from authenticated
`GET /api/v1/ask/images/{reference}` requests. References are random,
process-local, bounded to 128 entries, expire after five minutes, and are
revalidated beneath the configured image root before each read. Only bounded
PNG, JPEG, and WebP files are returned with `Cache-Control: no-store`; the
reference is not a filesystem path. References expire on runtime restart and
Ask history is not persisted. A selected report still does not prove Chroma
readiness, and deterministic adapter tests do not establish real Chroma,
embedding-model, or Ollama behavior.

Start from the repository root with its pinned environment:

```powershell
.\rag-env\Scripts\python.exe -m backend.app
```

The command loads settings, composes the dependencies once, then runs the Starlette app with the pinned Uvicorn installation. `/health` is liveness only. It does not establish that the index, build report, model adapters, Ollama, or catalog are ready. Model status is read-only.

The model registry and `LocalGenerationAdapter` are constructed lazily. Runtime composition and app startup do not download or load embedding/reranker weights, call Ollama, or run inference. The job manager starts and stops through the existing Starlette lifespan. Jobs are process-local; do not start multiple runtime processes against one jobs database. Logs and errors use stable codes, and Uvicorn access logging is disabled by the launcher. Do not enable request-body or secret logging in local wrappers.

This experiment runtime is a local development boundary, not an internet-facing deployment. It exposes no document-upload or index-build routes, no distributed job queue, no model lifecycle service, and no production identity provider. First-run index construction uses the separate bootstrap service below. Keep bearer tokens private and maintain operating-system/network controls even though the default bind is loopback.

The React/TypeScript operational overview and frontend development workflow are documented in [`frontend/README.md`](../../frontend/README.md). The frontend uses same-origin paths. Vite keeps `/health` and `/api/*` routed to this experiment runtime, while `/bootstrap-api/*` has a separate loopback target for the bootstrap-only service. The frontend does not add backend routes or runtime capabilities.

For a separate first-run service that can build an index before a report exists, see [bootstrap HTTP operations](bootstrap-http-operations.md). It uses a distinct loopback-only launcher and state directory; its authentication token is independent from the ordinary runtime token.
