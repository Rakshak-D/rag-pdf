"""Small, lazy ROUGE scoring boundary for audited evaluation metrics."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any


@dataclass(frozen=True)
class ReferenceSelection:
    text: str | None
    kind: str


@dataclass(frozen=True)
class RougeBatchResult:
    status: str
    reference_kind: str
    values: tuple[float, float, float] | None = None
    reason_code: str | None = None


def select_reference(reference_text: str | None, context_text: str, *, top_k_chunks: int, max_text_chunks: int) -> ReferenceSelection:
    """Port the research reference priority and context pseudo-reference cap."""

    explicit = reference_text.strip() if isinstance(reference_text, str) else ""
    if explicit and len(explicit) > 20:
        return ReferenceSelection(explicit, "ground_truth")
    context = context_text.strip() if isinstance(context_text, str) else ""
    if not context:
        return ReferenceSelection(None, "no_reference")
    parts = context.split("\n\n")
    effective_k = min(top_k_chunks, max_text_chunks)
    selected = "\n\n".join(parts[:effective_k]) if len(parts) > effective_k else context
    return ReferenceSelection(selected, "context_pseudoreference")


class RougeScorerAdapter:
    """Lazily initialize one stemmed scorer for all three audited metrics."""

    def __init__(self) -> None:
        self._scorer: Any = None
        self._load_attempted = False
        self._available = True

    def _load(self) -> Any | None:
        if self._load_attempted:
            return self._scorer
        self._load_attempted = True
        try:
            from rouge_score.rouge_scorer import RougeScorer

            self._scorer = RougeScorer(["rouge1", "rouge2", "rougeL"], use_stemmer=True)
        except Exception:
            self._available = False
            self._scorer = None
        return self._scorer

    def score(self, reference: str, response: str, reference_kind: str) -> RougeBatchResult:
        if not isinstance(reference, str) or not reference.strip() or not isinstance(response, str) or not response.strip():
            return RougeBatchResult("skipped", reference_kind, reason_code="missing_text")
        scorer = self._load()
        if scorer is None:
            return RougeBatchResult("unavailable", reference_kind, reason_code="rouge_unavailable")
        try:
            values = tuple(float(scorer.score(reference, response)[name].fmeasure) for name in ("rouge1", "rouge2", "rougeL"))
        except Exception:
            return RougeBatchResult("failed", reference_kind, reason_code="rouge_failed")
        if len(values) != 3 or any(not math.isfinite(value) or value < 0.0 or value > 1.0 for value in values):
            return RougeBatchResult("failed", reference_kind, reason_code="rouge_invalid_score")
        return RougeBatchResult("computed", reference_kind, values=values)
