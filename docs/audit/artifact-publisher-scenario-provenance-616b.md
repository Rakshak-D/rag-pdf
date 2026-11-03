# Phase 6.16B — Historical Publisher Scenario Provenance

**Review date:** 2026-11-03
**Starting checkpoint:** `f9c7cea055d68e67e0ace78b1c59b245c22feb9b`
**Branch:** `production-evaluation-workbench`
**Conclusion:** **USER_REPORT_ONLY**

This is an evidence-only reconciliation. No pytest invocation, publisher
process, synthetic or natural reproduction, dependency operation, or source
change was performed. The existing unstaged `requirements.txt` candidate was
left untouched.

## Reviewed source identities

These blob IDs identify the exact files reviewed at the starting checkpoint:

| File | Git blob ID |
|---|---|
| `backend/experiments/artifact_store.py` | `b7ef0e46307672d74b90b8b50bd13c5e898829c0` |
| `backend/experiments/ablation_plans.py` | `889b57d23b9314107dd4f5e43b1b74d62c054676` |
| `tests/unit/test_artifact_store.py` | `e43dd379cf7160c86b004b712700c16162aa53cd` |
| `tests/diagnostics/artifact_publisher.py` | `572cc7c9b7f324d7859f3d66a00f04510d0c227f` |
| `tests/unit/test_artifact_publisher_diagnostics.py` | `25a08aeffd6ef487d869cfc9cfafc883e0cbd36c` |
| `scripts/diagnose_artifact_publisher.py` | `6457e706cf7fef92c8cc2206ea7a0c8e047a7e93` |
| `docs/audit/artifact-publisher-incident.md` | `758c0b0937855255e871add7e3c484b6336fdd2e` |
| `docs/audit/artifact-publisher-evidence-gap-616a.md` | `1c12e5c1b6c37f4fb14faa95bb724f1c4afb3efa` |
| `docs/audit/release-readiness-612f.md` | `e37f71fc73d637e98c3ff763228d5bef65bdec90` |

Relevant history includes `925192ec269067c2effb6b5ccdede3f3088a1e93`, which
added concurrent artifact publication tests on 2026-10-27, and
`bf8951f1a3ba701e80eb2faae87f7d64d64c1014`, which added the provenance-safe
diagnostics and Phase 6.13A incident record. These commits contain source,
tests, and an audit report; they do not contain the original natural-failure
pytest output or a failure-evidence JSON record.

## Historical report and newly supplied account

The Phase 6.13A incident report records an intermittent concurrent
`ArtifactIntegrityError` described as occurring before publication completed.
It explicitly says that the original test output, exact message, traceback,
cause/context, artifact ID, thread identity, barrier state, artifact/temp-file
state, and catalog state were unavailable. It also says no original
natural-failure directory or diagnostic record was found. The report's
classification remains **C — not reproduced in the bounded diagnostic
matrix**; the eight later successful attempts are not a reliability result.

A user report supplied for this review, dated 2026-10-30, retrospectively
attributes the incident to the **ablation publisher** and says three of four
workers reached `os.link()`, while a fourth raised `ArtifactIntegrityError`
before the link boundary. It says the exact message and traceback were not
captured, and that a later diagnostic attempt did not reproduce the failure;
a separate deliberate pre-link injection followed. This is a historical user
statement, not an original log or independently verified execution record.
Its provenance is therefore **USER_REPORT_ONLY**. It is retained here without
rewriting or weakening the Phase 6.13A report's account of missing primary
evidence.

The retrospective account and the committed report are not proven to conflict:
the user statement may preserve additional recollection, but it does not
recover the missing primary record. The repository's deliberate pre-link test
fault is a possible source of later confusion, but the available evidence does
not establish that the user report conflated it with the natural incident.
The later 6.13A matrix recorded four passing ablation attempts and no publisher
exceptions; it cannot verify an earlier failure.

## Primary-evidence check

The bounded review covered the named incident, 6.16A evidence-gap and release
readiness reports, relevant tracked Git history, and the current publisher and
test source. The 6.13A report already documents that the original local output
and natural-failure directory were not found. No retained primary execution
record was identified in the reviewed repository history or audit records.
The prior broad search was not repeated, and no recursive search of the user's
machine was performed. No newly discovered original artifact exists to hash.

| Historical assertion | Independent primary support located? | Finding |
|---|---|---|
| Ablation scenario | No original node ID or run record. | User report only; not independently confirmed. |
| Three of four workers reached the link boundary | No original arrival log or child output. | User report only; exact meaning of “reached `os.link`” cannot be checked. |
| A fourth worker raised `ArtifactIntegrityError` | No original exception record. | User report only; exception instance, message, and traceback are unavailable. |
| The exception occurred before the built-in link call | No worker traceback or boundary event. | User report only; not independently confirmed. |
| The error was natural rather than injected | No original harness configuration, patch state, or failure evidence. | User report only; absence of an injection marker is not proof of natural causation. |

The current repository does contain a separate deliberate injection in
`test_concurrent_publication_helper_preserves_first_prelink_exception`. It
replaces the ablation serializer for worker 2 with
`ArtifactIntegrityError("controlled pre-link diagnostic fault")`. That test
is explicitly synthetic. It is not primary evidence for the 2026-10-30 event.

## Static ablation save boundaries

The current [`artifact_store.py`](../../backend/experiments/artifact_store.py)
and [`test_artifact_store.py`](../../tests/unit/test_artifact_store.py) show
these distinct paths. “Before link” below refers to whether the source path
can raise before the built-in `os.link` call; it is not a claim about the
historical worker.

| Failure boundary | Can raise `ArtifactIntegrityError`? | Before built-in `os.link`? | Evidence required to distinguish |
|---|---|---|---|
| `save_ablation_result`: result manifest/shared-index identity mismatch (line 226) | Yes, directly. | Yes; `_publish()` is not called. | Full traceback to the check, worker identity, and the result/plan identity comparison. |
| `save_ablation_result`: round-trip serialization stability check (line 231) | Yes, directly. | Yes; `_publish()` is not called. | Full traceback and preceding serialization/round-trip evidence. |
| `_destination` / `_check_component`: escaped, redirected, or symlinked path (lines 96–120) | Yes. | Yes; `_destination` is called before file creation and link. | Traceback plus bounded, test-owned path-component observations and operation events. |
| `_publish` parent-component check (lines 155–159) | `_check_component` can raise it; the `except OSError` does not catch this `ValueError` subclass. | Yes; it occurs before temporary-file creation and link. | Traceback identifying the component check and bounded directory state. |
| `_existing_or_raise` / `_bounded_read` after a `FileExistsError` (lines 125–148, 168–173) | Yes: unsafe/non-regular/unreadable/oversized or byte/hash-mismatched existing destination. | No. `os.link` has already been invoked and returned `FileExistsError`; the error concerns verification of the existing destination. | Link exception/event, full traceback, existing destination snapshot and hash/size, plus worker/gate evidence. |
| `AblationPlan.__post_init__` validation | Its direct validation branches raise `AblationPlanError`, not `ArtifactIntegrityError`; nested `RunPlan` integrity errors must be identified from their own traceback/type. | Yes; it precedes serialization and `_publish`. | Traceback and concrete exception type; do not infer this is an AIE from the surrounding save method. |
| Size, directory creation, temporary write/fsync, and other link errors | The inspected branches raise `ArtifactStoreError` or filesystem exceptions; ordinary non-`FileExistsError` link `OSError` is wrapped as `ArtifactStoreError`. | These operations are before, at, or after the link boundary according to their source location. | Exact traceback and explicit cause chain; exception class alone is insufficient. |

The helper's `publication_entered` checkpoint is written **before** it calls
the supplied `publish()` callback. It proves only that the worker passed the
helper's start barrier and was about to invoke the callback. It does not prove
that `save_ablation_result()`, `_publish()`, `_destination()`, or `os.link()`
was entered.

The helper's `arrivals` count is incremented in the test-local `coordinated_link`
proxy. A genuine retained count of 3/4 would support that three workers entered
that proxy. It would not identify the fourth worker's internal location, and
without its traceback it cannot distinguish plan/result validation, serialization,
destination checks, a not-yet-scheduled worker, or another failure boundary.
Nor does the count alone establish the result of each subsequent built-in link
call. The user-reported 3/4 value has no retained counter record here.

## Relevant tests and diagnostic limits

`test_concurrent_ablation_saves_are_idempotent_and_plan_bound` uses four
workers to save the same ablation result and plan through one store. When
`RAG_ARTIFACT_PUBLISH_DIAGNOSTICS=1`, it attaches the opt-in trace. Its helper
checkpoint is still pre-callback; actual link-proxy arrivals are recorded at
the proxy. This is a justified **candidate** for a future diagnostic because
the retrospective account names ablation, but the historical user report does
not prove this exact node was the original failing invocation.

`test_concurrent_publication_helper_preserves_first_prelink_exception` is a
different test. It deliberately raises `ArtifactIntegrityError` from the
worker-2 serializer and checks that the helper surfaces the exception and
traceback, releases/join workers, restores the module-local `os` proxy, and
leaves no temporary file. It does not provide a natural recurrence, and that
call does not attach the opt-in JSON trace.

The provenance-safe harness records exception types and tracebacks, first
worker-to-enter-capture order, peer outcomes, gate reason/arrival count, and a
bounded post-join artifact/temp-file snapshot when workers have stopped. Its
classification rules keep an `ArtifactIntegrityError` indeterminate unless
stronger evidence meets the narrow natural built-in-link rule. That preserves
the Phase 6.16A **NO_MATERIAL_CAPTURE_GAP** conclusion. These mechanisms explain
what a future run could capture; they do not establish what happened in the
historical run.

## Conclusion and future evidence gate

**USER_REPORT_ONLY.** The ablation attribution, 3/4 arrival count, fourth
worker's exception, pre-link timing, and natural provenance are not supported
by independent primary execution evidence in the reviewed records. Preserve
the retrospective account as context; do not amend the historical incident
report to present it as verified. Phase 6.16A remains
**NO_MATERIAL_CAPTURE_GAP**; this finding only records the provenance limit on
the historical scenario attribution.

Before making a causal claim, a future record would need at least the exact
test node and source revision, the untruncated per-worker traceback and
exception chain, explicit diagnostic/injection configuration, per-worker
instrumentation events locating the save stages, link-proxy arrivals and gate
state, all worker outcomes and first-observed ordering, and a post-join bounded
artifact/temp-file snapshot. A link-boundary event must be tied to the actual
worker/process, and a `publication_entered` checkpoint must not be substituted
for it. Even those observations would identify the observed failure path, not
automatically prove the deeper reason its precondition occurred.

The existing ablation concurrency test is the single best candidate if a
separately approved diagnostic run is later chosen. **No experiment is
authorized or performed by this report.** Any future run requires explicit
approval, one fresh isolated child invocation, and a stop after its first
success or failure. A passing result remains inconclusive; no retries or broad
matrix are implied.

The HNSW native-reader incident remains unresolved. The natural publisher
incident remains unresolved. Dependency adoption is deferred, Chroma migration
is unauthorized, and unattended general-purpose deployment remains **NO-GO**.

The candidate requirements file was not changed and retains SHA-256
`EF3095DBC1A1657CCB5D5FCBA4D2DF245DD5EC9D04B6C2D237823FDFE472734F`.
