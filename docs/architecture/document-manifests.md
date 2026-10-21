# Verified document manifests and source snapshots

Phase 2.4 binds extraction input bytes to `RunPlan.document_manifest_hash`.
`backend/engine/manifest.py` is pure during manifest construction and uses no
models, network clients, Chroma, Ollama, or generated output.

## Manifest identity

`DocumentManifest` contains schema version `1`, sorted entries, and a
`content_hash`. Each `DocumentManifestEntry` contains:

- a normalized POSIX path relative to the supplied source directory;
- the exact byte size;
- the incremental SHA-256 of the PDF bytes; and
- `doc_` plus a SHA-256 derived from the relative path and content hash.

The content hash is SHA-256 over canonical UTF-8 JSON with sorted keys and
compact separators, excluding absolute source paths and the content-hash
field itself. Adding, removing, renaming, or modifying a PDF changes it;
unchanged files at the same relative paths produce the same value under a
different absolute source root. Discovery deliberately retains the research
`rglob("*.pdf")` matching semantics; matching case behavior therefore follows
the host Python/pathlib platform. Entries are serialized with POSIX
separators, and symlinked files and directories are rejected.

Hashing checks file size and modification time before and after incremental
reads. Snapshot copying repeats the checks and verifies the destination size
and digest. These checks detect ordinary concurrent changes but are not a
cryptographic authenticity mechanism.

## Verification and staging

The PDF source root and caller-owned snapshot/extraction storage root must be
disjoint resolved directory trees. Equal roots and either root nested beneath
the other are rejected before manifest discovery, directory creation, or file
copying. This prevents staged PDFs from becoming part of a later recursive
source discovery pass.

`verify_document_manifest()` rebuilds the current manifest and compares the
exact relative file set, sizes, content hashes, and document identities. It
raises `ManifestMismatchError` with added, missing, changed, or unsafe paths;
it never regenerates or approves the expected manifest.

`stage_document_snapshot()` first verifies the source and then copies into a
temporary directory beneath the caller-owned storage root. It verifies every
copied file and the complete PDF file set before atomically renaming the
temporary directory to `snapshots/<manifest_hash>`. A verified final snapshot
is reused. An existing mismatched final snapshot fails without overwrite or
deletion. Failed staging removes only the exact temporary directory created by
that call after containment checking. Directory rename is atomic within the
same filesystem on the supported local filesystem; universal crash
consistency is not claimed.

The ingestion adapter builds this manifest for both direct resolved-config
calls and RunPlan calls. For a RunPlan, the current hash must equal the plan's
hash before staging or image-output creation. Extraction then reads the staged
snapshot and returns the actual manifest hash used.

`storage_root/snapshots/` is reserved for immutable source snapshots. The
configured image root and every PDF-stem image directory are resolved with
`resolve_owned_path()` and rejected if they alias or reside in that reserved
namespace. Dedicated image directories elsewhere under the owned storage root
remain valid.

## Duplicate-stem restriction and limits

The current research extraction identity uses the PDF stem for image
directories and source filenames in chunk IDs. The adapter therefore rejects
case-folded duplicate stems such as `one/Report.pdf` and `two/report.pdf`
before staging, extraction, or image output. Unique-stem documents retain the
existing parity behavior. A later versioned storage/index identity can remove
this temporary restriction.

The manifest hash is caller/source-derived, not signed, and does not prove
document authenticity. This phase does not provide multi-user upload
quarantine, byte/page/pixel upload limits, immutable model provenance,
versioned Chroma persistence, index activation, SQLite transactions, or a
complete downstream execution transaction.
