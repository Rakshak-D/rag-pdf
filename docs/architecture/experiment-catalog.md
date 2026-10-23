# SQLite experiment catalog

`ExperimentCatalog` is a local metadata index for the immutable JSON artifacts
written by `ExperimentArtifactStore`. The JSON file remains the source of truth
for complete traces, outcomes, observations and aggregates; SQLite stores only
validated discovery metadata and queryable metric rows.

The catalog uses schema version 1 (`PRAGMA user_version`) and refuses future or
incompatible schemas. Each registration is one SQLite transaction covering the
artifact row, its six ordered ablation variants when applicable, metric
aggregates, and comparison states. Foreign keys are enabled on every catalog
connection, with a bounded busy timeout and full SQLite synchronization. The
database path is explicit, must be outside the artifact-store root, and must
have an existing non-symlink parent; the catalog does not create arbitrary
directory trees.

Registration first writes and validates through the content-addressed artifact
store, then derives all catalog fields from a strict reload. Repeating a
registration is idempotent only when every metadata and related row agrees.
If file publication succeeds but the SQLite transaction fails, the artifact is
left intact and `register_existing_evaluation()` or
`register_existing_ablation(..., ablation_plan=...)` can recover it later.
Recovery is plan-bound for ablation artifacts and is also idempotent.

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
crash guarantees vary by platform. Neither the catalog nor an unkeyed SHA-256
artifact ID is a signature or proof of authorship.

`ExperimentService` is the application-level caller for these operations. It
validates plans, benchmark identity, trace provenance and ablation comparisons
before asking the catalog to register a result, while catalog read methods
remain the authority for artifact and metadata integrity checks. The service
does not add a second persistence format or background execution layer.
