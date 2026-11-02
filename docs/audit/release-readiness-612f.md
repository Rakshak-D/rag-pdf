# Release-readiness and residual-risk assessment — Phase 6.12F

- **Assessment date:** 2026-11-02
- **Repository branch:** `production-evaluation-workbench`
- **Evaluated committed revision:** `1d12d5c3029c9299088fa3f2c59807233d0729b2`
- **Previous assessment revision:** `d2b7de9acef42bed71578c42e0ed1dbf5d6020d6`
  (historical assessment; it predates the Phase 6.13A–C and Phase 6.12F
  checkpoints below).
- **Phase 6.12F bounded functional integration:** completed and committed at
  `1d12d5c3029c9299088fa3f2c59807233d0729b2`.
- **Unattended release qualification:** incomplete and blocked.

## Purpose and decision boundary

This record separates implementation, test results, bounded demonstrations,
failed validation, unresolved incidents, and decisions that require an operator.
It is not a product endorsement, deployment authorization, or acceptance of
residual risk. No risk is accepted by this assessment.

There is no single “ready” state for this workbench. A green component test,
successful synthetic workflow, report match, and protected research baseline
each establish different facts. The HNSW and artifact-publisher incidents,
dependency inconsistency, and operational qualification remain separate
concerns. The bounded Phase 6.12F functional integration checkpoint is
complete; this does not complete unattended release qualification.
Unattended, general-purpose use is **NO-GO** on the evidence reviewed here.
Controlled local research remains a possible policy for an operator to
consider, not an approved use decision.

## Evidence sources and checkpoint boundaries

This assessment uses committed implementation, documentation, and bounded
execution records through revision `1d12d5c3029c9299088fa3f2c59807233d0729b2`.
The earlier assessment revision `d2b7de9acef42bed71578c42e0ed1dbf5d6020d6`
is retained as historical context, not as the current evidence boundary.

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
| HNSW diagnostic | `dc871f2`, `1fcefac`, `d2b7de9` | Opt-in diagnostic foundation, a captured native query failure, and bounded later attempts that did not reproduce it; no root cause established. |
| 6.13A | `bf8951f1a3ba701e80eb2faae87f7d64d64c1014` | Provenance-safe diagnostic classification for artifact-publisher failures; diagnostic attribution improved, historical natural incident unresolved. |
| 6.13B | `c66369393d6bd55b7796d5f0eb4647079c9b2037` | Five isolated Chroma 1.5.0/1.5.9 compatibility trials; limited technical compatibility evidence, no migration or reliability approval. |
| 6.13C | `833c4a9d9f2e1ff3ddb69659ad54a76f95f34042` | Application-boundary probe and observer-validated confirmation; bounded synthetic retrieval on both versions, with a helper-created report in the confirmation study. |
| 6.12F-S1 | `d8f049129bbbee6a15e48f579ac527da294f7408` | Generation-failure persistence and explicit second invocation; retrieval failure remains at retrieval stage without fabricated downstream work. |
| 6.12F-S2 | `268e3014d7af060d09d07d884827b0ed6437db2e` | Controlled hard-link publication failure and valid orphan/catalog registration recovery tests; no natural publisher root cause established. |
| 6.12F | `1d12d5c3029c9299088fa3f2c59807233d0729b2` (parent `268e3014d7af060d09d07d884827b0ed6437db2e`) | Bounded genuine synthetic build-to-report-to-retrieval-to-evaluation integration and controlled negative/persistence scenarios; one successful invocation, not a reliability qualification. |

The 6.13B and 6.13C reports include end-of-checkpoint status text that
described their files as local or uncommitted at the time. Git history later
records the checkpoints above; those verified commit boundaries control this
assessment's status summary.

Related documentation includes the [bootstrap operations](../architecture/bootstrap-http-operations.md), [index inventory](../architecture/index-inventory.md), [report handoff design](../architecture/report-handoff-safety-design.md), [preflight operations](../architecture/report-handoff-preflight-operations.md), [Chroma verification contract](../architecture/chroma-store-verification-contract.md), and [real retrieval smoke](../architecture/manual-report-retrieval-smoke.md). Incident and compatibility records are the [HNSW report](chroma-hnsw-incident.md), [artifact-publisher report](artifact-publisher-incident.md), [Chroma version comparison](chroma-version-compatibility-613b.md), [application compatibility report](chroma-application-compatibility-613c.md), and committed [Phase 6.12F smoke guide](../architecture/manual-report-evaluation-smoke.md).

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
| Real retrieval | 6.12E, 6.13B/C, and 6.12F | Genuine synthetic retrieval passed in the committed 6.12F smoke. Phase 6.13B's five standalone trials passed; Phase 6.13C-R exercised three actual application queries per Chroma version. | An earlier native query failed with the HNSW reader error. The later bounded pass did not reproduce it. Chroma open/count/query intervals can change persistent bytes. | Bounded synthetic retrieval demonstrated; reliability qualification blocked. | A narrowly scoped, separately reviewed failure-time investigation or evidence-backed mitigation tied to the unresolved HNSW failure; no broad rerun is authorized here. |
| Evaluation execution | 6.12F-S1/S2 and committed 6.12F guide | One full smoke passed: genuine synthetic PDF/index build and builder report; report preflight; real retrieval, context formatter, EvaluationRunner, and ExperimentService; deterministic injected generation; provenance; immutable artifact/catalog registration and reload. Five retrieval-negative cases, generation failure plus separate explicit recovery, publication/registration failures, and invalid provenance were asserted. | One synthetic invocation does not establish real-corpus quality, production-model readiness, HNSW reliability, or operational qualification. The earlier failed invocation remains part of the record. | **Bounded synthetic functional integration demonstrated; general release reliability remains blocked.** | Address the independent retrieval, publisher, dependency-environment, security, and operational gates. Do not repeat already completed S1/S2 functional cases as a substitute. |
| Artifact publication/catalog | S1/S2 tests, 6.12F, and [incident report](artifact-publisher-incident.md) | Provenance-safe diagnostics are committed. S2 injected a real `os.link` failure and separately validated a valid orphan plus explicit idempotent registration recovery. The smoke's publication fault replaced `_publish()` before temp/link operations; its registration fault preserved an orphan but did not recover it. Eight later natural concurrent attempts passed. | Historical natural concurrent `ArtifactIntegrityError` remains unexplained; its original traceback, precise stage, artifact state, and catalog state were not recovered. Injected faults and eight passes do not establish natural reliability. | Functional failure semantics demonstrated; natural integrity/reliability gate unresolved. | Preserve and classify a naturally recurring failure with original stage, traceback, thread outcomes, artifact/temp-file state, and catalog state. Do not repeat the broad matrix or treat injected failures as reproduction. |
| Unattended general-purpose operation | Combined system evidence | Local loopback service boundaries, bounded queues, explicit credentials, durable job metadata, stable public errors. | HNSW and publisher failures; stale leases; no distributed ownership; no accepted recovery policy; production model/resource and monitoring behavior not validated. | **NO-GO.** | All independent release gates below, plus explicit operational ownership and accepted failure/recovery policy. |
| Research baseline | `scripts/verify_baseline.py`; Phase 6.12 checks | Latest recorded run passed with nine protected research files unchanged. The verification script hashes the manifest-listed tracked notebook Python/notebook files and checks the tracked set. | Baseline integrity does not establish pipeline quality, model validity, corpus representativeness, or validity of every metric. | Baseline guard passed; research quality remains a separate question. | Continue running the guard on relevant changes and review controlled datasets/configurations and scientific methodology independently. |

## Evaluation execution is not evaluation quality

The committed Phase 6.12F checkpoint records one bounded synthetic integration
invocation: **1 passed in 33.43 seconds, exit code 0, one invocation, no
retry**. It used a genuine synthetic PDF/index build and builder report,
report-level preflight, real Chroma retrieval, the real context formatter,
EvaluationRunner and ExperimentService, deterministic injected generation,
provenance validation, immutable artifact publication, catalog registration,
and reload. This demonstrates the functional path for that fixture; it does
not show that answers are good on real documents, production models are
available or reliable, retrieval generalizes, large datasets behave similarly,
or unattended execution is stable.

The one-case fixture has no ground-truth answer. The runner must retain the
actual M1–M19 observation statuses (`computed`, `skipped`, `unavailable`, or
`failed`) and reasons. Presence of all metric identifiers or aggregate rows is
not evidence that all metrics were numerically computed or scientifically
validated. Metric definitions, formulas, benchmark rules, and evaluation
methodology are outside this assessment and are not altered here.

The bounded positive evaluation reported 13 computed, 3 skipped, 3 unavailable,
and 0 failed metric observations. M10–M12 were skipped with
`missing_ground_truth`; M13 was unavailable with `fcd_embedder_unavailable`;
M18 was unavailable with `cpu_samples_unavailable`; and M19 was unavailable
with `ram_samples_unavailable`. The remaining M1–M9 and M14–M17 observations
were computed. Metric presence is not the same as computation, and these
synthetic results do not establish scientific validity.

## Incident 1 — Chroma HNSW reader failure

The observed error is `InternalError: Error creating hnsw segment reader:
Nothing found on disk`. The committed incident record documents CPython
3.10.11, 64-bit Windows, Chroma 1.5.0, and a failure at the native query path.
The controlled Phase 6.12F-R incident was in a fresh test process and failed
at query ordinal 1: the initial `text_vectors` query after store creation,
upsert, wrapper close, reopen, report validation, and successful text/image
count reads. The later image and repeated text queries were not reached.

The earlier Phase 6.12F integration invocation passed its positive evaluation
and five retrieval-negative cases, then failed during generation recovery at
the first native text query with `Error creating hnsw segment reader: Nothing
found on disk`. Generation was not reached, explicit recovery did not run, and
later persistence scenarios were not reached. A distinct later
Phase 6.12F invocation passed the complete bounded smoke once; it did not
reproduce the HNSW error and does not replace or erase the failed observation.
The subsequent fixed minimal-observer matrix ran four uninstrumented and four
minimal-mode fresh-process attempts; all eight passed. It did not capture a new
failure and did not establish that instrumentation prevented one. An earlier
separate 14-attempt matrix had one diagnostics-disabled query-1 failure. These
matrices have different instrumentation and conditions; no meaningful failure
probability is inferred from them. The last full Python result remains
**792 passed, 17 skipped, 1 failed**; no subsequent complete suite run replaced
it.

No failure-time Rust segment-state observation exists. Post-exit SQLite
collection-to-segment mapping and native-file presence are not atomic
failure-time evidence. The absent `index_metadata.pickle` and zero-length
`link_lists.bin` were also observed in passing fixtures and are not established
as corruption. Chroma bytes changing during open/count/query windows is an
observed side effect, but has not been connected causally to the HNSW error.
There is no deterministic reproducer, established root cause, or validated
production correction. The bounded functional integration checkpoint is
complete; HNSW reliability qualification remains blocked.

## Incident 2 — Artifact-publisher integrity failure

The dedicated [artifact-publisher incident report](artifact-publisher-incident.md)
records a separate intermittent concurrent `ArtifactIntegrityError` reported
before publication completed. The original natural-failure traceback, exact
message, artifact ID, and precise `_publish` stage were not recovered; those
details remain **unknown**. The available record describes concurrent
publication but does not establish reproducibility or root cause.

The current `ExperimentArtifactStore` validates and serializes the result,
creates a same-directory temporary file, flushes/fsyncs it, and publishes with
no-overwrite `os.link`. A pre-existing destination is read and compared to the
expected bytes and digest. This design and the passing concurrent idempotency
tests do not explain the natural failure. `tests/unit/test_artifact_store.py`
also has a deliberately injected `ArtifactIntegrityError` before the link
boundary; that controlled fault validates diagnostics and is **not** evidence
of the natural incident's cause. The committed S2 service test separately
injects a `PermissionError` at the actual hard-link boundary. The Phase 6.12F
smoke replaces `_publish()` before temporary-file/hard-link operations. Its
catalog-registration scenario leaves a valid immutable orphan without a row;
the S2 test separately verifies explicit orphan registration and idempotency.
None reproduces the natural concurrent failure.

Whether a complete artifact remained after the natural failure is **unknown**:
no post-failure filesystem snapshot or artifact ID is recorded. Whether a
catalog transaction committed is also **unknown** for the reported natural
incident. The isolated store-level concurrent test path does not itself use
the catalog, while the normal service path publishes the artifact before a
separate catalog transaction. In the latter case, a catalog failure can leave
a valid artifact without a catalog row by design. The incident evidence does
not establish that this catalog failure was the observed event. Eight
subsequent natural concurrent publication attempts passed, but they do not
establish reliability or resolve the original failure. The committed
provenance-safe diagnostic framework improves attribution; it has not
established the historical root cause. No causal link to HNSW is known.

## Chroma version and application compatibility evidence

The committed Phase 6.13B isolated comparison completed its five planned
trials: two fresh Chroma 1.5.0 trials, two fresh 1.5.9 trials, and one 1.5.9
read of a disposable copy built by 1.5.0. All five passed their bounded
standalone persistence/query checks. This supports only further isolated
compatibility evaluation. It does not establish HNSW reliability, migration
safety, rollback, security approval, or production dependency compatibility.

Phase 6.13C's original one-attempt-per-version matrix remained inconclusive
because the observer failed before calling Chroma. Its separately authorized
confirmation then completed the actual application retrieval path on both
versions, with three public collection queries per version. The confirmation
used a schema-valid, helper-created report, not a genuine builder-published
report. These bounded results do not establish reliability or approve an
upgrade. The project remains pinned to Chroma 1.5.0; no migration or rollback
is approved. See the [standalone comparison](chroma-version-compatibility-613b.md)
and [application compatibility record](chroma-application-compatibility-613c.md).

## Dependency consistency and security disposition

The committed smoke closeout recorded two unresolved `pip check` conflicts in
the existing project environment: `langchain-classic==1.0.1` requires
`async-timeout<5.0.0` on Python 3.10, while `async-timeout==5.0.1` is installed;
`omegaconf==2.3.0` requires `antlr4-python3-runtime==4.9.*`, while
`antlr4-python3-runtime==4.13.2` is installed. The smoke succeeded and no
behavioral impact was observed in that invocation. The conflicts are not
therefore declared harmless, universally unused, or resolved. The project
environment does not pass dependency consistency checks; no package was
changed as part of this assessment.

The Phase 6.13B report also records four known Chroma advisories affecting the
tested 1.5.0 and 1.5.9 releases at its review point. The experiment's embedded,
synthetic, non-networked scope narrows the exercised exposure but does not
provide security approval. No Chroma upgrade, migration, rollback, or
deployment is approved.

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

The Phase 6.13B standalone trials and Phase 6.13C-R application confirmation
also recorded, in their quiescent reader-interval snapshots, no file additions,
removals, or size changes, five digest changes, and four timestamp-only
changes. These observations cover open/count/query/close intervals and do not
identify which operation changed a file. The 6.12F successful run asserted
that its report and application index-manifest bytes were unchanged, but no
independently retained top-level Chroma digest-delta summary is available.
Chroma reads are not filesystem-read-only by these observations.

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
| **C — Chroma dependency evaluation** | The bounded 1.5.0/1.5.9 comparisons are complete; no production dependency change is approved. | The candidate was technically feasible in the bounded synthetic trials, but security advisories, persistence mutation, migration/rollback uncertainty, and HNSW reliability remain. The project stays pinned to 1.5.0. Any future candidate requires separate security, full-contract compatibility, migration, and rollback review. |

The previously recommended provenance-safe publisher diagnostic framework is
committed in Phase 6.13A. It classifies harness and publisher evidence more
carefully, but the historical natural failure remains unexplained; the eight
later passing natural attempts are not a reliability result. The standalone and
application Chroma comparisons are also complete within their stated limits.
Further incident capture should be considered only under a separate review and
fixed budget: preserve the first naturally recurring failure's stage, original
traceback, concurrent caller outcomes, artifact state, and catalog state. This
assessment authorizes no new experiment or production change. No broad rerun or
repetition of the completed S1/S2 tests is recommended.

Formal local-research risk acceptance is another option only for an authorized
operator. It changes a usage decision, not the technical evidence or Phase
6.12F completion status.

## Independent release gates

| Gate | Current decision | Evidence required to change the decision |
|---|---|---|
| **Index construction** | Bounded synthetic fresh-only construction and genuine builder report demonstrated. | General corpus/model/resource behavior, capacity, backup, and crash qualification remain unestablished; any such run requires separate scope and supervision. |
| **Retrieval** | Bounded synthetic retrieval demonstrated; **reliability qualification blocked** by the historical HNSW failure. | A failure mechanism or separately reviewed evidence-backed mitigation and failure policy. Define a reliability objective before any new sampling; no repeat matrix is authorized here. |
| **Evaluation and persistence** | Bounded synthetic end-to-end functional checkpoint complete; S1/S2 controlled failure semantics covered. Release reliability remains incomplete. | Resolve/disposition the HNSW, natural publisher, dependency consistency, security, and operational gates. The completed functional tests need not be repeated as substitutes for those gates. |
| **Publisher integrity** | Injected failure semantics and explicit orphan recovery demonstrated; natural concurrent incident unresolved. | Natural failure evidence with original stage/traceback and artifact/catalog state, or a separately reviewed cause/correction and reliability argument. |
| **Dependency environment and security** | Project remains pinned to Chroma 1.5.0; two `pip check` conflicts and unresolved Chroma advisories remain. | Separately disposition dependency consistency and security exposure; no package changes or Chroma migration are approved here. |
| **Operations** | Local boundaries demonstrated; unattended operations not qualified. | Approve ownership for credentials, trusted roots, backups, stale reservation response, service lifecycle, monitoring/log retention, model/resource availability, and visible operator failure handling. Browser E2E is still absent for the frontend. |
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
| Intermittent HNSW query failure — **Critical** | Captured at first text query after open/count in a fresh process; later bounded HNSW matrices and one full smoke passed without reproducing it. Retrieval and evaluation affected; cause unknown. | Plan/report/count checks, explicit errors, isolated diagnostics, no automatic retries or repair. | No cause, failure-time Rust state, or accepted failure policy. Preserve first exception/stage and bounded segment evidence if it recurs. | Narrow, separately reviewed native/upstream diagnosis or evidence-backed mitigation; no broad rerun authorized. **Not accepted.** |
| Artifact-publisher `ArtifactIntegrityError` — **High** | Natural concurrent pre-publication failure reported; original message/traceback, artifact persistence, catalog state, and reproducibility are unknown. Eight later natural attempts passed without resolving it. | Content-addressed IDs, fsync/temp file, exclusive no-overwrite link, strict existing-byte validation, catalog transaction/reload, provenance-safe diagnostic classification. | No preserved natural failure boundary or outcome snapshot. Capture exact stage, thread outcomes, temp/final file and catalog evidence if a natural failure recurs. | Focused natural incident attribution; separate review before production correction. **Not accepted.** |
| Dependency inconsistency — **Moderate** | `langchain-classic==1.0.1` requires `async-timeout<5.0.0` on Python 3.10, but `5.0.1` is installed; `omegaconf==2.3.0` requires `antlr4-python3-runtime==4.9.*`, but `4.13.2` is installed. The bounded smoke succeeded and no behavioral impact was observed; the conflicts remain unresolved. | No package was changed during the checkpoint; disposable Chroma environments are separate. | The project environment does not pass `pip check`; absence of observed impact in one smoke does not establish harmlessness or full path irrelevance. | Separate dependency/environment review and disposition; no install or pin change authorized here. **Not accepted.** |
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
- The committed Phase 6.12F guide records the one successful bounded smoke and
  its child results. The pytest wrapper validated the nested result, but the
  complete top-level JSON and exact report-preflight response were not archived
  separately. Four evaluation child records are retained and record exit code
  zero. The top-level `all_child_processes_exited` field was set after bounded
  subprocess waits, not by an independent process enumerator. Build/preflight
  PIDs were not retained, and there is no independently retained top-level
  Chroma digest-delta summary for that successful run.
- The prior failed Phase 6.12F observation remains separately retained and
  hashed; the successful run did not overwrite or supersede it. The last full
  Python result remains **792 passed, 17 skipped, 1 failed**. No later complete
  suite run replaced it, and none was run for this documentation assessment.
- The smoke closeout found two `pip check` conflicts in `rag-env`; no package
  changes followed. Their impact was not observed in the single successful
  invocation, but consistency remains unresolved.
- Frontend test/typecheck/build and browser limitations are inherited from the
  prior frontend checkpoint; no new frontend validation was performed here.
- The baseline verifier covers the manifest-listed tracked research files; it
  does not validate model weights, datasets, local runtime stores, or all
  generated artifacts.

## Final assessment

The workbench has separately committed local workflows for fresh index
construction, report discovery, manual report handoff, isolated retrieval, and
one bounded synthetic evaluation integration. Report inventory and readiness
remain report-level. Chroma persistence is mutation-capable during nominal
reads. The Phase 6.12F bounded functional integration checkpoint is complete
and committed; this does not complete operational or reliability qualification.

The HNSW failure is confirmed and unresolved. The natural concurrent
artifact-publisher incident is separately documented and its root cause is
unknown. The project environment has unresolved dependency consistency
conflicts, and operational/security qualification is incomplete. Research
quality is not established by the synthetic tests. **Unattended general-purpose
use remains NO-GO. No residual risk is accepted, no production deployment is
authorized, and no Chroma migration is approved.**

Any limited local-research use remains a candidate requiring a separate
explicit operator decision. No automatic retry, repair, stale-lease takeover,
report activation, or production workflow expansion is authorized. Any
production correction, dependency change, migration, or risk acceptance
requires its own reviewed checkpoint and approval gate.
