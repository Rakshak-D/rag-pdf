from __future__ import annotations

from pathlib import Path

import tiktoken
from langchain_core.documents import Document

from backend.config import ResolvedRunConfig
from backend.engine.ingestion import (
    bbox_chunker,
    build_image_objects,
    get_overlapping_images,
    is_caption_block,
    merge_bbox,
    stable_chunk_id,
    vertical_gap,
)
from notebooks.config import Config
from tests.support.source_loader import load_functions


def chunk_config(**overrides) -> ResolvedRunConfig:
    values = Config(
        reset_collections_on_start=False,
        pdf_dir=str(Path("pdf")),
        images_dir=str(Path("images")),
        database_dir=str(Path("database")),
        results_dir=str(Path("results")),
        model_comparison_results_dir=str(Path("model-comparison")),
        retrieval_results_dir=str(Path("retrieval-results")),
        **overrides,
    )
    return ResolvedRunConfig.from_legacy(values)


def synthetic_pages():
    return [{
        "source": "sample.pdf",
        "page": 1,
        "text_blocks": [
            {"text": "First block contains stable text.", "bbox": [10, 10, 100, 20], "page": 1},
            {"text": "Second block contains more stable text.", "bbox": [10, 30, 100, 40], "page": 1},
            {"text": "Third block is separated vertically.", "bbox": [10, 120, 100, 130], "page": 1},
        ],
        "images": [{
            "image_id": "sample_p1_i0",
            "path": "images/sample/page_1_img_0.png",
            "page": 1,
            "bbox": [0, 0, 120, 25],
        }],
    }]


def test_chunker_matches_selected_research_definition():
    config = chunk_config(chunk_max_tokens=100, chunk_overlap_tokens=20, chunk_max_vertical_gap=60)
    pages = synthetic_pages()
    selected = load_functions(
        "vertical_gap", "merge_bbox", "stable_chunk_id", "bbox_overlap",
        "is_caption_block", "get_overlapping_images", "build_chunk", "bbox_chunker",
        globals_override={
            "Document": Document,
            "tiktoken": tiktoken,
            "cfg": config,
            "LOGGER": type("Logger", (), {"info": lambda *args, **kwargs: None})(),
        },
    )

    expected = selected["bbox_chunker"](pages)
    actual = bbox_chunker(pages, config)

    assert [(doc.page_content, doc.metadata) for doc in actual] == [
        (doc.page_content, doc.metadata) for doc in expected
    ]


def test_chunker_handles_empty_pages_and_preserves_layout_metadata():
    config = chunk_config(chunk_max_tokens=100, chunk_overlap_tokens=0)
    pages = [{"source": "empty.pdf", "page": 1, "text_blocks": [], "images": []}]
    assert bbox_chunker(pages, config) == []

    chunks = bbox_chunker(synthetic_pages(), config)
    assert chunks
    assert chunks[0].metadata["source"] == "sample.pdf"
    assert chunks[0].metadata["page_num"] == 1
    assert chunks[0].metadata["bbox"] == (10, 10, 100, 40)
    assert chunks[0].metadata["related_image_ids"] == ["sample_p1_i0"]
    assert chunks[0].metadata["semantic_tag"] == "First block contains stable text"


def test_chunking_helpers_match_research_contracts():
    selected = load_functions(
        "vertical_gap", "merge_bbox", "stable_chunk_id", "bbox_overlap",
        "is_caption_block", "get_overlapping_images",
    )
    first = synthetic_pages()[0]["text_blocks"][0]
    second = synthetic_pages()[0]["text_blocks"][1]
    image = synthetic_pages()[0]["images"][0]

    assert vertical_gap(first, second) == selected["vertical_gap"](first, second)
    assert merge_bbox([first, second]) == selected["merge_bbox"]([first, second])
    assert stable_chunk_id("sample.pdf", 1, first["text"]) == selected["stable_chunk_id"](
        "sample.pdf", 1, first["text"]
    )
    assert get_overlapping_images(image["bbox"], [image]) == selected["get_overlapping_images"](
        image["bbox"], [image]
    )
    caption = {"text": "Figure one caption", "bbox": [0, 30, 120, 40]}
    assert is_caption_block(caption, image) == selected["is_caption_block"](caption, image)


def test_image_object_metadata_matches_research_definition():
    selected = load_functions(
        "is_caption_block", "build_image_objects",
        globals_override={},
    )
    pages = synthetic_pages()
    expected = selected["build_image_objects"](pages)
    actual = build_image_objects(pages)
    assert actual == expected
