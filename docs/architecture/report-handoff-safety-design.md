# Report handoff and activation safety design

## Decision summary

For the next operator-facing milestone, keep report handoff manual and use the
ordinary experiment runtime's existing validation path. A human may choose a
specific `report_match` candidate, then set
`RAG_WORKBENCH_INDEX_STORAGE_ROOT` and
`RAG_WORKBENCH_BUILD_REPORT_PATH` in the environment used to start the
ordinary runtime. The bootstrap service and browser do not write those
settings. The operator must check report readiness against the current source
and configuration, then rely on the normal retrieval boundary for persisted
store checks.

This is an explicit configuration handoff, not an active-index switch. It is
the smallest useful next step because the ordinary runtime already accepts a
trusted storage root and one report path. A matching report is not proof that
the store is sound, and this procedure cannot protect against arbitrary
external writers or filesystem tampering. If stronger pre-use store assurance
is required, the separate store-verification checkpoint below must establish
that contract before any activation mechanism is considered.

This document is a design only. It adds no selection, activation, runtime
configuration mutation, HTTP route, or experiment operation.

## Problem and existing boundaries

The system deliberately separates these facts:

1. `build_index(..., creation_policy="fresh_only")` creates a new
   fingerprint-owned index under the `WriterLease`, publishes its report, and
   finalizes its incomplete marker. Legacy builds retain their established
   behavior and may open an existing fingerprint-owned store.
2. `BootstrapJobManager` records durable job metadata. It does not designate
   an active report.
3. `IndexInventoryService` discovers bounded report candidates under the
   trusted `build-artifacts/<build-id>/build-report.json` hierarchy. It
   recomputes the source manifest and canonical `RunPlan`, rejects unsafe
   paths, and returns report-level classifications only.
4. `IndexReadinessService` checks one configured report against a current
   manifest and a plan. Its result has `check_level="report"` and
   `store_checked=false`.
5. The ordinary runtime requires `RAG_WORKBENCH_BUILD_REPORT_PATH` at startup.
   Its settings also identify `RAG_WORKBENCH_INDEX_STORAGE_ROOT`. Runtime
   composition checks the report's existence and required hierarchy; the
   readiness operation validates report compatibility.
6. Retrieval revalidates the report for the submitted plan, calls
   `ChromaVectorStore.open_existing()`, validates the index manifest and
   collection metadata, and compares persisted text/image counts with the
   report. Query execution then uses the configured adapters and collections.

There is no active-report registry, report-selection API, or general index
catalog. Inventory ordering is lexical by build ID and conveys no recency.
Multiple compatible reports remain distinct candidates for a human to
consider. The inventory viewer only displays relative references and
identities; it does not turn one into a runtime setting.

See [read-only inventory](index-inventory.md),
[bootstrap HTTP operations](bootstrap-http-operations.md), and
[local runtime](local-runtime.md) for their current contracts.

## Handoff options

| Option | Safety properties | Complexity and operator use | Recovery and limitations |
|---|---|---|---|
| **A — Manual configuration handoff** | Reuses the existing runtime's trusted roots, report validator, current-plan checks, and retrieval store checks. There is no pointer write that can silently change the selected report. | Lowest complexity. The operator explicitly sets the two existing environment variables and restarts the ordinary runtime. A relative report reference can guide the operator, but the trusted storage root remains separately configured. | Revert the two environment values and restart if startup/readiness fails. The report is reread by the runtime, but no report-content digest is pinned across the operator's inspection-to-use interval. The writer lease coordinates cooperating operations while held; it does not make an index immutable between retrievals or control external writers. |
| **B — Explicit handoff artifact** | A reviewable data file could bind a candidate to a manifest hash, index fingerprint, build ID, relative report reference, report digest, and configuration identity. A consumer could detect a changed report if it rechecks the digest. | More tooling and another file the operator must understand. If the runtime ignores it, it is advisory and can become stale. If the runtime consumes it, it becomes a new selection contract and source of truth that needs schema, lifecycle, and compatibility policy. | The artifact must be regenerated after source/config/report changes. It still does not prove Chroma readability or prevent a later index mutation. Recovery includes inspecting and replacing the handoff file without deleting build evidence. No existing runtime contract consumes such an artifact. |
| **C — Managed active-report registry** | Could centralize an explicit selected report and require a compare-and-swap update with pinned identities. It could make runtime startup selection repeatable if all readers honor the registry. | Highest complexity. It introduces a persistent pointer, write authorization, API/CLI mutation, runtime read synchronization, and a policy for multiple runtime instances. It is not required for the operator's immediate goal. | Requires atomic updates, crash semantics, read/write coordination, rollback, restart/version handling, and stale identity checks. It does not protect a mutable Chroma index unless index mutation is separately prevented. Recovery must never delete or repair the selected index. No such registry exists today. |

### Recommendation

Choose **Option A** for the next implementation milestone. Keep the operator's
choice explicit and local: inspect the candidate, use its safe relative
reference with the separately trusted storage root, set the existing runtime
variables manually, restart the ordinary runtime, and run its readiness check
before submitting work. Do not write environment variables from the browser or
bootstrap API.

Option B is not recommended now because the current runtime has no handoff
artifact input. A detached file would not strengthen enforcement; wiring it
into startup would create a new activation/configuration contract. Option C is
premature: it expands the trust boundary and still cannot make the current
fingerprint-owned Chroma directory immutable. Neither complexity is justified
before a specific need for managed multi-process selection is established.

Option A has a defined limit: it provides explicit human configuration and
uses validation at runtime boundaries, but it does not provide a transaction
between inventory display and runtime use. The runtime must not label a
report-level match as store-ready. A future managed-selection proposal must
first address this limit and the legacy writer behavior described below.

## Trust boundary and evidence

### Report-level compatibility

The inventory service uses the trusted server-side source and storage roots,
the current document manifest, and a canonical `RunPlan`. Its shared report
inspection validates the report schema, build ID and artifact hierarchy,
successful status, `partial_persistence == false`, report identities, and
nonnegative persisted counts. It rejects unsafe candidate paths and marks a
present fresh-build incomplete marker as incomplete evidence. A
`report_match` means only that the report passed those report checks for the
current plan at that inspection point. The response explicitly says
`check_level="report"` and `store_checked=false`.

The report includes the index fingerprint and document manifest identity, but
does not pin the complete `RunPlan.configuration_snapshot_hash`, a report
content digest, or a store-content digest. The inventory response does not
expose the trusted absolute roots. Candidate references are relative and must
be resolved only under the configured storage root by trusted local code.

### Store-level verification

The current retrieval path checks the store when it opens it: the index
manifest, expected collection names, collection metadata, and persisted counts
are checked against the plan/report. `open_existing()` rejects incomplete
fresh-build state and exposes no writable upsert capability. This is stronger
than report validation, but it is not a content hash of every vector or
document and does not prove future queries will succeed.

`open_existing()` is not a pure filesystem read. It calls
`WriterLease.acquire()` for the canonical storage root and fingerprint. The
lease creates a reservation directory and owner file, and is exclusive rather
than a shared-reader lock. It is held while the store is open and released on
`close()`. Chroma is then initialized through `chromadb.PersistentClient`,
which opens its persistent storage and performs collection list/get and
metadata checks. The public store is read-only, but the code does not open the
underlying Chroma files using an OS-level read-only mode or promise that
Chroma internals never write metadata, migrate state, or create temporary
files. Therefore, calling `open_existing()` solely for inventory would add
side effects and contention; inventory correctly does not call it.

While a repository build or store operation holds the fingerprint lease,
another cooperating `open`, `create_new`, `open_existing`, or builder for the
same root/fingerprint cannot acquire it. A process crash leaves the reservation
in place; there is no PID-based takeover. A fresh build crash also leaves the
incomplete marker, and fresh-only execution will not repair or overwrite that
directory. Recovery requires offline investigation of the exact owned
reservation and index evidence. Arbitrary external Chroma processes that do
not use this protocol are not controlled.

The exclusive lease also means concurrent retrievals against one fingerprint
may contend with each other, not just with writers. Multiple runtime
instances are not a shared-reader architecture. The current design does not
promise lock-free or concurrent Chroma reads.

### Runtime execution readiness

The ordinary runtime's environment must point at the intended trusted storage
root and exact report path. Runtime startup requires that the index root and
report exist and that the report path has the expected artifact hierarchy; it
does not, by existence alone, establish plan compatibility or collection
usability. The report readiness API recomputes the current manifest and plan
and validates the configured report, but remains report-level. Retrieval is
the boundary that opens Chroma and compares collection metadata and counts.
Semantic retrieval additionally requires the configured embedding adapters;
generation and external services have their own runtime requirements.

Do not describe any report candidate or readiness response as proof of:

- an active or selected index;
- readable persisted collections;
- successful future retrieval;
- loaded embedding/reranking models or a working generation backend; or
- an experiment having started or succeeded.

## Selection identity and freshness

| Identity/evidence | Available today | Handoff meaning |
|---|---|---|
| Trusted source namespace | Bootstrap server configuration; not returned to the browser | Keep server/operator configuration authoritative. Do not accept a browser-supplied source path as authority. |
| Trusted storage namespace | Bootstrap/runtime configuration; not returned by inventory | Operator must know and preserve the same root when composing the report path. A fingerprint alone does not identify a filesystem namespace. |
| Current document manifest hash | Preview and inventory response | Point-in-time identity of source content; inventory checks it before and after its scan. It can change immediately after the response. |
| Expected index fingerprint | Canonical `RunPlan` returned by preview/inventory | Binds the index-relevant configuration and source manifest under the established fingerprint algorithm. Never compute it in the browser. |
| Configuration snapshot hash and run fingerprint | Present in server-built `RunPlan` | Distinguish the full resolved configuration and execution plan. They are not fields in the build report identity contract or inventory candidate. |
| Build ID and relative report reference | Inventory candidate when the directory name is valid | Identify one report location. Build ID is not a timestamp and does not mean newest. Resolve only beneath the trusted storage root. |
| Validated report content digest | Not currently returned by inventory or pinned by ordinary runtime settings | Needed if a future handoff must detect byte-for-byte replacement between inspection and use. A digest is evidence of equality, not authenticity by itself. |
| Store content/collection digest | Not available | Counts and metadata catch some inconsistencies, but same-count content replacement is not detected by a report digest. Requires a separate store-integrity contract. |
| Verification timestamp | No timestamp is part of the inventory page contract | A local observation time may be shown as such, but it must not be treated as a freshness guarantee or ordering key. |
| Operator confirmation | No server-side selection record exists | Manual environment configuration is the explicit choice. Any future API selection must record the exact identity tuple and idempotency semantics. |

A future handoff must bind at minimum to the trusted source/storage namespace,
RunPlan schema and configuration snapshot identity, current manifest hash,
index fingerprint, build ID, relative report reference, and a validated report
content digest if replacement detection is required. The server must recompute
identities and read the report under the trusted roots. The client must not
author an identity or path. A report digest does not establish store integrity.

Revalidate at the handoff boundary and again in the ordinary runtime. The
selection becomes invalid or unusable as follows:

- Source PDF changes: the current manifest differs; readiness/plan validation
  fails. Inventory must be requested again. Do not silently substitute the new
  manifest.
- Relevant configuration changes: the canonical index fingerprint or full
  run identity changes; a report for the old plan does not match.
- Report bytes or path change: the runtime rereads and validates its configured
  report. There is no pinned report digest today, so replacement with another
  internally valid report of the same identities is not detected as a byte
  change. A future digest-pinning contract is needed if that distinction is
  material.
- Index contents change: metadata and count checks catch known classes of
  inconsistency; they do not detect every same-count content substitution.
  Repository writers are serialized while the lease is held, but legacy
  builds may mutate the existing fingerprint-owned store sequentially. External
  writers are outside the protocol.
- Build is interrupted or its incomplete marker appears: inventory classifies
  the evidence incomplete and `open_existing()` rejects the incomplete fresh
  store. No repair or deletion follows.
- A path is redirected: inventory rejects symlinked candidate paths and checks
  containment. Runtime settings reject symlink components at composition;
  store and lease paths are checked again at their boundaries. A non-cooperating
  actor that changes filesystem paths between checks remains outside a
  universal atomic filesystem guarantee.
- Runtime restarts with different settings: startup validates the configured
  roots/report layout; report readiness and retrieval validate the new plan.
  The runtime has no remembered active pointer to reconcile.

## Operator workflow for the recommended handoff

1. Connect to bootstrap mode and preview the complete resolved configuration.
2. Request inventory for that preview and review one explicit `report_match`
   candidate. If multiple candidates match, make an intentional human choice;
   do not use the first or lexically greatest ID as a recency proxy.
3. Confirm the candidate reference is relative and is exactly under
   `build-artifacts/<build-id>/build-report.json`. The inventory response is
   report-only and does not reserve the path or index.
4. Stop or otherwise ensure no build is operating on the selected fingerprint.
   Wait for a running bootstrap job to reach a recorded terminal state. Do not
   assume a timed-out shutdown stopped a worker; unresolved instance ownership
   is fail-closed. Do not allow an external Chroma process to write the store.
5. In the shell/environment used for the ordinary runtime, manually set
   `RAG_WORKBENCH_INDEX_STORAGE_ROOT` to the trusted index root and
   `RAG_WORKBENCH_BUILD_REPORT_PATH` to that root joined with the candidate's
   relative reference. Keep `RAG_WORKBENCH_DATA_DIR` and the resolved config's
   source identity consistent. Do not ask the browser to write these settings.
6. Start `python -m backend.app` as one ordinary runtime instance for this
   local workflow. Startup failure is not repaired by deleting or changing
   index files.
7. Call the ordinary runtime's authenticated index-readiness operation with
   the current complete configuration and expected manifest identity. Require
   a report-level match for the intended plan. This still does not check
   Chroma.
8. Submit an experiment only through the established explicit experiment
   workflow. Its retrieval path rereads the report, opens the existing store
   under the fingerprint lease, and validates collection identity and counts.
   If retrieval fails, stop and preserve all report/index evidence. Do not
   label a prior report match as successful retrieval.
9. If the wrong report was configured or startup/readiness fails, restore the
   previous environment values and restart the runtime. No index rollback is
   needed because this handoff did not change index data or write an active
   pointer.

The report path is one runtime setting, not an index switch shared across
processes. Restarting with another setting changes that process's configured
report only. It does not mutate the bootstrap service, job database, build
reports, or index.

## Threat and failure scenarios

| Scenario | Safety invariant and detection point | Expected behavior and rollback | Covered today / missing |
|---|---|---|---|
| 1. Compatible report, missing Chroma directory | A report match is not store readiness. `open_existing()` checks the index hierarchy after report validation. | Retrieval fails with its bounded store error; preserve the report. Revert runtime settings if this was the wrong choice; no index rollback. | Retrieval detects absence. Inventory intentionally does not check the store. |
| 2. Report counts disagree with collections | Returned records must not exceed validated report counts; retrieval compares both actual collection counts with the report. | Retrieval fails; no automatic repair or count rewrite. Preserve both sides for diagnosis. | Count mismatch is checked during retrieval. There is no separate pre-experiment store verifier. |
| 3. Report matches, source changes afterward | A plan must use the current manifest. Readiness and plan preparation recompute it. | Reject with manifest mismatch/change; preview and inspect again explicitly. No rollback. | Current manifest is checked by readiness/inventory; the response is point-in-time. |
| 4. Symlink redirects candidate or report | Trusted roots and fixed artifact hierarchy must remain contained. | Inventory classifies/rejects unsafe paths; runtime/store boundary fails closed where its path checks apply. Do not follow or clean the link automatically. | Inventory checks symlink components. Runtime/store checks exist at boundaries; races from external path replacement cannot be eliminated universally. |
| 5. Report replaced between inventory and use | Use the exact configured candidate under the trusted root and revalidate its content and identity at use. | Readiness/retrieval reread the report and reject malformed, failed, partial, or mismatched content. Revert the report setting if invalid. | Revalidation exists, but no report digest is pinned across the gap; a valid same-identity replacement is not identified as a replacement. |
| 6. Multiple compatible reports | Human choice must name one build ID/reference; order is not recency. | Show all candidates; operator explicitly selects one outside the current inventory UI. No automatic “newest” choice or rollback. | Inventory provides bounded lexical pages. The ordinary runtime accepts only one configured report path. |
| 7. Build runs while inventory is inspected | Inventory is point-in-time and must not imply a reservation. | A changing/temporarily unavailable candidate is classified or the scan fails; request a fresh scan after the job is terminal. No rollback. | Manifest is rechecked before/after; storage enumeration is bounded. No writer lease is intentionally acquired. |
| 8. Prior build crashed with writer reservation | Never steal a reservation based only on PID or age. | Store opening fails closed. Preserve marker and index; require offline investigation. No automatic rollback or cleanup. | `WriterLease` is fail-closed; no automatic stale takeover exists. |
| 9. Incomplete marker beside vectors | An incomplete fresh store cannot be treated as complete. | Inventory reports `incomplete`; `open_existing()` rejects it. Preserve evidence; no repair. | Both boundaries check the marker. |
| 10. Operator chooses failed/interrupted build | Only a validated successful, nonpartial report can match. | Inventory classifies failure/incomplete/invalid; retrieval rejects failed/partial reports or missing/incomplete store. Do not configure it as a successful handoff. | Report validator and inventory classification cover these states; external manual path entry is revalidated. |
| 11. Runtime startup fails after configuration change | A configured path is not proof of compatibility. | Keep storage untouched; restore previous shell values and restart. | Startup checks roots, report existence, and hierarchy. Full compatibility is checked later by readiness/retrieval. |
| 12. Future activation request loses its HTTP response | A client must not create another selection or assume failure. | No activation route exists today. Any future mutation needs durable request identity plus read-after-write inspection and a defined rollback/previous-value policy. | Not applicable today; entirely missing for Option C. |
| 13. Second runtime uses the same storage root | All cooperating store operations must honor the same fingerprint lease. | Lease acquisition serializes store opens; contention may fail a retrieval. Do not promise concurrent reader support or allow an unmanaged writer. | Per-fingerprint lease is exclusive for operations while held. There is no runtime-wide singleton or active-report registry. |
| 14. Browser disconnect during a proposed selection | Disconnect must not imply selection or clear server state inconsistently. | No selection request exists; inventory is read-only. A future mutation must use idempotency and readback, not implicit retries. | Current viewer clears in-memory inventory on disconnect; no activation behavior. |
| 15. Stale frontend response presents an old candidate | Candidate data must remain bound to the active connection, config, preview identities, and page request. | Discard stale response; require a fresh explicit inventory request. No report selection should occur from stale state. | Frontend generation guards invalidate on config, preview, connection, route, and manifest changes; strict client validation checks response identities. |

Rollback here means restoring operator-controlled runtime environment values
and restarting when appropriate. It never means deleting an index, report,
incomplete marker, or writer reservation. Option C would require a separate
transactional rollback design that restores only a prior pointer after
revalidation; it must not delete data.

## Chroma verification: explicit boundary

Do not add `open_existing()` to ordinary inventory or call it from a generic
preview. It acquires the exclusive fingerprint reservation, initializes a
persistent Chroma client, and can participate in internal storage setup or
metadata behavior. It may also block a cooperating builder or another
retrieval. Public upserts are disabled, but this is not an OS-enforced
read-only mount.

If operators need a pre-use store check before any experiment, design a
separate explicitly initiated verification operation with a truthful label
such as “store verification attempted.” It must use the same writer lease in
the thread that opens and closes the store, check the incomplete marker,
manifest, exact collections, metadata, and actual counts, bound its duration,
and preserve the HNSW exception as an unresolved failure. Before calling it
read-only, test for Chroma filesystem changes on the pinned Windows
environment and define behavior for active builds, stale reservations,
process termination, missing indexes, and concurrent runtime requests. If
these side effects cannot be bounded, keep the operation disabled and use
retrieval as the authoritative store boundary.

The historical Chroma HNSW reader failure (`Error creating hnsw segment
reader: Nothing found on disk`) remains unresolved. It must not be attributed
to, or declared fixed by, report handoff work. The intermittent concurrent
artifact-publisher integrity incident is also unresolved and is unrelated to
report selection; this design proposes no fix for either incident.

## Proposed implementation checkpoints

### Phase 6.12B — Manual handoff preflight

**Scope:** Add a trusted local, read-only command that lets an operator name a
build ID and revalidates that candidate against a complete resolved
configuration and current manifest. It prints a bounded report-level result
and a manual instruction using the existing environment variable names. It
does not write environment variables, create a handoff file, start a runtime,
open Chroma, or select an active report. The operator still manually applies
`RAG_WORKBENCH_INDEX_STORAGE_ROOT` and the joined relative report reference.

**Likely files:** a focused `scripts/report_handoff_preflight.py`, a narrow
explicit-build-ID inspection method on `IndexInventoryService`, tests under
`tests/unit/` and `tests/integration/`, and a short operations guide. Reuse
`ResolvedRunConfig`, manifest and RunPlan builders,
`IndexInventoryService`/the shared report validator, and existing
path-containment helpers. The implementation must not perform a broad or
unbounded search to find the selected report.

**Boundary and CLI contract:** require explicit trusted source/storage roots,
a complete config file, confirmed manifest and fingerprint hashes, and a
32-character lowercase build ID. Do not accept an arbitrary report path or
choose a build automatically. Recompute source manifest and fingerprint;
refuse nonmatching, failed, partial, incomplete, oversized, symlinked, or
unavailable reports. Inspect only fixed index-directory metadata and the
fresh-build/writer-reservation markers; do not open Chroma or acquire a lease.
Print only a relative report reference and bounded metadata. Any index
directory observation remains distinct from store verification and does not
pin bytes after the command exits.

**Tests:** deterministic configuration/identity cases; missing, replaced,
malformed, symlinked, and wrong-hierarchy reports; source changes; multiple
matching candidates with explicit selection; minimum index metadata; no
filesystem writes; no WriterLease, Chroma client, model, or network
construction; output contains no credentials. A subprocess integration test
should verify the bounded CLI contract and that ordinary runtime settings are
not written or changed.

**Failure injection:** change the source or report between candidate
enumeration and final validation; inject path-resolution and report-read
errors; confirm bounded errors and no mutation. If a stable report digest is
later required, stop and specify how the ordinary runtime will enforce it
before implementing any digest-only artifact.

**Manual validation:** inspect a synthetic compatible candidate, set the two
existing runtime settings manually in a disposable shell, start the ordinary
runtime, query readiness, then restore the shell. Confirm no bootstrap API
setting or persistent state changed.

**Stop conditions:** no output that implies Chroma readiness; no automatic
environment update; no arbitrary path input; no report digest claim unless a
consumer enforces it; no index writes or runtime launch.

The implemented command contract and its stable errors are described in
[read-only report handoff preflight operations](report-handoff-preflight-operations.md).

### Phase 6.12C — Store-verification contract gate

**Scope:** Before adding a store verifier, establish experimentally whether
`open_existing()` and the pinned Chroma version modify persistent files and
how exclusive lease contention affects runtime retrieval. This is initially a
bounded contract/test checkpoint, not an API or UI feature.

**Likely files:** focused tests around `backend/vectorstore/chroma_store.py`,
possibly a separate architecture note, and a disposable test-only harness.
Do not change normal retrieval semantics or Chroma dependencies in this gate.

**Tests:** real temporary Chroma store snapshots before/after open/count/close;
active WriterLease contention; missing and incomplete stores; forced process
termination; stale reservation preservation; repeated readers; and HNSW
failure recording. Use actual Chroma persistence and bounded runs.

**Stop conditions:** any unbounded Chroma side effect, inability to preserve
the reservation protocol, unclear crash behavior, or reproduced HNSW failure
without an established disposition keeps a separate verification operation
disabled. Do not call the operation read-only based only on blocked upserts.

### Phase 6.12D — Activation decision, only if still needed

**Scope:** A separate architecture and approval checkpoint deciding whether a
managed active-report registry solves a demonstrated operator need. It must
precede any mutation implementation. Revisit legacy in-place builds,
immutable index generations, multiple runtimes, Windows atomicity, permissions,
and arbitrary external writers.

**Likely files:** design documentation first. Any later registry schema,
API/CLI, or runtime reader belongs to a separately approved milestone and
must not be bundled with inventory or experiment submission.

**Required tests if approved:** compare-and-swap selection updates, durable
idempotency after lost responses, crash before/after commit, readers during
updates, stale report and store identities, rollback to a verified prior
selection, multiple runtime processes, symlink/path attacks, active builds,
and no deletion of old data.

**Stop conditions:** no proof of atomic selection and reader coordination; no
clearly specified permissions; no handling for mutable legacy indexes; or
any path that can silently change the report used by an already-running
runtime. Do not implement a mutating activation endpoint until those issues
have a separately reviewed design and explicit approval.

## Unresolved questions and hard blockers

- Should a future handoff pin report bytes, and if so, how will the ordinary
  runtime receive and enforce that digest without creating another source of
  truth?
- Is report identity plus collection metadata/counts sufficient, or is a
  store-content integrity contract required to detect same-count changes?
- Should legacy `build_index(..., creation_policy="legacy")` remain permitted
  to mutate an index after it has been selected for runtime use? The current
  lease only excludes simultaneous operations; it does not freeze an index
  between requests.
- Is pre-experiment store verification worth Chroma's persistent-client and
  exclusive-lease side effects? This must be established on the pinned
  Windows runtime.
- Is multiple-runtime access to one fingerprint an intended use case? The
  current exclusive lease can make overlapping retrievals contend.
- Which process boundary is trusted to keep the bootstrap and runtime storage
  roots stable against local administrative changes?

Until these are answered, the design supports a deliberate manual report
handoff with revalidation and honest failure behavior; it does not promise an
immutable active index or protection from non-cooperating local processes.

## Explicit implementation approval gate

This design authorizes no activation implementation. Phase 6.12B may be
proposed as a separate read-only preflight milestone. Any feature that writes
an active pointer, updates runtime settings, pins a selected report for future
processes, restarts a runtime, or submits an experiment requires a separate
reviewed design and explicit implementation authorization. The ordinary
runtime's required configured-report startup contract and the existing
retrieval boundary remain unchanged.
