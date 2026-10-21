from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.config import PathContainmentError, ResolvedRunConfig
from backend.experiments import build_run_plan
from backend.vectorstore import ChromaStoreInputError, ChromaStoreStateError, ChromaVectorStore
from notebooks.config import Config


def make_plan(
    tmp_path: Path,
    *,
    manifest_hash: str | None = None,
    pdf_dir: Path | None = None,
):
    config = ResolvedRunConfig.from_legacy(
        Config(
            pdf_dir=str(pdf_dir or (tmp_path / "pdfs")),
            images_dir=str(tmp_path / "images"),
            database_dir=str(tmp_path / "database"),
            results_dir=str(tmp_path / "results"),
            model_comparison_results_dir=str(tmp_path / "model-comparison"),
            retrieval_results_dir=str(tmp_path / "retrieval-results"),
            reset_collections_on_start=False,
        )
    )
    return build_run_plan(config, manifest_hash or "a" * 64)


def test_store_import_and_path_are_side_effect_free(tmp_path):
    storage = tmp_path / "owned"
    assert not storage.exists()
    plan = make_plan(tmp_path)
    # Opening is the explicit side-effect boundary, not importing the module.
    assert not (storage / "indexes" / plan.index_fingerprint).exists()


def test_invalid_batches_and_queries_are_rejected_before_chroma_calls(tmp_path):
    store = ChromaVectorStore.open(tmp_path / "owned", make_plan(tmp_path))
    with pytest.raises(ChromaStoreInputError, match="length"):
        store.upsert_text(["a"], [[1.0, 0.0], [0.0, 1.0]], ["doc"])
    with pytest.raises(ChromaStoreInputError, match="unique"):
        store.upsert_image(["a", "a"], [[1.0, 0.0], [0.0, 1.0]])
    with pytest.raises(ChromaStoreInputError, match="finite"):
        store.upsert_image(["a"], [[float("nan"), 0.0]])
    with pytest.raises(ChromaStoreInputError, match="non-empty"):
        store.upsert_image(["a"], [[]])
    with pytest.raises(ChromaStoreInputError, match="positive"):
        store.query_text([1.0, 0.0], 0)
    store.close()


def test_invalid_plan_is_rejected_before_storage_creation(tmp_path):
    plan = make_plan(tmp_path)
    payload = json.loads(plan.to_serializable_json())
    payload["index_fingerprint"] = "b" * 64
    with pytest.raises(Exception):
        ChromaVectorStore.open(tmp_path / "owned", type(plan).model_validate(payload))
    assert not (tmp_path / "owned").exists()


@pytest.mark.parametrize("layout", ["equal", "storage-child", "source-child"])
def test_storage_and_configured_pdf_roots_must_be_disjoint(tmp_path, layout):
    if layout == "equal":
        storage = tmp_path / "owned"
        pdf_dir = storage
    elif layout == "storage-child":
        pdf_dir = tmp_path / "pdfs"
        storage = pdf_dir / "owned"
    else:
        storage = tmp_path / "owned"
        pdf_dir = storage / "pdfs"
    plan = make_plan(tmp_path, pdf_dir=pdf_dir)

    with pytest.raises(PathContainmentError, match="must be disjoint"):
        ChromaVectorStore.open(storage, plan)
    assert not storage.exists()


def test_symlinked_indexes_path_is_rejected_before_store_creation(tmp_path):
    storage = tmp_path / "owned"
    storage.mkdir()
    target = storage / "snapshots"
    target.mkdir()
    try:
        (storage / "indexes").symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable in this environment")

    with pytest.raises(ChromaStoreStateError, match="symlinked indexes directory"):
        ChromaVectorStore.open(storage, make_plan(tmp_path))
    assert not (target / "unknown").exists()


def test_symlinked_index_directory_is_rejected_before_store_creation(tmp_path):
    storage = tmp_path / "owned"
    indexes = storage / "indexes"
    indexes.mkdir(parents=True)
    target = storage / "snapshots" / "redirect"
    target.mkdir(parents=True)
    plan = make_plan(tmp_path)
    try:
        (indexes / plan.index_fingerprint).symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable in this environment")

    with pytest.raises(ChromaStoreStateError, match="symlinked index directory"):
        ChromaVectorStore.open(storage, plan)
    assert not (target / "chroma").exists()


def test_incomplete_store_is_rejected_without_repair(tmp_path):
    plan = make_plan(tmp_path)
    index_root = tmp_path / "owned" / "indexes" / plan.index_fingerprint
    index_root.mkdir(parents=True)
    marker = index_root / "marker.txt"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(ChromaStoreStateError, match="incomplete"):
        ChromaVectorStore.open(tmp_path / "owned", plan)
    assert marker.read_text(encoding="utf-8") == "keep"
