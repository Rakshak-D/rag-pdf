# Manual Report Evaluation Smoke

## Purpose

Run the bounded integration check with:

```powershell
.\rag-env\Scripts\python.exe scripts/manual_report_evaluation_smoke.py
```

The harness creates a synthetic PDF under a temporary root, builds a fresh
real Chroma index through the established builder, validates the builder's
genuine report, and invokes the existing report-handoff preflight with the
explicit build ID, manifest hash, and index fingerprint. The preflight result
is report-level evidence only. The harness then explicitly supplies that
report path to `ExperimentService`; there is no report discovery or automatic
selection in the service call.

The evaluation uses one semantic text case, the real `retrieve_for_plan()`
path, the real `ContextFormatter`, `EvaluationRunner`, and
`LocalGenerationAdapter`, and an injected deterministic embedding adapter and
fake local generation client. The fake client only returns its fixed answer
when the retrieved evidence marker is present in the actual generation
context. The adapter settings and all generated files are confined to
temporary test-owned roots. Offline environment flags prevent model downloads;
the smoke does not contact Ollama or use production model weights.

## What success demonstrates

For this synthetic fixture, the smoke verifies that:

- The genuine build report matches the selected RunPlan and source manifest.
- Both persisted Chroma collection counts are checked against the report by
  the existing retrieval pipeline.
- A text record persisted by the build is retrieved and reaches the context
  formatter and generation prompt.
- The generation result, trace, evaluation report, case outcome, and metric
  observations satisfy existing provenance contracts.
- All M1–M19 observation and aggregate identifiers are present, with the
  runner's actual `computed`, `skipped`, `unavailable`, or `failed` statuses.
- `ExperimentService`, `ExperimentArtifactStore`, and `ExperimentCatalog`
  publish and reload a canonical result, with catalog aggregates matching the
  saved evaluation.
- Retrieval ownership is released on its acquiring thread, and child
  processes exit before the fixture is removed.

This is one controlled service-level integration case, not a live HTTP job
submission, browser test, general index verifier, or measurement of model
quality. It does not establish that production embedding or generation models
work, nor that a successful fixture predicts scientific evaluation results.
The evaluation intentionally has no ground-truth answer. Metrics that require
one or a model-backed embedder may therefore be skipped or unavailable; the
smoke preserves and reports those statuses rather than inventing scores.

## Failure boundaries exercised

Using the same one-time genuine build, isolated cases exercise missing and
identity-mismatched reports, mismatched persisted counts, a held writer
reservation, and an incomplete marker. The runner records retrieval-stage
failures without invoking generation or fabricating evidence. A deterministic
generation exception retains the actual retrieval evidence and is persisted
as a failed case; a later separate explicit service invocation demonstrates
recovery without an automatic retry.

The smoke injects its artifact-publication fault by replacing
`store._publish()`, before the temporary-file and hard-link operations. It
verifies that no artifact or catalog row is claimed at that injected boundary;
it does not exercise a real hard-link failure. The catalog-registration fault
verifies the distinction between an already published immutable artifact and
absent catalog registration: the artifact is preserved. The smoke does not
recover that orphan. The committed S2 service tests separately exercise the
real `os.link` failure and explicit orphan registration. A deliberately
altered runner result is rejected by the service provenance boundary before
persistence.

These cases use only temporary fixture state. They do not delete partial
artifacts, alter the genuine build report, remove a pre-existing reservation,
or modify an operator's index. A marker created for its isolated negative
case is contained within the disposable test root.

## Bounded successful smoke following the earlier HNSW failure

On 2026-11-02, the exact integration node
`tests/integration/test_manual_report_evaluation_smoke.py::test_real_retrieval_evaluation_and_catalog_round_trip`
was invoked once under CPython 3.10.11 on Windows AMD64 with Chroma 1.5.0.
Pytest reported **1 passed in 33.43 seconds**, exit code 0, and no timeout.
The retained attempt identifier was
`85c8364312e84022ad2c9528cda28b27`. No retry or second smoke invocation was
performed. The earlier failed run remains a separate retained observation;
the pass does not replace it.

The retained run records and the passing wrapper assertions support this
bounded functional result:

- A synthetic PDF was processed by the genuine `build_index()` path with
  `creation_policy="fresh_only"`. Deterministic text and image embedding
  adapters supplied vectors; the builder produced and validated its report.
  The report matched the reconstructed manifest and RunPlan and recorded one
  text and one image record.
- Report handoff preflight returned
  `report_compatible_for_manual_review` with `check_level: report`,
  `store_checked: false`, and unchanged storage. This is report-only evidence,
  not a Chroma health check. The pytest wrapper asserted the preflight
  projection and selected top-level fields during the run; neither the exact
  preflight response nor the complete top-level JSON was separately archived.
  The retained pytest output contains only the test summary; build and
  per-child result records are retained separately.
- The positive case used the real `retrieve_for_plan()` path,
  `ChromaVectorStore.open_existing()`, report/count validation, real
  `ContextFormatter`, `EvaluationRunner`, `ExperimentService`, artifact store,
  and catalog. Its observer wrapped the store query boundary and recorded one
  returned `text_vectors` query (ordinal 1), returning
  `synthetic.pdf_p1_c238e11c5`. The real generation adapter used a deterministic
  fake client and injected vector adapter; it did not call a production model.
  Provenance checks passed and the persisted evaluation reloaded identically
  with a succeeded catalog record and 19 metric aggregate rows.
- Metric outcomes were M1–M9 computed; M10–M12 skipped with
  `missing_ground_truth`; M13 unavailable with `fcd_embedder_unavailable`;
  M14–M17 computed; M18 unavailable with `cpu_samples_unavailable`; and M19
  unavailable with `ram_samples_unavailable`. Totals were 13 computed, 3
  skipped, 3 unavailable, and 0 failed. These synthetic observations do not
  establish research quality. The corresponding aggregate statuses were
  `computed` for M1–M9 and M14–M17, and `no_eligible_values` for M10–M13 and
  M18–M19.

The five persisted retrieval-negative cases all returned a failed
retrieval-stage evaluation as asserted: missing report and report identity
mismatch fail during report validation before opening Chroma; persisted count
mismatch opens the store and checks counts, then fails before a vector query;
an active cooperating writer reservation and a test-owned incomplete marker
fail closed at the store boundary before a vector query. The active reservation
owner was preserved and released by the test; the incomplete marker was
removed only after its preservation assertion inside the disposable fixture.

The generation-recovery child recorded two successful native text queries:
ordinal 1 before the controlled generation exception and ordinal 2 during a
separate explicit service invocation. The first result was persisted as a
failed generation-stage case with retrieved evidence and formatted context;
generation was called once and no automatic retry occurred. The explicit
second invocation succeeded with a distinct artifact and succeeded catalog
record. Its deterministic generator was still a test adapter, not a
production-model evaluation.

The persistence child recorded three separate cases. The `_publish()`
replacement raised before the real temporary-file/hard-link publication
operations and left no artifact or catalog row. The `_register()` replacement
failed after the catalog save path had published and reloaded the immutable
artifact, leaving the artifact without a catalog row; this smoke did not
perform explicit orphan recovery. The committed S2 service test covers actual
hard-link failure, and its catalog test covers explicit orphan registration,
19 unique metric rows, and idempotency. The invalid-provenance case ran the
real runner through a narrow test wrapper that altered its returned fingerprint;
the service rejected it with `evaluation_provenance_mismatch` before artifact
or catalog persistence.

All four evaluation child result files record child exit code 0 and a child
interpreter PID. The parent waited for each bounded `subprocess.run()` call to
return; a later liveness inspection found none of those four PIDs present.
The top-level `all_child_processes_exited` field is set after those waits, not
by an independent process enumerator. Build and preflight subprocesses also
completed, but their PIDs are not retained. No evaluation child timed out.
The retained fixture had no writer-reservation directory or
`.artifact-*.tmp` file at audit time. The smoke's in-run assertions confirmed
that the build report and application index manifest remained byte-identical.

Chroma snapshots in the retained query records list file sizes and modification
times. The more complete top-level Chroma digest-delta report was part of the
nested result and was not separately retained, so this checkpoint does not
claim a verified digest-change count for this run. Chroma operations are not
therefore described as filesystem-read-only.

The original Phase 6.12F failure is preserved separately: its positive case
and five retrieval-negative cases passed, then the generation-recovery case
failed at the first native text query with `Error creating hnsw segment
reader: Nothing found on disk`. Generation was called zero times; explicit
recovery and later persistence cases were not reached. The two retained
failure JSON records still match their recorded SHA-256 values:

| Original record | SHA-256 |
|---|---|
| `smoke-failure.json` | `F1E640DBF07CAD12CB2D0100FCFFD7D1FB3E9D16C8517728BDB78000B94D9128` |
| Generation-recovery `failure.json` | `D65114915E5F19753BE88D2245B19708562BDCD0E5DF23467585AFBD6AB6D19D` |

The later one-run pass establishes a bounded synthetic functional integration
checkpoint only. It does not resolve intermittent HNSW reliability, the
separate unexplained natural concurrent publisher incident, production-model
availability, migration or rollback safety, or research quality. See the
[HNSW incident record](../audit/chroma-hnsw-incident.md) and
[artifact-publisher incident record](../audit/artifact-publisher-incident.md).
Chroma remains pinned at 1.5.0; no migration or deployment approval follows.

One read-only `pip check` during this review reported two existing environment
conflicts: `langchain-classic 1.0.1` requires `async-timeout <5.0.0` on Python
3.10, while `async-timeout 5.0.1` is installed; and `omegaconf 2.3.0` requires
`antlr4-python3-runtime 4.9.*`, while 4.13.2 is installed. The exercised
builder imports `langchain_core.documents` (installed `langchain-core 1.2.10`);
the smoke and its resolved application path do not import `langchain-classic`,
`omegaconf`, `async_timeout`, or `antlr4`. No behavioral impact was observed
in this run, but the environment does not pass dependency consistency and
these conflicts remain unresolved. No package was changed.

## Chroma and lifecycle limits

Evaluation explicitly opens persistent Chroma through retrieval. As recorded
in the Phase 6.12C contract investigation, this is not a filesystem-read-only
operation: Chroma may change persistent SQLite or HNSW bytes or timestamps.
The harness reports observed changes and requires the selected build report
and application index identity manifest to remain byte-identical. It does not
attribute a particular Chroma write to a specific internal call and does not
require Chroma files to remain byte-identical.

The wrapper lease is expected to be released after each retrieval. Closing the
wrapper does not prove Chroma's cached internal system has stopped; the smoke
uses process exit to bound that lifetime. The Phase 6.12C conclusion remains
**NO-GO for an operation advertised as a filesystem-read-only Chroma
verifier**. Count agreement and one successful deterministic query do not
prove complete vector-content integrity or future retrieval reliability.

The historical HNSW reader failure (`Error creating hnsw segment reader:
Nothing found on disk`) and the intermittent concurrent artifact publisher
`ArtifactIntegrityError` remain unresolved. This smoke neither fixes nor
suppresses those incidents. If either occurs, its original diagnostics should
be preserved and classified; do not repeatedly rerun to hide it.
