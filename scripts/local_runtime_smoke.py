"""Opt-in isolated HTTP smoke test for the local workbench runtime."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config.schema import ResolvedRunConfig
from backend.engine.manifest import build_document_manifest
from backend.experiments.run_plan import RunPlan, build_run_plan
from notebooks.config import Config


class SmokeError(RuntimeError):
    """A bounded diagnostic code without paths or credentials."""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def check_port_available(port: int) -> None:
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise SmokeError("port_invalid")
    try:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
    except OSError as exc:
        raise SmokeError("port_unavailable") from exc


def request(port: int, path: str, *, token: str | None = None, body: dict | None = None) -> tuple[int, dict]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        response = opener.open(req, timeout=3)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        data_bytes = response.read(2 * 1024 * 1024 + 1)
        if len(data_bytes) > 2 * 1024 * 1024:
            raise SmokeError("response_too_large")
        return response.status, json.loads(data_bytes)


def wait_for_server(process: subprocess.Popen, port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SmokeError("server_exited_before_ready")
        try:
            status, payload = request(port, "/health")
            if status == 200 and payload == {"status": "ok", "api_version": "v1"}:
                return
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    raise SmokeError("server_start_timeout")


def stop_process(process: subprocess.Popen | None) -> None:
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def _expect(condition: bool, code: str) -> None:
    if not condition:
        raise SmokeError(code)


def run_smoke(*, vite: bool = False, timeout: float = 30, port: int | None = None) -> list[str]:
    if not 0 < timeout <= 120:
        raise SmokeError("timeout_invalid")
    outcomes: list[str] = []
    backend = proxy = None
    with tempfile.TemporaryDirectory(prefix="rag-workbench-smoke-") as name:
        root = Path(name)
        source = root / "source"; source.mkdir()
        index = root / "index"; index.mkdir()
        state = root / "state"; state.mkdir()
        artifacts = root / "artifacts"
        pdf = source / "synthetic.pdf"; pdf.write_bytes(b"%PDF-1.4\nsynthetic smoke input\n%%EOF\n")
        config = ResolvedRunConfig.from_legacy(Config(reset_collections_on_start=False, pdf_dir="synthetic/source", images_dir="synthetic/images", database_dir="synthetic/database", results_dir="synthetic/results", model_comparison_results_dir="synthetic/comparison", retrieval_results_dir="synthetic/retrieval"))
        manifest = build_document_manifest(source)
        plan = build_run_plan(config, manifest.content_hash)
        report_dir = index / "build-artifacts" / "synthetic-smoke"; report_dir.mkdir(parents=True)
        report = report_dir / "build-report.json"
        payload = {"report_schema_version": "1", "build_id": "synthetic-smoke", "artifact_root": str(report_dir), "report_path": str(report), "index_fingerprint": plan.index_fingerprint, "document_manifest_hash": manifest.content_hash, "status": "completed", "partial_persistence": False, "text_records_persisted": 0, "image_records_persisted": 0}
        with report.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream)
        token = secrets.token_urlsafe(32)
        port = free_port() if port is None else port
        check_port_available(port)
        def safe_request(target_port: int, path: str, **kwargs):
            status, response = request(target_port, path, **kwargs)
            rendered = json.dumps(response, ensure_ascii=False)
            if token in rendered or str(root) in rendered or str(root).replace("\\", "\\\\") in rendered:
                raise SmokeError("response_disclosed_secret_or_path")
            return status, response
        env = os.environ.copy()
        env.update({"RAG_WORKBENCH_API_TOKEN": token, "RAG_WORKBENCH_DATA_DIR": str(source), "RAG_WORKBENCH_ARTIFACT_STORE_DIR": str(artifacts), "RAG_WORKBENCH_CATALOG_DB_PATH": str(state / "catalog.sqlite"), "RAG_WORKBENCH_JOBS_DB_PATH": str(state / "jobs.sqlite"), "RAG_WORKBENCH_INDEX_STORAGE_ROOT": str(index), "RAG_WORKBENCH_BUILD_REPORT_PATH": str(report), "RAG_WORKBENCH_API_HOST": "127.0.0.1", "RAG_WORKBENCH_API_PORT": str(port)})
        env["PYTHONPATH"] = str(ROOT)
        guard = root / "guarded_launch.py"
        guard.write_text(
            "import os, runpy, chromadb\n"
            "from backend.embeddings.text import TextEmbeddingAdapter\n"
            "from backend.embeddings.image import ImageEmbeddingAdapter\n"
            "from backend.engine.reranking import CrossEncoderReranker\n"
            "from backend.engine.generation import LocalGenerationAdapter\n"
            "def forbidden(*args, **kwargs): os._exit(91)\n"
            "chromadb.PersistentClient = forbidden\n"
            "TextEmbeddingAdapter.load = forbidden\n"
            "ImageEmbeddingAdapter.load = forbidden\n"
            "CrossEncoderReranker.load = forbidden\n"
            "LocalGenerationAdapter.generate = forbidden\n"
            "runpy.run_module('backend.app', run_name='__main__')\n",
            encoding="utf-8",
        )
        try:
            missing_env = env.copy(); missing_env["RAG_WORKBENCH_BUILD_REPORT_PATH"] = str(index / "missing" / "build-report.json")
            missing = subprocess.run([sys.executable, "-m", "backend.app"], cwd=ROOT, env=missing_env, capture_output=True, timeout=timeout, check=False)
            _expect(missing.returncode == 2 and b"build_report_missing" in missing.stderr and token.encode() not in missing.stderr and str(root).encode() not in missing.stderr, "missing_report_check_failed")
            outcomes.append("missing report startup: rejected")
            backend = subprocess.Popen([sys.executable, str(guard)], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            wait_for_server(backend, port, timeout)
            _expect(safe_request(port, "/health")[0] == 200, "health_failed"); outcomes.append("health: ok")
            status, response = safe_request(port, "/api/v1/documents/manifest")
            _expect(status == 401 and response == {"error": {"code": "authentication_required"}}, "authentication_failed"); outcomes.append("authentication: ok")
            _expect(safe_request(port, "/api/v1/documents/manifest", token="incorrect-token")[0] == 401, "invalid_token_accepted")
            status, response = safe_request(port, "/api/v1/documents/manifest", token=token)
            _expect(status == 200 and response["schema_version"] == manifest.schema_version and response["content_hash"] == manifest.content_hash and response["entry_count"] == 1 and response["empty"] is False and response["entries"] == [manifest.entries[0].model_dump(mode="json")], "manifest_failed"); outcomes.append("manifest: ok")
            body = {"resolved_config": config.model_dump(mode="json"), "expected_manifest_hash": manifest.content_hash}
            status, response = safe_request(port, "/api/v1/run-plans", token=token, body=body)
            _expect(status == 200, "plan_failed")
            returned = RunPlan.from_serializable_json(json.dumps(response["run_plan"]))
            _expect(returned == plan, "plan_identity_failed"); outcomes.append("run plan: ok")
            readiness = {**body, "workflow": "evaluation"}
            status, response = safe_request(port, "/api/v1/index/readiness", token=token, body=readiness)
            _expect(status == 200 and response["status"] == "report_match" and response["expected_index_fingerprint"] == plan.index_fingerprint and response["store_checked"] is False and response["observed_text_records"] is None and response["observed_image_records"] is None, "report_match_failed"); outcomes.append("synthetic report compatibility: ok (store unverified)")
            unsafe = {**body, "resolved_config": {**body["resolved_config"], "reset_collections_on_start": True}}
            _expect(safe_request(port, "/api/v1/run-plans", token=token, body=unsafe)[0] == 400, "unsafe_config_accepted")
            invalid = {**body, "resolved_config": {**body["resolved_config"], "text_k": 0}}
            _expect(safe_request(port, "/api/v1/run-plans", token=token, body=invalid)[0] == 400, "invalid_config_accepted")
            mismatched_config = {**body["resolved_config"], "text_embed_model": body["resolved_config"]["text_embed_model"] + "-synthetic-mismatch"}
            mismatch_status, mismatch = safe_request(port, "/api/v1/index/readiness", token=token, body={**readiness, "resolved_config": mismatched_config})
            _expect(mismatch_status == 200 and mismatch["status"] == "mismatch" and mismatch["reason_code"] == "identity_mismatch" and mismatch["store_checked"] is False, "report_mismatch_failed")
            pdf.write_bytes(b"changed synthetic source")
            _expect(safe_request(port, "/api/v1/run-plans", token=token, body=body)[1]["error"]["code"] == "manifest_changed", "manifest_change_failed")
            outcomes.append("negative checks: ok")
            for route, key in (("/api/v1/jobs", "jobs"), ("/api/v1/artifacts", "artifacts")):
                status, response = safe_request(port, route, token=token)
                _expect(status == 200 and response[key] == [], "empty_catalog_failed")
            outcomes.append("empty jobs and artifacts: ok")
            if vite:
                node = shutil.which("node")
                vite_script = ROOT / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"
                if not node or not vite_script.is_file():
                    raise SmokeError("vite_unavailable")
                proxy_port = free_port()
                vite_env = os.environ.copy(); vite_env.pop("RAG_WORKBENCH_API_TOKEN", None)
                vite_env["RAG_WORKBENCH_DEV_PROXY_TARGET"] = f"http://127.0.0.1:{port}"
                proxy = subprocess.Popen([node, str(vite_script), "--host", "127.0.0.1", "--port", str(proxy_port), "--strictPort"], cwd=ROOT / "frontend", env=vite_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                wait_for_server(proxy, proxy_port, timeout)
                _expect(safe_request(proxy_port, "/api/v1/jobs")[0] == 401 and safe_request(proxy_port, "/api/v1/jobs", token=token)[1]["jobs"] == [], "proxy_auth_failed")
                outcomes.append("Vite HTTP proxy: ok")
            _expect(backend.poll() is None and {item.name for item in index.iterdir()} == {"build-artifacts"}, "unexpected_index_activity")
        finally:
            stop_process(proxy)
            stop_process(backend)
    return outcomes


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an isolated synthetic local-runtime HTTP smoke test.")
    parser.add_argument("--vite", action="store_true", help="also check the installed Vite development proxy")
    parser.add_argument("--timeout", type=float, default=30, help="startup timeout in seconds (maximum 120)")
    args = parser.parse_args()
    try:
        for outcome in run_smoke(vite=args.vite, timeout=args.timeout):
            print(outcome)
        print("Synthetic report only; no index or browser verification.")
        return 0
    except Exception as exc:
        print(f"Smoke test failed: {exc if isinstance(exc, SmokeError) else 'unexpected_error'}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
