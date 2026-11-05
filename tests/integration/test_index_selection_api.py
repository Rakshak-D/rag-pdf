from __future__ import annotations

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import pymupdf
import pytest
from starlette.testclient import TestClient

from backend.bootstrap_app.runtime import BootstrapSettings, build_bootstrap_runtime
from backend.app.runtime import LocalRuntimeSettings, build_runtime
from backend.config import ResolvedRunConfig
from backend.engine.manifest import build_document_manifest
from backend.engine.retrieval import HybridHit, query_id_for
from backend.experiments import BenchmarkCase, BenchmarkSuite, build_run_plan
from backend.services.index_selection import IndexSelectionError
from notebooks.config import Config


BUILD_A = "a" * 32
BUILD_B = "b" * 32
TOKEN = "selection-integration-token"
BOOTSTRAP_TOKEN = "bootstrap-selection-integration-token"


def _pdf_bytes(text: str = "source document") -> bytes:
    document = pymupdf.open()
    try:
        page = document.new_page()
        page.insert_text((72, 72), text)
        return document.tobytes()
    finally:
        document.close()


def _config(source: Path, root: Path) -> ResolvedRunConfig:
    return ResolvedRunConfig.from_legacy(Config(
        pdf_dir=str(source),
        images_dir=str(root / "images"),
        database_dir=str(root / "database"),
        results_dir=str(root / "results"),
        model_comparison_results_dir=str(root / "comparison"),
        retrieval_results_dir=str(root / "retrieval"),
        text_k=5,
        image_k=5,
        reset_collections_on_start=False,
        max_context_tokens=500,
        llm_retries=0,
    ))


def _settings(root: Path, source: Path, storage: Path, *, report: Path | None = None) -> LocalRuntimeSettings:
    state = root / "runtime-state"
    state.mkdir(parents=True, exist_ok=True)
    runtime = root / "runtime"
    runtime.mkdir(exist_ok=True)
    return LocalRuntimeSettings(
        api_token=TOKEN,
        data_dir=source,
        artifact_store_dir=runtime / "artifacts",
        catalog_db_path=state / "catalog.sqlite3",
        jobs_db_path=state / "jobs.sqlite3",
        index_storage_root=storage,
        build_report_path=report,
    )


def _write_report(storage: Path, config: ResolvedRunConfig, manifest_hash: str, build_id: str) -> Path:
    plan = build_run_plan(config, manifest_hash)
    artifact = storage / "build-artifacts" / build_id
    artifact.mkdir(parents=True, exist_ok=True)
    report = artifact / "build-report.json"
    report.write_text(json.dumps({
        "report_schema_version": "1",
        "build_id": build_id,
        "artifact_root": str(artifact),
        "report_path": str(report),
        "index_fingerprint": plan.index_fingerprint,
        "document_manifest_hash": manifest_hash,
        "status": "completed",
        "partial_persistence": False,
        "text_records_persisted": 1,
        "image_records_persisted": 0,
    }, sort_keys=True), encoding="utf-8")
    return report


def _selection_payload(config: ResolvedRunConfig, manifest_hash: str, build_id: str, revision: int = 0) -> dict[str, object]:
    plan = build_run_plan(config, manifest_hash)
    return {
        "resolved_config": config.model_dump(mode="json"),
        "expected_manifest_hash": manifest_hash,
        "expected_index_fingerprint": plan.index_fingerprint,
        "build_id": build_id,
        "expected_revision": revision,
        "operation_id": ("1" if build_id == BUILD_A else "2") * 32,
    }


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def _select_service(service, config: ResolvedRunConfig, manifest_hash: str, build_id: str, revision: int) -> dict[str, object]:
    payload = _selection_payload(config, manifest_hash, build_id, revision)
    return service.select(
        config=config,
        expected_manifest_hash=manifest_hash,
        expected_index_fingerprint=str(payload["expected_index_fingerprint"]),
        build_id=build_id,
        expected_revision=revision,
        operation_id=str(payload["operation_id"]),
    )


def _fixture(tmp_path: Path, *, with_report: bool = True):
    source = tmp_path / "source"
    source.mkdir()
    (source / "manual.pdf").write_bytes(_pdf_bytes())
    storage = tmp_path / "index-storage"
    storage.mkdir()
    config = _config(source, tmp_path)
    manifest = build_document_manifest(source)
    report = _write_report(storage, config, manifest.content_hash, BUILD_A) if with_report else None
    settings = _settings(tmp_path, source, storage, report=report)
    return settings, config, manifest, report


def test_selection_api_is_authenticated_and_does_not_choose_a_candidate(tmp_path: Path):
    settings, config, manifest, _ = _fixture(tmp_path, with_report=False)
    _write_report(settings.index_storage_root, config, manifest.content_hash, BUILD_A)
    _write_report(settings.index_storage_root, config, manifest.content_hash, BUILD_B)
    runtime = build_runtime(settings)
    with TestClient(runtime.app) as client:
        assert client.get("/api/v1/index/selection").status_code == 401
        assert client.post("/api/v1/index/selection", json={}).status_code == 401
        response = client.get("/api/v1/index/selection", headers=_auth())
        assert response.status_code == 200
        assert response.json()["selection_status"] == "unselected"
        assert response.json()["selection"] is None
        assert response.json()["store_checked"] is False


def test_selection_is_validated_durable_and_restored_after_runtime_reconstruction(tmp_path: Path, monkeypatch):
    settings, config, manifest, report = _fixture(tmp_path)
    assert report is not None
    bootstrap_state = tmp_path / "bootstrap-state"
    bootstrap_state.mkdir()
    bootstrap = build_bootstrap_runtime(BootstrapSettings(
        api_token=BOOTSTRAP_TOKEN,
        source_root=settings.data_dir,
        storage_root=settings.index_storage_root,
        state_dir=bootstrap_state,
    ))
    with TestClient(bootstrap.app) as bootstrap_client:
        inventory = bootstrap_client.post("/api/v1/bootstrap/index-inventory", headers={
            "Authorization": f"Bearer {BOOTSTRAP_TOKEN}",
        }, json={
            "resolved_config": config.model_dump(mode="json"),
            "expected_manifest_hash": manifest.content_hash,
        })
        assert inventory.status_code == 200, inventory.json()
        [bootstrap_candidate] = inventory.json()["candidates"]
        assert bootstrap_candidate["build_id"] == BUILD_A
        assert bootstrap_candidate["compatibility"] == "report_match"
        assert bootstrap_candidate["check_level"] == "report" and bootstrap_candidate["store_checked"] is False
        assert str(tmp_path) not in inventory.text
    from backend.vectorstore.chroma_store import ChromaVectorStore

    monkeypatch.setattr(ChromaVectorStore, "open_existing", lambda *_args, **_kwargs: pytest.fail("selection must not open Chroma"))
    runtime = build_runtime(settings)
    payload = _selection_payload(config, manifest.content_hash, BUILD_A)
    with TestClient(runtime.app) as client:
        selected = client.post("/api/v1/index/selection", headers=_auth(), json=payload)
        assert selected.status_code == 200, selected.json()
        state = selected.json()
        assert state["selection_status"] == "selected"
        assert state["selection"]["build_id"] == BUILD_A
        assert state["selection"]["report_reference"] == f"build-artifacts/{BUILD_A}/build-report.json"
        assert state["selection"]["report_sha256"]
        assert state["selection"]["document_manifest_hash"] == manifest.content_hash
        assert state["selection"]["index_fingerprint"] == payload["expected_index_fingerprint"]
        assert state["selection"]["report_status"] == "completed"
        assert state["selection"]["selection_revision"] == 1
        assert state["check_level"] == "report" and state["store_checked"] is False
        assert runtime.settings.jobs_db_path.parent.joinpath("active-index-selection.json").is_file()

    restored = build_runtime(settings)
    with TestClient(restored.app) as client:
        after_restart = client.get("/api/v1/index/selection", headers=_auth())
        assert after_restart.status_code == 200
        assert after_restart.json()["selection"] == state["selection"]
        assert after_restart.json()["selection_status"] == "selected"


def test_corrupt_selection_state_fails_closed_after_restart(tmp_path: Path):
    settings, _config_value, _manifest, _report = _fixture(tmp_path)
    state_path = settings.jobs_db_path.parent / "active-index-selection.json"
    state_path.write_text('{"schema_version":1,"unexpected":"value"}', encoding="utf-8")
    runtime = build_runtime(settings)
    with TestClient(runtime.app) as client:
        response = client.get("/api/v1/index/selection", headers=_auth())
        assert response.status_code == 200
        assert response.json()["selection_status"] == "invalid"
        assert response.json()["selection"] is None
        assert response.json()["reason_code"] == "selection_state_invalid"


def test_selection_rejects_bad_candidate_identity_and_compare_and_swap_conflict(tmp_path: Path):
    settings, config, manifest, _ = _fixture(tmp_path)
    runtime = build_runtime(settings)
    with TestClient(runtime.app) as client:
        wrong_build = client.post("/api/v1/index/selection", headers=_auth(), json=_selection_payload(config, manifest.content_hash, BUILD_B))
        assert wrong_build.status_code == 404
        assert wrong_build.json()["error"]["code"] == "build_report_not_found"

        malformed = _selection_payload(config, manifest.content_hash, BUILD_A)
        malformed["build_id"] = "../outside"
        assert client.post("/api/v1/index/selection", headers=_auth(), json=malformed).status_code == 400

        arbitrary_path = _selection_payload(config, manifest.content_hash, BUILD_A)
        arbitrary_path["report_path"] = str(settings.index_storage_root / "build-artifacts" / BUILD_A / "build-report.json")
        assert client.post("/api/v1/index/selection", headers=_auth(), json=arbitrary_path).status_code == 400

        mismatched = _selection_payload(config, manifest.content_hash, BUILD_A)
        mismatched["expected_index_fingerprint"] = "f" * 64
        response = client.post("/api/v1/index/selection", headers=_auth(), json=mismatched)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "index_fingerprint_mismatch"

        changed_config = config.model_copy(update={"chunk_overlap_tokens": config.chunk_overlap_tokens + 1})
        changed_payload = _selection_payload(changed_config, manifest.content_hash, BUILD_A)
        mismatch = client.post("/api/v1/index/selection", headers=_auth(), json=changed_payload)
        assert mismatch.status_code == 409
        assert mismatch.json()["error"]["code"] == "build_report_mismatch"

        valid = client.post("/api/v1/index/selection", headers=_auth(), json=_selection_payload(config, manifest.content_hash, BUILD_A))
        assert valid.status_code == 200
        stale_revision = _selection_payload(config, manifest.content_hash, BUILD_A, revision=0)
        stale_revision["operation_id"] = "3" * 32
        conflict = client.post("/api/v1/index/selection", headers=_auth(), json=stale_revision)
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "selection_revision_conflict"


@pytest.mark.parametrize(
    ("changes", "expected_code"),
    [({"status": "failed"}, "build_report_failed"), ({"partial_persistence": True}, "build_report_incomplete")],
)
def test_selection_rejects_failed_or_partial_build_reports(tmp_path: Path, changes, expected_code):
    settings, config, manifest, report = _fixture(tmp_path)
    assert report is not None
    payload = json.loads(report.read_text(encoding="utf-8"))
    payload.update(changes)
    report.write_text(json.dumps(payload), encoding="utf-8")
    runtime = build_runtime(settings)
    with TestClient(runtime.app) as client:
        response = client.post("/api/v1/index/selection", headers=_auth(), json=_selection_payload(config, manifest.content_hash, BUILD_A))
        assert response.status_code == 409
        assert response.json()["error"]["code"] == expected_code
        assert not settings.jobs_db_path.parent.joinpath("active-index-selection.json").exists()


def test_concurrent_selection_uses_revision_compare_and_swap(tmp_path: Path):
    settings, config, manifest, _ = _fixture(tmp_path)
    _write_report(settings.index_storage_root, config, manifest.content_hash, BUILD_B)
    runtime = build_runtime(settings)
    service = runtime.index_selection_service
    payload_a = _selection_payload(config, manifest.content_hash, BUILD_A)
    payload_b = _selection_payload(config, manifest.content_hash, BUILD_B)

    def select(payload):
        try:
            return service.select(
                config=config,
                expected_manifest_hash=manifest.content_hash,
                expected_index_fingerprint=str(payload["expected_index_fingerprint"]),
                build_id=str(payload["build_id"]),
                expected_revision=0,
                operation_id=str(payload["operation_id"]),
            )
        except IndexSelectionError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(select, (payload_a, payload_b)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(result == "selection_revision_conflict" for result in results) == 1
    state = service.get_state()
    assert state["selection_status"] == "selected"
    assert state["revision"] == 1


def test_selection_write_failure_does_not_claim_commit(tmp_path: Path, monkeypatch):
    settings, config, manifest, _ = _fixture(tmp_path)
    runtime = build_runtime(settings)
    monkeypatch.setattr(runtime.index_selection_service, "_write_record", lambda _record: (_ for _ in ()).throw(IndexSelectionError("selection_write_failed")))
    with TestClient(runtime.app) as client:
        response = client.post("/api/v1/index/selection", headers=_auth(), json=_selection_payload(config, manifest.content_hash, BUILD_A))
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "selection_commit_uncertain"
        assert client.get("/api/v1/index/selection", headers=_auth()).json()["selection_status"] == "legacy_configured"


def test_selection_write_readback_must_match_the_exact_operation(tmp_path: Path, monkeypatch):
    settings, config, manifest, _ = _fixture(tmp_path)
    runtime = build_runtime(settings)
    service = runtime.index_selection_service
    write_record = service._write_record

    def write_different_operation(record):
        write_record(record.model_copy(update={"operation_id": "f" * 32}))
        raise IndexSelectionError("selection_write_failed")

    monkeypatch.setattr(service, "_write_record", write_different_operation)
    with TestClient(runtime.app) as client:
        response = client.post("/api/v1/index/selection", headers=_auth(), json=_selection_payload(config, manifest.content_hash, BUILD_A))
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "selection_commit_uncertain"
        state = client.get("/api/v1/index/selection", headers=_auth()).json()
        assert state["selection_status"] == "selected"
        assert state["selection"]["build_id"] == BUILD_A


def test_symlinked_selection_record_fails_closed(tmp_path: Path):
    settings, _config_value, _manifest, _report = _fixture(tmp_path)
    state_path = settings.jobs_db_path.parent / "active-index-selection.json"
    outside = tmp_path / "outside-selection.json"
    outside.write_text("{}", encoding="utf-8")
    try:
        state_path.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable in this environment")
    runtime = build_runtime(settings)
    with TestClient(runtime.app) as client:
        response = client.get("/api/v1/index/selection", headers=_auth())
        assert response.status_code == 200
        assert response.json()["selection_status"] == "invalid"
        assert response.json()["reason_code"] == "selection_state_invalid"


def test_selection_becomes_stale_after_source_or_report_changes_without_auto_replacement(tmp_path: Path):
    settings, config, manifest, report = _fixture(tmp_path)
    assert report is not None
    runtime = build_runtime(settings)
    with TestClient(runtime.app) as client:
        response = client.post("/api/v1/index/selection", headers=_auth(), json=_selection_payload(config, manifest.content_hash, BUILD_A))
        assert response.status_code == 200
        (settings.data_dir / "manual.pdf").write_bytes(_pdf_bytes("updated source"))
        stale = client.get("/api/v1/index/selection", headers=_auth()).json()
        assert stale["selection_status"] == "stale"
        assert stale["selection"]["build_id"] == BUILD_A
        assert stale["reason_code"] == "manifest_changed"


def test_replaced_selected_report_is_stale_and_another_candidate_is_not_chosen(tmp_path: Path):
    settings, config, manifest, report = _fixture(tmp_path)
    assert report is not None
    _write_report(settings.index_storage_root, config, manifest.content_hash, BUILD_B)
    runtime = build_runtime(settings)
    with TestClient(runtime.app) as client:
        selected = client.post("/api/v1/index/selection", headers=_auth(), json=_selection_payload(config, manifest.content_hash, BUILD_A))
        assert selected.status_code == 200
        report.write_bytes(report.read_bytes() + b"\n")
        current = client.get("/api/v1/index/selection", headers=_auth()).json()
        assert current["selection_status"] == "stale"
        assert current["selection"]["build_id"] == BUILD_A
        assert current["reason_code"] == "selection_report_changed"


def test_explicit_legacy_report_path_remains_an_execution_fallback(tmp_path: Path):
    settings, config, manifest, report = _fixture(tmp_path)
    assert report is not None
    legacy_settings = replace(settings, build_report_path=report)
    runtime = build_runtime(legacy_settings)
    with TestClient(runtime.app) as client:
        state = client.get("/api/v1/index/selection", headers=_auth()).json()
        assert state["selection_status"] == "legacy_configured"
        assert state["selection"] is None
        assert state["legacy_report_reference"] == f"build-artifacts/{BUILD_A}/build-report.json"
        readiness = client.post("/api/v1/index/readiness", headers=_auth(), json={
            "workflow": "evaluation",
            "resolved_config": config.model_dump(mode="json"),
            "expected_manifest_hash": manifest.content_hash,
        })
        assert readiness.status_code == 200
        assert readiness.json()["status"] == "report_match"
        assert readiness.json()["build_id"] == BUILD_A
        assert readiness.json()["store_checked"] is False


def test_evaluation_execution_uses_the_pinned_selected_report_after_restart(tmp_path: Path):
    settings, config, manifest, report = _fixture(tmp_path)
    assert report is not None
    runtime = build_runtime(settings)
    chain_root = tmp_path / "evaluation-fixtures"
    chain_root.mkdir()
    from tests.unit.test_evaluation_runner import setup_chain

    _unused_plan, query, retrieval_result, formatter, generation = setup_chain(chain_root)
    plan = build_run_plan(config, manifest.content_hash)
    seen: list[Path] = []

    def retrieval(query_text, supplied_plan, _storage_root, **kwargs):
        seen.append(Path(kwargs["build_report_path"]))
        modalities = kwargs["modalities"]
        text_k = kwargs["text_k"]
        image_k = kwargs["image_k"]
        return replace(
            retrieval_result,
            query_id=query_id_for(query_text, supplied_plan, modalities, text_k, image_k),
            index_fingerprint=supplied_plan.index_fingerprint,
            document_manifest_hash=supplied_plan.document_manifest_hash,
            modalities=modalities,
            effective_text_k=text_k,
            effective_image_k=image_k,
            query_text=query_text,
        )

    headers = _auth()
    with TestClient(runtime.app) as client:
        selected = client.post("/api/v1/index/selection", headers=headers, json=_selection_payload(config, manifest.content_hash, BUILD_A))
        assert selected.status_code == 200

    restored = build_runtime(settings)
    restored.service._retrieval_callable = retrieval
    restored.service._model_registry = None
    restored.service._context_formatter = formatter
    restored.service._generation_adapter = generation
    with TestClient(restored.app) as client:
        selection = client.get("/api/v1/index/selection", headers=headers)
        assert selection.status_code == 200
        assert selection.json()["selection_status"] == "selected"
        response = client.post("/api/v1/evaluations", headers=headers, json={
            "run_plan": plan.model_dump(mode="json"),
            "benchmark_suite": {
                "benchmark_id": "selection-integration",
                "benchmark_version": "1",
                "cases": [{"case_id": "case-1", "query": query, "modalities": ["text"], "text_k": 5, "required_pages": ["3"]}],
            },
        })
        assert response.status_code == 201, response.json()
        assert seen == [report]


def test_evaluation_without_selection_fails_before_model_access(tmp_path: Path, monkeypatch):
    settings, config, manifest, _ = _fixture(tmp_path, with_report=False)
    runtime = build_runtime(settings)
    from tests.unit.test_evaluation_runner import setup_chain

    chain_root = tmp_path / "evaluation-fixtures"
    chain_root.mkdir()
    _unused_plan, query, _result, _formatter, _generation = setup_chain(chain_root)
    plan = build_run_plan(config, manifest.content_hash)
    monkeypatch.setattr(runtime.model_registry, "get_text_adapter", lambda *_args, **_kwargs: pytest.fail("model access must not precede selection"))
    with TestClient(runtime.app) as client:
        response = client.post("/api/v1/evaluations", headers=_auth(), json={
            "run_plan": plan.model_dump(mode="json"),
            "benchmark_suite": {
                "benchmark_id": "selection-required",
                "benchmark_version": "1",
                "cases": [{"case_id": "case-1", "query": query, "modalities": ["text"], "text_k": 5}],
            },
        })
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "index_not_selected"


def test_job_admission_pins_report_identity_before_a_later_selection_change(tmp_path: Path):
    settings, config, manifest, _report = _fixture(tmp_path)
    report_b = _write_report(settings.index_storage_root, config, manifest.content_hash, BUILD_B)
    runtime = build_runtime(settings)
    service = runtime.service
    selection = service.index_selection_service
    assert selection is not None
    _select_service(selection, config, manifest.content_hash, BUILD_A, 0)
    plan = build_run_plan(config, manifest.content_hash)
    suite = BenchmarkSuite("pin-test", "1", (BenchmarkCase("case-1", "question", ("text",), text_k=5),))
    manager = runtime.job_manager
    manager._started = True  # Exercise the bounded admission path without starting a worker.
    try:
        accepted = manager.submit_evaluation(plan, suite)
        work = manager._queue.get_nowait()
        assert work.pinned_index_report is not None
        assert work.pinned_index_report.build_id == BUILD_A
        _select_service(selection, config, manifest.content_hash, BUILD_B, 1)
        assert work.pinned_index_report.report_path != report_b
        assert work.pinned_index_report.build_id == BUILD_A
        assert accepted.status == "queued"
    finally:
        manager._queue.task_done()
        manager._shutdown = True
