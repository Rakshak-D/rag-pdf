"""Supervise the real launcher on a disposable Linux CI runner, without models."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import ProxyHandler, Request, build_opener

import psutil

ROOT = Path(__file__).resolve().parents[1]
SHA = "65ba3e145dcb1ab75a6d89717bc46791fda88b1957b4d63c258c044f5ad7a77c"
HOST = "127.0.0.1"
BASE_PORTS = (5173, 8000, 8010, 8020)


def read(url: str, host: str | None = None) -> tuple[int, bytes]:
    request = Request(url, headers={"Host": host} if host else {})
    with build_opener(ProxyHandler({})).open(request, timeout=2) as response:
        return response.status, response.read(1024 * 1024)


def resource_snapshot(root: Path) -> dict:
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    values = {"available_bytes": mem.available, "swap_total": swap.total,
              "swap_free": swap.free, "disk_free": shutil.disk_usage(root).free}
    with open("/proc/meminfo", encoding="ascii") as stream:
        for line in stream:
            if line.startswith(("CommitLimit:", "Committed_AS:")):
                key, value = line.split(":", 1)
                values[key] = int(value.split()[0]) * 1024
    values["commit_headroom"] = max(0, values["CommitLimit"] - values["Committed_AS"])
    return values


def choose_ports() -> tuple[int, tuple[int, ...]]:
    for offset in range(2000, 10001, 17):
        ports = tuple(p + offset for p in BASE_PORTS)
        sockets = []
        try:
            for port in ports:
                sock = socket.socket()
                sockets.append(sock)
                sock.bind((HOST, port))
            return offset, ports
        except OSError:
            pass
        finally:
            for sock in sockets:
                sock.close()
    raise RuntimeError("no unused launcher port set")


def main() -> None:
    if sys.platform != "linux" or os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuntimeError("hosted Ubuntu runner required")
    if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip() != os.environ["GITHUB_SHA"]:
        raise RuntimeError("checkout does not match workflow commit")
    pdf = ROOT / "examples/demo/Orbit_Field_Notes.pdf"
    if hashlib.sha256(pdf.read_bytes()).hexdigest() != SHA:
        raise RuntimeError("tracked PDF digest mismatch")
    model_processes = [p.info["name"] for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower().startswith("ollama")]
    if model_processes:
        raise RuntimeError("unexpected Ollama process before startup")
    offset, ports = choose_ports()
    print("HOSTED_PREFLIGHT", json.dumps({"resources": resource_snapshot(ROOT), "ports": ports,
          "python": sys.version.split()[0], "node": subprocess.check_output(["node", "--version"], text=True).strip(),
          "model_processes": len(model_processes)}), flush=True)
    with tempfile.TemporaryDirectory(prefix="rag-pdf-hosted-launcher-") as temporary:
        root = Path(temporary)
        source, storage, ordinary, bootstrap = [root / name for name in
            ("pdf-source", "index-storage", "ordinary-runtime-state", "bootstrap-runtime-state")]
        for path in (source, storage, ordinary, bootstrap):
            path.mkdir()
        shutil.copyfile(pdf, source / pdf.name)
        if hashlib.sha256((source / pdf.name).read_bytes()).hexdigest() != SHA:
            raise RuntimeError("copied PDF digest mismatch")
        env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "LANG", "LD_LIBRARY_PATH", "PLAYWRIGHT_BROWSERS_PATH") if key in os.environ}
        env.update({"PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1", "HF_HUB_OFFLINE": "1",
                    "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "ANONYMIZED_TELEMETRY": "False",
                    "RAG_WORKBENCH_DATA_DIR": str(source), "RAG_WORKBENCH_INDEX_STORAGE_ROOT": str(storage),
                    "RAG_WORKBENCH_LOCAL_STATE_DIR": str(ordinary), "RAG_WORKBENCH_BOOTSTRAP_STATE_DIR": str(bootstrap),
                    "RAG_WORKBENCH_ARTIFACT_STORE_DIR": str(ordinary / "artifacts"),
                    "RAG_WORKBENCH_CATALOG_DB_PATH": str(ordinary / "catalog.sqlite3"),
                    "RAG_WORKBENCH_JOBS_DB_PATH": str(ordinary / "jobs.sqlite3")})
        before = resource_snapshot(root)
        print("HOSTED_RESOURCE_GATE", json.dumps(before), flush=True)
        if before["available_bytes"] < 4 * 1024**3:
            raise RuntimeError("HOSTED_RESOURCE_BLOCKED: available physical RAM below 4 GiB")
        launcher = None
        children = []
        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as log:
            try:
                launcher = subprocess.Popen([sys.executable, "scripts/start_local_app.py", "--port-offset", str(offset)],
                                            cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                            start_new_session=True)
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    log.seek(0)
                    output = log.read()
                    if f"Source checkout: {ROOT}" in output and "RAG-PDF is running at" in output:
                        break
                    if launcher.poll() is not None:
                        raise RuntimeError("launcher exited before readiness")
                    time.sleep(.25)
                else:
                    raise RuntimeError("launcher readiness timed out")
                children = psutil.Process(launcher.pid).children(recursive=True)
                listeners = [c for c in psutil.net_connections(kind="inet") if c.status == psutil.CONN_LISTEN and c.laddr.port in ports]
                if len(listeners) != 4 or {c.laddr.port for c in listeners} != set(ports) or any(c.laddr.ip != HOST for c in listeners):
                    raise RuntimeError("launcher listeners missing or exposed beyond loopback")
                if not {c.pid for c in listeners}.issubset({p.pid for p in children}):
                    raise RuntimeError("listener ownership does not match launcher children")
                app, api, boot, gateway = ports
                for port, path, header in ((api, "/health", None), (boot, "/health", None),
                                           (gateway, "/gateway-health", f"{HOST}:{app}"), (app, "/", None)):
                    status, _ = read(f"http://{HOST}:{port}{path}", header)
                    if status != 200:
                        raise RuntimeError("service health failed")
                print("HOSTED_SERVICES_READY", json.dumps({"launcher_pid": launcher.pid,
                      "listeners": [{"pid": c.pid, "address": c.laddr.ip, "port": c.laddr.port} for c in listeners],
                      "health_statuses": [200, 200, 200, 200]}), flush=True)
                browser_env = dict(env, CI="true", M9C2B_APP_URL=f"http://{HOST}:{app}")
                subprocess.run(["node", "node_modules/playwright/cli.js", "test", "--config", "playwright.hosted-launcher.config.ts"],
                               cwd=ROOT / "frontend", env=browser_env, check=True, timeout=180)
                if any(storage.iterdir()):
                    raise RuntimeError("index storage unexpectedly populated")
            finally:
                if launcher and launcher.poll() is None:
                    children = psutil.Process(launcher.pid).children(recursive=True)
                    launcher.send_signal(signal.SIGINT)
                    try:
                        launcher.wait(timeout=35)
                    except subprocess.TimeoutExpired:
                        for child in reversed(children):
                            if child.is_running(): child.kill()
                        launcher.kill()
                        launcher.wait(timeout=5)
                        raise RuntimeError("launcher did not shut down normally")
                if launcher and launcher.returncode != 0:
                    raise RuntimeError(f"launcher exit status {launcher.returncode}")
                remaining = [p.pid for p in children if p.is_running() and p.status() != psutil.STATUS_ZOMBIE]
                ports_left = [c.laddr.port for c in psutil.net_connections(kind="inet") if c.status == psutil.CONN_LISTEN and c.laddr.port in ports]
                if remaining or ports_left:
                    raise RuntimeError(f"shutdown incomplete: child count={len(remaining)}, listener count={len(ports_left)}")
                print("HOSTED_SHUTDOWN_PASS", json.dumps({"children_remaining": 0, "listeners_remaining": 0}), flush=True)
    if root.exists():
        raise RuntimeError("temporary workspace cleanup failed")
    print("HOSTED_TEMP_CLEANUP_PASS", flush=True)


if __name__ == "__main__":
    main()
