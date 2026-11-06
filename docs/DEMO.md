# RAG-PDF demonstration runbook

## Verified on this machine

This runbook reuses one real, task-owned local workspace created during acceptance. It is outside the repository so its Chroma database and local runtime state are not committed.

- Demo PDF: examples/demo/Orbit_Field_Notes.pdf
- Matching imported source and selected index: %TEMP%\rag-pdf-overnight-evidence\demo-workspace
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

The external workspace is under the Windows temporary directory and may be removed by system cleanup. Keep it until after the presentation. If it is gone, use the checked-in PDF and prepare a new isolated index in advance; that requires real BGE indexing and should not be attempted for the first time during the presentation.

## Start the prepared demo

Open PowerShell. These commands are specific to the verified local machine and the dedicated overnight worktree:

    $repo = 'C:\RAKSHAK\MY CODE\AI\rag-pdf-overnight-wt'
    $python = 'C:\RAKSHAK\MY CODE\AI\rag-pdf\rag-env\Scripts\python.exe'
    $demo = Join-Path $env:TEMP 'rag-pdf-overnight-evidence\demo-workspace'

    Set-Location $repo
    $env:RAG_WORKBENCH_DATA_DIR = Join-Path $demo 'pdf-source'
    $env:RAG_WORKBENCH_INDEX_STORAGE_ROOT = Join-Path $demo 'index-storage'
    $env:RAG_WORKBENCH_LOCAL_STATE_DIR = Join-Path $demo 'ordinary-state'
    $env:RAG_WORKBENCH_BOOTSTRAP_STATE_DIR = Join-Path $demo 'bootstrap-state-run3'
    $env:RAG_WORKBENCH_MODEL_LIFECYCLE_POLICY = 'transient_serialized'
    $env:TRANSFORMERS_OFFLINE = '1'
    $env:HF_HUB_OFFLINE = '1'
    $env:HF_HUB_DISABLE_TELEMETRY = '1'

    ollama list
    & $python scripts/start_local_app.py

Confirm the output says the app is running, then open http://127.0.0.1:5173. Do not open a second copy if the services are already running. The launcher creates fresh server-side ordinary and bootstrap credentials and sends them only to the local gateway. Do not copy them into the browser.

Before the demo, check that the workspace folders and Ollama models still exist. Ollama must already be running at 127.0.0.1:11434, with nemotron-3-nano:4b and gemma4:e2b listed. The normal startup does not download models. Do not run ollama pull during the presentation. Stop the app with Ctrl+C; the launcher stops only its own service processes.

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
