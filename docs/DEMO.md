# RAG-PDF demonstration runbook

## Previously verified demonstration (2026-11-07)

This runbook reuses one real, task-owned local workspace created during acceptance. It is outside the repository so its Chroma database and local runtime state are not committed.

**Sprint 5 revalidation (2026-11-08):** in a separate disposable workspace, the checked-in Orbit PDF was imported through the browser, one explicitly confirmed build completed with 3 text records and 0 image records, the matching index was selected and read back, and the persisted Chroma collections reopened after restart. General Chat produced 42 from the installed nemotron-3-nano:4b model. Document Chat was not re-run: at the final model gate, about 2.1 GiB RAM, 4.4 GiB commit headroom, and 5.0 GiB free VRAM remained for the configured 7.2 GB gemma4:e2b bundle; prior Gemma requests nearly exhausted host RAM. The prior successful Gemma answer below remains historical rather than a current-branch requalification. Image retrieval remains unverified.

- Demo PDF: examples/demo/Orbit_Field_Notes.pdf
- Source and index as verified on 2026-11-07: %TEMP%\rag-pdf-overnight-evidence\demo-workspace
- PDF: 3 pages, 0 embedded image objects
- Expected facts, kept outside model prompts: [expected-facts.json](../examples/demo/expected-facts.json)
- Build: 96cf88843eb2480f8b5b52d81a8054cc, completed; 3 text records, 0 image records
- Index fingerprint: 6616379741057464ae1d6306baeda08cfb668d8eca4ade5a3107d2fcf322844c
- Document manifest: 4c41ff9f11c17ede0ae600e35aa29cabbf6bf01f739d74bbab04d4402c5132ec
- Build report SHA-256: d01841d3548a288d157ddaa9f1898870f8a065afa580197dd5b64de669df17f4
- Text embedding: BAAI/bge-large-en-v1.5
- Configured but not exercised on this image-free PDF: ViT-L-14 (laion2b_s32b_b82k)
- Configured reranker: cross-encoder/ms-marco-MiniLM-L-12-v2
- Document answer model: gemma4:e2b
- General Chat model used: nemotron-3-nano:4b

The selected RunPlan used hybrid retrieval with reranking enabled. The store reopened successfully after the app was stopped; its three text records remained present, the image collection was empty, and the writer lease was released. A later real Document Chat request retrieved the page 1 passage and returned [T1] pointing to that passage.

Windows temporary storage may remove or change the prepared workspace. Do not assume its current source set or active selection still matches the historical identities above. Before presenting, refresh Documents and verify the selected report matches the complete current PDF collection. If a new index is required, prepare it in advance rather than during the presentation.

## Start the prepared demo

**Current state check (2026-11-08):** a read-only inspection found that the former temporary workspace now contains both Orbit_Field_Notes.pdf and Voyager Grand Tour PDF.pdf. Its completed two-PDF report (build 5c5706c7f56549329389a641a10a0945, manifest 565dea2e315d6eaafcadcfb8e43771c0e28875c780c29b0eda21a50884f7e454, index 45717168ec845242162a03358e10ac5ca8e1a7f2a7514bb0f02256a34ed5c450) matches that source set, but the saved ordinary-runtime selection still points to the one-PDF build 96cf88843eb2480f8b5d52d81a8054cc. The two-PDF report is not selected. Starting from the historical selection will therefore leave Document Chat stale. The separate one-PDF backup remains intact with the original matching report and index. Neither workspace was changed by Sprint 5. If using the two-PDF source, select its compatible report through Documents and confirm the readback; use a separate ordinary-state directory if the saved selection must remain untouched. Do not rebuild either existing index.

The commands below are the historical overnight-worktree launch configuration. Verify Source checkout and current selection before use. Open PowerShell:

    $repo = 'C:\RAKSHAK\MY CODE\AI\rag-pdf-overnight-wt'
    $python = 'C:\RAKSHAK\MY CODE\AI\rag-pdf\rag-env\Scripts\python.exe'
    $demo = Join-Path $env:TEMP 'rag-pdf-overnight-evidence\demo-workspace'

    Set-Location $repo
    $env:RAG_WORKBENCH_DATA_DIR = Join-Path $demo 'pdf-source'
    $env:RAG_WORKBENCH_INDEX_STORAGE_ROOT = Join-Path $demo 'index-storage'
    $env:RAG_WORKBENCH_LOCAL_STATE_DIR = Join-Path $demo 'ordinary-state-sprint5-demo'
    $env:RAG_WORKBENCH_BOOTSTRAP_STATE_DIR = Join-Path $demo 'bootstrap-state-sprint5-demo'
    $env:RAG_WORKBENCH_MODEL_LIFECYCLE_POLICY = 'transient_serialized'
    $env:TRANSFORMERS_OFFLINE = '1'
    $env:HF_HUB_OFFLINE = '1'
    $env:HF_HUB_DISABLE_TELEMETRY = '1'

    ollama list
    & $python scripts/start_local_app.py

Confirm the output says the app is running, then open http://127.0.0.1:5173. These separate state directories avoid inheriting the saved one-PDF selection. In Documents, select the completed report that matches the current two-PDF collection and verify the ordinary-runtime readback before opening Document Chat. Do not build again. Do not open a second app copy if the services are already running. The launcher creates fresh server-side ordinary and bootstrap credentials and sends them only to the local gateway. Do not copy them into the browser.

Before the demo, check that the workspace folders and Ollama models still exist. Ollama must already be running at 127.0.0.1:11434, with nemotron-3-nano:4b and gemma4:e2b listed. The normal startup does not download models. Do not run ollama pull during the presentation. Stop the app with Ctrl+C in the same interactive PowerShell console; the launcher stops only its owned service processes. If the bootstrap ownership marker remains after shutdown, do not delete it manually; verify that owned services and ports are closed and preserve the state for diagnosis.

## Short presentation script

1. **Chat:** Open the app. Point out that no API token is entered. Explain that General Chat uses the local Ollama service and does not need a PDF index.
2. **Settings:** Select nemotron-3-nano:4b, set temperature to 0 and maximum output tokens to 64, and save. These values are browser-local preferences sent with General Chat requests.
3. **General Chat:** Ask, "What is six times seven?" The verified browser run returned 42 from the real local model.
4. **Documents:** Show the imported Orbit_Field_Notes.pdf, the completed build report, and the explicitly selected index. The index is already built; do not submit another build. The build used BAAI/bge-large-en-v1.5 and persisted three text vectors in Chroma.
5. **Document Chat:** Select Document Chat and ask, "How much power did the ORBIT test solar panel produce, and when?" The verified answer was: "The ORBIT test solar panel produced 184 watts of electrical output on 12 May 2025 at 10:00 a.m. [T1]"
6. Click [T1] and show the returned passage from Orbit_Field_Notes.pdf, page 1. Explain that the citation opens evidence returned by retrieval; it is not a formal proof of every model statement.
7. Ask, "What was the tower's peak wind speed during the test?" The real gemma4:e2b response was "Insufficient evidence." The response marked model-reported abstention and had no citation links. Describe this as one observed case, not a general guarantee.
8. **Results:** Open Results. It reads persisted evaluation/ablation artifacts. This demo has no benchmark artifact, so an empty result catalog is expected. Do not claim benchmark metrics.

## Architecture in one minute

PyMuPDF extracts page text and source metadata. The existing layout-aware chunker creates page-associated text records. BGE embeds those records, and Chroma persists them under a fingerprinted index. A validated selected report supplies the RunPlan for hybrid retrieval and the configured reranker. The context formatter selects bounded evidence and assigns labels such as [T1]. Ollama generates the answer, and the UI links only citations that reference returned evidence. General Chat skips all document retrieval and has no citations.

## Verified limits

- General Chat: real browser request succeeded with nemotron-3-nano:4b; the question was six times seven and the answer was 42.
- Text document path: real PDF extraction, real BGE build, Chroma persistence, compatible report selection, persisted-store reopening, actual retrieval, real gemma4:e2b answer, source/page citation, and evidence-drawer navigation passed.
- Settings: model, temperature, and output-token values affected the real General Chat request; browser-local preferences survived reload in the same browser profile.
- Responsive navigation: desktop collapse/expand and mobile drawer open, Escape dismissal, Documents/Settings/Results navigation passed at 1366 px and 390 px widths without horizontal overflow.
- One unsupported question was tested with real gemma4:e2b and returned "Insufficient evidence." with no citation links. This does not establish general abstention reliability or broad answer accuracy.
- Not qualified: image extraction/CLIP embedding and image retrieval, benchmark evaluation, and Windows strict-listener browser acceptance.
- Across two real Gemma Document Chat requests, available physical memory briefly fell to about 42 MiB for the supported question and 371 MiB for the unsupported question. Ollama unloaded the model after each request and memory recovered. Avoid other heavy applications and recheck resources before presenting. These are observations from two runs, not a universal memory guarantee.

## If something is unavailable

- If the app URL does not open, confirm that no earlier app instance is still running. Use the single startup command above; do not kill unrelated Python, Node, browser, or Ollama processes.
- If a PDF/index is missing, stop and prepare the workspace before the presentation. Do not rebuild during the demo unless memory and time have been checked.
- If Ollama is unavailable, check the Ollama tray application and ollama list. Do not substitute an unverified model or download weights during the demo.
- If Document Chat reports no selected index, open Documents and confirm the compatible report and active selection. Do not claim a report is query-ready until the selected state is shown.
- If the unsupported question is discussed, say that this one question produced an abstention; do not present it as a general guarantee.
