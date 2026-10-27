from __future__ import annotations

from pathlib import Path

import numpy as np

from backend.config import ResolvedRunConfig
from backend.embeddings import TextEmbeddingAdapter
from backend.experiments import build_run_plan
from backend.vectorstore import ChromaVectorStore
from notebooks.config import Config


class DeterministicModel:
    def encode(self, *, sentences, **kwargs):
        values = sentences if isinstance(sentences, list) else [sentences]
        vectors = []
        for value in values:
            vectors.append([float(len(value)), float(value.count("a")), 1.0])
        result = np.asarray(vectors, dtype=np.float32)
        return result if isinstance(sentences, list) else result[0]


def test_fake_text_embeddings_persist_through_chroma_reopen(tmp_path: Path):
    config = ResolvedRunConfig.from_legacy(
        Config(
            pdf_dir=str(tmp_path / "pdfs"),
            images_dir=str(tmp_path / "images"),
            database_dir=str(tmp_path / "database"),
            results_dir=str(tmp_path / "results"),
            model_comparison_results_dir=str(tmp_path / "model-comparison"),
            retrieval_results_dir=str(tmp_path / "retrieval-results"),
            text_embed_model="fake-text-model",
            reset_collections_on_start=False,
        )
    )
    plan = build_run_plan(config, "a" * 64)
    adapter = TextEmbeddingAdapter(config, model_loader=lambda name, device: DeterministicModel())
    result = adapter.embed_documents(
        [type("Document", (), {"page_content": "alpha"})(), type("Document", (), {"page_content": "beta"})()]
    )

    storage = tmp_path / "owned"
    store = ChromaVectorStore.open(storage, plan)
    try:
        store.upsert_text(
            ["chunk-1", "chunk-2"],
            result.embeddings,
            ["alpha", "beta"],
            [{"source": "a.pdf"}, {"source": "b.pdf"}],
        )
    finally:
        store.close()
    reopened = ChromaVectorStore.open(storage, plan)
    try:
        assert reopened.count("text") == 2
        assert reopened.query_text(result.embeddings[0], 1)["ids"] == [["chunk-1"]]
    finally:
        reopened.close()
