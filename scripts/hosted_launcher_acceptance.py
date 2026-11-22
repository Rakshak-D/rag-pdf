"""Supervise the real launcher on a disposable Linux CI runner, without models."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from collections.abc import Callable, Iterable
from urllib.request import ProxyHandler, Request, build_opener

import psutil

ROOT = Path(__file__).resolve().parents[1]
SHA = "65ba3e145dcb1ab75a6d89717bc46791fda88b1957b4d63c258c044f5ad7a77c"
HOST = "127.0.0.1"
BASE_PORTS = (5173, 8000, 8010, 8020)
MIN_FREE_RAM = 4 * 1024**3
PROCESS_SAMPLE_INTERVAL = 0.25
PROCESS_SAMPLE_MIN = 0.05
PROCESS_SAMPLE_MAX = 1.0
MODEL_PROCESS_MARKERS = (
    "ollama",
    "llama-server",
    "llama.cpp",
    "llama_cpp",
    "vllm",
    "tritonserver",
    "text-generation-launcher",
    "lmstudio",
    "gpt4all",
    "koboldcpp",
    "llamafile",
    "mlx_lm",
)


class AcceptanceFailure(RuntimeError):
    """A safe, non-sensitive failure category for the hosted check."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    created_at: float
    name: str
    parent_pid: int
    depth: int


@dataclass(frozen=True)
class ConnectTraceSummary:
    observed_connects: int = 0
    loopback_connects: int = 0
    non_loopback_connects: int = 0
    unparsed_ip_connects: int = 0


def find_model_processes(identities: Iterable[ProcessIdentity]) -> list[ProcessIdentity]:
    return [
        identity
        for identity in identities
        if any(marker in identity.name.lower() for marker in MODEL_PROCESS_MARKERS)
    ]


def model_process_snapshot(processes: Iterable[psutil.Process]) -> list[ProcessIdentity]:
    """Classify a process_iter snapshot without depending on optional info keys."""
    identities = [
        ProcessIdentity(
            process.pid,
            0.0,
            process.info.get("name") or "",
            0,
            0,
        )
        for process in processes
    ]
    return find_model_processes(identities)


def _identity_for(process: psutil.Process, depth: int) -> ProcessIdentity:
    return ProcessIdentity(
        process.pid,
        process.create_time(),
        process.name(),
        process.ppid(),
        depth,
    )


def _matches_identity(process: psutil.Process, identity: ProcessIdentity) -> bool:
    try:
        return process.is_running() and abs(process.create_time() - identity.created_at) < 0.01
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def terminate_owned_processes(
    identities: Iterable[ProcessIdentity],
    *,
    timeout: float = 3.0,
    process_lookup: Callable[[int], psutil.Process] = psutil.Process,
) -> list[ProcessIdentity]:
    """Stop only observed PID/start-time identities; never act by name or port."""
    targets: list[tuple[ProcessIdentity, psutil.Process]] = []
    for identity in sorted(identities, key=lambda item: item.depth, reverse=True):
        if identity.pid == os.getpid():
            continue
        try:
            process = process_lookup(identity.pid)
            if _matches_identity(process, identity):
                targets.append((identity, process))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    for _, process in targets:
        try:
            process.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    _, alive = psutil.wait_procs([process for _, process in targets], timeout=timeout)
    for process in alive:
        try:
            process.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    _, alive = psutil.wait_procs(alive, timeout=timeout)

    still_alive: list[ProcessIdentity] = []
    for identity, process in targets:
        if process in alive and _matches_identity(process, identity):
            still_alive.append(identity)
    return still_alive


class ProcessTreeMonitor:
    """Sample a task process tree and retain PID/start-time ownership records."""

    def __init__(
        self,
        root: subprocess.Popen[bytes] | subprocess.Popen[str],
        *,
        interval: float = PROCESS_SAMPLE_INTERVAL,
        launcher_marker: str | None = None,
        root_exit_expected: bool = False,
    ) -> None:
        if not PROCESS_SAMPLE_MIN <= interval <= PROCESS_SAMPLE_MAX:
            raise ValueError("process sample interval is outside the bounded range")
        self.root = root
        self.interval = interval
        self.launcher_marker = launcher_marker
        self.root_exit_expected = root_exit_expected
        self.root_identity = _identity_for(psutil.Process(root.pid), 0)
        self.launcher_identity = self.root_identity if launcher_marker is None else None
        self._identities: dict[tuple[int, int], ProcessIdentity] = {}
        self._failure_code: str | None = None
        self._sample_count = 0
        self._observed_processes: dict[tuple[int, int], str] = {}
        self._model_processes: dict[tuple[int, int], str] = {}
        self._expected_shutdown = False
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._record(self.root_identity)

    def _record(self, identity: ProcessIdentity) -> None:
        with self._lock:
            key = (identity.pid, round(identity.created_at * 1000))
            self._identities[key] = identity

    @property
    def identities(self) -> list[ProcessIdentity]:
        with self._lock:
            return list(self._identities.values())

    @property
    def failure_code(self) -> str | None:
        with self._lock:
            return self._failure_code

    @property
    def summary(self) -> dict[str, int | float]:
        with self._lock:
            return {
                "sampling_interval_seconds": self.interval,
                "samples": self._sample_count,
                "distinct_process_identities": len(self._observed_processes),
                "known_model_process_identities": len(self._model_processes),
            }

    def _fail(self, code: str) -> None:
        with self._lock:
            if self._failure_code is None:
                self._failure_code = code

    def _is_launcher(self, process: psutil.Process) -> bool:
        if self.launcher_marker is None:
            return process.pid == self.root.pid
        try:
            return any(Path(argument).name == self.launcher_marker for argument in process.cmdline())
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return False

    def sample_once(self) -> None:
        with self._lock:
            self._sample_count += 1
        if self.root.poll() is not None:
            if not self._expected_shutdown and not self.root_exit_expected:
                self._fail("unexpected_launcher_exit")
            return
        try:
            root_process = psutil.Process(self.root.pid)
            if not _matches_identity(root_process, self.root_identity):
                self._fail("launcher_identity_changed")
                return
            observed = [self.root_identity]
            with self._lock:
                root_key = (self.root_identity.pid, round(self.root_identity.created_at * 1000))
                self._observed_processes[root_key] = self.root_identity.name
            descendants = root_process.children(recursive=True)
            depth_by_pid = {self.root.pid: 0}
            for child in descendants:
                try:
                    parent_pid = child.ppid()
                    depth = depth_by_pid.get(parent_pid, 0) + 1
                    depth_by_pid[child.pid] = depth
                    identity = _identity_for(child, depth)
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
                self._record(identity)
                with self._lock:
                    key = (identity.pid, round(identity.created_at * 1000))
                    self._observed_processes[key] = identity.name
                observed.append(identity)
                if self.launcher_identity is None and self._is_launcher(child):
                    self.launcher_identity = identity

            if self.launcher_marker and self.launcher_identity is not None:
                if all(item.pid != self.launcher_identity.pid for item in observed):
                    if not self._expected_shutdown:
                        self._fail("unexpected_launcher_exit")
            model_processes = find_model_processes(observed)
            if model_processes:
                with self._lock:
                    for identity in model_processes:
                        key = (identity.pid, round(identity.created_at * 1000))
                        self._model_processes[key] = identity.name
                self._fail("unexpected_task_model_process")
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            if not self._expected_shutdown:
                self._fail("process_tree_observation_failed")

    def start(self) -> None:
        if self._thread is not None:
            return
        self.sample_once()
        self._thread = threading.Thread(target=self._run, name="launcher-process-monitor", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval):
            self.sample_once()

    def check(self) -> None:
        self.sample_once()
        if self.failure_code:
            raise AcceptanceFailure(self.failure_code)

    def mark_expected_shutdown(self) -> None:
        with self._lock:
            self._expected_shutdown = True

    def alive_identities(self) -> list[ProcessIdentity]:
        alive: list[ProcessIdentity] = []
        for identity in self.identities:
            try:
                process = psutil.Process(identity.pid)
                if _matches_identity(process, identity) and process.status() != psutil.STATUS_ZOMBIE:
                    alive.append(identity)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return alive

    def terminate_owned(self, *, timeout: float = 3.0) -> list[ProcessIdentity]:
        return terminate_owned_processes(self.identities, timeout=timeout)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.interval * 4))


def wait_monitored_process(
    process: subprocess.Popen[bytes] | subprocess.Popen[str],
    monitor: ProcessTreeMonitor,
    *,
    timeout: float,
    termination_timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    while process.poll() is None:
        monitor.check()
        if time.monotonic() >= deadline:
            monitor.terminate_owned(timeout=termination_timeout)
            try:
                process.wait(timeout=termination_timeout)
            except subprocess.TimeoutExpired:
                raise AcceptanceFailure("browser_process_cleanup_timeout") from None
            raise AcceptanceFailure("browser_test_timeout")
        time.sleep(min(monitor.interval, 0.1))
    monitor.check()
    remaining = [item for item in monitor.alive_identities() if item.pid != process.pid]
    if remaining:
        monitor.terminate_owned(timeout=termination_timeout)
        raise AcceptanceFailure("browser_child_survived_exit")
    if process.returncode != 0:
        raise AcceptanceFailure("browser_test_failed")


def run_with_cleanup(
    work: Callable[[], object],
    cleanup: Callable[[], object],
    report_cleanup_error: Callable[[str], None],
) -> object:
    result: object = None
    primary: BaseException | None = None
    primary_traceback = None
    try:
        result = work()
    except BaseException as error:
        primary = error
        primary_traceback = error.__traceback__
    try:
        cleanup()
    except BaseException as error:
        report_cleanup_error(type(error).__name__)
        if primary is None:
            raise
    if primary is not None:
        raise primary.with_traceback(primary_traceback)
    return result


def wait_for_ports_release(
    ports: set[int] | tuple[int, ...],
    *,
    timeout: float,
    interval: float,
    listener_ports: Callable[[], set[int]],
) -> set[int]:
    deadline = time.monotonic() + timeout
    remaining = set(ports)
    while time.monotonic() < deadline:
        remaining = set(ports) & listener_ports()
        if not remaining:
            return set()
        time.sleep(interval)
    return remaining


def _send_process_signal(identity: ProcessIdentity, received_signal: int) -> None:
    process = psutil.Process(identity.pid)
    if not _matches_identity(process, identity):
        raise psutil.NoSuchProcess(identity.pid)
    process.send_signal(received_signal)


def shutdown_task(
    root: subprocess.Popen[bytes] | subprocess.Popen[str],
    monitor: ProcessTreeMonitor,
    target: ProcessIdentity,
    *,
    ports: set[int] | tuple[int, ...],
    graceful_timeout: float = 35.0,
    termination_timeout: float = 5.0,
    port_timeout: float = 8.0,
    port_interval: float = 0.2,
    listener_ports: Callable[[], set[int]],
    signal_sender: Callable[[ProcessIdentity, int], None] = _send_process_signal,
) -> None:
    monitor.mark_expected_shutdown()
    try:
        if _matches_identity(psutil.Process(target.pid), target):
            signal_sender(target, signal.SIGINT)
        elif root.poll() is None:
            raise AcceptanceFailure("launcher_identity_missing_at_shutdown")
        root.wait(timeout=graceful_timeout)
    except subprocess.TimeoutExpired:
        monitor.terminate_owned(timeout=termination_timeout)
        if root.poll() is None:
            root.wait(timeout=termination_timeout)
        raise AcceptanceFailure("launcher_sigint_timeout") from None
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        monitor.terminate_owned(timeout=termination_timeout)
        if root.poll() is None:
            try:
                root.wait(timeout=termination_timeout)
            except subprocess.TimeoutExpired:
                pass
        raise AcceptanceFailure("launcher_shutdown_signal_failed") from None

    remaining = monitor.alive_identities()
    if remaining:
        monitor.terminate_owned(timeout=termination_timeout)
        raise AcceptanceFailure("owned_child_survived_shutdown")

    remaining_ports = wait_for_ports_release(
        ports,
        timeout=port_timeout,
        interval=port_interval,
        listener_ports=listener_ports,
    )
    if remaining_ports:
        monitor.terminate_owned(timeout=termination_timeout)
        remaining_ports = wait_for_ports_release(
            remaining_ports,
            timeout=port_timeout,
            interval=port_interval,
            listener_ports=listener_ports,
        )
        if remaining_ports:
            raise AcceptanceFailure("task_ports_not_released")
        raise AcceptanceFailure("port_release_required_owned_cleanup")
    if root.returncode not in (0, -signal.SIGINT):
        raise AcceptanceFailure("launcher_shutdown_nonzero_exit")


_IPV4_ADDRESS = re.compile(r'sin_addr=inet_addr\("([^\"]+)"\)')
_IPV6_ADDRESS = re.compile(r'sin6_addr=inet_pton\(AF_INET6, "([^\"]+)"\)')


def parse_connect_trace(lines: Iterable[str]) -> ConnectTraceSummary:
    observed = loopback = external = unparsed = 0
    for line in lines:
        if "connect(" not in line:
            continue
        if "sa_family=AF_INET6" in line:
            match = _IPV6_ADDRESS.search(line)
        elif "sa_family=AF_INET" in line:
            match = _IPV4_ADDRESS.search(line)
        else:
            continue
        observed += 1
        if match is None:
            unparsed += 1
            continue
        try:
            address = ipaddress.ip_address(match.group(1))
        except ValueError:
            unparsed += 1
            continue
        if address.is_loopback:
            loopback += 1
        else:
            external += 1
    return ConnectTraceSummary(observed, loopback, external, unparsed)


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


def _current_listener_ports() -> set[int]:
    return {
        connection.laddr.port
        for connection in psutil.net_connections(kind="inet")
        if connection.status == psutil.CONN_LISTEN
    }


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _save_failure_report(error: BaseException, diagnostics: dict[str, object]) -> None:
    code = error.code if isinstance(error, AcceptanceFailure) else "unexpected_failure"
    report = {
        "failure_code": code,
        "error_type": type(error).__name__,
        "resources": diagnostics.get("resources"),
        "ports": diagnostics.get("ports"),
        "process_monitor": diagnostics.get("process_monitor"),
        "network_observation": diagnostics.get("network_observation"),
        "cleanup_error_types": diagnostics.get("cleanup_error_types", []),
        "process_sampling_interval_seconds": PROCESS_SAMPLE_INTERVAL,
        "process_argument_logging": False,
    }
    print("HOSTED_FAILURE", json.dumps(report, sort_keys=True), flush=True)
    runner_temp = os.environ.get("RUNNER_TEMP")
    if runner_temp:
        try:
            (Path(runner_temp) / "rag-pdf-hosted-launcher-failure.json").write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError:
            print("HOSTED_FAILURE_REPORT_WRITE_FAILED", flush=True)


def _main(diagnostics: dict[str, object]) -> None:
    if sys.platform != "linux" or os.environ.get("GITHUB_ACTIONS") != "true":
        raise AcceptanceFailure("hosted_ubuntu_runner_required")
    checkout_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if checkout_sha != os.environ.get("GITHUB_SHA"):
        raise AcceptanceFailure("checkout_does_not_match_workflow_commit")
    pdf = ROOT / "examples/demo/Orbit_Field_Notes.pdf"
    if hashlib.sha256(pdf.read_bytes()).hexdigest() != SHA:
        raise AcceptanceFailure("tracked_pdf_digest_mismatch")

    initial_models = model_process_snapshot(psutil.process_iter(["name"]))
    if initial_models:
        raise AcceptanceFailure("model_process_present_before_startup")

    strace = shutil.which("strace")
    if not strace:
        raise AcceptanceFailure("process_scoped_connect_observer_unavailable")
    strace_version = subprocess.run(
        [strace, "-V"], check=True, capture_output=True, text=True, timeout=5
    ).stdout.splitlines()[0]
    offset, ports = choose_ports()
    diagnostics["ports"] = list(ports)
    print("HOSTED_PREFLIGHT", json.dumps({
        "checkout_sha": checkout_sha,
        "ports": ports,
        "python": sys.version.split()[0],
        "node": subprocess.check_output(["node", "--version"], text=True).strip(),
        "strace": strace_version,
        "model_processes_before_startup": 0,
    }), flush=True)

    with tempfile.TemporaryDirectory(prefix="rag-pdf-hosted-launcher-") as temporary:
        root = Path(temporary)
        source, storage, ordinary, bootstrap = [
            root / name
            for name in ("pdf-source", "index-storage", "ordinary-runtime-state", "bootstrap-runtime-state")
        ]
        for path in (source, storage, ordinary, bootstrap):
            path.mkdir()
        shutil.copyfile(pdf, source / pdf.name)
        if hashlib.sha256((source / pdf.name).read_bytes()).hexdigest() != SHA:
            raise AcceptanceFailure("copied_pdf_digest_mismatch")

        env = {
            key: os.environ[key]
            for key in ("PATH", "HOME", "TMPDIR", "LANG", "PLAYWRIGHT_BROWSERS_PATH")
            if key in os.environ
        }
        env.update({
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "ANONYMIZED_TELEMETRY": "False",
            "DO_NOT_TRACK": "1",
            "OTEL_SDK_DISABLED": "true",
            "RAG_WORKBENCH_DATA_DIR": str(source),
            "RAG_WORKBENCH_INDEX_STORAGE_ROOT": str(storage),
            "RAG_WORKBENCH_LOCAL_STATE_DIR": str(ordinary),
            "RAG_WORKBENCH_BOOTSTRAP_STATE_DIR": str(bootstrap),
            "RAG_WORKBENCH_ARTIFACT_STORE_DIR": str(ordinary / "artifacts"),
            "RAG_WORKBENCH_CATALOG_DB_PATH": str(ordinary / "catalog.sqlite3"),
            "RAG_WORKBENCH_JOBS_DB_PATH": str(ordinary / "jobs.sqlite3"),
        })
        trace_path = root / "connect.trace"
        launcher: subprocess.Popen[bytes] | None = None
        launcher_monitor: ProcessTreeMonitor | None = None
        browser: subprocess.Popen[bytes] | None = None
        browser_monitor: ProcessTreeMonitor | None = None
        cleanup_error_types: list[str] = []
        diagnostics["resources"] = {}
        diagnostics["cleanup_error_types"] = cleanup_error_types

        def listener_ports() -> set[int]:
            return _current_listener_ports() & set(ports)

        def run_acceptance() -> None:
            nonlocal launcher, launcher_monitor, browser, browser_monitor
            before = resource_snapshot(root)
            diagnostics["resources"] = before
            print("HOSTED_RESOURCE_GATE", json.dumps(before, sort_keys=True), flush=True)
            if before["available_bytes"] < MIN_FREE_RAM:
                raise AcceptanceFailure("HOSTED_RESOURCE_BLOCKED")

            command = [
                strace,
                "-f",
                "-qq",
                "-e",
                "trace=connect",
                "-o",
                str(trace_path),
                "--",
                sys.executable,
                "scripts/start_local_app.py",
                "--port-offset",
                str(offset),
            ]
            launcher_log = root / "launcher.log"
            with launcher_log.open("w", encoding="utf-8") as output:
                launcher = subprocess.Popen(
                    command,
                    cwd=ROOT,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                launcher_monitor = ProcessTreeMonitor(
                    launcher,
                    interval=PROCESS_SAMPLE_INTERVAL,
                    launcher_marker="start_local_app.py",
                )
                launcher_monitor.start()
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    launcher_monitor.check()
                    output_text = _read_text(launcher_log)
                    if f"Source checkout: {ROOT}" in output_text and "RAG-PDF is running at" in output_text:
                        if launcher_monitor.launcher_identity is None:
                            time.sleep(PROCESS_SAMPLE_INTERVAL)
                            continue
                        break
                    time.sleep(PROCESS_SAMPLE_INTERVAL)
                else:
                    raise AcceptanceFailure("launcher_readiness_timeout")

                launcher_monitor.check()
                if launcher.poll() is not None:
                    raise AcceptanceFailure("launcher_exited_before_browser_acceptance")
                identities = launcher_monitor.identities
                identity_pids = {item.pid for item in identities}
                listeners = [
                    connection
                    for connection in psutil.net_connections(kind="inet")
                    if connection.status == psutil.CONN_LISTEN and connection.laddr.port in ports
                ]
                if (
                    len(listeners) != 4
                    or {connection.laddr.port for connection in listeners} != set(ports)
                    or any(connection.laddr.ip != HOST for connection in listeners)
                ):
                    raise AcceptanceFailure("launcher_listeners_missing_or_not_loopback")
                if not {connection.pid for connection in listeners}.issubset(identity_pids):
                    raise AcceptanceFailure("listener_owner_not_in_task_process_tree")

                app, api, boot, gateway = ports
                for port, path, header in (
                    (api, "/health", None),
                    (boot, "/health", None),
                    (gateway, "/gateway-health", f"{HOST}:{app}"),
                    (app, "/", None),
                ):
                    status, _ = read(f"http://{HOST}:{port}{path}", header)
                    if status != 200:
                        raise AcceptanceFailure("service_health_failed")
                print("HOSTED_SERVICES_READY", json.dumps({
                    "launcher_pid": launcher_monitor.launcher_identity.pid,
                    "listeners": [
                        {"pid": connection.pid, "address": connection.laddr.ip, "port": connection.laddr.port}
                        for connection in listeners
                    ],
                    "health_statuses": [200, 200, 200, 200],
                }), flush=True)

                browser_env = dict(env, CI="true", M9C2B_APP_URL=f"http://{HOST}:{app}")
                browser = subprocess.Popen(
                    ["node", "node_modules/playwright/cli.js", "test", "--config", "playwright.hosted-launcher.config.ts"],
                    cwd=ROOT / "frontend",
                    env=browser_env,
                    stdin=subprocess.DEVNULL,
                )
                browser_monitor = ProcessTreeMonitor(
                    browser,
                    interval=PROCESS_SAMPLE_INTERVAL,
                    root_exit_expected=True,
                )
                browser_monitor.start()
                wait_monitored_process(browser, browser_monitor, timeout=180, termination_timeout=5)
                launcher_monitor.check()
                if any(storage.iterdir()):
                    raise AcceptanceFailure("index_storage_unexpectedly_populated")

        def cleanup() -> None:
            cleanup_failures: list[str] = []
            if browser_monitor is not None:
                try:
                    if browser is not None and (
                        browser.poll() is None
                        or any(item.pid != browser.pid for item in browser_monitor.alive_identities())
                    ):
                        browser_monitor.mark_expected_shutdown()
                        browser_monitor.terminate_owned(timeout=5)
                        if browser.poll() is None:
                            try:
                                browser.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                cleanup_failures.append("browser_process_survived_termination")
                    if any(item.pid != (browser.pid if browser else -1) for item in browser_monitor.alive_identities()):
                        cleanup_failures.append("browser_child_survived_cleanup")
                except BaseException as error:
                    cleanup_failures.append(type(error).__name__)
                finally:
                    browser_monitor.stop()

            if launcher_monitor is not None and launcher is not None:
                try:
                    target = launcher_monitor.launcher_identity or launcher_monitor.root_identity
                    shutdown_task(
                        launcher,
                        launcher_monitor,
                        target,
                        ports=set(ports),
                        listener_ports=listener_ports,
                    )
                    print("HOSTED_SHUTDOWN_PASS", json.dumps({
                        "owned_processes_remaining": 0,
                        "task_listeners_remaining": 0,
                    }), flush=True)
                except BaseException as error:
                    cleanup_failures.append(type(error).__name__)
                    launcher_monitor.terminate_owned(timeout=5)
                    if launcher.poll() is None:
                        try:
                            launcher.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            cleanup_failures.append("launcher_process_survived_termination")
                    if launcher_monitor.alive_identities():
                        cleanup_failures.append("launcher_descendant_survived_cleanup")
                    if listener_ports():
                        cleanup_failures.append("task_listener_survived_cleanup")
                finally:
                    launcher_monitor.stop()

            if cleanup_failures:
                cleanup_error_types.extend(cleanup_failures)
                raise AcceptanceFailure("task_process_cleanup_incomplete")

        network = ConnectTraceSummary(unparsed_ip_connects=1)
        try:
            run_with_cleanup(run_acceptance, cleanup, cleanup_error_types.append)
        finally:
            diagnostics["process_monitor"] = {
                "launcher_tree": launcher_monitor.summary if launcher_monitor else None,
                "browser_tree": browser_monitor.summary if browser_monitor else None,
                "limitation": "bounded periodic sampling can miss very short-lived or reparented processes",
            }
            print("HOSTED_PROCESS_MONITOR", json.dumps(diagnostics["process_monitor"], sort_keys=True), flush=True)
            try:
                if trace_path.exists():
                    with trace_path.open("r", encoding="utf-8", errors="replace") as trace_file:
                        network = parse_connect_trace(trace_file)
            except OSError:
                pass
            network_data = {
                "observer": "strace connect syscalls for launcher descendants",
                "observed_connects": network.observed_connects,
                "loopback_connects": network.loopback_connects,
                "non_loopback_connects": network.non_loopback_connects,
                "unparsed_ip_connects": network.unparsed_ip_connects,
                "coverage_limit": "observation only; excludes datagram-only traffic and does not block egress",
            }
            diagnostics["network_observation"] = network_data
            print("HOSTED_BACKEND_NETWORK_OBSERVATION", json.dumps(network_data, sort_keys=True), flush=True)
        if network.non_loopback_connects or network.unparsed_ip_connects:
            raise AcceptanceFailure("non_loopback_or_unparsed_backend_connect_observed")
        if any(storage.iterdir()):
            raise AcceptanceFailure("index_storage_unexpectedly_populated")

    if root.exists():
        raise AcceptanceFailure("temporary_workspace_cleanup_failed")
    print("HOSTED_TEMP_CLEANUP_PASS", flush=True)


def main() -> None:
    diagnostics: dict[str, object] = {}
    try:
        _main(diagnostics)
    except BaseException as error:
        _save_failure_report(error, diagnostics)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
