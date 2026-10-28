from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from backend.api.schemas import BenchmarkSuiteRequest, EvaluationRequest, RunPlanPreparationRequest, parse_json_object


def _case(case_id="case-1"):
    return {"case_id": case_id, "query": "What is the evidence?", "modalities": ["text"], "text_k": 5, "required_pages": []}


def test_strict_request_models_convert_valid_domain_inputs():
    payload = {
        "run_plan": {
            "plan_schema_version": "1",
            "document_manifest_hash": "a" * 64,
            "resolved_config_json": "{}",
            "configuration_snapshot_hash": "b" * 64,
            "index_fingerprint": "c" * 64,
            "run_fingerprint": "d" * 64,
        },
        "benchmark_suite": {"benchmark_id": "bench", "benchmark_version": "1", "cases": [_case()]},
    }
    model = EvaluationRequest.model_validate(payload)
    assert model.benchmark_suite.cases[0].case_id == "case-1"
    with pytest.raises(ValidationError):
        EvaluationRequest.model_validate({**payload, "unexpected": True})


def test_domain_validation_rejects_duplicate_cases_and_invalid_modalities():
    suite = BenchmarkSuiteRequest.model_validate({"benchmark_id": "bench", "benchmark_version": "1", "cases": [_case(), _case()]})
    with pytest.raises(ValueError):
        suite.to_domain()
    invalid = _case()
    invalid["modalities"] = ["audio"]
    with pytest.raises(ValidationError):
        BenchmarkSuiteRequest.model_validate({"benchmark_id": "bench", "benchmark_version": "1", "cases": [invalid]})


def test_json_parser_rejects_duplicate_keys_and_non_objects():
    with pytest.raises(ValueError):
        parse_json_object('{"query": "one", "query": "two"}')
    with pytest.raises(ValueError):
        parse_json_object("[]")
    with pytest.raises(ValueError):
        parse_json_object('{"value": NaN}')


def test_run_plan_preparation_request_is_strict_and_normalizes_sha256():
    valid = RunPlanPreparationRequest.model_validate({
        "resolved_config": {"text_embed_model": "model"},
        "expected_manifest_hash": "A" * 64,
    })
    assert valid.expected_manifest_hash == "a" * 64
    with pytest.raises(ValidationError):
        RunPlanPreparationRequest.model_validate({
            "resolved_config": {},
            "expected_manifest_hash": "g" * 64,
        })
    with pytest.raises(ValidationError):
        RunPlanPreparationRequest.model_validate({
            "resolved_config": {},
            "expected_manifest_hash": "a" * 64,
            "source_root": "C:/private",
        })
