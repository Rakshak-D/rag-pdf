# Documents and index workflow

The local application’s **Documents** page connects the configured bootstrap service, imports PDFs into its trusted source directory, previews the current source, inspects build reports, and shows build jobs. Importing a PDF never starts a build.

## Configuration and compatibility

When bootstrap is connected, the page requests `GET /api/v1/bootstrap/configuration` through the authenticated bootstrap boundary. The service derives a complete `ResolvedRunConfig` from the existing backend defaults, binds source, storage, and output locations to its configured roots, and forces `reset_collections_on_start`, `allow_destructive_index_reset`, and `allow_collection_delete` to `false`. The configuration is validated again by the server during preview, inventory inspection, and job submission. The browser does not invent model names or scientific settings.

In local-gateway mode, the browser calls `/bootstrap-api/v1/bootstrap/configuration`; the loopback gateway injects its server-held bootstrap credential using an exact route allowlist. Direct development mode continues to use the separate bootstrap bearer connection. The ordinary runtime credential is never replaced by or sent as the bootstrap credential.

Preview is a point-in-time source and configuration check. Report inventory is report-level compatibility only; it does not open Chroma or establish model readiness. A fresh build requires an explicit confirmation and is accepted through the existing idempotent job API. Existing fingerprint-owned data is never reset, repaired, overwritten, or selected automatically. If a submission response is uncertain, check the durable job history before explicitly authorizing another request.

Only completed reports that match the current source manifest and configuration are offered for selection. The ordinary runtime selection is read back after the change. A selected report is not proof that Chroma can be opened or that models are ready for a query. Document Chat remains the next step, and reports are not activated implicitly.

The upload form displays the server-provided byte and page limits. Filenames, PDF structure, duplicate names, and source mutations remain enforced by the bootstrap service. If import publication cannot be confirmed, refresh the inventory before attempting another upload.

The legacy `/index-builds` routes remain available for detailed job metadata and direct-development ordinary-runtime selection. They retain the advanced configuration editor; the routine Documents workflow does not require pasted JSON.
