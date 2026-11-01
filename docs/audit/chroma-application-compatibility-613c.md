# Phase 6.13C — Application-boundary Chroma compatibility

## Decision

**Inconclusive for Chroma 1.5.0 and Chroma 1.5.9.** Each version completed one synthetic writer process and reached the application's report validation, `ChromaVectorStore.open_existing()`, collection metadata checks, and persisted count checks. Both reader attempts then stopped in the probe's query observer before it delegated to Chroma. No native text query, image query, or repeated query was executed.

The reader failure was a probe `TypeError`, not the historical HNSW error and not evidence of a Chroma incompatibility. The one-attempt-per-version budget has been used; neither version was rerun after correcting the observer. This report therefore does not establish application query compatibility for either version.

## Scope and environment

Phase 6.13B was committed as `c66369393d6bd55b7796d5f0eb4647079c9b2037`, parent `bf8951f1a3ba701e80eb2faae87f7d64d64c1014`, and pushed to `origin/production-evaluation-workbench`. Its bounded standalone-client findings are recorded in [the Phase 6.13B report](chroma-version-compatibility-613b.md). This Phase 6.13C comparison used those separate temporary environments; it did not alter the project `rag-env`, dependency pins, or production modules.

Both disposable environments used CPython 3.10.11 on Windows AMD64, had system-site packages disabled, passed `pip check`, and imported the exact intended Chroma version. The test-only imports also used `pydantic==2.14.2`, `rank-bm25==0.2.2`, `nltk==3.9.2`, and `joblib==1.5.3`. The latter three project dependencies were added only to the two disposable environments to import the real retrieval module. No model, corpus, NLTK data, HTTP service, or external endpoint was used. The child network guard recorded no external-network attempt.

The fixture used a generated one-page synthetic PDF and a recomputed document manifest. The index was created through the real `ChromaVectorStore.create_new()` and populated with deterministic, explicit three-dimensional text and image vectors. The selected build report was **schema-valid synthetic test evidence**, written by the test helper after the store writes; it was not emitted by the fresh-index builder. The report identity and both count fields were then validated through the real retrieval layer. No claim of builder-produced report coverage is made.

The harness used a test-only namespace-package shim to avoid eager `backend.engine` and `backend.experiments` convenience imports that require the omitted full ingestion/model stack. The imported `RunPlan`, manifest, writer lease, Chroma adapter, report validation, and `retrieve_for_plan()` implementations were repository implementations. This is not full builder or evaluation-pipeline coverage.

## Fixed experiment budget and results

The predeclared budget was one independent application attempt per version. Each attempt used a writer child followed by a separate reader child. There were no retries.

| Chroma | Writer process | Reader process | Completed application stages | Result |
|---|---|---|---|---|
| 1.5.0 | Python PID 35688; exit 0 | Python PID 36780; launcher PID 6424; exit 1; no timeout | Fixture creation, report validation, `open_existing()`, collection identity/metadata validation, counts 2 text / 1 image, lease release | Inconclusive; query observer failed before calling Chroma |
| 1.5.9 | Python PID 29860; exit 0 | Python PID 9124; launcher PID 30056; exit 1; no timeout | Fixture creation, report validation, `open_existing()`, collection identity/metadata validation, counts 2 text / 1 image, lease release | Inconclusive; query observer failed before calling Chroma |

The process runner distinguished the Windows launcher PID from each worker's reported Python PID. Both writers completed and exited before their readers started. Each reader exited and was reaped; neither timed out. Failure evidence, stdout/stderr, and persistence snapshots remain in the disposable experiment root outside the repository.

## Application-boundary observations

For both versions, the reader reconstructed and verified the serialized `RunPlan`, recomputed the source manifest, and matched the writer's index and run fingerprints. The actual `_validate_build_report()` accepted the selected report as `completed` with the expected fingerprint, manifest hash, and counts. `ChromaVectorStore.open_existing()` returned both collections with the expected names and application metadata, including the fingerprint, manifest hash, modality, schema values, and `hnsw:space=cosine`.

The observed collection IDs were:

| Chroma | `text_vectors` | `image_vectors` |
|---|---|---|
| 1.5.0 | `91be2a50-6743-4ce3-995f-2cbc53ade589` | `88ae7eb2-e5e7-48a0-8caf-9311950b15e5` |
| 1.5.9 | `d36192b1-b10b-42ac-9092-6de4ccefb64e` | `ee1904a0-5d30-4966-888b-2c5efa2940e6` |

Counts returned by the real adapter were 2 and 1 and matched the report. The implementation obtains an exclusive cooperating writer reservation in `open_existing()`: the probe verified ownership and attempted a competing reservation, which was rejected as busy. The adapter's `close()` released the reservation in both readers. The current `open_existing()` docstring calls this a “shared reservation”; the observed implementation and `WriterLease` behavior are exclusive. No production documentation or code was changed in this investigation.

### Query-stage probe defect

Each reader entered the first text-query observer at ordinal 1. Before the observer called the original `ChromaVectorStore.query_text()`, it called `_event(events, "query_started", **item)` while `item` also contained a `stage` key. Python raised `TypeError: _event() got multiple values for argument 'stage'`. The application retrieval layer wrapped that observer exception as `RetrievalQueryError("text semantic retrieval failed")`.

Thus the trusted text adapter and query wrapper boundary were reached, but the underlying Chroma query was **not** called. The image query and repeated text query were not reached. No HNSW failure was observed because no HNSW query was executed. The original reader tracebacks and the explicit exception chain are preserved in the local disposable evidence; private machine paths are intentionally omitted here.

After preserving both attempts, the probe was corrected to keep the event stage separate from observation fields. A small non-Chroma regression test verifies that query event recording no longer passes `stage` twice. The parent probe now also returns a nonzero status when any matrix trial is incomplete. Those corrections were not followed by another Chroma attempt because the fixed budget was consumed. The recorded reader failures and their outcomes have not been rewritten.

## Persistence and identity observations

Snapshots were taken after writer exit and after reader exit. Each store snapshot contained 11 files. For both versions, across the reader process:

- No files were added or removed and no file sizes changed.
- Five file digests changed: `chroma.sqlite3`, plus `data_level0.bin` and `length.bin` in each of the two HNSW segment directories.
- Four HNSW files had timestamp-only changes: `header.bin` and `link_lists.bin` in both segment directories.
- The selected build-report bytes and application `index-manifest.json` bytes were unchanged.
- The fresh-build-incomplete marker was absent after writer finalization, and the cooperating reservation was absent after writer and reader close.

These snapshots cover the reader lifecycle, including client open, collection access, counts, close, and process exit. They do not attribute a particular mutation to one Chroma call. The underlying query did not run. Digest changes are observations, not evidence of corruption. This remains consistent with the [Chroma persistence contract](../architecture/chroma-store-verification-contract.md): open/count operations are not advertised as filesystem-read-only, and wrapper close does not prove that Chroma's cached internal system stopped; process exit is the quiescent boundary used here.

## Compatibility and risk disposition

| Area | Chroma 1.5.0 | Chroma 1.5.9 |
|---|---|---|
| Isolated environment identity | Verified | Verified |
| Application fixture write and process handoff | Passed | Passed |
| Report validation, `open_existing()`, metadata and counts | Passed in one synthetic attempt | Passed in one synthetic attempt |
| Real text/image query sequence through `retrieve_for_plan()` | Not completed | Not completed |
| Application-boundary classification | **C — Inconclusive** | **C — Inconclusive** |
| Migration or deployment approval | Not assessed / not approved | Not assessed / not approved |

This experiment neither reproduces nor resolves the historical `Error creating hnsw segment reader: Nothing found on disk` incident. The prior [HNSW incident record](chroma-hnsw-incident.md) remains authoritative: the intermittent query failure is real, its root cause is unknown, and no production correction has been validated. The earlier Phase 6.13B standalone-client passes cannot substitute for application query coverage.

The security advisories and candidate qualification limits remain as recorded in the [Phase 6.13B report](chroma-version-compatibility-613b.md). A local, embedded-only synthetic experiment is narrower exposure than a network-facing service, but it is not security approval. No existing research or user index was opened, migrated, or used. Persistence compatibility, reverse migration, rollback, production dependency compatibility, and full retrieval/evaluation compatibility remain unestablished.

Relevant implementation and existing contract coverage include [`ChromaVectorStore`](../../backend/vectorstore/chroma_store.py), [`WriterLease`](../../backend/config/writer_lease.py), [`retrieve_for_plan()`](../../backend/engine/retrieval.py), [retrieval integration tests](../../tests/integration/test_retrieval.py), and [Chroma verification contract tests](../../tests/integration/test_chroma_verification_contract.py). The earlier successful disposable retrieval demonstration is described in the [manual report retrieval smoke guide](../architecture/manual-report-retrieval-smoke.md); its outcome does not change this Phase 6.13C result.

## Limits and next decision

This checkpoint established one successful application store creation/open/count/lease lifecycle per Chroma version, using a helper-created schema-valid report. It did not complete a real query for either version. It also did not exercise the genuine index builder, production embedding models, image decoding, evaluation, browser/runtime workflows, or an existing index migration.

At the initial checkpoint, the next useful step was a separately authorized, corrected one-shot application comparison with an event recorder regression already in place. The original two-attempt budget was exhausted, and that checkpoint did not authorize more trials, dependency changes, or opening any non-disposable index. A separate authorization and its results are recorded in the Phase 6.13C-R section below. The initial inconclusive result did not change the unresolved HNSW and artifact-publisher incidents; general-purpose unattended operation remains **NO-GO**, and Phase 6.12F remains incomplete.

## Phase 6.13C-R — Observer-validated confirmation study

This separately authorized confirmation study preserves the initial Phase 6.13C outcome above. The original matrix remains **C — Inconclusive due to probe instrumentation failure** for both versions: its one attempt per version stopped before the public Chroma `Collection.query()` method was invoked. The observations below come from two new, uniquely identified attempts; they do not replace or relabel the original attempts.

### Observer correction and preflight

The original observer passed an observation dictionary containing `stage` to `_event(events, "query_started", **item)`, while also supplying the event stage positionally. Python rejected the duplicate `stage` argument. The corrected observer keeps the event name separate from its payload, removes reserved envelope fields from query observations, and captures observer errors in a separate `instrumentation_errors` list. It invokes the saved public collection `query()` method once, passes the original arguments through unchanged, returns the same result object, and uses a bare `raise` for the original query exception. An observer error during an otherwise successful query invalidates the attempt; an observer error during a failing query does not replace the query exception.

The deterministic, Chroma-independent regressions use a fake public collection with an invocation counter. They verify text and image calls, repeated text calls, unchanged embeddings and `n_results`, unchanged returned object, the duplicate-stage failure pattern, original exception identity/cause/traceback, observer callback failure, missing query evidence, and strict child-result parsing. After the confirmation run, the parent validator was also tightened to cross-check query collection IDs and names against opened collection metadata and to require the writer reservation to be absent after close. Both preserved child results pass this final validator offline.

Preflight and post-validation selection: `tests/unit/test_chroma_application_compatibility_probe.py` plus `tests/unit/test_chroma_hnsw_diagnostics.py` — **31 passed**. Python compilation passed. The tests require actual query-call records; a `query_started` event alone cannot satisfy the child success contract. The runner returns nonzero for an incomplete or instrumentation-invalid matrix.

### New fixed-budget attempts

The confirmation budget was one fresh application-level attempt per version. It was executed sequentially, with separate writer and reader child processes and unique temporary roots. No attempt was retried.

| Chroma | Attempt ID | Writer Python PID / exit | Reader Python PID / Windows launcher PID / exit | Result |
|---|---|---|---|---|
| 1.5.0 | `dbeec61442cb4877bf067638f0a1e40b` | 35276 / 0 | 29008 / 33588 / 0 | A — bounded application retrieval compatible |
| 1.5.9 | `6c190d2e45914613a2d3413756f223ff` | 720 / 0 | 25732 / 2984 / 0 | A — bounded application retrieval compatible |

Both environment identity checks reported CPython 3.10.11, Windows AMD64, the expected exact Chroma version, disabled system-site packages, no `rag-env` on `sys.path`, and successful `pip check`. The runner's network guard recorded no external network attempts. The writer exited before the reader started. Both readers exited normally without timeout, and the launcher PID is recorded separately from the worker interpreter PID.

The fixture was the same form as the initial study: a synthetic one-page PDF, recomputed document manifest, real application `ChromaVectorStore.create_new()` writes with deterministic explicit three-dimensional embeddings, and a **schema-valid synthetic report written by the test helper**, not a builder-published report. The reader reconstructed and revalidated the `RunPlan` and source identity, then called the actual `retrieve_for_plan()` implementation three times in the requested text, image, repeated-text order. Each call traversed report validation, `open_existing()`, metadata and both persisted-count checks, and wrapper close.

### Actual query outcomes

The reader wrapped the actual Chroma collection class's public `query()` method, retained the original method, and invoked it once per application query. The structured results record method entry and completion separately, modality, ordinal, collection name and UUID, `n_results`, returned persisted IDs, distances, and finite-distance validation. The fake-collection regressions separately prove call-through semantics and argument/result identity without depending on Chroma.

| Chroma | Query ordinal | Modality / collection | Returned IDs | Distances |
|---|---:|---|---|---|
| 1.5.0 | 1 | text / `text_vectors` (`1e303c47-35bf-4264-a2ef-eb1e6d8feb7e`) | `text-1`, `text-2` | 0.0, 1.0 |
| 1.5.0 | 2 | image / `image_vectors` (`cee664ee-348a-426c-8183-d5b0b45d6abc`) | `image-1` | 0.0 |
| 1.5.0 | 3 | text / `text_vectors` (same UUID as ordinal 1) | `text-1`, `text-2` | 0.0, 1.0 |
| 1.5.9 | 1 | text / `text_vectors` (`8ae86565-f7bc-42c6-8fb4-9fb6f88f0a5c`) | `text-1`, `text-2` | 0.0, 1.0 |
| 1.5.9 | 2 | image / `image_vectors` (`09955751-959b-41fc-9c4c-9dd703485f4f`) | `image-1` | 0.0 |
| 1.5.9 | 3 | text / `text_vectors` (same UUID as ordinal 1) | `text-1`, `text-2` | 0.0, 1.0 |

For each version, report validation succeeded three times; each `open_existing()` succeeded three times; counts were text 2 and image 1 on all three opens; collection metadata and cosine configuration matched the application manifest; and each retrieval result retained the expected RunPlan, report, and source identities. The probe verified the cooperating reservation was exclusive while each wrapper was open. All three closes per reader released the reservation, and the reservation was absent after retrieval. No production embedding constructor was invoked.

### Persistence and retained evidence

The writer-exit and reader-exit snapshots are retained separately from the original Phase 6.13C snapshots. For each new attempt, the store snapshot contained 11 files. Across the complete reader lifecycle, each version had no file additions or removals and no file-size changes; five files changed digest (the Chroma SQLite file and `data_level0.bin` plus `length.bin` in both HNSW segment directories), and four HNSW files had timestamp-only changes (`header.bin` and `link_lists.bin` in both segment directories). Application report bytes and `index-manifest.json` bytes remained identical. These changes cover open, count, query, close, and process exit together and cannot be attributed to an individual operation. Digest changes are not evidence of corruption.

No HNSW reader error occurred in these two attempts. This is one successful sample per version, not a reliability estimate and not evidence that the historical intermittent failure is fixed. The previous failure remains real; no failure-time Rust segment state was observed. The separately documented Phase 6.13B standalone comparison is not expanded by this result.

### Phase 6.13C-R disposition and limits

| Boundary | Chroma 1.5.0 | Chroma 1.5.9 |
|---|---|---|
| Application report validation, `open_existing()`, metadata and counts | Passed in this bounded synthetic attempt | Passed in this bounded synthetic attempt |
| Three actual public Chroma queries through `retrieve_for_plan()` | Passed | Passed |
| Phase 6.13C-R classification | **A — Bounded application retrieval compatible** | **A — Bounded application retrieval compatible** |
| HNSW reliability, user-index migration, or deployment approval | Not established / not approved | Not established / not approved |

The initial Phase 6.13C classification remains **C**; Phase 6.13C-R is a separate two-attempt confirmation with the results above. These experiments do not exercise the genuine index builder's report publication, production embedding models, a real research corpus, evaluation and artifact persistence, full authenticated/browser workflows, or migration and rollback. They do not establish that either Chroma version is security-approved. Embedded-only synthetic use narrows exposure relative to a network-facing service, but does not resolve the advisories and security limitations in the [Phase 6.13B assessment](chroma-version-compatibility-613b.md).

The historical HNSW incident remains unresolved, as does the separate artifact-publisher incident. Phase 6.12F remains incomplete and general-purpose unattended operation remains **NO-GO**. The findings in this report are local and uncommitted; no dependency, production, or protected research file was changed.
