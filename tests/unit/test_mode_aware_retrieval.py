from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from backend.config import ResolvedRunConfig
from backend.engine import retrieval as retrieval_module
from backend.engine.retrieval import (
    HybridHit,
    HybridRetrievalResult,
    ImageSemanticHit,
    SemanticRetrievalResult,
    TextSemanticHit,
    retrieve_for_plan,
)
from backend.experiments import build_run_plan
from notebooks.config import Config


def make_plan(mode: str):
    config = ResolvedRunConfig.from_legacy(
        Config(
            retrieval_mode=mode,
            reset_collections_on_start=False,
            adaptive_weighting=False,
            use_weighted_fusion=False,
            use_filtering=False,
            use_percentile_filtering=False,
        )
    )
    return build_run_plan(config, "a" * 64)


def write_report(storage: Path, plan, *, text_count=1, image_count=1):
    artifact = storage / "build-artifacts" / "build-1"
    artifact.mkdir(parents=True)
    report = artifact / "build-report.json"
    report.write_text(
        json.dumps(
            {
                "report_schema_version": "1",
                "build_id": "build-1",
                "artifact_root": str(artifact),
                "report_path": str(report),
                "index_fingerprint": plan.index_fingerprint,
                "document_manifest_hash": plan.document_manifest_hash,
                "status": "completed",
                "partial_persistence": False,
                "text_records_persisted": text_count,
                "image_records_persisted": image_count,
                "text_embedding_seconds": 1.0,
                "image_embedding_seconds": 2.0,
            }
        ),
        encoding="utf-8",
    )
    return report


def test_semantic_dispatch_normalizes_hits_without_bm25(monkeypatch, tmp_path):
    plan = make_plan("semantic")
    semantic = SemanticRetrievalResult(
        query_id="0" * 64,
        index_fingerprint=plan.index_fingerprint,
        document_manifest_hash=plan.document_manifest_hash,
        modalities=("text", "image"),
        effective_text_k=2,
        effective_image_k=1,
        text_hits=(TextSemanticHit("chunk-1", 1, 0.2, 0.8, "evidence", {"page_num": 1}),),
        image_hits=(ImageSemanticHit("image-1", 1, 0.3, 0.7, {"caption_text": "figure"}),),
        modality_timings=(("text", 0.1), ("image", 0.2)),
        total_elapsed_seconds=0.3,
        text_embedding_seconds=1.0,
        image_embedding_seconds=2.0,
        text_records_persisted=4,
        image_records_persisted=2,
    )

    def fake_semantic(*args, **kwargs):
        return semantic

    monkeypatch.setattr(retrieval_module, "retrieve_semantic", fake_semantic)
    monkeypatch.setattr(retrieval_module, "BM25Index", lambda *args: (_ for _ in ()).throw(AssertionError("BM25 must not run")))
    result = retrieve_for_plan("question", plan, tmp_path, build_report_path="unused.json", modalities=("text", "image"), text_k=2, image_k=1)
    assert isinstance(result, HybridRetrievalResult)
    assert result.query_id == retrieval_module.query_id_for("question", plan, ("text", "image"), 2, 1)
    assert result.fusion_strategy == "semantic"
    assert result.text_hits[0].fused_score == 0.8
    assert result.text_hits[0].semantic_similarity == 0.8
    assert result.text_hits[0].bm25_score is None
    assert result.text_hits[0].sources == ("semantic",)
    assert result.image_hits[0].caption == "figure"
    assert result.text_embedding_seconds == 1.0
    assert result.text_records_persisted == 4


def test_bm25_dispatch_uses_records_without_embeddings_or_vector_queries(monkeypatch, tmp_path):
    plan = make_plan("bm25")
    storage = tmp_path / "storage"
    report = write_report(storage, plan, text_count=1, image_count=1)

    class Records:
        def __init__(self, ids, documents, metadatas):
            self.ids = ids
            self.documents = documents
            self.metadatas = metadatas

    class Store:
        def __init__(self):
            self.closed = False
            self.reads = []

        def count(self, modality):
            return 1

        def read_records(self, modality):
            self.reads.append(modality)
            if modality == "text":
                return Records(("chunk-1",), ("alpha evidence",), ({"page_num": 1},))
            return Records(("image-1",), (None,), ({"caption_text": "alpha figure", "image_path": "safe.png"},))

        def query_text(self, *args):
            raise AssertionError("BM25-only mode must not query vectors")

        def query_image(self, *args):
            raise AssertionError("BM25-only mode must not query vectors")

        def close(self):
            self.closed = True

    store = Store()
    monkeypatch.setattr(retrieval_module.ChromaVectorStore, "open_existing", lambda *args, **kwargs: store)
    result = retrieve_for_plan("alpha", plan, storage, build_report_path=report, modalities=("text", "image"), text_k=1, image_k=1)
    assert isinstance(result, HybridRetrievalResult)
    assert result.fusion_strategy == "bm25"
    assert result.modalities == ("text", "image")
    assert result.text_hits[0].sources == ("bm25",)
    assert result.text_hits[0].bm25_score == result.text_hits[0].fused_score
    assert result.image_hits[0].caption == "alpha figure"
    assert result.text_records_persisted == 1
    assert result.image_records_persisted == 1
    assert store.reads == ["text", "image"]
    assert store.closed


def test_hybrid_dispatch_delegates_unchanged(monkeypatch, tmp_path):
    plan = make_plan("hybrid")
    expected = HybridRetrievalResult(
        "0" * 64, plan.index_fingerprint, plan.document_manifest_hash, ("text",), 1, None,
        (HybridHit("chunk-1", 1, 0.5, {}, sources=("bm25", "semantic")),), (),
        "rrf", (0.5, 0.5), {}, (), 0.1, query_text="question",
    )
    called = {}

    def fake_hybrid(*args, **kwargs):
        called.update(kwargs)
        return expected

    monkeypatch.setattr(retrieval_module, "retrieve_hybrid", fake_hybrid)
    result = retrieve_for_plan("question", plan, tmp_path, build_report_path="report.json", modalities=("text",), text_k=1)
    assert result is expected
    assert called["modalities"] == ("text",)
    assert called["text_k"] == 1
