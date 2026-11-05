"""Run the bounded real-browser workbench E2E in isolated local services."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def _same_path(left: str | Path, right: Path) -> bool:
    try:
        left_path = Path(left or Path.cwd()).resolve()
        right_path = right.resolve()
        return os.path.normcase(os.path.realpath(str(left_path))) == os.path.normcase(
            os.path.realpath(str(right_path))
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        return False


def _ensure_repository_root_on_sys_path(root: Path = ROOT) -> None:
    """Make repository imports work for direct script execution, without duplicates."""
    resolved_root = root.resolve()
    sys.path[:] = [entry for entry in sys.path if not _same_path(entry, resolved_root)]
    sys.path.insert(0, str(resolved_root))


_ensure_repository_root_on_sys_path()
EXPECTED_BRANCH = "production-evaluation-workbench"
REQUIREMENTS_SHA256 = "EF3095DBC1A1657CCB5D5FCBA4D2DF245DD5EC9D04B6C2D237823FDFE472734F"
GLOBAL_DEADLINE_SECONDS = 600
LOG_LIMIT_BYTES = 2 * 1024 * 1024
TASK_DISK_BUDGET_BYTES = 1024 * 1024 * 1024
EVIDENCE_SCHEMA_VERSION = 1
_SAFE_MODULE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


class E2EBlocked(RuntimeError):
    """A stable, non-sensitive failure code for launcher gates."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class EvidenceCaptureFailed(RuntimeError):
    """The task could not durably retain required launcher evidence."""


def _safe_missing_module_identifier(exc: ModuleNotFoundError) -> str:
    name = getattr(exc, "name", None)
    if isinstance(name, str) and _SAFE_MODULE_IDENTIFIER.fullmatch(name):
        return name
    return "module_identifier_redacted"


@dataclass
class LauncherAttempt:
    workspace: Path
    attempt_id: str
    git_identity: dict[str, str]
    source_sha256: str
    start_record: dict[str, Any]
    subprocess_started: bool = False
    managed_child_pids: list[int] = field(default_factory=list)
    active_managed_child_pids: list[int] = field(default_factory=list)
    fixtures_created: bool = False
    last_attempted_stage: str = "launcher_start"
    values: dict[str, Any] = field(default_factory=dict)

    @property
    def evidence_dir(self) -> Path:
        return self.workspace / "evidence"

    def child_started(self, pid: int) -> None:
        self.subprocess_started = True
        self.managed_child_pids.append(pid)
        self.active_managed_child_pids.append(pid)

    def child_stopped(self, pid: int) -> None:
        self.active_managed_child_pids = [item for item in self.active_managed_child_pids if item != pid]


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


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    with path.open("ab") as destination:
        destination.write(raw)
        destination.flush()
        os.fsync(destination.fileno())


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_git_identity(root: Path) -> dict[str, str]:
    git = shutil.which("git.exe") or shutil.which("git")
    if not git:
        raise E2EBlocked("git_unavailable_for_identity")
    try:
        result = subprocess.run(
            [git, "-C", str(root), "rev-parse", "--show-toplevel", "HEAD", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise E2EBlocked("git_identity_query_failed") from exc
    if result.returncode != 0:
        raise E2EBlocked("git_identity_query_failed")
    lines = result.stdout.splitlines()
    if len(lines) != 3:
        raise E2EBlocked("git_identity_output_invalid")
    try:
        resolved_root = Path(lines[0]).resolve(strict=True)
        expected_root = root.resolve(strict=True)
    except OSError as exc:
        raise E2EBlocked("git_root_resolution_failed") from exc
    if os.path.normcase(str(resolved_root)) != os.path.normcase(str(expected_root)):
        raise E2EBlocked("git_root_mismatch")
    head, branch = lines[1].strip().lower(), lines[2].strip()
    if len(head) != 40 or any(character not in "0123456789abcdef" for character in head):
        raise E2EBlocked("git_head_invalid")
    return {"repository_root": str(resolved_root), "head": head, "branch": branch}


def _initialize_attempt(
    workspace: Path,
    *,
    attempt_id: str,
    git_identity: dict[str, str],
    source_sha256: str,
    python_version: str,
    subprocess_started: bool = False,
) -> LauncherAttempt:
    evidence_dir = workspace / "evidence"
    try:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        record: dict[str, Any] = {
            "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
            "attempt_id": attempt_id,
            "started_utc": _utc_now(),
            "repository_commit": git_identity["head"],
            "repository_branch": git_identity["branch"],
            "launcher_source_sha256": source_sha256,
            "python_version": python_version,
            "intended_test_scope": [
                "bootstrap_authentication_preview",
                "bootstrap_read_only_inventory",
                "ordinary_workbench_authentication",
            ],
            "initial_stage": "launcher_start",
        }
        start_path = evidence_dir / "launcher-start.json"
        _write_json(start_path, record)
        read_back = json.loads(start_path.read_text(encoding="utf-8"))
        if read_back != record:
            raise EvidenceCaptureFailed("launcher_start_readback_mismatch")
    except EvidenceCaptureFailed:
        raise
    except Exception as exc:
        raise EvidenceCaptureFailed("launcher_start_write_failed") from exc
    return LauncherAttempt(
        workspace=workspace,
        attempt_id=attempt_id,
        git_identity=git_identity,
        source_sha256=source_sha256,
        start_record=record,
        subprocess_started=subprocess_started,
    )


def _create_attempt(workspace_base: Path, root: Path = ROOT) -> LauncherAttempt:
    # Read the fields required by launcher-start before allocating the workspace;
    # once the workspace exists, the start record is the next filesystem action.
    identity = _read_git_identity(root)
    source_hash = _sha256(Path(__file__).resolve())
    attempt_id = uuid.uuid4().hex
    try:
        workspace = Path(tempfile.mkdtemp(prefix="rag-pdf-618a-e2e-", dir=workspace_base))
    except Exception as exc:
        raise E2EBlocked("evidence_workspace_unavailable") from exc
    return _initialize_attempt(
        workspace,
        attempt_id=attempt_id,
        git_identity=identity,
        source_sha256=source_hash,
        python_version=sys.version,
        subprocess_started=True,  # The read-only Git identity subprocess completed before workspace creation.
    )


def _record_preflight_failure(attempt: LauncherAttempt, code: str, exc: Exception) -> None:
    record = {
        "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
        "attempt_id": attempt.attempt_id,
        "last_attempted_stage": attempt.last_attempted_stage,
        "failure_code": code,
        "exception_class": type(exc).__name__,
        "subprocess_started": attempt.subprocess_started,
        "managed_process_state": {
            "started_count": len(attempt.managed_child_pids),
            "active_count": len(attempt.active_managed_child_pids),
            "cleanup_status": "not_required_no_active_managed_children"
            if not attempt.active_managed_child_pids else "active_managed_children_present",
        },
        "fixtures_created": attempt.fixtures_created,
        "edge_discovery": attempt.values.get("edge", {"status": "not_attempted"}),
        "failed_utc": _utc_now(),
    }
    if isinstance(exc, ModuleNotFoundError):
        record["missing_module"] = _safe_missing_module_identifier(exc)
    _write_json(attempt.evidence_dir / "launcher-failure.json", record)


def _execute_preflight(
    attempt: LauncherAttempt,
    steps: list[tuple[str, Any]],
    resource_guard: Any = None,
) -> tuple[int, dict[str, Any]]:
    for stage, operation in steps:
        attempt.last_attempted_stage = stage
        try:
            _append_jsonl(attempt.evidence_dir / "launcher-progress.jsonl", {
                "attempt_id": attempt.attempt_id,
                "event": "stage_started",
                "stage": stage,
                "utc": _utc_now(),
            })
        except Exception as exc:
            return 1, {
                "classification": "LAUNCHER_PREFLIGHT_BLOCKED",
                "failure_code": "EVIDENCE_CAPTURE_FAILED",
                "exception_class": type(exc).__name__,
                "durable_evidence": False,
            }
        try:
            operation()
            if resource_guard is not None:
                resource_guard()
        except Exception as exc:
            code = exc.code if isinstance(exc, E2EBlocked) else "preflight_unexpected"
            missing_module = (
                _safe_missing_module_identifier(exc)
                if isinstance(exc, ModuleNotFoundError) else None
            )
            try:
                failed_event = {
                    "attempt_id": attempt.attempt_id,
                    "event": "stage_failed",
                    "stage": stage,
                    "failure_code": code,
                    "exception_class": type(exc).__name__,
                    "utc": _utc_now(),
                }
                if missing_module is not None:
                    failed_event["missing_module"] = missing_module
                _append_jsonl(attempt.evidence_dir / "launcher-progress.jsonl", failed_event)
                _record_preflight_failure(attempt, code, exc)
            except Exception as capture_exc:
                return 1, {
                    "classification": "LAUNCHER_PREFLIGHT_BLOCKED",
                    "failure_code": "EVIDENCE_CAPTURE_FAILED",
                    "exception_class": type(capture_exc).__name__,
                    "durable_evidence": False,
                }
            result = {
                "classification": "LAUNCHER_PREFLIGHT_BLOCKED",
                "failure_code": code,
                "exception_class": type(exc).__name__,
                "last_attempted_stage": stage,
                "durable_evidence": True,
            }
            if missing_module is not None:
                result["missing_module"] = missing_module
            return 1, result
        try:
            _append_jsonl(attempt.evidence_dir / "launcher-progress.jsonl", {
                "attempt_id": attempt.attempt_id,
                "event": "stage_succeeded",
                "stage": stage,
                "utc": _utc_now(),
            })
        except Exception as exc:
            return 1, {
                "classification": "LAUNCHER_PREFLIGHT_BLOCKED",
                "failure_code": "EVIDENCE_CAPTURE_FAILED",
                "exception_class": type(exc).__name__,
                "durable_evidence": False,
            }
    return 0, {"classification": "PREFLIGHT_READY", "durable_evidence": True}


def _verify_requirements(path: Path, expected_sha256: str = REQUIREMENTS_SHA256) -> None:
    if _sha256(path) != expected_sha256:
        raise E2EBlocked("requirements_integrity_mismatch")


def _verify_playwright(frontend: Path) -> None:
    try:
        package_metadata = json.loads((frontend / "package.json").read_text(encoding="utf-8"))
        package_lock = json.loads((frontend / "package-lock.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise E2EBlocked("playwright_package_metadata_unavailable") from exc
    if (
        package_metadata.get("devDependencies", {}).get("@playwright/test") != "1.64.1"
        or package_lock.get("packages", {}).get("node_modules/@playwright/test", {}).get("version") != "1.64.1"
        or not (frontend / "node_modules" / "@playwright" / "test").is_dir()
    ):
        raise E2EBlocked("playwright_test_dependency_unavailable")


def _environment_value_case_insensitive(environment: dict[str, str], name: str) -> str | None:
    values = sorted({
        value
        for key, value in environment.items()
        if key.casefold() == name.casefold() and value
    })
    if len(values) > 1:
        raise E2EBlocked("edge_environment_ambiguous")
    return values[0] if values else None


def _edge_candidates(environment: dict[str, str]) -> list[tuple[str, Path]]:
    candidates: list[tuple[str, Path]] = []
    for key, source in (
        ("ProgramFiles(x86)", "program_files_x86"),
        ("ProgramFiles", "program_files"),
        ("LOCALAPPDATA", "local_app_data"),
    ):
        base = _environment_value_case_insensitive(environment, key)
        if base:
            candidates.append((source, Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"))
    deduped: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for source, path in candidates:
        normalized = os.path.normcase(os.path.abspath(path))
        if normalized not in seen:
            deduped.append((source, path))
            seen.add(normalized)
    return deduped


def _windows_file_version(path: Path) -> str:
    if os.name != "nt":
        raise E2EBlocked("edge_version_metadata_unavailable")
    import ctypes
    from ctypes import wintypes

    class FixedFileInfo(ctypes.Structure):
        _fields_ = [
            ("dwSignature", wintypes.DWORD), ("dwStrucVersion", wintypes.DWORD),
            ("dwFileVersionMS", wintypes.DWORD), ("dwFileVersionLS", wintypes.DWORD),
            ("dwProductVersionMS", wintypes.DWORD), ("dwProductVersionLS", wintypes.DWORD),
            ("dwFileFlagsMask", wintypes.DWORD), ("dwFileFlags", wintypes.DWORD),
            ("dwFileOS", wintypes.DWORD), ("dwFileType", wintypes.DWORD),
            ("dwFileSubtype", wintypes.DWORD), ("dwFileDateMS", wintypes.DWORD),
            ("dwFileDateLS", wintypes.DWORD),
        ]

    version_api = ctypes.WinDLL("version", use_last_error=True)
    version_api.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    version_api.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    version_api.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
    version_api.GetFileVersionInfoW.restype = wintypes.BOOL
    version_api.VerQueryValueW.argtypes = [
        wintypes.LPCVOID, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)
    ]
    version_api.VerQueryValueW.restype = wintypes.BOOL
    ignored = wintypes.DWORD(0)
    size = version_api.GetFileVersionInfoSizeW(str(path), ctypes.byref(ignored))
    if not size:
        raise E2EBlocked("edge_version_metadata_unavailable")
    block = ctypes.create_string_buffer(size)
    if not version_api.GetFileVersionInfoW(str(path), 0, size, block):
        raise E2EBlocked("edge_version_metadata_unavailable")
    pointer = ctypes.c_void_p()
    length = wintypes.UINT(0)
    if not version_api.VerQueryValueW(block, "\\", ctypes.byref(pointer), ctypes.byref(length)):
        raise E2EBlocked("edge_version_metadata_unavailable")
    fixed = ctypes.cast(pointer, ctypes.POINTER(FixedFileInfo)).contents
    if fixed.dwSignature != 0xFEEF04BD:
        raise E2EBlocked("edge_version_metadata_invalid")
    return ".".join(str(value) for value in (
        fixed.dwFileVersionMS >> 16, fixed.dwFileVersionMS & 0xFFFF,
        fixed.dwFileVersionLS >> 16, fixed.dwFileVersionLS & 0xFFFF,
    ))


def _discover_edge(
    environment: dict[str, str] | None = None,
    *,
    version_reader: Any = _windows_file_version,
) -> dict[str, str]:
    env = os.environ if environment is None else environment
    for source, candidate in _edge_candidates(dict(env)):
        if candidate.is_file():
            try:
                resolved = candidate.resolve(strict=True)
            except OSError as exc:
                raise E2EBlocked("edge_path_resolution_failed") from exc
            result = {
                "status": "found",
                "path": str(resolved),
                "file_version": None,
                "discovery_source": source,
            }
            try:
                result["file_version"] = version_reader(resolved)
            except E2EBlocked as exc:
                result["failure_code"] = exc.code
            return result
    raise E2EBlocked("microsoft_edge_stable_unavailable")


def _select_loopback_ports(
    port_provider: Any = None,
    listener_check: Any = None,
) -> tuple[int, int, int]:
    port_provider = _free_loopback_port if port_provider is None else port_provider
    listener_check = _listeners if listener_check is None else listener_check
    ports = [port_provider() for _ in range(3)]
    if len(set(ports)) != 3:
        raise E2EBlocked("loopback_port_collision")
    for port in ports:
        if listener_check(port):
            raise E2EBlocked("dynamically_selected_port_not_free")
    return ports[0], ports[1], ports[2]


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
           secrets_to_redact: tuple[str, ...], attempt: LauncherAttempt | None = None) -> ChildProcess:
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
    if attempt is not None:
        attempt.child_started(process.pid)
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


def _build_preflight_steps(attempt: LauncherAttempt, prior_task_disk_bytes: int) -> list[tuple[str, Any]]:
    values = attempt.values

    def workspace_layout() -> None:
        for name in ("ordinary", "bootstrap", "logs", "tmp", "playwright-results"):
            (attempt.workspace / name).mkdir(parents=True, exist_ok=True)

    def task_budget() -> None:
        if prior_task_disk_bytes < 0 or prior_task_disk_bytes >= TASK_DISK_BUDGET_BYTES:
            raise E2EBlocked("prior_disk_accounting_invalid")

    def validate_checkout() -> None:
        if attempt.git_identity.get("branch") != EXPECTED_BRANCH:
            raise E2EBlocked("unexpected_repository_branch")

    def make_tokens() -> None:
        tokens = (secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(24))
        if len(set(tokens)) != len(tokens):
            raise E2EBlocked("test_token_generation_collision")
        values["tokens"] = tokens

    def verify_requirements() -> None:
        _verify_requirements(ROOT / "requirements.txt")

    def verify_playwright() -> None:
        _verify_playwright(FRONTEND)

    def discover_browser() -> None:
        try:
            values["edge"] = _discover_edge()
        except E2EBlocked:
            values["edge"] = {
                "status": "unavailable",
                "path": None,
                "file_version": None,
                "discovery_source": "standard_stable_locations",
            }
            raise
        if values["edge"].get("failure_code"):
            raise E2EBlocked(values["edge"]["failure_code"])

    def select_ports() -> None:
        values["ports"] = _select_loopback_ports()

    def import_configuration() -> None:
        sys.dont_write_bytecode = True
        from backend.config import ResolvedRunConfig
        from backend.engine.manifest import build_document_manifest
        from backend.experiments.run_plan import build_run_plan
        from notebooks.config import Config

        values.update({
            "ResolvedRunConfig": ResolvedRunConfig,
            "build_document_manifest": build_document_manifest,
            "build_run_plan": build_run_plan,
            "Config": Config,
        })

    def create_fixtures() -> None:
        ordinary_root = attempt.workspace / "ordinary"
        bootstrap_root = attempt.workspace / "bootstrap"
        paths = {
            "ordinary_root": ordinary_root,
            "ordinary_source": ordinary_root / "pdf-source",
            "ordinary_index": ordinary_root / "index-storage",
            "ordinary_state": ordinary_root / "state",
            "ordinary_artifacts": ordinary_root / "artifacts",
            "bootstrap_root": bootstrap_root,
            "bootstrap_source": bootstrap_root / "pdf-source",
            "bootstrap_state": bootstrap_root / "state",
            "bootstrap_storage": bootstrap_root / "index-storage",
        }
        for key in ("ordinary_source", "ordinary_index", "ordinary_state", "bootstrap_source", "bootstrap_state"):
            paths[key].mkdir(parents=True, exist_ok=True)
        values.update(paths)
        _write_pdf(paths["bootstrap_source"] / "synthetic.pdf")
        attempt.fixtures_created = True
        _write_pdf(paths["ordinary_source"] / "synthetic.pdf")

    def build_configurations() -> None:
        ResolvedRunConfig = values["ResolvedRunConfig"]
        Config = values["Config"]
        build_document_manifest = values["build_document_manifest"]
        build_run_plan = values["build_run_plan"]
        bootstrap_root = values["bootstrap_root"]
        bootstrap_source = values["bootstrap_source"]
        ordinary_root = values["ordinary_root"]
        ordinary_source = values["ordinary_source"]
        config = ResolvedRunConfig.from_legacy(Config(
            pdf_dir=str(bootstrap_source),
            images_dir=str(bootstrap_root / "images"),
            database_dir=str(bootstrap_root / "database"),
            results_dir=str(bootstrap_root / "results"),
            model_comparison_results_dir=str(bootstrap_root / "comparisons"),
            retrieval_results_dir=str(bootstrap_root / "retrieval-results"),
            reset_collections_on_start=False,
        ))
        if (config.reset_collections_on_start or config.workbench_policy.allow_destructive_index_reset
                or config.workbench_policy.allow_collection_delete):
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
        values.update({
            "config": config,
            "manifest": manifest,
            "plan": plan,
            "ordinary_plan": ordinary_plan,
            "ordinary_manifest": ordinary_manifest,
            "expected_payload": {
                "document_count": len(manifest.entries),
                "manifest_hash": manifest.content_hash,
                "index_fingerprint": plan.index_fingerprint,
                "run_fingerprint": plan.run_fingerprint,
                "resolved_config": config.model_dump(mode="json"),
            },
        })

    def prepare_service_environments() -> None:
        ordinary_port, bootstrap_port, _ = values["ports"]
        ordinary_root = values["ordinary_root"]
        ordinary_index = values["ordinary_index"]
        ordinary_state = values["ordinary_state"]
        ordinary_source = values["ordinary_source"]
        ordinary_artifacts = values["ordinary_artifacts"]
        bootstrap_root = values["bootstrap_root"]
        bootstrap_source = values["bootstrap_source"]
        bootstrap_state = values["bootstrap_state"]
        bootstrap_storage = values["bootstrap_storage"]
        ordinary_report_dir = ordinary_index / "build-artifacts" / "synthetic-browser-e2e"
        ordinary_report_dir.mkdir(parents=True, exist_ok=True)
        ordinary_report = ordinary_report_dir / "build-report.json"
        ordinary_plan = values["ordinary_plan"]
        ordinary_manifest = values["ordinary_manifest"]
        _write_json(ordinary_report, {
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
        })
        values["ordinary_report"] = ordinary_report
        _write_guard(attempt.workspace / "guarded_ordinary_runtime.py")
        _write_json(attempt.workspace / "expected-fixture.json", values["expected_payload"])
        baseline_env = _clean_child_environment(attempt.workspace)
        baseline_env["PYTHONDONTWRITEBYTECODE"] = "1"
        (attempt.workspace / "npmrc").write_text("registry=https://registry.npmjs.org/\nignore-scripts=true\n", encoding="utf-8")
        (attempt.workspace / "global-npmrc").write_text("", encoding="utf-8")
        ordinary_token, bootstrap_token, invalid_token = values["tokens"]
        values["secret_values"] = (ordinary_token, bootstrap_token, invalid_token)
        values["baseline_env"] = baseline_env
        values["ordinary_env"] = _environment_with(baseline_env, {
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
        values["bootstrap_env"] = _environment_with(baseline_env, {
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
        values["vite_env"] = vite_env

    def prepare_commands() -> None:
        node = shutil.which("node.exe") or shutil.which("node")
        vite_entry = FRONTEND / "node_modules" / "vite" / "bin" / "vite.js"
        npm_cmd = shutil.which("npm.cmd") or shutil.which("npm")
        if not node or not vite_entry.is_file() or not npm_cmd:
            raise E2EBlocked("node_vite_or_npm_unavailable")
        npm_cli = Path(npm_cmd).resolve().parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
        if not npm_cli.is_file():
            raise E2EBlocked("npm_cli_unavailable")
        values["node"] = node
        values["vite_entry"] = vite_entry
        values["test_command"] = [node, str(npm_cli), "run", "test:e2e"]

    return [
        ("workspace_layout", workspace_layout),
        ("task_budget", task_budget),
        ("repository_identity", validate_checkout),
        ("token_generation", make_tokens),
        ("requirements_integrity", verify_requirements),
        ("playwright_dependency_verification", verify_playwright),
        ("edge_discovery", discover_browser),
        ("loopback_port_selection_and_ownership", select_ports),
        ("python_imports", import_configuration),
        ("synthetic_fixture_creation", create_fixtures),
        ("configuration_and_manifest_generation", build_configurations),
        ("service_environment_construction", prepare_service_environments),
        ("subprocess_preparation", prepare_commands),
    ]


def _run(
    prior_task_disk_bytes: int = 0,
    *,
    preflight_only: bool = False,
) -> tuple[int, dict[str, Any]]:
    overall_started = time.monotonic()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise E2EBlocked("localappdata_unavailable")
    temp_root = Path(local_app_data) / "Temp"
    try:
        temp_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise E2EBlocked("evidence_workspace_unavailable") from exc
    attempt = _create_attempt(temp_root)
    workspace = attempt.workspace
    preflight_resource_metrics: dict[str, int] = {
        "prior_task_disk_bytes": prior_task_disk_bytes,
        "peak_task_owned_disk_bytes": max(0, prior_task_disk_bytes),
    }

    def guard_preflight_resources() -> None:
        if time.monotonic() - overall_started > GLOBAL_DEADLINE_SECONDS:
            raise E2EBlocked("overall_e2e_deadline_exceeded")
        # Let the explicit task_budget stage report an invalid prior measurement.
        if prior_task_disk_bytes < 0 or prior_task_disk_bytes >= TASK_DISK_BUDGET_BYTES:
            return
        task_bytes = prior_task_disk_bytes + _tree_bytes(workspace)
        preflight_resource_metrics["current_task_owned_disk_bytes"] = task_bytes
        preflight_resource_metrics["peak_task_owned_disk_bytes"] = max(
            preflight_resource_metrics["peak_task_owned_disk_bytes"], task_bytes
        )
        if task_bytes > TASK_DISK_BUDGET_BYTES:
            raise E2EBlocked("task_owned_disk_budget_exceeded")

    prepared_steps: dict[str, list[tuple[str, Any]]] = {}
    preflight_code, preflight_result = _execute_preflight(attempt, [(
        "preflight_plan_construction",
        lambda: prepared_steps.setdefault("steps", _build_preflight_steps(attempt, prior_task_disk_bytes)),
    )], resource_guard=guard_preflight_resources if preflight_only else None)
    if preflight_code:
        return preflight_code, {
            **preflight_result,
            "attempt_id": attempt.attempt_id,
            "evidence_directory": "evidence",
            "workspace_id": attempt.workspace.name,
        }
    preflight_code, preflight_result = _execute_preflight(
        attempt,
        prepared_steps["steps"],
        resource_guard=guard_preflight_resources if preflight_only else None,
    )
    if preflight_code:
        return preflight_code, {
            **preflight_result,
            "attempt_id": attempt.attempt_id,
            "evidence_directory": "evidence",
            "workspace_id": workspace.name,
        }

    if preflight_only:
        completed_stages = [
            row["stage"]
            for row in _read_jsonl(attempt.evidence_dir / "launcher-progress.jsonl")
            if row.get("event") == "stage_succeeded"
        ]
        summary = {
            "workspace_id": workspace.name,
            "attempt_id": attempt.attempt_id,
            "classification": "PREFLIGHT_READY_NO_BROWSER",
            "failure_code": None,
            "overall_elapsed_ms": round((time.monotonic() - overall_started) * 1000),
            "completed_preflight_stages": completed_stages,
            "edge_discovery": attempt.values.get("edge", {"status": "not_attempted"}),
            "fixtures_created": attempt.fixtures_created,
            "synthetic_configuration_prepared": all(
                key in attempt.values for key in ("config", "manifest", "plan", "ordinary_plan")
            ),
            "subprocess_command_prepared": "test_command" in attempt.values,
            "managed_process_state": {
                "started_count": len(attempt.managed_child_pids),
                "active_count": len(attempt.active_managed_child_pids),
            },
            "browser_launch_attempted": False,
            "service_startup_attempted": False,
            "http_requests": 0,
            "resource_metrics": preflight_resource_metrics,
        }
        attempt.last_attempted_stage = "preflight_summary_write"
        try:
            _write_json(workspace / "evidence" / "launcher-summary.json", summary)
        except Exception as exc:
            try:
                _record_preflight_failure(attempt, "EVIDENCE_CAPTURE_FAILED", exc)
            except Exception as capture_exc:
                return 1, {
                    "classification": "LAUNCHER_PREFLIGHT_BLOCKED",
                    "failure_code": "EVIDENCE_CAPTURE_FAILED",
                    "exception_class": type(capture_exc).__name__,
                    "durable_evidence": False,
                    "attempt_id": attempt.attempt_id,
                    "workspace_id": workspace.name,
                }
            return 1, {
                "classification": "LAUNCHER_PREFLIGHT_BLOCKED",
                "failure_code": "EVIDENCE_CAPTURE_FAILED",
                "exception_class": type(exc).__name__,
                "durable_evidence": True,
                "attempt_id": attempt.attempt_id,
                "workspace_id": workspace.name,
            }
        return 0, summary

    values = attempt.values
    ports = values["ports"]
    ordinary_port, bootstrap_port, vite_port = ports
    ordinary_source = values["ordinary_source"]
    ordinary_index = values["ordinary_index"]
    bootstrap_storage = values["bootstrap_storage"]
    ordinary_env = values["ordinary_env"]
    bootstrap_env = values["bootstrap_env"]
    vite_env = values["vite_env"]
    baseline_env = values["baseline_env"]
    secret_values = values["secret_values"]
    ordinary_token, bootstrap_token, invalid_token = values["tokens"]
    expected_payload = values["expected_payload"]
    ordinary_report = values["ordinary_report"]
    node = values["node"]
    vite_entry = values["vite_entry"]
    test_command = values["test_command"]
    started_at = time.time()
    deadline = overall_started + GLOBAL_DEADLINE_SECONDS

    processes: list[ChildProcess] = []
    process_records: list[dict[str, Any]] = []
    listener_records: dict[str, Any] = {}
    playwright_return: int | None = None
    playwright_elapsed_ms: int | None = None
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
                          ROOT, ordinary_env, workspace / "logs" / "ordinary-backend.log", secret_values, attempt)
        processes.append(ordinary)
        bootstrap = _spawn("bootstrap_backend", [sys.executable, "-m", "backend.bootstrap_app"],
                           ROOT, bootstrap_env, workspace / "logs" / "bootstrap-backend.log", secret_values, attempt)
        processes.append(bootstrap)

        listener_records["ordinary_backend"] = _wait_health(
            ordinary, f"http://127.0.0.1:{ordinary_port}/health", ordinary_port,
            min(deadline, time.monotonic() + 35), "ordinary_backend", sample_resources,
        )
        listener_records["bootstrap_backend"] = _wait_health(
            bootstrap, f"http://127.0.0.1:{bootstrap_port}/health", bootstrap_port,
            min(deadline, time.monotonic() + 35), "bootstrap_backend", sample_resources,
        )

        vite = _spawn("vite", [node, str(vite_entry), "--host", "127.0.0.1", "--port", str(vite_port), "--strictPort"],
                      FRONTEND, vite_env, workspace / "logs" / "vite.log", secret_values, attempt)
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
        playwright = _spawn("playwright_e2e", test_command, FRONTEND, playwright_env,
                            workspace / "logs" / "playwright-redacted.log", secret_values, attempt)
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
                attempt.child_stopped(child.process.pid)
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
        "attempt_id": attempt.attempt_id,
        "repository_commit": attempt.git_identity["head"],
        "repository_branch": attempt.git_identity["branch"],
        "launcher_source_sha256": attempt.source_sha256,
        "edge_discovery": values["edge"],
        "preflight_progress": _read_jsonl(workspace / "evidence" / "launcher-progress.jsonl"),
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
        "workspace_id": workspace.name,
        "attempt_id": attempt.attempt_id,
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


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run one isolated authenticated browser E2E invocation.")
    parser.add_argument(
        "--prior-task-disk-bytes", type=int, default=0,
        help="measured task-owned bytes already consumed by prerequisite installation in this task",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="prepare and verify browser prerequisites without starting services or a browser",
    )
    args = parser.parse_args(argv)
    try:
        exit_code, summary = _run(
            args.prior_task_disk_bytes,
            preflight_only=args.preflight_only,
        )
    except EvidenceCaptureFailed as exc:
        print(json.dumps({
            "classification": "LAUNCHER_PREFLIGHT_BLOCKED",
            "failure_code": "EVIDENCE_CAPTURE_FAILED",
            "exception_class": type(exc).__name__,
            "durable_evidence": False,
        }, sort_keys=True))
        return 1
    except E2EBlocked as exc:
        print(json.dumps({
            "classification": "LAUNCHER_PREFLIGHT_BLOCKED",
            "failure_code": exc.code,
            "exception_class": type(exc).__name__,
            "durable_evidence": False,
        }, sort_keys=True))
        return 1
    except Exception as exc:
        print(json.dumps({
            "classification": "LAUNCHER_PREFLIGHT_BLOCKED",
            "failure_code": "launcher_start_unexpected",
            "exception_class": type(exc).__name__,
            "durable_evidence": False,
        }, sort_keys=True))
        return 1
    print(json.dumps(summary, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
