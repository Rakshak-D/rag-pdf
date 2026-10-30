# Local bootstrap HTTP operations

The bootstrap service is a separate, loopback-only application for the first
fresh index build. It can start before any build report exists. It does not
expose experiment execution, ablation execution, index reset, document upload,
retrieval, or automatic index activation. The ordinary experiment runtime
continues to require its configured successful build report.

## Trusted settings and startup

Create the PDF source directory and bootstrap state directory before launch.
The index storage root may be absent, but its parent must exist. All roots must
be absolute, have no symlinked path components, and be pairwise disjoint. The
resolved configuration's `pdf_dir` must resolve to the same directory as the
trusted source setting. HTTP clients cannot select filesystem paths.

| Environment variable | Required | Meaning/default |
|---|---:|---|
| `RAG_WORKBENCH_BOOTSTRAP_API_TOKEN` | Yes | Private bearer token, 32–4096 non-whitespace characters. |
| `RAG_WORKBENCH_BOOTSTRAP_PDF_SOURCE_DIR` | Yes | Existing trusted PDF source directory. |
| `RAG_WORKBENCH_BOOTSTRAP_INDEX_STORAGE_ROOT` | Yes | Owned index root; it can be created later by the established fresh-only builder. |
| `RAG_WORKBENCH_BOOTSTRAP_STATE_DIR` | Yes | Existing, private directory for the manager reservation and SQLite job metadata. |
| `RAG_WORKBENCH_BOOTSTRAP_API_HOST` | No | `127.0.0.1`; only loopback IP addresses are accepted. |
| `RAG_WORKBENCH_BOOTSTRAP_API_PORT` | No | `8010`; range 1–65535. |
| `RAG_WORKBENCH_BOOTSTRAP_MAX_REQUEST_BODY_BYTES` | No | 2 MiB; range 1 byte–16 MiB. |
| `RAG_WORKBENCH_BOOTSTRAP_QUEUE_CAPACITY` | No | 2 pending jobs; range 1–32. There is one worker. |

Launch from the repository root:

```powershell
$env:RAG_WORKBENCH_BOOTSTRAP_API_TOKEN = '<secure local token>'
$env:RAG_WORKBENCH_BOOTSTRAP_PDF_SOURCE_DIR = '<absolute PDF directory>'
$env:RAG_WORKBENCH_BOOTSTRAP_INDEX_STORAGE_ROOT = '<absolute owned index directory>'
$env:RAG_WORKBENCH_BOOTSTRAP_STATE_DIR = '<existing absolute private state directory>'
.\rag-env\Scripts\python.exe -m backend.bootstrap_app
```

The launcher fixes Uvicorn to one worker. Composition and module import do not
open SQLite, Chroma, or models. The ASGI lifespan acquires the manager's
exclusive state-directory reservation before it initializes SQLite. A second
process using the same state directory is rejected. A crashed or ambiguous
reservation is retained; there is no automatic PID-based takeover. Recovery
requires offline operator investigation in isolated storage.

## Preview and explicit submission

Use `GET /health` for unauthenticated liveness only. All `/api/v1/bootstrap/*`
routes require `Authorization: Bearer <token>` and return no-store responses.
Request bodies are size bounded; duplicate JSON keys and non-finite JSON
numbers are rejected.

1. `POST /api/v1/bootstrap/preview` with exactly `{"resolved_config": {...}}`.
   Supply every `ResolvedRunConfig` field, including both
   `workbench_policy` flags. Destructive reset/delete settings are rejected.
2. Record the returned manifest hash and index fingerprint. Preview does not
   reserve the fingerprint, create directories, load a model, extract a PDF, or
   open Chroma. It is a point-in-time observation.
3. `POST /api/v1/bootstrap/jobs` with the complete configuration, the exact
   `expected_manifest_hash` and `expected_index_fingerprint`, and a unique
   `client_request_id` (16–80 ASCII letters, digits, `_` or `-`). The server
   recomputes the manifest and canonical RunPlan before delegating to the
   durable manager. The builder's per-fingerprint WriterLease and atomic
   fresh-only creation remain authoritative at execution time.

HTTP `202` means the job was admitted; it does not mean an index was built.
The same client request ID and same validated identity returns the original
job. Reusing that ID with another identity conflicts. An active job for the
same fingerprint conflicts, and the bounded pending queue returns `429` when
full. The client request ID is only an idempotency key, not an index identity.
After an uncertain network response, inspect `GET /api/v1/bootstrap/jobs`
pages and match the returned `client_request_id`; do not blindly retry. No
automatic retry route exists.

## Inspecting jobs and outcomes

`GET /api/v1/bootstrap/jobs?limit=50&offset=0` lists at most 100 jobs per page.
`GET /api/v1/bootstrap/jobs/{job_id}` returns one job. Responses contain only
bounded operational metadata and relative report references; configurations,
tokens, absolute paths, source contents, and raw tracebacks are not returned.
Statuses are `queued`, `running`, `succeeded`, `failed`, and `interrupted`.
Only job-level progress is claimed.

The manager persists job metadata in SQLite but keeps complete configuration
payloads transiently. On orderly shutdown queued jobs become interrupted. The
running build is allowed to finish; if the worker does not stop within the
shutdown bound, state ownership is retained. It cannot be safely cancelled by
the HTTP service. After a clean manager restart, unfinished metadata is marked
interrupted and is not replayed. A process crash leaves the state reservation
in place and prevents startup takeover until an operator has investigated.

Failed or partial builds preserve their report, incomplete marker, and any
persisted index data for investigation. There is no delete, repair, resume, or
automatic retry operation. A failure to persist final job metadata after an
index was published is an ambiguous job outcome; the index/report evidence is
preserved and no second build is launched automatically.

## After a successful build

A successful job exposes a relative reference such as
`build-artifacts/<build-id>/build-report.json`. Manually configure the ordinary
runtime's existing `RAG_WORKBENCH_INDEX_STORAGE_ROOT` and
`RAG_WORKBENCH_BUILD_REPORT_PATH`, then start `python -m backend.app`. The
bootstrap service never changes runtime configuration and never starts the
experiment runtime. The normal retrieval boundary remains authoritative and
revalidates report identity and persisted collections for each operation.
Neither a successful bootstrap job nor report compatibility guarantees that
future retrieval or model execution will succeed.

Actual builds run the existing indexing engine and may load/download configured
embedding models and consume substantial CPU, GPU, memory, and disk. Automated
HTTP smoke uses temporary synthetic PDFs, a trusted injected test adapter, and
real isolated Chroma persistence; it does not validate production model
downloads or Ollama. The React Index Builds workspace uses a separate
`/bootstrap-api/*` Vite proxy configured with
`RAG_WORKBENCH_BOOTSTRAP_DEV_PROXY_TARGET`; it does not route bootstrap bearer
tokens through the experiment proxy. See the [frontend operations guide](../../frontend/README.md).

The bootstrap Vite smoke exercises loopback HTTP through the real Vite proxy
and bootstrap application for health, authentication, preview, identity
rejection, and read-only job history. It does not submit a build or automate a
browser. The separate bootstrap HTTP smoke exercises the manager-driven real
Chroma build with a trusted synthetic adapter.

The ownership protocol coordinates cooperating repository writers only. It
cannot stop arbitrary external Chroma processes from ignoring the WriterLease.
Keep the storage root under local operator control and stop external writers
before building. The intermittent Chroma HNSW reader error
`Error creating hnsw segment reader: Nothing found on disk` remains unresolved;
this API does not attempt a Chroma lifecycle workaround.
