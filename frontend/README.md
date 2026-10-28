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

For a static production build, configure the chosen static host to serve `index.html` for the client routes (`/jobs`, `/jobs/*`, `/artifacts`, and `/artifacts/*`). The Python runtime does not serve or mount the frontend assets.

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

The API's `/health` route is liveness only. Runtime model state reports registered adapter state and does not prove index or Ollama readiness. Ablation result loading requires the original compatible base configuration; the UI does not invent or rewrite it. Artifact records have no creation timestamp, so the catalog view does not claim to identify the newest result. The UI does not provide document upload, index building, a query playground, or evaluation/ablation submission forms, and it does not display fabricated jobs or artifacts.
