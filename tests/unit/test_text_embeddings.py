from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from backend.config import ResolvedRunConfig
from backend.embeddings import (
    TextEmbeddingAdapter,
    TextEmbeddingInferenceError,
    TextEmbeddingInputError,
    TextEmbeddingLoadError,
    TextEmbeddingOutputError,
)
from notebooks.config import Config


def make_config(tmp_path: Path, model_name: str = "BAAI/bge-small-en") -> ResolvedRunConfig:
    return ResolvedRunConfig.from_legacy(
        Config(
            pdf_dir=str(tmp_path / "pdfs"),
            images_dir=str(tmp_path / "images"),
            database_dir=str(tmp_path / "database"),
            results_dir=str(tmp_path / "results"),
            model_comparison_results_dir=str(tmp_path / "model-comparison"),
            retrieval_results_dir=str(tmp_path / "retrieval-results"),
            text_embed_model=model_name,
            text_embed_batch_size=3,
            reset_collections_on_start=False,
        )
    )


@dataclass
class FakeDocument:
    page_content: str


class FakeModel:
    def __init__(self, *, dimension: int = 3, output: object | None = None):
        self.dimension = dimension
        self.output = output
        self.calls: list[dict] = []

    def encode(self, **kwargs):
        self.calls.append(kwargs)
        if self.output is not None:
            return self.output
        sentences = kwargs["sentences"]
        if isinstance(sentences, list):
            return np.arange(len(sentences) * self.dimension, dtype=np.float32).reshape(
                len(sentences), self.dimension
            )
        return np.arange(self.dimension, dtype=np.float32)


def loaded_adapter(tmp_path: Path, model_name: str = "BAAI/bge-small-en"):
    model = FakeModel()
    calls: list[tuple[str, str]] = []

    def loader(name: str, device: str):
        calls.append((name, device))
        return model

    return TextEmbeddingAdapter(make_config(tmp_path, model_name), model_loader=loader), model, calls


def test_import_and_construction_do_not_load_models_or_create_directories(tmp_path):
    adapter, _, calls = loaded_adapter(tmp_path)
    assert calls == []
    assert not (tmp_path / "database").exists()
    assert adapter.device is None


def test_load_is_lazy_and_idempotent(tmp_path):
    adapter, model, calls = loaded_adapter(tmp_path)
    assert adapter.load() is model
    assert adapter.load() is model
    assert len(calls) == 1
    assert calls[0][0] == "BAAI/bge-small-en"
    assert calls[0][1] in {"cpu", "cuda"}


def test_failed_load_is_typed_and_does_not_switch_model(tmp_path):
    def loader(name: str, device: str):
        raise OSError("network/model failure")

    adapter = TextEmbeddingAdapter(make_config(tmp_path), model_loader=loader)
    with pytest.raises(TextEmbeddingLoadError, match="BAAI/bge-small-en") as error:
        adapter.load()
    assert isinstance(error.value.__cause__, OSError)


def test_document_encoding_trims_preserves_order_and_passes_research_options(tmp_path):
    adapter, model, _ = loaded_adapter(tmp_path)
    result = adapter.embed_documents([FakeDocument(" first "), FakeDocument("second")])
    assert result.embeddings.shape == (2, 3)
    assert result.input_count == 2
    assert result.embeddings_created == 2
    assert result.dimension == 3
    assert result.average_time >= 0
    call = model.calls[0]
    assert call["sentences"] == ["first", "second"]
    assert call["batch_size"] == 3
    assert call["convert_to_numpy"] is True
    assert call["normalize_embeddings"] is True
    assert call["show_progress_bar"] is False


def test_bge_query_prefix_is_applied_only_for_queries(tmp_path):
    adapter, model, _ = loaded_adapter(tmp_path, "BAAI/bge-small-en")
    adapter.embed_documents([FakeDocument("document")])
    adapter.embed_query(" query ")
    assert model.calls[0]["sentences"] == ["document"]
    assert model.calls[1]["sentences"].startswith("Represent this sentence for searching relevant passages: ")

    non_bge, non_bge_model, _ = loaded_adapter(tmp_path / "non-bge", "sentence-transformers/all-MiniLM-L6-v2")
    non_bge.embed_query("query")
    assert non_bge_model.calls[0]["sentences"] == "query"


@pytest.mark.parametrize(
    "documents, error_match",
    [([], "must not be empty"), ([object()], "missing page_content"), ([FakeDocument(None)], "must be a string"), ([FakeDocument(" ")], "empty")],
)
def test_invalid_documents_are_rejected_before_model_call(tmp_path, documents, error_match):
    adapter, model, _ = loaded_adapter(tmp_path)
    with pytest.raises(TextEmbeddingInputError, match=error_match):
        adapter.embed_documents(documents)
    assert model.calls == []


@pytest.mark.parametrize("value", ["", "  ", None, 123])
def test_invalid_query_text_is_rejected_before_model_call(tmp_path, value):
    adapter, model, _ = loaded_adapter(tmp_path)
    with pytest.raises(TextEmbeddingInputError):
        adapter.embed_query(value)
    assert model.calls == []


@pytest.mark.parametrize(
    "output, error_match",
    [
        (np.ones(3), "2-dimensional"),
        (np.ones((1, 0)), "nonzero"),
        (np.ones((1, 3)), "row count"),
        (np.array([[np.nan, 0.0, 1.0], [1.0, 2.0, 3.0]]), "finite"),
    ],
)
def test_malformed_document_outputs_are_rejected(tmp_path, output, error_match):
    model = FakeModel(output=output)
    adapter = TextEmbeddingAdapter(make_config(tmp_path), model_loader=lambda name, device: model)
    with pytest.raises(TextEmbeddingOutputError, match=error_match):
        adapter.embed_documents([FakeDocument("one"), FakeDocument("two")])


def test_malformed_query_output_and_inference_failure_are_typed(tmp_path):
    malformed = FakeModel(output=np.ones((1, 3)))
    adapter = TextEmbeddingAdapter(make_config(tmp_path), model_loader=lambda name, device: malformed)
    with pytest.raises(TextEmbeddingOutputError, match="1-dimensional"):
        adapter.embed_query("query")

    def failing_loader(name: str, device: str):
        class FailingModel:
            def encode(self, **kwargs):
                raise RuntimeError("internal model detail")

        return FailingModel()

    failing = TextEmbeddingAdapter(make_config(tmp_path / "failing"), model_loader=failing_loader)
    with pytest.raises(TextEmbeddingInferenceError, match="inference failed") as error:
        failing.embed_query("query")
    assert isinstance(error.value.__cause__, RuntimeError)


def test_zero_elapsed_time_has_zero_average_without_division_error(tmp_path, monkeypatch):
    adapter, _, _ = loaded_adapter(tmp_path)
    monkeypatch.setattr("backend.embeddings.text.perf_counter", lambda: 10.0)
    result = adapter.embed_documents([FakeDocument("one")])
    assert result.elapsed_time == 0.0
    assert result.average_time == 0.0
