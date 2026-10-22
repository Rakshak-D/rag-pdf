"""Lazy, offline NLTK adapters for the audited BLEU and METEOR metrics."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from collections.abc import Callable, Sequence
from numbers import Real
from typing import Any, Literal


MetricStatus = Literal["computed", "skipped", "failed", "unavailable"]


@dataclass(frozen=True)
class NltkMetricResult:
    status: MetricStatus
    reference_kind: str
    value: float | None = None
    reason_code: str | None = None


class NltkMetricsAdapter:
    """Compute BLEU and METEOR without downloading NLTK resources.

    Optional callables are deliberately injectable so tests can exercise the
    contract without relying on installed corpora or scoring implementations.
    """

    def __init__(
        self,
        *,
        tokenizer: Callable[[str], Sequence[str]] | None = None,
        bleu_function: Callable[..., float] | None = None,
        smoothing_factory: Callable[[], Any] | None = None,
        meteor_function: Callable[..., float] | None = None,
    ) -> None:
        self._tokenizer = tokenizer
        self._bleu_function = bleu_function
        self._smoothing_factory = smoothing_factory
        self._meteor_function = meteor_function
        self._bleu_loaded = bleu_function is not None
        self._meteor_loaded = meteor_function is not None
        self._bleu_available = True
        self._meteor_available = True

    @staticmethod
    def _fallback_tokens(text: str) -> list[str]:
        return re.findall(r"[A-Za-z0-9']+", text.lower())

    def _tokens(self, text: str) -> tuple[list[str] | None, str | None]:
        try:
            if self._tokenizer is not None:
                tokens = self._tokenizer(text.lower())
            else:
                try:
                    import nltk
                except ImportError:
                    return None, "nltk_unavailable"

                try:
                    tokens = nltk.word_tokenize(text.lower())
                except LookupError:
                    tokens = self._fallback_tokens(text)
            if not isinstance(tokens, Sequence) or isinstance(tokens, (str, bytes)):
                return None, "tokenization_failed"
            normalized = []
            for token in tokens:
                if not isinstance(token, str):
                    return None, "tokenization_failed"
                normalized.append(token)
            return normalized, None
        except LookupError:
            return self._fallback_tokens(text), None
        except Exception:
            return None, "tokenization_failed"

    def _load_bleu(self) -> bool:
        if self._bleu_loaded:
            return self._bleu_available
        self._bleu_loaded = True
        try:
            from nltk.translate.bleu_score import SmoothingFunction, sentence_bleu

            self._bleu_function = sentence_bleu
            self._smoothing_factory = SmoothingFunction
        except Exception:
            self._bleu_available = False
        return self._bleu_available

    def _load_meteor(self) -> bool:
        if self._meteor_loaded:
            return self._meteor_available
        self._meteor_loaded = True
        try:
            from nltk.translate.meteor_score import meteor_score

            self._meteor_function = meteor_score
        except Exception:
            self._meteor_available = False
        return self._meteor_available

    @staticmethod
    def _score_result(value: object, reference_kind: str) -> NltkMetricResult:
        if isinstance(value, bool) or not isinstance(value, Real):
            return NltkMetricResult("failed", reference_kind, reason_code="invalid_score")
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0.0 or numeric > 1.0:
            return NltkMetricResult("failed", reference_kind, reason_code="invalid_score")
        return NltkMetricResult("computed", reference_kind, value=numeric)

    def _prepare(self, reference: str, response: str, reference_kind: str) -> tuple[list[str], list[str], NltkMetricResult | None]:
        if not isinstance(reference, str) or not reference.strip() or not isinstance(response, str) or not response.strip():
            return [], [], NltkMetricResult("skipped", reference_kind, reason_code="missing_text")
        reference_tokens, error = self._tokens(reference)
        if error:
            return [], [], NltkMetricResult("failed", reference_kind, reason_code=error)
        response_tokens, error = self._tokens(response)
        if error:
            return [], [], NltkMetricResult("failed", reference_kind, reason_code=error)
        if not reference_tokens or not response_tokens:
            return [], [], NltkMetricResult("skipped", reference_kind, reason_code="empty_tokens")
        return reference_tokens, response_tokens, None

    def score_bleu(self, reference: str, response: str, reference_kind: str = "ground_truth") -> NltkMetricResult:
        references, hypothesis, early = self._prepare(reference, response, reference_kind)
        if early is not None:
            if early.reason_code == "nltk_unavailable":
                return NltkMetricResult("unavailable", reference_kind, reason_code="bleu_unavailable")
            return early
        if not self._load_bleu():
            return NltkMetricResult("unavailable", reference_kind, reason_code="bleu_unavailable")
        try:
            smoothing = self._smoothing_factory() if self._smoothing_factory is not None else None
            value = self._bleu_function([references], hypothesis, smoothing_function=smoothing.method4)
        except Exception:
            return NltkMetricResult("failed", reference_kind, reason_code="bleu_failed")
        return self._score_result(value, reference_kind)

    def score_meteor(self, reference: str, response: str, reference_kind: str = "ground_truth") -> NltkMetricResult:
        references, hypothesis, early = self._prepare(reference, response, reference_kind)
        if early is not None:
            if early.reason_code == "nltk_unavailable":
                return NltkMetricResult("unavailable", reference_kind, reason_code="meteor_unavailable")
            return early
        if not self._load_meteor():
            return NltkMetricResult("unavailable", reference_kind, reason_code="meteor_unavailable")
        try:
            value = self._meteor_function([references], hypothesis)
        except LookupError:
            return NltkMetricResult("unavailable", reference_kind, reason_code="meteor_resource_unavailable")
        except Exception:
            return NltkMetricResult("failed", reference_kind, reason_code="meteor_failed")
        return self._score_result(value, reference_kind)
