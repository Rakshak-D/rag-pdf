"""Lazy embedding adapters for the local workbench."""

from .text import (
    EmbeddingBatchResult,
    TextEmbeddingAdapter,
    TextEmbeddingError,
    TextEmbeddingInferenceError,
    TextEmbeddingInputError,
    TextEmbeddingLoadError,
    TextEmbeddingOutputError,
)

__all__ = [
    "EmbeddingBatchResult",
    "TextEmbeddingAdapter",
    "TextEmbeddingError",
    "TextEmbeddingInferenceError",
    "TextEmbeddingInputError",
    "TextEmbeddingLoadError",
    "TextEmbeddingOutputError",
]
