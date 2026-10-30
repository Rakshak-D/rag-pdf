"""Real loopback HTTP smoke for bootstrap preview and a synthetic fresh build.

The embedding adapter is injected by this trusted local harness only. No model
weights, Ollama service, user PDFs, or pre-existing index are used.
"""
from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
import socket
import sys
import tempfile
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np

from backend.bootstrap_app.runtime import BootstrapSettings, build_bootstrap_runtime
from backend.config import ResolvedRunConfig
from backend.embeddings.text import EmbeddingBatchResult
from backend.engine.manifest import build_document_manifest
from backend.experiments.run_plan import build_run_plan
from notebooks.config import Config


class _SmokeTextAdapter:
    def __init__(self, config: ResolvedRunConfig):
        self.config = config
        self.model_name = config.text_embed_model

    def embed_documents(self, documents):
        count = len(documents)
        values = np.tile(np.asarray([[1.0, 0.0, 0.0]], dtype=np.float32), (count, 1))
        return EmbeddingBatchResult(values, 0.0, count, count, 3, 0.0)


def _serve_child(source_value: str, storage_value: str, state_value: str, token: str,
                 port: int, config_payload: dict, listener, stop_event, failures) -> None:
    """Spawn target: isolate Chroma handles so Windows releases them at exit."""
    try:
        import uvicorn

        source, storage, state = Path(source_value), Path(storage_value), Path(state_value)
        config = ResolvedRunConfig.model_validate(config_payload)
        settings = BootstrapSettings(
            api_token=token, source_root=source, storage_root=storage, state_dir=state,
            host="127.0.0.1", port=port, queue_capacity=2,
        )
        runtime = build_bootstrap_runtime(settings, adapter_factory=lambda: (_SmokeTextAdapter(config), None))
        server = uvicorn.Server(uvicorn.Config(runtime.app, log_level="warning", access_log=False))

        from threading import Thread
        def stop_server():
            stop_event.wait()
            server.should_exit = True
        watcher = Thread(target=stop_server, name="bootstrap-smoke-stop", daemon=True)
        watcher.start()
        server.run(sockets=[listener])
        watcher.join(5)
    except BaseException:
        try:
            failures.put("bootstrap_server_child_failed")
        except Exception:
            pass


def _request(url: str, token: str | None, *, method: str = "GET", payload=None):
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if payload is not None:
        data = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))
    except URLError as exc:
        raise RuntimeError("loopback_http_request_failed") from exc


def _write_pdf(path: Path) -> None:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Synthetic local bootstrap smoke document with one searchable paragraph.")
    doc.save(path)
    doc.close()


def _clone_report_candidate(storage: Path, source_report: Path, build_id: str) -> Path:
    """Create a temporary schema-valid inventory fixture from a real report."""
    artifact_dir = storage / "build-artifacts" / build_id
    artifact_dir.mkdir(parents=True)
    payload = json.loads(source_report.read_text(encoding="utf-8"))
    payload["build_id"] = build_id
    payload["artifact_root"] = str(artifact_dir)
    payload["report_path"] = str(artifact_dir / "build-report.json")
    report = artifact_dir / "build-report.json"
    report.write_text(json.dumps(payload, allow_nan=False, separators=(",", ":")), encoding="utf-8")
    return report


def _storage_snapshot(storage: Path) -> dict[str, bytes | None]:
    if not storage.exists():
        return {}
    snapshot: dict[str, bytes | None] = {}
    for path in storage.rglob("*"):
        relative = path.relative_to(storage).as_posix()
        snapshot[relative] = None if path.is_dir() else path.read_bytes()
    return snapshot


def run_smoke() -> dict[str, object]:
    token = "smoke-only-token-0123456789-abcdef0123456789"
    with tempfile.TemporaryDirectory(prefix="rag-bootstrap-http-") as temp:
        owned = Path(temp)
        source = owned / "source"
        state = owned / "state"
        storage = owned / "index-storage"
        source.mkdir()
        state.mkdir()
        _write_pdf(source / "synthetic.pdf")
        config = ResolvedRunConfig.from_legacy(Config(
            pdf_dir=str(source),
            images_dir=str(owned / "images"),
            database_dir=str(owned / "database"),
            results_dir=str(owned / "results"),
            model_comparison_results_dir=str(owned / "comparisons"),
            retrieval_results_dir=str(owned / "retrieval-results"),
            reset_collections_on_start=False,
        ))
        config_json = config.model_dump(mode="json")
        manifest = build_document_manifest(source)
        plan = build_run_plan(config, manifest.content_hash)

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        context = multiprocessing.get_context("spawn")
        stop_event = context.Event()
        failures = context.Queue(maxsize=1)
        server_process = context.Process(
            target=_serve_child,
            args=(str(source), str(storage), str(state), token, port, config.model_dump(mode="json"), listener, stop_event, failures),
            name="bootstrap-http-smoke-server",
        )
        server_process.start()
        listener.close()
        base = f"http://127.0.0.1:{port}"
        results: dict[str, object] = {}
        try:
            deadline = monotonic() + 30
            health = None
            while monotonic() < deadline:
                if not server_process.is_alive():
                    detail = failures.get_nowait() if not failures.empty() else "bootstrap_server_exited_before_startup"
                    raise RuntimeError(str(detail))
                try:
                    code, health = _request(base + "/health", None)
                    if code == 200:
                        break
                except RuntimeError:
                    pass
                sleep(0.1)
            else:
                raise RuntimeError("bootstrap_server_startup_timeout")

            if health is None or health.get("status") != "ok":
                raise RuntimeError("health_contract_failed")
            results["health"] = "200 unauthenticated liveness"

            status, _ = _request(base + "/api/v1/bootstrap/status", None)
            if status != 401:
                raise RuntimeError("authentication_contract_failed")
            status, mode = _request(base + "/api/v1/bootstrap/status", token)
            if status != 200 or mode.get("mode") != "bootstrap":
                raise RuntimeError("bootstrap_status_contract_failed")
            results["authentication"] = "unauthorized rejected; bearer token accepted"

            inventory_path = base + "/api/v1/bootstrap/index-inventory"
            inventory_body = {
                "resolved_config": config_json,
                "expected_manifest_hash": manifest.content_hash,
                "limit": 25,
                "offset": 0,
            }
            status, _ = _request(inventory_path, None, method="POST", payload=inventory_body)
            if status != 401:
                raise RuntimeError("inventory_authentication_contract_failed")
            status, empty_inventory = _request(inventory_path, token, method="POST", payload=inventory_body)
            if (status != 200 or empty_inventory.get("candidates") != []
                    or empty_inventory.get("complete") is not True
                    or empty_inventory.get("reason_code") != "no_build_reports"):
                raise RuntimeError("empty_inventory_contract_failed")
            results["inventory_empty"] = "authenticated complete empty report inventory"

            status, preview = _request(base + "/api/v1/bootstrap/preview", token, method="POST", payload={"resolved_config": config_json})
            if status != 200 or preview.get("manifest_hash") != manifest.content_hash or preview.get("index_fingerprint") != plan.index_fingerprint:
                raise RuntimeError("preview_identity_failed")
            results["preview"] = {"documents": preview["document_count"], "manifest_matches": True, "fingerprint_matches": True}

            job_request = {
                "resolved_config": config_json,
                "expected_manifest_hash": "0" * 64,
                "expected_index_fingerprint": plan.index_fingerprint,
                "client_request_id": "smoke-request-00000001",
            }
            status, mismatch = _request(base + "/api/v1/bootstrap/jobs", token, method="POST", payload=job_request)
            if status != 409 or mismatch.get("error", {}).get("code") != "manifest_changed":
                raise RuntimeError("manifest_confirmation_rejection_failed")
            results["manifest_mismatch"] = "409 rejected"

            job_request["expected_manifest_hash"] = manifest.content_hash
            status, accepted = _request(base + "/api/v1/bootstrap/jobs", token, method="POST", payload=job_request)
            if status != 202:
                raise RuntimeError("build_job_admission_failed")
            job_id = accepted["job"]["job_id"]
            results["admission"] = {"status": status, "job_id": job_id}

            deadline = monotonic() + 180
            job = accepted["job"]
            while job["status"] in {"queued", "running"} and monotonic() < deadline:
                sleep(0.2)
                status, body = _request(base + f"/api/v1/bootstrap/jobs/{job_id}", token)
                if status != 200:
                    raise RuntimeError("job_lookup_failed")
                job = body["job"]
            if job["status"] != "succeeded":
                raise RuntimeError(f"synthetic_build_not_successful:{job['status']}:{job.get('error_code')}")
            if job["manifest_hash"] != manifest.content_hash or job["index_fingerprint"] != plan.index_fingerprint:
                raise RuntimeError("job_result_identity_failed")
            report = storage / job["report_reference"]
            if not report.is_file() or job["text_records"] is None or job["text_records"] < 1:
                raise RuntimeError("genuine_report_or_record_count_missing")

            status, stale_inventory = _request(
                inventory_path, token, method="POST",
                payload={**inventory_body, "expected_manifest_hash": "0" * 64},
            )
            if status != 409 or stale_inventory.get("error", {}).get("code") != "manifest_changed":
                raise RuntimeError("inventory_manifest_mismatch_contract_failed")

            # These report aliases are temporary test fixtures cloned from the
            # genuine report schema. They do not represent additional builds.
            aliases = [
                _clone_report_candidate(storage, report, "0" * 32),
                _clone_report_candidate(storage, report, "f" * 32),
            ]
            malformed_dir = storage / "build-artifacts" / ("e" * 32)
            malformed_dir.mkdir(parents=True)
            malformed_report = malformed_dir / "build-report.json"
            malformed_report.write_text("{malformed", encoding="utf-8")
            status, jobs_before_inventory = _request(base + "/api/v1/bootstrap/jobs?limit=10&offset=0", token)
            if status != 200:
                raise RuntimeError("job_list_before_inventory_failed")
            storage_snapshot = _storage_snapshot(storage)
            report_snapshot = {
                path.relative_to(storage).as_posix(): path.read_bytes()
                for path in (report, *aliases, malformed_report)
            }
            page_rows = []
            for offset in range(4):
                status, inventory = _request(
                    inventory_path, token, method="POST",
                    payload={**inventory_body, "limit": 1, "offset": offset},
                )
                if (status != 200 or inventory.get("complete") is not True
                        or inventory.get("total_candidates") != 4):
                    raise RuntimeError("inventory_pagination_contract_failed")
                page_rows.extend(inventory["candidates"])
            candidate_ids = [candidate["build_id"] for candidate in page_rows]
            if candidate_ids != sorted(candidate_ids) or len(set(candidate_ids)) != 4:
                raise RuntimeError("inventory_ordering_contract_failed")
            invalid = next(candidate for candidate in page_rows if candidate["build_id"] == "e" * 32)
            if invalid["compatibility"] != "invalid" or invalid["reason_code"] != "report_invalid":
                raise RuntimeError("inventory_malformed_candidate_contract_failed")
            if any(candidate["store_checked"] is not False or candidate["check_level"] != "report" for candidate in page_rows):
                raise RuntimeError("inventory_verification_level_contract_failed")
            after_snapshot = {
                path.relative_to(storage).as_posix(): path.read_bytes()
                for path in (report, *aliases, malformed_report)
            }
            if report_snapshot != after_snapshot:
                raise RuntimeError("inventory_modified_report_evidence")
            if storage_snapshot != _storage_snapshot(storage):
                raise RuntimeError("inventory_modified_storage")
            status, jobs_after_inventory = _request(base + "/api/v1/bootstrap/jobs?limit=10&offset=0", token)
            if (status != 200
                    or [item["job_id"] for item in jobs_before_inventory["jobs"]]
                    != [item["job_id"] for item in jobs_after_inventory["jobs"]]):
                raise RuntimeError("inventory_changed_job_history")
            results["inventory"] = {
                "candidate_count": len(page_rows),
                "pagination": "four one-item pages in lexical order",
                "malformed_candidate": "classified invalid without failing the scan",
                "store_checked": False,
                "synthetic_aliases_are_builds": False,
            }

            status, replay = _request(base + "/api/v1/bootstrap/jobs", token, method="POST", payload=job_request)
            if status != 202 or replay.get("job", {}).get("job_id") != job_id:
                raise RuntimeError("completed_request_replay_failed")
            results["real_chroma_build"] = {
                "status": job["status"],
                "report_published": True,
                "text_records": job["text_records"],
                "image_records": job["image_records"],
                "completed_idempotent_replay": "same job returned; no second build",
                "adapter": "trusted synthetic adapter; no model downloads",
            }
            status, listing = _request(base + "/api/v1/bootstrap/jobs?limit=10&offset=0", token)
            if status != 200 or not any(item["job_id"] == job_id for item in listing["jobs"]):
                raise RuntimeError("job_list_contract_failed")
            results["job_lookup_and_list"] = "passed"
        finally:
            stop_event.set()
            server_process.join(45)
            if server_process.is_alive():
                server_process.terminate()
                server_process.join(10)
                if server_process.is_alive():
                    raise RuntimeError("bootstrap_server_process_cleanup_failed")
                raise RuntimeError("bootstrap_server_shutdown_timeout")
            if server_process.exitcode != 0:
                raise RuntimeError("bootstrap_server_exit_failed")
            failures.close()
            failures.join_thread()
            server_process.close()
        results["shutdown"] = "clean"
    return results


def main() -> int:
    try:
        result = run_smoke()
    except Exception as exc:
        # Keep diagnostics bounded; no token, source path, or traceback is emitted.
        code = str(exc).split(":", 1)[0]
        print(f"Bootstrap HTTP smoke failed: {code}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    print("This exercised real loopback HTTP and isolated Chroma persistence with a test adapter; it did not exercise production model loading or a browser.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
