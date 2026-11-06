"""Isolated Linux browser acceptance with fail-closed listener ownership."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import ipaddress
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
import time
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

import psutil


ROOT = Path(__file__).resolve().parents[1]
MAX_PROCESS_DEPTH = 64
MAX_SELECTED_PORT_ROWS = 32
MAX_EVIDENCE_BYTES = 8 * 1024 * 1024
SERVICE_START_TIMEOUT_SECONDS = 35.0
PLAYWRIGHT_TIMEOUT_SECONDS = 660.0
PROCESS_TERM_GRACE_SECONDS = 3.0
PROCESS_KILL_GRACE_SECONDS = 4.0
TCP_STATE_BY_HEX = {
    "01": "ESTABLISHED", "02": "SYN_SENT", "03": "SYN_RECV",
    "04": "FIN_WAIT1", "05": "FIN_WAIT2", "06": "TIME_WAIT",
    "07": "CLOSE", "08": "CLOSE_WAIT", "09": "LAST_ACK",
    "0A": "LISTEN", "0B": "CLOSING", "0C": "NEW_SYN_RECV",
}
KNOWN_PSUTIL_TCP_STATES = {
    "ESTABLISHED", "SYN_SENT", "SYN_RECV", "FIN_WAIT1", "FIN_WAIT2",
    "TIME_WAIT", "CLOSE", "CLOSE_WAIT", "LAST_ACK", "LISTEN", "CLOSING",
}


class E2EBlocked(RuntimeError):
    """A bounded failure carrying a safe, stable code only."""

    def __init__(self, code: str):
        self.code = code if re.fullmatch(r"[a-z][a-z0-9_]{1,79}", code or "") else "linux_e2e_blocked"
        super().__init__(self.code)


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    parent_pid: int
    session_id: int
    start_ticks: int
    state: str

    def creation_key(self) -> tuple[int, int, int, int]:
        return (self.pid, self.start_ticks, self.parent_pid, self.session_id)


@dataclass(frozen=True)
class ProcTcpRow:
    family: int
    address: str | None
    port: int
    state: str
    inode: int


@dataclass(frozen=True)
class PsutilTcpRow:
    family: int
    socket_type: int
    address: str
    port: int
    state: str
    pid: int | None


@dataclass(frozen=True)
class ListenerSnapshot:
    port: int
    proc_rows: tuple[ProcTcpRow, ...]
    psutil_rows: tuple[PsutilTcpRow, ...]
    query_complete: bool = True

    @property
    def proc_listeners(self) -> tuple[ProcTcpRow, ...]:
        return tuple(row for row in self.proc_rows if row.state == "LISTEN")

    @property
    def psutil_listeners(self) -> tuple[PsutilTcpRow, ...]:
        return tuple(row for row in self.psutil_rows if row.state == psutil.CONN_LISTEN)

    def sanitized(self) -> dict[str, Any]:
        return {
            "query_complete": self.query_complete,
            "port": self.port,
            "proc_matching_count": len(self.proc_rows),
            "psutil_matching_count": len(self.psutil_rows),
            "proc_listener_count": len(self.proc_listeners),
            "psutil_listener_count": len(self.psutil_listeners),
            "proc_listeners": [
                {
                    "family": "ipv4" if row.family == socket.AF_INET else "ipv6",
                    "address": row.address,
                    "port": row.port,
                    "state": row.state,
                    "inode": row.inode,
                }
                for row in self.proc_listeners[:MAX_SELECTED_PORT_ROWS]
            ],
            "psutil_listeners": [
                {
                    "family": "ipv4" if row.family == socket.AF_INET else "ipv6",
                    "address": row.address,
                    "port": row.port,
                    "state": row.state,
                    "pid": row.pid,
                }
                for row in self.psutil_listeners[:MAX_SELECTED_PORT_ROWS]
            ],
        }


@dataclass(frozen=True)
class OwnedListener:
    address: str
    port: int
    pid: int
    inode: int
    ancestry: tuple[ProcessIdentity, ...]

    def sanitized(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "port": self.port,
            "pid": self.pid,
            "inode": self.inode,
            "ancestry_pids_root_to_owner": [identity.pid for identity in self.ancestry],
            "ancestry_creation_identities_verified": True,
            "ancestry_depth": max(0, len(self.ancestry) - 1),
        }


@dataclass
class ManagedProcess:
    name: str
    popen: subprocess.Popen[bytes]
    identity: ProcessIdentity
    port: int | None = None


def validate_port(port: object) -> int:
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise E2EBlocked("listener_port_invalid")
    return port


def validate_linux_primitives(platform: str | None = None, proc_root: Path = Path("/proc")) -> None:
    selected = sys.platform if platform is None else platform
    if selected != "linux":
        raise E2EBlocked("linux_runner_required")
    if not proc_root.is_dir():
        raise E2EBlocked("procfs_unavailable")
    if not callable(getattr(os, "pidfd_open", None)) or not callable(getattr(signal, "pidfd_send_signal", None)):
        raise E2EBlocked("pidfd_process_ownership_unavailable")


def parse_proc_stat(pid: int, raw: str) -> ProcessIdentity:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid < 1 or not isinstance(raw, str):
        raise E2EBlocked("proc_stat_invalid")
    closing = raw.rfind(")")
    try:
        reported_pid = int(raw[: raw.find(" (")].strip())
        fields = raw[closing + 1 :].strip().split()
        state = fields[0]
        parent_pid = int(fields[1])
        session_id = int(fields[3])
        start_ticks = int(fields[19])
    except (ValueError, IndexError) as exc:
        raise E2EBlocked("proc_stat_invalid") from exc
    if closing < 0 or reported_pid != pid or state not in {"R", "S", "D", "T", "t", "Z", "X", "I", "W", "P"}:
        raise E2EBlocked("proc_stat_invalid")
    if parent_pid < 0 or session_id < 0 or start_ticks < 0:
        raise E2EBlocked("proc_stat_invalid")
    return ProcessIdentity(pid, parent_pid, session_id, start_ticks, state)


def read_process_identity(pid: int, proc_root: Path = Path("/proc")) -> ProcessIdentity | None:
    try:
        raw = (proc_root / str(pid) / "stat").read_text(encoding="ascii")
    except (FileNotFoundError, ProcessLookupError):
        return None
    except OSError as exc:
        raise E2EBlocked("proc_process_identity_unavailable") from exc
    identity = parse_proc_stat(pid, raw)
    return None if identity.state in {"Z", "X"} else identity


def _valid_hex(value: str, size: int) -> bool:
    return len(value) == size and re.fullmatch(r"[0-9A-Fa-f]+", value) is not None


def parse_proc_tcp_table(text: str, family: int, port: int) -> tuple[ProcTcpRow, ...]:
    validate_port(port)
    address_hex_size = 8 if family == socket.AF_INET else 32 if family == socket.AF_INET6 else 0
    if not address_hex_size or not isinstance(text, str):
        raise E2EBlocked("proc_tcp_table_invalid")
    lines = text.splitlines()
    if not lines or not {"local_address", "st", "inode"}.issubset(set(lines[0].split())):
        raise E2EBlocked("proc_tcp_table_incomplete")
    rows: list[ProcTcpRow] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) < 10:
            raise E2EBlocked("proc_tcp_row_invalid")
        try:
            local = fields[1].split(":")
            if len(local) != 2 or not _valid_hex(local[0], address_hex_size) or not _valid_hex(local[1], 4):
                raise ValueError("endpoint")
            local_port = int(local[1], 16)
        except ValueError as exc:
            raise E2EBlocked("proc_tcp_row_invalid") from exc
        if local_port != port:
            continue
        state_hex = fields[3].upper()
        if state_hex not in TCP_STATE_BY_HEX:
            raise E2EBlocked("proc_tcp_state_unknown")
        try:
            inode = int(fields[9], 10)
        except ValueError as exc:
            raise E2EBlocked("proc_tcp_row_invalid") from exc
        if inode < 0:
            raise E2EBlocked("proc_tcp_row_invalid")
        address = None
        if family == socket.AF_INET:
            try:
                address = socket.inet_ntop(socket.AF_INET, bytes.fromhex(local[0])[::-1])
            except (ValueError, OSError) as exc:
                raise E2EBlocked("proc_tcp_address_invalid") from exc
        state = TCP_STATE_BY_HEX[state_hex]
        if state == "LISTEN" and inode == 0:
            raise E2EBlocked("proc_tcp_listener_inode_missing")
        rows.append(ProcTcpRow(family, address, local_port, state, inode))
        if len(rows) > MAX_SELECTED_PORT_ROWS:
            raise E2EBlocked("listener_observation_limit_exceeded")
    return tuple(rows)


def _local_address(value: object) -> tuple[str, int]:
    if hasattr(value, "ip") and hasattr(value, "port"):
        address, port = getattr(value, "ip"), getattr(value, "port")
    elif isinstance(value, (tuple, list)) and len(value) >= 2:
        address, port = value[0], value[1]
    else:
        raise E2EBlocked("psutil_local_address_invalid")
    if not isinstance(address, str) or isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise E2EBlocked("psutil_local_address_invalid")
    try:
        parsed = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError as exc:
        raise E2EBlocked("psutil_local_address_invalid") from exc
    return parsed.compressed, port


def _psutil_state_values() -> set[str]:
    names = (
        "CONN_ESTABLISHED", "CONN_SYN_SENT", "CONN_SYN_RECV", "CONN_FIN_WAIT1",
        "CONN_FIN_WAIT2", "CONN_TIME_WAIT", "CONN_CLOSE", "CONN_CLOSE_WAIT",
        "CONN_LAST_ACK", "CONN_LISTEN", "CONN_CLOSING",
    )
    return {str(getattr(psutil, name)) for name in names if hasattr(psutil, name)}


def parse_psutil_tcp_rows(connections: Iterable[object], port: int) -> tuple[PsutilTcpRow, ...]:
    validate_port(port)
    rows: list[PsutilTcpRow] = []
    valid_states = _psutil_state_values()
    try:
        iterator = iter(connections)
        for connection in iterator:
            try:
                family = int(getattr(connection, "family"))
                socket_type = int(getattr(connection, "type"))
                address, local_port = _local_address(getattr(connection, "laddr"))
            except (AttributeError, TypeError, ValueError) as exc:
                raise E2EBlocked("psutil_connection_incomplete") from exc
            if local_port != port:
                continue
            if family not in {socket.AF_INET, socket.AF_INET6} or socket_type != socket.SOCK_STREAM:
                raise E2EBlocked("psutil_connection_protocol_invalid")
            parsed = ipaddress.ip_address(address)
            if (family == socket.AF_INET) != (parsed.version == 4):
                raise E2EBlocked("psutil_connection_family_mismatch")
            state = getattr(connection, "status", None)
            if not isinstance(state, str) or state not in valid_states:
                raise E2EBlocked("psutil_tcp_state_unknown")
            raw_pid = getattr(connection, "pid", None)
            if raw_pid is not None and (isinstance(raw_pid, bool) or not isinstance(raw_pid, int) or raw_pid < 1):
                raise E2EBlocked("psutil_listener_pid_invalid")
            if state == psutil.CONN_LISTEN and raw_pid is None:
                raise E2EBlocked("psutil_listener_pid_missing")
            rows.append(PsutilTcpRow(family, socket_type, address, local_port, state, raw_pid))
            if len(rows) > MAX_SELECTED_PORT_ROWS:
                raise E2EBlocked("listener_observation_limit_exceeded")
    except E2EBlocked:
        raise
    except Exception as exc:
        raise E2EBlocked("psutil_tcp_query_failed") from exc
    return tuple(rows)


class LinuxListenerVerifier:
    def __init__(
        self,
        proc_root: Path = Path("/proc"),
        connection_provider: Callable[..., Iterable[object]] | None = None,
        fd_targets: Callable[[int], Sequence[str]] | None = None,
        identity_reader: Callable[[int], ProcessIdentity | None] | None = None,
    ) -> None:
        self.proc_root = Path(proc_root)
        self.connection_provider = connection_provider or psutil.net_connections
        self.fd_targets = fd_targets or self._read_fd_targets
        self.identity_reader = identity_reader or (lambda pid: read_process_identity(pid, self.proc_root))

    def snapshot(self, port: int) -> ListenerSnapshot:
        selected = validate_port(port)
        try:
            tcp = (self.proc_root / "net" / "tcp").read_text(encoding="ascii")
            tcp6 = (self.proc_root / "net" / "tcp6").read_text(encoding="ascii")
        except OSError as exc:
            raise E2EBlocked("proc_tcp_query_failed") from exc
        proc_rows = (
            *parse_proc_tcp_table(tcp, socket.AF_INET, selected),
            *parse_proc_tcp_table(tcp6, socket.AF_INET6, selected),
        )
        try:
            rows = parse_psutil_tcp_rows(self.connection_provider(kind="tcp"), selected)
        except E2EBlocked:
            raise
        except Exception as exc:
            raise E2EBlocked("psutil_tcp_query_failed") from exc
        return ListenerSnapshot(selected, tuple(proc_rows), tuple(rows), True)

    def verify_owned_listener(self, snapshot: ListenerSnapshot, root: ProcessIdentity) -> OwnedListener:
        if not snapshot.query_complete:
            raise E2EBlocked("listener_query_incomplete")
        proc_rows, ps_rows = snapshot.proc_listeners, snapshot.psutil_listeners
        if len(proc_rows) != len(ps_rows):
            raise E2EBlocked("listener_inventory_disagreement")
        if len(proc_rows) != 1:
            raise E2EBlocked("listener_count_invalid")
        proc_row, ps_row = proc_rows[0], ps_rows[0]
        if proc_row.family != socket.AF_INET or ps_row.family != socket.AF_INET:
            raise E2EBlocked("listener_ipv4_required")
        if ps_row.socket_type != socket.SOCK_STREAM or ps_row.state != psutil.CONN_LISTEN:
            raise E2EBlocked("listener_tcp_state_invalid")
        if proc_row.state != "LISTEN" or ps_row.state != "LISTEN":
            raise E2EBlocked("listener_tcp_state_invalid")
        if proc_row.address != "127.0.0.1" or ps_row.address != "127.0.0.1":
            raise E2EBlocked("listener_loopback_required")
        if proc_row.port != snapshot.port or ps_row.port != snapshot.port:
            raise E2EBlocked("listener_port_mismatch")
        if ps_row.pid is None or isinstance(ps_row.pid, bool) or ps_row.pid < 1:
            raise E2EBlocked("listener_pid_invalid")
        if proc_row.inode <= 0:
            raise E2EBlocked("listener_inode_invalid")
        chain = self.verify_process_ancestry(ps_row.pid, root)
        if f"socket:[{proc_row.inode}]" not in self.fd_targets(ps_row.pid):
            raise E2EBlocked("listener_inode_owner_mismatch")
        self._recheck_ancestry(chain)
        return OwnedListener(ps_row.address, snapshot.port, ps_row.pid, proc_row.inode, chain)

    def assert_zero_listeners(self, snapshot: ListenerSnapshot) -> None:
        if not snapshot.query_complete:
            raise E2EBlocked("listener_cleanup_query_incomplete")
        proc_count = len(snapshot.proc_listeners)
        psutil_count = len(snapshot.psutil_listeners)
        if proc_count != psutil_count:
            raise E2EBlocked("listener_cleanup_inventory_disagreement")
        if proc_count:
            raise E2EBlocked("listener_cleanup_listener_remains")

    def verify_process_ancestry(self, owner_pid: int, root: ProcessIdentity) -> tuple[ProcessIdentity, ...]:
        current_root = self.identity_reader(root.pid)
        if current_root is None or current_root.creation_key() != root.creation_key() or current_root.state == "Z":
            raise E2EBlocked("process_root_identity_changed")
        if root.session_id != root.pid:
            raise E2EBlocked("process_root_session_invalid")
        reverse: list[ProcessIdentity] = []
        seen: set[int] = set()
        pid = owner_pid
        for _ in range(MAX_PROCESS_DEPTH):
            if pid in seen:
                raise E2EBlocked("process_ancestry_cycle")
            seen.add(pid)
            identity = self.identity_reader(pid)
            if identity is None or identity.state == "Z":
                raise E2EBlocked("process_ancestry_process_not_live")
            if identity.session_id != root.pid:
                raise E2EBlocked("process_ancestry_session_mismatch")
            reverse.append(identity)
            if pid == root.pid:
                if identity.creation_key() != root.creation_key():
                    raise E2EBlocked("process_root_identity_changed")
                break
            if identity.parent_pid <= 0 or identity.parent_pid == pid:
                raise E2EBlocked("process_ancestry_incomplete")
            pid = identity.parent_pid
        else:
            raise E2EBlocked("process_ancestry_depth_exceeded")
        if reverse[-1].pid != root.pid:
            raise E2EBlocked("process_ancestry_root_missing")
        chain = tuple(reversed(reverse))
        self._recheck_ancestry(chain)
        return chain

    def _recheck_ancestry(self, chain: Sequence[ProcessIdentity]) -> None:
        for expected in chain:
            current = self.identity_reader(expected.pid)
            if current is None or current.state == "Z" or current.creation_key() != expected.creation_key():
                raise E2EBlocked("process_ancestry_identity_changed")

    def _read_fd_targets(self, pid: int) -> tuple[str, ...]:
        fd_dir = self.proc_root / str(pid) / "fd"
        targets: list[str] = []
        try:
            with os.scandir(fd_dir) as entries:
                for entry in entries:
                    try:
                        target = os.readlink(entry.path)
                    except FileNotFoundError:
                        continue
                    except OSError as exc:
                        raise E2EBlocked("proc_fd_inventory_incomplete") from exc
                    if re.fullmatch(r"socket:\[\d+\]", target):
                        targets.append(target)
        except E2EBlocked:
            raise
        except OSError as exc:
            raise E2EBlocked("proc_fd_inventory_incomplete") from exc
        return tuple(targets)


def process_table(proc_root: Path = Path("/proc")) -> dict[int, ProcessIdentity]:
    processes: dict[int, ProcessIdentity] = {}
    try:
        entries = list(os.scandir(proc_root))
    except OSError as exc:
        raise E2EBlocked("proc_process_table_unavailable") from exc
    for entry in entries:
        if not entry.name.isdecimal():
            continue
        pid = int(entry.name)
        try:
            raw = Path(entry.path, "stat").read_text(encoding="ascii")
        except (FileNotFoundError, ProcessLookupError):
            continue
        except OSError as exc:
            raise E2EBlocked("proc_process_table_incomplete") from exc
        processes[pid] = parse_proc_stat(pid, raw)
    return processes


def task_process_tree(
    root: ProcessIdentity,
    processes: Mapping[int, ProcessIdentity],
    *,
    allow_root_missing: bool = False,
) -> tuple[ProcessIdentity, ...]:
    current_root = processes.get(root.pid)
    if current_root is not None and current_root.creation_key() != root.creation_key():
        raise E2EBlocked("process_root_identity_changed")
    if current_root is None and not allow_root_missing:
        raise E2EBlocked("process_root_identity_changed")
    selected = {root.pid: current_root} if current_root is not None else {}
    changed = True
    while changed:
        changed = False
        for identity in processes.values():
            if identity.pid not in selected and (identity.session_id == root.pid or identity.parent_pid in selected):
                selected[identity.pid] = identity
                changed = True
    return tuple(selected.values())

def validate_chromium_executable(value: str | None) -> Path:
    if not value:
        raise E2EBlocked("linux_chromium_unavailable")
    candidate = Path(value)
    if not candidate.is_absolute():
        raise E2EBlocked("linux_chromium_path_invalid")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise E2EBlocked("linux_chromium_path_invalid") from exc
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise E2EBlocked("linux_chromium_path_invalid")
    return resolved


def validate_browser_version(value: str) -> str:
    normalized = value.strip() if isinstance(value, str) else ""
    if not re.fullmatch(r"(?:Chromium|Google Chrome)\s+[0-9A-Za-z.+-]{1,48}", normalized):
        raise E2EBlocked("linux_chromium_version_unavailable")
    return normalized


def _process_record(process: ManagedProcess) -> dict[str, Any]:
    return {
        "name": process.name,
        "root_pid": process.identity.pid,
        "root_start_ticks": process.identity.start_ticks,
        "root_session_id": process.identity.session_id,
        "root_creation_identity_verified": True,
    }


def _outside_checkout(path: Path) -> None:
    try:
        path.resolve(strict=False).relative_to(ROOT)
    except ValueError:
        return
    raise E2EBlocked("evidence_must_be_outside_checkout")


def run_acceptance(frontend_dir: Path, evidence_dir: Path | None = None) -> tuple[int, dict[str, Any]]:
    validate_linux_primitives()
    if not hasattr(psutil, "CONN_LISTEN"):
        raise E2EBlocked("psutil_listener_state_unavailable")
    frontend = frontend_dir.resolve(strict=True)
    if not frontend.is_dir() or frontend == ROOT / "frontend" or ROOT in frontend.parents:
        raise E2EBlocked("frontend_worktree_must_be_temporary_copy")
    node = shutil.which("node")
    if not node:
        raise E2EBlocked("node_unavailable")
    for required in (
        frontend / "node_modules" / "vite" / "bin" / "vite.js",
        frontend / "node_modules" / "@playwright" / "test" / "cli.js",
    ):
        if not required.is_file():
            raise E2EBlocked("frontend_dependencies_incomplete")
    browser_executable = validate_chromium_executable(os.environ.get("E2E_BROWSER_EXECUTABLE_PATH"))
    browser_version = validate_browser_version(os.environ.get("E2E_BROWSER_VERSION", ""))

    external_evidence = (
        evidence_dir.resolve(strict=False)
        if evidence_dir is not None
        else (Path(tempfile.gettempdir()) / f"rag-pdf-linux-e2e-evidence-{secrets.token_hex(8)}").resolve(strict=False)
    )
    _outside_checkout(external_evidence)
    if external_evidence.exists() or not external_evidence.parent.is_dir():
        raise E2EBlocked("evidence_destination_not_fresh")

    report: dict[str, Any] = {
        "schema_version": 1,
        "classification": "LINUX_BROWSER_EXECUTION_BLOCKED",
        "platform": "linux",
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "psutil_version": psutil.__version__,
        "browser": {
            "selection": "chromium",
            "engine": browser_version.split(" ", 1)[0],
            "executable": "runner_preinstalled",
            "version": browser_version,
        },
        "stage": "workspace_preparation",
        "fixture": {},
        "selected_ports": {},
        "services": {},
        "scenarios": [],
        "scenario_count": 0,
        "security": {
            "tokens_unique": False,
            "tokens_absent_from_evidence": False,
            "browser_guard_violations": None,
        },
        "cleanup": {"complete": False, "processes": {}, "ports_zero_listeners": {}},
        "original_failure_code": None,
        "failure_code": None,
        "evidence_manifest_sha256": None,
    }
    tokens: tuple[str, str, str] = ()
    managed: list[ManagedProcess] = []
    ports: tuple[int, ...] = ()
    verifier = LinuxListenerVerifier()
    playwright_result_path: Path | None = None
    observations_path: Path | None = None
    evidence_files: dict[str, bytes] = {}
    first_failure: E2EBlocked | None = None

    with tempfile.TemporaryDirectory(prefix="rag-pdf-linux-e2e-") as temporary:
        workspace = Path(temporary).resolve(strict=True)
        try:
            paths = create_workspace_layout(workspace)
            tokens = generate_test_tokens()
            report["security"]["tokens_unique"] = len(set(tokens)) == 3
            report["stage"] = "synthetic_fixture"
            fixture = build_synthetic_fixture(paths)
            report["fixture"] = {
                "pdf_count": 2,
                "page_count_per_pdf": fixture["pdf_page_count"],
                "document_count": fixture["document_count"],
                "manifest_hash": fixture["manifest_hash"],
                "index_fingerprint": fixture["index_fingerprint"],
                "run_fingerprint": fixture["run_fingerprint"],
            }
            expected_path = workspace / "expected-fixture.json"
            write_json_fsync(expected_path, fixture["expected"])
            guard_script = workspace / "guarded_service.py"
            write_guarded_service(guard_script)
            playwright_result_path = workspace / "playwright-results.json"
            observations_path = workspace / "api-observations.jsonl"

            ordinary_port, bootstrap_port, vite_port = select_loopback_ports(3)
            ports = (ordinary_port, bootstrap_port, vite_port)
            report["selected_ports"] = {
                "ordinary": ordinary_port,
                "bootstrap": bootstrap_port,
                "vite": vite_port,
            }
            baseline = clean_child_environment(os.environ, workspace)
            ordinary_token, bootstrap_token, invalid_token = tokens
            ordinary_env = service_environment(
                baseline, "ordinary", ordinary_token, paths, ordinary_port, fixture["report_path"]
            )
            bootstrap_env = service_environment(
                baseline, "bootstrap", bootstrap_token, paths, bootstrap_port
            )

            report["stage"] = "ordinary_service_start"
            ordinary = spawn_owned(
                "ordinary", [sys.executable, "-u", str(guard_script)], ROOT, ordinary_env, ordinary_port
            )
            managed.append(ordinary)
            report["processes"] = [_process_record(item) for item in managed]

            report["stage"] = "bootstrap_service_start"
            bootstrap = spawn_owned(
                "bootstrap", [sys.executable, "-u", str(guard_script)], ROOT, bootstrap_env, bootstrap_port
            )
            managed.append(bootstrap)
            report["processes"] = [_process_record(item) for item in managed]

            vite_env = dict(baseline)
            vite_env.update(
                {
                    "RAG_WORKBENCH_DEV_PROXY_TARGET": f"http://127.0.0.1:{ordinary_port}",
                    "RAG_WORKBENCH_BOOTSTRAP_DEV_PROXY_TARGET": f"http://127.0.0.1:{bootstrap_port}",
                }
            )
            report["stage"] = "vite_start"
            vite = start_vite(frontend, vite_port, vite_env, node)
            managed.append(vite)
            report["processes"] = [_process_record(item) for item in managed]

            report["stage"] = "listener_and_health_acceptance"
            for process in (ordinary, bootstrap, vite):
                listener, health = wait_for_owned_health(process, verifier)
                report["services"][process.name] = {
                    "root_pid": process.identity.pid,
                    "root_start_ticks": process.identity.start_ticks,
                    "listener": listener.sanitized(),
                    "health": health,
                }

            browser_env = dict(baseline)
            browser_env.update(
                {
                    "E2E_BASE_URL": f"http://127.0.0.1:{vite_port}",
                    "E2E_ORDINARY_TOKEN": ordinary_token,
                    "E2E_BOOTSTRAP_TOKEN": bootstrap_token,
                    "E2E_INVALID_TOKEN": invalid_token,
                    "E2E_EXPECTED_JSON": expected_path.read_text(encoding="utf-8"),
                    "E2E_RESULT_FILE": str(playwright_result_path),
                    "E2E_OBSERVATIONS_FILE": str(observations_path),
                    "E2E_BROWSER": "chromium",
                    "E2E_BROWSER_EXECUTABLE_PATH": str(browser_executable),
                    "PLAYWRIGHT_OUTPUT_DIR": str(paths["playwright_results"]),
                }
            )
            report["stage"] = "playwright_execution"
            playwright = start_playwright(frontend, browser_env, node)
            managed.append(playwright)
            report["processes"] = [_process_record(item) for item in managed]
            exit_code = wait_playwright(playwright)
            report["playwright_exit_code"] = exit_code
            if exit_code == 124:
                raise E2EBlocked("playwright_execution_timeout")

            report["stage"] = "playwright_result_validation"
            result, observations = load_playwright_result(playwright_result_path, observations_path)
            report["scenario_count"] = result.get("completed_tests", 0)
            report["scenarios"] = [
                {key: item.get(key) for key in ("title", "status", "retry")}
                for item in result.get("tests", [])
                if isinstance(item, dict)
            ]
            assert_browser_result(result, observations)
            if exit_code != 0:
                raise E2EBlocked("playwright_exit_nonzero")
            evidence_files = copy_sanitized_playwright_files(
                playwright_result_path, observations_path, tokens
            )
            report["security"]["browser_guard_violations"] = 0
            report["security"]["api_request_count"] = sum(
                len(item.get("requests", [])) for item in observations
            )
            report["security"]["token_roles"] = sorted(
                {
                    row.get("auth_role")
                    for item in observations
                    for row in item.get("requests", [])
                    if isinstance(row, dict)
                }
            )
            report["stage"] = "post_browser_integrity"
            assert_no_bootstrap_index_mutation(paths["bootstrap_index"])
            report["bootstrap_index_mutation"] = False
            for process in (ordinary, bootstrap, vite):
                snapshot = verifier.snapshot(process.port or 0)
                verified = verifier.verify_owned_listener(snapshot, process.identity)
                report["services"][process.name]["listener_after_browser"] = verified.sanitized()
        except E2EBlocked as exc:
            first_failure = exc
            report["original_failure_code"] = exc.code
        except Exception as exc:
            first_failure = E2EBlocked("linux_e2e_internal_error")
            report["original_failure_code"] = first_failure.code
            report["failure_exception_type"] = type(exc).__name__
        finally:
            if tokens and playwright_result_path is not None and observations_path is not None:
                try:
                    if playwright_result_path.is_file() and observations_path.is_file():
                        evidence_files = copy_sanitized_playwright_files(
                            playwright_result_path, observations_path, tokens
                        )
                except E2EBlocked as exc:
                    report["playwright_evidence_failure_code"] = exc.code

            cleanup_failures: list[str] = []
            for process in reversed(managed):
                try:
                    report["cleanup"]["processes"][process.name] = stop_owned_process_tree(process)
                except E2EBlocked as exc:
                    cleanup_failures.append(exc.code)
                    report["cleanup"]["processes"][process.name] = {
                        "verified": False, "failure_code": exc.code,
                    }
            if ports:
                for port in ports:
                    try:
                        snapshot = verifier.snapshot(port)
                        verifier.assert_zero_listeners(snapshot)
                        report["cleanup"]["ports_zero_listeners"][str(port)] = {
                            "query_complete": True,
                            "proc_listener_count": len(snapshot.proc_listeners),
                            "psutil_listener_count": len(snapshot.psutil_listeners),
                            "verified": True,
                        }
                    except E2EBlocked as exc:
                        cleanup_failures.append(exc.code)
                        report["cleanup"]["ports_zero_listeners"][str(port)] = {
                            "query_complete": False,
                            "verified": False,
                            "failure_code": exc.code,
                        }
            report["cleanup"]["complete"] = not cleanup_failures and len(report["cleanup"]["processes"]) == len(managed)
            if cleanup_failures:
                report["classification"] = "INTEGRITY_OR_CLEANUP_BLOCKED"
                report["failure_code"] = "process_or_listener_cleanup_unverified"
                report["cleanup"]["failure_codes"] = cleanup_failures
            elif first_failure is not None:
                report["failure_code"] = first_failure.code
                report["classification"] = classify_failure(
                    report["stage"], first_failure.code, cleanup_complete=True
                )
            else:
                report["classification"] = "LINUX_BROWSER_E2E_VERIFIED"
                report["failure_code"] = None
            if tokens:
                report["security"]["tokens_absent_from_evidence"] = not any(
                    contains_secret(value, tokens) for value in evidence_files.values()
                )
            try:
                manifest_hash = write_evidence_bundle(
                    external_evidence, report, evidence_files, tokens
                )
                report["evidence_manifest_sha256"] = manifest_hash
                print(
                    json.dumps(
                        {
                            "classification": report["classification"],
                            "failure_code": report["failure_code"],
                            "evidence_manifest_sha256": manifest_hash,
                            "scenario_count": report["scenario_count"],
                        },
                        sort_keys=True,
                    )
                )
            except E2EBlocked as exc:
                report["classification"] = "INTEGRITY_OR_CLEANUP_BLOCKED"
                report["failure_code"] = "evidence_integrity_unverified"
                report["evidence_failure_code"] = exc.code
                print(
                    json.dumps(
                        {"classification": report["classification"], "failure_code": report["failure_code"]},
                        sort_keys=True,
                    ),
                    file=sys.stderr,
                )
                return 3, report

    if report["classification"] == "LINUX_BROWSER_E2E_VERIFIED":
        return 0, report
    if report["classification"] == "INTEGRITY_OR_CLEANUP_BLOCKED":
        return 3, report
    if report["classification"] == "LINUX_BROWSER_BEHAVIOR_FAILED":
        return 1, report
    return 2, report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend-dir", required=True, type=Path)
    parser.add_argument("--evidence-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        code, _report = run_acceptance(args.frontend_dir, args.evidence_dir)
        return code
    except E2EBlocked as exc:
        print(
            json.dumps(
                {"classification": "LINUX_BROWSER_EXECUTION_BLOCKED", "failure_code": exc.code},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


def write_json_fsync(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def write_synthetic_pdf(path: Path) -> None:
    import pymupdf

    document = pymupdf.open()
    document.new_page().insert_text((72, 72), "Synthetic authenticated browser fixture. No user corpus.")
    document.save(path)
    document.close()
    reopened = pymupdf.open(path)
    try:
        if len(reopened) != 1 or "Synthetic authenticated browser fixture" not in reopened[0].get_text():
            raise E2EBlocked("synthetic_pdf_verification_failed")
    finally:
        reopened.close()


def write_guarded_service(path: Path) -> None:
    source = '''import os

def forbidden(*_args, **_kwargs):
    os._exit(91)

import chromadb
from backend.embeddings.text import TextEmbeddingAdapter
from backend.embeddings.image import ImageEmbeddingAdapter
from backend.engine.reranking import CrossEncoderReranker
from backend.engine.generation import LocalGenerationAdapter
from backend.vectorstore.chroma_store import ChromaVectorStore
chromadb.PersistentClient = forbidden
TextEmbeddingAdapter.load = forbidden
ImageEmbeddingAdapter.load = forbidden
CrossEncoderReranker.load = forbidden
LocalGenerationAdapter.generate = forbidden
ChromaVectorStore.open_existing = forbidden
ChromaVectorStore.create_new = forbidden

role = os.environ.get("RAG_E2E_SERVICE_ROLE")
if role == "ordinary":
    from backend.app.runtime import main
elif role == "bootstrap":
    from backend.bootstrap_app.runtime import main
else:
    raise SystemExit(92)
raise SystemExit(main())
'''
    path.write_text(source, encoding="utf-8", newline="\n")
    compile(source, str(path), "exec")


def build_synthetic_fixture(paths: Mapping[str, Path]) -> dict[str, Any]:
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    from backend.config import ResolvedRunConfig
    from backend.engine.manifest import build_document_manifest
    from backend.experiments.run_plan import build_run_plan
    from notebooks.config import Config

    write_synthetic_pdf(paths["ordinary_source"] / "synthetic.pdf")
    write_synthetic_pdf(paths["bootstrap_source"] / "synthetic.pdf")
    bootstrap_root = paths["bootstrap_root"]
    config = ResolvedRunConfig.from_legacy(
        Config(
            pdf_dir=str(paths["bootstrap_source"]),
            images_dir=str(bootstrap_root / "images"),
            database_dir=str(bootstrap_root / "database"),
            results_dir=str(bootstrap_root / "results"),
            model_comparison_results_dir=str(bootstrap_root / "comparisons"),
            retrieval_results_dir=str(bootstrap_root / "retrieval-results"),
            reset_collections_on_start=False,
        )
    )
    if config.reset_collections_on_start or config.workbench_policy.allow_destructive_index_reset or config.workbench_policy.allow_collection_delete:
        raise E2EBlocked("synthetic_configuration_destructive")
    manifest = build_document_manifest(paths["bootstrap_source"])
    plan = build_run_plan(config, manifest.content_hash)
    ordinary_config = ResolvedRunConfig.from_legacy(
        Config(
            pdf_dir=str(paths["ordinary_source"]),
            images_dir=str(paths["ordinary_root"] / "images"),
            database_dir=str(paths["ordinary_root"] / "database"),
            results_dir=str(paths["ordinary_root"] / "results"),
            model_comparison_results_dir=str(paths["ordinary_root"] / "comparisons"),
            retrieval_results_dir=str(paths["ordinary_root"] / "retrieval-results"),
            reset_collections_on_start=False,
        )
    )
    ordinary_manifest = build_document_manifest(paths["ordinary_source"])
    ordinary_plan = build_run_plan(ordinary_config, ordinary_manifest.content_hash)
    report_root = paths["ordinary_index"] / "build-artifacts" / "synthetic-browser-e2e"
    report_root.mkdir(parents=True, exist_ok=True)
    report_path = report_root / "build-report.json"
    write_json_fsync(
        report_path,
        {
            "report_schema_version": "1",
            "build_id": "synthetic-browser-e2e",
            "artifact_root": str(report_root),
            "report_path": str(report_path),
            "index_fingerprint": ordinary_plan.index_fingerprint,
            "document_manifest_hash": ordinary_manifest.content_hash,
            "status": "completed",
            "partial_persistence": False,
            "text_records_persisted": 0,
            "image_records_persisted": 0,
        },
    )
    expected = {
        "document_count": len(manifest.entries),
        "manifest_hash": manifest.content_hash,
        "index_fingerprint": plan.index_fingerprint,
        "run_fingerprint": plan.run_fingerprint,
        "resolved_config": config.model_dump(mode="json"),
    }
    return {
        "report_path": report_path,
        "expected": expected,
        "manifest_hash": manifest.content_hash,
        "index_fingerprint": plan.index_fingerprint,
        "run_fingerprint": plan.run_fingerprint,
        "document_count": len(manifest.entries),
        "pdf_page_count": 1,
    }


def clean_child_environment(base: Mapping[str, str], workspace: Path) -> dict[str, str]:
    excluded_prefixes = (
        "RAG_WORKBENCH_", "E2E_", "PLAYWRIGHT_", "NPM_CONFIG_", "npm_config_",
        "VITE_", "NODE_",
    )
    excluded_names = {
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        "http_proxy", "https_proxy", "all_proxy", "no_proxy",
        "PYTHONPATH", "PYTHONHOME", "GITHUB_TOKEN", "GH_TOKEN",
        "NPM_TOKEN", "NODE_AUTH_TOKEN", "PYPI_TOKEN",
    }
    result = {
        key: value for key, value in base.items()
        if not key.startswith(excluded_prefixes)
        and key not in excluded_names
        and not any(marker in key.upper() for marker in ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL"))
    }
    result.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(ROOT),
        "PYTHONUNBUFFERED": "1",
        "HOME": str(workspace / "home"),
        "TMPDIR": str(workspace / "tmp"),
    })
    Path(result["HOME"]).mkdir(parents=True, exist_ok=True)
    Path(result["TMPDIR"]).mkdir(parents=True, exist_ok=True)
    return result


def service_environment(
    baseline: Mapping[str, str], role: str, token: str, paths: Mapping[str, Path],
    port: int, report_path: Path | None = None,
) -> dict[str, str]:
    env = dict(baseline)
    if role == "ordinary":
        env.update({
            "RAG_WORKBENCH_API_TOKEN": token,
            "RAG_WORKBENCH_DATA_DIR": str(paths["ordinary_source"]),
            "RAG_WORKBENCH_ARTIFACT_STORE_DIR": str(paths["ordinary_artifacts"]),
            "RAG_WORKBENCH_CATALOG_DB_PATH": str(paths["ordinary_state"] / "catalog.sqlite"),
            "RAG_WORKBENCH_JOBS_DB_PATH": str(paths["ordinary_state"] / "jobs.sqlite"),
            "RAG_WORKBENCH_INDEX_STORAGE_ROOT": str(paths["ordinary_index"]),
            "RAG_WORKBENCH_BUILD_REPORT_PATH": str(report_path),
            "RAG_WORKBENCH_API_HOST": "127.0.0.1",
            "RAG_WORKBENCH_API_PORT": str(port),
            "RAG_E2E_SERVICE_ROLE": "ordinary",
        })
    elif role == "bootstrap":
        env.update({
            "RAG_WORKBENCH_BOOTSTRAP_API_TOKEN": token,
            "RAG_WORKBENCH_BOOTSTRAP_PDF_SOURCE_DIR": str(paths["bootstrap_source"]),
            "RAG_WORKBENCH_BOOTSTRAP_INDEX_STORAGE_ROOT": str(paths["bootstrap_index"]),
            "RAG_WORKBENCH_BOOTSTRAP_STATE_DIR": str(paths["bootstrap_state"]),
            "RAG_WORKBENCH_BOOTSTRAP_API_HOST": "127.0.0.1",
            "RAG_WORKBENCH_BOOTSTRAP_API_PORT": str(port),
            "RAG_E2E_SERVICE_ROLE": "bootstrap",
        })
    else:
        raise E2EBlocked("service_role_invalid")
    return env


def http_health(port: int, timeout: float = 1.5) -> tuple[int, dict[str, Any]]:
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=timeout) as response:
            status, body = int(response.status), response.read(4097)
    except (OSError, URLError, TimeoutError) as exc:
        raise E2EBlocked("service_health_unavailable") from exc
    if len(body) > 4096:
        raise E2EBlocked("service_health_response_oversized")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise E2EBlocked("service_health_response_invalid") from exc
    if not isinstance(payload, dict):
        raise E2EBlocked("service_health_response_invalid")
    return status, payload


def spawn_owned(
    name: str, argv: Sequence[str], cwd: Path, env: Mapping[str, str],
    port: int | None = None, popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    proc_root: Path = Path("/proc"),
) -> ManagedProcess:
    if not argv or any(not isinstance(item, str) or not item for item in argv):
        raise E2EBlocked("process_arguments_invalid")
    try:
        process = popen_factory(
            list(argv), cwd=str(cwd), env=dict(env), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False, start_new_session=True,
        )
    except OSError as exc:
        raise E2EBlocked("process_start_failed") from exc
    identity = read_process_identity(process.pid, proc_root)
    if identity is None:
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
        raise E2EBlocked("process_identity_capture_failed")
    if identity.session_id != identity.pid:
        raise E2EBlocked("process_session_identity_invalid")
    return ManagedProcess(name, process, identity, port)


def wait_for_owned_health(
    managed: ManagedProcess, verifier: LinuxListenerVerifier,
    timeout_seconds: float = SERVICE_START_TIMEOUT_SECONDS,
) -> tuple[OwnedListener, dict[str, Any]]:
    if managed.port is None:
        raise E2EBlocked("service_port_missing")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if managed.popen.poll() is not None:
            raise E2EBlocked(f"{managed.name}_process_exited_before_ready")
        snapshot = verifier.snapshot(managed.port)
        if not snapshot.proc_listeners and not snapshot.psutil_listeners:
            time.sleep(0.1)
            continue
        listener = verifier.verify_owned_listener(snapshot, managed.identity)
        try:
            status, payload = http_health(managed.port)
        except E2EBlocked:
            time.sleep(0.1)
            continue
        if status != 200 or payload.get("status") != "ok":
            raise E2EBlocked(f"{managed.name}_health_invalid")
        after = verifier.snapshot(managed.port)
        stable = verifier.verify_owned_listener(after, managed.identity)
        if (stable.pid, stable.inode, stable.address, stable.port) != (listener.pid, listener.inode, listener.address, listener.port):
            raise E2EBlocked("listener_identity_changed_during_health")
        return stable, {"status": status, "body_status": "ok"}
    raise E2EBlocked(f"{managed.name}_readiness_timeout")


def write_evidence_bundle(
    destination: Path, payload: Mapping[str, Any], files: Mapping[str, bytes],
    secret_values: Sequence[str],
) -> str:
    destination = destination.resolve(strict=False)
    if destination.exists() or not destination.parent.is_dir():
        raise E2EBlocked("evidence_destination_not_fresh")
    if any(Path(name).name != name or name in {"", ".", ".."} for name in files):
        raise E2EBlocked("evidence_filename_invalid")
    summary = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
    materialized = {"runner-evidence.json": summary, **dict(files)}
    if sum(len(data) for data in materialized.values()) > MAX_EVIDENCE_BYTES:
        raise E2EBlocked("evidence_size_limit_exceeded")
    if any(contains_secret(data, secret_values) for data in materialized.values()):
        raise E2EBlocked("evidence_secret_detected")
    destination.mkdir(parents=True, exist_ok=False)
    manifest_rows = []
    for name, data in sorted(materialized.items()):
        with (destination / name).open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        manifest_rows.append({"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    manifest = json.dumps({"schema_version": 1, "files": manifest_rows}, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    with (destination / "evidence-manifest.json").open("xb") as stream:
        stream.write(manifest)
        stream.flush()
        os.fsync(stream.fileno())
    return hashlib.sha256(manifest).hexdigest()


def load_playwright_result(result_path: Path, observations_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
        observations = [json.loads(line) for line in observations_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError) as exc:
        raise E2EBlocked("playwright_evidence_invalid") from exc
    if not isinstance(result, dict) or not isinstance(observations, list):
        raise E2EBlocked("playwright_evidence_invalid")
    return result, observations


def assert_browser_result(result: Mapping[str, Any], observations: Sequence[Mapping[str, Any]]) -> None:
    if result.get("expected_tests") != 3 or result.get("completed_tests") != 3:
        raise E2EBlocked("playwright_scenario_count_invalid")
    tests = result.get("tests")
    if not isinstance(tests, list) or len(tests) != 3:
        raise E2EBlocked("playwright_scenario_count_invalid")
    if result.get("status") != "passed" or any(t.get("status") != "passed" or t.get("retry") != 0 for t in tests):
        raise E2EBlocked("playwright_scenario_failed")
    expected = {
        "bootstrap authentication and preview stay isolated and ephemeral",
        "build report inventory is read-only and empty",
        "ordinary workbench authentication and catalogs use only ordinary API token",
    }
    if {t.get("title") for t in tests} != expected:
        raise E2EBlocked("playwright_scenario_set_invalid")
    if len(observations) != 3:
        raise E2EBlocked("playwright_observation_count_invalid")
    for item in observations:
        if not isinstance(item, dict) or item.get("status") != "passed" or item.get("guard_violations") != []:
            raise E2EBlocked("browser_network_or_auth_guard_failed")


def classify_failure(stage: str, failure_code: str, *, cleanup_complete: bool) -> str:
    """Keep security/integrity failures distinct from browser behavior and setup failures."""
    if not cleanup_complete or stage == "post_browser_integrity":
        return "INTEGRITY_OR_CLEANUP_BLOCKED"
    if failure_code in {
        "browser_network_or_auth_guard_failed",
        "playwright_evidence_secret_detected",
        "evidence_secret_detected",
        "evidence_integrity_unverified",
        "synthetic_bootstrap_index_mutation_detected",
    } or failure_code.startswith((
        "listener_", "proc_tcp_", "psutil_", "proc_fd_", "proc_process_",
        "process_root_", "process_ancestry_", "process_session_",
        "process_identity_", "process_cleanup_", "process_tree_",
    )):
        return "INTEGRITY_OR_CLEANUP_BLOCKED"
    if failure_code == "playwright_scenario_failed":
        return "LINUX_BROWSER_BEHAVIOR_FAILED"
    return "LINUX_BROWSER_EXECUTION_BLOCKED"


def copy_sanitized_playwright_files(result_path: Path, observations_path: Path, tokens: Sequence[str]) -> dict[str, bytes]:
    try:
        result, observations = result_path.read_bytes(), observations_path.read_bytes()
    except OSError as exc:
        raise E2EBlocked("playwright_evidence_missing") from exc
    if contains_secret(result, tokens) or contains_secret(observations, tokens):
        raise E2EBlocked("playwright_evidence_secret_detected")
    if len(result) + len(observations) > MAX_EVIDENCE_BYTES:
        raise E2EBlocked("playwright_evidence_size_limit_exceeded")
    return {"playwright-results.json": result, "api-observations.jsonl": observations}


def assert_no_bootstrap_index_mutation(index_root: Path) -> None:
    if any(index_root.rglob("build-report.json")) or any(index_root.rglob("chroma.sqlite3")):
        raise E2EBlocked("synthetic_bootstrap_index_mutation_detected")
    if any(p.name in {".incomplete", ".writer-reservation.json"} for p in index_root.rglob("*")):
        raise E2EBlocked("synthetic_bootstrap_index_mutation_detected")


def start_vite(frontend: Path, port: int, env: Mapping[str, str], node: str) -> ManagedProcess:
    entry = frontend / "node_modules" / "vite" / "bin" / "vite.js"
    if not entry.is_file():
        raise E2EBlocked("vite_dependency_unavailable")
    return spawn_owned(
        "vite", [node, str(entry), "--host", "127.0.0.1", "--port", str(port), "--strictPort"],
        frontend, env, port,
    )


def start_playwright(frontend: Path, env: Mapping[str, str], node: str) -> ManagedProcess:
    cli = frontend / "node_modules" / "@playwright" / "test" / "cli.js"
    if not cli.is_file():
        raise E2EBlocked("playwright_dependency_unavailable")
    return spawn_owned(
        "playwright", [node, str(cli), "test", "--config", "playwright.config.ts"],
        frontend, env,
    )


def wait_playwright(managed: ManagedProcess) -> int:
    try:
        return int(managed.popen.wait(timeout=PLAYWRIGHT_TIMEOUT_SECONDS))
    except subprocess.TimeoutExpired:
        return 124



def signal_verified_identity(identity: ProcessIdentity, sig: int, proc_root: Path = Path("/proc")) -> bool:
    try:
        descriptor = os.pidfd_open(identity.pid, 0)
    except ProcessLookupError:
        return False
    except OSError as exc:
        raise E2EBlocked("process_cleanup_identity_unverified") from exc
    try:
        current = read_process_identity(identity.pid, proc_root)
        if current is None:
            return False
        if (current.pid, current.start_ticks, current.session_id) != (identity.pid, identity.start_ticks, identity.session_id):
            raise E2EBlocked("process_cleanup_identity_mismatch")
        try:
            signal.pidfd_send_signal(descriptor, sig)
        except ProcessLookupError:
            return False
        except OSError as exc:
            raise E2EBlocked("process_cleanup_signal_failed") from exc
        return True
    finally:
        os.close(descriptor)


def stop_owned_process_tree(managed: ManagedProcess, proc_root: Path = Path("/proc")) -> dict[str, Any]:
    processes = process_table(proc_root)
    owned = task_process_tree(managed.identity, processes, allow_root_missing=True)
    descendants = [item for item in owned if item.pid != managed.identity.pid]
    for identity in sorted(descendants, key=lambda item: (item.start_ticks, item.pid), reverse=True):
        signal_verified_identity(identity, signal.SIGTERM, proc_root)
    signal_verified_identity(managed.identity, signal.SIGTERM, proc_root)

    def live_owned() -> tuple[ProcessIdentity, ...]:
        current = process_table(proc_root)
        try:
            return tuple(
                item for item in task_process_tree(managed.identity, current, allow_root_missing=True)
                if item.state != "Z"
            )
        except E2EBlocked as exc:
            raise

    deadline = time.monotonic() + PROCESS_TERM_GRACE_SECONDS
    while time.monotonic() < deadline and live_owned():
        time.sleep(0.05)
    remaining = live_owned()
    for identity in remaining:
        signal_verified_identity(identity, signal.SIGKILL, proc_root)
    deadline = time.monotonic() + PROCESS_KILL_GRACE_SECONDS
    while time.monotonic() < deadline and live_owned():
        time.sleep(0.05)
    remaining = live_owned()
    try:
        managed.popen.wait(timeout=0.1)
    except subprocess.TimeoutExpired as exc:
        raise E2EBlocked("process_root_cleanup_unverified") from exc
    if remaining:
        raise E2EBlocked("process_tree_cleanup_unverified")
    final_table = process_table(proc_root)
    if any(item.session_id == managed.identity.pid and item.state != "Z" for item in final_table.values()):
        raise E2EBlocked("process_tree_cleanup_unverified")
    return {
        "root_exited": managed.popen.poll() is not None,
        "captured_descendant_count": len(descendants),
        "live_descendants_remaining": 0,
    }


def select_loopback_ports(count: int, binder: Callable[[], socket.socket] | None = None) -> tuple[int, ...]:
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 8:
        raise E2EBlocked("port_selection_count_invalid")
    socket_factory = binder or (lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM))
    ports: list[int] = []
    for _ in range(count):
        listener = socket_factory()
        try:
            listener.bind(("127.0.0.1", 0))
            port = int(listener.getsockname()[1])
        except OSError as exc:
            raise E2EBlocked("loopback_port_selection_failed") from exc
        finally:
            listener.close()
        validate_port(port)
        if port in ports:
            raise E2EBlocked("loopback_port_collision")
        ports.append(port)
    return tuple(ports)


def generate_test_tokens() -> tuple[str, str, str]:
    tokens = (secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(24))
    if len(set(tokens)) != 3:
        raise E2EBlocked("test_token_generation_collision")
    return tokens


def contains_secret(data: bytes | str, secret_values: Sequence[str]) -> bool:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return any(secret.encode("utf-8") in raw for secret in secret_values if secret)


def create_workspace_layout(workspace: Path) -> dict[str, Path]:
    base = workspace.resolve(strict=True)
    if not base.is_dir() or base.is_symlink():
        raise E2EBlocked("workspace_root_invalid")
    names = {
        "ordinary_root": "ordinary",
        "ordinary_source": "ordinary/pdf-source",
        "ordinary_index": "ordinary/index-storage",
        "ordinary_state": "ordinary/state",
        "ordinary_artifacts": "ordinary/artifacts",
        "bootstrap_root": "bootstrap",
        "bootstrap_source": "bootstrap/pdf-source",
        "bootstrap_index": "bootstrap/index-storage",
        "bootstrap_state": "bootstrap/state",
        "playwright_results": "playwright-results",
        "logs": "logs",
        "home": "home",
        "tmp": "tmp",
    }
    paths = {key: base / relative for key, relative in names.items()}
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or path.resolve(strict=True) != path:
            raise E2EBlocked("workspace_path_redirected")
        try:
            path.resolve().relative_to(base)
        except ValueError as exc:
            raise E2EBlocked("workspace_path_escape") from exc
    roots = (
        paths["ordinary_source"], paths["ordinary_index"], paths["ordinary_state"], paths["ordinary_artifacts"],
        paths["bootstrap_source"], paths["bootstrap_index"], paths["bootstrap_state"],
        paths["playwright_results"], paths["logs"], paths["home"], paths["tmp"],
    )
    for index, left in enumerate(roots):
        for right in roots[index + 1 :]:
            if left == right or left in right.parents or right in left.parents:
                raise E2EBlocked("workspace_roots_overlap")
    return paths


if __name__ == "__main__":
    raise SystemExit(main())
