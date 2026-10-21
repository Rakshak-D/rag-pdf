from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
from PIL import Image

from backend.config import ResolvedRunConfig
from backend.embeddings import ImageEmbeddingAdapter, ImageModelComponents
from backend.experiments import build_run_plan
from backend.vectorstore import ChromaVectorStore
from notebooks.config import Config


class DeterministicImageModel:
    def encode_image(self, batch):
        return torch.tensor([[1.0, 0.0, 0.0]] * batch.shape[0])

    def encode_text(self, tokens):
        return torch.tensor([[0.0, 1.0, 0.0]] * len(tokens.values))


class Tokens:
    def __init__(self, values):
        self.values = values

    def to(self, device):
        return self


def test_fake_image_embeddings_persist_through_chroma_reopen(tmp_path: Path):
    image_path = tmp_path / "image.png"
    Image.new("RGB", (8, 8), "red").save(image_path)
    config = ResolvedRunConfig.from_legacy(
        Config(
            pdf_dir=str(tmp_path / "pdfs"),
            images_dir=str(tmp_path / "images"),
            database_dir=str(tmp_path / "database"),
            results_dir=str(tmp_path / "results"),
            model_comparison_results_dir=str(tmp_path / "model-comparison"),
            retrieval_results_dir=str(tmp_path / "retrieval-results"),
            image_embed_model="fake-image-model",
            image_embed_pretrained="fake-pretrained",
            reset_collections_on_start=False,
        )
    )
    plan = build_run_plan(config, "a" * 64)
    components = ImageModelComponents(
        DeterministicImageModel(),
        lambda image: torch.ones(3, 2, 2),
        lambda texts: Tokens(texts),
        3,
    )
    adapter = ImageEmbeddingAdapter(config, model_loader=lambda name, pretrained, device: components)
    result = adapter.embed_images([{"image_id": "image-1", "path": str(image_path)}])

    storage = tmp_path / "owned"
    store = ChromaVectorStore.open(storage, plan)
    store.upsert_image(["image-1"], result.embeddings, [{"source": "image.png"}])
    store.close()
    reopened = ChromaVectorStore.open(storage, plan)
    assert reopened.count("image") == 1
    assert reopened.query_image(result.embeddings[0], 1)["ids"] == [["image-1"]]
