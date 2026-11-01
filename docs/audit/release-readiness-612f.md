# Release-readiness and residual-risk assessment — Phase 6.12F

- **Assessment date:** 2026-11-01
- **Repository branch:** `production-evaluation-workbench`
- **Evaluated committed revision:** `d2b7de9acef42bed71578c42e0ed1dbf5d6020d6`
- **Phase 6.12F status:** incomplete; its smoke, integration test, and guide
  are local untracked files and are not part of the evaluated commit.

## Purpose and decision boundary

This record separates implementation, test results, bounded demonstrations,
failed validation, unresolved incidents, and decisions that require an operator.
It is not a product endorsement, deployment authorization, or acceptance of
residual risk. No risk is accepted by this assessment.

There is no single “ready” state for this workbench. A green component test,
successful synthetic workflow, report match, and protected research baseline
each establish different facts. The current HNSW and artifact-publisher
incidents remain distinct integrity/reliability concerns. Unattended,
general-purpose use is **NO-GO** on the evidence reviewed here. Controlled local
research is a possible policy for an operator to consider, not an approved use
decision. Phase 6.12F remains blocked.

## Evidence sources and checkpoint boundaries

The assessment uses the committed implementation and documentation at the
revision above, the uncommitted Phase 6.12F files present in the worktree, and
the validation results recorded by the preceding checkpoints. The Phase 6.12F
files are considered local evidence only; they have not been reviewed as a
committed checkpoint.

| Checkpoint | Commit | What its evidence establishes |
|---|---|---|
| 6.10A/6.10B | `9d5b0be`, `4b6c301` | Fresh-only manager foundation and authenticated bootstrap HTTP service. |
| 6.10C | `bece14d` | Frontend fresh-build operator workflow. |
| 6.11A/6.11B | `d55fa61`, `bb7e816` | Bounded read-only report inventory service and authenticated endpoint. |
| 6.11C | `6cc84a1` | Frontend report inventory viewer. |
| 6.12A | `190ed38` | Manual handoff safety design; no activation implementation. |
| 6.12B | `f52dd3a` | Explicit-build-ID, report-level local preflight. |
| 6.12C | `603e219` | Real Chroma side-effect/lease investigation; NO-GO for a verifier described as filesystem-read-only. |
| 6.12D | `7f8b9ea` | Synthetic real-build to explicit ordinary-runtime report handoff smoke. |
| 6.12E | `e2ca827` | Isolated real text/image retrieval smoke through the established retrieval path. |
| HNSW diagnostic | `dc871f2`, `1fcefac`, `d2b7de9` | Opt-in diagnostic foundation, one captured query failure, and a later bounded minimal-observer matrix that did not reproduce it. |
| 6.12F | **No commit** | Local evaluation-to-artifact smoke/test/guide. A positive run was reported once; subsequent recovery validation and the latest full-suite gate failed. |

The design and operations documents are cross-referenced below: [bootstrap
operations](../architecture/bootstrap-http-operations.md), [index
inventory](../architecture/index-inventory.md), [report handoff
design](../architecture/report-handoff-safety-design.md), [preflight
operations](../architecture/report-handoff-preflight-operations.md), [Chroma
verification contract](../architecture/chroma-store-verification-contract.md),
[real retrieval smoke](../architecture/manual-report-retrieval-smoke.md),
and the [HNSW incident record](chroma-hnsw-incident.md). The local Phase
6.12F guide `docs/architecture/manual-report-evaluation-smoke.md` is
uncommitted worktree material and is not a durable cross-reference.

## Implemented capabilities and evidence limits

The bootstrap application is separate from the ordinary experiment runtime.
It can start without a build report, binds to loopback, uses its own bearer
token and manager state, and exposes preview, fresh-only build jobs, job
inspection, and bounded report inventory. The ordinary runtime still requires
an explicitly configured build report. The two frontend service connections
and development proxy targets are separate.

Build admission recomputes trusted source and RunPlan identities. Durable job
metadata is in SQLite; accepted configuration payloads are transient. The
manager uses one worker and bounded admission, preserves ambiguous state, and
does not replay work after restart. Writer reservations coordinate cooperating
processes only. No HTTP or UI path activates a report, repairs storage, removes
an incomplete marker, or starts an experiment from bootstrap mode.

Inventory and ordinary-runtime readiness establish **report-level** evidence.
They state `check_level: report` and `store_checked: false`; neither opens
Chroma. The local manual preflight requires an explicit build ID and recomputes
the manifest and RunPlan, but is a point-in-time observation and does not hold a
lease or pin report bytes through later runtime startup. Runtime retrieval
remains responsible for its own report and persisted-count validation.

The ordinary retrieval and evaluation services remain the execution boundary.
Retrieval uses the established plan-bound report validator, opens the actual
persistent store, compares text/image collection counts with the report, and
then queries Chroma. The experiment service validates provenance before
artifact/catalog persistence. The content-addressed artifact file and SQLite
catalog are separate persistence systems: publication can succeed while
catalog registration fails, leaving an immutable artifact without a catalog
row. Explicit registration recovery exists; it is not automatic.

No active-index pointer or automatic activation is implemented. Chroma's
`open_existing()`/count/query path is mutation-capable from a filesystem
perspective: Phase 6.12C observed persistent SQLite/HNSW digest changes during
open/count windows. Wrapper close releases the cooperating lease but does not
prove the cached internal Chroma system has stopped. A clean child-process exit
was used to bound its lifetime in the isolated retrieval smoke.

## Workflow readiness matrix

“Disposition” applies only to the stated evidence and scope. Synthetic evidence
does not establish production-model or real-corpus behavior.

| Workflow | Evidence source / last checkpoint | Evidence type and coverage | Known gap or failed gate | Disposition | Minimum evidence to reconsider |
|---|---|---|---|---|---|
| Static frontend and API-client behavior | 6.10C/6.11C; `frontend/README.md` | Reported 95 frontend tests across six files; typecheck and production build passed. Component/client tests use test responses. | No authenticated browser E2E. An in-app browser could render the page but could not complete authenticated local-service access; that is not evidence of successful browser operation. | Component-tested; browser acceptance unverified. | A bounded browser walkthrough from an environment that can reach the loopback Vite proxy, without exposing the backend beyond loopback. |
| Bootstrap preview | 6.10B/6.10C | Real HTTP service and proxy smokes; complete configuration, trusted source binding, recomputed manifest and RunPlan identities. | Point-in-time source/config evidence; preview is not a reservation. No production model readiness implied. | Demonstrated for local service contract. | Operational validation with the intended local configuration and source, retaining the current validation/security contract. |
| Fresh index build | 6.10A/6.10B; 6.12D/E | Fresh-only manager and established builder; synthetic PDF, real Chroma persistence, deterministic injected adapters, genuine report. | Does not validate arbitrary existing indexes, production model downloads, resource ceilings, or uninterrupted real-corpus operation. Partial persistence remains preserved rather than repaired. | Demonstrated for isolated synthetic build. | A separately scoped operator-run build using the intended corpus/models, with capacity, failure, backup, and evidence-retention controls. |
| Build-job persistence and crash behavior | 6.10A/6.10B; bootstrap operations guide | Durable SQLite metadata, request idempotency, active-fingerprint conflict, interruption state, bounded single worker and shutdown behavior covered by prior API/manager tests. | Payload is transient; work is not resumed. Crash reservation can block future startup and requires offline operator investigation; no automatic stale takeover. | Contract tested locally; not a distributed/restart-resumable queue. | Operationally approved manual recovery procedure and failure-injection evidence for deployment-specific storage and shutdown. |
| Build-report inventory | 6.11A/B/C | Bounded deterministic scan, strict classifications, authenticated endpoint, typed UI; no Chroma open. | Candidate pages are point-in-time and not a stable snapshot; report match is not store readability. | Demonstrated as report discovery only. | No additional evidence needed for its limited purpose; any store claim requires the separate gated verification design. |
| Manual report handoff | 6.12B/D | Explicit build ID, manifest/fingerprint confirmation, report-only preflight, real synthetic build, temporary runtime environment with explicit selected report. | No durable content pin; source/report/index may change after preflight. No automatic activation. | Handoff mechanics demonstrated on a synthetic fixture. | Operator-specific manual configuration and restart evidence; stronger selection guarantees require a separately approved design. |
| Ordinary runtime readiness | 6.12D | Real loopback runtime; authenticated manifest, RunPlan, and readiness calls. Readiness returns report-level evidence and null store counts. | Does not check collections, incomplete markers, reservations, model availability, or execute retrieval. | Demonstrated as report compatibility only. | No wording or policy may promote this endpoint to store/runtime readiness without a new contract. |
| Real retrieval | 6.12E; HNSW diagnostics through `d2b7de9` | One isolated genuine build returned text and image fixture hits; counts matched its genuine report. Separate HNSW failure was captured at first text query after open/count; eight later minimal/off attempts did not reproduce it. | HNSW query reliability remains unresolved. Chroma reads can modify persistence. One passing smoke is not general reliability. | Synthetic query path demonstrated; release reliability gate failed. | Failure mechanism or a separately reviewed, justified mitigation and failure policy; define a reliability objective before any sample-size-based test plan. |
| Evaluation execution | Phase 6.12F local files only | One reported positive deterministic service-level evaluation traversed retrieval, context formatting, generation, provenance validation, artifact save, catalog registration, and reload. | Recovery query later failed with the HNSW error; the local integration/full-suite gate did not pass. Not committed. One positive run does not validate genuine answer quality or reliability. | Incomplete and blocked. | Reviewed, committed integration checkpoint after the HNSW gate and persistence incident are addressed. Any separate risk acceptance would not by itself complete Phase 6.12F or erase its failed gate. |
| Artifact publication/catalog | Artifact store/catalog tests; local 6.12F report | Content-addressed no-overwrite publication and strict reload; normal concurrent idempotency tests and one isolated round-trip reported successful. Failure-injection distinguishes publication from catalog registration. | Separate intermittent natural concurrent pre-publication `ArtifactIntegrityError` is unresolved; original error details and persistence state are not retained in repository evidence. | Incomplete integrity/reliability gate. | Preserve a natural failure with exact stage, original traceback, artifact/temp-file snapshot, and catalog state; then establish cause and validate a reviewed correction if warranted. |
| Unattended general-purpose operation | Combined system evidence | Local loopback service boundaries, bounded queues, explicit credentials, durable job metadata, stable public errors. | HNSW and publisher failures; stale leases; no distributed ownership; no accepted recovery policy; production model/resource and monitoring behavior not validated. | **NO-GO.** | All independent release gates below, plus explicit operational ownership and accepted failure/recovery policy. |
| Research baseline | `scripts/verify_baseline.py`; Phase 6.12 checks | Latest recorded run passed with nine protected research files unchanged. The verification script hashes the manifest-listed tracked notebook Python/notebook files and checks the tracked set. | Baseline integrity does not establish pipeline quality, model validity, corpus representativeness, or validity of every metric. | Baseline guard passed; research quality remains a separate question. | Continue running the guard on relevant changes and review controlled datasets/configurations and scientific methodology independently. |

## Evaluation execution is not evaluation quality

The reported Phase 6.12F positive result is a deterministic synthetic pipeline
demonstration. It shows that one controlled case can retrieve persisted fixture
evidence, format context, invoke an injected deterministic generator, validate
provenance, publish a result, register catalog metadata, and reload the result.
It does not show that answers are good on real documents, production models are
available or reliable, retrieval generalizes, large datasets behave similarly,
or unattended execution is stable.

The one-case fixture has no ground-truth answer. The runner must retain the
actual M1–M19 observation statuses (`computed`, `skipped`, `unavailable`, or
`failed`) and reasons. Presence of all metric identifiers or aggregate rows is
not evidence that all metrics were numerically computed or scientifically
validated. Metric definitions, formulas, benchmark rules, and evaluation
methodology are outside this assessment and are not altered here.

## Incident 1 — Chroma HNSW reader failure

The observed error is `InternalError: Error creating hnsw segment reader:
Nothing found on disk`. The committed incident record documents CPython
3.10.11, 64-bit Windows, Chroma 1.5.0, and a failure at the native query path.
The controlled Phase 6.12F-R incident was in a fresh test process and failed
at query ordinal 1: the initial `text_vectors` query after store creation,
upsert, wrapper close, reopen, report validation, and successful text/image
count reads. The later image and repeated text queries were not reached.

The subsequent fixed minimal-observer matrix ran four uninstrumented and four
minimal-mode fresh-process attempts; all eight passed. It did not capture a new
failure and did not establish that instrumentation prevented one. An earlier
separate 14-attempt matrix had one diagnostics-disabled query-1 failure. These
matrices have different instrumentation and conditions; no meaningful failure
probability is inferred from them. The latest Phase 6.12F report also records
the same HNSW error in `query_text` during its recovery scenario and a full
Python result of **792 passed, 17 skipped, 1 failed**. That failed result is
preserved; the later eight passes do not replace it.

No failure-time Rust segment-state observation exists. Post-exit SQLite
collection-to-segment mapping and native-file presence are not atomic
failure-time evidence. The absent `index_metadata.pickle` and zero-length
`link_lists.bin` were also observed in passing fixtures and are not established
as corruption. Chroma bytes changing during open/count/query windows is an
observed side effect, but has not been connected causally to the HNSW error.
There is no deterministic reproducer, established root cause, or validated
production correction. Phase 6.12F remains blocked.

## Incident 2 — Artifact-publisher integrity failure

Repository documentation and the recorded checkpoint history identify a
separate intermittent concurrent artifact-publisher `ArtifactIntegrityError`
reported before publication completed. The repository contains no dedicated
incident report with the original natural-failure traceback, exact assertion
message, artifact ID, or an exact stage within `_publish`; those details are
**unknown**. The available record describes a concurrent publisher context,
but does not preserve enough original process/thread evidence to establish
reproducibility or a root cause.

The current `ExperimentArtifactStore` validates and serializes the result,
creates a same-directory temporary file, flushes/fsyncs it, and publishes with
no-overwrite `os.link`. A pre-existing destination is read and compared to the
expected bytes and digest. This design and the passing concurrent idempotency
tests do not explain the natural failure. `tests/unit/test_artifact_store.py`
also has a deliberately injected `ArtifactIntegrityError` before the link
boundary; that controlled fault validates the concurrency helper's diagnostics
and is **not** evidence of the natural incident's cause.

Whether a complete artifact remained after the natural failure is **unknown**:
no post-failure filesystem snapshot or artifact ID is recorded. Whether a
catalog transaction committed is also **unknown** for the reported natural
incident. The isolated store-level concurrent test path does not itself use
the catalog, while the normal service path publishes the artifact before a
separate catalog transaction. In the latter case, a catalog failure can leave
a valid artifact without a catalog row by design. The incident evidence does
not establish that this catalog failure was the observed event. No targeted
production correction has been validated. A successful single-run round-trip
does not resolve this separate incident, and no causal link to HNSW is known.

## Chroma lifecycle and ownership limitations

Phase 6.12C observed same-size digest changes to `chroma.sqlite3` and selected
HNSW files across `open_existing()`/count intervals, and timestamp changes to
other HNSW files. It did not attribute those changes to one specific call or
label them corruption. A deterministic query in that investigation succeeded
without further observed persistence changes. Wrapper close released the
application's lease, while Chroma's cached internal system remained in-process
until process exit. A forced child termination left the reservation behind;
cooperating opens then failed closed. There is no automatic PID-based lease
takeover.

The lease excludes cooperating repository writers for a fingerprint; it does
not govern external or legacy writers that ignore the protocol. A report
identity is not a durable content pin. Readiness and inventory can still match
while a marker or reservation exists because those operations are report-only.
Preflight observations can become stale before a manual runtime restart.

## Deployment choices

| Choice | Assessment | Scope, utility, and outstanding decision |
|---|---|---|
| **A — Controlled local research only** | A candidate limited-use policy may be considered. No authorization is granted here. | Requires an operator to explicitly approve the use case and residual risks. Use explicit initiation, synthetic/disposable or backed-up inputs where possible, one supervised operation at a time, preserved logs, manual output review, no unattended scheduling, no automatic activation, retry, repair, or lease takeover, and stop on any integrity/HNSW error. These controls reduce exposure; they do not fix either incident. Production-model and real-corpus behavior remain unestablished. |
| **B — Unattended general-purpose use** | **NO-GO.** | Repeated real retrieval has an unresolved native failure and the artifact publisher has a separate unexplained integrity incident. Crash recovery, external writers, monitoring, model availability/resource limits, and operational failure policy are insufficiently established. A passing targeted matrix or one artifact round-trip is not a basis to change this decision. |
| **C — Isolated dependency/architecture evaluation** | Viable future investigation; no dependency change is made or approved. | Compare pinned Chroma 1.5.0 with one specifically selected later stable release in an isolated environment, and only if test parity and migration risk can be bounded. Preserve the current environment. Require API/schema compatibility, real synthetic persistence/open/count/query parity, side-effect snapshots, crash/lease behavior, HNSW regression evidence, rollback, and an explicit data-migration plan before considering any production change. A newer version is not presumed to fix the failure. Alternative backends require a separate architecture justification. |

The next engineering priority recommended here is a **narrow artifact-publisher
root-cause capture**, not a broad rerun: preserve the first natural failure's
exact operation stage and original traceback; identify all concurrent callers;
record temp/final artifact paths by safe relative identity, hashes and file
presence before/after; and snapshot catalog rows only after publisher threads
have stopped. Distinguish serialization validation, temp creation/write/fsync,
`os.link`, existing-file verification, and catalog registration. The existing
controlled pre-link injected exception should remain a test of the helper, not
be mistaken for natural reproduction. A fixed attempt budget and stop-on-first
failure should be approved before execution. This checkpoint can clarify an
independent integrity boundary without changing production code. It does not
resolve the HNSW release blocker. Isolated dependency evaluation remains a
separate plausible follow-up. No investigation is started by this recommendation.

Formal local-research risk acceptance is another option only for an authorized
operator. It changes a usage decision, not the technical evidence or Phase
6.12F completion status.

## Independent release gates

| Gate | Current decision | Evidence required to change the decision |
|---|---|---|
| **Index construction** | Demonstrated for synthetic fresh-only builds; not general-purpose qualified. | Preserve fresh-only enforcement, manifest/RunPlan identity, genuine report validation, writer ownership, and partial/crash evidence. Validate intended corpus/model/resource behavior under a separately approved supervised run; no recovery-by-deletion. |
| **Retrieval** | **Failed / blocked for release reliability.** | Characterize the captured HNSW failure or obtain a separately reviewed, evidence-backed mitigation and failure policy. Show that report/count validation remains intact and that failures remain explicit. Define the reliability objective and test design before selecting sample counts; do not demand arbitrary reruns. |
| **Evaluation and persistence** | **Incomplete.** | Complete the real retrieval-to-context-to-generation-to-runner-to-provenance-to-artifact/catalog round-trip on reviewed final source, including recovery/error cases. Resolve or explicitly disposition publisher failure with preserved evidence; distinguish orphan artifact from catalog state. |
| **Operations** | Local boundaries demonstrated; unattended operations not qualified. | Document and approve ownership for credentials, trusted roots, backups, stale reservation response, service lifecycle, monitoring/log retention, model/resource availability, and visible operator failure handling. Browser E2E is still absent for the frontend. |
| **Research** | Protected baseline guard passed; research validity not inferred. | Keep protected files unchanged, use controlled and disclosed corpus/configuration provenance, preserve all metric statuses, and separately review benchmark/metric methodology and reproducibility. Synthetic fixtures are not research-quality results. |

No single gate passing substitutes for another. In particular, a report match
does not pass retrieval; a successful retrieval smoke does not pass evaluation
quality; an artifact/catalog round-trip does not resolve concurrent publication
integrity; and a baseline hash check does not certify experimental validity.

## Risk register

Every unresolved risk below has acceptance status **not accepted**. Severity is
an assessment of consequence if the risk occurs, not an estimated probability.

| Risk / severity | Evidence and uncertainty; affected workflows | Existing safeguards | Missing guarantee and detection | Remediation category; approval; acceptance |
|---|---|---|---|---|
| Intermittent HNSW query failure — **Critical** | Captured at first text query after open/count in a fresh process; a later eight-attempt matrix did not reproduce it. Retrieval and evaluation affected; cause unknown. | Plan/report/count checks, explicit errors, isolated diagnostics, no automatic retries or repair. | No cause, failure-time Rust state, or accepted failure policy. Detect by preserving first exception/stage and isolated segment evidence. | Narrow native/upstream diagnosis or separately evaluated dependency; engineering approval before changes. **Not accepted.** |
| Artifact-publisher `ArtifactIntegrityError` — **High** | Natural concurrent pre-publication failure reported; original message/traceback, artifact persistence, catalog state, and reproducibility are unknown. | Content-addressed IDs, fsync/temp file, exclusive no-overwrite link, strict existing-byte validation, catalog transaction/reload. | No preserved natural failure boundary or outcome snapshot. Capture exact stage, thread outcomes, temp/final file and catalog evidence. | Focused publisher root-cause investigation; approval before production correction. **Not accepted.** |
| Chroma writes during nominal reads — **High** | Persistent SQLite/HNSW digest changes observed during open/count windows; causality and corruption status unknown. Affects preflight/verification expectations and retrieval. | Phase 6.12C NO-GO against “filesystem-read-only” claims; disposable smoke snapshots. | No immutable/read-only Chroma contract. Compare owned snapshots and preserve failures; do not demand byte identity. | Separate side-effect/lifecycle contract and explicit approval for any verifier. **Not accepted.** |
| Cached Chroma system after wrapper close — **High** | Internal system observed cached after wrapper close; child exit bounded lifetime in real retrieval smoke. | Lease release on acquiring thread; child-process isolation for smokes. | Wrapper close does not end Chroma client internals. Observe process lifetime and avoid assuming close equals shutdown. | Explicit lifecycle/resource ownership design; approval before any process workaround. **Not accepted.** |
| Stale fingerprint reservation after crash — **High** | Forced child termination retained lease; subsequent cooperating open returned busy. | Fail-closed reservation; no PID takeover or automatic recovery. | No approved offline diagnosis/recovery procedure. Detect marker and preserve it; operator investigation required. | Separately reviewed manual recovery policy. No automatic cleanup. Operator approval required. **Not accepted.** |
| External writer bypasses cooperating lease — **High** | WriterLease coordinates repository participants only; external/legacy Chroma clients can ignore it. | Trusted local roots and operational instruction to stop external writers. | No OS-wide exclusivity against non-cooperating processes. Detect operationally where possible; source-of-truth ownership remains local. | Deployment isolation/process ownership controls. Operator approval required. **Not accepted.** |
| Report identity without durable content pin — **High** | Report/schema/identity are revalidated, but the ordinary runtime does not pin report content digest from preflight through use. | Explicit build-ID selection, bounded validator, path hierarchy checks, preflight metadata rechecks. | Replacement with same identity/count can still match; no lock across preflight and runtime. Compare current report immediately before use; record residual race. | New selection identity contract or manual procedure; design approval required. **Not accepted.** |
| Readiness match despite incomplete marker/reservation — **High** | Ordinary readiness is report-only and does not inspect those states; handoff tests document this boundary. | Preflight separately observes marker/reservation at a point in time; retrieval checks its own boundary. | State may change after preflight; readiness is not store readiness. Detect only through explicitly scoped checks or actual retrieval. | Separate store/lifecycle operation contract; Phase 6.12C prohibits advertising it as read-only. **Not accepted.** |
| Source/report/index changes between preflight and use — **High** | Point-in-time manifest/report checks; no cross-operation lock or durable pin. | Recompute current manifest and RunPlan; explicit identities; runtime revalidation. | Changes after each check remain possible; same-metadata replacements may escape. Revalidate at the operation boundary and retain evidence. | Stronger identity/coordination design only if required. **Not accepted.** |
| Production model availability and resource limits — **High** | Smokes use deterministic injected adapters; no production embedding/generation model or Ollama was validated. | Lazy model construction, explicit configuration, bounded job queue/workers. | Download/cache availability, GPU/CPU/RAM and production latency/failure untested. Detect in supervised deployment-specific qualification. | Separate model/resource qualification and capacity plan; operator approval. **Not accepted.** |
| Missing authenticated browser E2E — **Moderate** | React/Vitest and real Vite HTTP proxy smokes passed; browser could not complete authenticated loopback flow in available isolated browser. | Separate tokens, in-memory-only token storage, protected status endpoints, proxy isolation, accessible component tests. | No full browser proof of keyboard/accessibility and authenticated workflow. Run same-host bounded browser check. | Browser validation in an approved local environment; no non-loopback exposure. **Not accepted.** |
| Synthetic evaluation differs from real research performance — **High** | One-case deterministic fixture; no reference answer; model-backed metrics may be skipped/unavailable. | M1–M19 explicit status model; protected research baseline. | No real-corpus retrieval quality, answer quality, scale, or scientific validity established. Detect through separately designed research evaluation. | Controlled dataset/benchmark and methodology review; research approval. **Not accepted.** |

## Candidate controlled-local-research operating policy

An operator could consider a limited local-research policy with explicit
scope: supervised local invocation; disposable or backed-up storage; known
trusted roots; no unattended schedule; no automatic report activation, retry,
repair, deletion, or lease takeover; preserve the first failure and temporary
evidence; inspect outputs manually; stop on HNSW or integrity errors; use no
production-reliability or scientific-quality claim from the synthetic smokes.
This does not make the underlying risks disappear and does not authorize use.
The operator must explicitly decide whether this limited exposure is
acceptable, identify the data and environment, and approve a failure/stop
procedure. Without that decision, status remains not accepted.

## Unknowns and assumptions

- The natural artifact-publisher failure's exact message, stack, publisher
  stage, final artifact presence/content, catalog transaction state, and
  reproducibility are not established by retained repository evidence.
- HNSW Rust reader state at the instant of the failure is unavailable. The
  post-exit SQLite mapping is not a substitute.
- The latest Phase 6.12F run and full-suite counts are reported evidence from
  the uncommitted local checkpoint; no new full-suite run was performed for
  this documentation assessment.
- Frontend test/typecheck/build and browser limitations are inherited from the
  prior frontend checkpoint; no new frontend validation was performed here.
- The baseline verifier covers the manifest-listed tracked research files; it
  does not validate model weights, datasets, local runtime stores, or all
  generated artifacts.

## Final assessment

The workbench has substantial, separately committed local operator workflows
for fresh index construction, report discovery, manual report handoff, and
isolated retrieval. Their evidence is strongest for synthetic fixtures and
bounded local tests. Report inventory and readiness must remain described as
report-level. Chroma persistence is mutation-capable during nominal reads.

The HNSW failure is confirmed and unresolved. The artifact-publisher integrity
incident is separately reported and insufficiently characterized. Phase 6.12F
has one reported positive evaluation-to-artifact demonstration but its recovery
validation and latest full-suite gate failed, and the checkpoint is uncommitted.
**Phase 6.12F is incomplete. Unattended general-purpose use is NO-GO. No
residual risk is accepted and no deployment is authorized by this document.**

Any limited local-research use requires a separate explicit operator decision.
Any production correction, dependency change, activation workflow, or risk
acceptance requires its own reviewed checkpoint and approval gate.
