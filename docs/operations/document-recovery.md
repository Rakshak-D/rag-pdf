# Document workflow recovery

Uploading changes the source manifest. An older selection then becomes stale; it must not answer for the updated collection. Preview the current PDFs, choose **Use this index** for a compatible completed report, then **Ask documents**. Preview automatically reads report inventory. Selection is confirmed by ordinary-runtime readback. A fresh build always requires confirmation.

Running jobs resume polling when Documents is reopened. Completion refreshes preview and compatible reports. Refresh jobs reads the current bootstrap database; using a different bootstrap state directory does not import another directory's job history. Completed reports are discovered independently in the shared storage root.

If files exist without a completed compatible report, inspect the report checks and job details. Existing files remain preserved. Recovery of an incomplete fingerprint requires an operator-configured separate source/storage workspace; the UI does not delete or repair partial data. Advanced Index Builds remains available for custom configurations. Report compatibility is not a real-store or model-readiness claim.

## Prepared laptop workspace

The source now contains Orbit_Field_Notes.pdf and Voyager Grand Tour PDF.pdf. Current manifest: `565dea2e315d6eaafcadcfb8e43771c0e28875c780c29b0eda21a50884f7e454`. Completed matching build: `5c5706c7f56549329389a641a10a0945`. The original one-PDF index and backup remain preserved. Do not delete the added PDF to restore the old selection without deliberately reviewing that source change.

The two-PDF report was selected and read back through the actual local gateway in a separate ordinary state directory. To use that selection without replacing the original saved selection:

```powershell
Set-Location 'C:\RAKSHAK\MY CODE\AI\rag-pdf-final-ux-wt'
$demo = Join-Path $env:TEMP 'rag-pdf-overnight-evidence\demo-workspace'
$env:RAG_WORKBENCH_DATA_DIR = Join-Path $demo 'pdf-source'
$env:RAG_WORKBENCH_INDEX_STORAGE_ROOT = Join-Path $demo 'index-storage'
$env:RAG_WORKBENCH_LOCAL_STATE_DIR = 'C:\RAKSHAK\MY CODE\AI\rag-pdf-hardening-evidence\ordinary-state'
$env:RAG_WORKBENCH_BOOTSTRAP_STATE_DIR = 'C:\RAKSHAK\MY CODE\AI\rag-pdf-hardening-evidence\bootstrap-state'
$env:RAG_WORKBENCH_MODEL_LIFECYCLE_POLICY = 'transient_serialized'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
& 'C:\RAKSHAK\MY CODE\AI\rag-pdf\rag-env\Scripts\python.exe' scripts/start_local_app.py --port-offset 100
```

Open http://127.0.0.1:5273. Do not start a second instance. Stop with Ctrl+C. Ollama must already be configured separately; startup does not load its weights. General Chat uses browser preferences; Document Chat preserves the selected RunPlan. Wait for each operation to finish before another model operation.

This correction verified browser upload, changed-source preview invalidation, compatible report selection/readback, composer recovery, Settings persistence, and empty Results. No new real inference or index build was run. Previous Gemma inference nearly exhausted physical RAM; recheck resources before attempting it. Full multimodal retrieval and benchmark evaluation remain unverified. The original text-only demonstration record is in docs/DEMO.md.

The conversation sprint automatically checks preparation when Documents opens. Use Check documents for a manual refresh. New Chat keeps earlier chats in the conversation picker; navigation and reload preserve tab-session transcripts and drafts. See local-chat-first.md for storage and evidence-expiry behavior.

## Avoid an older running frontend

A browser refresh cannot change the checkout that a running Vite process serves.
The launcher prints `Source checkout:`. Confirm it names `rag-pdf-final-ux-wt`.
An existing app at port 5173 was serving the older `rag-pdf-hardening-wt` checkout,
which does not preserve conversations. The commands above intentionally use
port offset 100: UI 5273, ordinary API 8100, bootstrap API 8110, gateway 8120.
They leave that existing app untouched. Do not run model operations concurrently
in the two apps. To use default ports instead, stop the earlier app with Ctrl+C
in its own terminal before launching the corrected checkout without the offset.
Occupied ports now fail before any service starts.
