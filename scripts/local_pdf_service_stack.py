"""Run isolated loopback bootstrap, gateway and Vite services for PDF viewer acceptance."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]
NASA_SOURCE = ROOT / "data" / "pdf" / "mars-science-laboratory.pdf"
NASA_SHA256 = "2baee40744fc1c35b3ad015f8672a45710ac6bd9124a4041cfc0d7bfba4e10f6"
STOP = False


def _stop(_signum: int, _frame: object) -> None:
    global STOP
    STOP = True


def _port(environment_name: str) -> int:
    configured = os.environ.get(environment_name)
    if configured is not None:
        try:
            port = int(configured)
        except ValueError as exc:
            raise RuntimeError("local service port configuration is invalid") from exc
        if not 1 <= port <= 65535:
            raise RuntimeError("local service port configuration is invalid")
        with socket.socket() as listener:
            try:
                listener.bind(("127.0.0.1", port))
            except OSError as exc:
                raise RuntimeError("a required loopback port is already in use") from exc
        return port
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _base_environment() -> dict[str, str]:
    keys = {
        "path", "systemroot", "windir", "systemdrive", "homedrive", "homepath",
        "temp", "tmp", "userprofile", "home", "appdata", "localappdata", "comspec",
    }
    return {key: value for key, value in os.environ.items() if key.lower() in keys}


def _start(command: list[str], cwd: Path, env: dict[str, str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        start_new_session=(os.name == "posix"),
    )


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=4)


def _read(
    url: str,
    *,
    timeout: float = 2.0,
    accept: str = "application/json, application/pdf",
) -> tuple[int, Any, dict[str, str]]:
    opener = build_opener(ProxyHandler({}))
    request = Request(url, headers={"Accept": accept})
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(16 * 1024 * 1024 + 1)
            if len(body) > 16 * 1024 * 1024:
                raise RuntimeError("bounded response exceeded")
            content_type = response.headers.get_content_type()
            parsed = json.loads(body) if content_type == "application/json" else body
            return response.status, parsed, {key.lower(): value for key, value in response.headers.items()}
    except HTTPError as response:
        body = response.read(64 * 1024)
        try:
            parsed = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = None
        return response.code, parsed, {key.lower(): value for key, value in response.headers.items()}
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("loopback service request failed") from exc


def _wait(
    url: str,
    processes: list[subprocess.Popen[bytes]],
    service: str,
    timeout: float = 30.0,
    accept: str = "application/json, application/pdf",
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(process.poll() is not None for process in processes):
            raise RuntimeError(f"{service} service exited during startup")
        try:
            status, _body, _headers = _read(url, accept=accept)
            if status == 200:
                return
        except RuntimeError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"{service} service did not become ready")


def _wait_listener(host: str, port: int, processes: list[subprocess.Popen[bytes]], service: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(process.poll() is not None for process in processes):
            raise RuntimeError(f"{service} service exited during startup")
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"{service} service did not become ready")


def _api_checks(app_origin: str, source_file: Path, original: bytes) -> dict[str, Any]:
    base = f"{app_origin}/bootstrap-api/v1/bootstrap/documents"
    status, inventory, _headers = _read(f"{base}?limit=50&offset=0")
    if status != 200 or not isinstance(inventory, dict):
        raise RuntimeError("real document inventory was unavailable")
    documents = inventory.get("documents")
    if inventory.get("inventory_status") != "available" or inventory.get("total") != 1 or not isinstance(documents, list) or len(documents) != 1:
        raise RuntimeError("isolated source inventory did not contain exactly the controlled NASA PDF")
    document = documents[0]
    document_id = document.get("document_id")
    manifest_hash = inventory.get("manifest_hash")
    expected_document_id = "doc_" + hashlib.sha256(
        f"{document.get('relative_path')}\0{document.get('sha256')}".encode("utf-8")
    ).hexdigest()
    if (not isinstance(document_id, str) or not document_id.startswith("doc_") or
            document.get("relative_path") != NASA_SOURCE.name or
            document.get("sha256") != NASA_SHA256 or
            document_id != expected_document_id or
            not isinstance(manifest_hash, str) or len(manifest_hash) != 64):
        raise RuntimeError("real source inventory identity did not match the verified NASA PDF")

    content_url = f"{base}/{document_id}/content?manifest_hash={manifest_hash}"
    status, content, headers = _read(content_url)
    if (status != 200 or not isinstance(content, bytes) or
            hashlib.sha256(content).hexdigest() != NASA_SHA256 or
            headers.get("x-rag-document-id") != document_id or
            headers.get("x-rag-manifest-hash") != manifest_hash or
            headers.get("x-rag-document-sha256") != NASA_SHA256 or
            headers.get("x-rag-page-count") != "4" or
            headers.get("content-type", "").split(";", 1)[0].lower() != "application/pdf" or
            headers.get("cache-control", "").lower() != "no-store"):
        raise RuntimeError("authenticated loopback gateway returned inconsistent PDF bytes or identity")

    invalid_id = f"doc_{'f' * 64}"
    status, error, _headers = _read(f"{base}/{invalid_id}/content?manifest_hash={manifest_hash}")
    if status != 404 or not isinstance(error, dict) or error.get("error", {}).get("code") != "document_not_found":
        raise RuntimeError("unknown document identity did not fail without borrowing source content")

    stale_manifest = ("0" if manifest_hash[0] != "0" else "1") + manifest_hash[1:]
    status, error, _headers = _read(f"{base}/{document_id}/content?manifest_hash={stale_manifest}")
    if status != 409 or not isinstance(error, dict) or error.get("error", {}).get("code") != "document_manifest_changed":
        raise RuntimeError("stale manifest did not produce the documented conflict")

    changed_bytes = original + b"\n% isolated M9-B2 change check\n"
    try:
        source_file.write_bytes(changed_bytes)
        status, error, _headers = _read(content_url)
        if status != 409 or not isinstance(error, dict) or error.get("error", {}).get("code") != "document_manifest_changed":
            raise RuntimeError("changed source did not invalidate its previous manifest identity")
        status, changed_inventory, _headers = _read(f"{base}?limit=50&offset=0")
        if status != 200 or changed_inventory.get("manifest_hash") == manifest_hash:
            raise RuntimeError("source mutation did not produce a changed manifest")
    finally:
        source_file.write_bytes(original)

    hold = source_file.parent.parent / "source-held-for-missing-document-check.pdf"
    source_file.replace(hold)
    try:
        status, empty_inventory, _headers = _read(f"{base}?limit=50&offset=0")
        if status != 200 or empty_inventory.get("total") != 0 or empty_inventory.get("documents") != []:
            raise RuntimeError("missing source was not reflected in the actual inventory")
        current_manifest = empty_inventory.get("manifest_hash")
        if not isinstance(current_manifest, str) or len(current_manifest) != 64:
            raise RuntimeError("empty source inventory omitted its manifest identity")
        status, error, _headers = _read(f"{base}/{document_id}/content?manifest_hash={current_manifest}")
        if status != 404 or not isinstance(error, dict) or error.get("error", {}).get("code") != "document_not_found":
            raise RuntimeError("missing PDF did not produce the documented unavailable result")
    finally:
        hold.replace(source_file)

    status, restored, _headers = _read(f"{base}?limit=50&offset=0")
    if status != 200 or restored.get("manifest_hash") != manifest_hash or restored.get("documents", [{}])[0].get("document_id") != document_id:
        raise RuntimeError("isolated source did not return to its original verified identity")

    return {
        "inventory_documents": 1,
        "document_id": document_id,
        "manifest_hash": manifest_hash,
        "pdf_sha256": NASA_SHA256,
        "page_count": 4,
        "gateway_content_bytes": len(content),
        "invalid_id": "404 document_not_found",
        "stale_manifest": "409 document_manifest_changed",
        "changed_source": "409 document_manifest_changed; original bytes restored",
        "missing_source": "empty inventory; 404 document_not_found; original bytes restored",
        "index_builds_submitted": 0,
        "model_requests": 0,
    }


def serve() -> int:
    if not NASA_SOURCE.is_file():
        raise RuntimeError("tracked NASA source PDF is missing")
    original = NASA_SOURCE.read_bytes()
    if hashlib.sha256(original).hexdigest() != NASA_SHA256:
        raise RuntimeError("tracked NASA source PDF checksum mismatch")

    source_port = _port("M9B2_BOOTSTRAP_PORT")
    gateway_port = _port("M9B2_GATEWAY_PORT")
    vite_port = _port("M9B2_VITE_PORT")
    ordinary_port = _port("M9B2_ORDINARY_PORT")
    if len({source_port, gateway_port, vite_port, ordinary_port}) != 4:
        raise RuntimeError("local service ports must be distinct")

    global STOP
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    with tempfile.TemporaryDirectory(prefix="rag-pdf-b2-local-service-") as temporary:
        root = Path(temporary)
        source_root = root / "source"
        state_dir = root / "bootstrap-state"
        storage_parent = root / "owned-storage"
        storage_root = storage_parent / "index"
        source_root.mkdir()
        state_dir.mkdir()
        storage_parent.mkdir()
        source_file = source_root / NASA_SOURCE.name
        source_file.write_bytes(original)

        bootstrap_token = secrets.token_urlsafe(36)
        ordinary_token = secrets.token_urlsafe(36)
        app_origin = f"http://127.0.0.1:{vite_port}"
        bootstrap_origin = f"http://127.0.0.1:{source_port}"
        gateway_origin = f"http://127.0.0.1:{gateway_port}"
        base_env = _base_environment()
        bootstrap_env = {
            **base_env,
            "RAG_WORKBENCH_BOOTSTRAP_API_TOKEN": bootstrap_token,
            "RAG_WORKBENCH_BOOTSTRAP_PDF_SOURCE_DIR": str(source_root),
            "RAG_WORKBENCH_BOOTSTRAP_INDEX_STORAGE_ROOT": str(storage_root),
            "RAG_WORKBENCH_BOOTSTRAP_STATE_DIR": str(state_dir),
            "RAG_WORKBENCH_BOOTSTRAP_API_HOST": "127.0.0.1",
            "RAG_WORKBENCH_BOOTSTRAP_API_PORT": str(source_port),
        }
        gateway_env = {
            **base_env,
            "RAG_LOCAL_ORDINARY_TOKEN": ordinary_token,
            "RAG_LOCAL_BOOTSTRAP_TOKEN": bootstrap_token,
            "RAG_LOCAL_APP_ORIGIN": app_origin,
            "RAG_LOCAL_APP_HOST": f"127.0.0.1:{vite_port}",
            "RAG_LOCAL_ORDINARY_TARGET": f"http://127.0.0.1:{ordinary_port}",
            "RAG_LOCAL_BOOTSTRAP_TARGET": bootstrap_origin,
            "RAG_LOCAL_GATEWAY_HOST": "127.0.0.1",
            "RAG_LOCAL_GATEWAY_PORT": str(gateway_port),
        }
        vite_env = {
            **base_env,
            "VITE_RAG_LOCAL_GATEWAY": "true",
            "RAG_WORKBENCH_LOCAL_GATEWAY_TARGET": gateway_origin,
        }

        processes: list[subprocess.Popen[bytes]] = []
        try:
            processes.append(_start([sys.executable, "-m", "backend.bootstrap_app"], ROOT, bootstrap_env))
            _wait(f"{bootstrap_origin}/health", processes, "bootstrap")
            processes.append(_start([sys.executable, "-m", "backend.local_gateway"], ROOT, gateway_env))
            _wait_listener("127.0.0.1", gateway_port, processes, "local gateway")
            node = shutil.which("node")
            if not node:
                raise RuntimeError("Node.js executable is unavailable")
            vite_entry = ROOT / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"
            if not vite_entry.is_file():
                raise RuntimeError("locked frontend dependencies are not installed")
            processes.append(_start([
                node, str(vite_entry), "--host", "127.0.0.1", "--port", str(vite_port), "--strictPort",
            ], ROOT / "frontend", vite_env))
            _wait(app_origin, processes, "Vite viewer", accept="text/html")
            smoke = _api_checks(app_origin, source_file, original)
            if storage_root.exists() and any(storage_root.iterdir()):
                raise RuntimeError("isolated index storage changed without an explicit build")
            print(f"LOCAL_DOCUMENT_SERVICE_SMOKE {json.dumps(smoke, sort_keys=True)}", flush=True)
            print("LOCAL_DOCUMENT_VIEWER_READY", flush=True)
            while not STOP and all(process.poll() is None for process in processes):
                time.sleep(0.25)
            if any(process.poll() is not None for process in processes):
                raise RuntimeError("a local service exited during browser acceptance")
        finally:
            for process in reversed(processes):
                _stop_process(process)
            if storage_root.exists() and any(storage_root.iterdir()):
                raise RuntimeError("isolated index storage was unexpectedly populated")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(serve())
    except Exception as exc:
        # Do not print environment values, tokens, absolute paths, or source contents.
        print(f"LOCAL_DOCUMENT_SERVICE_SMOKE_FAILED {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
