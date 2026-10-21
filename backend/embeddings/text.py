"""Lazy SentenceTransformers text embeddings for layout-aware chunks."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable

import numpy as np

from backend.config.schema import ResolvedRunConfig


ModelLoader = Callable[[str, str], Any]
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class TextEmbeddingError(RuntimeError):
    """Base exception for text embedding adapter failures."""


class TextEmbeddingInputError(TextEmbeddingError, ValueError):
    """Raised when caller-provided documents or text are invalid."""


class TextEmbeddingLoadError(TextEmbeddingError):
    """Raised when the configured SentenceTransformer cannot be loaded."""


class TextEmbeddingInferenceError(TextEmbeddingError):
    """Raised when the loaded model fails during encoding."""


class TextEmbeddingOutputError(TextEmbeddingError):
    """Raised when a model returns an invalid embedding shape or value."""


@dataclass(frozen=True)
class EmbeddingBatchResult:
    """Validated document vectors and timing information for one batch."""

    embeddings: np.ndarray
    elapsed_time: float
    input_count: int
    embeddings_created: int
    dimension: int
    average_time: float


def _sentence_transformer_loader(model_name: str, device: str) -> Any:
    """Load SentenceTransformer only at the explicit model-loading boundary."""

    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name_or_path=model_name, device=device)


def _default_device() -> str:
    """Choose CUDA or CPU without importing torch during module import."""

    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def _validate_numeric_array(value: Any, *, label: str, dimensions: int) -> np.ndarray:
    try:
        array = np.asarray(value)
    except Exception as exc:
        raise TextEmbeddingOutputError(f"{label} could not be converted to a numeric array") from exc
    if array.ndim != dimensions:
        raise TextEmbeddingOutputError(
            f"{label} must be {dimensions}-dimensional; got shape {array.shape}"
        )
    if any(size == 0 for size in array.shape):
        raise TextEmbeddingOutputError(f"{label} must have nonzero dimensions; got shape {array.shape}")
    if not np.issubdtype(array.dtype, np.number):
        raise TextEmbeddingOutputError(f"{label} must contain numeric values")
    try:
        finite = np.isfinite(array).all()
    except TypeError as exc:
        raise TextEmbeddingOutputError(f"{label} must contain finite numeric values") from exc
    if not bool(finite):
        raise TextEmbeddingOutputError(f"{label} must contain only finite numeric values")
    return array


class TextEmbeddingAdapter:
    """Lazily load the configured text model and encode research Documents.

    Construction and import are side-effect-free.  The injected ``model_loader``
    receives ``(model_name, device)`` and is intended for deterministic tests;
    the default loader imports SentenceTransformers only when ``load`` or an
    embedding operation is first called.
    """

    def __init__(
        self,
        config: ResolvedRunConfig,
        *,
        model_loader: ModelLoader | None = None,
    ) -> None:
        if not isinstance(config, ResolvedRunConfig):
            raise TypeError("TextEmbeddingAdapter requires ResolvedRunConfig")
        try:
            self.config = ResolvedRunConfig.model_validate(config.model_dump(mode="python"))
        except Exception as exc:
            raise TextEmbeddingInputError("text embedding configuration failed validation") from exc
        self._model_loader = model_loader or _sentence_transformer_loader
        self._model: Any | None = None
        self._device: str | None = None

    @property
    def model_name(self) -> str:
        return self.config.text_embed_model

    @property
    def device(self) -> str | None:
        return self._device

    def load(self) -> Any:
        """Load the configured model once and return it."""

        if self._model is not None:
            return self._model
        try:
            device = _default_device()
            model = self._model_loader(self.model_name, device)
        except Exception as exc:
            raise TextEmbeddingLoadError(
                f"failed to load text embedding model {self.model_name!r}"
            ) from exc
        if model is None or not callable(getattr(model, "encode", None)):
            raise TextEmbeddingLoadError(
                f"loaded text embedding model {self.model_name!r} has no callable encode method"
            )
        self._device = device
        self._model = model
        return model

    @staticmethod
    def _document_texts(documents: Any) -> list[str]:
        if isinstance(documents, (str, bytes, bytearray)):
            raise TextEmbeddingInputError("documents must be a non-empty sequence")
        try:
            values = list(documents)
        except TypeError as exc:
            raise TextEmbeddingInputError("documents must be a non-empty sequence") from exc
        if not values:
            raise TextEmbeddingInputError("documents must not be empty")
        texts: list[str] = []
        for index, document in enumerate(values):
            if not hasattr(document, "page_content"):
                raise TextEmbeddingInputError(
                    f"document at index {index} is missing page_content"
                )
            content = getattr(document, "page_content")
            if not isinstance(content, str):
                raise TextEmbeddingInputError(
                    f"document at index {index} page_content must be a string"
                )
            trimmed = content.strip()
            if not trimmed:
                raise TextEmbeddingInputError(
                    f"document at index {index} has empty page_content"
                )
            texts.append(trimmed)
        return texts

    @staticmethod
    def _text_input(text: Any, *, label: str) -> str:
        if not isinstance(text, str):
            raise TextEmbeddingInputError(f"{label} must be a string")
        trimmed = text.strip()
        if not trimmed:
            raise TextEmbeddingInputError(f"{label} must not be empty")
        return trimmed

    def embed_documents(self, documents: Any) -> EmbeddingBatchResult:
        """Encode non-empty Document-like objects in input order."""

        texts = self._document_texts(documents)
        model = self.load()
        started = perf_counter()
        try:
            raw = model.encode(
                sentences=texts,
                batch_size=self.config.text_embed_batch_size,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        except Exception as exc:
            raise TextEmbeddingInferenceError(
                f"text embedding inference failed for model {self.model_name!r}"
            ) from exc
        elapsed = max(0.0, perf_counter() - started)
        array = _validate_numeric_array(raw, label="document embeddings", dimensions=2)
        if array.shape[0] != len(texts):
            raise TextEmbeddingOutputError(
                f"document embedding row count {array.shape[0]} does not match input count {len(texts)}"
            )
        count = len(texts)
        return EmbeddingBatchResult(
            embeddings=array,
            elapsed_time=elapsed,
            input_count=count,
            embeddings_created=int(array.shape[0]),
            dimension=int(array.shape[1]),
            average_time=elapsed / count if count else 0.0,
        )

    def encode_text(self, text: str, use_query_prefix: bool = False) -> np.ndarray:
        """Encode one string, optionally applying the research BGE prefix."""

        trimmed = self._text_input(text, label="text")
        model_input = trimmed
        if use_query_prefix and "bge" in self.model_name.lower():
            model_input = f"{BGE_QUERY_PREFIX}{trimmed}"
        model = self.load()
        try:
            raw = model.encode(
                sentences=model_input,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        except Exception as exc:
            raise TextEmbeddingInferenceError(
                f"text embedding inference failed for model {self.model_name!r}"
            ) from exc
        return _validate_numeric_array(raw, label="query embedding", dimensions=1)

    def embed_query(self, query: str) -> np.ndarray:
        """Encode a query using the configured model's query semantics."""

        return self.encode_text(query, use_query_prefix=True)
