# Artifact Publisher Integrity Incident

**Investigation checkpoint:** Phase 6.13A, 2026-11-01
**Classification:** **C — Not reproduced in the bounded diagnostic matrix.**
**Production changes:** None.
**Phase 6.12F:** Remains incomplete.
**Unattended general-purpose use:** NO-GO; no residual risk is accepted here.

This investigation records the current artifact-store contract, the limits of
the original incident evidence, and one bounded run of the existing concurrent
publisher tests with opt-in test diagnostics. A clean matrix does not resolve
the historical incident. The broader release assessment is in
[release-readiness-612f.md](release-readiness-612f.md).

## Original incident evidence

### Diagnostic provenance repair

The earlier runner interpreted a generic publisher-wrapper event as natural
failure evidence, even when the test-owned gate had raised a timeout. A later
partial repair searched both explicit causes and incidental contexts for the
gate marker; that could misclassify an independent exception raised while a
gate exception was active. Neither suppressed nor unsuppressed `__context__`
establishes causation. The revised diagnostic recognizes only the direct marker
or the exact `ArtifactStoreError` wrapper with that marker as its explicit
cause. Deliberate injected faults have a separate marker. Other wrappers and
unknown origins are indeterminate.

Diagnostic callbacks are guarded separately from publisher calls. Callback
errors are retained as incomplete diagnostic evidence, while a bare re-raise
preserves the original publisher exception. On publisher success, a callback
error prevents verified child-success evidence. The four-thread helper retains
each error and reports first *observed* order without claiming causation. A
mixture of injection, gate, or unknown origins is indeterminate; secondary gate
timeouts are not counted as separate natural recurrences.

The child success record is versioned and includes the attempt and scenario,
actual interpreter PID, four thread outcomes, artifact ID, and integrity and
temporary-cleanup checks. It is written after the existing artifact assertions.
The parent validates the record and exit status; missing or contradictory
evidence cannot pass. Startup failures, timeouts, and missing child evidence
are orchestration failures or indeterminate. The Popen PID may identify a
Windows launcher and is not substituted for the child interpreter PID.

Natural publisher evidence requires an actual built-in link call to raise and
the publisher's explicit wrapper cause to be that same exception, plus a
recorded link-boundary event. This narrow rule does not classify unrelated
publisher failures as natural; they remain indeterminate. Evidence loss or
diagnostic callback errors also prevent a natural classification. No result
here establishes the origin of the historical failure.

The focused regression mapping is:

| Requirement | Test in `tests/unit/test_artifact_publisher_diagnostics.py` |
|---|---|
| Suppressed and unsuppressed incidental context | `test_incidental_gate_context_is_not_causal` |
| Direct marker, unknown wrapper, cycle | `test_unrecognized_cause_and_cycles_do_not_establish_gate_provenance` |
| Real publisher gate wrapping and filesystem timeout control | `test_gate_timeout_wrapped_by_real_publisher_is_not_natural`, `test_unknown_timeout_and_mixed_failures_are_indeterminate` |
| Direct and wrapped injection | `test_injected_fault_is_identified_directly_and_through_real_publisher` |
| Callback preservation, real wrappers, multiple failures | `test_callback_failure_preserves_original_exception`, `test_callback_failure_preserves_real_wrapped_publisher_cause`, `test_base_exception_from_diagnostic_callback_does_not_replace_publisher_error`, `test_base_exception_from_evidence_callback_preserves_publisher_cause` |
| Callback failure on success and evidence write/serialization/context failure | `test_successful_operation_retains_diagnostic_callback_error`, `test_success_evidence_write_failure_is_not_success`, `test_evidence_serialization_failure_is_reported`, `test_evidence_callback_failure_preserves_injected_publisher_cause`, `test_failure_context_callback_error_is_separate_from_publisher_error`, `test_traceback_formatter_failure_preserves_publisher_error`, `test_exception_text_failure_preserves_original_and_reports_incomplete_evidence` |
| Cascading and ambiguous failures | `test_injected_prelink_fault_preserves_peer_outcomes`, `test_injected_fault_and_marked_gate_timeout_are_both_preserved`, `test_prelink_injection_followed_by_gate_timeout_is_not_natural`, `test_unmarked_publisher_boundary_failure_with_peer_release_is_indeterminate` |
| Structured success and aggregate validation | `test_child_success_contract_requires_complete_consistent_evidence`, `test_aggregate_rejects_success_and_failure_records_together`, `test_failure_classifier_requires_natural_boundary_evidence`, `test_isolated_child_publishes_valid_success_evidence` |
| Startup, timeout, crash and unavailable output | `test_child_startup_failure_is_orchestration_error`, `test_real_child_timeout_is_reaped_and_not_natural`, `test_real_child_crash_without_evidence_is_not_natural`, `test_unavailable_output_directory_is_orchestration_error` |
| Existing concurrent publication invariants | `tests/unit/test_artifact_store.py::test_concurrent_evaluation_saves_publish_one_complete_canonical_artifact`, `tests/unit/test_artifact_store.py::test_concurrent_ablation_saves_are_idempotent_and_plan_bound` |

The existing concurrent evaluation and ablation artifact-store tests retain
their content-addressing, reload, and cleanup assertions. The previously
recorded eight successful attempts remain valid observations and do not prove
reliability. The original natural incident's traceback and cause remain
unknown; it has not been attributed to the harness.

The earlier checkpoint history reports an intermittent concurrent
`ArtifactIntegrityError` described as occurring before publication completed.
The original test output, exact exception message, full traceback, cause and
context, artifact ID, thread identity, barrier state, final artifact state,
temporary-file state, and catalog state were not present in the repository or
the locally retained pytest output inspected for this checkpoint. No original
natural-failure directory or diagnostic record was found. These details remain
**unknown**; they are not reconstructed here.

The repository does contain a deliberate test fault,
`ArtifactIntegrityError("controlled pre-link diagnostic fault")`, used to
check concurrent-test error reporting. It is controlled fault injection and
does not reproduce or explain the historical natural incident.

## Publication path and possible error boundaries

The actual path in `ExperimentArtifactStore` is:

```text
save_evaluation_result / save_ablation_result
  → serialize and validate canonical result
  → UTF-8 bytes and SHA-256 artifact ID
  → construct and validate contained destination
  → create destination directory
  → create same-directory temporary file
  → write, flush, and fsync temporary file
  → os.link(temp, destination), without overwrite
      ├─ destination created → return StoredArtifact
      └─ FileExistsError → bounded read and exact-byte + digest validation
  → finally unlink temporary file (cleanup OSError is suppressed)
```

An `ArtifactIntegrityError` does **not** identify one unique stage:

- Save-time validation can raise it before `_publish`, including unstable
  canonical serialization or an ablation result/plan identity mismatch.
- Destination validation can raise it for containment, redirection, or
  symlinked store components.
- If `os.link` reports `FileExistsError`, existing-destination validation can
  raise it when the destination is unsafe, unreadable, oversized, or differs
  from the expected bytes or content digest.
- Other `os.link` `OSError`s are translated to `ArtifactStoreError`.
- Directory-creation `OSError`s are translated to `ArtifactStoreError`;
  temporary-file creation and write/flush/fsync errors may propagate as
  filesystem exceptions. Temporary unlink errors are suppressed in `finally`.

The error class alone therefore cannot prove that `os.link` ran or that a
destination had already been published.

### Intended invariants and existing evidence

Canonical serializers produce the bytes whose SHA-256 is the artifact ID.
The destination is content-addressed and published with a no-overwrite hard
link. A destination collision is accepted only after bounded content and hash
validation. A successful save returns a `StoredArtifact`; tests reload and
validate its content. Temporary files are removed in `finally` when cleanup
succeeds, and existing store tests assert no temporary files remain after
ordinary successful and selected controlled-failure paths.

The publisher does not silently turn a thrown publication error into success.
However, cleanup errors are intentionally suppressed, so post-failure
inspection is needed to establish whether a temporary file remains. A
filesystem error may occur after some state has been created; the original
incident has no preserved snapshot to determine that state.

## Concurrent test structure

The evaluation and ablation publication tests each use four threads, one shared
`ExperimentArtifactStore` instance, one shared immutable result payload, and
the same canonical artifact ID for all callers. A `threading.Barrier(4)` starts
the workers together. The test replaces only
`artifact_store_module.os.link` with a module-local proxy: each worker reaches a
second gate at the final-link boundary, and the original `os.link` runs after
all four have arrived. The gate has a ten-second timeout. On a worker error,
the helper releases the peer gate so the other threads can finish. Threads are
joined with bounded waits.

The helper captures each worker's exception type, message, traceback, cause,
context, last test checkpoint, and link-arrival count. It now selects “first
failure” as the first worker to enter the exception-capture lock; it does not
claim to observe nanosecond ordering of simultaneous native exceptions.
Secondary peer failures are recorded separately from that first observed
exception. The successful matrix had no worker exceptions, so there was no
first or secondary exception to analyze.

The hard-link interception and barrier deliberately synchronize the
publication race and can affect its timing. They are part of the existing
concurrency test, not production behavior. The new instrumentation is
additionally opt-in through `RAG_ARTIFACT_PUBLISH_DIAGNOSTICS=1`; with the
variable unset, the ordinary test path does not install the diagnostic wrappers
or write diagnostic files.

## Diagnostic method and fixed budget

`tests/diagnostics/artifact_publisher.py` keeps at most 64 in-memory events per
worker and eight workers. Events contain an attempt ID, artifact type, short
artifact-ID prefix, process/thread identifiers, monotonic time, and bounded
stage labels. With diagnostics enabled, module-local wrappers observe
serialization, destination validation, temporary-file creation, fsync,
hard-link collision/publication, and the publisher return/error boundary. The
wrappers re-raise the original exception and do not retry publication.

On a worker failure, after bounded thread joins, the helper writes at most one
bounded JSON record below the pytest `tmp_path`. That record includes every
worker error, the observed-first index, link-gate state, and a bounded
post-join inspection of the expected final file and temporary entries. The
artifact content read is capped at 8 MiB; the evidence file is capped at 2 MiB.
Individual traceback strings are capped at 96 KiB with an explicit truncation
flag if that bound is exceeded.
The snapshot is taken after the worker operations, not atomically at exception
time. If a worker remains alive, artifact-state inspection is skipped. No
catalog is involved in these direct artifact-store tests.

The experiment budget was fixed before execution:

| Scenario | Maximum | Attempts | Passed | Publisher exceptions | Other failures |
|---|---:|---:|---:|---:|---:|
| Concurrent evaluation artifact save | 4 fresh pytest processes | 4 | 4 | 0 | 0 |
| Concurrent ablation artifact save | 4 fresh pytest processes | 4 | 4 | 0 | 0 |
| **Total** | **8** | **8** | **8** | **0** | **0** |

| Scenario | Attempt ID | Popen handle PID | Elapsed | Outcome |
|---|---|---:|---:|---|
| Evaluation | `evaluation-01-5af8cd1683ed` | 17216 | 4.250 s | Passed |
| Evaluation | `evaluation-02-cf14276f05af` | 6232 | 4.813 s | Passed |
| Evaluation | `evaluation-03-6cec3a7c12e8` | 19444 | 4.094 s | Passed |
| Evaluation | `evaluation-04-5c3fc54de3f9` | 26632 | 4.015 s | Passed |
| Ablation | `ablation-01-42d9a4d2c0ca` | 30836 | 4.641 s | Passed |
| Ablation | `ablation-02-7dd6c1671942` | 31420 | 5.469 s | Passed |
| Ablation | `ablation-03-6c94396c77aa` | 12984 | 4.437 s | Passed |
| Ablation | `ablation-04-cbf2bf569e07` | 31652 | 4.906 s | Passed |

Elapsed times are descriptive, not a reliability measure. Each attempt used a
new pytest `--basetemp` beneath a unique directory in the system temporary area;
attempts ran sequentially. There were no injected publisher faults in the
matrix. The environment was CPython 3.10.11 (64-bit), Windows platform build
10.0.26300, and pytest 9.1.1. No dependency or environment manifest was
changed.

The runner recorded the PID returned by the Windows virtual-environment
`Popen` handle. In this environment that launcher can start the base Python
interpreter as a descendant, so the saved attempt records do not establish the
actual pytest interpreter PID or retain worker thread IDs for passing attempts.
The in-process event buffer included process/thread identifiers while each
attempt ran, but passing attempts intentionally produced no persistent
diagnostic record. This is a diagnostic limitation; no failed attempt exists
whose thread evidence was lost. The matrix established fresh pytest process
boundaries, not exact Python PID telemetry for every passing trial.

All eight tests returned success. Existing assertions verified one complete
canonical artifact, identical returned references, successful reload, and no
remaining `.artifact-*.tmp` files after each test. No failure-evidence file was
created. Because the successful tests do not preserve a complete filesystem
snapshot, this run does not make a broader claim about directory bytes or
catalog state.

The prior recorded suite result remains `792 passed, 17 skipped, 1 failed` due
to the separate HNSW incident. This focused artifact matrix is not a replacement
for that failed full-suite gate.

## Catalog boundary

The concurrent publisher tests above exercise the artifact store directly;
they do not construct a catalog or SQLite database. The service/catalog order
is separate:

```text
artifact store publication
  → reload and validate published artifact
  → SQLite catalog registration in a transaction
  → return result
```

The artifact and catalog transaction are not one atomic operation. If catalog
registration fails after publication, the valid immutable artifact can remain
without a catalog row. Catalog tests cover transactional rollback and explicit
`register_existing_*` recovery, including idempotent/concurrent registration.
Those tests do not explain the historical publisher error. The historic
incident's catalog participation is unknown.

## Hypotheses and evidence

| Hypothesis | Evidence in this checkpoint | Assessment |
|---|---|---|
| Canonical payloads differed across racing callers | The tests share one result object and one store; each save independently serializes and validates it. The eight observed runs produced the expected single artifact. No historical competing-payload bytes or IDs are preserved. | Not supported by this run; historical cause unknown. |
| Existing destination failed content verification | The implementation checks exact bytes and SHA-256 on a hard-link collision. No collision integrity failure occurred in these eight runs. The historical destination state is unknown. | Not reproduced; not ruled out historically. |
| Filesystem/path validation raised `ArtifactIntegrityError` | These checks can raise before or around publication. No such error occurred in the matrix. No original stage traceback exists. | No causal evidence. |
| Temporary-file or hard-link filesystem behavior caused the report | The test coordinates all four callers at the hard-link boundary; all attempts succeeded. No filesystem exception or temp residue was observed. | No causal evidence; test synchronization is an observer effect. |
| Test helper caused or obscured the reported error | The existing helper synchronizes the final link and records peer outcomes. Its deliberate pre-link fault is controlled. The natural matrix passed. | No evidence that it explains the original report. |
| Catalog registration was the failing boundary | Catalog registration follows publication in the service path, but the reported exception's stage and catalog state are unavailable. Direct publisher tests do not use a catalog. | Unknown and separate from this publisher matrix. |

## Root-cause classification and release status

**C — Not reproduced in the bounded matrix.** The earlier concurrent
`ArtifactIntegrityError` report remains unresolved. The eight passing attempts
do not establish a failure rate, prove the implementation reliable under
concurrency, or disprove the original incident. No natural traceback, artifact
state, or catalog outcome was recovered. No root cause or production
correction is supported by this evidence.

The HNSW reader incident is separate; no shared cause is inferred. Phase 6.12F
remains incomplete, and unattended general-purpose operation remains NO-GO.
Neither restricted research use nor any residual risk is authorized by this
document.

## Required next evidence and approval gate

A useful next observation requires a natural publisher exception accompanied
by the first observed worker traceback and operation stage, all peer outcomes,
link-barrier state, destination existence and bounded digest comparison,
temporary-file state after all workers stop, and a retained disposable
workspace. If the operation goes through the service/catalog path, record the
artifact publication and SQLite catalog outcome as separate checkpoints.

Do not inject an error and call it a natural recurrence. Do not overwrite,
delete, retry, or repair a potentially inconsistent destination to make a test
pass. A proposed production change requires a separate evidence review,
specific failure mechanism, narrowly scoped correction, and dedicated
concurrency/failure-injection validation before implementation approval.

## Validation performed

- Final provenance-repair diagnostic, artifact-store, catalog, and service
  unit selection: **61 passed**. This includes the existing concurrent
  publication tests and isolated child success, timeout, and crash checks.
- Diagnostic helper and artifact-store/catalog/service unit selection:
  **29 passed, 2 deselected**. The deselected natural concurrent publisher
  cases are the same cases executed within the fixed eight-attempt matrix.
- Experiment API persistence integration tests: **25 passed**.
- Python compilation of the changed diagnostic files: passed.
- Relative link check for this report: passed.
- Research baseline: **9 protected files unchanged**.
- The full Python suite was not rerun. Its last recorded result remains
  `792 passed, 17 skipped, 1 failed` due to the HNSW incident.
