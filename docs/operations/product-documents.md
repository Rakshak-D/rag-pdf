# Documents workspace

Sprint 1 starts from `2b08ed2e613ac794917ce07455f96e1d73cd6064` on
`feat/product-rebuild-01-documents`. The local tag
`checkpoint/product-rebuild-01-base` preserves that starting point.

## Use the collection

1. Open Library, or choose **Attach PDF** beside the Chat composer. Select a
   file and explicitly choose **Add PDF**.
   The server validates the file and refreshes the library. Uploading never
   starts indexing; all PDFs in the configured source form one shared collection.
   Chat attachments are not scoped to a single conversation.
2. The preparation area checks the trusted configuration and completed reports.
   Use **Use this index** when a compatible completed preparation exists.
   Otherwise review the confirmation and choose **Prepare documents**.
3. A queued/running job shows its actual state. Reopening Documents restores
   monitoring. A completed report still requires explicit selection and readback.
4. **Ask PDFs** opens Document Chat once a compatible index is selected.
   Selection validates report identity; actual store/model readiness is checked
   when a question is submitted.

If an upload or submission response is uncertain, use the offered readback
before retrying. No state-changing request is retried automatically. Failed or
interrupted jobs keep their details under Advanced. Existing incomplete storage
is preserved; review it in the advanced build workspace rather than deleting it.
Custom research configurations remain available on the legacy Index Builds route.

## Start and stop

Use the existing prepared Python environment and locked frontend dependencies.
From this checkout, set the trusted absolute source, storage, ordinary state,
and bootstrap state paths documented in `local-chat-first.md`, then run:

```powershell
python scripts/start_local_app.py
```

Open the loopback URL printed by the launcher. Confirm its `Source checkout:`
is this checkout; an already running Vite process can serve an older worktree.
Use `--port-offset 200` for separate free ports (UI 5373). Stop with Ctrl+C.
Do not share active state with another running app or perform concurrent model
operations. The launcher does not load models merely to render the interface.

## Design and verification boundary

`product-foundation.css` supplies ink, paper, ultramarine, success/warning,
spacing, typography and keyboard-focus tokens after the legacy styles.
`documents-workflow.css` owns the responsive library and preparation layout.
Chat, Library and Settings are the primary navigation. **Advanced Research**
in Settings links to Results, evaluations, ablations, jobs and detailed index
builds. Existing routes and bookmarks remain available.

This slice reuses existing clients and authenticated gateway contracts. It
changes no scientific configuration, backend API, retrieval implementation,
index, or model. No files were deleted. Real browser checks cover isolated
uploads, validation, interrupted-response recovery, selected-report readback,
PDF Chat handoff, desktop/mobile layout and reduced motion. Deterministic tests
cover build admission, job monitoring and failed/interrupted recovery. No new
real index build or model inference is claimed. Source-page viewing remains
outside this sprint.
