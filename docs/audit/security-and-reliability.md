# Security, reliability and persistence review

This is an evidence-based review of the tracked implementation. Findings marked “risk” are design risks or unverified conditions, not confirmed vulnerabilities.

## Confirmed behaviors and risks

| Area | Evidence | Assessment |
|---|---|---|
| Upload validation | No API/upload handler exists. `loading_pdf()` accepts a configured directory and opens every recursive `*.pdf`. | Upload size/page/MIME/scan policy is not implemented; API must add it before accepting user files. |
| Paths and filenames | Image destination uses `pdf_path.stem`; context formatter loads metadata `image_path`; no client-facing path validation exists. | A future upload layer must reject traversal, normalize names, use generated IDs and enforce an owned root. Current code is not evidence of an exploitable endpoint. |
| Limits | No configured max PDF bytes, pages, extracted images, image pixels, context bytes, or job duration. | Resource-exhaustion risk for large/adversarial PDFs and multimodal prompts. |
| Reset/delete | `reset_collections_on_start=True` deletes named Chroma collections; `delete_collection()` permanently deletes one collection and swallows reset deletion exceptions. | Destructive operation needs explicit job/API authorization, index IDs, locks, confirmation and audit log. |
| Isolation | Fixed `vector_text`/`vector_image` names and one `database_dir`; no document/config/model namespace. | Cross-run/cross-document contamination is possible when reset is disabled or multiple jobs share the directory. |
| Duplicate/interrupted ingestion | IDs are skipped using an in-memory set loaded at startup; `collection.add()` is one batch; no ingestion manifest or transaction. | Restart behavior can be partial; stable IDs help idempotency but do not prove complete paired text/image indexing. |
| Result persistence | Main writes UUID-short config snapshots and PDFs directly; no SQLite/job/run manifest. | Results are hard to query and correlate; interrupted writes can leave partial artifacts. |
| Model lifecycle | `LocalLLM` starts Ollama when the probe fails and terminates only its own `Popen`; constructor failure after start does not show a cleanup path. | Process cleanup and application shutdown need structured ownership/finally handling. |
| Retry semantics | LLM retries streaming and non-streaming exceptions with exponential sleep; exhausted calls return a synthetic answer unless `llm_fail_fast`. | A failed answer can be scored as output unless consumers honor `error`; retry total time is unbounded by deadline. |
| Concurrency | Global mutable `cfg`, shared fixed Chroma names, shared output directories, and no job lock. | Concurrent runs can race on reset, collection writes, model memory, output names and config mutation. |
| Logging | Query, context length, ground-truth availability, generated answer and model output are printed; report PDFs include retrieved context/images. | Sensitive document content may enter terminal/report artifacts. Logging/redaction policy is absent. |
| Network/process | Hugging Face-backed model initialization can download; Ollama HTTP is localhost; `subprocess.Popen(["ollama","serve"], shell=False)` is bounded to a fixed executable name. | Network policy/cache provenance and executable discovery should be explicit; no shell injection is evident in this call. |
| GPU telemetry | NVML probes device 0 and treats unavailable monitoring as zero. | Zero is ambiguous; persist `unavailable` separately from measured zero. |

## Reliability gaps to resolve before API exposure

Use a job record with states `queued/running/succeeded/failed/cancelled`, owner/run ID, input manifest, resolved config hash, index fingerprint, package/model revisions, timestamps, error type, and artifact manifest. Use per-index locks and immutable versioned directories. Ingestion should stage output, fsync/rename a manifest only after all text/image records are aligned, and support resume by manifest. Reset should be an explicit version deletion operation—not a startup default.

FastAPI should enforce content type, byte limit, page limit, decompression/pixel budget, filename normalization, document ID generation, and storage containment. Never pass user paths directly to `Path` operations. Serve images only through an ID-to-path repository lookup. Add timeouts around PDF extraction, model calls and subprocess startup; make cancellation terminate owned processes and mark jobs failed/cancelled.

Ollama thinking mode must be capability-aware: list model metadata, record whether `think` was accepted, and reject or downgrade explicitly rather than passing the flag blindly. Keep `context_tokens`, `output_tokens`, and chunk-token budgets as separate fields and record actual counts.

## Persistence recommendation

SQLite should store documents, ingestion attempts, immutable index versions, configuration snapshots, experiment runs, query traces, metric values with provenance, and artifact paths. PDFs/images/vector stores belong under an ignored local data root keyed by generated IDs. The repository should keep only small deterministic fixtures and manifests; existing tracked research assets should not be deleted during this audit. Add ignore rules in a later hygiene phase after an explicit migration of needed fixtures.

## Unverified items

No API exists to audit for live upload behavior, and no production deployment, concurrent run, Ollama process, or Chroma database was executed. Therefore upload vulnerability status, real-world retry behavior, file permissions, network exposure, and interruption recovery are unverified rather than confirmed secure or insecure.
