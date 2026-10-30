# RAG Evaluation Workbench frontend

This is the local React/TypeScript interface for the separate experiment and bootstrap Starlette services. It presents backend liveness, authenticated model-adapter state, an overview, jobs and artifacts, experiment workflows, and a fresh-only Index Builds workspace. Job and artifact catalogs have server-backed filters and paging; detail pages show stored metadata, metric aggregates, ablation comparison states, and explicitly requested result JSON.

## Requirements and local run

- Node.js 20.19+ or 22.12+ (the project was scaffolded and verified with Node 24.14.0).
- npm (verified with npm 11.12.1).
- The backend's configured local runtime and Python environment.

From the repository root, start the API in one terminal:

```powershell
python -m backend.app
```

Then from `frontend/`:

```powershell
npm ci
npm run dev
```

Vite proxies `/health` and `/api` to `http://127.0.0.1:8000` by default. To use another local backend, set the non-secret `RAG_WORKBENCH_DEV_PROXY_TARGET` variable before starting Vite. This changes only the development proxy destination; it is not an API token.

After `npm ci`, the isolated real-server proxy check can be run from the
repository root with `.\rag-env\Scripts\python.exe scripts/local_runtime_smoke.py --vite`.
See [the smoke-test guide](../docs/architecture/local-integration-smoke.md).
It checks HTTP proxying without driving a browser or loading models.

For a static production build, configure the chosen static host to serve `index.html` for the client routes (`/jobs`, `/jobs/*`, `/artifacts`, `/artifacts/*`, `/experiments/*`, and `/index-builds/*`). The Python runtime does not serve or mount the frontend assets.

## Experiments

The Experiments area submits through the existing authenticated job endpoints. Add benchmark cases using your own evaluation queries, references, and expected page identifiers; the form starts blank and does not invent benchmark content.

Connect, then select **Inspect documents** to read the configured PDF source manifest. Supply a complete resolved configuration JSON object from the trusted project configuration workflow. For an evaluation, select **Prepare RunPlan** and inspect the returned fingerprints before explicitly submitting the benchmark job. The backend validates the configuration, current manifest, safety policy, and fingerprints; the browser does not create them. Changing the configuration invalidates the prepared plan. If source documents change, inspect them again and prepare another plan.

For an ablation, the inspected manifest identity and supplied base configuration are passed to the existing job endpoint, which creates the canonical six variants. Manifest inspection and plan preparation do not build an index or prove that a compatible index exists. Index discovery remains unavailable. First-run building is available only through the separate Index Builds workspace described below.

Use **Check configured index** to compare the configured build report against
the inspected manifest and configuration. A `report_match` confirms report
compatibility only; vector collections and their counts are not independently
checked. `mismatch`, `unavailable`, and `indeterminate` describe a report
that differs, cannot be accessed, or cannot be validated. Execution repeats its
own checks, and this point-in-time observation does not guarantee success.

Submission uses a single POST. If the connection fails after sending, the UI explains that acceptance is uncertain and does not retry automatically; check Jobs before submitting again. Form payloads and tokens are transient in memory and are cleared on disconnect or when leaving the workflow.

## Authentication

Enter the local API bearer token in the connection panel. The browser keeps it in React memory only, sends it only to protected `/api/v1/*` requests, and clears it on disconnect or reload. It is not saved to browser storage, URLs, cookies, or a frontend environment file. Configure the backend token through the backend's secure local runtime configuration; never commit it.

## Checks

```powershell
npm test
npm run typecheck
npm run build
```

The test suite mocks HTTP responses and does not require the Python backend, models, Ollama, or a corpus. `npm run build` creates ignored output in `dist/`.

## Current boundary

The API's `/health` route is liveness only. Runtime model state reports registered adapter state and does not prove index or Ollama readiness. Ablation result loading requires the original compatible base configuration; the UI does not invent or rewrite it. Artifact records have no creation timestamp, so the catalog view does not claim to identify the newest result. The UI does not provide document upload or a query playground, and it does not display fabricated jobs or artifacts.

## First-run Index Builds workspace

Index Builds uses the separate bootstrap-only backend started with
`python -m backend.bootstrap_app`. It does not depend on the ordinary
experiment runtime or its required build report. Configure the bootstrap
service independently with trusted source, index-storage, state-directory,
and bearer-token settings; see the
[bootstrap operations guide](../docs/architecture/bootstrap-http-operations.md).

For local development, configure both Vite targets before starting Vite:

```powershell
$env:RAG_WORKBENCH_DEV_PROXY_TARGET = 'http://127.0.0.1:8000'
$env:RAG_WORKBENCH_BOOTSTRAP_DEV_PROXY_TARGET = 'http://127.0.0.1:8010'
npm run dev
```

The existing `/health` and `/api/*` routes continue to reach the experiment
runtime. Bootstrap requests use the separate `/bootstrap-api/*` prefix, which
Vite rewrites to the bootstrap service's `/api/*` routes. The target accepts
only a loopback HTTP origin and cannot be selected by browser input. If it is
unset, Vite returns `bootstrap_proxy_unconfigured`; bootstrap traffic never
falls back to the experiment service. A real HTTP proxy check is available
from the repository root with
`.\rag-env\Scripts\python.exe scripts\bootstrap_vite_smoke.py`.

Enter the bootstrap token in the dedicated connection panel on Index Builds.
This token is independent from the experiment token; neither is reused
automatically. Both remain in memory only. Paste a complete resolved
configuration object. The editor checks JSON, duplicate keys, required fields,
and destructive settings, but inserts no defaults and calculates no
fingerprints. The backend remains authoritative for configuration validation.

**Preview index build** displays the server's current document count, manifest
hash, and index/run fingerprints. Preview is a point-in-time observation and
does not reserve an index. Review the exact identities and check the explicit
acknowledgment before **Start fresh build** becomes available. Existing
fingerprint-owned directories block submission; this interface cannot repair
or overwrite them.

Each explicit submission uses one cryptographically random client request ID
and one POST. The UI does not retry. If the response is uncertain, it retains
the request ID in memory and offers read-only job-history search. After a
reload, reconnect and inspect the paged server history. No token or
configuration draft is persisted. Job history supports manual refresh and
filtering by request ID, job ID, or status. It shows only actual `queued`,
`running`, `succeeded`, `failed`, and `interrupted` states; it invents no
percentages or stage progress.

### Read-only build report inventory

On the Index Builds page, first connect to bootstrap mode, enter the complete
resolved configuration, and select **Preview index build**. The **Build report
inventory** section then uses the server-computed manifest and index
fingerprint to request a read-only scan. It remains available when an existing
fingerprint-owned index blocks a new build. Select **Inspect build reports**;
use **Refresh inventory** or the bounded Previous/Next controls for explicit
read-only requests. The browser never computes either identity or accepts a
client-selected storage path.

Candidates are displayed as `report_match`, `mismatch`, `failed`, `incomplete`,
`invalid`, or `unavailable`. Ordering is lexical by build ID and does not imply
recency. A complete empty scan says no reports were found in the inspected
inventory. An incomplete scan, including `inventory_limit_exceeded`, is shown
as incomplete and is never described as empty. Pages are point-in-time views,
not a stable snapshot while builds may publish reports.

Every classification is report-level evidence only: **Chroma collections
were not checked**. The viewer offers no report selection, activation, repair,
deletion, retry, or experiment action. A compatible report does not prove
persisted vectors are readable or that retrieval or model execution will
succeed. Source changes invalidate the inventory and preview; preview again
before another scan.

A succeeded job records a validated build report reference and actual record
counts. It does not activate the index, prove future retrieval, establish
production model readiness, or start an experiment. Manually configure the
ordinary runtime's existing report and storage settings and perform its normal
checks. Failed or partial index evidence remains preserved.

Vitest covers mocked API and React behavior. The bootstrap Vite smoke exercises
real loopback HTTP through Vite but does not drive a browser. The existing
`local_runtime_smoke.py --vite` separately checks the experiment-runtime
proxy. The intermittent Chroma HNSW reader and concurrent artifact-publisher
issues remain unresolved.
