# Configuration matrix

Source: every dataclass field in `notebooks/config.py` (lines 42–154) and consumers found by AST/`rg` inspection in `notebooks/rag_pipeline.py`. Defaults below are effective when importing `config.py`; `validate()` creates directories and checks only the listed invariants.

## Paths, lifecycle and models

| Field (default) | Consumer / effect | UI control and audit note |
|---|---|---|
| `pdf_dir=data/pdf` | `loading_pdf`; recursive ingestion input | document-set selector; path must be server-owned |
| `images_dir=data/images_pymupdf` | extraction output and image loading | derived storage path, not arbitrary client input |
| `database_dir=data/database` | Chroma persistent client | index root; must be isolated/versioned |
| `results_dir=data/results`; `model_comparison_results_dir=data/model_comparison`; `retrieval_results_dir=data/retrieval_results` | JSON/PDF exporters and ablation routing | artifact destination; use run IDs and atomic manifests |
| `reset_collections_on_start=True` | `VectorStore(... reset_collection=...)` | dangerous reset toggle with explicit confirmation; destroys named collections on every dense run |
| `metrics_verbose=True` | metric logging/suppression | logging toggle; not a metric behavior control |
| `llm_retries=2`, `llm_retry_backoff_sec=1.0`, `llm_fail_fast=False` | Ollama retry loop | retry policy; expose failure mode, bound total time |
| `text_embed_model=BAAI/bge-large-en-v1.5` | `TextEmbeddingModel` | model selector; changes vector dimension/index identity |
| `image_embed_model=ViT-L-14`, `image_embed_pretrained=laion2b_s32b_b82k` | `ImageEmbeddingModel` | paired model selector; changes image index identity |
| `reranker_model=cross-encoder/ms-marco-MiniLM-L-12-v2` | `Reranker` when enabled | model selector; changes retrieval behavior |
| `llm_model=gemma4:e2b`, `llm_models=[gemma4:e2b]` | `LocalLLM` loop | Ollama model multi-select; actual availability checked at runtime |
| `tiktoken_encoding=cl100k_base` | chunking, context token counting, prompt stats | tokenizer selector; chunk token and context token are not the same budget |

## Ingestion and indexing

| Field | Consumer | UI control / dependency |
|---|---|---|
| `chunk_max_tokens=384`, `chunk_overlap_tokens=128`, `chunk_max_vertical_gap=60`, `chunk_min_text_len=20` | `bbox_chunker` and extraction filter | numeric ingestion controls; changing any changes chunk IDs/corpus |
| `text_embed_batch_size=64`, `image_embed_batch_size=8` | embedding batching | performance control; not semantic behavior, but affects memory/latency |
| `text_collection_name=vector_text`, `image_collection_name=vector_image` | Chroma names | derived/versioned identifiers; current names are not document/config isolated |
| `img_variance_threshold=10`, `img_white_pixel_threshold=240`, `img_white_ratio_threshold=0.95`, `img_min_width=50`, `img_min_height=50`, `img_min_aspect_ratio=0.1`, `img_max_aspect_ratio=10.0` | image filtering in `loading_pdf` | grouped image-quality controls; changes extracted corpus and should be included in index identity |

## Retrieval and fusion

| Field | Consumer | UI control / constraints |
|---|---|---|
| `text_k=10`, `image_k=5`, `rerank_k=5`, `rrf_k_constant=20` | retrieval candidate counts/RRF | numeric controls; validation only checks `text_k >= rerank_k` |
| `bm25_weight=0.5`, `semantic_weight=0.5` | weighted hybrid fusion | paired sliders summing to 1; still configured in BM25/semantic modes though only hybrid consumes fusion |
| four adaptive presets `(0.7,0.3)`, `(0.2,0.8)`, `(0.5,0.5)`, `(0.3,0.7)` | `_get_adaptive_weights` | advanced JSON/preset control; validate sums are not currently checked |
| `bm25_strong_max_score_threshold=10`, `bm25_strong_std_threshold=2`, `bm25_weak_max_score_threshold=3` | adaptive classification | advanced numeric controls |
| `retrieval_mode=hybrid` | authoritative semantic/BM25/hybrid branch | select; adaptive and weighted fusion require hybrid |
| `use_reranker=True`, `adaptive_weighting=True`, `use_weighted_fusion=False` | optional rerank/adaptive/weighted sum vs RRF | feature toggles; adaptive/weighted flags invalid outside hybrid |
| `reranker_ce_weight=.65`, `reranker_fused_weight=.30`, `reranker_boost_weight=.05` | final reranker score | three sliders sum 1; validation checks sum only |
| `use_cross_modal_boost=True`, `cross_modal_max_boost=.3`, `cross_modal_boost_per_overlap=.1` | overlap-based distance adjustment and reranker feature | toggle + numeric controls; not answer-quality evidence |
| `image_caption_image_weight=.8` | image/caption vector blend | slider 0–1; validation and constructor check bounds |

## Context and generation

| Field | Consumer | UI control / note |
|---|---|---|
| `max_text_chunks=3`, `max_images=1` | `ContextFormatter` | numeric controls |
| `max_context_tokens=1024` | approximate `4 chars/token` text context cap | label as approximate text budget; it is not the Ollama context window |
| `text_distance_threshold=1.0`, `image_distance_threshold=.75`, `use_filtering=True`, `use_percentile_filtering=True`, `percentile_cutoff=80` | context candidate filtering | grouped controls; percentile requires filtering |
| `llm_temperature=.25` | Ollama `options.temperature` | generation control |
| `llm_max_tokens=384` | Ollama `options.num_predict`; response output cap | output-token control, distinct from chunk/context limits |
| `llm_think_mode=False` | Ollama `think` argument | capability-aware toggle needed; current code does not inspect model support |

## Evaluation-only fields

`rouge_top_k_chunks=5` controls the pseudo-reference context cap for ROUGE; `top_k_accuracy_k=5` controls required-page coverage. Other metrics use ground truth or context independently. These fields should not alter an index identity.

## Validation gaps and effective-value recording

`Config.validate()` checks retrieval mode/flag dependencies, BM25 weight sum, image blend range, `text_k >= rerank_k`, percentile range/dependency, nonempty model list, reranker weight sum, BM25 signal weight sum, and creates all directories. It does not validate positive token/k/batch values, directory containment, collection-name safety, adaptive preset sums, image threshold ordering, temperature/max output bounds, model-name nonempty strings, or `llm_model` membership in `llm_models`. Defaults in Python function signatures capture values at import time, so mutating `cfg` later does not change all defaults. The snapshot serializes `cfg.__dict__`, but does not include code revision, package lock, hardware, tokenizer/model revisions, benchmark hash, index manifest, or actual effective constructor arguments. The UI/API should display a canonical resolved config and index fingerprint.
