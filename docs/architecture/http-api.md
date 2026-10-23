# Authenticated experiment HTTP API

The experiment API is a small Starlette ASGI application created by
`create_app(service, api_token=...)`. Starlette is used directly because the
current dependency contract pins Starlette but does not make FastAPI part of
the API design. Deployment requires an external ASGI server; this phase does
not create a global app, infer settings, or construct a real pipeline service.

All `/api/v1/*` routes require an explicitly supplied bearer token. The token
is compared with `hmac.compare_digest`; it is a local access boundary rather
than an identity provider. Production deployment must provide the secret
through its own secure configuration and add network controls before exposing
the API publicly. `/health` is unauthenticated liveness only and does not claim
index, model, catalog or Ollama readiness.

The API accepts strict Pydantic request models with forbidden extra fields and
converts them into the existing `RunPlan`, `ResolvedRunConfig`, `BenchmarkSuite`
and canonical ablation-plan contracts. Run-plan fingerprints are verified by
the existing deserializer. Ablation requests use the canonical six definitions
and force the existing non-destructive safety policy. Request JSON is bounded,
decoded as strict UTF-8, rejects duplicate keys and non-finite constants, and
does not accept storage or model paths. Responses use stable bounded error
objects, `Cache-Control: no-store`, and `X-Content-Type-Options: nosniff`.

Routes are provided for health, evaluation and ablation submission, artifact
listing/metadata, metric aggregates, ablation comparisons, and serialized
result loading. Execution and catalog calls run through Starlette's supported
thread-pool utility so synchronous retrieval, generation and SQLite work does
not execute directly on the event loop. Requests remain synchronous from the
client's perspective and may run for a long time; this is not a durable job
system, and a disconnected client does not create a fabricated result.

Read routes delegate to `ExperimentService`, which delegates to the catalog and
artifact store. Result loading preserves canonical serialization and requires
the accepted plan for ablation artifacts. The API returns no raw request
queries, references, passages, responses, credentials, absolute paths or
arbitrary exception messages. The current request/response bound is local and
conservative; a later job layer may provide durable asynchronous execution.
# Bounded jobs

An optional `ExperimentJobManager` supplied to `create_app` adds authenticated, bounded job submission and polling. Job requests return HTTP 202 while validated plans remain in process memory; lifecycle metadata is stored in a separate SQLite registry. The manager delegates execution to `ExperimentService`, and does not provide distributed coordination or restart-resumable payloads.
