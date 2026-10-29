# RAG Evaluation Workbench frontend

This is the local React/TypeScript interface for the existing Starlette API. It presents backend liveness, authenticated model-adapter state, and a navigable overview, jobs explorer, and artifact explorer. Job and artifact catalogs have server-backed filters and paging; detail pages show the stored metadata, metric aggregates, ablation comparison states, and an explicitly requested result JSON view.

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

For a static production build, configure the chosen static host to serve `index.html` for the client routes (`/jobs`, `/jobs/*`, `/artifacts`, `/artifacts/*`, and `/experiments/*`). The Python runtime does not serve or mount the frontend assets.

## Experiments

The Experiments area submits through the existing authenticated job endpoints. Add benchmark cases using your own evaluation queries, references, and expected page identifiers; the form starts blank and does not invent benchmark content.

Connect, then select **Inspect documents** to read the configured PDF source manifest. Supply a complete resolved configuration JSON object from the trusted project configuration workflow. For an evaluation, select **Prepare RunPlan** and inspect the returned fingerprints before explicitly submitting the benchmark job. The backend validates the configuration, current manifest, safety policy, and fingerprints; the browser does not create them. Changing the configuration invalidates the prepared plan. If source documents change, inspect them again and prepare another plan.

For an ablation, the inspected manifest identity and supplied base configuration are passed to the existing job endpoint, which creates the canonical six variants. Manifest inspection and plan preparation do not build an index or prove that a compatible index exists. Index discovery and building remain unavailable through the UI.

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

The API's `/health` route is liveness only. Runtime model state reports registered adapter state and does not prove index or Ollama readiness. Ablation result loading requires the original compatible base configuration; the UI does not invent or rewrite it. Artifact records have no creation timestamp, so the catalog view does not claim to identify the newest result. The UI does not provide document upload, index building, or a query playground, and it does not display fabricated jobs or artifacts.
