from __future__ import annotations

from pathlib import Path

import numpy as np

from backend.engine import retrieve_hybrid
from tests.integration.test_retrieval import (
    QueryImageAdapter,
    QueryTextAdapter,
    make_plan,
    make_store,
    write_report,
)


def test_hybrid_retrieval_fuses_each_modality_and_keeps_models_injected(tmp_path: Path):
    config, plan = make_plan(tmp_path)
    storage = tmp_path / "owned"
    make_store(storage, plan, image=False)
    report = write_report(storage, plan, text_count=2, image_count=0)

    result = retrieve_hybrid(
        "first document",
        plan,
        storage,
        build_report_path=report,
        modalities=("text",),
        text_adapter=QueryTextAdapter(config, []),
        text_k=1,
    )
    assert result.modalities == ("text",)
    assert len(result.text_hits) == 1
    assert result.text_hits[0].record_id == "chunk-1"
    assert result.text_hits[0].rank == 1
    assert result.text_hits[0].document == "first document"
    assert result.text_hits[0].metadata["source"] == "document.pdf"
    assert result.diagnostics["text"]["bm25_only_docs"] >= 0


def test_hybrid_empty_modality_does_not_load_query_adapter(tmp_path: Path):
    config, plan = make_plan(tmp_path)
    storage = tmp_path / "owned"
    make_store(storage, plan, text=False, image=False)
    report = write_report(storage, plan, text_count=0, image_count=0)

    class MustNotLoad:
        def __init__(self, config):
            self.config = config
            self.model_name = config.text_embed_model

        def embed_query(self, query):
            raise AssertionError("empty modality loaded an adapter")

    result = retrieve_hybrid(
        "query", plan, storage, build_report_path=report,
        modalities=("text",), text_adapter=MustNotLoad(config),
    )
    assert result.text_hits == ()
