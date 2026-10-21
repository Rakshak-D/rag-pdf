"""Typed configuration primitives for the future local workbench.

This package deliberately imports the lightweight legacy configuration module
only.  It does not import or execute ``notebooks.rag_pipeline``.
"""

from .fingerprint import (
    INDEX_SCHEMA_VERSION,
    INDEX_DEFINING_FIELDS,
    RUN_CONFIG_EXCLUDED_FIELDS,
    RUN_CONFIG_FIELDS,
    canonical_index_definition,
    index_fingerprint,
    run_configuration_definition,
    run_configuration_fingerprint,
)
from .paths import PathContainmentError, resolve_owned_path
from .schema import (
    LegacyConfigSchema,
    ResolvedRunConfig,
    WorkbenchSafetyPolicy,
)

__all__ = [
    "INDEX_SCHEMA_VERSION",
    "INDEX_DEFINING_FIELDS",
    "RUN_CONFIG_EXCLUDED_FIELDS",
    "RUN_CONFIG_FIELDS",
    "LegacyConfigSchema",
    "PathContainmentError",
    "ResolvedRunConfig",
    "WorkbenchSafetyPolicy",
    "canonical_index_definition",
    "index_fingerprint",
    "run_configuration_definition",
    "run_configuration_fingerprint",
    "resolve_owned_path",
]
