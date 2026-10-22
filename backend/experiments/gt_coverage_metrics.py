"""Audited semantic/lexical ground-truth coverage metric (M15)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
import math
import re
from numbers import Real
from typing import Any, Literal

from backend.experiments.faithfulness_metrics import FALLBACK_STOPWORDS


MetricStatus = Literal["computed", "skipped", "failed", "unavailable"]


@dataclass(frozen=True)
class GtCoverageResult:
    status: MetricStatus
    value: float | None = None
    reason_code: str | None = None
    model_identifier: str | None = None


def _regex_sentences(text: str) -> list[str]:
    return [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", text) if sentence.strip()]


def _local_sentences(text: str) -> list[str]:
    try:
        import nltk

        try:
            sentences = nltk.sent_tokenize(text)
        except Exception:
            sentences = _regex_sentences(text)
    except Exception:
        sentences = _regex_sentences(text)
    return [sentence.strip() for sentence in sentences if isinstance(sentence, str) and sentence.strip()]


def _local_stopwords() -> set[str]:
    try:
        from nltk.corpus import stopwords

        return set(stopwords.words("english"))
    except Exception:
        return set(FALLBACK_STOPWORDS)


def _vector(value: Any) -> list[float] | None:
    try:
        if getattr(value, "ndim", 1) != 1 or len(value) == 0:
            return None
        output: list[float] = []
        for item in value:
            if hasattr(item, "item"):
                item = item.item()
            if isinstance(item, bool) or not isinstance(item, Real):
                return None
            numeric = float(item)
            if not math.isfinite(numeric):
                return None
            output.append(numeric)
        return output or None
    except Exception:
        return None


class GtCoverageMetricAdapter:
    """Compute M15 with semantic sentence coverage or lexical fallback."""

    def __init__(self, embedder: Any | None = None, *, stopwords: Iterable[str] | None = None, stopword_loader: Callable[[], Iterable[str]] | None = None, sentence_splitter: Callable[[str], Iterable[str]] | None = None, similarity_threshold: float = 0.60) -> None:
        if not isinstance(similarity_threshold, (int, float)) or isinstance(similarity_threshold, bool) or not math.isfinite(float(similarity_threshold)) or not 0.0 <= float(similarity_threshold) <= 1.0:
            raise ValueError("similarity_threshold must be finite and within [0, 1]")
        self._embedder = embedder
        self._stopwords = set(stopwords) if stopwords is not None else None
        self._stopword_loader = stopword_loader or _local_stopwords
        self._sentence_splitter = sentence_splitter or _local_sentences
        self.similarity_threshold = float(similarity_threshold)

    @property
    def model_identifier(self) -> str | None:
        value = getattr(self._embedder, "model_name", None)
        if not isinstance(value, str) or not value.strip() or len(value) > 160 or "/" in value or "\\" in value:
            return None
        return value

    @property
    def stopwords(self) -> set[str]:
        if self._stopwords is None:
            self._stopwords = set(self._stopword_loader())
        return self._stopwords

    def _sentences(self, text: str) -> list[str] | None:
        try:
            values = list(self._sentence_splitter(text))
            if any(not isinstance(value, str) for value in values):
                return None
            return [value.strip() for value in values if value.strip()]
        except Exception:
            return None

    def _semantic(self, reference: str, response: str) -> GtCoverageResult:
        reference_sentences = self._sentences(reference)
        response_sentences = self._sentences(response)
        if reference_sentences is None or response_sentences is None:
            return GtCoverageResult("failed", reason_code="gt_coverage_sentence_split_failed", model_identifier=self.model_identifier)
        if not reference_sentences or not response_sentences:
            return GtCoverageResult("skipped", reason_code="empty_sentences", model_identifier=self.model_identifier)
        if not callable(getattr(self._embedder, "encode_text", None)):
            return GtCoverageResult("failed", reason_code="gt_coverage_embedder_invalid", model_identifier=self.model_identifier)
        try:
            reference_vectors = [_vector(self._embedder.encode_text(sentence)) for sentence in reference_sentences]
            response_vectors = [_vector(self._embedder.encode_text(sentence)) for sentence in response_sentences]
            if any(vector is None for vector in reference_vectors + response_vectors):
                return GtCoverageResult("failed", reason_code="gt_coverage_invalid_embedding", model_identifier=self.model_identifier)
            dimensions = {len(vector) for vector in reference_vectors + response_vectors if vector is not None}
            if len(dimensions) != 1:
                return GtCoverageResult("failed", reason_code="gt_coverage_embedding_dimension_mismatch", model_identifier=self.model_identifier)
            covered = 0
            for reference_vector in reference_vectors:
                reference_norm = math.sqrt(sum(value * value for value in reference_vector))
                if reference_norm == 0.0:
                    return GtCoverageResult("failed", reason_code="gt_coverage_zero_embedding", model_identifier=self.model_identifier)
                maximum = -1.0
                for response_vector in response_vectors:
                    response_norm = math.sqrt(sum(value * value for value in response_vector))
                    if response_norm == 0.0:
                        return GtCoverageResult("failed", reason_code="gt_coverage_zero_embedding", model_identifier=self.model_identifier)
                    similarity = sum(left * right for left, right in zip(reference_vector, response_vector)) / (reference_norm * response_norm)
                    if not math.isfinite(similarity) or similarity < -1.0 or similarity > 1.0:
                        return GtCoverageResult("failed", reason_code="gt_coverage_invalid_similarity", model_identifier=self.model_identifier)
                    maximum = max(maximum, similarity)
                if maximum >= self.similarity_threshold:
                    covered += 1
            score = (covered / len(reference_vectors)) * 100.0
            if not math.isfinite(score) or not 0.0 <= score <= 100.0:
                return GtCoverageResult("failed", reason_code="gt_coverage_invalid_score", model_identifier=self.model_identifier)
            return GtCoverageResult("computed", value=score, model_identifier=self.model_identifier)
        except Exception:
            return GtCoverageResult("failed", reason_code="gt_coverage_failed", model_identifier=self.model_identifier)

    def _lexical(self, reference: str, response: str) -> GtCoverageResult:
        try:
            reference_tokens = [token for token in re.findall(r"\b[A-Za-z]{3,}\b", reference.lower()) if token not in self.stopwords]
            response_tokens = [token for token in re.findall(r"\b[A-Za-z]{3,}\b", response.lower()) if token not in self.stopwords]
            if not reference_tokens:
                return GtCoverageResult("computed", value=0.0)
            score = 100.0 * sum((Counter(reference_tokens) & Counter(response_tokens)).values()) / len(reference_tokens)
            if not math.isfinite(score) or not 0.0 <= score <= 100.0:
                return GtCoverageResult("failed", reason_code="gt_coverage_invalid_score")
            return GtCoverageResult("computed", value=score)
        except Exception:
            return GtCoverageResult("failed", reason_code="gt_coverage_failed")

    def score(self, reference: str, response: str, reference_kind: str) -> GtCoverageResult:
        if not isinstance(reference, str) or not isinstance(response, str) or not reference.strip() or not response.strip():
            return GtCoverageResult("skipped", reason_code="missing_text", model_identifier=self.model_identifier if self._embedder is not None else None)
        return self._semantic(reference.strip(), response.strip()) if self._embedder is not None else self._lexical(reference.strip(), response.strip())
