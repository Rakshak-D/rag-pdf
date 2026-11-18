"""Generate small deterministic PDFs and independent PyMuPDF geometry oracles."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import fitz


PYMUPDF_VERSION = "1.26.7"


def _glyph_rect(page: fitz.Page, text: str, origin: tuple[float, float], font_size: float) -> list[float]:
    """Bound authored Helvetica glyph outlines, then apply the PDF page rotation."""
    matches = page.search_for(text)
    if len(matches) != 1:
        raise RuntimeError(f"fixture text did not resolve uniquely: {text}")
    font = fitz.Font("helv")
    cursor = origin[0]
    glyph_rects: list[fitz.Rect] = []
    for character in text:
        glyph = font.glyph_bbox(ord(character))
        if not character.isspace():
            glyph_rects.append(fitz.Rect(
                cursor + glyph.x0 * font_size,
                origin[1] - glyph.y1 * font_size,
                cursor + glyph.x1 * font_size,
                origin[1] - glyph.y0 * font_size,
            ))
        cursor += font.text_length(character, fontsize=font_size)
    if not glyph_rects:
        raise RuntimeError("fixture line has no visible glyphs")
    rect = glyph_rects[0]
    for glyph in glyph_rects[1:]:
        rect |= glyph
    rect *= page.rotation_matrix
    return [rect.x0, rect.y0, rect.x1, rect.y1]


def _write_layout_pdf(destination: Path) -> dict[str, object]:
    document = fitz.open()
    first = document.new_page(width=420, height=600)
    first.insert_text((40, 100), "A column concludes with source", fontsize=12, fontname="helv")
    first.insert_text((220, 100), "Second column has unrelated text", fontsize=12, fontname="helv")
    first.insert_text((40, 200), "A stable multiline passage", fontsize=14, fontname="helv")
    first.insert_text((40, 218), "continues on the next line.", fontsize=14, fontname="helv")
    first.insert_text((40, 270), "Variable page dimensions retain source geometry", fontsize=12, fontname="helv")

    rotated = document.new_page(width=500, height=320)
    rotated.insert_text((40, 120), "Rotated source passage at ninety degrees", fontsize=14, fontname="helv")
    rotated.set_rotation(90)
    first = document.load_page(0)
    rotated = document.load_page(1)

    document.set_metadata({
        "title": "RAG-PDF deterministic PDF.js layout fixture",
        "author": "RAG-PDF test fixture",
        "subject": "Synthetic text placement for source-geometry regression tests",
        "keywords": "test fixture, PDF.js, layout",
        "creator": "generate_pdfjs_layout_fixtures.py",
        "producer": "PyMuPDF 1.26.7",
        "creationDate": "D:20200101000000Z",
        "modDate": "D:20200101000000Z",
    })
    page_data = {
        "page_count": document.page_count,
        "pages": [
            {
                "page": 1,
                "width": float(first.rect.width),
                "height": float(first.rect.height),
                "unrotated_width": float(first.mediabox.width),
                "unrotated_height": float(first.mediabox.height),
                "rotation": int(first.rotation),
            },
            {
                "page": 2,
                "width": float(rotated.rect.width),
                "height": float(rotated.rect.height),
                "unrotated_width": float(rotated.mediabox.width),
                "unrotated_height": float(rotated.mediabox.height),
                "rotation": int(rotated.rotation),
            },
        ],
        "cases": {
            "multiline": {
                "page": 1,
                "passage": "A stable multiline passage continues on the next line.",
                "expected_lines": [
                    _glyph_rect(first, "A stable multiline passage", (40, 200), 14),
                    _glyph_rect(first, "continues on the next line.", (40, 218), 14),
                ],
            },
            "variable-dimensions": {
                "page": 1,
                "passage": "Variable page dimensions retain source geometry",
                "expected_lines": [_glyph_rect(first, "Variable page dimensions retain source geometry", (40, 270), 12)],
            },
            "column-discontinuity": {
                "page": 1,
                "passage": "source Second column",
                "expected_lines": [],
                "expected_fallback": True,
            },
            "rotated": {
                "page": 2,
                "passage": "Rotated source passage at ninety degrees",
                "expected_lines": [_glyph_rect(rotated, "Rotated source passage at ninety degrees", (40, 120), 14)],
            },
        },
    }
    document.save(destination, garbage=4, deflate=True, no_new_id=True)
    return {
        "file": destination.name,
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        **page_data,
    }


def generate(output_dir: Path) -> dict[str, object]:
    if fitz.VersionBind != PYMUPDF_VERSION:
        raise RuntimeError(f"PyMuPDF {PYMUPDF_VERSION} required; found {fitz.VersionBind}")
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf = output_dir / "layout-integrity.pdf"
    metadata = _write_layout_pdf(pdf)
    manifest = {
        "generator": "scripts/generate_pdfjs_layout_fixtures.py",
        "pymupdf_version": fitz.VersionBind,
        "oracle": "PyMuPDF Page.search_for verifies authored text; Helvetica glyph metrics and Page.rotation_matrix produce independent expected rectangles",
        "documents": [metadata],
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    manifest = generate(args.output_dir)
    print(json.dumps({"documents": len(manifest["documents"]), "pymupdf_version": fitz.VersionBind}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
