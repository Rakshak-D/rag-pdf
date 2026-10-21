"""Validated, side-effect-free benchmark suite contracts."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal


class BenchmarkContractError(ValueError):
    """Raised when benchmark cases or suites are invalid."""


_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_MODALITIES = {"text", "image"}
_MAX_CASES = 100


def _identity(value: object, *, label: str, max_length: int = 80) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length or not _ID_RE.fullmatch(value):
        raise BenchmarkContractError(f"{label} is invalid")
    return value


def _positive(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BenchmarkContractError(f"{label} must be a positive integer")
    return value


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    query: str
    modalities: tuple[Literal["text", "image"], ...] = ("text", "image")
    text_k: int | None = None
    image_k: int | None = None
    required_pages: tuple[str, ...] = ()
    reference_text: str | None = None

    def __post_init__(self) -> None:
        _identity(self.case_id, label="case_id")
        if not isinstance(self.query, str) or not self.query.strip() or len(self.query.strip()) > 4096:
            raise BenchmarkContractError("query must be non-empty and bounded")
        if not isinstance(self.modalities, tuple) or not self.modalities or len(set(self.modalities)) != len(self.modalities) or any(value not in _MODALITIES for value in self.modalities):
            raise BenchmarkContractError("modalities must contain unique supported values")
        if self.text_k is not None:
            _positive(self.text_k, label="text_k")
        if self.image_k is not None:
            _positive(self.image_k, label="image_k")
        normalized: list[str] = []
        for value in self.required_pages:
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                raise BenchmarkContractError("required_pages must contain strings or integers")
            page = str(value).strip()
            if not page or len(page) > 32:
                raise BenchmarkContractError("required page references must be non-empty and bounded")
            normalized.append(page)
        if len(set(normalized)) != len(normalized):
            raise BenchmarkContractError("required_pages must not contain duplicates")
        if self.reference_text is not None and not isinstance(self.reference_text, str):
            raise BenchmarkContractError("reference_text must be a string or None")
        if isinstance(self.reference_text, str):
            reference = self.reference_text.strip()
            if len(reference) > 200_000:
                raise BenchmarkContractError("reference_text is too long")
            object.__setattr__(self, "reference_text", reference)
        object.__setattr__(self, "query", self.query.strip())
        object.__setattr__(self, "required_pages", tuple(normalized))


@dataclass(frozen=True)
class BenchmarkSuite:
    benchmark_id: str
    benchmark_version: str
    cases: tuple[BenchmarkCase, ...]

    def __post_init__(self) -> None:
        _identity(self.benchmark_id, label="benchmark_id", max_length=120)
        if not isinstance(self.benchmark_version, str) or not self.benchmark_version.strip() or len(self.benchmark_version) > 80 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", self.benchmark_version):
            raise BenchmarkContractError("benchmark_version is invalid")
        if not isinstance(self.cases, tuple) or not self.cases or len(self.cases) > _MAX_CASES:
            raise BenchmarkContractError(f"cases must contain between 1 and {_MAX_CASES} items")
        if any(not isinstance(case, BenchmarkCase) for case in self.cases):
            raise BenchmarkContractError("cases must contain BenchmarkCase values")
        ids = [case.case_id for case in self.cases]
        if len(set(ids)) != len(ids):
            raise BenchmarkContractError("case_id values must be unique")
