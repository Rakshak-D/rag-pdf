from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.config import ResolvedRunConfig
from backend.experiments import build_run_plan
from backend.vectorstore import ChromaStoreStateError, ChromaVectorStore
from notebooks.config import Config


def make_plan(tmp_path: Path, *, manifest_hash: str = "a" * 64, model_name: str = "model-a"):
    config = ResolvedRunConfig.from_legacy(
        Config(
            pdf_dir=str(tmp_path / "pdfs"),
            images_dir=str(tmp_path / "images"),
            database_dir=str(tmp_path / "database"),
            results_dir=str(tmp_path / "results"),
            model_comparison_results_dir=str(tmp_path / "model-comparison"),
            retrieval_results_dir=str(tmp_path / "retrieval-results"),
            text_embed_model=model_name,
            reset_collections_on_start=False,
        )
    )
    return build_run_plan(config, manifest_hash)


def test_supplied_vectors_are_persistent_and_modalities_are_separate(tmp_path):
    storage = tmp_path / "owned"
    plan = make_plan(tmp_path)
    store = ChromaVectorStore.open(storage, plan)
    store.upsert_text(["same-id"], [[1.0, 0.0]], ["text body"], [{"page": 1}])
    store.upsert_image(["same-id"], [[0.0, 1.0]], [{"page": 2}])
    assert store.count("text") == 1
    assert store.count("image") == 1
    assert store.ids("text") == ["same-id"]
    assert store.ids("image") == ["same-id"]
    text_result = store.query_text([1.0, 0.0], 1)
    assert text_result["ids"] == [["same-id"]]
    assert text_result["metadatas"] == [[{"page": 1}]]
    store.close()

    reopened = ChromaVectorStore.open(storage, plan)
    assert reopened.count("text") == 1
    assert reopened.count("image") == 1
    assert reopened.query_image([0.0, 1.0], 1)["ids"] == [["same-id"]]


def test_distinct_index_fingerprints_are_isolated(tmp_path):
    storage = tmp_path / "owned"
    first = make_plan(tmp_path, model_name="model-a")
    second = make_plan(tmp_path, model_name="model-b")
    assert first.index_fingerprint != second.index_fingerprint
    first_store = ChromaVectorStore.open(storage, first)
    second_store = ChromaVectorStore.open(storage, second)
    first_store.upsert_text(["first"], [[1.0, 0.0]], ["first"], [{"source": "first"}])
    assert first_store.count("text") == 1
    assert second_store.count("text") == 0
    assert first_store.index_root != second_store.index_root


def test_tampered_manifest_and_collection_metadata_fail_without_repair(tmp_path):
    storage = tmp_path / "owned"
    plan = make_plan(tmp_path)
    store = ChromaVectorStore.open(storage, plan)
    store.upsert_text(["keep"], [[1.0, 0.0]], ["keep"], [{"source": "keep"}])
    store.close()
    index_root = storage / "indexes" / plan.index_fingerprint
    manifest_path = index_root / "index-manifest.json"
    original = manifest_path.read_text(encoding="utf-8")
    payload = json.loads(original)
    payload["index_schema_version"] = "unknown"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ChromaStoreStateError, match="index manifest identity mismatch"):
        ChromaVectorStore.open(storage, plan)
    manifest_path.write_text(original, encoding="utf-8")

    repaired = ChromaVectorStore.open(storage, plan)
    repaired.close()
    import chromadb

    client = chromadb.PersistentClient(path=str(index_root / "chroma"))
    collection = client.get_collection("text_vectors", embedding_function=None)
    tampered_metadata = {
        key: value for key, value in collection.metadata.items() if key != "hnsw:space"
    }
    tampered_metadata["index_fingerprint"] = "b" * 64
    collection.modify(metadata=tampered_metadata)
    with pytest.raises(ChromaStoreStateError, match="metadata mismatch"):
        ChromaVectorStore.open(storage, plan)
    assert repaired is not None


def test_manifest_contains_versioned_identity_and_cosine_metadata(tmp_path):
    plan = make_plan(tmp_path)
    store = ChromaVectorStore.open(tmp_path / "owned", plan)
    payload = json.loads((store.index_root / "index-manifest.json").read_text(encoding="utf-8"))
    assert payload["index_fingerprint"] == plan.index_fingerprint
    assert payload["document_manifest_hash"] == plan.document_manifest_hash
    assert store._collections["text"].metadata["hnsw:space"] == "cosine"
    assert store._collections["image"].metadata["modality"] == "image"
