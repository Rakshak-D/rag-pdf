# Pipeline and dataflow audit

Primary evidence is `notebooks/rag_pipeline.py`; line numbers below refer to that file as inspected on this branch. The staged notebooks duplicate most of these definitions. Structures are Python dictionaries, LangChain `Document`, NumPy arrays, Chroma result dictionaries, and report dictionaries—not typed API schemas.

## End-to-end call graph

`main(TEST_QUESTIONS)` → `Config.validate()` → `loading_pdf()` → `bbox_chunker()` and `build_image_objects()` → model initialization (`TextEmbeddingModel`, `ImageEmbeddingModel`) → `VectorStore` initialization → embedding and `add_documents()` (dense/hybrid) or direct `BM25IndexBuilder` (BM25-only) → `RetrievalRag` → per-question `retrieve()` → `ContextFormatter.format()` → `LocalLLM`/`llm_response()` → metric functions → JSON config snapshot and PDF exporters. `run_ablation()` mutates the global config and invokes `main()` six times.

## Stage contracts

### 1. PDF extraction and image filtering

`loading_pdf()` (`233–404`) recursively sorts `*.pdf`, opens each with PyMuPDF, and returns a list of page dictionaries: `{source, page, text_blocks, images}`. Each text block is `{block_id, text, bbox, page}`. Images are deduplicated by page xref, skip soft masks, require configured dimensions, reject fully transparent/mostly white/low-variance/extreme-aspect pixmaps, save PNGs under `images_dir/<pdf stem>/`, and emit `{image_id, path, page, bbox}`. Failure of one image is logged and skipped; failure of one PDF is logged, appended to `failed_pdf`, and processing continues. There are no configured PDF byte/page limits, MIME checks, or quarantine paths. Side effects are directory creation and image writes.

### 2. BBox chunking and image association

`bbox_chunker()` (`616–705`) uses tiktoken, page order, `chunk_max_tokens`, `chunk_overlap_tokens`, and `chunk_max_vertical_gap`. It merges adjacent blocks until token or vertical-gap limits, then steps back by blocks up to the overlap budget. `build_chunk()` emits a LangChain `Document` with joined text and metadata `{source, page_num, bbox, chunk_id, related_image_ids, semantic_tag}`. `related_image_ids` come from bbox overlap/tolerance helpers (`428–580`). The stable ID hashes source, page, and text; identical text on the same source/page can collide by design. It skips short text blocks (`chunk_min_text_len`) during extraction and skips empty chunks.

### 3. Text/image embeddings

`TextEmbeddingModel` (`930–1014`) loads SentenceTransformer on CUDA if available, otherwise CPU; BGE models receive a query prefix only for query encoding. Documents are batch encoded, normalized, and returned with timing/stats. `ImageEmbeddingModel` (`747–917`) loads OpenCLIP, preprocesses images, normalizes image/text vectors, and fuses them as `0.8*image + 0.2*caption` by default without a final renormalization. Bad image files are skipped and the input list is mutated in place to retain only aligned objects. Model downloads/cache behavior is delegated to libraries. Failure to load a model raises `RuntimeError`; empty text/image input has explicit errors or empty output.

### 4. Chroma storage and initialization

`VectorStore` (`1094–1281`) creates a `chromadb.PersistentClient` at `database_dir`, opens or creates named cosine collections, loads existing IDs, and optionally deletes/recreates a collection when `reset_collection=True`. `add_documents()` sanitizes metadata and generates IDs from chunk/image IDs or stable hashes; duplicate IDs are skipped in an in-memory set. `query()` validates one-dimensional embeddings and positive `k`, caps k to collection count, and returns Chroma’s nested result dictionary. `delete_collection()` permanently removes a collection. There is no collection namespace derived from document set, config, model version, or run ID; default collection names are globally reused.

### 5. BM25

`BM25Index` (`1798–1894`) lowercases, separates alphanumeric boundaries, tokenizes with NLTK/fallback regex, removes stopwords/domain stopwords, stems ordinary tokens, expands simple plural/tense variants, and runs `BM25Okapi`. It returns top-k documents/metadata/IDs, raw scores, query tokens, and stats (max/mean/min/std, non-zero docs, corpus coverage, overlap fallback). If all BM25 scores are zero, it falls back to token-overlap ranking. It is in-memory and rebuilt per run; its stats history is mutable state.

### 6–7. Semantic retrieval, fusion, adaptive weighting

`RetrievalRag.retrieve_text()`/`retrieve_images()` (`2441–2640`) embed the query, query Chroma, and optionally query BM25. Semantic distances are converted to scores; hybrid mode either uses normalized weighted sums (`bm25_weight`, `semantic_weight`) or RRF (`rrf_k_constant`). `_get_adaptive_weights()` classifies a query as keyword/semantic/balanced and uses BM25 max/std/corpus signals and configured presets. `retrieve()` combines text/image results, records stage latencies, and stores retrieval metrics. Adaptive weighting applies only to hybrid mode; `Config.validate()` rejects inconsistent flags.

### 8. Cross-encoder reranking

`Reranker` (`1303–1369`) scores `[query, item["text"]]` pairs with `CrossEncoder`, min-max normalizes CE/fused/boost signals, and computes `0.65*ce + 0.30*fused + 0.05*boost` in hybrid mode. It returns top `rerank_k`; model construction is eager and failure propagates.

### 9. Cross-modal boost

Within `RetrievalRag.retrieve()` (`2676–2730`), text/image metadata IDs are intersected. When enabled, each overlap reduces a text distance by `min(cross_modal_max_boost, overlaps*cross_modal_boost_per_overlap)` and contributes to reranker boost. This is a retrieval heuristic, not evidence of answer correctness.

### 10. Context filtering and token budget

`ContextFormatter` (`1534–1733`) deduplicates exact text and near duplicates using MD5 and 5-gram Jaccard ≥70%, filters text by distance or percentile, preserves hybrid rank order, selects images by distance and count, loads PIL images, and formats text with source/page labels. `max_context_tokens` is enforced as an approximate `max_context_tokens*4` character budget, not tokenizer tokens; the LLM later counts tiktoken tokens but does not truncate the prompt. `max_text_chunks`, `max_images`, thresholds, and filtering flags control this stage.

### 11. Ollama discovery and generation

`LocalLLM` (`2836–3133`) probes `http://localhost:11434/api/tags`; if unavailable it starts `ollama serve` via `subprocess.Popen`, polls up to ten seconds, and terminates only processes it started. It calls `ollama.list()`, checks exact model names, builds a strict context/image prompt, base64-encodes PIL images, and calls `ollama.chat()` with temperature, `num_predict=max_tokens`, `think=cfg.llm_think_mode`, and optional streaming. Retries are `llm_retries` with exponential backoff. Non-fail-fast exhaustion returns a synthetic “No answer (LLM error).” with an error field. Thinking capability is not discovered or validated; the boolean is passed to every model. The generated FCD is `100*(1-cosine(response first 2,000 chars, prompt first 2,000 chars))` using the text embedder, not a factual verifier.

### 12. Ground truth and benchmark

`TEST_QUESTIONS` (`3147` onward in the integrated source) contains 14 questions with IDs, types, source document, difficulty, question, ground-truth answer, expected image flag, and `required_context.pages/focus`. `llm_response()` maps ground truth by ID/question and emits per-query response, context, reference, image, generation timing and telemetry. Questions are embedded in source rather than a versioned fixture file.

### 13. Telemetry

`ResourceMonitor` samples the child process with psutil and GPU index 0 with pynvml at 50 ms. It reports average/peak CPU percent, average/peak RSS GB, RAM delta, GPU utilization/active utilization/duty cycle, VRAM and sample counts. Missing NVML becomes zero/empty telemetry. Sampling is process-local and can miss short GPU bursts; GPU index 0 is assumed.

### 14. Aggregation and PDF export

`main()` computes shared retrieval metrics once, writes `config_snapshot_<uuid8>.json`, then evaluates each configured LLM and writes per-model result PDFs. It writes one retrieval PDF and a comparison PDF. Exporters in `5438–5918` include retrieved text/images, config, metrics and per-query outputs. Writes are timestamp/model-based but have no atomic temp-file/manifest transaction. Existing results are not deleted.

### 15. Six-configuration ablation

`_ABLATION_CONFIGS`/`run_ablation()` (`6375–6466`) run Dense-only, BM25-only, Hybrid-static, Hybrid-adaptive, Hybrid-reranker, and Full-pipeline. Each mutates global `cfg`, redirects three output directories to labeled subdirectories, validates, catches and prints errors, then restores only the three directories. Other overridden fields remain mutated after the loop; `reset_collections_on_start=True` means dense configurations delete/rebuild collections each run. The harness does not persist a structured run manifest tying every output to a complete effective config.

## Global state, filesystem, network and working-directory assumptions

`cfg` is a mutable module-global; many function defaults capture its values at import time. `PROJECT_ROOT` depends on `__file__` or `Path.cwd()` and notebook path setup inserts `notebooks` into `sys.path`. `validate()` creates all configured directories. Extraction writes images; Chroma writes SQLite/vector files; main writes JSON/PDFs. Network/process effects include Hugging Face/OpenCLIP/SentenceTransformer downloads, Ollama HTTP calls, Ollama subprocess creation, and optional model telemetry. Paths and filenames are not user-upload-safe contracts yet. All stage failures should be turned into typed job states before API exposure.
