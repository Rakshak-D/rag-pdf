from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pytest
import torch
from PIL import Image, ImageDraw

from backend.config import ResolvedRunConfig
from backend.embeddings import ImageEmbeddingAdapter, ImageModelComponents, TextEmbeddingAdapter
from backend.engine import build_index
from backend.engine.manifest import ManifestMismatchError, build_document_manifest
from backend.experiments import build_run_plan
from backend.vectorstore import ChromaVectorStore
from notebooks.config import Config


def make_config(tmp_path: Path, *, text_model: str = "fake-text", image_model: str = "fake-image"):
    return ResolvedRunConfig.from_legacy(
        Config(
            pdf_dir=str(tmp_path / "pdfs"),
            images_dir=str(tmp_path / "images"),
            database_dir=str(tmp_path / "database"),
            results_dir=str(tmp_path / "results"),
            model_comparison_results_dir=str(tmp_path / "model-comparison"),
            retrieval_results_dir=str(tmp_path / "retrieval-results"),
            text_embed_model=text_model,
            text_embed_batch_size=2,
            image_embed_model=image_model,
            image_embed_pretrained="fake-pretrained",
            image_embed_batch_size=2,
            reset_collections_on_start=False,
        )
    )


def write_pdf(path: Path, text: str, *, with_image: bool = False):
    import pymupdf

    path.parent.mkdir(parents=True, exist_ok=True)
    document = pymupdf.open()
    page = document.new_page()
    if text:
        page.insert_text((72, 72), text)
    if with_image:
        image = Image.new("RGB", (100, 80), "#336699")
        draw = ImageDraw.Draw(image)
        for x in range(0, 100, 5):
            draw.line((x, 0, 99 - x, 79), fill="#f0c040", width=3)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        page.insert_image(pymupdf.Rect(72, 140, 172, 220), stream=buffer.getvalue())
    document.save(path)
    document.close()


class FakeTextModel:
    def __init__(self, calls):
        self.calls = calls

    def encode(self, **kwargs):
        self.calls.append(kwargs)
        count = len(kwargs["sentences"])
        return np.tile(np.asarray([[1.0, 0.0, 0.0]], dtype=np.float32), (count, 1))


class FakeImageModel:
    def encode_image(self, batch):
        return torch.tensor([[0.0, 1.0, 0.0]] * batch.shape[0])

    def encode_text(self, tokens):
        return torch.tensor([[0.0, 0.0, 1.0]] * len(tokens.values))


class FakeTokens:
    def __init__(self, values):
        self.values = values

    def to(self, device):
        return self


def adapters(config: ResolvedRunConfig, calls: list):
    text_model = FakeTextModel(calls)
    text_adapter = TextEmbeddingAdapter(config, model_loader=lambda name, device: text_model)
    image_components = ImageModelComponents(
        FakeImageModel(),
        lambda image: torch.ones(3, 2, 2),
        lambda texts: FakeTokens(texts),
        3,
    )
    image_adapter = ImageEmbeddingAdapter(
        config,
        model_loader=lambda name, pretrained, device: image_components,
    )
    return text_adapter, image_adapter


def make_plan(config: ResolvedRunConfig, pdf_dir: Path):
    return build_run_plan(config, build_document_manifest(pdf_dir).content_hash)


def test_valid_build_persists_expected_identity_and_separate_attempt_artifacts(tmp_path):
    pdf_dir = tmp_path / "pdfs"
    write_pdf(pdf_dir / "document.pdf", "A deterministic text block for index building.", with_image=True)
    config = make_config(tmp_path)
    plan = make_plan(config, pdf_dir)
    calls: list = []
    text_adapter, image_adapter = adapters(config, calls)

    first = build_index(
        plan,
        pdf_dir,
        tmp_path / "owned",
        text_adapter=text_adapter,
        image_adapter=image_adapter,
    )
    second = build_index(
        plan,
        pdf_dir,
        tmp_path / "owned",
        text_adapter=TextEmbeddingAdapter(config, model_loader=lambda name, device: FakeTextModel(calls)),
        image_adapter=ImageEmbeddingAdapter(
            config,
            model_loader=lambda name, pretrained, device: ImageModelComponents(
                FakeImageModel(), lambda image: torch.ones(3, 2, 2), lambda texts: FakeTokens(texts), 3
            ),
        ),
    )
    assert first.status in {"completed", "completed_with_warnings"}
    assert second.status == first.status
    assert first.index_fingerprint == plan.index_fingerprint
    assert first.document_manifest_hash == plan.document_manifest_hash
    assert first.build_id != second.build_id
    assert first.artifact_root != second.artifact_root
    assert first.report_path.is_file()
    assert second.report_path.is_file()
    assert first.text_vectors_written == second.text_vectors_written == 1
    assert first.image_vectors_written == second.image_vectors_written
    assert json.loads(first.report_path.read_text(encoding="utf-8"))["build_id"] == first.build_id

    store = ChromaVectorStore.open(tmp_path / "owned", plan)
    text_ids = store.ids("text")
    image_ids = store.ids("image")
    assert len(text_ids) == first.text_vectors_written
    assert len(image_ids) == first.image_vectors_written
    assert all(identifier.strip() for identifier in text_ids + image_ids)


def test_manifest_mismatch_fails_before_build_artifacts_models_or_chroma(tmp_path):
    pdf_dir = tmp_path / "pdfs"
    write_pdf(pdf_dir / "document.pdf", "A deterministic text block for index building.")
    config = make_config(tmp_path)
    plan = make_plan(config, pdf_dir)
    (pdf_dir / "new.pdf").write_bytes(b"new")
    calls: list = []
    text_adapter, image_adapter = adapters(config, calls)
    with pytest.raises(ManifestMismatchError):
        build_index(
            plan,
            pdf_dir,
            tmp_path / "owned",
            text_adapter=text_adapter,
            image_adapter=image_adapter,
        )
    assert calls == []
    assert not (tmp_path / "owned").exists()


def test_extraction_failure_reports_failed_before_model_or_chroma(tmp_path):
    pdf_dir = tmp_path / "pdfs"
    write_pdf(pdf_dir / "good.pdf", "A valid deterministic text block for building.")
    (pdf_dir / "bad.pdf").write_bytes(b"not a PDF")
    config = make_config(tmp_path)
    plan = make_plan(config, pdf_dir)
    calls: list = []
    text_adapter, image_adapter = adapters(config, calls)
    result = build_index(
        plan,
        pdf_dir,
        tmp_path / "owned",
        text_adapter=text_adapter,
        image_adapter=image_adapter,
    )
    assert result.status == "failed"
    assert result.extraction_failures[0]["source"] == "bad.pdf"
    assert calls == []
    assert not (tmp_path / "owned" / "indexes").exists()
    assert result.report_path.is_file()


def test_empty_text_and_image_inputs_do_not_load_models(tmp_path):
    pdf_dir = tmp_path / "pdfs"
    write_pdf(pdf_dir / "empty.pdf", "")
    config = make_config(tmp_path)
    plan = make_plan(config, pdf_dir)
    calls: list = []
    text_adapter, image_adapter = adapters(config, calls)
    result = build_index(
        plan,
        pdf_dir,
        tmp_path / "owned",
        text_adapter=text_adapter,
        image_adapter=image_adapter,
    )
    assert result.status == "completed"
    assert result.text_chunks_prepared == 0
    assert result.image_objects_extracted == 0
    assert result.text_vectors_written == 0
    assert result.image_vectors_written == 0
    assert calls == []


def test_mismatched_injected_adapter_is_rejected_before_chroma(tmp_path):
    pdf_dir = tmp_path / "pdfs"
    write_pdf(pdf_dir / "document.pdf", "A deterministic text block for index building.")
    config = make_config(tmp_path)
    plan = make_plan(config, pdf_dir)
    mismatched = TextEmbeddingAdapter(make_config(tmp_path / "other", text_model="different"), model_loader=lambda n, d: FakeTextModel([]))
    with pytest.raises(Exception, match="text adapter configuration"):
        build_index(plan, pdf_dir, tmp_path / "owned", text_adapter=mismatched)
    assert not (tmp_path / "owned" / "indexes").exists()
