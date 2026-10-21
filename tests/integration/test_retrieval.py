from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from backend.config import ResolvedRunConfig
from backend.engine import (
    RetrievalInputError,
    RetrievalQueryError,
    RetrievalReportError,
    RetrievalStoreError,
    retrieve_semantic,
)
from backend.engine.manifest import build_document_manifest
from backend.experiments import build_run_plan
from backend.vectorstore import ChromaStoreStateError, ChromaVectorStore
from notebooks.config import Config


def make_config(tmp_path: Path) -> ResolvedRunConfig:
    return ResolvedRunConfig.from_legacy(
        Config(
            pdf_dir=str(tmp_path / "pdfs"),
            images_dir=str(tmp_path / "images"),
            database_dir=str(tmp_path / "database"),
            results_dir=str(tmp_path / "results"),
            model_comparison_results_dir=str(tmp_path / "model-comparison"),
            retrieval_results_dir=str(tmp_path / "retrieval-results"),
            text_k=5,
            image_k=5,
            reset_collections_on_start=False,
        )
    )


def make_plan(tmp_path: Path):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    (pdf_dir / "document.pdf").write_bytes(b"deterministic source")
    config = make_config(tmp_path)
    plan = build_run_plan(config, build_document_manifest(pdf_dir).content_hash)
    return config, plan


def write_report(storage: Path, plan, *, status="completed", partial=False, text_count=1, image_count=1, build_id="build-1"):
    artifact = storage / "build-artifacts" / build_id
    artifact.mkdir(parents=True, exist_ok=True)
    report_path = artifact / "build-report.json"
    report_path.write_text(
        json.dumps(
            {
                "report_schema_version": "1",
                "build_id": build_id,
                "artifact_root": str(artifact),
                "report_path": str(report_path),
                "index_fingerprint": plan.index_fingerprint,
                "document_manifest_hash": plan.document_manifest_hash,
                "status": status,
                "partial_persistence": partial,
                "text_records_persisted": text_count,
                "image_records_persisted": image_count,
            }
        ),
        encoding="utf-8",
    )
    return report_path


def make_store(storage: Path, plan, *, text=True, image=True):
    store = ChromaVectorStore.open(storage, plan)
    if text:
        store.upsert_text(
            ["chunk-1", "chunk-2"],
            [[1.0, 0.0], [0.8, 0.2]],
            ["first document", "second document"],
            [
                {"source": "document.pdf", "page_num": 1, "bbox": "[1,2,3,4]"},
                {"source": "document.pdf", "page_num": 2, "bbox": "[5,6,7,8]"},
            ],
        )
    if image:
        store.upsert_image(
            ["image-1"],
            [[0.0, 1.0]],
            [{"image_id": "image-1", "image_path": "build-artifacts/build-1/images/image.png"}],
        )
    store.close()


class QueryTextAdapter:
    def __init__(self, config, calls):
        self.config = config
        self.model_name = config.text_embed_model
        self.calls = calls

    def embed_query(self, query):
        self.calls.append(query)
        return np.asarray([1.0, 0.0], dtype=np.float32)


class QueryImageAdapter:
    def __init__(self, config, calls):
        self.config = config
        self.model_name = config.image_embed_model
        self.pretrained = config.image_embed_pretrained
        self.calls = calls

    def embed_query(self, query):
        self.calls.append(query)
        return np.asarray([0.0, 1.0], dtype=np.float32)


def test_combined_retrieval_validates_report_and_keeps_modalities_separate(tmp_path):
    config, plan = make_plan(tmp_path)
    storage = tmp_path / "owned"
    make_store(storage, plan)
    report = write_report(storage, plan, text_count=2, image_count=1)
    text_calls: list[str] = []
    image_calls: list[str] = []
    result = retrieve_semantic(
        "find the document",
        plan,
        storage,
        build_report_path=report,
        text_adapter=QueryTextAdapter(config, text_calls),
        image_adapter=QueryImageAdapter(config, image_calls),
        text_k=20,
        image_k=20,
    )
    assert result.effective_text_k == 2
    assert result.effective_image_k == 1
    assert [hit.chunk_id for hit in result.text_hits] == ["chunk-1", "chunk-2"]
    assert [hit.image_id for hit in result.image_hits] == ["image-1"]
    assert result.text_hits[0].rank == 1
    assert result.text_hits[0].similarity == pytest.approx(1.0)
    assert result.text_hits[0].metadata["bbox"] == [1, 2, 3, 4]
    assert text_calls == ["find the document"]
    assert image_calls == ["find the document"]
    repeated = retrieve_semantic(
        "find the document", plan, storage, build_report_path=report,
        text_adapter=QueryTextAdapter(config, []), modalities=("text",), text_k=2,
    )
    assert repeated.query_id == retrieve_semantic(
        "find the document", plan, storage, build_report_path=report,
        text_adapter=QueryTextAdapter(config, []), modalities=("text",), text_k=2,
    ).query_id


def test_failed_or_partial_report_is_rejected_before_query_embedding(tmp_path):
    config, plan = make_plan(tmp_path)
    storage = tmp_path / "owned"
    make_store(storage, plan)
    for status, partial in (("failed", False), ("completed", True)):
        report = write_report(storage, plan, status=status, partial=partial)
        calls: list[str] = []
        with pytest.raises(RetrievalReportError):
            retrieve_semantic(
                "query", plan, storage, build_report_path=report,
                text_adapter=QueryTextAdapter(config, calls), modalities=("text",),
            )
        assert calls == []


def test_absent_index_is_not_created_by_retrieval(tmp_path):
    config, plan = make_plan(tmp_path)
    storage = tmp_path / "owned"
    report = write_report(storage, plan, text_count=0, image_count=0)
    with pytest.raises(RetrievalStoreError):
        retrieve_semantic("query", plan, storage, build_report_path=report, modalities=("text",))
    assert not (storage / "indexes").exists()


def test_open_existing_rejects_incomplete_index_without_repair(tmp_path):
    config, plan = make_plan(tmp_path)
    storage = tmp_path / "owned"
    index_root = storage / "indexes" / plan.index_fingerprint
    index_root.mkdir(parents=True)
    with pytest.raises(ChromaStoreStateError, match="incomplete|manifest"):
        ChromaVectorStore.open_existing(storage, plan)
    assert not (index_root / "index-manifest.json").exists()
    assert not (index_root / "chroma").exists()


def test_empty_collections_do_not_load_query_models(tmp_path):
    config, plan = make_plan(tmp_path)
    storage = tmp_path / "owned"
    make_store(storage, plan, text=False, image=False)
    report = write_report(storage, plan, text_count=0, image_count=0)

    class FailIfCalled:
        def __init__(self, config, modality):
            self.config = config
            self.model_name = (
                config.text_embed_model if modality == "text" else config.image_embed_model
            )
            self.pretrained = config.image_embed_pretrained

        def embed_query(self, query):
            raise AssertionError("empty collection loaded a query model")

    result = retrieve_semantic(
        "query", plan, storage, build_report_path=report,
        text_adapter=FailIfCalled(config, "text"),
        image_adapter=FailIfCalled(config, "image"),
    )
    assert result.text_hits == ()
    assert result.image_hits == ()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"text_k": 0},
        {"text_k": True},
        {"modalities": ("audio",)},
        {"modalities": ("text", "text")},
    ],
)
def test_invalid_retrieval_inputs_are_rejected(tmp_path, kwargs):
    config, plan = make_plan(tmp_path)
    storage = tmp_path / "owned"
    with pytest.raises(RetrievalInputError):
        retrieve_semantic("query", plan, storage, build_report_path="report.json", **kwargs)


def test_missing_misplaced_and_tampered_reports_are_rejected(tmp_path):
    config, plan = make_plan(tmp_path)
    storage = tmp_path / "owned"
    with pytest.raises(RetrievalReportError):
        retrieve_semantic("query", plan, storage, build_report_path="missing.json")
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    with pytest.raises(RetrievalReportError):
        retrieve_semantic("query", plan, storage, build_report_path=outside)
    make_store(storage, plan)
    report = write_report(storage, plan)
    payload = json.loads(report.read_text(encoding="utf-8"))
    payload["index_fingerprint"] = "0" * 64
    report.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RetrievalReportError):
        retrieve_semantic("query", plan, storage, build_report_path=report)

    payload["index_fingerprint"] = plan.index_fingerprint
    payload["text_records_persisted"] = 99
    report.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RetrievalStoreError, match="count"):
        retrieve_semantic(
            "query", plan, storage, build_report_path=report,
            modalities=("text",), text_adapter=QueryTextAdapter(config, []),
        )


def test_malformed_chroma_distances_are_rejected_and_store_is_closed(tmp_path, monkeypatch):
    config, plan = make_plan(tmp_path)
    storage = tmp_path / "owned"
    make_store(storage, plan, image=False)
    report = write_report(storage, plan, text_count=2, image_count=0)

    def malformed_query(self, vector, n_results):
        return {"ids": [["chunk-1"]], "documents": [["first"]], "metadatas": [[{}]], "distances": [[float("nan")]]}

    monkeypatch.setattr(ChromaVectorStore, "query_text", malformed_query)
    with pytest.raises(RetrievalQueryError, match="non-finite distance"):
        retrieve_semantic(
            "query", plan, storage, build_report_path=report,
            text_adapter=QueryTextAdapter(config, []), modalities=("text",),
        )


def test_empty_query_is_rejected_before_report_or_store_access(tmp_path):
    config, plan = make_plan(tmp_path)
    with pytest.raises(RetrievalInputError):
        retrieve_semantic("   ", plan, tmp_path / "owned", build_report_path="missing.json")
