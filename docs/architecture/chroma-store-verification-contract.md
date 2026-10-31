# Chroma store-verification contract gate

## Scope and decision

This document records a bounded, test-only investigation of the existing
`ChromaVectorStore.open_existing()` lifecycle. It does not add a verifier,
HTTP operation, frontend control, report selection, activation, runtime
configuration change, repair, or experiment execution.

**Decision: NO-GO for a store-verification operation described or treated as
read-only.** On the pinned environment, opening the synthetic persistent store
reproducibly changed bytes in Chroma's SQLite database and HNSW segment files.
The current wrapper also cannot close Chroma's client through a public client
API, and its lease is thread-bound and exclusive. The lease tests show useful
fail-closed behavior, but the observed persistent changes and the unresolved
HNSW reader incident leave the side-effect and completion contract too
uncertain for an operator-facing verification action.

The result is about this installed environment and the tested operations. It
does not establish data corruption, explain why Chroma changed those bytes, or
prove that every open has the same effects.

## Environment and fixture

The experiment used the repository's `rag-env` on 64-bit Windows:

| Component | Observed version |
|---|---|
| CPython | 3.10.11, 64-bit |
| Windows | Windows 10 build 26300 |
| Chroma | 1.5.0 |
| pytest | Repository test configuration |

Each test used a temporary PDF source root, a genuine `RunPlan`, a temporary
owned storage root, deterministic vectors, and two collections containing two
text records and one image record. The report and index manifest were genuine
schema-shaped test artifacts. No user corpus, production storage, models,
Ollama, remote service, or job database was used. The fixture creator ran in a
disposable child process and stopped its Chroma `System` after writing, so the
baseline was captured after the creator process had exited and the files were
quiescent. Chroma's product telemetry capture is disabled when pytest is
loaded; the tested code path used only local `PersistentClient` operations.

The focused contract test reports Python, platform, and Chroma versions and
captures bounded test-local tracebacks and filesystem snapshots. The test
artifacts live under pytest temporary directories only. Its pytest finalizer
stops and removes only parent-process Chroma systems whose persistent paths
are beneath that test's temporary root; spawned child systems end with their
own child process.

## Existing open and close lifecycle

The relevant implementation is in
[`chroma_store.py`](../../backend/vectorstore/chroma_store.py), with ownership
in [`writer_lease.py`](../../backend/config/writer_lease.py).

`open_existing(storage_root, run_plan)` currently proceeds as follows:

1. Verify the immutable `RunPlan` and resolve the configured source and
   storage roots as disjoint paths.
2. Validate the fixed `indexes/<fingerprint>` hierarchy, reject symlinked
   components, and check owned-path containment.
3. Reject a missing index directory before creating a writer reservation.
4. Acquire the per-fingerprint `WriterLease`, then claim store ownership.
   This is an **exclusive** reservation, not a shared-reader lock.
5. Reject the incomplete marker; require the index manifest and Chroma
   directory; parse the manifest and compare its schema, fingerprint, and
   document-manifest identity with the plan.
6. Construct `chromadb.PersistentClient`, list collections, require exactly
   `text_vectors` and `image_vectors`, obtain both collections, and validate
   their identity metadata and cosine-distance setting.
7. Return a wrapper exposing read methods and rejecting writes for an
   `open_existing()` instance.

The store open path does **not** validate collection record counts against a
build report. It does not validate the build report at all. Retrieval first
uses the shared report validator, opens the store, then reads text and image
counts and compares those values to the validated report before issuing a
query. Count comparison belongs to
[`retrieval.py`](../../backend/engine/retrieval.py), not to
`ChromaVectorStore.open_existing()`.

`ChromaVectorStore.close()` drops the wrapper's client and collection
references, releases the store claim, then releases the owned lease. The
installed `chromadb.Client` object has no public `close()` method. Chroma's
`SharedSystemClient` cache still contained the system after wrapper close in
the test process. The test did not call Chroma's internal `System.stop()` for
the verifier process because that is not the wrapper's public lifecycle
contract. A future operation must not interpret wrapper close as proof that
all Chroma internals have been shut down.

## WriterLease ownership and concurrency

`WriterLease` uses a filesystem reservation for one storage-root/fingerprint
pair. Its capability is bound to both the process ID and acquiring thread ID;
release verifies the private token and the same ownership context. A crashed
owner leaves the reservation in place. There is no PID-based stale-owner
takeover.

Observed with synchronization events and spawned processes:

| Scenario | Result |
|---|---|
| A writer lease is held, then `open_existing()` is attempted for that fingerprint | Rejected with `writer_lease_busy`; the existing owner file remains unchanged. |
| A child has an `open_existing()` store open, then a writer lease is requested | Rejected with `writer_lease_busy`. |
| A second same-fingerprint `open_existing()` is attempted in-process or from another process | Rejected with `writer_lease_busy`. |
| A different fingerprint is opened while the first fingerprint is held | Allowed; its separate index and reservation are independent. |
| The legitimate owner closes on its acquiring thread | The reservation is removed and a later acquisition/open succeeds. |
| `close()` is called from another thread | `writer_lease_owner_invalid`; the reservation remains, and the original thread can still close it. |

Thus cooperating writers and readers are serialized per fingerprint, not
shared. External processes that do not use `WriterLease` are outside this
coordination boundary. A future async service must perform lease acquisition,
all store operations, and close on one ownership thread. Separate threadpool
calls are not guaranteed to return to the same thread.

## Filesystem snapshot method and observations

For each of two planned cold-process trials, the test captured recursive
relative path, file type, size, modification time, and SHA-256 for regular
files before open, immediately after `open_existing()` returned, after both
`count()` calls, after a deterministic text query, after wrapper close, and
after the child process exited. It separately classified Chroma SQLite files,
WAL/SHM/journal sidecars, HNSW segment files, temporary files, and the
fingerprint reservation. The index manifest and report bytes were compared
before and after. The focused regression selection and final full-suite run
also passed this test on the final source state. Results below summarize the
final quiescent-baseline run.

The synthetic Chroma directory held nine files: `chroma.sqlite3` plus four
HNSW files for each of two collection segment directories (`data_level0.bin`,
`header.bin`, `length.bin`, and `link_lists.bin`). No WAL, SHM, journal, or
temporary files were present at the captured checkpoints. No Chroma path was
added or removed during a successful open, count, query, or close.

| Checkpoint interval | Observed filesystem evidence |
|---|---|
| Before open → `open_existing()` returned | The owned lease directory and `owner` file appeared. The `chroma.sqlite3` digest changed. HNSW `data_level0.bin` and `length.bin` digests changed across the two segment directories in both trials. No file sizes changed. The `indexes/<fingerprint>/chroma` directory mtime changed as expected from Chroma initialization. |
| Open returned → both counts captured | Further HNSW `data_level0.bin` and `length.bin` digests changed. `header.bin` and `link_lists.bin` had timestamp-only changes in the captured open/count windows. No file sizes or directory entries changed. The snapshot intervals do not prove that the synchronous `count()` calls themselves caused the writes; Chroma background work may run during an interval. |
| Counts → deterministic text query | No path, size, digest, or modification-time changes were observed in either trial. The query returned the expected two IDs. |
| Wrapper close | Only the owned lease directory and its `owner` file were removed. The `.writer-leases` parent directory mtime changed. No Chroma file size/digest changed. |
| Child process exit after normal close | No further path, size, digest, or mtime changes were observed. |

These same-size byte changes may be internal Chroma persistence/synchronization.
The experiment does not identify their cause or classify them as corruption.
It does establish that `open_existing()` plus metadata/count inspection
cannot be advertised as filesystem-read-only on this fixture and version.

## Validation and failure behavior

The focused contract tests exercise actual Chroma persistence and verify the
existing checks without changing production code:

- Missing index directories fail before a lease namespace is created.
- Missing Chroma directories, missing or malformed index manifests, wrong
  manifest identities, and an incomplete fresh-build marker fail closed and
  are not repaired.
- Missing or extra collections and incorrect collection identity metadata are
  rejected; the test-injected bad state remains in place after rejection.
- A report whose persisted text count is deliberately wrong can still be
  opened by `open_existing()`; retrieval rejects it when it compares store
  counts to the validated report. This confirms the layer boundary.
- Symlinked index hierarchy rejection is tested where Windows permits
  symlink creation. The platform-dependent symlink case is skipped when the
  OS denies link creation; other fixed-path containment checks remain active.

Failure to acquire a reservation leaves the current owner's marker untouched.
Failure after a successful lease acquisition follows the store's existing
release path; no repair or deletion of index/report evidence is attempted.

## Clean close and forced process termination

For clean close, a spawned child opened the store, read counts, executed one
deterministic query, closed the wrapper on the acquiring thread, and exited.
The reservation was removed. Chroma files and report/manifest bytes did not
change between wrapper close and process exit in the captured trial. The
Chroma system remained in its process cache after wrapper close until that
process exited; the wrapper does not expose a public Chroma shutdown call.

For forced termination, a separate child held the store open at a controlled
event. The parent confirmed a competing acquisition was rejected, terminated
only that child, and compared the storage snapshot immediately before and
after termination. In this test, no Chroma path, size, digest, or timestamp
changed during termination. The fingerprint lease directory and owner file
remained. A later `WriterLease.acquire()` and `open_existing()` both failed
with `writer_lease_busy`; no automatic takeover occurred. The report and index
manifest bytes remained unchanged. The test does **not** claim the index is
safe to reopen after the crash; the retained reservation deliberately blocks
that operation pending offline investigation.

## Historical HNSW observation

The documented error remains unresolved:

`InternalError: Error creating hnsw segment reader: Nothing found on disk`

The incident record in [`chroma-hnsw-incident.md`](../audit/chroma-hnsw-incident.md)
places prior failures at collection query, but contains no failure-time
snapshot identifying the exact disk state. The two cold-process trials in
this contract test succeeded through open, collection metadata, counts, and a
text query. The forced-termination case did not attempt to reopen because the
retained lease correctly blocked it. These passes neither reproduce nor fix
the historical incident.

If the error recurs, the next diagnostic must preserve the original traceback,
the exact stage (`PersistentClient`/collection open, count, or query), and a
bounded snapshot of the temporary test index. Do not remove the reservation,
repair the index, or infer a cause from a post-failure file listing alone.

## Proposed future verification levels and result shape

A future explicitly initiated operation should report the work it actually
completed, not a generic `ready: true`:

| Level | Evidence represented | Does not establish |
|---|---|---|
| `report` | Shared report-schema, path, status, persistence, manifest, and fingerprint checks. | Chroma was opened or store contents were checked. |
| `store_open_attempted` | The operation attempted a Chroma open under the fingerprint lease and records success or a stable failure code. | Collection metadata/counts passed. |
| `store_metadata` | Index manifest, exact collection set, and collection identity metadata passed. | Counts match the report, all vector bytes are intact, or queries work. |
| `store_counts` | Both collection counts were read and matched a validated report under the same held lease. | Vector/document content integrity or future query success. |
| `query_smoke` | A separately specified deterministic query completed for stated modalities. | All queries, embeddings, retrieval strategies, models, or future requests work. |

A future result should bind to the current manifest hash, expected index
fingerprint, explicit build ID, relative report reference, and validated
report identity. It should include the last completed stage, booleans for
whether Chroma was opened, collection metadata checked, counts checked, and a
query executed, observed counts only when actually read, a stable bounded
failure code, and an explicit completion state. Any `store_checked: true`
must mean the named store checks actually ran; it must not be used by the
current report-only inventory. No result should claim full vector-content
integrity, indefinite freshness, runtime/model readiness, or activation.

Any verification that opens Chroma has a mutation-capable side-effect profile
on this pinned build. That profile must be named in user-facing semantics and
accepted before implementation. Counts and query are separate levels; a
query-level check must not be smuggled into a count-only result.

## GO / NO-GO and missing guarantees

**NO-GO at this checkpoint.** The existing lease provides useful exclusion
for cooperating processes, clean close releases its own reservation, and a
crash retains fail-closed evidence. However:

1. Persistent SQLite and HNSW bytes changed during open/count intervals on the
   quiescent synthetic fixture. The exact Chroma internal cause and whether
   these changes are an acceptable maintenance write are not established.
2. The wrapper cannot call a public client close method. Its process-local
   Chroma system remains cached after wrapper close, while the lease has been
   released. The current code does not define a supported full client/system
   shutdown contract.
3. A forced process termination leaves an ambiguous reservation that blocks
   reopen. This is conservative, but there is no automated recovery and none
   should be added as part of verification.
4. The historical HNSW query failure remains unresolved. It did not recur in
   these two trials, so it is not a reproduced blocker for this test fixture,
   but it limits claims about query-level verification.
5. A cooperating lease cannot prevent a legacy or external writer that ignores
   the reservation. No immutable snapshot spans verification and later
   runtime use.

The next justified work is a separate design/approval checkpoint to determine
whether Chroma's observed persistence writes and system lifetime can be
bounded and explicitly accepted for an operator-triggered check. Until then,
keep report inventory and manual report handoff at `check_level: report`,
`store_checked: false`. Do not add an endpoint or UI action.

## Future implementation gate

If a later approved design proceeds, require at least:

1. A supported, bounded lifecycle for PersistentClient internals or a documented
   decision that the system lives for the containing process. All lease-bound
   operations must execute on one thread from acquire through release.
2. A documented side-effect budget based on repeated snapshots from a
   quiescent store, with stable tests for SQLite, sidecars, HNSW files, and
   cleanup. Any observed Chroma writes must be explained and explicitly
   accepted; no claim of read-only behavior is allowed.
3. Failure-injection tests for missing/mismatched metadata, wrong collection
   sets, count mismatch, HNSW open/count/query failures, close failure,
   concurrent writer rejection, clean shutdown, and forced process death.
4. A bounded result contract with distinct report/open/metadata/count/query
   levels and stable sanitized error codes.
5. Manual operator confirmation that verification can initialize Chroma and
   may change persistent bytes. No automatic retry, stale-reservation
   recovery, repair, activation, or experiment execution.
6. A separate explicit approval before any production API or frontend
   operation is implemented.

Until these conditions are reviewed, store verification remains disabled.
