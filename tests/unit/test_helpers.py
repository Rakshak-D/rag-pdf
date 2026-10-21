from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from tests.support.source_loader import load_functions


def test_stable_chunk_id_is_source_page_and_md5_prefix():
    stable_chunk_id = load_functions("stable_chunk_id")["stable_chunk_id"]
    text = "Voyager trajectory"
    expected = f"Voyager.pdf_p3_c{hashlib.md5(text.encode('utf-8')).hexdigest()[:8]}"

    assert stable_chunk_id("Voyager.pdf", 3, text) == expected
    assert stable_chunk_id("Voyager.pdf", 3, text) == stable_chunk_id("Voyager.pdf", 3, text)
    assert stable_chunk_id("Voyager.pdf", 4, text) != expected


def test_bbox_helpers_cover_empty_touching_and_disjoint_cases():
    functions = load_functions("merge_bbox", "bbox_overlap", "vertical_gap")

    assert functions["merge_bbox"]([]) is None
    assert functions["merge_bbox"]([
        {"bbox": (3, 4, 10, 12)},
        {"bbox": (1, 8, 15, 20)},
    ]) == (1, 4, 15, 20)
    assert functions["bbox_overlap"]((0, 0, 10, 10), (10, 10, 20, 20)) is True
    assert functions["bbox_overlap"]((0, 0, 10, 10), (11, 0, 20, 10)) is False
    assert functions["vertical_gap"]({"bbox": (0, 0, 1, 10)}, {"bbox": (0, 25, 1, 30)}) == 15


def test_stable_hash_and_metadata_round_trip_follow_source_contract():
    functions = load_functions("stable_hash", "sanitize_metadata", "deserialize_metadata")
    stable_hash = functions["stable_hash"]
    sanitize = functions["sanitize_metadata"]
    deserialize = functions["deserialize_metadata"]

    assert stable_hash({"b": 2, "a": 1}) == stable_hash({"a": 1, "b": 2})
    assert len(stable_hash("content")) == 64
    with pytest.raises(TypeError):
        stable_hash(123)

    clean = sanitize({"none": None, "bbox": (1, 2, 3, 4), "count": 2, "obj": object()})
    assert clean["none"] == ""
    assert json.loads(clean["bbox"]) == [1, 2, 3, 4]
    assert clean["count"] == 2
    assert isinstance(clean["obj"], str)

    assert deserialize({"bbox": clean["bbox"], "related_image_ids": '["img-1"]'}) == {
        "bbox": [1, 2, 3, 4],
        "related_image_ids": ["img-1"],
    }
    assert deserialize({"bbox": "not-json"})["bbox"] == "not-json"
    assert deserialize(None) == {}


def test_caption_selection_prioritizes_specific_caption_then_heading_then_id():
    select = load_functions("_select_best_caption")["_select_best_caption"]

    assert select("  Specific image caption  ", "Heading", "img-1") == "Specific image caption"
    assert select("short", "Heading", "img-1") == "Heading"
    assert select("", "", "img-1") == "img-1"


def test_image_objects_use_existing_caption_association_logic():
    functions = load_functions(
        "bbox_overlap", "is_caption_block", "build_image_objects",
    )
    pages = [{
        "source": "fixture.pdf",
        "page": 2,
        "text_blocks": [
            {"text": "Figure 1: spacecraft", "bbox": (0, 105, 100, 120)},
            {"text": "far away text", "bbox": (0, 400, 100, 415)},
        ],
        "images": [{"image_id": "img-2", "bbox": (0, 0, 100, 100), "path": "x.png"}],
    }]

    result = functions["build_image_objects"](pages)
    assert result[0]["image_id"] == "img-2"
    assert result[0]["source"] == "fixture.pdf"
    assert result[0]["page_num"] == 2
    assert result[0]["caption_text"] == "Figure 1: spacecraft"


@dataclass
class DocumentStub:
    page_content: str
    metadata: dict


class TokenizerStub:
    def encode(self, text):
        return text.split()


def test_bbox_chunker_preserves_metadata_and_respects_token_boundary():
    functions = load_functions(
        "bbox_overlap", "vertical_gap", "merge_bbox", "stable_chunk_id",
        "get_overlapping_images", "build_chunk", "bbox_chunker",
        globals_override={
            "Document": DocumentStub,
            "cfg": SimpleNamespace(chunk_max_tokens=384, chunk_max_vertical_gap=60,
                                    chunk_overlap_tokens=128, tiktoken_encoding="stub"),
            "tiktoken": SimpleNamespace(get_encoding=lambda _: TokenizerStub()),
            "LOGGER": SimpleNamespace(info=lambda *args, **kwargs: None),
        },
    )
    pages = [{
        "source": "fixture.pdf",
        "page": 1,
        "text_blocks": [
            {"text": "alpha beta", "bbox": (0, 0, 40, 10)},
            {"text": "gamma delta", "bbox": (0, 20, 40, 30)},
            {"text": "epsilon zeta", "bbox": (0, 100, 40, 110)},
        ],
        "images": [{"image_id": "img-1", "bbox": (0, 35, 40, 50)}],
    }]

    chunks = functions["bbox_chunker"](pages, max_tokens=4, max_vertical_gap=60, overlap_tokens=0)

    assert [chunk.page_content for chunk in chunks] == ["alpha beta\ngamma delta", "epsilon zeta"]
    assert chunks[0].metadata["source"] == "fixture.pdf"
    assert chunks[0].metadata["page_num"] == 1
    assert chunks[0].metadata["related_image_ids"] == ["img-1"]
    assert chunks[0].metadata["chunk_id"].startswith("fixture.pdf_p1_c")
    assert chunks[1].metadata["bbox"] == (0, 100, 40, 110)
