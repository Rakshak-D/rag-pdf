"""Thread-safe, lazy cache for the workbench's model-backed adapters."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import re
import threading
from typing import Callable

from backend.config.schema import ResolvedRunConfig
from backend.embeddings.image import ImageEmbeddingAdapter
from backend.embeddings.text import TextEmbeddingAdapter
from backend.engine.reranking import CrossEncoderReranker


_KINDS = {"text", "image", "reranker"}
_STATES = {"unloaded", "loading", "ready", "failed"}
_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class ModelRuntimeError(RuntimeError):
    """A bounded, stable registry error without loader details."""

    def __init__(self, error_code: str) -> None:
        self.error_code = error_code if isinstance(error_code, str) and _CODE_RE.fullmatch(error_code) else "model_registry_error"
        super().__init__(self.error_code)


def _safe_identifier(value: str) -> str:
    """Keep status output free of local model-cache paths."""
    candidate = value.strip()
    if not candidate or Path(candidate).is_absolute() or "\\" in candidate or re.match(r"^[A-Za-z]:", candidate):
        return "configured_model"
    return candidate[:160]


@dataclass(frozen=True)
class ModelComponentStatus:
    component_kind: str
    model_identifier: str
    state: str
    error_code: str | None = None
    explicitly_warmed: bool = False

    def __post_init__(self) -> None:
        if self.component_kind not in _KINDS or not isinstance(self.model_identifier, str) or not self.model_identifier or self.state not in _STATES:
            raise ModelRuntimeError("runtime_status_invalid")
        if self.error_code is not None and not _CODE_RE.fullmatch(self.error_code):
            raise ModelRuntimeError("runtime_status_invalid")
        if not isinstance(self.explicitly_warmed, bool):
            raise ModelRuntimeError("runtime_status_invalid")


@dataclass(frozen=True)
class ModelRuntimeSnapshot:
    components: tuple[ModelComponentStatus, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.components, tuple) or any(not isinstance(item, ModelComponentStatus) for item in self.components):
            raise ModelRuntimeError("runtime_snapshot_invalid")
        keys = [(item.component_kind, item.model_identifier) for item in self.components]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ModelRuntimeError("runtime_snapshot_invalid")


@dataclass(frozen=True)
class ModelAdapterBundle:
    text_adapter: TextEmbeddingAdapter | None
    image_adapter: ImageEmbeddingAdapter | None
    reranker: CrossEncoderReranker | None


class ModelAdapterRegistry:
    """Cache compatible adapter wrappers without loading their models implicitly."""

    def __init__(
        self,
        *,
        text_factory: Callable[[ResolvedRunConfig], TextEmbeddingAdapter] | None = None,
        image_factory: Callable[[ResolvedRunConfig], ImageEmbeddingAdapter] | None = None,
        reranker_factory: Callable[[ResolvedRunConfig], CrossEncoderReranker] | None = None,
    ) -> None:
        self._text_factory = text_factory or TextEmbeddingAdapter
        self._image_factory = image_factory or ImageEmbeddingAdapter
        self._reranker_factory = reranker_factory or CrossEncoderReranker
        if not all(callable(factory) for factory in (self._text_factory, self._image_factory, self._reranker_factory)):
            raise ModelRuntimeError("adapter_factory_invalid")
        self._lock = threading.RLock()
        self._text: dict[tuple[str, int], TextEmbeddingAdapter] = {}
        self._image: dict[tuple[str, str, int, float], ImageEmbeddingAdapter] = {}
        self._reranker: dict[tuple[str, float, float, float], CrossEncoderReranker] = {}
        self._warmed: set[tuple[str, object]] = set()

    @staticmethod
    def _config(config: ResolvedRunConfig) -> ResolvedRunConfig:
        if not isinstance(config, ResolvedRunConfig):
            raise ModelRuntimeError("configuration_invalid")
        try:
            return ResolvedRunConfig.model_validate(config.model_dump(mode="python"))
        except Exception as exc:
            raise ModelRuntimeError("configuration_invalid") from exc

    def get_text_adapter(self, config: ResolvedRunConfig) -> TextEmbeddingAdapter:
        config = self._config(config)
        key = (config.text_embed_model, config.text_embed_batch_size)
        with self._lock:
            adapter = self._text.get(key)
            if adapter is None:
                try:
                    adapter = self._text_factory(config)
                except Exception as exc:
                    raise ModelRuntimeError("text_adapter_creation_failed") from exc
                if not isinstance(adapter, TextEmbeddingAdapter) and not all(hasattr(adapter, name) for name in ("load", "embed_query", "encode_text")):
                    raise ModelRuntimeError("text_adapter_invalid")
                self._text[key] = adapter
            return adapter

    def get_image_adapter(self, config: ResolvedRunConfig) -> ImageEmbeddingAdapter:
        config = self._config(config)
        key = (config.image_embed_model, config.image_embed_pretrained, config.image_embed_batch_size, float(config.image_caption_image_weight))
        with self._lock:
            adapter = self._image.get(key)
            if adapter is None:
                try:
                    adapter = self._image_factory(config)
                except Exception as exc:
                    raise ModelRuntimeError("image_adapter_creation_failed") from exc
                if not isinstance(adapter, ImageEmbeddingAdapter) and not all(hasattr(adapter, name) for name in ("load", "embed_query")):
                    raise ModelRuntimeError("image_adapter_invalid")
                self._image[key] = adapter
            return adapter

    def get_reranker(self, config: ResolvedRunConfig) -> CrossEncoderReranker:
        config = self._config(config)
        key = (config.reranker_model, float(config.reranker_ce_weight), float(config.reranker_fused_weight), float(config.reranker_boost_weight))
        if any(not math.isfinite(value) for value in key[1:]):
            raise ModelRuntimeError("configuration_invalid")
        with self._lock:
            adapter = self._reranker.get(key)
            if adapter is None:
                try:
                    adapter = self._reranker_factory(config)
                except Exception as exc:
                    raise ModelRuntimeError("reranker_creation_failed") from exc
                if not isinstance(adapter, CrossEncoderReranker) and not all(hasattr(adapter, name) for name in ("load", "score_pairs")):
                    raise ModelRuntimeError("reranker_invalid")
                self._reranker[key] = adapter
            return adapter

    def get_bundle(self, config: ResolvedRunConfig) -> ModelAdapterBundle:
        config = self._config(config)
        return ModelAdapterBundle(
            self.get_text_adapter(config),
            self.get_image_adapter(config),
            self.get_reranker(config) if config.use_reranker else None,
        )

    @staticmethod
    def _component_name(value: object) -> str:
        if not isinstance(value, str) or value not in _KINDS:
            raise ModelRuntimeError("component_invalid")
        return value

    def _adapter_for(self, config: ResolvedRunConfig, component: str):
        if component == "text":
            return self.get_text_adapter(config)
        if component == "image":
            return self.get_image_adapter(config)
        return self.get_reranker(config)

    def preload(self, config: ResolvedRunConfig, *, components: tuple[str, ...]) -> ModelRuntimeSnapshot:
        config = self._config(config)
        if not isinstance(components, tuple) or not components or len(set(components)) != len(components):
            raise ModelRuntimeError("components_invalid")
        names = tuple(self._component_name(value) for value in components)
        for name in names:
            adapter = self._adapter_for(config, name)
            try:
                adapter.load()
            except Exception as exc:
                raise ModelRuntimeError(f"{name}_model_load_failed") from exc
            with self._lock:
                self._warmed.add((name, self._key_for(config, name)))
        return self.snapshot()

    @staticmethod
    def _key_for(config: ResolvedRunConfig, component: str):
        if component == "text":
            return (config.text_embed_model, config.text_embed_batch_size)
        if component == "image":
            return (config.image_embed_model, config.image_embed_pretrained, config.image_embed_batch_size, float(config.image_caption_image_weight))
        return (config.reranker_model, float(config.reranker_ce_weight), float(config.reranker_fused_weight), float(config.reranker_boost_weight))

    def snapshot(self) -> ModelRuntimeSnapshot:
        with self._lock:
            records: list[ModelComponentStatus] = []
            for kind, values in (("text", self._text.items()), ("image", self._image.items()), ("reranker", self._reranker.items())):
                for key, adapter in values:
                    identifier = _safe_identifier(getattr(adapter, "model_name", "configured_model"))
                    if kind == "image":
                        identifier = f"{identifier}|{_safe_identifier(getattr(adapter, 'pretrained', 'configured_pretrained'))}"
                    existing = {(item.component_kind, item.model_identifier) for item in records}
                    if (kind, identifier) in existing:
                        suffix = hashlib.sha256(repr(key).encode("utf-8")).hexdigest()[:12]
                        identifier = f"{identifier}|{suffix}"
                    records.append(ModelComponentStatus(kind, identifier, getattr(adapter, "runtime_state", "unloaded"), getattr(adapter, "runtime_error_code", None), (kind, key) in self._warmed))
            records.sort(key=lambda item: (item.component_kind, item.model_identifier))
            return ModelRuntimeSnapshot(tuple(records))


__all__ = ["ModelAdapterRegistry", "ModelRuntimeSnapshot", "ModelComponentStatus", "ModelRuntimeError", "ModelAdapterBundle"]
