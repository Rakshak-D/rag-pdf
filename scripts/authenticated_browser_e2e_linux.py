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
import stat
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
MAX_STARTUP_STATUS_BYTES = 64 * 1024
MAX_STARTUP_STATUS_RECORDS = 32
MAX_STARTUP_STATUS_RECORD_BYTES = 4096
SYNTHETIC_BROWSER_BUILD_ID = "8d3f6c1a2b4e59708c1d2e3f4a5b6c7d"
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
STARTUP_STAGES = (
    "guard_initialization",
    "guard_dependency_import",
    "guard_installation",
    "runtime_module_import",
    "runtime_entrypoint",
    "server_startup",
    "health_accepted",
)
STARTUP_EVENTS = {"started", "completed", "failed", "forbidden_operation", "health_accepted"}
STARTUP_STATUS_KEYS = {
    "schema_version", "service_role", "stage", "event", "exception_class",
    "missing_module", "application_error_code", "forbidden_operation", "exit_code",
    "signal_number", "reached_http_readiness",
}
_SAFE_MODULE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){0,15}\Z")
_SAFE_CODE = re.compile(r"[a-z][a-z0-9_]{1,79}\Z")
_SAFE_EXCEPTION_CLASS = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,79}\Z")


class E2EBlocked(RuntimeError):
    """A bounded failure carrying a safe, stable code only."""

    def __init__(self, code: str, *, details: Mapping[str, Any] | None = None):
        self.code = code if re.fullmatch(r"[a-z][a-z0-9_]{1,79}", code or "") else "linux_e2e_blocked"
        self.details = dict(details) if details is not None else None
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
    startup_status_path: Path | None = None


def safe_exception_metadata(exc: BaseException) -> dict[str, str | None]:
    """Return only bounded identifiers; never retain exception messages or paths."""
    chain: list[BaseException] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    root = chain[-1] if chain else exc
    raw_class = type(root).__name__
    exception_class = raw_class if _SAFE_EXCEPTION_CLASS.fullmatch(raw_class) else "Exception"
    missing_module: str | None = None
    application_error_code: str | None = None
    for item in chain:
        if isinstance(item, (ModuleNotFoundError, ImportError)):
            candidate = getattr(item, "name", None)
            if isinstance(candidate, str) and _SAFE_MODULE_NAME.fullmatch(candidate):
                missing_module = candidate
        for attribute in ("error_code", "code"):
            candidate = getattr(item, attribute, None)
            if isinstance(candidate, str) and _SAFE_CODE.fullmatch(candidate):
                application_error_code = candidate
    return {
        "exception_class": exception_class,
        "missing_module": missing_module,
        "application_error_code": application_error_code,
    }


def _startup_record(
    service_role: str,
    stage: str,
    event: str,
    *,
    exception_class: str | None = None,
    missing_module: str | None = None,
    application_error_code: str | None = None,
    forbidden_operation: bool = False,
    exit_code: int | None = None,
    signal_number: int | None = None,
    reached_http_readiness: bool = False,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "service_role": service_role,
        "stage": stage,
        "event": event,
        "exception_class": exception_class,
        "missing_module": missing_module,
        "application_error_code": application_error_code,
        "forbidden_operation": forbidden_operation,
        "exit_code": exit_code,
        "signal_number": signal_number,
        "reached_http_readiness": reached_http_readiness,
    }


def validate_startup_records(records: Sequence[object], service_role: str) -> list[dict[str, Any]]:
    if service_role not in {"ordinary", "bootstrap"} or not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise E2EBlocked("startup_status_invalid")
    if not records or len(records) > MAX_STARTUP_STATUS_RECORDS:
        raise E2EBlocked("startup_status_record_count_invalid")
    validated: list[dict[str, Any]] = []
    stage_events: dict[str, set[str]] = {}
    started: set[str] = set()
    terminal: set[str] = set()
    completed: set[str] = set()
    total = 0
    for raw in records:
        if not isinstance(raw, dict) or set(raw) != STARTUP_STATUS_KEYS:
            raise E2EBlocked("startup_status_schema_invalid")
        record = dict(raw)
        try:
            encoded = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise E2EBlocked("startup_status_schema_invalid") from exc
        total += len(encoded) + 1
        if len(encoded) + 1 > MAX_STARTUP_STATUS_RECORD_BYTES or total > MAX_STARTUP_STATUS_BYTES:
            raise E2EBlocked("startup_status_size_limit_exceeded")
        stage, event = record["stage"], record["event"]
        if (
            isinstance(record["schema_version"], bool) or record["schema_version"] != 1
            or record["service_role"] != service_role
            or not isinstance(stage, str) or stage not in STARTUP_STAGES
            or not isinstance(event, str) or event not in STARTUP_EVENTS
        ):
            raise E2EBlocked("startup_status_identity_invalid")
        if any(not isinstance(record[key], bool) for key in ("forbidden_operation", "reached_http_readiness")):
            raise E2EBlocked("startup_status_schema_invalid")
        for key, pattern in (("exception_class", _SAFE_EXCEPTION_CLASS), ("missing_module", _SAFE_MODULE_NAME), ("application_error_code", _SAFE_CODE)):
            value = record[key]
            if value is not None and (not isinstance(value, str) or not pattern.fullmatch(value)):
                raise E2EBlocked("startup_status_identifier_invalid")
        for key in ("exit_code", "signal_number"):
            value = record[key]
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise E2EBlocked("startup_status_schema_invalid")
        if event in {"started", "completed", "health_accepted"} and any(
            record[key] is not None
            for key in ("exception_class", "missing_module", "application_error_code", "signal_number")
        ):
            raise E2EBlocked("startup_status_schema_invalid")
        if event == "completed" and record["exit_code"] is not None and not (
            stage == "server_startup" and record["exit_code"] == 0
        ):
            raise E2EBlocked("startup_status_schema_invalid")
        if event in {"started", "health_accepted"} and record["exit_code"] is not None:
            raise E2EBlocked("startup_status_schema_invalid")
        if event == "forbidden_operation" and any(
            record[key] is not None
            for key in ("exception_class", "missing_module", "application_error_code", "signal_number")
        ):
            raise E2EBlocked("startup_status_schema_invalid")
        events = stage_events.setdefault(stage, set())
        if event in events:
            raise E2EBlocked("startup_status_duplicate_or_conflicting_record")
        events.add(event)
        if event == "started":
            if stage == "health_accepted" or stage in started or stage in terminal:
                raise E2EBlocked("startup_status_transition_invalid")
            prerequisites_by_stage = {
                "guard_dependency_import": ("guard_initialization",),
                "guard_installation": ("guard_dependency_import",),
                "runtime_module_import": ("guard_installation",),
                "runtime_entrypoint": ("runtime_module_import",),
                "server_startup": ("runtime_entrypoint",),
            }
            if any(item not in completed for item in prerequisites_by_stage.get(stage, ())):
                raise E2EBlocked("startup_status_transition_invalid")
            if any(row["event"] == "failed" for row in validated):
                raise E2EBlocked("startup_status_transition_invalid")
            started.add(stage)
        elif event in {"completed", "failed"}:
            if stage not in started or stage in terminal:
                raise E2EBlocked("startup_status_transition_invalid")
            terminal.add(stage)
            if event == "completed":
                completed.add(stage)
            if event == "failed" and record["reached_http_readiness"]:
                raise E2EBlocked("startup_status_transition_invalid")
        elif event == "forbidden_operation":
            if stage not in started or stage in terminal or not record["forbidden_operation"] or record["exit_code"] != 91:
                raise E2EBlocked("startup_status_forbidden_marker_invalid")
        elif event == "health_accepted":
            prerequisites = {
                "guard_initialization", "guard_dependency_import", "guard_installation",
                "runtime_module_import", "runtime_entrypoint", "server_startup",
            }
            if (
                stage != "health_accepted" or not record["reached_http_readiness"]
                or not prerequisites <= started
                or not prerequisites - {"server_startup"} <= completed
                or "server_startup" in terminal
            ):
                raise E2EBlocked("startup_status_health_marker_invalid")
        if event != "health_accepted" and record["reached_http_readiness"]:
            raise E2EBlocked("startup_status_health_marker_invalid")
        if event != "forbidden_operation" and record["forbidden_operation"]:
            raise E2EBlocked("startup_status_forbidden_marker_invalid")
        validated.append(record)
    if validated[0]["stage"] != "guard_initialization" or validated[0]["event"] != "started":
        raise E2EBlocked("startup_status_initial_stage_missing")
    return validated


def load_startup_status(path: Path, service_role: str) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise E2EBlocked("startup_status_missing")
    try:
        if path.stat().st_size > MAX_STARTUP_STATUS_BYTES:
            raise E2EBlocked("startup_status_size_limit_exceeded")
        raw = path.read_bytes()
    except OSError as exc:
        raise E2EBlocked("startup_status_unreadable") from exc
    if not raw or not raw.endswith(b"\n"):
        raise E2EBlocked("startup_status_malformed")
    lines = raw.splitlines()
    if len(lines) > MAX_STARTUP_STATUS_RECORDS:
        raise E2EBlocked("startup_status_record_count_invalid")
    records: list[object] = []
    def strict_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    try:
        for line in lines:
            if not line or len(line) + 1 > MAX_STARTUP_STATUS_RECORD_BYTES:
                raise E2EBlocked("startup_status_malformed")
            records.append(json.loads(line.decode("utf-8"), object_pairs_hook=strict_object))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise E2EBlocked("startup_status_malformed") from exc
    return validate_startup_records(records, service_role)


class StartupStatusRecorder:
    """Small append-only, fsynced status writer for one Linux child service."""

    def __init__(self, path: Path, service_role: str, *, require_fresh: bool = False):
        if service_role not in {"ordinary", "bootstrap"} or not path.is_absolute() or path.name != f"{service_role}.jsonl":
            raise E2EBlocked("startup_status_path_invalid")
        if path.is_symlink() or not path.parent.is_dir() or path.parent.is_symlink():
            raise E2EBlocked("startup_status_path_invalid")
        if require_fresh and path.exists():
            raise E2EBlocked("startup_status_path_not_fresh")
        self.path = path
        self.service_role = service_role

    def append(self, stage: str, event: str, **fields: Any) -> None:
        if stage not in STARTUP_STAGES or event not in STARTUP_EVENTS:
            raise E2EBlocked("startup_status_identity_invalid")
        record = _startup_record(self.service_role, stage, event, **fields)
        payload = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
        flags = os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = -1
        try:
            descriptor = os.open(self.path, flags, 0o600)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise E2EBlocked("startup_status_path_invalid")
            try:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            except ImportError:
                pass
            if os.fstat(descriptor).st_size:
                prior = load_startup_status(self.path, self.service_role)
            else:
                prior = []
            validate_startup_records([*prior, record], self.service_role)
            offset = 0
            while offset < len(payload):
                offset += os.write(descriptor, payload[offset:])
            os.fsync(descriptor)
            directory_flag = getattr(os, "O_DIRECTORY", 0)
            if directory_flag:
                directory_descriptor = os.open(self.path.parent, os.O_RDONLY | directory_flag)
                try:
                    os.fsync(directory_descriptor)
                finally:
                    os.close(directory_descriptor)
        except OSError as exc:
            raise E2EBlocked("startup_status_write_failed") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def has_terminal(self, stage: str) -> bool:
        if not self.path.exists():
            return False
        return any(
            row["stage"] == stage and row["event"] in {"completed", "failed"}
            for row in load_startup_status(self.path, self.service_role)
        )


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
        "startup_diagnostic": None,
        "playwright_failure": None,
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
            startup_status_dir = workspace / "startup-status"
            startup_status_dir.mkdir(mode=0o700)
            ordinary_status_path = startup_status_dir / "ordinary.jsonl"
            bootstrap_status_path = startup_status_dir / "bootstrap.jsonl"
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
                baseline, "ordinary", ordinary_token, paths, ordinary_port, fixture["report_path"],
                startup_status_path=ordinary_status_path,
            )
            bootstrap_env = service_environment(
                baseline, "bootstrap", bootstrap_token, paths, bootstrap_port,
                startup_status_path=bootstrap_status_path,
            )

            report["stage"] = "ordinary_service_start"
            ordinary = spawn_owned(
                "ordinary", [sys.executable, "-u", str(guard_script)], ROOT, ordinary_env, ordinary_port,
                startup_status_path=ordinary_status_path,
            )
            managed.append(ordinary)
            report["processes"] = [_process_record(item) for item in managed]

            report["stage"] = "bootstrap_service_start"
            bootstrap = spawn_owned(
                "bootstrap", [sys.executable, "-u", str(guard_script)], ROOT, bootstrap_env, bootstrap_port,
                startup_status_path=bootstrap_status_path,
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
                    "startup": load_startup_status(process.startup_status_path, process.name)
                    if process.startup_status_path is not None else None,
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
            if has_browser_guard_violation(observations):
                raise E2EBlocked("browser_network_or_auth_guard_failed")
            safe_tests, _safe_observations = _validate_playwright_evidence(result, observations)
            report["scenario_count"] = len(safe_tests)
            report["scenarios"] = [
                {key: item.get(key) for key in ("title", "status", "retry")}
                for item in safe_tests
            ]
            try:
                assert_browser_result(result, observations)
            except E2EBlocked as exc:
                if exc.details is not None:
                    report["playwright_failure"] = exc.details
                raise
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
            if exc.details is not None:
                if report["stage"] == "playwright_result_validation":
                    report["playwright_failure"] = exc.details
                else:
                    report["startup_diagnostic"] = exc.details
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
                    cleanup = stop_owned_process_tree(process)
                    cleanup["process_exit"] = managed_process_exit(process)
                    report["cleanup"]["processes"][process.name] = cleanup
                except E2EBlocked as exc:
                    cleanup_failures.append(exc.code)
                    report["cleanup"]["processes"][process.name] = {
                        "verified": False, "failure_code": exc.code,
                        "process_exit": managed_process_exit(process),
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
                report["classification"] = (
                    "LINUX_STARTUP_CAUSE_IDENTIFIED"
                    if startup_cause_identified(report.get("startup_diagnostic"))
                    else classify_failure(report["stage"], first_failure.code, cleanup_complete=True)
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
    source = '''from __future__ import annotations

import contextlib
import importlib
import os
from pathlib import Path
import re
import sys

from scripts.authenticated_browser_e2e_linux import StartupStatusRecorder, safe_exception_metadata

role = os.environ.get("RAG_E2E_SERVICE_ROLE")
status_value = os.environ.get("RAG_E2E_STARTUP_STATUS_PATH")
if role not in {"ordinary", "bootstrap"} or not status_value:
    raise SystemExit(92)
status_path = Path(status_value)
if not status_path.is_absolute() or status_path.name != f"{role}.jsonl":
    raise SystemExit(92)
recorder = StartupStatusRecorder(status_path, role, require_fresh=True)
current_stage = "guard_initialization"
recorder.append(current_stage, "started")
recorder.append(current_stage, "completed")

class SafeDiagnosticStderr:
    """Consumes child logs without retaining messages, paths, or credentials."""
    def __init__(self):
        self.pending = ""
        self.exception_class = None
        self.missing_module = None
        self.application_error_code = None

    def write(self, value):
        if not isinstance(value, str):
            return 0
        for character in value:
            if character == "\\n":
                self._line(self.pending)
                self.pending = ""
            elif len(self.pending) < 2048:
                self.pending += character
            else:
                self.pending = ""
        return len(value)

    def _line(self, line):
        code = re.search(r"(?:Local runtime configuration error|Bootstrap configuration error): ([a-z][a-z0-9_]{1,79})\\Z", line)
        if code:
            self.application_error_code = code.group(1)
        module = re.search(r"No module named ['\\\"]([A-Za-z_][A-Za-z0-9_]*(?:\\.[A-Za-z_][A-Za-z0-9_]*){0,15})['\\\"]", line)
        if module:
            self.missing_module = module.group(1)
            self.exception_class = "ModuleNotFoundError"
        kind = re.match(r"([A-Za-z][A-Za-z0-9_]{0,79}(?:Error|Exception))(?::|\\Z)", line)
        if kind:
            self.exception_class = kind.group(1)

    def flush(self):
        if self.pending:
            self._line(self.pending)
            self.pending = ""

    def isatty(self):
        return False


diagnostics = SafeDiagnosticStderr()

def record_failure(stage, exc=None, exit_code=None):
    fields = safe_exception_metadata(exc) if exc is not None else {
        "exception_class": diagnostics.exception_class,
        "missing_module": diagnostics.missing_module,
        "application_error_code": diagnostics.application_error_code,
    }
    if diagnostics.missing_module and not fields.get("missing_module"):
        fields["missing_module"] = diagnostics.missing_module
    if diagnostics.exception_class and not fields.get("exception_class"):
        fields["exception_class"] = diagnostics.exception_class
    if diagnostics.application_error_code and not fields.get("application_error_code"):
        fields["application_error_code"] = diagnostics.application_error_code
    recorder.append(stage, "failed", **fields, exit_code=exit_code)

try:
    current_stage = "guard_dependency_import"
    recorder.append(current_stage, "started")
    import chromadb
    from backend.embeddings.text import TextEmbeddingAdapter
    from backend.embeddings.image import ImageEmbeddingAdapter
    from backend.engine.reranking import CrossEncoderReranker
    from backend.engine.generation import LocalGenerationAdapter
    from backend.vectorstore.chroma_store import ChromaVectorStore
    recorder.append(current_stage, "completed")

    current_stage = "guard_installation"
    recorder.append(current_stage, "started")
    def forbidden(*_args, **_kwargs):
        recorder.append(current_stage, "forbidden_operation", forbidden_operation=True, exit_code=91)
        os._exit(91)
    chromadb.PersistentClient = forbidden
    TextEmbeddingAdapter.load = forbidden
    ImageEmbeddingAdapter.load = forbidden
    CrossEncoderReranker.load = forbidden
    LocalGenerationAdapter.generate = forbidden
    ChromaVectorStore.open_existing = forbidden
    ChromaVectorStore.create_new = forbidden
    recorder.append(current_stage, "completed")

    current_stage = "runtime_module_import"
    recorder.append(current_stage, "started")
    module_name = "backend.app.runtime" if role == "ordinary" else "backend.bootstrap_app.runtime"
    runtime_module = importlib.import_module(module_name)
    recorder.append(current_stage, "completed")

    current_stage = "runtime_entrypoint"
    recorder.append(current_stage, "started")
    main = getattr(runtime_module, "main", None)
    if not callable(main):
        raise RuntimeError("entrypoint unavailable")
    recorder.append(current_stage, "completed")

    current_stage = "server_startup"
    recorder.append(current_stage, "started")
    build_name = "build_runtime" if role == "ordinary" else "build_bootstrap_runtime"
    original_build = getattr(runtime_module, build_name)
    def diagnosed_build(*args, **kwargs):
        try:
            return original_build(*args, **kwargs)
        except BaseException as exc:
            if not recorder.has_terminal("server_startup"):
                record_failure("server_startup", exc)
            raise
    setattr(runtime_module, build_name, diagnosed_build)
    original_run_server = runtime_module.run_server
    def diagnosed_run_server(*args, **kwargs):
        try:
            return original_run_server(*args, **kwargs)
        except BaseException as exc:
            if not recorder.has_terminal("server_startup"):
                record_failure("server_startup", exc)
            raise
    runtime_module.run_server = diagnosed_run_server

    with contextlib.redirect_stderr(diagnostics):
        result = main()
        diagnostics.flush()
    exit_code = result if isinstance(result, int) and not isinstance(result, bool) else 0
    if not recorder.has_terminal("server_startup"):
        if exit_code != 0 or diagnostics.exception_class or diagnostics.application_error_code:
            record_failure("server_startup", exit_code=exit_code)
        else:
            recorder.append("server_startup", "completed", exit_code=exit_code)
    raise SystemExit(exit_code)
except SystemExit as exc:
    code = exc.code if isinstance(exc.code, int) and not isinstance(exc.code, bool) else 1
    if code != 0 and not recorder.has_terminal(current_stage):
        record_failure(current_stage, exc, exit_code=code)
    raise
except BaseException as exc:
    if not recorder.has_terminal(current_stage):
        record_failure(current_stage, exc, exit_code=1)
    raise SystemExit(1)
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
    report_root = paths["ordinary_index"] / "build-artifacts" / SYNTHETIC_BROWSER_BUILD_ID
    report_root.mkdir(parents=True, exist_ok=True)
    report_path = report_root / "build-report.json"
    write_json_fsync(
        report_path,
        {
            "report_schema_version": "1",
            "build_id": SYNTHETIC_BROWSER_BUILD_ID,
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
    port: int, report_path: Path | None = None, *, startup_status_path: Path | None = None,
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
            "RAG_E2E_STARTUP_STATUS_PATH": str(startup_status_path) if startup_status_path else "",
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
            "RAG_E2E_STARTUP_STATUS_PATH": str(startup_status_path) if startup_status_path else "",
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
    proc_root: Path = Path("/proc"), startup_status_path: Path | None = None,
) -> ManagedProcess:
    if not argv or any(not isinstance(item, str) or not item for item in argv):
        raise E2EBlocked("process_arguments_invalid")
    if startup_status_path is not None:
        expected_role = name if name in {"ordinary", "bootstrap"} else None
        if (
            expected_role is None or not startup_status_path.is_absolute()
            or startup_status_path.name != f"{expected_role}.jsonl"
            or env.get("RAG_E2E_STARTUP_STATUS_PATH") != str(startup_status_path)
        ):
            raise E2EBlocked("startup_status_path_invalid")
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
    return ManagedProcess(name, process, identity, port, startup_status_path)


def startup_exit_details(managed: ManagedProcess) -> dict[str, Any]:
    returncode = managed.popen.poll()
    process_exit: dict[str, int | None] = {"exit_code": returncode, "signal_number": -returncode if isinstance(returncode, int) and returncode < 0 else None}
    if managed.startup_status_path is None:
        return {"process_exit": process_exit, "status_available": False, "status_error_code": "startup_status_unconfigured"}
    try:
        records = load_startup_status(managed.startup_status_path, managed.name)
    except E2EBlocked as exc:
        return {
            "process_exit": process_exit,
            "status_available": False,
            "status_error_code": exc.code,
            "reached_http_readiness": False,
        }
    failure_record = next(
        (row for row in reversed(records) if row["event"] in {"failed", "forbidden_operation"}),
        None,
    )
    return {
        "process_exit": process_exit,
        "status_available": True,
        "status_records": records,
        "failure_stage": failure_record["stage"] if failure_record is not None else None,
        "failure_record": failure_record,
        "forbidden_operation_fired": any(item["event"] == "forbidden_operation" for item in records),
        "reached_http_readiness": any(item["event"] == "health_accepted" for item in records),
    }


def managed_process_exit(managed: ManagedProcess) -> dict[str, int | None]:
    returncode = managed.popen.poll()
    return {
        "exit_code": returncode,
        "signal_number": -returncode if isinstance(returncode, int) and returncode < 0 else None,
    }


def assert_startup_status_running(managed: ManagedProcess) -> list[dict[str, Any]]:
    if managed.startup_status_path is None:
        return []
    records = load_startup_status(managed.startup_status_path, managed.name)
    by_stage_event = {(row["stage"], row["event"]) for row in records}
    completed_stages = {
        "guard_initialization", "guard_dependency_import", "guard_installation",
        "runtime_module_import", "runtime_entrypoint",
    }
    if any((stage, "completed") not in by_stage_event for stage in completed_stages):
        raise E2EBlocked(
            "startup_stage_incomplete",
            details={"process_exit": managed_process_exit(managed), "status_records": records},
        )
    if ("server_startup", "started") not in by_stage_event:
        raise E2EBlocked(
            "startup_stage_incomplete",
            details={"process_exit": managed_process_exit(managed), "status_records": records},
        )
    if any(row["event"] in {"failed", "forbidden_operation"} for row in records):
        failure_record = next(row for row in reversed(records) if row["event"] in {"failed", "forbidden_operation"})
        raise E2EBlocked(
            "startup_stage_failed",
            details={
                "process_exit": managed_process_exit(managed),
                "failure_stage": failure_record["stage"],
                "failure_record": failure_record,
                "status_records": records,
            },
        )
    return records


def wait_for_owned_health(
    managed: ManagedProcess, verifier: LinuxListenerVerifier,
    timeout_seconds: float = SERVICE_START_TIMEOUT_SECONDS,
) -> tuple[OwnedListener, dict[str, Any]]:
    if managed.port is None:
        raise E2EBlocked("service_port_missing")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if managed.popen.poll() is not None:
            raise E2EBlocked(
                f"{managed.name}_process_exited_before_ready",
                details=startup_exit_details(managed),
            )
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
        if managed.startup_status_path is not None:
            assert_startup_status_running(managed)
            status_writer = StartupStatusRecorder(managed.startup_status_path, managed.name)
            status_writer.append(
                "health_accepted", "health_accepted", reached_http_readiness=True
            )
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


PLAYWRIGHT_SCENARIO_TITLES = (
    "bootstrap authentication and preview stay isolated and ephemeral",
    "build report inventory is read-only and empty",
    "ordinary workbench authentication and catalogs use only ordinary API token",
)
PLAYWRIGHT_STATUSES = {"passed", "failed", "timedOut", "skipped", "interrupted"}
PLAYWRIGHT_FAILURE_CATEGORIES = {
    "locator_ambiguous", "expectation_timeout", "navigation_failed",
    "browser_or_context_failed", "test_setup_failed", "assertion_failed",
    "unknown_test_failure",
}
PLAYWRIGHT_PHASES = {
    "browser_page_navigation", "initial_disconnected_state",
    "invalid_bootstrap_authentication", "valid_bootstrap_authentication",
    "bootstrap_preview", "bootstrap_inventory", "bootstrap_disconnect", "bootstrap_reload", "final_integrity",
    "invalid_ordinary_authentication", "valid_ordinary_authentication",
    "ordinary_catalog_navigation", "ordinary_disconnect", "ordinary_reload",
}
PLAYWRIGHT_CHECKS = {
    "disconnected_preview_blocked", "invalid_bootstrap_token_rejected_without_secret_echo",
    "valid_bootstrap_token_authenticated_by_status", "server_preview_identity_matches_synthetic_fixture",
    "disconnect_cleared_bootstrap_session_and_config", "reload_required_bootstrap_authentication",
    "empty_inventory_store_not_checked_no_mutation", "inventory_cleared_on_disconnect_and_reload",
    "unauthenticated_liveness_separate_from_readiness", "invalid_ordinary_token_rejected_without_secret_echo",
    "ordinary_models_and_empty_catalogs_from_server", "jobs_and_artifacts_navigation_read_only_and_empty",
    "disconnect_and_reload_cleared_ordinary_authentication",
}
PLAYWRIGHT_API_PATHS = {
    "/health", "/api/v1/runtime/models", "/api/v1/jobs", "/api/v1/artifacts",
    "/bootstrap-api/v1/bootstrap/status", "/bootstrap-api/v1/bootstrap/preview",
    "/bootstrap-api/v1/bootstrap/index-inventory", "/bootstrap-api/v1/bootstrap/jobs",
}
PLAYWRIGHT_SUMMARY_KEYS = {
    "component_count", "item_count", "mode", "accepting_jobs", "document_count",
    "manifest_hash", "index_fingerprint", "run_fingerprint", "fresh_build_eligible",
    "candidate_count", "total_candidates", "complete", "store_checked", "check_level",
}
PLAYWRIGHT_GUARD_LABELS = {
    "non_loopback_request", "request_outside_test_origin", "token_in_url",
    "unparseable_request_url", "unparseable_route_url", "bootstrap_auth_scope_violation",
    "ordinary_auth_scope_violation",
}
BOOTSTRAP_STATUS_EVENTS = {
    "input_filled", "submit_attempted", "submit_click_completed", "submit_click_failed",
    "browser_request_observed", "browser_request_non_get", "route_handler_entered",
    "route_role_invalid", "route_role_bootstrap", "route_role_ordinary", "route_role_none", "route_role_other",
    "route_allowed", "route_rejected", "request_failed", "http_response_401",
    "http_response_success", "http_response_other", "response_wait_registered",
    "response_wait_matched", "response_wait_timed_out", "response_wait_failed",
    "ui_authentication_error_observed", "browser_page_error_observed",
}


def _safe_report_int(value: object, *, minimum: int, maximum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum


def has_browser_guard_violation(observations: object) -> bool:
    if not isinstance(observations, list):
        return False
    return any(
        isinstance(observation, dict)
        and isinstance(observation.get("guard_violations"), list)
        and any(isinstance(violation, str) and bool(violation) for violation in observation["guard_violations"])
        for observation in observations
    )


def _valid_failure_summary(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    required = {"category", "phase", "failure_phase"}
    allowed = required | {"source_file", "line", "column"}
    if not required.issubset(value) or not set(value).issubset(allowed):
        return False
    category = value.get("category")
    if not isinstance(category, str) or category not in PLAYWRIGHT_FAILURE_CATEGORIES:
        return False
    phase = value.get("phase")
    if phase is not None and (not isinstance(phase, str) or phase not in PLAYWRIGHT_PHASES):
        return False
    failure_phase = value.get("failure_phase")
    expected_failure_phase = "setup" if phase is None else "teardown" if phase == "final_integrity" else "execution"
    if failure_phase != expected_failure_phase:
        return False
    if "source_file" in value:
        if value.get("source_file") != "frontend/e2e/authenticated-workflows.spec.ts":
            return False
        if not _safe_report_int(value.get("line"), minimum=1, maximum=10000):
            return False
        if "column" in value and not _safe_report_int(value.get("column"), minimum=1, maximum=10000):
            return False
    elif "line" in value or "column" in value:
        return False
    return True


def _validate_observation_record(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    required = {
        "test", "status", "duration_ms", "browser_version", "requests", "guard_violations", "checks",
        "bootstrap_status_events",
    }
    if set(value) != required:
        return False
    test_title = value.get("test")
    status = value.get("status")
    if (
        not isinstance(test_title, str) or test_title not in PLAYWRIGHT_SCENARIO_TITLES
        or not isinstance(status, str) or status not in PLAYWRIGHT_STATUSES
    ):
        return False
    if not _safe_report_int(value.get("duration_ms"), minimum=0, maximum=1800000):
        return False
    browser_version = value.get("browser_version")
    if not isinstance(browser_version, str) or not re.fullmatch(r"[A-Za-z0-9 ._-]{1,80}", browser_version):
        return False
    requests = value.get("requests")
    if not isinstance(requests, list) or len(requests) > 200:
        return False
    for request in requests:
        if not isinstance(request, dict):
            return False
        allowed = {"method", "path", "auth_role", "status", "failed", "response_summary"}
        required_request = {"method", "path", "auth_role", "status"}
        if not required_request.issubset(request) or not set(request).issubset(allowed):
            return False
        method = request.get("method")
        request_path = request.get("path")
        auth_role = request.get("auth_role")
        if not isinstance(method, str) or method not in {"GET", "HEAD", "OPTIONS", "POST"}:
            return False
        if not isinstance(request_path, str) or request_path not in PLAYWRIGHT_API_PATHS:
            return False
        if not isinstance(auth_role, str) or auth_role not in {"none", "ordinary", "bootstrap", "invalid", "other"}:
            return False
        status = request.get("status")
        if status is not None and not _safe_report_int(status, minimum=100, maximum=599):
            return False
        if "failed" in request and not isinstance(request.get("failed"), bool):
            return False
        summary = request.get("response_summary")
        if summary is not None:
            if not isinstance(summary, dict) or not set(summary).issubset(PLAYWRIGHT_SUMMARY_KEYS):
                return False
            for key, item in summary.items():
                if item is None:
                    continue
                if key in {"mode", "check_level"}:
                    if not isinstance(item, str) or item not in (
                        {"bootstrap", "ordinary"} if key == "mode" else {"report", "store"}
                    ):
                        return False
                elif key in {"manifest_hash", "index_fingerprint", "run_fingerprint"}:
                    if not isinstance(item, str) or not re.fullmatch(r"[a-f0-9]{64}", item):
                        return False
                elif key in {"accepting_jobs", "fresh_build_eligible", "complete", "store_checked"}:
                    if not isinstance(item, bool):
                        return False
                elif not _safe_report_int(item, minimum=0, maximum=100000):
                    return False
    violations = value.get("guard_violations")
    if not isinstance(violations, list) or len(violations) > 100:
        return False
    if any(
        not isinstance(item, str) or not item or len(item) > 512
        or any(ord(char) < 32 or ord(char) > 126 for char in item)
        for item in violations
    ):
        return False
    checks = value.get("checks")
    if not isinstance(checks, list) or len(checks) > 20 or any(
        not isinstance(item, str) or item not in PLAYWRIGHT_CHECKS for item in checks
    ):
        return False
    status_events = value.get("bootstrap_status_events")
    if not isinstance(status_events, list) or len(status_events) > 32 or any(
        not isinstance(item, str) or item not in BOOTSTRAP_STATUS_EVENTS for item in status_events
    ):
        return False
    return True


def _validate_playwright_evidence(result: object, observations: object) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(result, dict) or set(result) != {
        "schema_version", "status", "expected_tests", "completed_tests", "tests"
    }:
        raise E2EBlocked("playwright_evidence_invalid")
    if (
        not _safe_report_int(result.get("schema_version"), minimum=1, maximum=1)
        or not _safe_report_int(result.get("expected_tests"), minimum=3, maximum=3)
    ):
        raise E2EBlocked("playwright_evidence_invalid")
    result_status = result.get("status")
    if not isinstance(result_status, str) or result_status not in {"passed", "failed", "timedout", "interrupted"}:
        raise E2EBlocked("playwright_evidence_invalid")
    tests = result.get("tests")
    completed = result.get("completed_tests")
    if (
        not isinstance(tests, list) or len(tests) > 3
        or not _safe_report_int(completed, minimum=0, maximum=3)
        or completed != len(tests)
    ):
        raise E2EBlocked("playwright_evidence_invalid")

    safe_tests: list[dict[str, Any]] = []
    for index, item in enumerate(tests):
        if not isinstance(item, dict):
            raise E2EBlocked("playwright_evidence_invalid")
        allowed = {"title", "status", "duration_ms", "retry", "failure"}
        required = {"title", "status", "duration_ms", "retry"}
        if not required.issubset(item) or not set(item).issubset(allowed):
            raise E2EBlocked("playwright_evidence_invalid")
        if item.get("title") != PLAYWRIGHT_SCENARIO_TITLES[index]:
            raise E2EBlocked("playwright_evidence_invalid")
        item_status = item.get("status")
        if not isinstance(item_status, str) or item_status not in PLAYWRIGHT_STATUSES:
            raise E2EBlocked("playwright_evidence_invalid")
        if not _safe_report_int(item.get("duration_ms"), minimum=0, maximum=1800000):
            raise E2EBlocked("playwright_evidence_invalid")
        if not _safe_report_int(item.get("retry"), minimum=0, maximum=2):
            raise E2EBlocked("playwright_evidence_invalid")
        is_failure = item_status in {"failed", "timedOut", "interrupted"}
        if is_failure != ("failure" in item) or (is_failure and not _valid_failure_summary(item["failure"])):
            raise E2EBlocked("playwright_evidence_invalid")
        safe_item = {key: item[key] for key in ("title", "status", "duration_ms", "retry")}
        if is_failure:
            safe_item["failure"] = {key: item["failure"][key] for key in item["failure"]}
        safe_tests.append(safe_item)

    if result_status == "passed" and any(
        item["status"] in {"failed", "timedOut", "interrupted"} for item in safe_tests
    ):
        raise E2EBlocked("playwright_evidence_invalid")

    if not isinstance(observations, list) or len(observations) != len(safe_tests):
        raise E2EBlocked("playwright_observation_count_invalid")
    safe_observations: list[dict[str, Any]] = []
    for index, observation in enumerate(observations):
        if not _validate_observation_record(observation):
            raise E2EBlocked("playwright_evidence_invalid")
        if observation["test"] != safe_tests[index]["title"] or observation["status"] != safe_tests[index]["status"]:
            raise E2EBlocked("playwright_evidence_invalid")
        safe_observation = dict(observation)
        safe_observation["guard_violations"] = [
            item if item in PLAYWRIGHT_GUARD_LABELS
            else "mutation_blocked" if item.startswith("mutation_blocked:")
            else "unclassified_guard_violation"
            for item in observation["guard_violations"]
        ]
        safe_observations.append(safe_observation)
    return safe_tests, safe_observations


def load_playwright_result(result_path: Path, observations_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        result_bytes = result_path.read_bytes()
        observation_bytes = observations_path.read_bytes()
        if len(result_bytes) + len(observation_bytes) > MAX_EVIDENCE_BYTES:
            raise E2EBlocked("playwright_evidence_size_limit_exceeded")
        result = json.loads(result_bytes.decode("utf-8"))
        observations = [
            json.loads(line) for line in observation_bytes.decode("utf-8").splitlines() if line.strip()
        ]
    except E2EBlocked:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise E2EBlocked("playwright_evidence_invalid") from exc
    if not isinstance(result, dict) or not isinstance(observations, list):
        raise E2EBlocked("playwright_evidence_invalid")
    return result, observations


def assert_browser_result(result: Mapping[str, Any], observations: Sequence[Mapping[str, Any]]) -> None:
    if has_browser_guard_violation(observations):
        raise E2EBlocked("browser_network_or_auth_guard_failed")
    tests, safe_observations = _validate_playwright_evidence(result, observations)
    if any(item["guard_violations"] for item in safe_observations):
        raise E2EBlocked("browser_network_or_auth_guard_failed")
    if not tests:
        raise E2EBlocked("playwright_no_tests_executed")
    failed = next((item for item in tests if item["status"] in {"failed", "timedOut", "interrupted"}), None)
    if failed is not None:
        raise E2EBlocked(
            "playwright_scenario_failed",
            details={"scenario_count": len(tests), "scenarios": tests, "failed_test": failed},
        )
    if len(tests) != 3 or any(item["status"] != "passed" or item["retry"] != 0 for item in tests):
        if any(item["retry"] != 0 for item in tests):
            raise E2EBlocked("playwright_retry_detected")
        raise E2EBlocked("playwright_scenarios_incomplete")
    if result.get("status") != "passed":
        raise E2EBlocked("playwright_run_incomplete")


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
    if failure_code in {
        "playwright_evidence_invalid", "playwright_evidence_size_limit_exceeded",
        "playwright_observation_count_invalid", "playwright_report_missing",
        "playwright_run_incomplete",
    }:
        return "BROWSER_EVIDENCE_INCOMPLETE"
    if failure_code == "playwright_retry_detected":
        return "LINUX_BROWSER_BEHAVIOR_FAILED"
    return "LINUX_BROWSER_EXECUTION_BLOCKED"


def startup_cause_identified(details: object) -> bool:
    if (
        not isinstance(details, dict) or details.get("status_available") is not True
        or details.get("reached_http_readiness") is True
    ):
        return False
    failure = details.get("failure_record")
    if not isinstance(failure, dict):
        return False
    if failure.get("event") == "forbidden_operation":
        return failure.get("forbidden_operation") is True and failure.get("exit_code") == 91
    return failure.get("event") == "failed" and any(
        failure.get(key) is not None
        for key in ("exception_class", "missing_module", "application_error_code")
    )


def copy_sanitized_playwright_files(result_path: Path, observations_path: Path, tokens: Sequence[str]) -> dict[str, bytes]:
    try:
        result, observations = load_playwright_result(result_path, observations_path)
    except OSError as exc:
        raise E2EBlocked("playwright_evidence_missing") from exc
    safe_tests, safe_observations = _validate_playwright_evidence(result, observations)
    safe_result = json.dumps(
        {
            "schema_version": 1,
            "status": result["status"],
            "expected_tests": 3,
            "completed_tests": len(safe_tests),
            "tests": safe_tests,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    safe_observation_bytes = b"\n".join(
        json.dumps(item, sort_keys=True, separators=(",", ":")).encode("utf-8")
        for item in safe_observations
    ) + (b"\n" if safe_observations else b"")
    if contains_secret(safe_result, tokens) or contains_secret(safe_observation_bytes, tokens):
        raise E2EBlocked("playwright_evidence_secret_detected")
    if len(safe_result) + len(safe_observation_bytes) > MAX_EVIDENCE_BYTES:
        raise E2EBlocked("playwright_evidence_size_limit_exceeded")
    return {"playwright-results.json": safe_result, "api-observations.jsonl": safe_observation_bytes}


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
