from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.config import (
    INDEX_DEFINING_FIELDS,
    LegacyConfigSchema,
    PathContainmentError,
    canonical_index_definition,
    index_fingerprint,
    resolve_owned_path,
)


def test_owned_path_resolves_nested_relative_path_without_creating_it(tmp_path):
    candidate = resolve_owned_path(tmp_path, Path("runs") / "one")
    assert candidate == (tmp_path / "runs" / "one").resolve()
    assert not candidate.exists()


@pytest.mark.parametrize("candidate", ["../outside", "../../", "C:/outside"])
def test_owned_path_rejects_traversal_and_absolute_escape(tmp_path, candidate):
    if os.name != "nt" and candidate == "C:/outside":
        pytest.skip("Windows drive path case is only meaningful on Windows")
    with pytest.raises(PathContainmentError):
        resolve_owned_path(tmp_path, candidate)


def test_owned_path_rejects_symlink_escape_when_supported(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable in this environment")
    with pytest.raises(PathContainmentError):
        resolve_owned_path(tmp_path, link / "file.bin")


def test_fingerprint_is_stable_and_canonical():
    config = LegacyConfigSchema()
    first = index_fingerprint(config, "A" * 64)
    second = index_fingerprint(config, "a" * 64)
    assert first == second
    assert len(first) == 64
    payload = canonical_index_definition(config, "a" * 64)
    assert tuple(payload["settings"]) == INDEX_DEFINING_FIELDS


@pytest.mark.parametrize("field", INDEX_DEFINING_FIELDS)
def test_each_index_defining_setting_changes_fingerprint(field):
    config = LegacyConfigSchema()
    changed = dict(config.model_dump())
    current = changed[field]
    if isinstance(current, int):
        changed[field] = current + 1
    elif isinstance(current, float):
        changed[field] = current + 0.01
    else:
        changed[field] = f"{current}-changed"
    assert index_fingerprint(config, "a" * 64) != index_fingerprint(
        LegacyConfigSchema(**changed), "a" * 64
    )


@pytest.mark.parametrize(
    "field",
    [
        "database_dir", "results_dir", "llm_temperature", "llm_max_tokens",
        "text_k", "reranker_model", "metrics_verbose",
    ],
)
def test_runtime_and_output_only_settings_do_not_change_fingerprint(field):
    config = LegacyConfigSchema()
    changed = dict(config.model_dump())
    values = {
        "database_dir": "different-db",
        "results_dir": "different-results",
        "llm_temperature": 1.0,
        "llm_max_tokens": 999,
        "text_k": 6,
        "reranker_model": "different-reranker",
        "metrics_verbose": False,
    }
    changed[field] = values[field]
    assert index_fingerprint(config, "a" * 64) == index_fingerprint(
        LegacyConfigSchema(**changed), "a" * 64
    )


@pytest.mark.parametrize("manifest_hash", ["", "abc", "g" * 64, "a" * 63])
def test_invalid_manifest_hash_is_rejected(manifest_hash):
    with pytest.raises(ValueError, match="document_manifest_hash"):
        index_fingerprint(LegacyConfigSchema(), manifest_hash)
