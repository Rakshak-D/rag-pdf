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
and force the existing non-destructive safety policy. The configured request
body byte limit is enforced as ASGI stream chunks are consumed; a body over the
limit returns `413 request_body_too_large`. JSON requests require
`application/json`, decode as strict UTF-8, reject duplicate keys and non-finite
constants, and must be a valid object matching the strict request schema.
Invalid JSON and schemas return `400 request_invalid`; unsupported content
types return `415 unsupported_content_type`. These are application request
bounds, not network-level denial-of-service or rate-limiting controls.

Responses use stable bounded error objects,
`Cache-Control: no-store`, and `X-Content-Type-Options: nosniff`. The serialized
result-loading route applies the configured byte limit before returning the
complete JSON body; oversized results return `413 response_body_too_large`
without truncation. The same application setting currently bounds requests and
serialized result responses. `/health` remains unauthenticated liveness only.

Routes are provided for health, evaluation and ablation submission, artifact
listing/metadata, metric aggregates, ablation comparisons, and serialized
result loading. The authenticated `GET /api/v1/documents/manifest` route
re-hashes the explicitly configured local PDF source root and returns the
canonical manifest hash and relative entry metadata. It returns an explicit
`empty` flag for an empty PDF corpus and never includes the source root. The
authenticated `POST /api/v1/run-plans` route accepts a complete
`resolved_config` and the previously observed `expected_manifest_hash`. It
validates the configuration and non-destructive policy, rebuilds the live
manifest, rejects an empty corpus or a changed hash, and then delegates plan
construction and fingerprinting to the existing `build_run_plan()` domain
function. Its `run_plan` object uses the same serialized fields accepted by
evaluation submission. Neither route extracts PDFs, creates snapshots,
initializes models, accesses Chroma, writes build reports, or creates indexes.
Manifest and prepared-plan JSON responses are bounded by the configured
response byte limit. Stable errors include `manifest_source_unconfigured`,
`manifest_source_unavailable`, `manifest_invalid`, `manifest_source_changed`,
`manifest_empty`, `manifest_changed`, `manifest_hash_invalid`, `configuration_invalid`, and
`destructive_configuration_forbidden`.

`POST /api/v1/index/readiness` accepts `workflow` (`evaluation` or
`ablation`), a complete `resolved_config`, and `expected_manifest_hash`.
It rechecks the configured source manifest and uses the existing plan and
report validation contracts against the runtime's configured build report.
`report_match` means the report matches; `mismatch` means the report is
incompatible, `unavailable` means it cannot be read, and `indeterminate`
means report validation could not establish compatibility. The response's
`store_checked` is always false and observed counts are null: this endpoint
does not open Chroma. Reported counts are returned only for validated reports.
This is a point-in-time report check, not index discovery or a guarantee of
retrieval, model availability, or execution success.

Execution and catalog calls run through Starlette's supported
thread-pool utility so synchronous retrieval, generation and SQLite work does
not execute directly on the event loop. Requests remain synchronous from the
client's perspective and may run for a long time; this is not a durable job
system, and a disconnected client does not create a fabricated result.

Artifact-list and job-list `limit`/`offset` query values are checked as decimal
strings and bounded to 19 digits before integer conversion. Values above
SQLite's signed 64-bit integer maximum, as well as malformed or longer values,
return `400 query_invalid` before the list operation is called. Leading zeroes
are accepted when the complete input remains within the digit bound. This is
an application parsing bound; it does not impose a network-level URI-size
limit.

Read routes delegate to `ExperimentService`, which delegates to the catalog and
artifact store. Result loading preserves canonical serialization and requires
the accepted plan for ablation artifacts. The API returns no raw request
queries, references, passages, responses, credentials, absolute paths or
arbitrary exception messages. The current request/response bound is local and
conservative; a later job layer may provide durable asynchronous execution.
# Bounded jobs

An optional `ExperimentJobManager` supplied to `create_app` adds authenticated, bounded job submission and polling. Job requests return HTTP 202 while validated plans remain in process memory; lifecycle metadata is stored in a separate SQLite registry. The manager delegates execution to `ExperimentService`, and does not provide distributed coordination or restart-resumable payloads.

`GET /api/v1/runtime/models` returns the configured registry's read-only safe
component status, or `runtime_registry_unconfigured` when the service has no
registry. It never performs warm-up or inference, and model status does not
claim that Ollama or an index is ready.
