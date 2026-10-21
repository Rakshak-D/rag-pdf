from __future__ import annotations

from pathlib import Path

from backend.config import ResolvedRunConfig
from backend.engine.context import EvidenceContext, SelectedTextEvidence
from backend.engine.generation import LocalGenerationAdapter
from backend.engine.retrieval import HybridHit
from backend.experiments import build_run_plan
from notebooks.config import Config


class Encoder:
    def encode(self, value: str):
        return list(range(len(value)))


def test_local_generation_uses_only_supplied_evidence(tmp_path: Path):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    config = ResolvedRunConfig.from_legacy(Config(pdf_dir=str(pdf_dir), images_dir=str(tmp_path / "images"), database_dir=str(tmp_path / "db"), results_dir=str(tmp_path / "results"), model_comparison_results_dir=str(tmp_path / "comparison"), retrieval_results_dir=str(tmp_path / "retrieval"), reset_collections_on_start=False, max_context_tokens=500, llm_retries=0))
    plan = build_run_plan(config, "a" * 64)
    hit = HybridHit("chunk", 1, 0.1, {"source": "doc.pdf", "page_num": 2}, document="Only supplied evidence.")
    context_text = "[T1] Source: doc.pdf, page 2 (ID: chunk)\nOnly supplied evidence."
    context = EvidenceContext("query", plan.index_fingerprint, plan.document_manifest_hash, plan.run_fingerprint, "c" * 64, context_text, len(context_text), (SelectedTextEvidence("T1", "chunk", "doc.pdf", "2", "Only supplied evidence.", hit),), (), {}, (), 0.0, "What does it say?")
    requests = []

    class Client:
        def chat(self, **request):
            requests.append(request)
            return {"message": {"content": "It says so [T1]."}}

    result = LocalGenerationAdapter(client_factory=lambda _: Client(), encoder=Encoder()).generate(context, plan)
    assert result.status == "succeeded"
    assert result.context_fingerprint == "c" * 64
    assert "Only supplied evidence." in requests[0]["messages"][1]["content"]
    assert "What does it say?" in requests[0]["messages"][1]["content"]
