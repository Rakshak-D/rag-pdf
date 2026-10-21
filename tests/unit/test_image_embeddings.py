from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import torch
from PIL import Image

from backend.config import ResolvedRunConfig
from backend.embeddings import (
    ImageEmbeddingAdapter,
    ImageEmbeddingBatchResult,
    ImageEmbeddingInferenceError,
    ImageEmbeddingInputError,
    ImageEmbeddingLoadError,
    ImageEmbeddingOutputError,
    ImageModelComponents,
)
from notebooks.config import Config


def make_config(tmp_path: Path, *, model_name: str = "ViT-B-32", pretrained: str = "fake"):
    return ResolvedRunConfig.from_legacy(
        Config(
            pdf_dir=str(tmp_path / "pdfs"),
            images_dir=str(tmp_path / "images"),
            database_dir=str(tmp_path / "database"),
            results_dir=str(tmp_path / "results"),
            model_comparison_results_dir=str(tmp_path / "model-comparison"),
            retrieval_results_dir=str(tmp_path / "retrieval-results"),
            image_embed_model=model_name,
            image_embed_pretrained=pretrained,
            image_embed_batch_size=2,
            image_caption_image_weight=0.75,
            reset_collections_on_start=False,
        )
    )


class TokenBatch:
    def __init__(self, values):
        self.values = values
        self.device = None

    def to(self, device):
        self.device = device
        return self


class FakeClipModel:
    def __init__(self, image_output=None, text_output=None, fail_text=False):
        self.image_output = image_output
        self.text_output = text_output
        self.fail_text = fail_text
        self.image_calls = []
        self.text_calls = []

    def encode_image(self, batch):
        self.image_calls.append(batch)
        if callable(self.image_output):
            return self.image_output(batch)
        if self.image_output is not None:
            return self.image_output
        return torch.tensor([[1.0, 0.0, 0.0]] * batch.shape[0], dtype=torch.float32)

    def encode_text(self, tokens):
        self.text_calls.append(tokens)
        if self.fail_text:
            raise RuntimeError("caption failure detail")
        if callable(self.text_output):
            return self.text_output(tokens)
        if self.text_output is not None:
            return self.text_output
        return torch.tensor([[0.0, 1.0, 0.0]], dtype=torch.float32)


def make_adapter(tmp_path: Path, *, model=None, model_name="ViT-B-32", pretrained="fake", preprocess=None):
    model = model or FakeClipModel()
    calls = []
    preprocess = preprocess or (lambda image: torch.ones(3, 2, 2) * (image.size[0] / 10.0))

    def tokenizer(texts):
        return TokenBatch(texts)

    def loader(name, loaded_pretrained, device):
        calls.append((name, loaded_pretrained, device))
        return ImageModelComponents(model, preprocess, tokenizer, 3)

    adapter = ImageEmbeddingAdapter(
        make_config(tmp_path, model_name=model_name, pretrained=pretrained),
        model_loader=loader,
    )
    return adapter, model, calls


def write_image(path: Path, mode="RGBA", color=(255, 0, 0, 128)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new(mode, (10, 8), color).save(path)


def test_import_and_construction_are_lazy_and_empty_input_does_not_load(tmp_path):
    adapter, _, calls = make_adapter(tmp_path)
    assert calls == []
    result = adapter.embed_images([])
    assert isinstance(result, ImageEmbeddingBatchResult)
    assert result.embeddings.shape == (0, 0)
    assert result.dimension == 0
    assert calls == []
    assert not (tmp_path / "images").exists()


def test_load_uses_configured_identity_once(tmp_path):
    adapter, _, calls = make_adapter(tmp_path, model_name="CustomClip", pretrained="weights-v1")
    adapter.load()
    adapter.load()
    assert calls == [("CustomClip", "weights-v1", adapter.device)]


def test_load_failure_is_typed_without_fallback(tmp_path):
    calls = []

    def failing_loader(name, pretrained, device):
        calls.append((name, pretrained, device))
        raise OSError("external model detail")

    adapter = ImageEmbeddingAdapter(
        make_config(tmp_path, model_name="Requested", pretrained="requested-v1"),
        model_loader=failing_loader,
    )
    with pytest.raises(ImageEmbeddingLoadError, match="Requested.*requested-v1") as error:
        adapter.load()
    assert isinstance(error.value.__cause__, OSError)
    assert calls[0][:2] == ("Requested", "requested-v1")


def test_rgb_processing_order_batch_size_and_image_normalization(tmp_path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    third = tmp_path / "third.png"
    write_image(first)
    write_image(second, mode="RGB", color=(0, 255, 0))
    write_image(third, mode="RGB", color=(0, 0, 255))
    seen_modes = []

    def preprocess(image):
        seen_modes.append(image.mode)
        return torch.tensor([1.0, 0.0, 0.0])

    adapter, model, _ = make_adapter(tmp_path, preprocess=preprocess)
    result = adapter.embed_images([
        {"image_id": "one", "path": str(first)},
        {"image_id": "two", "path": str(second)},
        {"image_id": "three", "path": str(third)},
    ])
    assert seen_modes == ["RGB", "RGB", "RGB"]
    assert [batch.shape[0] for batch in model.image_calls] == [2, 1]
    assert result.image_objects[0]["image_id"] == "one"
    assert result.image_objects[1]["image_id"] == "two"
    assert result.image_objects[2]["image_id"] == "three"
    assert np.allclose(result.embeddings, [[1, 0, 0], [1, 0, 0], [1, 0, 0]])
    assert np.allclose(np.linalg.norm(result.embeddings, axis=1), [1, 1, 1])


def test_invalid_image_files_are_reported_and_valid_images_continue(tmp_path):
    valid = tmp_path / "valid.png"
    write_image(valid)
    adapter, _, _ = make_adapter(tmp_path)
    result = adapter.embed_images([
        {"image_id": "bad", "path": str(tmp_path / "missing.png")},
        {"image_id": "valid", "path": str(valid)},
        {"image_id": "unsupported", "path": str(tmp_path / "bad.txt")},
    ])
    assert result.input_count == 3
    assert result.embeddings_created == 1
    assert result.failed_loads == 2
    assert [item["image_id"] for item in result.image_objects] == ["valid"]
    assert [issue.kind for issue in result.issues] == ["image_load", "image_load"]


def test_caption_fusion_preserves_weighted_sum_without_renormalizing(tmp_path):
    image = tmp_path / "image.png"
    write_image(image)
    model = FakeClipModel()
    adapter, _, _ = make_adapter(tmp_path, model=model)
    result = adapter.embed_images([{"image_id": "image", "path": str(image), "caption_text": "caption"}])
    # Image vector is [1,0,0], caption vector is [0,1,0], weight is 0.75.
    assert np.allclose(result.embeddings[0], [0.75, 0.25, 0.0])
    assert not np.isclose(np.linalg.norm(result.embeddings[0]), 1.0)
    assert result.caption_fallbacks == 0


def test_caption_failure_falls_back_to_normalized_image_vector(tmp_path):
    image = tmp_path / "image.png"
    write_image(image)
    adapter, _, _ = make_adapter(tmp_path, model=FakeClipModel(fail_text=True))
    result = adapter.embed_images([{"image_id": "image", "path": str(image), "caption_text": "caption"}])
    assert np.allclose(result.embeddings[0], [1.0, 0.0, 0.0])
    assert result.caption_fallbacks == 1
    assert result.issues[0].kind == "caption"
    assert isinstance(result.issues[0].cause, RuntimeError)


@pytest.mark.parametrize("caption", [None, "", "  ", 42])
def test_invalid_caption_is_treated_as_no_caption(tmp_path, caption):
    image = tmp_path / "image.png"
    write_image(image)
    adapter, _, _ = make_adapter(tmp_path)
    result = adapter.embed_images([{"image_id": "image", "path": str(image), "caption_text": caption}])
    assert result.caption_fallbacks == 0
    assert result.issues == ()


@pytest.mark.parametrize("output, match", [(torch.zeros((1, 3)), "zero-norm"), (torch.ones((1, 0)), "dimension"), (torch.tensor([[float("nan"), 0.0, 1.0]]), "finite")])
def test_invalid_image_model_outputs_are_rejected(tmp_path, output, match):
    image = tmp_path / "image.png"
    write_image(image)
    adapter, _, _ = make_adapter(tmp_path, model=FakeClipModel(image_output=output))
    with pytest.raises(ImageEmbeddingOutputError, match=match):
        adapter.embed_images([{"image_id": "image", "path": str(image)}])


def test_invalid_top_level_inputs_and_query_are_rejected_before_model_calls(tmp_path):
    adapter, model, _ = make_adapter(tmp_path)
    for value in [None, "image", [object()]]:
        with pytest.raises(ImageEmbeddingInputError):
            adapter.embed_images(value)
    for value in [None, "", "  ", 42]:
        with pytest.raises(ImageEmbeddingInputError):
            adapter.embed_query(value)
    assert model.image_calls == []
    assert model.text_calls == []


def test_query_is_tokenized_and_normalized(tmp_path):
    adapter, model, _ = make_adapter(tmp_path)
    vector = adapter.embed_query(" query ")
    assert vector.shape == (3,)
    assert np.isclose(np.linalg.norm(vector), 1.0)
    assert model.text_calls[0].values == ["query"]


def test_invalid_query_outputs_and_inference_errors_are_typed(tmp_path):
    image = tmp_path / "unused.png"
    outputs = [torch.ones(3), torch.zeros((1, 3)), torch.tensor([[float("nan"), 0.0, 1.0]])]
    for output, match in zip(outputs, ["2-dimensional", "zero-norm", "finite"]):
        model = FakeClipModel(text_output=output)
        adapter, _, _ = make_adapter(tmp_path / match, model=model)
        with pytest.raises(ImageEmbeddingOutputError, match=match):
            adapter.embed_query("query")

    adapter, _, _ = make_adapter(tmp_path / "failure", model=FakeClipModel(fail_text=True))
    with pytest.raises(ImageEmbeddingInferenceError, match="inference failed") as error:
        adapter.embed_query("query")
    assert isinstance(error.value.__cause__, RuntimeError)


def test_caller_sequence_is_not_mutated(tmp_path):
    valid = tmp_path / "valid.png"
    write_image(valid)
    original = [
        {"image_id": "bad", "path": str(tmp_path / "missing.png")},
        {"image_id": "valid", "path": str(valid)},
    ]
    snapshot = [dict(item) for item in original]
    adapter, _, _ = make_adapter(tmp_path)
    result = adapter.embed_images(original)
    assert original == snapshot
    assert len(result.image_objects) == 1
