"""Canonical fingerprints for immutable index definitions."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from .schema import LegacyConfigSchema


INDEX_SCHEMA_VERSION = "1"
INDEX_DEFINING_FIELDS = (
    "chunk_max_tokens",
    "chunk_overlap_tokens",
    "chunk_max_vertical_gap",
    "chunk_min_text_len",
    "tiktoken_encoding",
    "text_embed_model",
    "image_embed_model",
    "image_embed_pretrained",
    "image_caption_image_weight",
    "img_variance_threshold",
    "img_white_pixel_threshold",
    "img_white_ratio_threshold",
    "img_min_width",
    "img_min_height",
    "img_min_aspect_ratio",
    "img_max_aspect_ratio",
    "text_embed_batch_size",
    "image_embed_batch_size",
)

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _validated_values(config: LegacyConfigSchema | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(config, LegacyConfigSchema):
        return config.model_dump(mode="python")
    return LegacyConfigSchema(**dict(config)).model_dump(mode="python")


def _manifest_hash(value: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError("document_manifest_hash must be exactly 64 hexadecimal characters")
    return value.lower()


def canonical_index_definition(
    config: LegacyConfigSchema | Mapping[str, Any],
    document_manifest_hash: str,
    *,
    index_schema_version: str = INDEX_SCHEMA_VERSION,
) -> dict[str, Any]:
    """Return the stable, allowlisted definition used for hashing."""

    if not index_schema_version.strip():
        raise ValueError("index_schema_version must be non-empty")
    values = _validated_values(config)
    return {
        "document_manifest_sha256": _manifest_hash(document_manifest_hash),
        "index_schema_version": index_schema_version,
        "settings": {field: values[field] for field in INDEX_DEFINING_FIELDS},
    }


def index_fingerprint(
    config: LegacyConfigSchema | Mapping[str, Any],
    document_manifest_hash: str,
    *,
    index_schema_version: str = INDEX_SCHEMA_VERSION,
) -> str:
    """Hash a canonical UTF-8 JSON index definition with SHA-256."""

    payload = canonical_index_definition(
        config,
        document_manifest_hash,
        index_schema_version=index_schema_version,
    )
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
