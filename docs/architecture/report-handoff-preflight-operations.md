# Read-only report handoff preflight

## Purpose and boundary

`scripts/report_handoff_preflight.py` checks one build report that an operator
names explicitly, before the operator manually configures the ordinary
experiment runtime. It does not select a report automatically, activate an
index, write runtime settings, create a handoff file, start another process,
open Chroma, acquire a writer lease, build an index, or run an experiment.

A successful result is labeled
`report_compatible_for_manual_review`. It means the named report passed the
existing bounded report validator for the current trusted PDF source and
resolved configuration during this command. The command also observes the
fixed index directory, its identity manifest, the Chroma directory's
presence, and the incomplete/writer-reservation markers. These observations
do not verify Chroma collections or vector contents.

The result always states:

```text
check_level: report
store_checked: false
```

Do not treat the result as proof of retrieval success, model readiness, active
runtime configuration, or permission to execute an evaluation.

## Invocation

Run from the repository root using a complete resolved configuration JSON
file and the trusted local roots:

```powershell
.\rag-env\Scripts\python.exe scripts/report_handoff_preflight.py `
  --config "<complete-resolved-config.json>" `
  --source "<trusted-existing-pdf-directory>" `
  --storage-root "<trusted-index-storage-root>" `
  --build-id "<32-character-lowercase-build-id>" `
  --expected-manifest-hash "<confirmed-lowercase-sha256>" `
  --expected-index-fingerprint "<confirmed-lowercase-sha256>"
```

The build ID is the only report selector. The command constructs the fixed
relative path:

```text
build-artifacts/<build-id>/build-report.json
```

It does not accept an arbitrary report path, scan for a preferred report, or
interpret lexical build-ID order as recency. If several reports match, the
operator must run the preflight again with the chosen build ID.

The configuration must include every `ResolvedRunConfig` field and every
`WorkbenchSafetyPolicy` field. Duplicate JSON keys, non-finite numbers,
missing fields, extra fields, destructive settings, relative roots, symlinks,
and overlapping source/storage roots are rejected. The current manifest and
RunPlan are recomputed. Supplied hashes are confirmation constraints; they do
not override those calculations.

## What is inspected

The preflight reuses `IndexInventoryService` and the shared report validator
for report schema, build ID, hierarchy, successful status, partial-persistence
state, identity, count, containment, size, and symlink checks. It rechecks the
source manifest after report inspection and compares report file identity
before and after inspection where filesystem metadata makes a change
observable.

After a compatible report is found, the command performs bounded metadata-only
checks beneath the trusted storage root:

- The expected fingerprint directory exists and is not a symlink.
- `index-manifest.json` is a bounded, strict JSON object whose complete schema
  and fingerprint/manifest identities match the current RunPlan.
- The `chroma` directory exists and is not a symlink. Its contents and
  collections are not opened or inspected.
- No `.fresh-build-incomplete` marker is present.
- No fingerprint-specific `.writer-leases` reservation is present at the
  observation points. Existing reservations are never removed or taken over.

The directory, manifest, and marker observations are not Chroma verification.
The command does not assert collection existence, persisted record counts,
readability, or protection from uncooperative external writers. The actual
retrieval boundary remains responsible for opening and validating the store.

## Successful output and manual handoff

The bounded JSON output includes the explicit build ID, relative report
reference, current manifest and index fingerprint, validated report counts
and status, report-only verification fields, fixed-path observations, and a
point-in-time warning. It contains no absolute source or storage path and no
report body.

After reviewing the output, the operator may manually set these values in the
environment used to start the ordinary experiment runtime:

- `RAG_WORKBENCH_INDEX_STORAGE_ROOT`: the same trusted storage root supplied
  to the preflight.
- `RAG_WORKBENCH_BUILD_REPORT_PATH`: that storage root joined with the
  displayed relative `report_reference`.

Restart the ordinary runtime. Its configured-report requirement, readiness
checks, and retrieval-time store validation remain authoritative. This
command never writes an environment file or changes the current process
environment.

## Failure codes and exit status

Failures print only a stable `error_code`, without a traceback or private
absolute path. Invalid input and unsafe evidence exit with status `2`;
unavailable trusted storage/report evidence exits with status `3`; a
compatible report-level result exits with status `0`.

Important codes include `configuration_missing`, `configuration_invalid`,
`destructive_configuration_forbidden`, `source_root_invalid`,
`storage_root_invalid`, `source_storage_overlap`, `manifest_changed`,
`manifest_confirmation_invalid`, `index_fingerprint_changed`,
`build_id_invalid`, `report_missing`, `report_failed`, `report_partial`,
`report_corrupt`, `report_identity_mismatch`, `report_path_unsafe`,
`report_oversized`, `report_changed_during_inspection`,
`incomplete_marker_present`, `index_directory_missing`,
`index_directory_unsafe`, `index_manifest_unavailable`,
`index_manifest_mismatch`, `chroma_directory_missing`,
`writer_reservation_present`, `index_changed_during_inspection`, and
`storage_unavailable`.

No failure triggers repair, retry, deletion, stale-reservation recovery, or
report rewriting. Preserve incomplete and partial build evidence for separate
operator investigation.

## Freshness limits and incidents

The command is a bounded point-in-time inspection. Source files, report bytes,
index metadata, or Chroma files may change after exit or between this check and
ordinary runtime startup. File identity rechecks detect common concurrent
replacement/modification but are not a lock, durable content pin, or guarantee
against same-metadata changes. The existing writer lease coordinates only
cooperating operations while held; preflight does not hold one, and arbitrary
external Chroma writers are not governed by it.

The historical Chroma HNSW reader failure (`Error creating hnsw segment
reader: Nothing found on disk`) and intermittent concurrent artifact-publisher
integrity incident remain unresolved. This preflight does not diagnose or fix
either issue. A report-compatible result is not a guarantee that either
collection can be read successfully.

See [report handoff and activation safety design](report-handoff-safety-design.md),
[read-only index inventory](index-inventory.md), and
[local runtime operations](local-runtime.md) for the surrounding contracts.
