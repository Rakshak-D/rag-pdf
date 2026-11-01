# Isolated manual report retrieval smoke

This smoke extends the [manual report handoff preflight](report-handoff-preflight-operations.md)
and is bounded by the [Chroma store verification contract](chroma-store-verification-contract.md).

Run the local integration harness from the repository root:

```powershell
.\rag-env\Scripts\python.exe scripts/manual_report_retrieval_smoke.py
```

The harness creates a synthetic PDF, a semantic RunPlan, a fresh-only Chroma
index, and the builder's genuine successful report under temporary storage.
Deterministic injected text and image embedding adapters create and query the
three-dimensional fixture vectors. No production embedding model, model
download, Ollama request, or external API is used.

The harness selects the generated build explicitly with the existing report
handoff preflight CLI. It then starts the ordinary runtime with that exact
report path in a temporary child-process environment and checks the document
manifest, RunPlan fingerprint, and report-readiness response. The runtime is
stopped before the retrieval process starts. Its readiness remains report-level
evidence with `check_level: report` and `store_checked: false`.

A separate child process reconstructs the same validated RunPlan and calls the
established `retrieve_for_plan()` semantic path for one text query and one
image query. The injected query vectors match the deterministic build vectors.
The pipeline opens the persisted store, checks both collection counts against
the selected report before embedding, executes the modality queries, validates
the returned records and finite distances, and closes the wrapper on the same
thread that acquired the writer lease. The process exits after the smoke so
Chroma's cached internal system is not shared with another test process.

The harness records filesystem snapshots before the retrieval child, after the
retrieval wrapper closes, and after process exit. It reports added and removed
files, size changes, digest changes, timestamp-only changes, and directory
timestamp changes. It requires the selected report and application index
identity manifest to retain their original bytes, and requires the cooperating
writer reservation to be released on normal completion. It does not require
Chroma SQLite or HNSW files to remain byte-identical. Any observed Chroma
changes are evidence from this bounded fixture, not proof of which Chroma call
caused them or whether they indicate corruption.

One bounded local run on CPython 3.10.11, 64-bit Windows, and Chroma 1.5.0
returned one text hit and one image hit, with both observed collection counts
equal to the report's `1` and `1`. Between the pre-retrieval snapshot and both
the post-wrapper-close and post-process-exit snapshots, `chroma.sqlite3` had a
different digest with unchanged size. Four HNSW `data_level0.bin` and
`length.bin` files across the two segment directories had different digests;
four `header.bin`/`link_lists.bin` files had timestamp changes without digest
changes. No Chroma files were added or removed, and no file-size change was
observed. The report and application index manifest retained their bytes. The
cooperating reservation was acquired and released by the same child PID and
thread. This single fixture run does not attribute a specific write to open,
count, or query; the HNSW reader incident did not recur in this invocation.

Disposable negative fixtures cover identity mismatch, failed and partial
reports, missing index storage, malformed collection metadata, count mismatch,
an occupied writer reservation, an incomplete marker, and a query adapter
error. Tests leave failed evidence in the temporary workspace until cleanup;
the harness does not repair indexes or take over reservations. A count mismatch
must be rejected before the query adapter runs. A query error must release the
lease owned by that retrieval call. Reservation conflicts preserve the
pre-existing owner's file until the test releases only its own lease.

A successful smoke establishes that this particular synthetic index can be
opened and queried through the current retrieval path at this point in time,
and that observed collection counts matched its selected report during that
operation. It does not establish vector-content integrity, future retrieval
reliability, safety of arbitrary indexes, production-model behavior, or a
filesystem-read-only Chroma lifecycle. Phase 6.12C observed persistent Chroma
file changes during open/count windows and remains **NO-GO** for a verifier
advertised as read-only. Wrapper close releases the lease but does not stop
Chroma's cached client system; process exit is the lifecycle boundary used here.

The ordinary runtime still receives a manually selected report path. The
smoke does not activate a report, modify persistent runtime settings, start an
experiment, or add a retrieval API. The historical HNSW reader failure and
concurrent artifact-publisher intermittency remain unresolved. If an HNSW
error occurs, the child records its exact stage, traceback, Python and Chroma
versions, last checkpoints, lease state, and a bounded temporary filesystem
snapshot; the harness preserves that temporary diagnostics directory on
failure.
