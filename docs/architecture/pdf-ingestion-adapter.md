# PDF ingestion and chunking adapter

Phase 2.3 adds `backend/engine/ingestion.py`, a small engine boundary for
local PDF extraction and layout-aware chunking. It accepts either a validated
`ResolvedRunConfig` or a verified `RunPlan`; when given a plan it calls
`RunPlan.verify_integrity()` before reading the stored configuration snapshot.
Direct configurations are revalidated and must pass the non-destructive
workbench safety policy. No mutable research `cfg` singleton is imported or
used.

## Ported behavior

The module ports the selected definitions from the protected
`notebooks/rag_pipeline.py`:

- recursively sorted PDF discovery and document/page order;
- PyMuPDF text-block extraction sorted by `(y0, x0)` with the configured
  `chunk_min_text_len` rule;
- soft-mask, duplicate-xref, alpha, size, white-pixel, variance, and aspect
  ratio image handling;
- source filename, page number, image ID, path, and bounding-box metadata;
- `vertical_gap`, `merge_bbox`, `stable_chunk_id`, `bbox_overlap`, caption
  detection, and related-image association;
- tiktoken-based maximum chunk tokens, vertical-gap splitting, overlap
  step-back, LangChain `Document` output, bounding boxes, and `semantic_tag`.

The adapter passes every value explicitly. It does not initialize embeddings,
rerankers, Chroma, Ollama, GPU telemetry, HTTP clients, or the full research
module.

## Storage and failures

`extract_pdfs()` receives a caller-owned `storage_root`. The configured
`images_dir` and each PDF-stem subdirectory are resolved with
`resolve_owned_path()` before output directories or image files are created.
Existing image paths are not overwritten. PDF input is a local configured
directory in this phase, not an upload path or quarantine policy.

The return value is `PdfExtractionResult` with page dictionaries matching the
successful research shape, plus `successful_files` and structured
`failed_files`. A later-page exception can leave already extracted pages in
`pages`, but the affected PDF is still reported as failed and is not listed as
successful. Image-level failures are reported through logging and skipped as
in the research implementation.

PDF-stem image directories are retained for parity. Two different PDFs with
the same stem can therefore collide; this is not a final multi-user identity.
Unique document IDs, verified manifests, versioned storage, atomic ingestion,
and explicit activation belong to a later persistence phase.

## Parity and limits

Tests generate small PDFs in pytest temporary directories and compare adapter
pages and image metadata with the actual selected research definitions loaded
through `tests.support.source_loader`. Chunk tests compare `Document` content
and metadata, including order, overlap, bounding boxes, stable IDs, semantic
tags, and related-image IDs. Only output-root prefixes are normalized.

The current RunPlan document hash is caller-supplied; this adapter does not
prove that it matches the PDF directory contents. It also does not impose the
later upload byte/page/pixel policy or create a document manifest. There is no
versioned index, Chroma integration, or end-to-end retrieval adapter yet.
