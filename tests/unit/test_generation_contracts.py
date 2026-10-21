from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.support.source_loader import load_class_methods


class FailingOllama:
    def __init__(self):
        self.calls = 0

    def chat(self, **kwargs):
        self.calls += 1
        raise RuntimeError("deterministic Ollama failure")


class TokenizerStub:
    @staticmethod
    def encode(value):
        return value.split()


class TimeStub:
    current = 0.0

    @classmethod
    def perf_counter(cls):
        cls.current += 0.01
        return cls.current

    @staticmethod
    def sleep(_seconds):
        return None


def llm_class(cfg, ollama):
    return load_class_methods(
        "LocalLLM",
        "generate_response",
        globals_override={
            "cfg": cfg,
            "ollama": ollama,
            "time": TimeStub,
            "LOGGER": SimpleNamespace(warning=lambda *a, **k: None, error=lambda *a, **k: None),
        },
    )


def make_llm(fail_fast=False, retries=2):
    ollama = FailingOllama()
    cfg = SimpleNamespace(llm_retries=retries, llm_retry_backoff_sec=0.0,
                          llm_fail_fast=fail_fast, llm_think_mode=False,
                          llm_temperature=0.25, llm_max_tokens=384)
    cls = llm_class(cfg, ollama)
    instance = object.__new__(cls)
    instance.model_name = "fixture-model"
    instance.tokenizer = TokenizerStub()
    instance.build_prompt = lambda query, context: f"Question: {query}\nContext: {context}"
    return instance, ollama


def test_exhausted_generation_returns_explicit_synthetic_error_result():
    llm, ollama = make_llm(fail_fast=False, retries=2)

    result = llm.generate_response("question", "context", stream=False)

    assert ollama.calls == 3
    assert result["response"] == "No answer (LLM error)."
    assert result["error"] == "deterministic Ollama failure"
    assert result["attempts"] == 3
    assert result["generation_time_sec"] == 0.0


def test_fail_fast_generation_raises_after_configured_attempts():
    llm, ollama = make_llm(fail_fast=True, retries=1)

    with pytest.raises(RuntimeError, match=r"LLM generation failed after 2 attempt\(s\)"):
        llm.generate_response("question", "context", stream=False)

    assert ollama.calls == 2
