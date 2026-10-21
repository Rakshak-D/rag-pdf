# Deterministic local index builds

`backend.engine.index_builder.build_index()` is the Phase 4.1 orchestration
boundary. It verifies a `RunPlan`, checks source/storage separation, rebuilds
the actual PDF manifest, and requires its hash to match before staging,
creating build artifacts, loading models, or opening Chroma. It then uses the
existing manifest-bound extraction and layout-aware chunking adapters,
constructs image objects, encodes non-empty text and image inputs, validates
both embedding arrays, writes the versioned Chroma collections, and verifies
stored counts and IDs.

Each attempt receives a generated operational build ID. Generated extraction
images and the atomically written `build-report.json` live beneath
`storage_root/build-artifacts/<build-id>/`; attempts do not share image output.
The build ID is not part of the index fingerprint, so stable source/config
inputs retain stable chunk and image IDs. Rerunning a plan uses Chroma upserts
with those stable IDs, while interrupted writes are explicitly not claimed to
be transactionally atomic.

Text records use stable chunk IDs and retain chunk documents. Image records use
stable image IDs and serialize source, page, path, caption, and bounding-box
metadata. Scalars remain Chroma scalars; `None` becomes an empty string and
lists, tuples, and dictionaries become deterministic JSON strings. Unsupported
metadata values are rejected.

Build results distinguish `completed`, `completed_with_warnings`, and
`failed`. Individual image failures and caption fallbacks produce warnings and
can still yield a usable partial modality result. Preflight rejection raises
without a storage root, model load, Chroma access, or build report. Once the
operational attempt begins, chunking, embedding, store, persistence, and
verification failures return a report with schema version `1`, a controlled
`failure_stage`, `failure_type`, sanitized `failure_message`, prepared /
embedded / persisted counts, and `partial_persistence`. Extraction failures
are reported at the `extraction` stage before model loading or Chroma access.

Reports are written by temporary-file replacement in the build-attempt
directory. A report-write failure raises `ReportWriteError`; atomic report
writing does not make Chroma vector persistence transactional. If text has
been written and a later image write or verification fails, the report marks
that vector writes may remain. The builder does not delete or silently repair
such state.

Embedding outputs are checked for two-dimensional numeric finite arrays,
nonzero dimensions, and exact row alignment before `ChromaVectorStore.open()`.
Text and image embedding spaces are validated independently. Empty modalities
do not initialize their model or issue an empty embedding call.

Generated image paths are build-specific under
`storage_root/build-artifacts/<build-id>/images/`. A repeated build can update
persisted image metadata to reference the latest attempt's image path; it is
therefore not correct to claim that every metadata field is identical across
attempts, even though stable vector IDs and counts remain the same.

This service stores an index but does not activate it. There is no active-index
pointer, SQLite registry, cross-component transaction, retrieval orchestration,
or model-weight provenance enforcement. Model names and pretrained identifiers
remain configuration identity, not proof of immutable model artifacts.
