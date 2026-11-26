# RAG-PDF

RAG-PDF is a local-first application for chatting with installed Ollama models and asking questions against explicitly selected PDF indexes. It combines a React/Vite interface, authenticated local APIs, PyMuPDF extraction, BGE text embeddings, Chroma persistence, optional OpenCLIP image retrieval, reranking, and Ollama generation.

## What works

- **General Chat** sends real conversation requests to the installed Ollama model selected in Settings. Conversation turns stay in the active page session and are not saved as durable chat history.
- **Document Chat** uses the selected index's validated build report and RunPlan, the existing retrieval pipeline, context formatter, Ollama generation adapter, citation validation, and authenticated evidence delivery.
- **Documents** imports PDFs, shows the real source inventory, previews a server-resolved configuration, admits an explicitly confirmed fresh build, reports actual job state, and lets the user select a compatible build.
- **Results** and the legacy job, evaluation, ablation, and artifact-detail routes continue to use persisted backend records.

## Historical local demonstration

The November 2026 [demo runbook](docs/DEMO.md) records the following results on the original demo checkout. For the product branch, the [acceptance audit](docs/audit/m9-c0-product-acceptance.md) documents the outstanding live Document Chat check, and the [launcher report](docs/audit/m9-c2b-hosted-launcher-acceptance.md) covers startup and PDF viewing.

The checked-in [demo PDF](examples/demo/Orbit_Field_Notes.pdf) was processed and indexed with the configured BAAI/bge-large-en-v1.5 text model. The fresh build completed with three persisted text records and zero image records. The index was reopened after application shutdown and selected again after runtime reconstruction.

A real browser General Chat request used nemotron-3-nano:4b, temperature 0, and a 64-token output limit; the answer to "What is six times seven?" was 42.

A real browser Document Chat request used the selected gemma4:e2b RunPlan and retrieved page 1 of the demo PDF. It answered with the measured 184 watts and date and linked [T1] to the actual passage on page 1.

This is **text RAG qualification on a small synthetic corpus**, not broad accuracy evidence. One unsupported wind-speed question produced “Insufficient evidence.” with no citation links; this single response does not establish general abstention reliability. The demo PDF contains no embedded images, so CLIP embeddings and image retrieval were not qualified. No benchmark evaluation was run, so Results may correctly show an empty catalog. See [the demo runbook](docs/DEMO.md) for exact local steps and evidence.

## Start locally

Use a prepared project Python environment, Node.js, the locked frontend dependencies, and a running Ollama service with a supported installed model. Set the trusted source and index-storage paths before starting:

    $env:RAG_WORKBENCH_DATA_DIR = 'C:\path\to\pdf-source'
    $env:RAG_WORKBENCH_INDEX_STORAGE_ROOT = 'C:\path\to\index-storage'
    python scripts/start_local_app.py

Open http://127.0.0.1:5173. The launcher starts the ordinary API, bootstrap API, server-side credential gateway, and Vite on loopback. The browser does not receive API bearer credentials. Stop the application with Ctrl+C. Read [local operation and security boundaries](docs/operations/local-chat-first.md) before choosing persistent storage paths.

## Research integrity

The original research notebooks, metrics, and canonical ablation definitions remain available. Ordinary chat is separate from benchmark evaluation; chat interactions do not create research artifacts or metrics. Model selection in General Chat does not rewrite a selected index's RunPlan or historical evaluation configuration.
