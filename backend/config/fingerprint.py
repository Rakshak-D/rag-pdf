"""Canonical fingerprints for immutable index definitions."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import fields as dataclass_fields
from typing import Any

from notebooks.config import Config

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

# These are operational/output concerns, not experiment semantics.  The
# document manifest and index fingerprint already identify the indexed input;
# collection names are storage details and the reset flag is a destructive
# lifecycle control.  Workbench policy flags are intentionally not legacy
# configuration fields and are excluded by the run-plan layer.
RUN_CONFIG_EXCLUDED_FIELDS = frozenset({
    "pdf_dir",
    "images_dir",
    "database_dir",
    "results_dir",
    "model_comparison_results_dir",
    "retrieval_results_dir",
    "metrics_verbose",
    "text_collection_name",
    "image_collection_name",
    "reset_collections_on_start",
})
RUN_CONFIG_FIELDS = tuple(
    field.name
    for field in dataclass_fields(Config)
    if field.name not in RUN_CONFIG_EXCLUDED_FIELDS
)

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _validated_values(config: LegacyConfigSchema | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(config, LegacyConfigSchema):
        values = config.model_dump(mode="python")
        # Re-parse typed objects as well as mappings so model_construct-created
        # instances cannot bypass the validation boundary before hashing.
        if "workbench_policy" in values:
            from .schema import ResolvedRunConfig

            return ResolvedRunConfig.model_validate(values).model_dump(mode="python")
        return LegacyConfigSchema.model_validate(values).model_dump(mode="python")
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


def run_configuration_definition(
    config: Any,
    index_fingerprint_value: str,
    *,
    run_schema_version: str = INDEX_SCHEMA_VERSION,
) -> dict[str, Any]:
    """Return the canonical definition for a validated experiment run.

    This accepts the typed resolved schema, not arbitrary mappings.  In
    particular, a mapping serialized from ``ResolvedRunConfig`` is not
    silently reduced to legacy fields; callers must pass the validated object
    so its safety policy and complete snapshot are handled by the run-plan
    builder.
    """

    from .schema import ResolvedRunConfig

    if not isinstance(config, ResolvedRunConfig):
        raise TypeError("run configuration fingerprint requires ResolvedRunConfig")
    if not _SHA256_RE.fullmatch(index_fingerprint_value):
        raise ValueError("index_fingerprint must be exactly 64 hexadecimal characters")
    if not run_schema_version.strip():
        raise ValueError("run_schema_version must be non-empty")
    values = ResolvedRunConfig.model_validate(config.model_dump(mode="python")).model_dump(mode="python")
    return {
        "run_schema_version": run_schema_version,
        "index_fingerprint": index_fingerprint_value.lower(),
        "settings": {field: values[field] for field in RUN_CONFIG_FIELDS},
    }


def run_configuration_fingerprint(
    config: Any,
    index_fingerprint_value: str,
    *,
    run_schema_version: str = INDEX_SCHEMA_VERSION,
) -> str:
    """Hash the canonical run-configuration definition."""

    payload = run_configuration_definition(
        config,
        index_fingerprint_value,
        run_schema_version=run_schema_version,
    )
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
