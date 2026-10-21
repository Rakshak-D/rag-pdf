from __future__ import annotations

import pytest

from backend.experiments import BenchmarkCase, BenchmarkContractError, BenchmarkSuite


def test_benchmark_contracts_normalize_pages_and_preserve_order():
    case = BenchmarkCase("case-1", "  What is shown? ", ("text",), text_k=3, required_pages=(1, " 2 "), reference_text="  a valid reference answer  ")
    suite = BenchmarkSuite("demo", "v1", (case,))
    assert suite.cases[0].query == "What is shown?"
    assert suite.cases[0].required_pages == ("1", "2")
    assert suite.cases[0].reference_text == "a valid reference answer"


@pytest.mark.parametrize("reference", [None, "", "   ", "short"])
def test_reference_text_is_optional_and_normalized(reference):
    assert BenchmarkCase("case", "query", ("text",), reference_text=reference).reference_text in {None, "", "short"}


def test_reference_text_rejects_invalid_or_oversized_values():
    with pytest.raises(BenchmarkContractError):
        BenchmarkCase("case", "query", ("text",), reference_text=123)
    with pytest.raises(BenchmarkContractError):
        BenchmarkCase("case", "query", ("text",), reference_text="x" * 200001)


@pytest.mark.parametrize("factory", [
    lambda: BenchmarkCase("bad", "q", ("text", "text")),
    lambda: BenchmarkCase("bad", "q", ("audio",)),
    lambda: BenchmarkCase("bad", "q", text_k=True),
    lambda: BenchmarkCase("bad", "q", required_pages=("1", "1")),
])
def test_invalid_cases_are_rejected(factory):
    with pytest.raises(BenchmarkContractError):
        factory()


def test_duplicate_case_ids_and_oversized_suites_are_rejected():
    case = BenchmarkCase("case", "q", ("text",))
    with pytest.raises(BenchmarkContractError):
        BenchmarkSuite("bench", "1", (case, case))
    cases = tuple(BenchmarkCase(f"case-{i}", f"q-{i}", ("text",)) for i in range(101))
    with pytest.raises(BenchmarkContractError):
        BenchmarkSuite("bench", "1", cases)
