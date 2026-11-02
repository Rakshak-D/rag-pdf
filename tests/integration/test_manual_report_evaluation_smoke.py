from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def test_real_retrieval_evaluation_and_catalog_round_trip():
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith("RAG_WORKBENCH_")
    }
    environment.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "ANONYMIZED_TELEMETRY": "FALSE",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
        "OLLAMA_HOST": "http://127.0.0.1:1",
    })
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "manual_report_evaluation_smoke.py")],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    assert lines[-1] == "manual_report_evaluation_smoke_passed"
    evidence = json.loads(lines[-2])

    assert evidence["build"]["status"] in {"completed", "completed_with_warnings"}
    assert evidence["build"]["text_records"] > 0
    assert evidence["preflight"] == {
        "result": "report_compatible_for_manual_review",
        "check_level": "report",
        "store_checked": False,
        "storage_unchanged": True,
    }
    evaluation = evidence["evaluation"]
    assert evaluation["outcome_status"] == evaluation["catalog_status"] == "succeeded"
    assert evaluation["artifact_round_trip_equal"] is True
    assert evaluation["report_evaluation_fingerprint_valid"] is True
    assert evaluation["catalog_aggregate_count"] == 19
    assert evaluation["retrieved_evidence_marker"] is True
    assert evaluation["generation_prompt_contained_evidence"] is True
    assert evaluation["generation_response_hash_matches"] is True
    assert set(evaluation["metric_ids"]) == {f"M{number}" for number in range(1, 20)}

    assert set(evidence["retrieval_failure_cases"]) == {
        "missing_report", "report_identity_mismatch", "persisted_count_mismatch",
        "active_writer_reservation", "incomplete_marker",
    }
    assert evidence["generation_failure_and_recovery"]["generation_failure"]["automatic_retry"] is False
    assert evidence["generation_failure_and_recovery"]["explicit_recovery"]["outcome_status"] == "succeeded"
    assert set(evidence["persistence_failure_cases"]) == {
        "artifact_publication_failure", "catalog_registration_failure", "invalid_evaluation_provenance",
    }
    assert evidence["report_and_index_manifest_unchanged"] is True
    assert evidence["all_child_processes_exited"] is True
