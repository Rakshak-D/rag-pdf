from __future__ import annotations

from pathlib import Path

import pytest

from backend.config import ResolvedRunConfig
from backend.engine.context import EvidenceContext, SelectedImageEvidence, SelectedTextEvidence
from backend.engine.generation import GenerationInferenceError, GenerationInputError, LocalGenerationAdapter
from backend.engine.retrieval import HybridHit
from backend.experiments import build_run_plan
from notebooks.config import Config


class CharEncoder:
    def encode(self, value: str):
        return list(range(len(value)))


def make_plan(tmp_path: Path, **overrides):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    values = dict(
        pdf_dir=str(pdf_dir), images_dir=str(tmp_path / "images"),
        database_dir=str(tmp_path / "db"), results_dir=str(tmp_path / "results"),
        model_comparison_results_dir=str(tmp_path / "comparison"),
        retrieval_results_dir=str(tmp_path / "retrieval"),
        reset_collections_on_start=False, max_context_tokens=1000,
        llm_retries=2, llm_retry_backoff_sec=0, llm_fail_fast=False,
    )
    values.update(overrides)
    config = ResolvedRunConfig.from_legacy(Config(**values))
    return build_run_plan(config, "a" * 64), config


def make_context(plan, *, image=None, query="What is shown?"):
    hit = HybridHit("chunk-1", 1, 0.5, {"source": "paper.pdf", "page_num": 1}, document="The evidence answer.")
    selected_text = (SelectedTextEvidence("T1", "chunk-1", "paper.pdf", "1", "The evidence answer.", hit),)
    selected_images = ()
    if image is not None:
        image_hit = HybridHit("image-1", 1, 0.4, {"source": "paper.pdf", "page_num": 1}, caption="caption")
        selected_images = (SelectedImageEvidence("I1", "image-1", "paper.pdf", "1", "caption", image, image_hit),)
    text = "[T1] Source: paper.pdf, page 1 (ID: chunk-1)\nThe evidence answer." if selected_text else ""
    return EvidenceContext("q1", plan.index_fingerprint, plan.document_manifest_hash, plan.run_fingerprint, "b" * 64, text, len(text), selected_text, selected_images, {}, (), 0.0, query)


class FakeClient:
    def __init__(self, response=None, failures=0):
        self.response = response or {"message": {"content": "Answer [T1]."}}
        self.failures = failures
        self.requests = []

    def chat(self, **request):
        self.requests.append(request)
        if len(self.requests) <= self.failures:
            raise RuntimeError("private server detail")
        return self.response


def test_generation_is_lazy_and_preserves_provenance(tmp_path):
    plan, _ = make_plan(tmp_path)
    client = FakeClient()
    factories = []
    adapter = LocalGenerationAdapter(client_factory=lambda host: (factories.append(host) or client), encoder=CharEncoder(), sleeper=lambda _: None)
    result = adapter.generate(make_context(plan), plan)
    assert factories == ["http://127.0.0.1:11434"]
    assert result.status == "succeeded"
    assert result.query_text == "What is shown?"
    assert result.query_id == "q1"
    assert result.run_fingerprint == plan.run_fingerprint
    assert result.response == "Answer [T1]."
    assert result.attempts == 1
    assert client.requests[0]["model"] == plan.resolved_config().llm_model
    assert {message["role"] for message in client.requests[0]["messages"]} == {"system", "user"}
    assert "What is shown?" in client.requests[0]["messages"][1]["content"]


def test_failed_inference_retries_and_returns_explicit_failure(tmp_path):
    plan, _ = make_plan(tmp_path, llm_retries=2, llm_fail_fast=False)
    client = FakeClient(failures=3)
    result = LocalGenerationAdapter(client_factory=lambda _: client, encoder=CharEncoder(), sleeper=lambda _: None).generate(make_context(plan), plan)
    assert result.status == "failed"
    assert result.response is None
    assert result.error_code == "inference_failed"
    assert result.attempts == 3
    assert "private server detail" not in str(result)


def test_fail_fast_raises_after_retry_exhaustion(tmp_path):
    plan, _ = make_plan(tmp_path, llm_retries=1, llm_fail_fast=True)
    client = FakeClient(failures=2)
    with pytest.raises(GenerationInferenceError) as exc_info:
        LocalGenerationAdapter(client_factory=lambda _: client, encoder=CharEncoder(), sleeper=lambda _: None).generate(make_context(plan), plan)
    assert exc_info.value.attempts == 2


def test_invalid_context_is_rejected_before_client_construction(tmp_path):
    plan, _ = make_plan(tmp_path)
    called = []
    invalid = make_context(plan, query="")
    with pytest.raises(GenerationInputError):
        LocalGenerationAdapter(client_factory=lambda _: called.append(True), encoder=CharEncoder()).generate(invalid, plan)
    assert called == []
