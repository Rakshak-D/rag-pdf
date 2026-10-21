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
from .image import (
    ImageEmbeddingAdapter,
    ImageEmbeddingBatchResult,
    ImageEmbeddingError,
    ImageEmbeddingInferenceError,
    ImageEmbeddingInputError,
    ImageEmbeddingIssue,
    ImageEmbeddingLoadError,
    ImageEmbeddingOutputError,
    ImageModelComponents,
)

__all__ = [
    "EmbeddingBatchResult",
    "TextEmbeddingAdapter",
    "TextEmbeddingError",
    "TextEmbeddingInferenceError",
    "TextEmbeddingInputError",
    "TextEmbeddingLoadError",
    "TextEmbeddingOutputError",
    "ImageEmbeddingAdapter",
    "ImageEmbeddingBatchResult",
    "ImageEmbeddingError",
    "ImageEmbeddingInferenceError",
    "ImageEmbeddingInputError",
    "ImageEmbeddingIssue",
    "ImageEmbeddingLoadError",
    "ImageEmbeddingOutputError",
    "ImageModelComponents",
]
