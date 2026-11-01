from __future__ import annotations

import pytest

from scripts.manual_report_retrieval_smoke import RetrievalSmokeError, run_smoke


def test_manual_report_handoff_runs_real_text_and_image_retrieval():
    try:
        result = run_smoke(timeout=45)
    except RetrievalSmokeError as exc:
        diagnostics = f"; diagnostics retained at {exc.diagnostics_path}" if exc.diagnostics_path else ""
        raise AssertionError(f"manual retrieval smoke failed at {exc.code}{diagnostics}") from None

    assert result["report_counts"] == result["observed_counts"]
    assert result["report_counts"]["text"] > 0
    assert result["report_counts"]["image"] > 0
    assert result["text_query"]["record_ids"]
    assert result["text_query"]["fixture_text_matched"] is True
    assert result["image_query"]["record_ids"]
    assert result["image_query"]["metadata_ids_match"] is True
    assert result["report_and_index_manifest_bytes_unchanged"] is True
    assert result["lease_released_on_retrieval_thread"]
    assert result["failure_cases"]
    assert result["chroma_side_effects"]
