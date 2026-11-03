# Phase 6.15B — HNSW reader cache and persistence lifecycle audit

- **Review date:** 2026-11-03
- **Repository checkpoint:** `8d6b015` (`Correct Chroma HNSW production query source attribution`, followed by a source-backed tracing-path clarification)
- **Phase 6.15A correction:** `a0624b0a3829a318a3c4a0e1d41711c47dd4010e`, parent `c00a2b1bd9e1947e0dd8185dd52402a27eb301a2`
- **Source reviewed:** Chroma 1.5.0 source commit `ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda`
- **Installed package:** `chromadb==1.5.0`, CPython 3.10.11, Windows AMD64
- **Hypothesis assessment:** **Plausible, not established**
- **Native queries, builds, installs, or index access in this audit:** None

## Executive finding

Chroma 1.5.0 source supports a concrete conditional path: an HNSW pool miss calls the persisted reader loader; if its segment directory is missing, its metadata pickle is missing, or the pickle contains an empty `id_to_label`, that loader returns `UninitializedSegment`, which formats as `Nothing found on disk`. A cache hit instead wraps the in-memory index and does not consult the pickle.

The same source also explains why a fresh process can successfully query a fixture whose pickle is absent after exit. The first count or KNN call backfills the collection. The backfill handler treats an uninitialized reader as HNSW sequence zero, replays retained SQLite log entries, creates and caches a writer, and applies the log chunk. Below the synchronization threshold, the query can then use that in-memory cached index without a pickle. The SQLite watermark remains at its last persisted value, so purge does not remove newer log rows.

That lifecycle makes the proposed cache-miss/unpersisted-metadata mechanism plausible, but it does not explain the historical failures by itself. The normal first-count/backfill sequence should populate the shared pool before the later KNN reader lookup. The records show no failure-time pool state or eviction; the same-process failing test also does not show that wrapper close removed the Chroma system. No failure-time pickle state was captured. The installed extension's exact correspondence to the reviewed source build is also not independently proven.

## Source and binary provenance

Phase 6.15A was corrected in a forward commit after its source table incorrectly attributed production KNN planning to `in_memory_frontend.rs`. The correction records the actual `Bindings` → `ServiceBasedFrontend` → configured local executor path. A second one-file commit, `8d6b015`, strengthens the tracing assessment by checking the full embedded Python binding path. The prior commits remain unchanged in history.

The official Chroma 1.5.0 tag resolves to source commit [`ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda`](https://github.com/chroma-core/chroma/tree/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda). The local distribution reports version 1.5.0; the installed `.pyd` has a locally valid `RECORD` entry, but its original wheel URL/hash and source commit were not retained. Therefore, Rust behavior below is established for the cited 1.5.0 source and is a source-grounded model for the installed binary, not a byte-for-byte binary/source identity claim. See the corrected [6.15A provenance record](hnsw-native-diagnostic-feasibility-615a.md).

The source references below are all pinned to the commit above. No current `main` behavior is used to explain the installed 1.5.0 package.

## Production query path

| Step | Pinned implementation |
|---|---|
| Python adapter | The installed `RustBindingsAPI` calls the embedded binding. `Bindings::query()` validates/builds `QueryRequest` and calls `frontend_clone.query(request)` with the GIL released. [Binding query](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/python_bindings/src/bindings.rs#L654-L701) |
| Frontend selection | `impls/mod.rs` aliases `Frontend` to `ServiceBasedFrontend`; the embedded binding configures `ExecutorConfig::Local`. [Alias](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/frontend/src/impls/mod.rs#L1-L7), [binding configuration](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/python_bindings/src/bindings.rs#L69-L139) |
| KNN planning | `ServiceBasedFrontend::retryable_query()` resolves the collection and segment records, constructs `Knn`, and awaits `self.executor.knn(...)`. [Production frontend query](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/frontend/src/impls/service_based_frontend.rs#L1744-L1813) |
| Executor construction | `ServiceBasedFrontend::try_from_config()` constructs the configured executor. [Frontend configuration](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/frontend/src/impls/service_based_frontend.rs#L2305-L2353) |
| Local execution | `LocalExecutor::knn()` invokes collection backfill, then requests an HNSW reader from the segment manager. [Backfill and KNN](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/frontend/src/executor/local.rs#L52-L198) |
| Pool and disk | `get_hnsw_reader()` returns a reader around the cached index on a hit. On a miss it calls `LocalHnswSegmentReader::from_segment()`, starts the index, and inserts it in the pool. [Segment manager](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/segment/src/local_segment_manager.rs#L95-L145) |

The production path is not `InMemoryFrontend`; that separate module uses `TestReferenceSegment`. The corrected Phase 6.15A report states this distinction.

## Writer and persistence lifecycle

### Collection writes and backfill

The Rust binding's `upsert()` calls `frontend_clone.upsert(req)`. `ServiceBasedFrontend::upsert()` converts the operation to log records and calls `retryable_push_logs()`, which delegates to the configured log service. [Binding upsert](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/python_bindings/src/bindings.rs#L500-L545), [frontend upsert](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/frontend/src/impls/service_based_frontend.rs#L1083-L1155), [log delegation](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/frontend/src/impls/service_based_frontend.rs#L860-L875).

For local SQLite, `push_logs()` commits rows into `embeddings_queue`, then synchronously requests `BackfillMessage` and `PurgeLogsMessage` before returning. The backfill handler resolves the collection's vector segment and current metadata/HNSW sequence watermarks. It asks the HNSW manager for a reader; if that returns `UninitializedSegment`, it uses HNSW watermark zero, reads the retained log range, applies metadata logs, gets a writer, and applies the HNSW chunk. [SQLite log write](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/log/src/sqlite_log.rs#L330-L405), [backfill handler](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/log/src/local_compaction_manager.rs#L140-L250).

The manager uses the same `IndexUuid` key for readers and writers. A writer cache miss initializes a persistent HNSW index and inserts it in the pool; a writer cache hit reuses the shared `LocalHnswIndex`. The writer is therefore available to later readers through the same pool entry. [Shared reader/writer pool](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/segment/src/local_segment_manager.rs#L95-L150), [writer initialization](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/segment/src/local_hnsw.rs#L500-L610).

### Synchronization threshold

The Chroma 1.5.0 collection-configuration default is `sync_threshold=1000`. RAG-PDF creates its collections with `hnsw:space=cosine` and no threshold override; a source search of the application and relevant tests found no `hnsw:sync_threshold` override. [Default population](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/chromadb/api/collection_configuration.py#L435-L460), [RAG-PDF collection creation](../../backend/vectorstore/chroma_store.py#L345-L375).

`apply_log_chunk()` increments `num_elements_since_last_persist` for new log offsets and mutates the in-memory HNSW index and ID map. It calls `persist()` only when the counter reaches the configured threshold. `persist()` saves the native index and writes `index_metadata.pickle`; only after that does the apply path update SQLite's `max_seq_id` row and reset the counter. With fewer than 1,000 new operations since the last persistence, this path does not write the pickle or advance that persisted HNSW watermark. [Threshold check](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/segment/src/local_hnsw.rs#L613-L835), [index and ID-map persistence](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/segment/src/local_hnsw.rs#L850-L865).

This is relevant to the controlled fixtures: the retained Phase 6.12F run built one text and one image record, and `tests/integration/test_retrieval.py` writes two text records and one image record. Each modality is a separate collection, so these insertions are below the default threshold per collection. This establishes that a successful small write can leave the new HNSW metadata unpersisted; it does not establish that this state caused a historical query failure.

During backfill, if the HNSW watermark is zero, the subsequent purge watermark is `min(metadata_max_seq_id, hnsw_max_seq_id)`. The SQLite purge deletes rows with sequence IDs strictly less than that watermark. Thus newer log entries remain available for a fresh executor to replay when the HNSW threshold has not advanced the persisted watermark. [Purge watermark](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/log/src/local_compaction_manager.rs#L260-L310), [SQLite purge predicate](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/log/src/sqlite_log.rs#L465-L495).

### Cache eviction and persistence

The Rust manager's default pool capacity is 65,536, but the embedded binding passes the Python `hnsw_cache_size`; the incident record observed 102 on this Windows environment. The pool's eviction listener calls `LocalHnswIndex::close()`, whose inspected implementation calls `index.close_fd()`; it does not call `persist()`. Eviction removes the pool entry, so a later lookup can take the disk-reader path. [Pool configuration and eviction callback](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/segment/src/local_segment_manager.rs#L15-L75), [FD close](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/segment/src/local_hnsw.rs#L390-L405), [observed Windows capacity](chroma-hnsw-incident.md#incident-summary).

No explicit persistence call was found in the RAG-PDF wrapper close path, Chroma's embedded `RustBindingsAPI.stop()`, or the native cache eviction close path. `ChromaVectorStore.close()` drops its Python client/collection references and releases the application lease; it does not call a Chroma client close method. Chroma's Python `SharedSystemClient` retains systems in a class-level mapping keyed by persistent directory, so reopening the same path in the same process can reuse the same system. A fresh process starts with a new Python system map and new Rust manager/cache. [RAG-PDF close](../../backend/vectorstore/chroma_store.py#L474-L484), [shared system map](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/chromadb/api/shared_system_client.py#L13-L47), [Python binding start/stop](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/chromadb/api/rust.py#L73-L130), [native index close](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/segment/src/local_hnsw.rs#L390-L405).

These inspected paths establish that wrapper close and cache eviction do not explicitly save HNSW metadata. They do not establish the behavior of every native-library destructor or operating-system buffer at process termination; no low-level teardown experiment was run.

## Query, reopen, and application publication sequence

The relevant RAG-PDF sequence is:

1. The builder opens the plan-owned store, upserts text and image records, verifies collection counts and IDs, and closes its wrapper in `finally`.
2. It writes the build report after that wrapper close and finalizes the application index manifest. The builder does not run a semantic KNN query. [Builder sequence](../../backend/engine/index_builder.py#L591-L670).
3. A later retrieval opens the existing store, reads text and image counts, validates them against the report, then performs the text query followed by the image query. The `count()` calls enter Chroma before the first semantic KNN query. [Retrieval sequence](../../backend/engine/retrieval.py#L575-L638).
4. In the Phase 6.12F smoke, the genuine builder ran in a child process; evaluation ran later in separate child processes. A fresh evaluation process begins with an empty Chroma system/cache, but its count path invokes `LocalExecutor::try_backfill_collection()` before returning. [Smoke child sequence](../../scripts/manual_report_evaluation_smoke.py#L155-L207), [scenario sequence](../../scripts/manual_report_evaluation_smoke.py#L266-L380).

The source therefore separates three readiness states:

1. **Application report/index publication:** the builder has validated its own counts/IDs and published the app report/manifest.
2. **SQLite collection and segment state:** collection metadata and record counts can be read.
3. **Independent persisted HNSW reader state:** a cache miss can load the vector segment from its directory, metadata pickle, nonempty ID map, and native HNSW files.

The first two do not guarantee the third. A fresh query may still succeed when the pickle is absent because the first count/backfill replays retained logs and seeds the new in-memory cache before KNN. Conversely, if collection backfill has already been marked complete and the pool entry is later evicted while the metadata remains unpersisted, a subsequent KNN cache miss can reach the reader error. That latter sequence is source-supported but no such eviction or timing was captured in the incident.

## The three native branches and error propagation

On a persistent-reader cache miss, `LocalHnswSegmentReader::from_segment()` returns the same `UninitializedSegment` variant when:

| Branch | Source condition |
|---|---|
| Segment directory absent | `<persist_root>/<segment UUID>` does not exist. |
| Metadata pickle absent | Directory exists but `index_metadata.pickle` does not. |
| Empty ID map | Pickle deserializes but `id_to_label` is empty. |

The variant displays `Nothing found on disk`. The manager wraps it as `Error creating hnsw segment reader: ...`; the Python binding maps an internal Chroma error to `chromadb.errors.InternalError` using its formatted text. A malformed pickle and a nonempty ID map with native HNSW load failure use distinct error variants. [Reader branches](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/segment/src/local_hnsw.rs#L101-L173), [manager error prefix and lookup](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/segment/src/local_segment_manager.rs#L75-L122), [Python error mapping](https://github.com/chroma-core/chroma/blob/ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda/rust/python_bindings/src/errors.rs#L13-L36).

## Historical evidence compared with the lifecycle model

| Retained observation | Assessment against the hypothesis |
|---|---|
| Original Phase 6.12F evaluation child failed on its first native `text_vectors` query. Its report and five retrieval-negative cases had passed; generation and later persistence cases were not reached. | The error is consistent with a cache-miss reader initialization failure under the cited source, but the record contains no pool event or query-time pickle observation. Retrieval performs count checks first, which should invoke backfill and warm the shared pool. The failure therefore leaves an unobserved transition/state gap; it does not prove the missing-pickle branch. |
| Controlled standalone original retrieval test failed at query 1 after same-process upserts, wrapper close, reopen, count checks, then query. | The error is conditionally consistent with a later pool miss. It is contrary to the simple claim that `ChromaVectorStore.close()` itself clears the native cache: source shows the wrapper drops references, while Chroma's process-global system map retains the system. Eviction, system replacement, another unrecorded lifecycle event, or binary/source differences remain possible; none was captured. |
| Passing instrumented attempts and passing uninstrumented attempts. | They show the error is intermittent across recorded attempts. They do not record per-segment pool hits, eviction, or the reader branch; therefore they do not distinguish the persistence hypothesis. |
| Four successful cold-process retrieval attempts, each with no cached Python Chroma system before open. | These contradict a deterministic claim that a cold process plus an absent pickle must fail. They are consistent with source's cold backfill path: the first count replays retained logs, creates/caches a writer, and KNN then gets a pool hit. The query-time cache path and pickle state were not recorded. |
| `index_metadata.pickle` absent in some passing and failing post-exit fixtures; HNSW segment directories and SQLite mappings were present in post-exit inventories. | Not discriminating. These observations occurred after process exit, not at the reader branch. Missing pickle can be expected for a sub-threshold in-memory writer whose log entries remain replayable. Segment-directory existence does not distinguish missing-pickle from empty-map branches. |
| Zero-byte `link_lists.bin` appeared in both passing and failing post-exit fixtures. | Not discriminating and not evidence of corruption; the same inventory was observed in successes. |
| No retained failure-time Rust branch, HNSW pool eviction, or deterministic reproducer. | Unknown at the causally relevant time. The suspected eviction/cache-miss transition was not observed. |

The full-suite historical result remains `792 passed, 17 skipped, 1 failed`; no later complete-suite run replaces it. The successful bounded Phase 6.12F smoke and the later passing diagnostics remain separate evidence, not resolution of the earlier native failure. See the [incident record](chroma-hnsw-incident.md) and [release assessment](release-readiness-612f.md).

## Explicit causal questions

1. **Can a cached reader be returned without consulting `index_metadata.pickle`?** Yes. A pool hit wraps the cached `LocalHnswIndex`; it does not call `from_segment()`.
2. **Can a cache miss fail if the pickle is absent?** Yes. A direct persistent-reader miss with a segment directory but no pickle returns `UninitializedSegment`. During the initial backfill path, that variant is caught and treated as sequence zero; a later direct KNN miss is not handled that way.
3. **Can a completed write leave the pickle absent?** Yes. Below the default 1,000-operation sync threshold, the HNSW index and ID map are updated in memory, but this apply path does not call `persist()` or advance the SQLite HNSW watermark. The small RAG-PDF fixtures are below that threshold.
4. **Does the retained failure directly demonstrate a pool miss?** No cache hit/miss event was captured. Conditional on the installed extension matching the cited 1.5.0 source, the query error's `UninitializedSegment` path is reached through `from_segment()` after a pool miss. That is a source-based inference, not a recorded cache observation.
5. **Does the retained failure demonstrate that the pickle was absent at that instant?** No. The available inventories are post-exit and include both passing and failing fixtures.
6. **Could the missing-directory or empty-ID-map branch explain the same error?** Yes. Both return the same variant and message; no retained evidence distinguishes them from the missing-pickle branch.
7. **Can supported Python-side diagnostics uniquely identify the branch?** No. Existing Python observers can record collection/query context and post-exception file/SQLite state, but not the Rust pool hit/miss, transient ID map, or which check returned the variant. These observations are not atomic at the native throw.
8. **Would a custom native build prove the underlying cause?** No. It can identify the branch and segment at failure. It does not by itself establish why the directory/pickle/map had that state, why a cache miss occurred, whether eviction or backfill state caused it, or whether the instrumented build changed timing.

## Hypothesis and next engineering decision

**Assessment: plausible, not established.** Source proves the components of the mechanism are possible: sub-threshold writes can omit the pickle; a cache miss can turn that absence into the observed error; and a fresh process can succeed by replaying still-retained log entries into a cache before KNN. Source also shows why the simple “wrapper close caused a cold cache” explanation is insufficient: the wrapper does not stop the Chroma system, and first-count backfill normally seeds the pool. A later cache miss after backfill, or a different one of the two reader branches, remains unobserved.

**Recommended next action: seek separate approval for one disposable diagnostic native build if branch identity will change the next investigation.** Supported Python diagnostics cannot expose the missing failure-time Rust state. Minimum branch attribution is a stable branch code (`segment_directory_absent`, `metadata_pickle_absent`, or `id_to_label_empty`) and vector-segment UUID attached to the existing reader error; reaching `from_segment()` already implies a pool miss in the cited manager path. To evaluate the cache/persistence hypothesis rather than only name the branch, capture a bounded in-memory per-segment event ring containing pool insert/evict/close, backfill completion, persist attempt/success, and the persisted sequence watermark. Flush only on the matching failure. These fields can show whether a reader failure followed backfill and an eviction while persistence remained below threshold; they still provide a timeline, not proof of root cause. Preserve the Python error class and control flow. Do not log documents, embeddings, record IDs, pickle contents, or unrestricted paths. If no plausible branch result would change the engineering action, defer the build.

A separately approved build would need the exact source commit, a pinned Windows Rust/MSVC/SDK toolchain, reviewed source-only instrumentation diff, build logs, wheel tag/ABI check, and final wheel/extension hashes. The result would be a diagnostic artifact, not an official Chroma release binary. Instrumentation may perturb timing, so prefer bounded in-memory event capture attached only on failure over synchronous logging.

If approved later, cap the first runtime use at one execution of the isolated synthetic retrieval case that previously failed, with one test-owned index, one sequential query flow, no retries, no cache-size override, and full failure evidence retained before cleanup. Stop if source/build identity, artifact isolation, original error preservation, or child cleanup cannot be verified. If it passes without capturing the branch, stop as inconclusive; do not extend into a matrix. If it fails, preserve the branch and cache/backfill events and stop for a separate review before any additional run.

## Limits and independent blockers

- No HNSW root cause, failure probability, reliability qualification, or mitigation is established.
- No Chroma query, index access, native build, dependency install, or source modification occurred.
- The two-pin dependency proposal remains unadopted and full clean-install qualification remains incomplete.
- The natural concurrent artifact-publisher incident remains unresolved and independent.
- Chroma remains pinned to 1.5.0; migration is not authorized.
- Phase 6.12F bounded functional integration remains completed, but HNSW reliability qualification remains blocked.
- Unattended general-purpose deployment remains **NO-GO**.
