# Production evaluation workbench architecture

The first target is a local-first monolith: one FastAPI process, one React/TypeScript app, SQLite metadata, local document/artifact storage, versioned Chroma indexes, and Ollama. Keep the research algorithms intact behind typed adapters; do not introduce microservices.

## Proposed layout

```text
backend/
  app/main.py                 # FastAPI wiring, lifespan, health
  api/{documents,indexes,queries,experiments,artifacts}.py
  schemas/{config,documents,runs,traces,metrics}.py
  services/{ingestion,indexing,retrieval,generation,evaluation,jobs}.py
  repositories/{sqlite,files,indexes}.py
  engine/                     # faithful extraction/chunk/embedding/retrieval adapters
  settings.py                 # Pydantic Settings, resolved config/fingerprints
frontend/
  src/{pages,components,api,features,types}/
tests/{unit,integration,evaluation,fixtures}/
data-local/                   # ignored runtime root
docker-compose.yml
```

## Backend boundaries

Pydantic schemas should model `Document`, `Page`, `TextBlock`, `ImageObject`, `Chunk`, `IndexVersion`, `ResolvedConfig`, `Experiment`, `QueryTrace`, `MetricValue`, and `Artifact`. Keep `LangChain Document` internal to the engine adapter. Each query trace should retain query ID, index ID, retrieved IDs/ranks/scores, adaptive weights, cross-modal overlaps, selected context, token counts, model call metadata, stage timings, resource telemetry and errors, with configurable redaction.

`engine/ingestion` should preserve `loading_pdf`, image filters, `bbox_chunker`, stable IDs and image association. `engine/indexing` should preserve Chroma cosine collections and BM25 preprocessing. `engine/retrieval` should preserve semantic/BM25/hybrid, RRF/weighted fusion, adaptive weights, reranker, cross-modal boost and formatter behavior. `engine/generation` should wrap Ollama discovery/lifecycle, multimodal payloads, retries and thinking capability checks. `evaluation` should call the existing formulas unchanged for baseline mode, but return metric provenance and eligible/skipped counts.

## Versioned indexes and SQLite

An index version is immutable and keyed by `(document_manifest_hash, ingestion_config_hash, text_model_id, image_model_id, collection_schema_version)`. Store text/image Chroma data under a generated directory, not fixed shared names. SQLite rows should link document versions, index versions, configs, runs, queries, metrics and exported artifacts. A failed build is retained as failed metadata but never promoted to active. Activation is one SQLite transaction after manifest validation.

## Job and API behavior

Use an in-process bounded executor for the first local version; do not add Celery/Redis. Endpoints should create jobs and return IDs for ingestion/indexing/evaluation, expose status/events by polling or local SSE, and serialize destructive index operations with locks. Query can be synchronous initially with a deadline; benchmark/ablation runs should be jobs. FastAPI lifespan owns shared model registries and closes owned resources.

## React workbench

The UI should have document/index inventory, configuration form with derived-field warnings, query playground with evidence and trace tabs, experiment/ablation runner, metric comparison, and artifact download. Controls should map to the configuration matrix, separate ingestion/index identity from retrieval/generation/evaluation controls, show unavailable metrics instead of zero, and display the exact resolved config and benchmark revision.

## Local deployment and CI

Docker Compose may run the API and frontend, with Ollama remaining a host/local dependency or optional service depending on GPU setup. Mount `data-local` outside the image. CI should run Python 3.10 compatibility, lint/type checks, unit/integration tests with fake embedder/LLM and temporary Chroma, notebook JSON/AST drift checks, and a small deterministic evaluation fixture. Heavy model tests remain opt-in and must record model/cache prerequisites.

## Preservation rules

Keep `notebooks/` as research baseline. Import or copy logic only after golden fixtures prove equivalence for extraction, chunk metadata, index add/query, each retrieval mode, formatter selection, Ollama error handling, metric formulas and six ablation configuration resolution. A compatibility adapter may call the existing source during phase one; refactoring is later and must be behavior-diffed.
