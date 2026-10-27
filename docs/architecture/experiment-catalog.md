# SQLite experiment catalog

`ExperimentCatalog` is a local metadata index for the immutable JSON artifacts
written by `ExperimentArtifactStore`. The JSON file remains the source of truth
for complete traces, outcomes, observations and aggregates; SQLite stores only
validated discovery metadata and queryable metric rows.

The catalog uses schema version 1 (`PRAGMA user_version`) and refuses future or
incompatible schemas. Each registration acquires a SQLite write reservation
before checking whether the artifact row exists. The existence check and
conditional writes therefore serialize across connections. One transaction
covers the artifact row, its six ordered ablation variants when applicable,
metric aggregates, and comparison states. Re-registering the same immutable
artifact is idempotent under concurrent callers when all stored metadata and
related rows agree; conflicts continue to fail integrity validation. Foreign
keys are enabled on every catalog connection, with a bounded busy timeout and
full SQLite synchronization. The
database path is explicit, must be outside the artifact-store root, and must
have an existing non-symlink parent; the catalog does not create arbitrary
directory trees.

Artifact publication happens before catalog registration. Registration reloads
and validates the published bytes, then derives all catalog fields from that
strict result. These filesystem and SQLite operations are separate; they are
not one cross-resource atomic transaction. If publication succeeds and the
SQLite transaction later fails, the immutable artifact can remain in the store
without a catalog row. The transaction rollback leaves no partial artifact,
variant, metric, or comparison rows, and it does not delete or replace the
published file.

After the registration problem is resolved, callers can explicitly recover
metadata with `register_existing_evaluation()` or
`register_existing_ablation(..., ablation_plan=...)`. Recovery reloads and
validates the existing artifact before rebuilding its catalog rows; ablation
recovery requires the accepted plan. Repeating recovery is idempotent when all
stored metadata agrees. Recovery is explicit: the catalog does not scan for
orphans, repair them in the background, or provide another recovery service.

Loads require a registered row, reload the artifact through the artifact store,
and cross-check the catalog against the reconstructed typed result. Catalog
metadata alone cannot fabricate a valid result. Listing supports bounded,
parameterized filters and stable ordering; metric and ablation-comparison rows
preserve nullable values and explicit statuses rather than turning unavailable
or ineligible measurements into zero.

The catalog stores no raw queries, prompts, passages, answers, captions, image
bytes, absolute paths or arbitrary exception text. Artifact and database
filesystem errors are exposed through bounded public error classes. SQLite
transactions and the artifact store's atomic publication provide local
durability appropriate to the selected filesystem, but directory and process
crash guarantees vary by platform. SQLite serializes writers to this local
catalog; it does not provide distributed coordination or make the application
a multi-host job system. Neither the catalog nor an unkeyed SHA-256 artifact
ID is a signature or proof of authorship.

`ExperimentService` is the application-level caller for these operations. It
validates plans, benchmark identity, trace provenance and ablation comparisons
before asking the catalog to register a result, while catalog read methods
remain the authority for artifact and metadata integrity checks. The service
does not add a second persistence format or background execution layer.
