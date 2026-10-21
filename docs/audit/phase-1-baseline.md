# Phase 1 baseline harness

This phase adds a lightweight, source-preserving test and provenance harness.
It does not implement FastAPI, React, a job database, production settings, or
research refactors.

## Commands

```text
python -m pytest tests/unit tests/integration
python scripts/capture_baseline.py
python scripts/verify_baseline.py
git diff --check
```

The capture command writes the deliberate manifest
`tests/fixtures/research-baseline.json`. It records the starting branch/commit,
SHA-256 hashes for tracked `notebooks/*.py` and `notebooks/*.ipynb`, Config
field/default metadata, notebook structural metadata, and non-secret runtime
provenance. It contains no data contents, model weights, vectors, environment
variables or absolute paths.

## Coverage and limitations

Real assertions cover current `Config.validate()` behavior, temporary directory
creation, stable IDs/hashes, bbox operations, metadata serialization, caption
selection, image-caption association, chunk metadata/boundaries, and exact
contract behavior for retrieval-similarity and FCD aggregation. The chunk test
loads the actual function definitions from `notebooks/rag_pipeline.py` with
small test-only collaborators; it does not reimplement the algorithm.

The PDF integration test creates a one-page PDF only in pytest's temporary
directory and exercises `loading_pdf()` if `numpy` and `pymupdf` are installed.
It is skipped when those dependencies are unavailable. No test imports the full
pipeline module, initializes embedding/reranker models, starts Ollama, calls the
network, creates Chroma collections, or writes to repository `data/` paths.

Model-dependent retrieval, reranking, embedding, Ollama generation/thinking
mode, GPU telemetry, BERTScore runtime behavior, Chroma persistence, full
benchmark results, and six-configuration ablation remain unverified integration
boundaries. The tests make no claims about retrieval quality or answer quality.

## Baseline review rule

`verify_baseline.py` detects hash drift from the approved starting research
baseline. Intentional source changes require review, updated golden contracts or
fixtures where appropriate, and an explicitly regenerated manifest; the
manifest is not a blanket prohibition on future development.
