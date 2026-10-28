# Manifest inspection and RunPlan preparation

The local runtime passes its configured `RAG_WORKBENCH_DATA_DIR` to
`create_app()` as a trusted document source root. The root is never supplied
by an HTTP request. Apps created directly with `create_app()` can opt in with
the explicit `document_source_root` argument; otherwise these operations
return `manifest_source_unconfigured`. Runtime path validation keeps the PDF
source separate from index and persistent workbench storage.

## Inspect the current source manifest

`GET /api/v1/documents/manifest` requires the existing bearer token. On each
request, it delegates to `build_document_manifest()` and returns the manifest
schema version, canonical content hash, entry count, `empty` flag, and entries
with normalized relative path, byte size, file SHA-256, and generated document
ID. It never returns the absolute root or PDF contents. Symlinks, unsafe
inputs, and files that change while being hashed produce bounded errors rather
than a partial successful manifest. The response follows the app's configured
response byte limit.

Example response:

```json
{
  "schema_version": "1",
  "content_hash": "<sha256>",
  "entry_count": 1,
  "empty": false,
  "entries": [
    {
      "relative_path": "reports/example.pdf",
      "size_bytes": 1234,
      "sha256": "<sha256>",
      "document_id": "doc_<sha256>"
    }
  ]
}
```

An empty corpus is valid for inspection and returns `empty: true` with no
entries. It cannot be used to prepare an experiment plan.

## Prepare an integrity-checked RunPlan

`POST /api/v1/run-plans` accepts a strict JSON object with exactly
`resolved_config` and `expected_manifest_hash`. The configuration must include
the complete `ResolvedRunConfig` field set and pass its cross-field and
workbench safety validation. Destructive reset/delete options are rejected
without rewriting the submitted object.

The handler re-hashes the configured source root at preparation time. An empty
corpus returns `manifest_empty`; a hash that differs from the hash shown during
inspection returns `manifest_changed` and no plan. On a match, the existing
`build_run_plan()` implementation builds and verifies the plan. No API-layer
fingerprint implementation is used. The response's `run_plan` object is the
serialized domain RunPlan accepted by the existing evaluation request
contract, accompanied by the matching manifest schema, hash, and entry count.

Example request:

```json
{
  "resolved_config": { "...": "complete validated configuration" },
  "expected_manifest_hash": "<sha256 from manifest inspection>"
}
```

These endpoints only inspect source bytes and prepare metadata. They do not
extract documents, create snapshots, load models, access Chroma, write reports,
or build/reset indexes. Stable errors include `configuration_invalid`,
`destructive_configuration_forbidden`, `manifest_source_unconfigured`,
`manifest_source_unavailable`, `manifest_source_changed`, `manifest_invalid`,
`manifest_empty`, `manifest_changed`, and `manifest_hash_invalid`. Responses are authenticated,
no-store, path-safe, and bounded; request bodies use the normal API size limit.
