"""Start the local APIs, credential-holding gateway, and Vite UI."""

from __future__ import annotations

import os
from pathlib import Path
import secrets
import signal
import shutil
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


_HOST = "127.0.0.1"
_APP_PORT = 5173
_ORDINARY_PORT = 8000
_BOOTSTRAP_PORT = 8010
_GATEWAY_PORT = 8020


class StartupError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _checked_path(value: str | Path, code: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise StartupError(code)
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink() or bool(getattr(current, "is_junction", lambda: False)()):
            raise StartupError("local_path_redirected")
    try:
        return path.resolve(strict=False)
    except OSError as exc:
        raise StartupError(code) from exc


def _ensure_disjoint(paths: list[Path]) -> None:
    for index, first in enumerate(paths):
        for second in paths[index + 1:]:
            if first == second or first in second.parents or second in first.parents:
                raise StartupError("local_storage_paths_overlap")


def _state_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") if os.name == "nt" else os.environ.get("XDG_STATE_HOME")
    if base:
        root = Path(base) / ("RAG-PDF" if os.name == "nt" else "rag-pdf")
    else:
        root = Path.home() / ".local" / "state" / "rag-pdf"
    if not root.is_absolute():
        raise StartupError("local_state_path_invalid")
    return root


def _runtime_environment() -> dict[str, str]:
    env = os.environ.copy()
    app_state = _checked_path(env.get("RAG_WORKBENCH_LOCAL_STATE_DIR", str(_state_root())), "local_state_path_invalid")

    source = env.get("RAG_WORKBENCH_DATA_DIR") or env.get("RAG_WORKBENCH_BOOTSTRAP_PDF_SOURCE_DIR")
    storage = env.get("RAG_WORKBENCH_INDEX_STORAGE_ROOT") or env.get("RAG_WORKBENCH_BOOTSTRAP_INDEX_STORAGE_ROOT")
    if not source or not storage:
        raise StartupError("configure_source_and_index_storage_paths")
    source_path = _checked_path(source, "document_source_directory_unavailable")
    storage_path = _checked_path(storage, "index_storage_path_invalid")
    if not source_path.is_dir():
        raise StartupError("document_source_directory_unavailable")
    if env.get("RAG_WORKBENCH_DATA_DIR") and env.get("RAG_WORKBENCH_BOOTSTRAP_PDF_SOURCE_DIR"):
        if Path(env["RAG_WORKBENCH_DATA_DIR"]).resolve() != Path(env["RAG_WORKBENCH_BOOTSTRAP_PDF_SOURCE_DIR"]).resolve():
            raise StartupError("ordinary_bootstrap_source_mismatch")
    if env.get("RAG_WORKBENCH_INDEX_STORAGE_ROOT") and env.get("RAG_WORKBENCH_BOOTSTRAP_INDEX_STORAGE_ROOT"):
        if Path(env["RAG_WORKBENCH_INDEX_STORAGE_ROOT"]).resolve() != Path(env["RAG_WORKBENCH_BOOTSTRAP_INDEX_STORAGE_ROOT"]).resolve():
            raise StartupError("ordinary_bootstrap_storage_mismatch")

    bootstrap_state = _checked_path(env.get("RAG_WORKBENCH_BOOTSTRAP_STATE_DIR", str(app_state / "bootstrap")), "bootstrap_state_path_invalid")
    artifact_dir = _checked_path(env.get("RAG_WORKBENCH_ARTIFACT_STORE_DIR", str(app_state / "artifacts")), "artifact_store_path_invalid")
    catalog_db = _checked_path(env.get("RAG_WORKBENCH_CATALOG_DB_PATH", str(app_state / "catalog.sqlite3")), "catalog_database_path_invalid")
    jobs_db = _checked_path(env.get("RAG_WORKBENCH_JOBS_DB_PATH", str(app_state / "jobs.sqlite3")), "jobs_database_path_invalid")
    _ensure_disjoint([source_path, storage_path, app_state])
    _ensure_disjoint([source_path, storage_path, artifact_dir, catalog_db, jobs_db, bootstrap_state / "bootstrap-jobs.sqlite3"])
    app_state.mkdir(parents=True, exist_ok=True)
    storage_path.mkdir(parents=True, exist_ok=True)
    bootstrap_state.mkdir(parents=True, exist_ok=True)
    env.update({
        "RAG_WORKBENCH_API_TOKEN": secrets.token_urlsafe(48),
        "RAG_WORKBENCH_API_HOST": _HOST,
        "RAG_WORKBENCH_API_PORT": str(_ORDINARY_PORT),
        "RAG_WORKBENCH_DATA_DIR": str(source_path),
        "RAG_WORKBENCH_INDEX_STORAGE_ROOT": str(storage_path),
        "RAG_WORKBENCH_ARTIFACT_STORE_DIR": str(artifact_dir),
        "RAG_WORKBENCH_CATALOG_DB_PATH": str(catalog_db),
        "RAG_WORKBENCH_JOBS_DB_PATH": str(jobs_db),
        "RAG_WORKBENCH_BOOTSTRAP_API_TOKEN": secrets.token_urlsafe(48),
        "RAG_WORKBENCH_BOOTSTRAP_PDF_SOURCE_DIR": str(source_path),
        "RAG_WORKBENCH_BOOTSTRAP_INDEX_STORAGE_ROOT": str(storage_path),
        "RAG_WORKBENCH_BOOTSTRAP_STATE_DIR": str(bootstrap_state),
        "RAG_WORKBENCH_BOOTSTRAP_API_HOST": _HOST,
        "RAG_WORKBENCH_BOOTSTRAP_API_PORT": str(_BOOTSTRAP_PORT),
    })
    env["RAG_LOCAL_ORDINARY_TOKEN"] = env["RAG_WORKBENCH_API_TOKEN"]
    env["RAG_LOCAL_BOOTSTRAP_TOKEN"] = env["RAG_WORKBENCH_BOOTSTRAP_API_TOKEN"]
    env["RAG_LOCAL_APP_ORIGIN"] = f"http://{_HOST}:{_APP_PORT}"
    env["RAG_LOCAL_APP_HOST"] = f"{_HOST}:{_APP_PORT}"
    env["RAG_LOCAL_ORDINARY_TARGET"] = f"http://{_HOST}:{_ORDINARY_PORT}"
    env["RAG_LOCAL_BOOTSTRAP_TARGET"] = f"http://{_HOST}:{_BOOTSTRAP_PORT}"
    env["RAG_LOCAL_GATEWAY_HOST"] = _HOST
    env["RAG_LOCAL_GATEWAY_PORT"] = str(_GATEWAY_PORT)
    return env


def _vite_environment(runtime_env: dict[str, str]) -> dict[str, str]:
    allowed = {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT", "COMSPEC", "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA"}
    env = {key: value for key, value in runtime_env.items() if key.upper() in allowed}
    path_value = next((value for key, value in runtime_env.items() if key.upper() == "PATH"), "")
    if path_value:
        env["PATH"] = path_value
    env["RAG_WORKBENCH_LOCAL_GATEWAY_TARGET"] = f"http://{_HOST}:{_GATEWAY_PORT}"
    env["VITE_RAG_LOCAL_GATEWAY"] = "true"
    return env


def _ordinary_environment(runtime_env: dict[str, str]) -> dict[str, str]:
    env = runtime_env.copy()
    for key in (
        "RAG_WORKBENCH_BOOTSTRAP_API_TOKEN",
        "RAG_WORKBENCH_BOOTSTRAP_PDF_SOURCE_DIR",
        "RAG_WORKBENCH_BOOTSTRAP_INDEX_STORAGE_ROOT",
        "RAG_WORKBENCH_BOOTSTRAP_STATE_DIR",
        "RAG_WORKBENCH_BOOTSTRAP_API_HOST",
        "RAG_WORKBENCH_BOOTSTRAP_API_PORT",
        "RAG_LOCAL_ORDINARY_TOKEN",
        "RAG_LOCAL_BOOTSTRAP_TOKEN",
        "RAG_LOCAL_APP_ORIGIN",
        "RAG_LOCAL_APP_HOST",
        "RAG_LOCAL_ORDINARY_TARGET",
        "RAG_LOCAL_BOOTSTRAP_TARGET",
        "RAG_LOCAL_GATEWAY_HOST",
        "RAG_LOCAL_GATEWAY_PORT",
    ):
        env.pop(key, None)
    return env


def _bootstrap_environment(runtime_env: dict[str, str]) -> dict[str, str]:
    env = runtime_env.copy()
    for key in (
        "RAG_WORKBENCH_API_TOKEN",
        "RAG_WORKBENCH_DATA_DIR",
        "RAG_WORKBENCH_INDEX_STORAGE_ROOT",
        "RAG_WORKBENCH_ARTIFACT_STORE_DIR",
        "RAG_WORKBENCH_CATALOG_DB_PATH",
        "RAG_WORKBENCH_JOBS_DB_PATH",
        "RAG_LOCAL_ORDINARY_TOKEN",
        "RAG_LOCAL_BOOTSTRAP_TOKEN",
        "RAG_LOCAL_APP_ORIGIN",
        "RAG_LOCAL_APP_HOST",
        "RAG_LOCAL_ORDINARY_TARGET",
        "RAG_LOCAL_BOOTSTRAP_TARGET",
        "RAG_LOCAL_GATEWAY_HOST",
        "RAG_LOCAL_GATEWAY_PORT",
    ):
        env.pop(key, None)
    return env


def _gateway_environment(runtime_env: dict[str, str]) -> dict[str, str]:
    allowed = {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT", "COMSPEC", "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "PATH"}
    env = {key: value for key, value in runtime_env.items() if key.upper() in allowed}
    for key in (
        "RAG_LOCAL_ORDINARY_TOKEN",
        "RAG_LOCAL_BOOTSTRAP_TOKEN",
        "RAG_LOCAL_APP_ORIGIN",
        "RAG_LOCAL_APP_HOST",
        "RAG_LOCAL_ORDINARY_TARGET",
        "RAG_LOCAL_BOOTSTRAP_TARGET",
        "RAG_LOCAL_GATEWAY_HOST",
        "RAG_LOCAL_GATEWAY_PORT",
    ):
        env[key] = runtime_env[key]
    return env


def _request(url: str, *, host: str | None = None, timeout: float = 1.0) -> tuple[int, str]:
    headers = {"Host": host} if host else {}
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout) as response:
        body = response.read(4096).decode("utf-8", "replace")
        return response.status, body


def _wait_http(name: str, process: subprocess.Popen, url: str, *, expected: int, host: str | None = None) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise StartupError(f"{name}_exited_before_ready")
        try:
            status, _body = _request(url, host=host)
            if status == expected:
                return
        except (URLError, TimeoutError, OSError):
            pass
        time.sleep(0.2)
    raise StartupError(f"{name}_readiness_timeout")


def _start(name: str, command: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.Popen:
    try:
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  creationflags=creationflags)
    except OSError as exc:
        raise StartupError(f"{name}_start_failed") from exc
    return process


def _request_stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        break_event = getattr(signal, "CTRL_BREAK_EVENT", None)
        if break_event is not None:
            try:
                process.send_signal(break_event)
                return
            except OSError:
                pass
    process.terminate()


def _stop_owned(processes: list[subprocess.Popen]) -> None:
    for process in reversed(processes):
        _request_stop(process)
    for process in reversed(processes):
        if process.poll() is None:
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    frontend = repo / "frontend"
    processes: list[subprocess.Popen] = []
    try:
        runtime_env = _runtime_environment()
        node = shutil.which("node")
        vite = frontend / "node_modules" / "vite" / "bin" / "vite.js"
        if not node or not vite.is_file():
            raise StartupError("node_modules_unavailable_run_npm_ci")

        ordinary = _start("ordinary_api", [sys.executable, "-m", "backend.app"], cwd=repo, env=_ordinary_environment(runtime_env))
        processes.append(ordinary)
        _wait_http("ordinary_api", ordinary, f"http://{_HOST}:{_ORDINARY_PORT}/health", expected=200)
        bootstrap = _start("bootstrap_api", [sys.executable, "-m", "backend.bootstrap_app"], cwd=repo, env=_bootstrap_environment(runtime_env))
        processes.append(bootstrap)
        _wait_http("bootstrap_api", bootstrap, f"http://{_HOST}:{_BOOTSTRAP_PORT}/health", expected=200)
        gateway = _start("local_gateway", [sys.executable, "-m", "backend.local_gateway"], cwd=repo, env=_gateway_environment(runtime_env))
        processes.append(gateway)
        _wait_http("local_gateway", gateway, f"http://{_HOST}:{_GATEWAY_PORT}/gateway-health", expected=200,
                   host=f"{_HOST}:{_APP_PORT}")
        bootstrap_proxy = f"http://{_HOST}:{_APP_PORT}/bootstrap-api/health"
        vite_env = _vite_environment(runtime_env)
        vite_process = _start("vite", [node, str(vite), "--host", _HOST, "--port", str(_APP_PORT), "--strictPort"],
                              cwd=frontend, env=vite_env)
        processes.append(vite_process)
        _wait_http("vite", vite_process, f"http://{_HOST}:{_APP_PORT}/", expected=200)
        _wait_http("vite_bootstrap_proxy", vite_process, bootstrap_proxy, expected=200)
        print(f"RAG-PDF is running at http://{_HOST}:{_APP_PORT}")
        print("Local bearer credentials are generated for this run and remain in server processes.")
        print("Press Ctrl+C to stop the services started by this command.")
        while True:
            for process in processes:
                if process.poll() is not None:
                    raise StartupError("local_service_exited")
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0
    except StartupError as exc:
        print(f"RAG-PDF startup failed: {exc.code}", file=sys.stderr)
        return 2
    finally:
        _stop_owned(processes)


if __name__ == "__main__":
    raise SystemExit(main())
