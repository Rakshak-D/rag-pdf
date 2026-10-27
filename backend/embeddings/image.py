"""Lazy OpenCLIP image and caption embeddings for extracted PDF images."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
import threading
from typing import Any

import numpy as np

from backend.config.schema import ResolvedRunConfig


@dataclass(frozen=True)
class ImageModelComponents:
    """Loaded OpenCLIP components supplied by the default or test loader."""

    model: Any
    preprocess: Callable[[Any], Any]
    tokenizer: Callable[[list[str]], Any]
    dimension: int


ImageModelLoader = Callable[[str, str, str], ImageModelComponents]


class ImageEmbeddingError(RuntimeError):
    """Base exception for image embedding adapter failures."""


class ImageEmbeddingInputError(ImageEmbeddingError, ValueError):
    """Raised when top-level image or query inputs are invalid."""


class ImageEmbeddingLoadError(ImageEmbeddingError):
    """Raised when the configured OpenCLIP model cannot be loaded."""


class ImageEmbeddingInferenceError(ImageEmbeddingError):
    """Raised when OpenCLIP inference fails for a valid batch or query."""


class ImageEmbeddingOutputError(ImageEmbeddingError):
    """Raised when OpenCLIP returns invalid dimensions or numeric values."""


@dataclass(frozen=True)
class ImageEmbeddingIssue:
    """A UI-safe issue for one image load or caption operation."""

    identifier: str
    kind: str
    message: str
    cause: BaseException | None = None


@dataclass(frozen=True)
class ImageEmbeddingBatchResult:
    """Successful image vectors aligned with the returned image objects."""

    embeddings: np.ndarray
    image_objects: tuple[Mapping[str, Any], ...]
    elapsed_time: float
    input_count: int
    embeddings_created: int
    dimension: int
    average_time: float
    failed_loads: int
    caption_fallbacks: int
    issues: tuple[ImageEmbeddingIssue, ...]


def _default_loader(model_name: str, pretrained: str, device: str) -> ImageModelComponents:
    """Load OpenCLIP only inside the explicit adapter loading boundary."""

    import open_clip

    model, preprocess, _ = open_clip.create_model_and_transforms(
        model_name=model_name,
        pretrained=pretrained,
    )
    tokenizer = open_clip.get_tokenizer(model_name)
    model = model.to(device)
    model.eval()
    model_config = open_clip.get_model_config(model_name)
    dimension = int(model_config["embed_dim"])
    return ImageModelComponents(model, preprocess, tokenizer, dimension)


def _default_device() -> str:
    """Select CUDA or CPU without importing torch during module import."""

    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def _identifier(image_object: Mapping[str, Any]) -> str:
    image_id = image_object.get("image_id")
    if isinstance(image_id, str) and image_id.strip():
        return image_id
    path = image_object.get("path")
    if isinstance(path, str) and path.strip():
        return Path(path).name or "<unnamed-image>"
    return "<unknown-image>"


def _as_torch_tensor(value: Any, *, label: str) -> Any:
    import torch

    try:
        tensor = value if isinstance(value, torch.Tensor) else torch.as_tensor(value)
    except Exception as exc:
        raise ImageEmbeddingOutputError(f"{label} is not a numeric tensor") from exc
    if not tensor.is_floating_point() and not tensor.is_complex():
        tensor = tensor.float()
    return tensor


def _normalize_rows(value: Any, *, label: str, expected_rows: int, dimension: int) -> np.ndarray:
    import torch

    tensor = _as_torch_tensor(value, label=label)
    if tensor.ndim != 2:
        raise ImageEmbeddingOutputError(
            f"{label} must be 2-dimensional; got shape {tuple(tensor.shape)}"
        )
    if tensor.shape[0] != expected_rows:
        raise ImageEmbeddingOutputError(
            f"{label} row count {tensor.shape[0]} does not match expected {expected_rows}"
        )
    if tensor.shape[1] != dimension or tensor.shape[1] == 0:
        raise ImageEmbeddingOutputError(
            f"{label} dimension {tensor.shape[1]} does not match expected nonzero dimension {dimension}"
        )
    if not bool(torch.isfinite(tensor).all()):
        raise ImageEmbeddingOutputError(f"{label} must contain only finite numeric values")
    norms = tensor.norm(dim=-1, keepdim=True)
    if not bool(torch.isfinite(norms).all()) or bool((norms <= 0).any()):
        raise ImageEmbeddingOutputError(f"{label} contains a zero-norm or invalid vector")
    normalized = tensor / norms
    result = normalized.detach().cpu().numpy()
    if not np.isfinite(result).all():
        raise ImageEmbeddingOutputError(f"{label} normalization produced invalid values")
    return result


class ImageEmbeddingAdapter:
    """Lazily load OpenCLIP and embed extracted image-object mappings."""

    def __init__(
        self,
        config: ResolvedRunConfig,
        *,
        model_loader: ImageModelLoader | None = None,
    ) -> None:
        if not isinstance(config, ResolvedRunConfig):
            raise TypeError("ImageEmbeddingAdapter requires ResolvedRunConfig")
        try:
            self.config = ResolvedRunConfig.model_validate(config.model_dump(mode="python"))
        except Exception as exc:
            raise ImageEmbeddingInputError("image embedding configuration failed validation") from exc
        self._model_loader = model_loader or _default_loader
        self._components: ImageModelComponents | None = None
        self._device: str | None = None
        self._load_lock = threading.Lock()
        self._inference_lock = threading.Lock()
        self._state = "unloaded"
        self._error_code: str | None = None

    @property
    def model_name(self) -> str:
        return self.config.image_embed_model

    @property
    def pretrained(self) -> str:
        return self.config.image_embed_pretrained

    @property
    def device(self) -> str | None:
        return self._device

    @property
    def dimension(self) -> int:
        return self._components.dimension if self._components is not None else 0

    @property
    def runtime_state(self) -> str:
        return self._state

    @property
    def runtime_error_code(self) -> str | None:
        return self._error_code

    def load(self) -> ImageModelComponents:
        """Load and validate OpenCLIP components exactly once."""

        if self._components is not None:
            return self._components
        with self._load_lock:
            if self._components is not None:
                return self._components
            self._state = "loading"
            try:
                device = _default_device()
                components = self._model_loader(self.model_name, self.pretrained, device)
                if not isinstance(components, ImageModelComponents) or components.dimension <= 0:
                    raise ImageEmbeddingLoadError("OpenCLIP loader returned invalid components")
                if not callable(getattr(components.model, "encode_image", None)) or not callable(getattr(components.model, "encode_text", None)):
                    raise ImageEmbeddingLoadError("OpenCLIP components have no supported encoder")
            except Exception as exc:
                self._components = None
                self._device = None
                self._state = "failed"
                self._error_code = "image_model_load_failed"
                if isinstance(exc, ImageEmbeddingLoadError):
                    raise
                raise ImageEmbeddingLoadError(f"failed to load OpenCLIP model {self.model_name!r} with pretrained {self.pretrained!r}") from exc
            self._device = device
            self._components = components
            self._state = "ready"
            self._error_code = None
            return components

    @staticmethod
    def _validate_objects(image_objects: Any) -> list[Mapping[str, Any]]:
        if isinstance(image_objects, (str, bytes, bytearray, Mapping)):
            raise ImageEmbeddingInputError("image_objects must be a sequence of mappings")
        try:
            values = list(image_objects)
        except TypeError as exc:
            raise ImageEmbeddingInputError("image_objects must be a sequence of mappings") from exc
        if any(not isinstance(value, Mapping) for value in values):
            raise ImageEmbeddingInputError("image_objects must contain only mappings")
        return values

    @staticmethod
    def _caption(value: Mapping[str, Any]) -> str:
        caption = value.get("caption_text")
        return caption.strip() if isinstance(caption, str) else ""

    def _encode_caption(self, caption: str, components: ImageModelComponents) -> np.ndarray:
        import torch

        tokens = components.tokenizer([caption])
        if callable(getattr(tokens, "to", None)):
            tokens = tokens.to(self._device)
        with self._inference_lock:
            with torch.no_grad():
                raw = components.model.encode_text(tokens)
        return _normalize_rows(
            raw,
            label="caption embeddings",
            expected_rows=1,
            dimension=components.dimension,
        )[0]

    def embed_images(self, image_objects: Any) -> ImageEmbeddingBatchResult:
        """Embed valid image objects while reporting per-item load/caption issues."""

        values = self._validate_objects(image_objects)
        input_count = len(values)
        if not values:
            return ImageEmbeddingBatchResult(
                embeddings=np.empty((0, self.dimension), dtype=np.float32),
                image_objects=(),
                elapsed_time=0.0,
                input_count=0,
                embeddings_created=0,
                dimension=self.dimension,
                average_time=0.0,
                failed_loads=0,
                caption_fallbacks=0,
                issues=(),
            )

        components = self.load()
        from PIL import Image
        import torch

        started = perf_counter()
        processed: list[Any] = []
        successful_objects: list[Mapping[str, Any]] = []
        issues: list[ImageEmbeddingIssue] = []
        for image_object in values:
            path = image_object.get("path")
            identifier = _identifier(image_object)
            if not isinstance(path, str) or not path.strip():
                issues.append(ImageEmbeddingIssue(identifier, "image_load", "image path is missing"))
                continue
            try:
                with Image.open(path) as pil_image:
                    rgb_image = pil_image.convert("RGB")
                    try:
                        processed_image = components.preprocess(rgb_image)
                    finally:
                        rgb_image.close()
            except Exception as exc:
                issues.append(
                    ImageEmbeddingIssue(
                        identifier,
                        "image_load",
                        "image could not be opened or preprocessed",
                        exc,
                    )
                )
                continue
            processed.append(processed_image)
            successful_objects.append(dict(image_object))

        if not processed:
            elapsed = max(0.0, perf_counter() - started)
            return ImageEmbeddingBatchResult(
                embeddings=np.empty((0, components.dimension), dtype=np.float32),
                image_objects=(),
                elapsed_time=elapsed,
                input_count=input_count,
                embeddings_created=0,
                dimension=components.dimension,
                average_time=0.0,
                failed_loads=sum(issue.kind == "image_load" for issue in issues),
                caption_fallbacks=0,
                issues=tuple(issues),
            )

        image_embeddings: list[np.ndarray] = []
        for batch_start in range(0, len(processed), self.config.image_embed_batch_size):
            batch = processed[batch_start: batch_start + self.config.image_embed_batch_size]
            try:
                batch_tensor = torch.stack(batch).to(self._device)
                with self._inference_lock:
                    with torch.no_grad():
                        raw = components.model.encode_image(batch_tensor)
                batch_vectors = _normalize_rows(
                    raw,
                    label="image embeddings",
                    expected_rows=len(batch),
                    dimension=components.dimension,
                )
            except ImageEmbeddingOutputError:
                raise
            except Exception as exc:
                raise ImageEmbeddingInferenceError(
                    f"image embedding inference failed for model {self.model_name!r}"
                ) from exc
            image_embeddings.extend(batch_vectors)

        final_embeddings: list[np.ndarray] = []
        caption_fallbacks = 0
        image_weight = float(self.config.image_caption_image_weight)
        for image_object, image_embedding in zip(successful_objects, image_embeddings):
            caption = self._caption(image_object)
            if not caption:
                final_embeddings.append(image_embedding)
                continue
            try:
                caption_embedding = self._encode_caption(caption, components)
                fused = image_weight * image_embedding + (1.0 - image_weight) * caption_embedding
                if not np.isfinite(fused).all():
                    raise ImageEmbeddingOutputError("caption fusion produced invalid values")
                final_embeddings.append(fused)
            except Exception as exc:
                caption_fallbacks += 1
                issues.append(
                    ImageEmbeddingIssue(
                        _identifier(image_object),
                        "caption",
                        "caption embedding failed; image embedding retained",
                        exc,
                    )
                )
                final_embeddings.append(image_embedding)

        elapsed = max(0.0, perf_counter() - started)
        result = np.vstack(final_embeddings)
        if result.shape != (len(successful_objects), components.dimension) or not np.isfinite(result).all():
            raise ImageEmbeddingOutputError("final image embeddings have invalid shape or values")
        return ImageEmbeddingBatchResult(
            embeddings=result,
            image_objects=tuple(successful_objects),
            elapsed_time=elapsed,
            input_count=input_count,
            embeddings_created=len(final_embeddings),
            dimension=components.dimension,
            average_time=elapsed / len(final_embeddings) if final_embeddings else 0.0,
            failed_loads=sum(issue.kind == "image_load" for issue in issues),
            caption_fallbacks=caption_fallbacks,
            issues=tuple(issues),
        )

    def embed_query(self, query: str) -> np.ndarray:
        """Tokenize and L2-normalize one OpenCLIP text query."""

        if not isinstance(query, str):
            raise ImageEmbeddingInputError("query must be a string")
        query = query.strip()
        if not query:
            raise ImageEmbeddingInputError("query must not be empty")
        components = self.load()
        import torch

        try:
            tokens = components.tokenizer([query])
            if callable(getattr(tokens, "to", None)):
                tokens = tokens.to(self._device)
            with self._inference_lock:
                with torch.no_grad():
                    raw = components.model.encode_text(tokens)
        except Exception as exc:
            raise ImageEmbeddingInferenceError(
                f"image query embedding inference failed for model {self.model_name!r}"
            ) from exc
        return _normalize_rows(
            raw,
            label="image query embedding",
            expected_rows=1,
            dimension=components.dimension,
        )[0]
