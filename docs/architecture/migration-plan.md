# Migration plan and acceptance criteria

The active branch is `production-evaluation-workbench`; `main` is the protected research baseline. The migration must be evidence-driven and reversible.

## Phase 1 — reproducible baseline (first implementation phase)

Freeze the research baseline without changing notebooks. Add a read-only baseline runner/test harness outside the research sources that imports the existing `.py` implementation with temporary directories and fakes for Ollama/model downloads where needed. Capture: Python/package versions, commit SHA, benchmark question hash, all resolved config fields, model identifiers, hardware, chunk records, index counts, retrieval traces, metric eligible/skipped counts and output manifests. Add golden fixtures for the three PDFs (or compact extracted fixture contracts if licensing/storage makes PDFs unsuitable).

Acceptance: `git diff main -- notebooks data` is empty; baseline can be run twice in an isolated temporary root; chunk IDs/metadata, retrieval IDs/ranks/scores and metric formulas match observed reference outputs; failed generation is represented distinctly from a valid answer; no generated DB/model/PDF enters Git.

## Phase 2 — typed configuration and engine seams

Introduce Pydantic settings/resolved-config schemas and an adapter around the current engine. Preserve defaults and formulas. Add validation for positive bounds, path containment, model lists, weight sums, token budgets, threshold ordering and mode dependencies. Compute index fingerprints without changing algorithm parameters.

Acceptance: every public `Config` field has a schema/control mapping; invalid combinations fail before side effects; a serialized resolved config reconstructs the same effective constructor arguments; baseline fixtures are behavior-equivalent.

## Phase 3 — safe ingestion and versioned persistence

Add document upload policy, generated document IDs, staged files, PDF/image limits, immutable manifests, versioned Chroma paths and SQLite metadata. Implement resume/idempotency and explicit reset/delete with locks. Do not delete existing tracked fixtures in this phase.

Acceptance: traversal names are rejected; oversize/page/pixel-limit files fail before model work; interrupted ingestion cannot become active; duplicate ingestion yields the same manifest/index fingerprint; two documents/configs can coexist without collection collision; deletion requires an explicit index ID.

## Phase 4 — query API and trace fidelity

Expose typed query/trace endpoints and Ollama capability-aware generation. Keep synchronous local query first; add bounded jobs for long evaluations. Persist stage timings, resource availability, retries and errors.

Acceptance: semantic, BM25 and hybrid queries return the same ordered evidence as baseline on fixtures; RRF, adaptive weighting, reranking, cross-modal boost and filtering are all covered; an Ollama timeout/retry produces a failed trace or explicit error state, never a silently valid answer.

## Phase 5 — evaluation workbench and six-config ablation

Build experiment records, benchmark selection, metric provenance, comparison views and ablation jobs. Run the exact six existing configurations, with immutable per-config resolved snapshots and artifacts. Keep current metric formulas in “research-compatible” mode; any corrected metric is a separately named metric.

Acceptance: all six labels produce separate run records/artifacts, config overrides do not leak between runs, results are reproducible from the run manifest, ground-truth absence is visible, and report labels distinguish measured metrics, references, heuristics and model-judged metrics.

## Phase 6 — React UI, packaging and CI hardening

Add the React/TypeScript surfaces from `production-workbench.md`, Compose, local data mounts, CI, migration documentation and operator diagnostics. Do not add distributed services.

Acceptance: UI can upload within policy, select an active index, run a query, inspect evidence/trace, launch an experiment, compare metrics and retrieve artifacts; CI runs unit/integration/evaluation-contract tests; a clean local checkout can reproduce the baseline with documented Ollama/model prerequisites.

## Required test inventory

- Configuration: defaults, invalid mode/flag combinations, weight sums, token/k bounds, path containment, serialization round-trip, index fingerprint sensitivity.
- Ingestion/chunking: deterministic PDF ordering, image rejection thresholds, page/block contracts, bbox merge/overlap, stable chunk IDs, overlap behavior, duplicate text collision behavior.
- Persistence: temporary Chroma initialization, add/query, duplicate IDs, empty collection, interrupted/staged manifest, version isolation, explicit deletion.
- Retrieval: semantic/BM25/hybrid, zero-score BM25 fallback, RRF and weighted fusion, adaptive presets, reranker normalization, cross-modal overlap, percentile and near-duplicate filtering.
- Generation: model unavailable, Ollama unavailable/startup timeout, streaming interruption, retries/backoff, fail-fast, empty response, image encoding, thinking capability unsupported.
- Metrics: exact known-value ROUGE/BLEU/METEOR/BERTScore contract fixtures where dependency available, FCD formula, lexical faithfulness thresholds, semantic/term-frequency GT coverage, page coverage, latency/throughput/resource aggregation, skipped/zero provenance.
- Reproducibility: identical manifest/config/model revisions produce identical chunk IDs and retrieval outputs; ablation configs are isolated and complete; artifact manifest references the run.

## Proving `main` is unchanged

At each phase run `git diff --name-only main -- notebooks` and `git diff --name-only main -- data`; both must remain empty for migration-only changes. Also compare `git hash-object` for every tracked research source against the pre-phase manifest and use a clean checkout of `main` as a reference. This audit itself adds only the seven requested documents on the current branch; it does not modify, merge, reset or force-push `main`.

## Proposed first implementation task

Implement Phase 1 only: a non-invasive baseline contract harness and tests, with temporary runtime directories and deterministic fakes, while leaving all research notebooks and `rag_pipeline.py` untouched. Do not begin the FastAPI or React implementation until those baseline acceptance criteria are observed.
