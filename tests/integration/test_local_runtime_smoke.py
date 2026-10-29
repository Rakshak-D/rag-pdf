from __future__ import annotations

import socket
import tempfile

import pytest

from scripts import local_runtime_smoke as smoke


def test_real_server_smoke_uses_owned_temporary_files_and_cleans_up(tmp_path, monkeypatch):
    sentinel = tmp_path / "user-file.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    result = smoke.run_smoke(timeout=30)
    assert "missing report startup: rejected" in result
    assert "synthetic report compatibility: ok (store unverified)" in result
    assert "empty jobs and artifacts: ok" in result
    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert list(tmp_path.iterdir()) == [sentinel]


def test_unavailable_port_and_invalid_limits_are_rejected():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        with pytest.raises(smoke.SmokeError, match="port_unavailable"):
            smoke.run_smoke(port=listener.getsockname()[1])
    with pytest.raises(smoke.SmokeError, match="timeout_invalid"):
        smoke.run_smoke(timeout=0)


def test_startup_timeout_is_bounded(monkeypatch):
    class Process:
        def poll(self):
            return None

    ticks = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(smoke.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(smoke.time, "sleep", lambda _: None)
    monkeypatch.setattr(smoke, "request", lambda *_args, **_kw: (_ for _ in ()).throw(OSError()))
    with pytest.raises(smoke.SmokeError, match="server_start_timeout"):
        smoke.wait_for_server(Process(), 1234, 1)


def test_failure_terminates_child_and_removes_only_owned_files(tmp_path, monkeypatch):
    sentinel = tmp_path / "keep.txt"
    sentinel.write_text("unchanged", encoding="utf-8")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    original_popen = smoke.subprocess.Popen
    processes = []

    def track(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(smoke.subprocess, "Popen", track)
    original_expect = smoke._expect

    def fail_at_health(condition, code):
        if code == "health_failed":
            raise smoke.SmokeError("controlled_failure")
        original_expect(condition, code)

    monkeypatch.setattr(smoke, "_expect", fail_at_health)
    try:
        with pytest.raises(smoke.SmokeError, match="controlled_failure"):
            smoke.run_smoke(timeout=30)
        assert processes and all(process.poll() is not None for process in processes)
        assert sentinel.read_text(encoding="utf-8") == "unchanged"
        assert list(tmp_path.iterdir()) == [sentinel]
    finally:
        for process in processes:
            smoke.stop_process(process)
