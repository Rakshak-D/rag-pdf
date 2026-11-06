# Local bootstrap HTTP operations

The bootstrap service is a separate, loopback-only application for the first
fresh index build. It can start before any build report exists. It supports
bounded PDF import and source inventory, but does not expose experiment
execution, ablation execution, index reset, retrieval, deletion, or automatic
index activation. The ordinary experiment runtime continues to require its
configured successful build report.

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
| `RAG_WORKBENCH_BOOTSTRAP_MODEL_LIFECYCLE_POLICY` | No | `cached` (default) or `transient_serialized`; applies scoped admission/release to internally created build adapters. |

Launch from the repository root:

```powershell
$env:RAG_WORKBENCH_BOOTSTRAP_API_TOKEN = '<secure local token>'
$env:RAG_WORKBENCH_BOOTSTRAP_PDF_SOURCE_DIR = '<absolute PDF directory>'
$env:RAG_WORKBENCH_BOOTSTRAP_INDEX_STORAGE_ROOT = '<absolute owned index directory>'
$env:RAG_WORKBENCH_BOOTSTRAP_STATE_DIR = '<existing absolute private state directory>'
.\rag-env\Scripts\python.exe -m backend.bootstrap_app
```

## Importing and listing source documents

Both document routes require the bootstrap bearer token. The service uses
only its configured `source_root`; a request cannot select a destination.

- `GET /api/v1/bootstrap/documents?limit=50&offset=0` returns a page of the
  current `build_document_manifest()` entries, the corpus manifest hash, an
  exact total, and the effective `max_upload_bytes`. Pages contain at most
  100 records. An empty `documents` array with `total: 0` is a verified empty
  source. An unreadable root or invalid manifest returns a bounded error.
- `POST /api/v1/bootstrap/documents` accepts raw bytes with
  `Content-Type: application/pdf` and an `X-Document-Filename` header. The
  request body is streamed and limited by
  `RAG_WORKBENCH_BOOTSTRAP_MAX_REQUEST_BODY_BYTES` (default 2 MiB, maximum
  16 MiB). PDFs must have a valid signature, be readable and unencrypted, and
  contain 1–200 pages. PyMuPDF validates structure without extracting text or
  loading embedding, reranking, generation, or Chroma components.

Filenames use a conservative ASCII policy: 1–120 characters, begin with a
letter or digit, contain only letters, digits, spaces, parentheses, periods,
underscores or hyphens, and end in lowercase `.pdf`. Path separators, drive
prefixes, control characters and Windows device names are rejected. Existing
names and case-insensitive stems conflict; files are never overwritten.

The service writes to a random `.part` file inside the trusted source root,
streams and hashes the bytes, flushes and fsyncs the file, validates the PDF,
then publishes with an atomic no-overwrite hard link. The document ID and
corpus hash come from the existing manifest identity contract. Imports are
serialized with job admission and return a conflict while a build is queued
or running; a build submission racing an import must revalidate its manifest.
An `imported` response means the file was found in the recomputed manifest.
`committed_inventory_unavailable` means publication occurred but that final
confirmation failed; inspect the inventory before attempting another upload.
Pre-publication failures return `outcome: rejected` and a bounded error code.

Imported documents are source files available for a later explicit Index
Builds preview. Import does not index, activate, or make a document queryable.
There is no document delete, overwrite, activation, or automatic reindex route.

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

The default adapter policy is cached. For constrained machines, start with
`RAG_WORKBENCH_BOOTSTRAP_MODEL_LIFECYCLE_POLICY=transient_serialized`. The
builder serializes internally created model operations and releases text
weights before loading image weights. Admission checks measured RAM, system
commit headroom, and applicable CUDA free memory. The gate is process-local and
point-in-time, not a reservation against a separate ordinary-runtime process;
avoid concurrent model-backed work when host headroom is tight. Reserve floors
are generic safety margins, not model-size guarantees; unknown measurements or
low capacity fail the build with a sanitized stable lifecycle code. Fresh-only ownership,
report validation, failure-marker preservation, and lease semantics remain
unchanged. A custom adapter factory is caller-owned and rejected under the
transient policy.

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

## Read-only build-report inventory

`POST /api/v1/bootstrap/index-inventory` accepts a complete
`resolved_config`, the observed `expected_manifest_hash`, and optional
`limit`/`offset` values. It uses the bootstrap service's trusted source and
storage roots; clients cannot choose a report path or filesystem directory.
The server validates the configuration and non-destructive policy, recomputes
the current source manifest and canonical RunPlan, then delegates to the
bounded inventory service.

The default scan examines at most 512 direct entries under
`<storage-root>/build-artifacts/`, reads reports up to 1 MiB, and returns at
most 100 candidates per page. Requests may use `limit` 1–100 and `offset`
0–512. Ordering is lexical by build-artifact directory name, not by creation
time. If the entry cap is exceeded, the response sets `complete: false`,
leaves `total_candidates` null, and returns no partial candidate list. Pages
are point-in-time observations; builds created between page requests can
change later pages.

Candidate compatibility values are `report_match`, `mismatch`, `failed`,
`incomplete`, `invalid`, and `unavailable`. Counts are returned only for a
validated matching report. Every response identifies `check_level: "report"`
and `store_checked: false`. A matching report establishes report identity and
successful-build metadata only; it does not establish that Chroma collections
currently exist, are readable, or will support retrieval. The endpoint does
not open Chroma, acquire a WriterLease, submit a job, or activate a report.
Malformed candidate reports are classified within the inventory when safely
possible. Invalid roots, changed source manifests, or a scan that cannot be
bounded fail closed. See the [index inventory contract](index-inventory.md)
for the complete classification and read-only boundary.

The React Index Builds workspace exposes this operation in its **Build report
inventory** section. Connect to the bootstrap service, enter a complete
resolved configuration, and preview it first. The viewer submits the preview's
server-computed manifest identity with the configuration; the server
recomputes the manifest and RunPlan. Inventory does not require
`fresh_build_eligible`, so operators can inspect reports when an index already
exists. Configuration edits, connection changes, a new preview, or a changed
manifest clear the displayed inventory. Each scan and page is explicitly
requested; there is no automatic scan loop.

Complete empty results are distinct from incomplete scans. In particular,
`inventory_limit_exceeded` means the inspection cap was reached and does not
mean the storage contains no reports. Pagination is bounded to 100 candidates
and offset 512, uses lexical order, and does not create a stable snapshot across
concurrent publication. Candidate references are rendered as relative text;
the UI does not turn them into filesystem links or use them to configure the
ordinary runtime. The inventory viewer has no selection, activation, repair,
deletion, retry, or experiment controls.

## After a successful build

A successful job exposes a relative reference such as
`build-artifacts/<build-id>/build-report.json`. Start the ordinary runtime with
its trusted `RAG_WORKBENCH_INDEX_STORAGE_ROOT`; a report path is no longer
required for startup. In **Index Builds**, preview the same source/configuration,
inspect report candidates, connect separately to the ordinary runtime, and
explicitly select a compatible build. The ordinary runtime persists that
selection and revalidates its source and report identity after restart. Existing
deployments may retain `RAG_WORKBENCH_BUILD_REPORT_PATH` as a legacy fallback
when no selection record exists. The bootstrap service never writes the
ordinary selection or starts the experiment runtime.

Report inventory and selection do not open Chroma or verify collections. The
normal retrieval boundary remains authoritative and validates persisted
collections for each accepted execution. Neither a successful bootstrap job
nor report compatibility guarantees that future retrieval or model execution
will succeed. See [local runtime](local-runtime.md) for selection persistence,
stale-state handling, authentication, and job pinning.

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
