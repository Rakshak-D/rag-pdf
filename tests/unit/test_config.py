from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest

from notebooks.config import Config


def temporary_config(tmp_path: Path) -> Config:
    return Config(
        pdf_dir=str(tmp_path / "pdf"),
        images_dir=str(tmp_path / "images"),
        database_dir=str(tmp_path / "database"),
        results_dir=str(tmp_path / "results"),
        model_comparison_results_dir=str(tmp_path / "model-comparison"),
        retrieval_results_dir=str(tmp_path / "retrieval-results"),
    )


def test_default_configuration_fields_and_values_are_current():
    config = Config()
    names = {field.name for field in fields(Config)}

    assert config.retrieval_mode == "hybrid"
    assert config.bm25_weight == 0.5
    assert config.semantic_weight == 0.5
    assert config.chunk_max_tokens == 384
    assert config.chunk_overlap_tokens == 128
    assert config.max_context_tokens == 1024
    assert config.llm_max_tokens == 384
    assert config.reset_collections_on_start is True
    assert config.llm_models == ["gemma4:e2b"]
    assert {"pdf_dir", "database_dir", "retrieval_mode", "llm_model"}.issubset(names)


def test_valid_configuration_creates_only_explicit_temporary_directories(tmp_path):
    config = temporary_config(tmp_path)
    config.validate()

    assert all(Path(path).is_dir() for path in (
        config.pdf_dir,
        config.images_dir,
        config.database_dir,
        config.results_dir,
        config.model_comparison_results_dir,
        config.retrieval_results_dir,
    ))
    assert not (Path.cwd() / "data" / "database").exists() or config.database_dir != str(Path.cwd() / "data" / "database")


@pytest.mark.parametrize("field,value", [
    ("retrieval_mode", "invalid"),
    ("retrieval_mode", "dense"),
])
def test_invalid_retrieval_mode_is_rejected(tmp_path, field, value):
    config = temporary_config(tmp_path)
    setattr(config, field, value)

    with pytest.raises(ValueError, match="retrieval_mode"):
        config.validate()


def test_hybrid_weight_sum_is_validated(tmp_path):
    config = temporary_config(tmp_path)
    config.bm25_weight = 0.8
    config.semantic_weight = 0.3

    with pytest.raises(ValueError, match="sum to 1.0"):
        config.validate()


def test_top_k_relationship_is_validated(tmp_path):
    config = temporary_config(tmp_path)
    config.text_k = 4
    config.rerank_k = 5

    with pytest.raises(ValueError, match="text_k"):
        config.validate()


def test_filtering_dependency_is_validated(tmp_path):
    config = temporary_config(tmp_path)
    config.use_filtering = False
    config.use_percentile_filtering = True

    with pytest.raises(ValueError, match="use_percentile_filtering"):
        config.validate()


def test_reranker_weights_are_validated(tmp_path):
    config = temporary_config(tmp_path)
    config.reranker_ce_weight = 0.5

    with pytest.raises(ValueError, match="reranker_ce_weight"):
        config.validate()


def test_bm25_signal_weights_are_validated(tmp_path):
    config = temporary_config(tmp_path)
    config.bm25_signal_max_weight = 0.5

    with pytest.raises(ValueError, match="BM25 signal weights"):
        config.validate()
