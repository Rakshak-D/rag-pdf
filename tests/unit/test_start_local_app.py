from __future__ import annotations

from pathlib import Path
import subprocess
import signal

import pytest

from scripts import start_local_app


def test_windows_services_start_in_private_process_groups(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(start_local_app.os, "name", "nt")
    calls = {}

    class Process:
        pass

    def fake_popen(command, **kwargs):
        calls["command"] = command
        calls.update(kwargs)
        return Process()

    monkeypatch.setattr(start_local_app.subprocess, "Popen", fake_popen)
    process = start_local_app._start("ordinary_api", ["python", "-m", "backend.app"], cwd=tmp_path, env={})

    assert isinstance(process, Process)
    assert calls["creationflags"] == subprocess.CREATE_NEW_PROCESS_GROUP


def test_windows_shutdown_sends_ctrl_break_before_waiting(monkeypatch):
    monkeypatch.setattr(start_local_app.os, "name", "nt")
    events = []

    class Process:
        returncode = None

        def poll(self):
            return self.returncode

        def send_signal(self, value):
            events.append(("signal", value))

        def terminate(self):
            raise AssertionError("graceful process-group signal should be used")

        def wait(self, timeout):
            events.append(("wait", timeout))
            self.returncode = 0

    process = Process()
    start_local_app._stop_owned([process])

    assert events == [
        ("signal", signal.CTRL_BREAK_EVENT),
        ("wait", 8),
    ]


def test_shutdown_falls_back_to_owned_process_termination_after_timeout(monkeypatch):
    monkeypatch.setattr(start_local_app.os, "name", "posix")
    events = []

    class Process:
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            events.append("terminate")

        def wait(self, timeout):
            events.append(("wait", timeout))
            if len([event for event in events if isinstance(event, tuple)]) == 1:
                raise subprocess.TimeoutExpired("owned-child", timeout)
            self.returncode = -9

        def kill(self):
            events.append("kill")

    start_local_app._stop_owned([Process()])
    assert events == ["terminate", ("wait", 8), "kill", ("wait", 5)]


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


@pytest.mark.parametrize("offset,expected", [(0, (5173,8000,8010,8020)), (100,(5273,8100,8110,8120))])
def test_port_offset_preserves_distinct_loopback_service_ports(offset, expected):
    assert start_local_app._port_values(offset) == expected


@pytest.mark.parametrize("offset", [-1, 10001, True, "100"])
def test_invalid_port_offset_is_rejected(offset):
    with pytest.raises(start_local_app.StartupError, match="local_port_offset_invalid"):
        start_local_app._port_values(offset)


def test_port_collision_is_rejected_without_starting_children():
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if start_local_app.os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", 0)); listener.listen()
        with pytest.raises(start_local_app.StartupError, match="local_ports_in_use"):
            start_local_app._check_ports((listener.getsockname()[1],))
