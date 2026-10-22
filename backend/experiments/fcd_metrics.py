"""Audited prompt-embedding distance (FCD) metric boundary."""

from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Real
from typing import Any, Literal


MetricStatus = Literal["computed", "skipped", "failed", "unavailable"]


@dataclass(frozen=True)
class FcdResult:
    status: MetricStatus
    value: float | None = None
    reason_code: str | None = None
    model_identifier: str | None = None


def build_legacy_prompt(query: str, context: str) -> str:
    """Build the exact prompt used by the research LocalLLM prompt builder."""

    return f"""Use ONLY the context and images provided below to answer the question. Do not use any outside knowledge. If the context is insufficient, clearly state what is missing.

If images are provided, treat them as direct evidence alongside the text — describe what they show and how they support or relate to the answer.

Be thorough but natural. Write in clear prose, not bullet points or numbered sections. Aim for 150–250 words.

Context:
{context}

Question: {query}

Answer:""".strip()


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
            number = float(item)
            if not math.isfinite(number):
                return None
            output.append(number)
        return output if output else None
    except Exception:
        return None


class FcdMetricAdapter:
    """Calculate FCD using a caller-supplied query embedder only."""

    def __init__(self, embedder: Any | None) -> None:
        self._embedder = embedder

    @property
    def model_identifier(self) -> str | None:
        value = getattr(self._embedder, "model_name", None)
        return value if isinstance(value, str) and value.strip() and len(value) <= 160 and "\\" not in value and "/" not in value else None

    def score(self, query: str, context: str, response: str) -> FcdResult:
        if self._embedder is None or not callable(getattr(self._embedder, "embed_query", None)):
            return FcdResult("unavailable", reason_code="fcd_embedder_unavailable")
        if not isinstance(query, str) or not isinstance(context, str) or not isinstance(response, str):
            return FcdResult("failed", reason_code="fcd_invalid_input", model_identifier=self.model_identifier)
        response_snippet = response[:2000].strip()
        if not response_snippet:
            return FcdResult("skipped", reason_code="empty_response", model_identifier=self.model_identifier)
        try:
            prompt_snippet = build_legacy_prompt(query, context)[:2000]
            response_vector = _vector(self._embedder.embed_query(response_snippet))
            prompt_vector = _vector(self._embedder.embed_query(prompt_snippet))
            if response_vector is None or prompt_vector is None:
                return FcdResult("failed", reason_code="fcd_invalid_embedding", model_identifier=self.model_identifier)
            if len(response_vector) != len(prompt_vector):
                return FcdResult("failed", reason_code="fcd_embedding_dimension_mismatch", model_identifier=self.model_identifier)
            response_norm = math.sqrt(sum(value * value for value in response_vector))
            prompt_norm = math.sqrt(sum(value * value for value in prompt_vector))
            if response_norm == 0.0 or prompt_norm == 0.0:
                return FcdResult("failed", reason_code="fcd_zero_embedding", model_identifier=self.model_identifier)
            similarity = sum(left * right for left, right in zip(response_vector, prompt_vector)) / (response_norm * prompt_norm)
            if not math.isfinite(similarity):
                return FcdResult("failed", reason_code="fcd_invalid_score", model_identifier=self.model_identifier)
            distance = (1.0 - similarity) * 100
            if not math.isfinite(distance):
                return FcdResult("failed", reason_code="fcd_invalid_score", model_identifier=self.model_identifier)
            return FcdResult("computed", value=round(distance, 2), model_identifier=self.model_identifier)
        except Exception:
            return FcdResult("failed", reason_code="fcd_failed", model_identifier=self.model_identifier)
