"""Audited retrieval-similarity measurements for supported retrieval modes."""

from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Real
from typing import Literal

from backend.engine.retrieval import HybridRetrievalResult


SimilarityStatus = Literal["computed", "skipped", "failed", "unavailable"]


@dataclass(frozen=True)
class RetrievalSimilarityResult:
    status: SimilarityStatus
    value: float | None = None
    reason_code: str | None = None
    metric_name: str = "Retrieval Similarity"
    signal: str = ""
    modality_means: tuple[tuple[str, float], ...] = ()


class RetrievalSimilarityMetricAdapter:
    """Select and average only score fields supplied by hybrid retrieval."""

    @staticmethod
    def _mode_info(mode: object) -> tuple[str, str] | None:
        return {
            "semantic": ("Cosine Similarity", "semantic_similarity"),
            "bm25": ("BM25 Relevance", "bm25_score"),
            "hybrid": ("Fused Relevance", "fused_score"),
        }.get(mode)

    @staticmethod
    def score(result: HybridRetrievalResult | None, mode: object) -> RetrievalSimilarityResult:
        info = RetrievalSimilarityMetricAdapter._mode_info(mode)
        if info is None:
            return RetrievalSimilarityResult("unavailable", reason_code="unsupported_retrieval_mode")
        metric_name, field_name = info
        if result is None:
            return RetrievalSimilarityResult("failed", reason_code="retrieval_failed", metric_name=metric_name, signal=field_name)
        means: list[tuple[str, float]] = []
        invalid = False
        for modality, hits in (("text", result.text_hits), ("image", result.image_hits)):
            if modality not in result.modalities:
                continue
            values: list[float] = []
            for hit in hits:
                value = getattr(hit, field_name, None)
                if value is None:
                    continue
                if isinstance(value, bool) or not isinstance(value, Real):
                    invalid = True
                    continue
                try:
                    numeric = float(value)
                except (TypeError, ValueError, OverflowError):
                    invalid = True
                    continue
                if not math.isfinite(numeric):
                    invalid = True
                    continue
                values.append(numeric)
            if values:
                means.append((modality, sum(values) / len(values)))
        if invalid:
            return RetrievalSimilarityResult("failed", reason_code="invalid_retrieval_score", metric_name=metric_name, signal=field_name, modality_means=tuple(means))
        if not means:
            return RetrievalSimilarityResult("skipped", reason_code="no_usable_retrieval_scores", metric_name=metric_name, signal=field_name)
        value = sum(mean for _, mean in means) / len(means)
        if not math.isfinite(value):
            return RetrievalSimilarityResult("failed", reason_code="invalid_retrieval_score", metric_name=metric_name, signal=field_name, modality_means=tuple(means))
        return RetrievalSimilarityResult("computed", value, metric_name=metric_name, signal=field_name, modality_means=tuple(means))
