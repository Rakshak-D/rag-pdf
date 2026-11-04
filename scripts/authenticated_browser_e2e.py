"""Run the bounded real-browser workbench E2E in isolated local services."""
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
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
REPOSITORY_HEAD = "15e15cbea9aef69abbc12253ded514c9387aac51"
REQUIREMENTS_SHA256 = "EF3095DBC1A1657CCB5D5FCBA4D2DF245DD5EC9D04B6C2D237823FDFE472734F"
GLOBAL_DEADLINE_SECONDS = 600
LOG_LIMIT_BYTES = 2 * 1024 * 1024
TASK_DISK_BUDGET_BYTES = 1024 * 1024 * 1024


class E2EBlocked(RuntimeError):
    """A stable, non-sensitive failure code for launcher gates."""


class ChildProcess:
    def __init__(self, name: str, process: subprocess.Popen[bytes], log_path: Path,
                 secrets_to_redact: tuple[str, ...]) -> None:
        self.name = name
        self.process = process
        self.log_path = log_path
        self.secrets = tuple(value.encode("utf-8") for value in secrets_to_redact if value)
        self.bytes_written = 0
        self.reader_errors: list[str] = []
        self.reader = threading.Thread(target=self._drain, name=f"e2e-log-{name}", daemon=True)
        self.reader.start()

    def _drain(self) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("wb") as output:
                assert self.process.stdout is not None
                for line in iter(self.process.stdout.readline, b""):
                    safe = line
                    for secret in self.secrets:
                        safe = safe.replace(secret, b"[REDACTED]")
                    if self.bytes_written < LOG_LIMIT_BYTES:
                        kept = safe[: LOG_LIMIT_BYTES - self.bytes_written]
                        output.write(kept)
                        self.bytes_written += len(kept)
                        output.flush()
                output.flush()
                os.fsync(output.fileno())
        except Exception as exc:  # Preserve a bounded code, not an exception payload.
            self.reader_errors.append(type(exc).__name__)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    with path.open("wb") as destination:
        destination.write(raw)
        destination.flush()
        os.fsync(destination.fileno())


def _tree_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    pending = [path]
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except FileNotFoundError:
                        continue
        except FileNotFoundError:
            continue
    return total


def _clean_child_environment(workspace: Path) -> dict[str, str]:
    excluded_exact = {
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        "NODE_AUTH_TOKEN", "NPM_TOKEN", "GITHUB_TOKEN", "GH_TOKEN",
        "NODE_OPTIONS", "NODE_PATH", "PYTHONSTARTUP",
    }
    env = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper in excluded_exact or upper.startswith("RAG_WORKBENCH_") or upper.startswith("NPM_CONFIG_"):
            continue
        if any(marker in upper for marker in ("_TOKEN", "_SECRET", "_PASSWORD", "_API_KEY", "_CREDENTIAL")):
            continue
        env[key] = value
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    env["TEMP"] = str(workspace / "tmp")
    env["TMP"] = str(workspace / "tmp")
    env["NO_PROXY"] = "127.0.0.1,localhost,::1"
    env["no_proxy"] = env["NO_PROXY"]
    env["npm_config_userconfig"] = str(workspace / "npmrc")
    env["npm_config_globalconfig"] = str(workspace / "global-npmrc")
    env["npm_config_cache"] = str(workspace / "npm-cache")
    return env


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _listeners(port: int) -> list[dict[str, str | int]]:
    netstat = shutil.which("netstat.exe") or shutil.which("netstat")
    if not netstat:
        raise E2EBlocked("netstat_unavailable_for_port_ownership_check")
    result = subprocess.run([netstat, "-ano", "-p", "tcp"], capture_output=True, timeout=10, check=False)
    if result.returncode != 0:
        raise E2EBlocked("netstat_port_ownership_query_failed")
    matches: list[dict[str, str | int]] = []
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        fields = line.split()
        if len(fields) < 5 or fields[0].upper() != "TCP" or fields[3].upper() != "LISTENING":
            continue
        local = fields[1]
        if local.rsplit(":", 1)[-1] == str(port):
            try:
                pid = int(fields[4])
            except ValueError:
                pid = -1
            matches.append({"local_address": local, "pid": pid})
    return matches


def _assert_owned_loopback_listener(port: int, pid: int, name: str) -> dict[str, str | int]:
    matches = _listeners(port)
    if len(matches) != 1:
        raise E2EBlocked(f"{name}_listener_count_invalid")
    owner = matches[0]
    if owner["local_address"] != f"127.0.0.1:{port}" or owner["pid"] != pid:
        raise E2EBlocked(f"{name}_listener_ownership_or_bind_invalid")
    return owner


def _spawn(name: str, argv: list[str], cwd: Path, env: dict[str, str], log_path: Path,
           secrets_to_redact: tuple[str, ...]) -> ChildProcess:
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=flags,
        start_new_session=(os.name != "nt"),
    )
    return ChildProcess(name, process, log_path, secrets_to_redact)


def _powershell_json(script: str) -> Any:
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if not powershell:
        raise E2EBlocked("powershell_unavailable_for_process_tree_check")
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        raise E2EBlocked("process_tree_query_failed")
    output = result.stdout.decode("utf-8", "replace").strip()
    return json.loads(output) if output else []


def _process_tree(root_pid: int) -> list[dict[str, Any]]:
    script = rf"""
$root = {int(root_pid)}
$all = @(Get-CimInstance Win32_Process)
$ids = [System.Collections.Generic.HashSet[int]]::new()
[void]$ids.Add($root)
$changed = $true
while ($changed) {{
  $changed = $false
  foreach ($item in $all) {{
    if ($ids.Contains([int]$item.ParentProcessId) -and -not $ids.Contains([int]$item.ProcessId)) {{
      [void]$ids.Add([int]$item.ProcessId)
      $changed = $true
    }}
  }}
}}
$rows = @($all | Where-Object {{ $ids.Contains([int]$_.ProcessId) }} | Select-Object ProcessId, ParentProcessId, Name)
ConvertTo-Json -InputObject $rows -Compress
"""
    rows = _powershell_json(script)
    if isinstance(rows, dict):
        rows = [rows]
    return [{"pid": int(row["ProcessId"]), "parent_pid": int(row["ParentProcessId"]), "name": str(row["Name"])} for row in rows]


def _stop(child: ChildProcess, timeout: float = 8.0) -> dict[str, Any]:
    process = child.process
    signal_result = "already_exited"
    taskkill_result: int | None = None
    if process.poll() is None:
        try:
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                process.send_signal(signal.SIGINT)
            signal_result = "graceful_signal_sent"
            process.wait(timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            signal_result = "graceful_stop_failed"
            if os.name == "nt":
                taskkill = shutil.which("taskkill.exe") or shutil.which("taskkill")
                if not taskkill:
                    raise E2EBlocked(f"{child.name}_taskkill_unavailable")
                result = subprocess.run([taskkill, "/PID", str(process.pid), "/T", "/F"],
                                        capture_output=True, timeout=15, check=False)
                taskkill_result = result.returncode
            else:
                process.kill()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired as exc:
                raise E2EBlocked(f"{child.name}_process_not_reaped") from exc
    child.reader.join(timeout=5)
    if child.reader.is_alive() or child.reader_errors:
        raise E2EBlocked(f"{child.name}_log_capture_not_stable")
    return {
        "name": child.name,
        "pid": process.pid,
        "exit_code": process.returncode,
        "signal": signal_result,
        "taskkill_exit_code": taskkill_result,
        "log_bytes": child.bytes_written,
    }


def _http_json(url: str, timeout: float = 2.0) -> tuple[int, dict[str, Any]]:
    request = Request(url, headers={"Accept": "application/json"})
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise E2EBlocked("readiness_response_too_large")
            return response.status, json.loads(raw.decode("utf-8"))
    except HTTPError as exc:
        raise E2EBlocked("readiness_http_error") from exc
    except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise E2EBlocked("readiness_request_failed") from exc


def _wait_health(child: ChildProcess, url: str, port: int, deadline: float, label: str,
                 sample_resources: Any) -> dict[str, str | int]:
    while time.monotonic() < deadline:
        sample_resources()
        if child.process.poll() is not None:
            raise E2EBlocked(f"{label}_process_exited_before_ready")
        try:
            status, payload = _http_json(url)
        except E2EBlocked:
            time.sleep(0.15)
            continue
        if status == 200 and payload.get("status") == "ok":
            return _assert_owned_loopback_listener(port, child.process.pid, label)
        time.sleep(0.15)
    raise E2EBlocked(f"{label}_readiness_timeout")


def _write_pdf(path: Path) -> None:
    import pymupdf

    document = pymupdf.open()
    document.new_page().insert_text((72, 72), "Synthetic browser E2E document; no user corpus.")
    document.save(path)
    document.close()


def _write_guard(path: Path) -> None:
    source = (
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
        "runpy.run_module('backend.app', run_name='__main__')\n"
    )
    path.write_text(source, encoding="utf-8", newline="\n")


def _environment_with(env: dict[str, str], values: dict[str, str]) -> dict[str, str]:
    result = dict(env)
    result.update(values)
    return result


def _write_evidence_inventory(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "evidence-manifest.json":
            continue
        try:
            rows.append({
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            })
        except (OSError, FileNotFoundError):
            rows.append({"path": path.relative_to(root).as_posix(), "unreadable": True})
    _write_json(root / "evidence-manifest.json", {"files": rows})
    return rows


def _scan_for_tokens(root: Path, token_values: tuple[str, ...]) -> list[str]:
    needles = tuple(token.encode("utf-8") for token in token_values)
    overlap = max((len(secret) for secret in needles), default=1) - 1
    matches: list[str] = []
    for path in root.rglob("*"):
        if any(secret in path.name for secret in token_values):
            matches.append(path.relative_to(root).as_posix())
            continue
        if not path.is_file():
            continue
        try:
            tail = b""
            found = False
            with path.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    data = tail + block
                    if any(secret in data for secret in needles):
                        found = True
                        break
                    tail = data[-overlap:] if overlap else b""
            if found:
                matches.append(path.relative_to(root).as_posix())
        except OSError:
            continue
    return matches


def _run(prior_task_disk_bytes: int = 0) -> tuple[int, dict[str, Any]]:
    started_at = time.time()
    overall_started = time.monotonic()
    deadline = overall_started + GLOBAL_DEADLINE_SECONDS
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise E2EBlocked("localappdata_unavailable")
    temp_root = Path(local_app_data) / "Temp"
    temp_root.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix="rag-pdf-618a-e2e-", dir=temp_root))
    for name in ("ordinary", "bootstrap", "logs", "evidence", "tmp", "playwright-results"):
        (workspace / name).mkdir()

    ordinary_token = secrets.token_urlsafe(32)
    bootstrap_token = secrets.token_urlsafe(32)
    invalid_token = secrets.token_urlsafe(24)
    if len({ordinary_token, bootstrap_token, invalid_token}) != 3:
        raise E2EBlocked("test_token_generation_collision")
    secret_values = (ordinary_token, bootstrap_token, invalid_token)

    if _sha256(ROOT / "requirements.txt") != REQUIREMENTS_SHA256:
        raise E2EBlocked("requirements_proposal_integrity_mismatch")
    package_metadata = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    package_lock = json.loads((FRONTEND / "package-lock.json").read_text(encoding="utf-8"))
    if (package_metadata.get("devDependencies", {}).get("@playwright/test") != "1.64.1"
            or package_lock.get("packages", {}).get("node_modules/@playwright/test", {}).get("version") != "1.64.1"
            or not (FRONTEND / "node_modules" / "@playwright" / "test").is_dir()):
        raise E2EBlocked("playwright_test_dependency_unavailable")
    edge = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    if not edge.is_file():
        raise E2EBlocked("microsoft_edge_stable_unavailable")

    ports = [_free_loopback_port() for _ in range(3)]
    if len(set(ports)) != 3:
        raise E2EBlocked("loopback_port_collision")
    for port in ports:
        if _listeners(port):
            raise E2EBlocked("dynamically_selected_port_not_free")
    ordinary_port, bootstrap_port, vite_port = ports

    sys.dont_write_bytecode = True
    from backend.config import ResolvedRunConfig
    from backend.engine.manifest import build_document_manifest
    from backend.experiments.run_plan import build_run_plan
    from notebooks.config import Config

    ordinary_root = workspace / "ordinary"
    ordinary_source = ordinary_root / "pdf-source"
    ordinary_index = ordinary_root / "index-storage"
    ordinary_state = ordinary_root / "state"
    ordinary_artifacts = ordinary_root / "artifacts"
    bootstrap_root = workspace / "bootstrap"
    bootstrap_source = bootstrap_root / "pdf-source"
    bootstrap_state = bootstrap_root / "state"
    bootstrap_storage = bootstrap_root / "index-storage"
    for path in (ordinary_source, ordinary_index, ordinary_state, bootstrap_source, bootstrap_state):
        path.mkdir(parents=True, exist_ok=True)
    _write_pdf(bootstrap_source / "synthetic.pdf")
    _write_pdf(ordinary_source / "synthetic.pdf")
    config = ResolvedRunConfig.from_legacy(Config(
        pdf_dir=str(bootstrap_source),
        images_dir=str(bootstrap_root / "images"),
        database_dir=str(bootstrap_root / "database"),
        results_dir=str(bootstrap_root / "results"),
        model_comparison_results_dir=str(bootstrap_root / "comparisons"),
        retrieval_results_dir=str(bootstrap_root / "retrieval-results"),
        reset_collections_on_start=False,
    ))
    if config.reset_collections_on_start or config.workbench_policy.allow_destructive_index_reset or config.workbench_policy.allow_collection_delete:
        raise E2EBlocked("destructive_configuration_not_safe")
    manifest = build_document_manifest(bootstrap_source)
    plan = build_run_plan(config, manifest.content_hash)
    ordinary_config = ResolvedRunConfig.from_legacy(Config(
        pdf_dir=str(ordinary_source),
        images_dir=str(ordinary_root / "images"),
        database_dir=str(ordinary_root / "database"),
        results_dir=str(ordinary_root / "results"),
        model_comparison_results_dir=str(ordinary_root / "comparisons"),
        retrieval_results_dir=str(ordinary_root / "retrieval-results"),
        reset_collections_on_start=False,
    ))
    ordinary_manifest = build_document_manifest(ordinary_source)
    ordinary_plan = build_run_plan(ordinary_config, ordinary_manifest.content_hash)
    config_payload = config.model_dump(mode="json")
    expected_payload = {
        "document_count": len(manifest.entries),
        "manifest_hash": manifest.content_hash,
        "index_fingerprint": plan.index_fingerprint,
        "run_fingerprint": plan.run_fingerprint,
        "resolved_config": config_payload,
    }

    ordinary_report_dir = ordinary_index / "build-artifacts" / "synthetic-browser-e2e"
    ordinary_report_dir.mkdir(parents=True)
    ordinary_report = ordinary_report_dir / "build-report.json"
    report_payload = {
        "report_schema_version": "1",
        "build_id": "synthetic-browser-e2e",
        "artifact_root": str(ordinary_report_dir),
        "report_path": str(ordinary_report),
        "index_fingerprint": ordinary_plan.index_fingerprint,
        "document_manifest_hash": ordinary_manifest.content_hash,
        "status": "completed",
        "partial_persistence": False,
        "text_records_persisted": 0,
        "image_records_persisted": 0,
    }
    _write_json(ordinary_report, report_payload)
    _write_guard(workspace / "guarded_ordinary_runtime.py")
    _write_json(workspace / "expected-fixture.json", expected_payload)

    baseline_env = _clean_child_environment(workspace)
    baseline_env["PYTHONDONTWRITEBYTECODE"] = "1"
    (workspace / "npmrc").write_text("registry=https://registry.npmjs.org/\nignore-scripts=true\n", encoding="utf-8")
    (workspace / "global-npmrc").write_text("", encoding="utf-8")

    ordinary_env = _environment_with(baseline_env, {
        "RAG_WORKBENCH_API_TOKEN": ordinary_token,
        "RAG_WORKBENCH_DATA_DIR": str(ordinary_source),
        "RAG_WORKBENCH_ARTIFACT_STORE_DIR": str(ordinary_artifacts),
        "RAG_WORKBENCH_CATALOG_DB_PATH": str(ordinary_state / "catalog.sqlite"),
        "RAG_WORKBENCH_JOBS_DB_PATH": str(ordinary_state / "jobs.sqlite"),
        "RAG_WORKBENCH_INDEX_STORAGE_ROOT": str(ordinary_index),
        "RAG_WORKBENCH_BUILD_REPORT_PATH": str(ordinary_report),
        "RAG_WORKBENCH_API_HOST": "127.0.0.1",
        "RAG_WORKBENCH_API_PORT": str(ordinary_port),
    })
    bootstrap_env = _environment_with(baseline_env, {
        "RAG_WORKBENCH_BOOTSTRAP_API_TOKEN": bootstrap_token,
        "RAG_WORKBENCH_BOOTSTRAP_PDF_SOURCE_DIR": str(bootstrap_source),
        "RAG_WORKBENCH_BOOTSTRAP_INDEX_STORAGE_ROOT": str(bootstrap_storage),
        "RAG_WORKBENCH_BOOTSTRAP_STATE_DIR": str(bootstrap_state),
        "RAG_WORKBENCH_BOOTSTRAP_API_HOST": "127.0.0.1",
        "RAG_WORKBENCH_BOOTSTRAP_API_PORT": str(bootstrap_port),
    })
    vite_env = dict(baseline_env)
    vite_env["RAG_WORKBENCH_DEV_PROXY_TARGET"] = f"http://127.0.0.1:{ordinary_port}"
    vite_env["RAG_WORKBENCH_BOOTSTRAP_DEV_PROXY_TARGET"] = f"http://127.0.0.1:{bootstrap_port}"

    processes: list[ChildProcess] = []
    process_records: list[dict[str, Any]] = []
    listener_records: dict[str, Any] = {}
    playwright_return: int | None = None
    playwright_elapsed_ms: int | None = None
    test_command: list[str] = []
    failure_code: str | None = None
    cleanup_ok = False
    node_modules = FRONTEND / "node_modules"
    node_modules_before = _tree_bytes(node_modules)
    free_before = shutil.disk_usage("C:/").free
    resource_metrics: dict[str, int] = {
        "prior_task_disk_bytes": prior_task_disk_bytes,
        "peak_task_owned_disk_bytes": prior_task_disk_bytes,
        "minimum_free_c_bytes": free_before,
    }

    def sample_resources() -> None:
        workspace_bytes = _tree_bytes(workspace)
        node_modules_delta = max(0, _tree_bytes(node_modules) - node_modules_before)
        task_bytes = prior_task_disk_bytes + workspace_bytes + node_modules_delta
        free_now = shutil.disk_usage("C:/").free
        resource_metrics["current_task_owned_disk_bytes"] = task_bytes
        resource_metrics["peak_task_owned_disk_bytes"] = max(resource_metrics["peak_task_owned_disk_bytes"], task_bytes)
        resource_metrics["minimum_free_c_bytes"] = min(resource_metrics["minimum_free_c_bytes"], free_now)
        if task_bytes > TASK_DISK_BUDGET_BYTES:
            raise E2EBlocked("task_owned_disk_budget_exceeded")

    try:
        sample_resources()
        ordinary = _spawn("ordinary_backend", [sys.executable, str(workspace / "guarded_ordinary_runtime.py")],
                          ROOT, ordinary_env, workspace / "logs" / "ordinary-backend.log", secret_values)
        processes.append(ordinary)
        bootstrap = _spawn("bootstrap_backend", [sys.executable, "-m", "backend.bootstrap_app"],
                           ROOT, bootstrap_env, workspace / "logs" / "bootstrap-backend.log", secret_values)
        processes.append(bootstrap)

        listener_records["ordinary_backend"] = _wait_health(
            ordinary, f"http://127.0.0.1:{ordinary_port}/health", ordinary_port,
            min(deadline, time.monotonic() + 35), "ordinary_backend", sample_resources,
        )
        listener_records["bootstrap_backend"] = _wait_health(
            bootstrap, f"http://127.0.0.1:{bootstrap_port}/health", bootstrap_port,
            min(deadline, time.monotonic() + 35), "bootstrap_backend", sample_resources,
        )

        node = shutil.which("node.exe") or shutil.which("node")
        vite_entry = FRONTEND / "node_modules" / "vite" / "bin" / "vite.js"
        npm_cmd = shutil.which("npm.cmd") or shutil.which("npm")
        if not node or not vite_entry.is_file() or not npm_cmd:
            raise E2EBlocked("node_vite_or_npm_unavailable")
        npm_cli = Path(npm_cmd).resolve().parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
        if not npm_cli.is_file():
            raise E2EBlocked("npm_cli_unavailable")

        vite = _spawn("vite", [node, str(vite_entry), "--host", "127.0.0.1", "--port", str(vite_port), "--strictPort"],
                      FRONTEND, vite_env, workspace / "logs" / "vite.log", secret_values)
        processes.append(vite)
        listener_records["vite"] = _wait_health(
            vite, f"http://127.0.0.1:{vite_port}/health", vite_port,
            min(deadline, time.monotonic() + 35), "vite", sample_resources,
        )
        status, _ = _http_json(f"http://127.0.0.1:{vite_port}/bootstrap-api/health")
        if status != 200:
            raise E2EBlocked("vite_bootstrap_proxy_readiness_failed")
        _assert_owned_loopback_listener(vite_port, vite.process.pid, "vite")

        playwright_env = dict(baseline_env)
        playwright_env.update({
            "E2E_BASE_URL": f"http://127.0.0.1:{vite_port}",
            "E2E_ORDINARY_TOKEN": ordinary_token,
            "E2E_BOOTSTRAP_TOKEN": bootstrap_token,
            "E2E_INVALID_TOKEN": invalid_token,
            "E2E_EXPECTED_JSON": json.dumps(expected_payload, sort_keys=True, separators=(",", ":")),
            "E2E_OBSERVATIONS_FILE": str(workspace / "evidence" / "browser-observations.jsonl"),
            "E2E_RESULT_FILE": str(workspace / "evidence" / "playwright-result.json"),
            "PLAYWRIGHT_OUTPUT_DIR": str(workspace / "playwright-results"),
        })
        test_command = [node, str(npm_cli), "run", "test:e2e"]
        playwright = _spawn("playwright_e2e", test_command, FRONTEND, playwright_env,
                            workspace / "logs" / "playwright-redacted.log", secret_values)
        processes.append(playwright)
        test_start = time.monotonic()
        while playwright.process.poll() is None:
            sample_resources()
            if time.monotonic() >= deadline:
                raise E2EBlocked("overall_e2e_deadline_exceeded")
            time.sleep(0.2)
        playwright_return = playwright.process.returncode
        playwright_elapsed_ms = round((time.monotonic() - test_start) * 1000)

    except E2EBlocked as exc:
        failure_code = str(exc)
    except Exception as exc:
        failure_code = f"launcher_unexpected_{type(exc).__name__}"
    finally:
        # Capture descendants while all process roots are still identifiable.
        tree_before: dict[str, list[dict[str, Any]]] = {}
        for child in processes:
            try:
                tree_before[child.name] = _process_tree(child.process.pid)
            except Exception as exc:
                failure_code = failure_code or f"process_tree_capture_failed_{type(exc).__name__}"
        for child in reversed(processes):
            try:
                process_records.append(_stop(child))
            except Exception as exc:
                process_records.append({"name": child.name, "cleanup_error": type(exc).__name__})
                failure_code = failure_code or f"cleanup_failed_{child.name}"
        surviving: list[dict[str, Any]] = []
        for child in processes:
            try:
                current_tree = _process_tree(child.process.pid)
                known = {row["pid"] for row in tree_before.get(child.name, [])}
                known.update(row["pid"] for row in current_tree)
                known.add(child.process.pid)
                live = _powershell_json(
                    "$wanted = @(" + ",".join(str(pid) for pid in sorted(known)) + "); "
                    "$rows = @(Get-CimInstance Win32_Process | Where-Object { $wanted -contains [int]$_.ProcessId } | "
                    "Select-Object ProcessId, ParentProcessId, Name); ConvertTo-Json -InputObject $rows -Compress"
                )
                if isinstance(live, dict):
                    live = [live]
                surviving.extend({"root": child.name, "pid": int(row["ProcessId"]), "name": str(row["Name"])} for row in live)
            except Exception as exc:
                surviving.append({"root": child.name, "verification_error": type(exc).__name__})

        listener_after = {str(port): _listeners(port) for port in ports}
        cleanup_ok = not surviving and all(not rows for rows in listener_after.values()) and all(
            child.process.poll() is not None and not child.reader.is_alive() and not child.reader_errors
            for child in processes
        )
        if not cleanup_ok:
            failure_code = failure_code or "owned_process_or_listener_remains"

    test_results: dict[str, Any] | None = None
    result_file = workspace / "evidence" / "playwright-result.json"
    if result_file.is_file():
        try:
            test_results = json.loads(result_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            failure_code = failure_code or "playwright_result_invalid"
    observations_file = workspace / "evidence" / "browser-observations.jsonl"
    if observations_file.exists():
        for line in observations_file.read_text(encoding="utf-8").splitlines():
            try:
                json.loads(line)
            except json.JSONDecodeError:
                failure_code = failure_code or "browser_observations_invalid"

    ordinary_index_entries = sorted(path.relative_to(ordinary_index).as_posix() for path in ordinary_index.rglob("*") if path.is_file())
    bootstrap_storage_entries = sorted(path.relative_to(bootstrap_storage).as_posix() for path in bootstrap_storage.rglob("*") if path.is_file()) if bootstrap_storage.exists() else []
    expected_ordinary_files = ["build-artifacts/synthetic-browser-e2e/build-report.json"]
    no_index_or_job_mutation = ordinary_index_entries == expected_ordinary_files and not bootstrap_storage_entries
    if not no_index_or_job_mutation:
        failure_code = failure_code or "unexpected_index_storage_activity"

    token_matches = _scan_for_tokens(workspace, secret_values)
    if token_matches:
        failure_code = failure_code or "secret_found_in_task_evidence"
    _write_json(workspace / "evidence" / "launcher-detail.json", {
        "starting_git_head": REPOSITORY_HEAD,
        "started_utc_epoch": started_at,
        "test_command": [Path(test_command[0]).name, "npm-cli.js", "run", "test:e2e"] if test_command else [],
        "playwright_exit_code": playwright_return,
        "playwright_elapsed_ms": playwright_elapsed_ms,
        "playwright_result": test_results,
        "listener_owners": listener_records,
        "listeners_after_cleanup": listener_after,
        "process_cleanup": process_records,
        "process_trees_before_cleanup": tree_before,
        "surviving_owned_processes": surviving,
        "cleanup_ok": cleanup_ok,
        "ordinary_index_files": ordinary_index_entries,
        "bootstrap_storage_files": bootstrap_storage_entries,
        "no_index_or_job_mutation": no_index_or_job_mutation,
        "secret_scan_matches": token_matches,
        "failure_code": failure_code,
        "workspace_bytes": _tree_bytes(workspace),
        "resource_metrics": resource_metrics,
    })
    inventory = _write_evidence_inventory(workspace / "evidence")

    passed = bool(
        playwright_return == 0
        and test_results
        and test_results.get("status") == "passed"
        and test_results.get("expected_tests") == 3
        and test_results.get("completed_tests") == 3
        and cleanup_ok
        and no_index_or_job_mutation
        and not token_matches
        and failure_code is None
    )
    test_records = test_results.get("tests", []) if test_results else []
    passed_tests = sum(1 for item in test_records if item.get("status") == "passed")
    failed_tests = sum(1 for item in test_records if item.get("status") in {"failed", "timedOut"})
    if not cleanup_ok or not no_index_or_job_mutation or token_matches:
        classification = "INTEGRITY_OR_CLEANUP_BLOCKED"
    elif passed and not failed_tests:
        classification = "AUTHENTICATED_BROWSER_E2E_VERIFIED"
    elif failed_tests:
        classification = "BROWSER_BEHAVIOR_FAILED"
    elif passed_tests:
        classification = "PARTIAL_BROWSER_E2E_VERIFIED"
    else:
        classification = "BROWSER_EXECUTION_BLOCKED"
    summary = {
        "workspace": str(workspace),
        "classification": classification,
        "failure_code": failure_code,
        "playwright_exit_code": playwright_return,
        "playwright_elapsed_ms": playwright_elapsed_ms,
        "overall_elapsed_ms": round((time.monotonic() - overall_started) * 1000),
        "browser_version": next((row.get("browser_version") for row in _read_jsonl(observations_file)), None),
        "scenario_records": len(test_records),
        "passed_scenarios": passed_tests,
        "failed_scenarios": failed_tests,
        "cleanup_ok": cleanup_ok,
        "resource_metrics": resource_metrics,
        "no_index_or_job_mutation": no_index_or_job_mutation,
        "secret_scan_matches": len(token_matches),
        "evidence_files": inventory,
    }
    _write_json(workspace / "evidence" / "launcher-summary.json", summary)
    _write_evidence_inventory(workspace / "evidence")
    return (0 if passed else 1), summary


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    values = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                values.append(item)
        except json.JSONDecodeError:
            continue
    return values


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run one isolated authenticated browser E2E invocation.")
    parser.add_argument(
        "--prior-task-disk-bytes", type=int, default=0,
        help="measured task-owned bytes already consumed by prerequisite installation in this task",
    )
    args = parser.parse_args()
    if args.prior_task_disk_bytes < 0 or args.prior_task_disk_bytes >= TASK_DISK_BUDGET_BYTES:
        print(json.dumps({"classification": "BROWSER_EXECUTION_BLOCKED", "failure_code": "prior_disk_accounting_invalid"}))
        return 1
    try:
        exit_code, summary = _run(args.prior_task_disk_bytes)
    except Exception as exc:
        print(json.dumps({"classification": "BROWSER_EXECUTION_BLOCKED", "failure_code": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(summary, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
