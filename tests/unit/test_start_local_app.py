from __future__ import annotations

from pathlib import Path

import pytest

from scripts import start_local_app


def test_launcher_generates_distinct_server_credentials_and_preserves_configured_roots(tmp_path: Path, monkeypatch):
    source = tmp_path / "pdf-source"
    storage = tmp_path / "index-storage"
    state = tmp_path / "local-state"
    source.mkdir()
    storage.mkdir()
    monkeypatch.setenv("RAG_WORKBENCH_DATA_DIR", str(source))
    monkeypatch.setenv("RAG_WORKBENCH_INDEX_STORAGE_ROOT", str(storage))
    monkeypatch.setenv("RAG_WORKBENCH_LOCAL_STATE_DIR", str(state))
    monkeypatch.setenv("RAG_WORKBENCH_API_TOKEN", "old-ordinary-token")
    monkeypatch.setenv("RAG_WORKBENCH_BOOTSTRAP_API_TOKEN", "old-bootstrap-token")

    env = start_local_app._runtime_environment()
    assert env["RAG_WORKBENCH_DATA_DIR"] == str(source)
    assert env["RAG_WORKBENCH_BOOTSTRAP_PDF_SOURCE_DIR"] == str(source)
    assert env["RAG_WORKBENCH_INDEX_STORAGE_ROOT"] == str(storage)
    assert env["RAG_WORKBENCH_BOOTSTRAP_INDEX_STORAGE_ROOT"] == str(storage)
    assert env["RAG_WORKBENCH_API_TOKEN"] != "old-ordinary-token"
    assert env["RAG_WORKBENCH_BOOTSTRAP_API_TOKEN"] != "old-bootstrap-token"
    assert env["RAG_WORKBENCH_API_TOKEN"] != env["RAG_WORKBENCH_BOOTSTRAP_API_TOKEN"]
    assert env["RAG_LOCAL_ORDINARY_TOKEN"] == env["RAG_WORKBENCH_API_TOKEN"]
    assert env["RAG_LOCAL_BOOTSTRAP_TOKEN"] == env["RAG_WORKBENCH_BOOTSTRAP_API_TOKEN"]
    assert env["RAG_LOCAL_GATEWAY_HOST"] == "127.0.0.1"
    assert (state / "bootstrap" / "bootstrap-jobs.sqlite3").parent.is_dir()

    ordinary = start_local_app._ordinary_environment(env)
    bootstrap = start_local_app._bootstrap_environment(env)
    gateway = start_local_app._gateway_environment(env)
    assert ordinary["RAG_WORKBENCH_API_TOKEN"] == env["RAG_WORKBENCH_API_TOKEN"]
    assert "RAG_WORKBENCH_BOOTSTRAP_API_TOKEN" not in ordinary
    assert "RAG_LOCAL_BOOTSTRAP_TOKEN" not in ordinary
    assert bootstrap["RAG_WORKBENCH_BOOTSTRAP_API_TOKEN"] == env["RAG_WORKBENCH_BOOTSTRAP_API_TOKEN"]
    assert "RAG_WORKBENCH_API_TOKEN" not in bootstrap
    assert "RAG_LOCAL_ORDINARY_TOKEN" not in bootstrap
    assert gateway["RAG_LOCAL_ORDINARY_TOKEN"] == env["RAG_WORKBENCH_API_TOKEN"]
    assert gateway["RAG_LOCAL_BOOTSTRAP_TOKEN"] == env["RAG_WORKBENCH_BOOTSTRAP_API_TOKEN"]
    assert "RAG_WORKBENCH_API_TOKEN" not in gateway
    assert "RAG_WORKBENCH_BOOTSTRAP_API_TOKEN" not in gateway


def test_vite_environment_contains_no_credentials_or_user_vite_variables():
    runtime = {
        "PATH": "C:\\Node",
        "RAG_WORKBENCH_API_TOKEN": "ordinary-secret",
        "RAG_WORKBENCH_BOOTSTRAP_API_TOKEN": "bootstrap-secret",
        "RAG_LOCAL_ORDINARY_TOKEN": "ordinary-secret",
        "VITE_OTHER_SECRET": "must-not-pass",
        "SystemRoot": "C:\\Windows",
    }
    env = start_local_app._vite_environment(runtime)
    assert env["VITE_RAG_LOCAL_GATEWAY"] == "true"
    assert env["PATH"] == "C:\\Node"
    assert env["RAG_WORKBENCH_LOCAL_GATEWAY_TARGET"] == "http://127.0.0.1:8020"
    assert all("TOKEN" not in key.upper() for key in env)
    assert "VITE_OTHER_SECRET" not in env
    assert "ordinary-secret" not in repr(env)
    assert "bootstrap-secret" not in repr(env)


def test_launcher_refuses_missing_or_overlapping_roots_without_creating_them(tmp_path: Path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setenv("RAG_WORKBENCH_DATA_DIR", str(source))
    monkeypatch.setenv("RAG_WORKBENCH_INDEX_STORAGE_ROOT", str(source / "indexes"))
    monkeypatch.setenv("RAG_WORKBENCH_LOCAL_STATE_DIR", str(tmp_path / "state"))
    with pytest.raises(start_local_app.StartupError, match="local_storage_paths_overlap"):
        start_local_app._runtime_environment()
    assert not (source / "indexes").exists()


def test_launcher_refuses_state_root_nested_with_source(tmp_path: Path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setenv("RAG_WORKBENCH_DATA_DIR", str(source))
    monkeypatch.setenv("RAG_WORKBENCH_INDEX_STORAGE_ROOT", str(tmp_path / "indexes"))
    monkeypatch.setenv("RAG_WORKBENCH_LOCAL_STATE_DIR", str(source / "state"))
    with pytest.raises(start_local_app.StartupError, match="local_storage_paths_overlap"):
        start_local_app._runtime_environment()


def test_readiness_failure_identifies_only_the_local_service_role(monkeypatch):
    class ExitedProcess:
        @staticmethod
        def poll():
            return 17

    with pytest.raises(start_local_app.StartupError) as error:
        start_local_app._wait_http("ordinary_api", ExitedProcess(), "http://127.0.0.1:8000/health", expected=200)
    assert error.value.code == "ordinary_api_exited_before_ready"
    assert "127.0.0.1" not in str(error.value)


def test_readiness_timeout_identifies_only_the_local_service_role(monkeypatch):
    clock = iter([0, 0, 31])
    monkeypatch.setattr(start_local_app.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(start_local_app.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(start_local_app, "_request", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("private path")))

    class RunningProcess:
        @staticmethod
        def poll():
            return None

    with pytest.raises(start_local_app.StartupError) as error:
        start_local_app._wait_http("bootstrap_api", RunningProcess(), "http://127.0.0.1:8010/health", expected=200)
    assert error.value.code == "bootstrap_api_readiness_timeout"
    assert "private path" not in str(error.value)
