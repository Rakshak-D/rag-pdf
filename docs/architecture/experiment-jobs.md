# Bounded local experiment jobs

The job manager adds a deliberately small asynchronous boundary around the synchronous `ExperimentService`. It uses a bounded in-process queue, fixed standard-library worker threads, and a separate SQLite metadata database. Workers call only `run_evaluation()` or `run_ablation()`; retrieval, generation, metrics, artifact publication, and catalog transactions remain owned by the existing layers.

Jobs contain only safe metadata in SQLite. Validated plans and benchmark suites are held in memory until consumed, so benchmark queries and reference text are never persisted in the job registry. On startup, queued and running rows from a previous process become `interrupted` with `process_restarted`; work is not resumed. This is a single-manager-process facility, not a distributed or restart-resumable queue.

When configured with multiple workers, jobs may execute concurrently within
that process. A shared `ModelAdapterRegistry` reuses adapters for compatible
settings, with each adapter protecting its own model calls. The shared SQLite
catalog serializes local registration transactions, while each result remains
an independently immutable, content-addressed artifact. Sharing these local
components between worker threads does not coordinate separate processes or
hosts.

The lifecycle is `queued -> running -> succeeded|partial|failed|interrupted`, with queued work also cancellable. A persisted evaluation whose own cases failed remains distinguishable from a worker failure: it has an artifact ID and persisted result status, while a worker failure has no fabricated artifact and a stable error code. Shutdown stops acceptance, marks unfinished rows interrupted, and waits only for the configured bounded timeout. A late completion cannot overwrite a terminal row, although any artifact it registered remains discoverable.

Authenticated Starlette routes submit jobs at `POST /api/v1/jobs/evaluations` and `/api/v1/jobs/ablations`, poll with `GET /api/v1/jobs/{job_id}`, list with `GET /api/v1/jobs`, and cancel queued work with `POST /api/v1/jobs/{job_id}/cancel`. Existing synchronous routes remain available. Without a configured manager, job routes return `job_execution_unconfigured` and never fall back to synchronous execution.

The service may share an explicitly injected model-adapter registry across
workers. Registry reuse is keyed by compatible model settings and does not
make job startup load models. Model status is reported separately through the
authenticated runtime endpoint; it is not a readiness claim for Ollama or the
index.

Each `ExperimentJobManager` has a one-shot lifecycle. Repeated `start()` calls
while it is running are idempotent, and repeated `shutdown()` calls are safe.
Once shutdown begins, that manager instance cannot restart; construct a new
manager for a later application lifecycle. Shutdown waits only for its bounded
timeout and does not forcibly cancel a model inference already in progress. A
worker that finishes late cannot replace the terminal interrupted job state.
SQLite continues to hold job metadata only: accepted payloads are not persisted,
and execution cannot resume after process restart.
