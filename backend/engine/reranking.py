"""Lazy cross-encoder scoring for already retrieved text candidates."""

from __future__ import annotations

from dataclasses import replace
import math
from collections.abc import Callable, Sequence
from numbers import Real
from typing import Any

import numpy as np


class RerankerError(RuntimeError):
    """Base error for lazy cross-encoder operations."""


class RerankerInputError(RerankerError, ValueError):
    """Raised for invalid reranker inputs."""


class RerankerLoadError(RerankerError):
    """Raised when the configured cross-encoder cannot be loaded."""


class RerankerInferenceError(RerankerError):
    """Raised when a cross-encoder returns an unusable result."""


def normalize_scores(values: Sequence[float] | None, *, neutral: float = 0.5) -> list[float]:
    if values is None or len(values) == 0:
        return []
    numbers = [float(value) for value in values]
    if any(not math.isfinite(value) for value in numbers):
        raise RerankerInputError("reranker scores must be finite numbers")
    low, high = min(numbers), max(numbers)
    if high - low < 1e-6:
        return [neutral] * len(numbers)
    return [(value - low) / (high - low) for value in numbers]


class CrossEncoderReranker:
    """A lazy, injectable wrapper around ``sentence_transformers.CrossEncoder``."""

    def __init__(self, config: Any, *, loader: Callable[[str], Any] | None = None) -> None:
        self.config = config
        self.model_name = config.reranker_model
        self._loader = loader
        self._model: Any | None = None

    def load(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            if self._loader is not None:
                model = self._loader(self.model_name)
            else:
                from sentence_transformers import CrossEncoder
                model = CrossEncoder(self.model_name)
        except Exception as exc:
            raise RerankerLoadError(
                f"could not load configured reranker {self.model_name!r}"
            ) from exc
        self._model = model
        return model

    @staticmethod
    def _validate_texts(texts: Sequence[str]) -> list[str]:
        if isinstance(texts, (str, bytes, bytearray)):
            raise RerankerInputError("candidate texts must be a sequence of strings")
        values = list(texts)
        if any(not isinstance(text, str) or not text.strip() for text in values):
            raise RerankerInputError("candidate texts must be non-empty strings")
        return values

    def score_pairs(self, query: str, texts: Sequence[str]) -> list[float]:
        if not isinstance(query, str) or not query.strip():
            raise RerankerInputError("reranker query must be a non-empty string")
        values = self._validate_texts(texts)
        if not values:
            return []
        try:
            scores = self.load().predict([[query.strip(), text] for text in values])
        except RerankerError:
            raise
        except Exception as exc:
            raise RerankerInferenceError("cross-encoder inference failed") from exc
        try:
            array = np.asarray(scores)
        except (TypeError, ValueError) as exc:
            raise RerankerInferenceError("cross-encoder returned a non-numeric sequence") from exc
        if array.ndim == 2 and array.shape[1] == 1:
            array = array.reshape(-1)
        elif array.ndim != 1:
            raise RerankerInferenceError("cross-encoder returned an ambiguous score shape")
        if len(array) != len(values):
            raise RerankerInferenceError("cross-encoder returned the wrong number of scores")
        validated: list[float] = []
        for score in array.tolist():
            if isinstance(score, bool) or not isinstance(score, Real):
                raise RerankerInferenceError("cross-encoder returned a non-numeric score")
            value = float(score)
            if not math.isfinite(value):
                raise RerankerInferenceError("cross-encoder returned a non-finite score")
            validated.append(value)
        return validated

    def rerank_items(self, query: str, items: Sequence[Any], *, top_k: int) -> list[Any]:
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
            raise RerankerInputError("rerank_k must be a positive integer")
        candidates = list(items)
        if not candidates:
            return []
        texts = []
        for item in candidates:
            text = getattr(item, "document", None)
            if text is None and isinstance(item, dict):
                text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                raise RerankerInputError("each reranker candidate must have non-empty text")
            texts.append(text)
        raw = self.score_pairs(query, texts)
        ce_norm = normalize_scores(raw)
        fused = [getattr(item, "boosted_fused_score", None) if getattr(item, "boosted_fused_score", None) is not None else getattr(item, "fused_score", None) for item in candidates]
        if all(value is None for value in fused):
            fused_norm = [0.0] * len(candidates)
            has_fused = False
        else:
            values = [0.0 if value is None else float(value) for value in fused]
            fused_norm = normalize_scores(values)
            has_fused = True
        boosts = [float(getattr(item, "cross_modal_boost", 0.0) or 0.0) for item in candidates]
        boost_norm = normalize_scores(boosts, neutral=0.0) if any(value > 0 for value in boosts) else [0.0] * len(candidates)
        ranked: list[tuple[Any, float, float, float]] = []
        for index, (item, score, ce, fn, bn) in enumerate(zip(candidates, raw, ce_norm, fused_norm, boost_norm)):
            final = self.config.reranker_ce_weight * ce + self.config.reranker_fused_weight * fn + self.config.reranker_boost_weight * bn if has_fused else ce
            original_fused = float(getattr(item, "fused_score", 0.0) or 0.0)
            ranked.append((replace(item, reranker_score=score, reranker_normalized_score=ce, normalized_fused_score=fn, normalized_cross_modal_boost=bn, final_rank_score=round(float(final), 6), reranker_applied=True), float(final), original_fused, float(score)))
        ranked.sort(key=lambda entry: (-entry[1], -entry[2], -entry[3]))
        return [entry[0] for entry in ranked[:top_k]]
