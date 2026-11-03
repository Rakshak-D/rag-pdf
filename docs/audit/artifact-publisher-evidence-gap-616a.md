# Phase 6.16A — Artifact Publisher Evidence Coverage

**Review date:** 2026-11-03
**Starting checkpoint:** `ed45804f2dec9d93a8e0e36a0c03d0bc8e8fb577` on `production-evaluation-workbench`
**Prior checkpoint:** `5cddbe7d24e1f6727bdd0f9c275cc5f1c1703163`
**Decision:** **NO_MATERIAL_CAPTURE_GAP**

This is a static audit of the checked-out publisher source and opt-in diagnostic
harness. No test, publisher reproduction, service experiment, or concurrent
matrix was run for this review. The existing two-pin `requirements.txt`
candidate remains a deferred, unstaged proposal; this report does not qualify
or adopt it.

## Audited source identities

The source and diagnostic files below were read from the Stage A checkpoint,
which matched the remote feature head during this audit. These Git blob IDs
identify the reviewed contents:

| File | Blob ID |
|---|---|
| `backend/experiments/artifact_store.py` | `b7ef0e46307672d74b90b8b50bd13c5e898829c0` |
| `tests/diagnostics/artifact_publisher.py` | `572cc7c9b7f324d7859f3d66a00f04510d0c227f` |
| `tests/unit/test_artifact_publisher_diagnostics.py` | `25a08aeffd6ef487d869cfc9cfafc883e0cbd36c` |
| `tests/unit/test_artifact_store.py` | `e43dd379cf7160c86b004b712700c16162aa53cd` |
| `scripts/diagnose_artifact_publisher.py` | `6457e706cf7fef92c8cc2206ea7a0c8e047a7e93` |
| `docs/audit/artifact-publisher-incident.md` | `758c0b0937855255e871add7e3c484b6336fdd2e` |

## Incident evidence and limits

The incident record describes an intermittent concurrent
`ArtifactIntegrityError`, but the original exception message, complete
traceback, cause/context, artifact ID, worker identity, gate state, destination
snapshot, temporary-file state, and catalog outcome were not retained. Eight
later diagnostic attempts passed and did not establish a failure rate or
resolve the incident. The historical root cause remains unknown. See the
[incident record](artifact-publisher-incident.md#original-incident-evidence),
especially its sections on the original evidence and the bounded matrix.

`ArtifactIntegrityError` is not a unique publication-stage signal. In the
current [artifact store](../../backend/experiments/artifact_store.py), it can
arise during evaluation serialization stability or ablation plan/serialization
validation, destination containment checks, and validation of an existing
destination after `os.link` reports `FileExistsError`. A different `os.link`
`OSError` is wrapped as `ArtifactStoreError`; directory-creation errors are
also wrapped. Therefore neither the exception class nor its text establishes
that a hard link ran, that an artifact was published, or that the historical
failure was caused by the publisher's link call.

## Coverage review

| Failure boundary | Existing capture | Evidence limitation | Existing regression | Remaining gap |
|---|---|---|---|---|
| Save-time validation before `_publish` | The concurrent worker handler retains the original exception object in-process and records its concrete type, message, formatted traceback, cause/context text, worker index, process/thread IDs, checkpoint, stage label, and observed-order index. The traceback identifies the actual source line for validation errors. | The structured `operation_stage` is the last instrumentation event, not a guaranteed exact internal sub-step. For example, an ablation plan mismatch can occur before serializer instrumentation; the traceback remains the precise source evidence. | `test_concurrent_publication_helper_preserves_first_prelink_exception` injects an `ArtifactIntegrityError` and checks that the helper surfaces its type, traceback, and original exception as the assertion cause. It does not write JSON evidence in that test. Generic persisted-failure tests cover exception capture and retention. | No dedicated persisted-JSON test for this exact subclass; static flow shows the same generic capture path. This is a narrow test-coverage omission, not a demonstrated retention defect. |
| Destination validation (`_destination`, `_check_component`) | Instrumented operation events identify `_destination`; the worker traceback and exception fields are retained. The wrapper re-raises the original exception. | Nested validation details are not a separate structured reason code. Event history and traceback provide the boundary and source location. | `test_unmarked_publisher_boundary_failure_with_peer_release_is_indeterminate` verifies an unmarked boundary error is retained and not promoted to natural evidence; `test_corruption_is_rejected_and_never_repaired` covers integrity rejection outside the concurrent diagnostic path. | No evidence-retention defect found. |
| Existing destination and content verification (`_existing_or_raise`, `_bounded_read`) | The event sequence records the wrapped `_existing_or_raise` boundary; the exception traceback, cause/context, and post-join destination digest/size/existence are retained when all workers stop. | Snapshot is after joins, not atomic at throw time. The helper does not expose every `_bounded_read` internal branch as a separate event. | Existing corruption test rejects mismatched content without repair; generic boundary-failure evidence tests preserve error records. | No evidence-retention defect found; the post-join snapshot cannot prove the exact file state at exception time. |
| Temporary-file write, hard-link, and cleanup | Events retain temporary creation and fsync boundaries; the test-local `os.link` proxy records arrival, gate release and direct built-in link exceptions. Worker errors and a bounded post-join scan retain temporary-file names/types/sizes. | Cleanup errors in production `_publish` are suppressed. If workers remain alive, post-join artifact/temp inspection is explicitly skipped. Snapshot is bounded to the expected destination parent and 64 entries. | Tests cover controlled link faults, gate timeouts, injected/peer failures, and temporary cleanup; the historical natural error is not reproduced by those injections. | A suppressed cleanup failure is only observable through the post-join state when workers have stopped; this limit is recorded, not hidden. |
| Worker ordering and peer outcomes | Every worker exception is retained; `observed_order` and `first_failure_worker_index` mean first worker entering the exception-capture lock, not physical throw order. The record includes all worker outcomes, peer failure, final-link arrivals, gate release state/reason, and alive-after-join flags. | Simultaneous throw ordering is not observable. Threads that remain alive prevent a complete event/artifact snapshot and make evidence indeterminate. | Mixed injected/gate, cascading failure, callback failure, and unmarked-boundary tests exercise conservative classification and peer release. | No gap for the stated first-observed ordering contract. |
| Exception provenance and classification | Exact test gate/injection markers are distinguished. The only `natural_publisher_exception_evidence` classification requires an exact `ArtifactStoreError` with the same built-in `os.link` `OSError` as its explicit cause, plus a matching link event for the same worker/process/thread. Other exceptions, including `ArtifactIntegrityError`, remain `indeterminate_failure`. | A raw `ArtifactIntegrityError` is retained but is not classified as natural solely from its class, text, traceback, or event. Synthetic code that raises an unmarked `ArtifactIntegrityError` also remains indeterminate; its traceback can show the test injection site. | Classifier tests reject malformed, mixed, incomplete, unmarked, and contradictory evidence; explicit gate and injected-marker tests remain separate. | No false-positive natural classification found. A captured AIE would need source/traceback review before any causal claim. |
| Child output and evidence persistence | The runner sets opt-in diagnostic variables, launches a fresh child, retains combined pytest output up to 2 MiB, validates child success records, locates one failure JSON, and classifies missing, malformed, timed-out, or contradictory evidence conservatively. | If evidence writing fails, the helper reports the evidence error and preserves the original exception as the raised assertion cause; that failure is not reclassified as natural. Exception text/cause/context are bounded strings, not serialized exception objects. | Startup, timeout/reaping, crash, unavailable-output, serialization/write-callback, and child-record tests cover these failure modes. | No material gap for retaining an actionable first failure. |
| Artifact/catalog boundary | These concurrent diagnostics exercise `ExperimentArtifactStore` directly. They do not register a catalog row. The service's publication and catalog registration remain separate operations. | The harness cannot establish catalog state because this scenario does not use a catalog. | Separate service/catalog tests cover persistence and registration behavior, not this historical publisher incident. | Catalog participation in the original incident remains unknown; it is outside this direct-store diagnostic's evidence. |

## What a future natural `ArtifactIntegrityError` would establish

With the opt-in trace enabled in the existing isolated concurrent test, an
`ArtifactIntegrityError` would be retained as its exact exception type with
its worker traceback, cause/context text, checkpoint, first-observed order,
peer outcomes, link-gate counts/state, and (if all workers stop) a bounded
post-join destination and temporary-file snapshot. The exception object and
its live traceback chain are also retained inside the child until the helper
raises its assertion from the original exception. The parent keeps the child
output and returns an indeterminate classification if evidence is absent,
malformed, mixed, truncated, or incomplete.

The worker's structured stage label is a last-observed diagnostic stage and
can be coarser than the exception's exact internal validation operation. The
event sequence records wrapped operation names; the untruncated traceback is
the source-level locator. This is sufficient to preserve actionable evidence
for subsequent review without converting an AIE into proof of a natural hard-
link failure. AIEs remain indeterminate under the classifier because the
existing positive natural rule is deliberately narrower. Marker-based injected
faults and marked harness timeouts have their own classifications; mixed or
unmarked cases remain indeterminate.

The snapshot is deliberately post-join, bounded, and confined to the test-
owned store. It records hashes/metadata and whether bytes match the expected
synthetic payload, not artifact contents. There is no catalog snapshot in this
direct-store scenario. Error strings are length-bounded; they are not a
general-purpose redaction system, so the diagnostic should remain confined to
the disposable test workspace. No user document or embedding is part of these
synthetic publisher tests.

Existing tests cover the relevant generic exception, worker, provenance,
evidence-write, and post-join mechanisms. One test injects an AIE and verifies
the original exception is surfaced, but does not persist that particular AIE
as JSON. Because the production diagnostic path captures `BaseException`
generically and the persisted schema is exception-class agnostic, this missing
one-case assertion is not evidence of a material capture failure. No test-only
change is warranted for this audit.

## Decision and next step

**NO_MATERIAL_CAPTURE_GAP.** The existing opt-in harness can retain and
distinguish an observed AIE sufficiently for a bounded, test-owned follow-up:
it preserves the exception class and traceback, identifies the first worker
to enter capture, retains peers and gate state, and records post-join artifact
and temporary-file state when safe. It deliberately leaves an AIE
indeterminate unless independent boundary evidence meets the stricter natural
link rule. This is an evidence-readiness finding, not a natural recurrence,
root-cause determination, publisher reliability result, or release approval.

No pytest command was run for Phase 6.16A: the audit found no demonstrated
capture defect that would justify consuming the focused-test budget. Do not
repeat Phase 6.13A's eight-attempt matrix. If separately approved, the next
useful natural observation would be one fresh child invocation of the exact
existing isolated concurrent test relevant to the incident, with diagnostics
enabled and its own temporary root; stop after that one outcome. A pass remains
inconclusive. A failure must be preserved and reviewed before any further
attempt. The original report does not identify which artifact scenario
produced the historical AIE, so that scenario mapping must be resolved from
available historical records before choosing the node.

## Validation and preservation

- Static source/test/runner review only; no new fault injection or native,
  service, Chroma, or concurrent reproduction ran.
- No production, diagnostic, test, dependency, or research file was changed
  for this audit.
- The Phase 6.15D deferral report is checkpointed at
  `ed45804f2dec9d93a8e0e36a0c03d0bc8e8fb577`.
- The pending `requirements.txt` candidate remains unstaged and unchanged; it
  is not an adopted dependency set.
- HNSW native-reader failure remains unresolved. The natural publisher
  integrity incident remains unresolved. Dependency adoption is deferred,
  Chroma migration is unauthorized, and unattended general-purpose deployment
  remains **NO-GO**.
