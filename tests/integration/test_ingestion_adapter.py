from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from backend.config import PathContainmentError, ResolvedRunConfig
from backend.engine.ingestion import PdfExtractionResult, extract_pdfs
from backend.experiments import build_run_plan
from notebooks.config import Config
from tests.support.source_loader import load_functions


def make_config(tmp_path: Path, *, images_dir: Path | None = None) -> ResolvedRunConfig:
    image_path = images_dir or (tmp_path / "owned" / "images")
    return ResolvedRunConfig.from_legacy(
        Config(
            pdf_dir=str(tmp_path / "pdfs"),
            images_dir=str(image_path),
            database_dir=str(tmp_path / "database"),
            results_dir=str(tmp_path / "results"),
            model_comparison_results_dir=str(tmp_path / "model-comparison"),
            retrieval_results_dir=str(tmp_path / "retrieval-results"),
            reset_collections_on_start=False,
        )
    )


def write_pdf(path: Path, text: str, *, with_image: bool = False) -> None:
    import pymupdf

    path.parent.mkdir(parents=True, exist_ok=True)
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    if with_image:
        image = Image.new("RGB", (100, 80), "#336699")
        draw = ImageDraw.Draw(image)
        for x in range(0, 100, 10):
            draw.line((x, 0, 99 - x, 79), fill="#f0c040", width=3)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        page.insert_image(pymupdf.Rect(72, 140, 172, 220), stream=buffer.getvalue())
    document.save(path)
    document.close()


def normalize_paths(value, roots: list[Path]):
    if isinstance(value, dict):
        return {key: normalize_paths(item, roots) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_paths(item, roots) for item in value]
    if isinstance(value, str):
        for root in roots:
            root_text = str(root)
            if value.startswith(root_text):
                return "<image-root>" + value[len(root_text):]
    return value


def research_loading(pdf_dir: Path, images_dir: Path, config: ResolvedRunConfig):
    import numpy as np
    import pymupdf

    selected = load_functions(
        "is_low_variance", "is_mostly_white", "is_extreme_aspect_ratio", "loading_pdf",
        globals_override={
            "np": np,
            "pymupdf": pymupdf,
            "Path": Path,
            "cfg": config,
            "LOGGER": type("Logger", (), {
                "info": lambda *args, **kwargs: None,
                "warning": lambda *args, **kwargs: None,
                "error": lambda *args, **kwargs: None,
                "debug": lambda *args, **kwargs: None,
            })(),
            "traceback": __import__("traceback"),
        },
    )
    return selected["loading_pdf"](str(pdf_dir), str(images_dir))


def test_empty_input_directory_has_explicit_empty_result(tmp_path):
    config = make_config(tmp_path)
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    result = extract_pdfs(config, pdf_dir, tmp_path / "owned")
    assert result == PdfExtractionResult(pages=[], successful_files=[], failed_files=[])
    assert not (tmp_path / "owned").exists()


def test_real_pdf_extraction_matches_research_pages_and_image_metadata(tmp_path):
    pdf_dir = tmp_path / "pdfs"
    write_pdf(pdf_dir / "b.pdf", "B document contains enough deterministic text.", with_image=True)
    write_pdf(pdf_dir / "nested" / "a.pdf", "A document contains enough deterministic text.")
    config = make_config(tmp_path)
    research_images = tmp_path / "research-images"
    expected = research_loading(pdf_dir, research_images, config)

    result = extract_pdfs(config, pdf_dir, tmp_path / "owned")
    actual = normalize_paths(result.pages, [research_images, tmp_path / "owned" / "images"])
    expected_normalized = normalize_paths(expected, [research_images])

    assert actual == expected_normalized
    assert result.successful_files == ["b.pdf", "a.pdf"]
    assert result.failed_files == []
    assert result.pages[0]["source"] == "b.pdf"
    assert result.pages[1]["source"] == "a.pdf"
    assert result.pages[0]["images"][0]["page"] == 1
    assert Path(result.pages[0]["images"][0]["path"]).is_relative_to(tmp_path / "owned")


def test_minimum_text_length_and_resource_cleanup_are_preserved(tmp_path):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    document_path = pdf_dir / "short-and-long.pdf"
    import pymupdf

    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "tiny")
    page.insert_text((72, 150), "This is a sufficiently long deterministic text block.")
    document.save(document_path)
    document.close()

    result = extract_pdfs(make_config(tmp_path), pdf_dir, tmp_path / "owned")
    blocks = result.pages[0]["text_blocks"]
    assert [block["text"] for block in blocks] == [
        "This is a sufficiently long deterministic text block."
    ]
    document_path.unlink()


def test_corrupt_pdf_is_reported_without_discarding_other_files(tmp_path):
    pdf_dir = tmp_path / "pdfs"
    write_pdf(pdf_dir / "good.pdf", "A valid PDF with enough deterministic text.")
    (pdf_dir / "bad.pdf").write_bytes(b"not a PDF")

    result = extract_pdfs(make_config(tmp_path), pdf_dir, tmp_path / "owned")
    assert result.successful_files == ["good.pdf"]
    assert len(result.failed_files) == 1
    assert result.failed_files[0]["source"] == "bad.pdf"
    assert result.failed_files[0]["error_type"]
    assert any(page["source"] == "good.pdf" for page in result.pages)


def test_image_output_must_remain_under_owned_storage_root(tmp_path):
    pdf_dir = tmp_path / "pdfs"
    write_pdf(pdf_dir / "document.pdf", "A valid PDF with enough deterministic text.")
    config = make_config(tmp_path, images_dir=tmp_path / "outside-images")
    with pytest.raises(PathContainmentError):
        extract_pdfs(config, pdf_dir, tmp_path / "owned")
    assert not (tmp_path / "outside-images").exists()


def test_verified_run_plan_is_the_execution_boundary(tmp_path):
    pdf_dir = tmp_path / "pdfs"
    write_pdf(pdf_dir / "planned.pdf", "A planned PDF with enough deterministic text.")
    config = make_config(tmp_path)
    plan = build_run_plan(config, "a" * 64)
    result = extract_pdfs(plan, pdf_dir, tmp_path / "owned")
    assert result.successful_files == ["planned.pdf"]
    assert result.failed_files == []


def test_import_does_not_expose_research_global_or_create_repository_data():
    import backend.engine.ingestion as ingestion

    assert not hasattr(ingestion, "cfg")
    assert not Path("data-local").exists()
