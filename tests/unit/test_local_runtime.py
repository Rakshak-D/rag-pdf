from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys

import pytest
from starlette.testclient import TestClient

from backend.app.runtime import (
    LocalRuntimeSettings,
    RuntimeConfigurationError,
    build_runtime,
    run_server,
)
from backend.services.model_registry import ModelAdapterRegistry


class _GenerationFake:
    def generate(self, *args, **kwargs):
        raise AssertionError("runtime startup must not generate")


class _ContextFake:
    def format(self, *args, **kwargs):
        raise AssertionError("runtime startup must not format contexts")


def _settings(tmp_path: Path, **overrides) -> LocalRuntimeSettings:
    data = tmp_path / "pdf-source"
    data.mkdir(exist_ok=True)
    index = tmp_path / "index-storage"
    index.mkdir(exist_ok=True)
    report = index / "build-artifacts" / "build-test" / "build-report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text('{"status":"test placeholder"}', encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    runtime = tmp_path / "runtime"
    runtime.mkdir(exist_ok=True)
    values = dict(
        api_token="test-local-token",
        data_dir=data,
        artifact_store_dir=runtime / "artifacts",
        catalog_db_path=state / "catalog.sqlite3",
        jobs_db_path=state / "jobs.sqlite3",
        index_storage_root=index,
        build_report_path=report,
    )
    values.update(overrides)
    return LocalRuntimeSettings(**values)


def _environment(settings: LocalRuntimeSettings) -> dict[str, str]:
    return {
        "RAG_WORKBENCH_API_TOKEN": settings.api_token,
        "RAG_WORKBENCH_DATA_DIR": str(settings.data_dir),
        "RAG_WORKBENCH_ARTIFACT_STORE_DIR": str(settings.artifact_store_dir),
        "RAG_WORKBENCH_CATALOG_DB_PATH": str(settings.catalog_db_path),
        "RAG_WORKBENCH_JOBS_DB_PATH": str(settings.jobs_db_path),
        "RAG_WORKBENCH_INDEX_STORAGE_ROOT": str(settings.index_storage_root),
        "RAG_WORKBENCH_BUILD_REPORT_PATH": str(settings.build_report_path),
    }


def test_environment_settings_require_paths_and_default_to_loopback(tmp_path: Path):
    settings = _settings(tmp_path)
    loaded = LocalRuntimeSettings.from_environment(_environment(settings))
    assert loaded.host == "127.0.0.1"
    assert loaded.port == 8000
    assert loaded.max_request_body_bytes == 24 * 1024 * 1024
    assert loaded.worker_count == 1
    assert loaded.max_pending_jobs == 10
    assert "test-local-token" not in repr(loaded)

    missing = _environment(settings)
    del missing["RAG_WORKBENCH_API_TOKEN"]
    with pytest.raises(RuntimeConfigurationError) as error:
        LocalRuntimeSettings.from_environment(missing)
    assert error.value.error_code == "required_setting_missing"
    assert "test-local-token" not in str(error.value)

    missing_index = _environment(settings)
    del missing_index["RAG_WORKBENCH_INDEX_STORAGE_ROOT"]
    with pytest.raises(RuntimeConfigurationError) as error:
        LocalRuntimeSettings.from_environment(missing_index)
    assert error.value.error_code == "required_setting_missing"


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"api_token": ""}, "api_token_invalid"),
        ({"api_token": "secret token"}, "api_token_invalid"),
        ({"host": "0.0.0.0"}, "api_host_must_be_loopback"),
        ({"port": True}, "api_port_invalid"),
        ({"max_request_body_bytes": 64 * 1024 * 1024 + 1}, "request_limit_invalid"),
        ({"worker_count": 33}, "worker_count_invalid"),
    ],
)
def test_runtime_settings_reject_invalid_values(tmp_path: Path, changes, code):
    with pytest.raises(RuntimeConfigurationError) as error:
        replace(_settings(tmp_path), **changes)
    assert error.value.error_code == code
    assert "secret token" not in str(error.value)


def test_path_overlap_and_missing_report_fail_before_artifact_creation(tmp_path: Path):
    settings = _settings(tmp_path)
    bad = replace(settings, jobs_db_path=settings.data_dir / "jobs.sqlite3")
    with pytest.raises(RuntimeConfigurationError) as error:
        build_runtime(bad)
    assert error.value.error_code == "runtime_paths_overlap"
    assert not settings.artifact_store_dir.exists()

    missing_report = replace(settings, build_report_path=settings.index_storage_root / "absent" / "build-report.json")
    with pytest.raises(RuntimeConfigurationError) as error:
        build_runtime(missing_report)
    assert error.value.error_code == "build_report_missing"
    assert not settings.artifact_store_dir.exists()

    missing_index = replace(settings, index_storage_root=tmp_path / "missing-index")
    with pytest.raises(RuntimeConfigurationError) as error:
        build_runtime(missing_index)
    assert error.value.error_code == "runtime_directory_missing"
    assert not settings.artifact_store_dir.exists()

    misplaced_report = tmp_path / "outside" / "build-report.json"
    misplaced_report.parent.mkdir()
    misplaced_report.write_text("{}", encoding="utf-8")
    bad_report_path = replace(settings, build_report_path=misplaced_report)
    with pytest.raises(RuntimeConfigurationError) as error:
        build_runtime(bad_report_path)
    assert error.value.error_code == "build_report_path_invalid"
    assert not settings.artifact_store_dir.exists()


def test_symlinked_runtime_roots_are_rejected_before_side_effects(tmp_path: Path):
    settings = _settings(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "runtime-link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory symlinks unavailable on this platform: {type(exc).__name__}")
    bad = replace(settings, artifact_store_dir=link)
    with pytest.raises(RuntimeConfigurationError) as error:
        build_runtime(bad)
    assert error.value.error_code == "runtime_path_symlink"
    assert not (settings.catalog_db_path).exists()


def test_composition_is_lazy_and_app_lifespan_owns_job_manager(tmp_path: Path):
    settings = _settings(tmp_path)
    registry = ModelAdapterRegistry(
        text_factory=lambda _config: pytest.fail("adapter factory should remain unused at startup"),
    )
    runtime = build_runtime(
        settings,
        context_formatter=_ContextFake(),
        generation_adapter=_GenerationFake(),
        model_registry=registry,
    )
    assert runtime.model_registry.snapshot().components == ()
    assert runtime.job_manager._started is False
    assert settings.artifact_store_dir.is_dir()
    assert settings.catalog_db_path.is_file()
    assert settings.jobs_db_path.is_file()

    with TestClient(runtime.app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok", "api_version": "v1"}
        assert client.get("/api/v1/runtime/models").status_code == 401
        status = client.get(
            "/api/v1/runtime/models",
            headers={"Authorization": "Bearer test-local-token"},
        )
        assert status.status_code == 200
        assert status.json() == {"components": []}
        assert runtime.job_manager._started is True
    assert runtime.job_manager._shutdown is True
    assert runtime.model_registry.snapshot().components == ()


def test_runtime_import_does_not_construct_app_or_touch_storage(tmp_path: Path):
    repository = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repository)
    env["RAG_WORKBENCH_ARTIFACT_STORE_DIR"] = str(tmp_path / "must-not-exist")
    result = subprocess.run(
        [sys.executable, "-c", "import backend.app.main; print(hasattr(backend.app.main, 'app'))"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"
    assert not (tmp_path / "must-not-exist").exists()


def test_server_uses_settings_without_exposing_token(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    runtime = build_runtime(settings, context_formatter=_ContextFake(), generation_adapter=_GenerationFake())
    captured = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: captured.update(app=app, **kwargs))
    run_server(runtime)
    assert captured["app"] is runtime.app
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8000
    assert captured["access_log"] is False
    assert "api_token" not in captured
