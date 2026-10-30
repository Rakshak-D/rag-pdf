"""Exercise the real bootstrap API through Vite's dedicated local proxy."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
sys.path.insert(0, str(ROOT))

from backend.config import ResolvedRunConfig
from backend.engine.manifest import build_document_manifest
from backend.experiments.run_plan import build_run_plan
from notebooks.config import Config


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(url: str, token: str | None = None, *, method: str = "GET", payload=None):
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=8) as response:
            raw = response.read().decode()
            try:
                return response.status, json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("proxy_non_json_response") from exc
    except HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as parse_exc:
            raise RuntimeError("proxy_non_json_error_response") from parse_exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("proxy_request_failed") from exc


def _write_pdf(path: Path) -> None:
    import pymupdf

    document = pymupdf.open()
    document.new_page().insert_text((72, 72), "Synthetic bootstrap proxy smoke document.")
    document.save(path)
    document.close()


def _spawn_group() -> dict[str, object]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _stop(process: subprocess.Popen, label: str) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.send_signal(signal.SIGINT)
        process.wait(timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait(timeout=8)
            raise RuntimeError(f"{label}_cleanup_failed") from exc


def _check_unconfigured_proxy(node: str) -> None:
    env = os.environ.copy()
    env.pop("RAG_WORKBENCH_BOOTSTRAP_DEV_PROXY_TARGET", None)
    port = _port()
    vite = subprocess.Popen(
        [node, str(FRONTEND / "node_modules" / "vite" / "bin" / "vite.js"), "--host", "127.0.0.1", "--port", str(port), "--strictPort"],
        cwd=FRONTEND, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **_spawn_group(),
    )
    try:
        deadline = monotonic() + 30
        while monotonic() < deadline:
            if vite.poll() is not None:
                raise RuntimeError("unconfigured_vite_exited")
            try:
                code, body = _request(f"http://127.0.0.1:{port}/bootstrap-api/v1/bootstrap/status")
                if code == 503 and body.get("error", {}).get("code") == "bootstrap_proxy_unconfigured":
                    return
            except RuntimeError:
                pass
            sleep(0.15)
        raise RuntimeError("unconfigured_proxy_diagnostic_missing")
    finally:
        _stop(vite, "unconfigured_vite")


def run_smoke() -> dict[str, object]:
    node = shutil.which("node")
    vite_entry = FRONTEND / "node_modules" / "vite" / "bin" / "vite.js"
    if not node or not vite_entry.is_file():
        raise RuntimeError("node_or_vite_unavailable_run_npm_ci")

    token = "bootstrap-vite-smoke-token-0123456789-abcdef"
    with tempfile.TemporaryDirectory(prefix="rag-bootstrap-vite-") as directory:
        root = Path(directory)
        source, state, storage = root / "pdf-source", root / "job-state", root / "index-storage"
        source.mkdir(); state.mkdir()
        _write_pdf(source / "synthetic.pdf")
        config = ResolvedRunConfig.from_legacy(Config(
            pdf_dir=str(source), images_dir=str(root / "images"), database_dir=str(root / "database"),
            results_dir=str(root / "results"), model_comparison_results_dir=str(root / "comparisons"),
            retrieval_results_dir=str(root / "retrieval-results"), reset_collections_on_start=False,
        ))
        manifest = build_document_manifest(source)
        plan = build_run_plan(config, manifest.content_hash)
        backend_port, vite_port = _port(), _port()
        backend_env = os.environ.copy()
        backend_env.update({
            "RAG_WORKBENCH_BOOTSTRAP_API_TOKEN": token,
            "RAG_WORKBENCH_BOOTSTRAP_PDF_SOURCE_DIR": str(source),
            "RAG_WORKBENCH_BOOTSTRAP_INDEX_STORAGE_ROOT": str(storage),
            "RAG_WORKBENCH_BOOTSTRAP_STATE_DIR": str(state),
            "RAG_WORKBENCH_BOOTSTRAP_API_HOST": "127.0.0.1",
            "RAG_WORKBENCH_BOOTSTRAP_API_PORT": str(backend_port),
        })
        vite_env = os.environ.copy()
        vite_env["RAG_WORKBENCH_BOOTSTRAP_DEV_PROXY_TARGET"] = f"http://127.0.0.1:{backend_port}"
        backend = subprocess.Popen(
            [sys.executable, "-m", "backend.bootstrap_app"], cwd=ROOT, env=backend_env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **_spawn_group(),
        )
        vite = None
        try:
            vite = subprocess.Popen(
                [node, str(vite_entry), "--host", "127.0.0.1", "--port", str(vite_port), "--strictPort"],
                cwd=FRONTEND, env=vite_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **_spawn_group(),
            )
            base = f"http://127.0.0.1:{vite_port}"
            deadline = monotonic() + 40
            while monotonic() < deadline:
                if backend.poll() is not None:
                    raise RuntimeError("bootstrap_server_exited")
                if vite.poll() is not None:
                    raise RuntimeError("vite_server_exited")
                try:
                    code, body = _request(base + "/bootstrap-api/health")
                    if code == 200 and body.get("status") == "ok":
                        break
                except RuntimeError:
                    pass
                sleep(0.15)
            else:
                raise RuntimeError("vite_proxy_startup_timeout")

            result: dict[str, object] = {"health_proxy": "200 unauthenticated liveness"}
            code, unauthorized = _request(base + "/bootstrap-api/v1/bootstrap/status")
            if code != 401:
                raise RuntimeError("bootstrap_authentication_proxy_failed")
            code, status = _request(base + "/bootstrap-api/v1/bootstrap/status", token)
            if code != 200 or status.get("mode") != "bootstrap":
                raise RuntimeError("bootstrap_status_proxy_failed")
            result["authenticated_status"] = "200 bootstrap mode; bearer token accepted only on protected proxy request"

            code, preview = _request(base + "/bootstrap-api/v1/bootstrap/preview", token, method="POST", payload={"resolved_config": config.model_dump(mode="json")})
            if code != 200 or preview.get("manifest_hash") != manifest.content_hash or preview.get("index_fingerprint") != plan.index_fingerprint:
                raise RuntimeError("bootstrap_preview_proxy_identity_failed")
            result["preview"] = {"document_count": preview["document_count"], "manifest_match": True, "fingerprint_match": True}

            code, mismatch = _request(base + "/bootstrap-api/v1/bootstrap/jobs", token, method="POST", payload={
                "resolved_config": config.model_dump(mode="json"), "expected_manifest_hash": "0" * 64,
                "expected_index_fingerprint": plan.index_fingerprint, "client_request_id": "vite-smoke-request-0001",
            })
            if code != 409 or mismatch.get("error", {}).get("code") != "manifest_changed":
                raise RuntimeError("bootstrap_mismatch_proxy_failed")
            result["identity_mismatch"] = "409 manifest confirmation rejected"
            code, jobs = _request(base + "/bootstrap-api/v1/bootstrap/jobs?limit=25&offset=0", token)
            if code != 200 or jobs.get("jobs") != []:
                raise RuntimeError("bootstrap_job_inspection_proxy_failed")
            result["job_inspection"] = "200 authenticated empty history; no build submitted"
        finally:
            if vite is not None:
                _stop(vite, "vite")
            _stop(backend, "bootstrap_server")
        result["shutdown"] = "Vite and bootstrap server stopped"
        result["scope"] = "Real HTTP through Vite to the bootstrap API; preview only, no Chroma build, no browser automation."
        _check_unconfigured_proxy(node)
        result["missing_proxy_configuration"] = "503 bootstrap_proxy_unconfigured; no fallback to experiment API"
        return result


def main() -> int:
    try:
        output = run_smoke()
    except Exception as exc:
        code = str(exc).split(":", 1)[0]
        print(f"Bootstrap Vite proxy smoke failed: {code}", file=sys.stderr)
        return 1
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
