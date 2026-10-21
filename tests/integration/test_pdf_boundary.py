from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.source_loader import load_functions


def test_tiny_pdf_extraction_uses_temp_storage_only(tmp_path):
    np = pytest.importorskip("numpy")
    pymupdf = pytest.importorskip("pymupdf")

    pdf_dir = tmp_path / "pdf"
    images_dir = tmp_path / "images"
    pdf_dir.mkdir()
    document_path = pdf_dir / "fixture.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "A deterministic fixture sentence for extraction.")
    document.save(document_path)
    document.close()

    functions = load_functions(
        "is_low_variance", "is_mostly_white", "is_extreme_aspect_ratio", "loading_pdf",
        globals_override={
            "np": np,
            "pymupdf": pymupdf,
            "Path": Path,
            "cfg": type("Cfg", (), {
                "img_min_width": 50, "img_min_height": 50,
                "img_variance_threshold": 10, "img_white_pixel_threshold": 240,
                "img_white_ratio_threshold": 0.95, "img_min_aspect_ratio": 0.1,
                "img_max_aspect_ratio": 10.0, "chunk_min_text_len": 20,
            })(),
            "LOGGER": type("Logger", (), {
                "info": lambda *args, **kwargs: None,
                "warning": lambda *args, **kwargs: None,
                "error": lambda *args, **kwargs: None,
                "debug": lambda *args, **kwargs: None,
            })(),
            "traceback": __import__("traceback"),
        },
    )

    pages = functions["loading_pdf"](str(pdf_dir), str(images_dir))

    assert len(pages) == 1
    assert pages[0]["source"] == "fixture.pdf"
    assert pages[0]["page"] == 1
    assert "deterministic fixture sentence" in pages[0]["text_blocks"][0]["text"]
    assert not list(Path.cwd().glob("fixture.pdf"))
