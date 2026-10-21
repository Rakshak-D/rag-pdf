# Repository inventory

Audit basis: tracked files from `git ls-files`, inspected on branch `production-evaluation-workbench` on 2026-10-21. The working tree was clean before this audit. No research source was edited.

## Scope and totals

These totals are recalculated from the tracked file set at the current Phase
1.1 starting commit (`bf2a491`). The earlier audit snapshot counted 388 files
before the Phase 1 harness was committed; the current repository contains 408
tracked files.

| Category | Count | Bytes | Evidence |
|---|---:|---:|---|
| Tracked files | 408 | 560,993,356 | `git ls-files`; Python byte-count script |
| PDFs | 59 | 447,743,276 | suffix and byte scan |
| PNG images | 100 | 93,218,729 | suffix and byte scan |
| JPG figures | 202 | 18,297,296 | suffix and byte scan |
| Notebooks | 7 | 1,275,119 | JSON parse succeeded for all seven |
| Python | 12 | 334,140 | AST/source files, including Phase 1 harness |
| JSON | 13 | 50,158 | Research snapshots plus Phase 1 manifest |
| Markdown/text/no-extension | 14 | 74,638 | Root files, audit/architecture docs, and fixture README |
| DOCX | 1 | 0 | `data/results/Metrics Results.docx`; tracked zero-byte artifact |

The text/binary split from the scan is not a semantic classification: PDFs and images without NUL bytes can appear as text. Every tracked path was byte-readable; notebook JSON and Python AST parsing also succeeded. No unread tracked file was identified. The empty DOCX is readable as a zero-byte file, but has no document package to inspect.

## Root files

- `README.md` (9,368 bytes): describes a multimodal PyMuPDF/OpenCLIP/BGE/Chroma/Ollama pipeline and notebook quick start. It claims `data/database` in the tree although that directory is not tracked.
- `LICENSE` (MIT, copyright 2026 Rakshak D): permissive license; preserve attribution in any extracted/reused code.
- `.gitignore`: generic Python/Jupyter/build/cache ignores, `.env`, virtual environments, and `db.sqlite3`; it does not ignore `data/database`, uploaded PDFs, vector stores, model caches, or generated experiment PDFs.
- `.python-version`: `3.10.11`.
- `requirements.txt`: 218 pinned lines by line count, including the core stack and many transitive/environment-heavy packages. It pins CUDA Torch wheels (`+cu121`) and also includes both `fitz` and `PyMuPDF`; see dependency notes below.

## Source and notebook inventory

| Path | Evidence-based contents |
|---|---|
| `notebooks/config.py` | 203 lines; global `cfg = Config()`, project-root detection, 6 path fields, ingestion/image/retrieval/generation/evaluation settings, validation, and directory creation. |
| `notebooks/01_load_document.ipynb` | 15 cells (14 code, 1 markdown), 6 outputs; repeats logging and PDF/image loading helpers. |
| `notebooks/02_chunking.ipynb` | 32 cells (29 code, 3 markdown), 9 outputs; repeats loading, then bbox chunking and image association. |
| `notebooks/03_embedding.ipynb` | 28 cells (26 code, 2 markdown), 12 outputs; image/text embedding classes and model initialization. |
| `notebooks/04_vector_store.ipynb` | 21 cells (20 code, 1 markdown), 7 outputs; Chroma persistence and metadata serialization. |
| `notebooks/05_retrieval.ipynb` | 51 code cells plus 1 markdown, no outputs; retrieval classes, BM25, fusion, reranking, context selection. |
| `notebooks/06_llm.ipynb` | 18 cells (16 code, 2 markdown), 6 outputs; Ollama lifecycle, availability, prompt, multimodal generation. |
| `notebooks/rag_pipeline.ipynb` | 93 cells (83 code, 10 markdown), no outputs; integrated copy of the pipeline, benchmark questions, metrics, PDF export, and six-config harness. |
| `notebooks/rag_pipeline.py` | 6,476 lines; executable integrated source. AST found the complete call surface listed in `pipeline-and-dataflow.md`. |

The notebooks are not independent modules: the AST shows repeated definitions of `loading_pdf`, `bbox_chunker`, `ImageEmbeddingModel`, `TextEmbeddingModel`, `VectorStore`, `RetrievalRag`, `LocalLLM`, and related helpers. Exact duplicate code cells occur across the staged notebooks. `rag_pipeline.ipynb` also contains a large copy of `rag_pipeline.py` logic. Treat the `.py` file and integrated notebook as two sources of truth until proven equivalent; do not silently consolidate them during this audit.

## Tracked data and generated assets

- `data/pdf/`: three research PDFs: `Voyager Grand Tour PDF.pdf`, `highlights_of_hubbles_exploration_of_the_universe.pdf`, and `mars-science-laboratory.pdf`.
- `data/images_pymupdf/`: 99 extracted PNGs in three document-named directories (10 Voyager, 84 Hubble, 5 Mars). These are generated ingestion fixtures, not source PDFs.
- `data/test_images/test_image.png`: one test/fixture image.
- `notebooks/figures/`: 202 JPG figures, named `figure-<page>-<ordinal>.jpg`; likely notebook/report display assets. Their provenance is not encoded in the path, so licensing/source provenance remains an unknown.
- `data/retrieval_results/`: 17 timestamped retrieval-report PDFs, approximately 11–20 MB each.
- `data/results/`: 38 files: model result PDFs, two `Metrics Results` exports, 12 JSON config snapshots, and the zero-byte DOCX. These are runtime experiment outputs and should not be in a production source tree by default.
- `data/model_comparison/`: 13 timestamped comparison PDFs, generated reports.

No vector database, local model weights, secret file, or uploaded PDF outside `data/pdf` was found in the tracked listing. This is an observation, not proof that none exists in untracked or external caches. The tracked generated PDFs embed experiment output and may embed retrieved context/images; treat them as potentially sensitive runtime data.

## Dependency and hygiene observations

The code directly imports PyMuPDF, NumPy, Torch, PIL, OpenCLIP, Sentence Transformers, ChromaDB, LangChain `Document`, tiktoken, rank-bm25, NLTK, Ollama, requests, psutil, pynvml, reportlab, rouge-score, BERTScore and tabulate. It also imports `subprocess`, `shutil`, `dotenv`, and several logging packages. `requirements.txt` contains additional packages not proven by the two Python files alone (FastAPI is not present despite the target architecture; `fitz`/`frontend` and many medical/document/cloud packages need dependency provenance before removal). The audit does not remove any dependency.

## Commands and inspection result

Executed: `git branch --show-current`, `git status --short`, `git ls-files`; Python scripts for tracked byte sizes, extension counts, notebook JSON/AST summaries, duplicate cells/definitions; `rg` for call sites and side effects; line-level reads of requested text and Python sources. All tracked paths were read successfully. No test suite, model inference, ingestion run, or notebook execution was performed because this deliverable is an audit and the environment lacks a demonstrated reproducible runtime.
