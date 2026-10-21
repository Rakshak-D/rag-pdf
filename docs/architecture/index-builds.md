# Deterministic local index builds

`backend.engine.index_builder.build_index()` is the Phase 4.1 orchestration
boundary. It verifies a `RunPlan`, checks source/storage separation, rebuilds
the actual PDF manifest, and requires its hash to match before staging,
creating build artifacts, loading models, or opening Chroma. It then uses the
existing manifest-bound extraction and layout-aware chunking adapters,
constructs image objects, encodes non-empty text and image inputs, writes the
versioned Chroma collections, and verifies stored counts and IDs.

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
can still yield a usable partial modality result. Any PDF extraction failure
returns a failed report before model loading or Chroma access. A failure after
vector persistence begins returns a failed report noting that partial writes
may exist; it does not delete or silently repair them.

This service stores an index but does not activate it. There is no active-index
pointer, SQLite registry, cross-component transaction, retrieval orchestration,
or model-weight provenance enforcement. Model names and pretrained identifiers
remain configuration identity, not proof of immutable model artifacts.
