"""Focused tests for hosted launcher process and network supervision."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from scripts.hosted_launcher_acceptance import (
    AcceptanceFailure,
    ProcessIdentity,
    ProcessTreeMonitor,
    find_model_processes,
    parse_connect_trace,
    run_with_cleanup,
    shutdown_task,
    wait_for_ports_release,
)


class ModelProcessDetectionTests(unittest.TestCase):
    def test_synthetic_model_process_names_are_detected_without_arguments(self) -> None:
        synthetic = [
            ProcessIdentity(41001, 10.0, "ollama", 41000, 1),
            ProcessIdentity(41002, 11.0, "llama-server", 41000, 1),
            ProcessIdentity(41003, 12.0, "python", 41000, 1),
            ProcessIdentity(41004, 13.0, "node", 41000, 1),
        ]

        self.assertEqual(
            [(item.pid, item.name) for item in find_model_processes(synthetic)],
            [(41001, "ollama"), (41002, "llama-server")],
        )

    @unittest.skipUnless(sys.platform == "linux", "Linux process names are exercised on the hosted runner")
    def test_monitor_detects_a_harmless_task_owned_synthetic_model_process(self) -> None:
        child_code = (
            "import ctypes,time; "
            "ctypes.CDLL(None).prctl(15,b'ollama',0,0,0); time.sleep(30)"
        )
        root_code = (
            "import subprocess,sys,time; "
            "subprocess.Popen([sys.executable,'-c',sys.argv[1]]); time.sleep(30)"
        )
        root = subprocess.Popen([sys.executable, "-c", root_code, child_code])
        monitor = ProcessTreeMonitor(root, interval=0.05)
        monitor.start()
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and monitor.failure_code is None:
                time.sleep(0.02)
            with self.assertRaisesRegex(AcceptanceFailure, "unexpected_task_model_process"):
                monitor.check()
        finally:
            monitor.terminate_owned(timeout=2)
            monitor.stop()
            if root.poll() is None:
                root.wait(timeout=2)


class ProcessTreeTests(unittest.TestCase):
    def test_unexpected_launcher_exit_is_reported(self) -> None:
        process = subprocess.Popen([sys.executable, "-c", "pass"])
        monitor = ProcessTreeMonitor(process, interval=0.05)
        process.wait(timeout=5)

        monitor.sample_once()

        with self.assertRaisesRegex(RuntimeError, "unexpected_launcher_exit"):
            monitor.check()
        monitor.stop()

    def test_owned_child_surviving_parent_is_found_and_cleaned_by_identity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="launcher-supervision-test-") as temporary:
            child_pid_file = Path(temporary) / "child.pid"
            child_code = "import time; time.sleep(60)"
            root_code = (
                "import subprocess,sys,time; "
                "child=subprocess.Popen([sys.executable,'-c',sys.argv[1]]); "
                "open(sys.argv[2],'w').write(str(child.pid)); time.sleep(60)"
            )
            root = subprocess.Popen(
                [sys.executable, "-c", root_code, child_code, str(child_pid_file)]
            )
            monitor = ProcessTreeMonitor(root, interval=0.05)
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not child_pid_file.exists():
                    time.sleep(0.02)
                self.assertTrue(child_pid_file.exists())
                child_pid = int(child_pid_file.read_text(encoding="ascii"))
                monitor.sample_once()
                self.assertIn(child_pid, {identity.pid for identity in monitor.identities})

                root.terminate()
                root.wait(timeout=5)
                monitor.sample_once()
                self.assertIn(child_pid, {identity.pid for identity in monitor.alive_identities()})

                monitor.terminate_owned(timeout=2)
                self.assertNotIn(child_pid, {identity.pid for identity in monitor.alive_identities()})
            finally:
                monitor.terminate_owned(timeout=2)
                monitor.stop()
                if root.poll() is None:
                    root.kill()
                    root.wait(timeout=5)

    def test_sigint_timeout_cleans_only_recorded_task_processes_and_fails(self) -> None:
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"]
        )
        monitor = ProcessTreeMonitor(process, interval=0.05)
        monitor.sample_once()
        target = next(item for item in monitor.identities if item.pid == process.pid)

        with self.assertRaisesRegex(RuntimeError, "launcher_sigint_timeout"):
            shutdown_task(
                process,
                monitor,
                target,
                ports=(),
                graceful_timeout=0.05,
                termination_timeout=2,
                port_timeout=0.05,
                port_interval=0.01,
                listener_ports=lambda: set(),
                signal_sender=lambda _identity, _signal: None,
            )

        self.assertIsNotNone(process.poll())
        monitor.stop()


class CleanupAndPortTests(unittest.TestCase):
    def test_browser_failure_terminates_only_its_recorded_process_tree(self) -> None:
        browser = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        monitor = ProcessTreeMonitor(browser, interval=0.05, root_exit_expected=True)
        monitor.start()
        primary = RuntimeError("browser acceptance failed")

        def fail_browser() -> None:
            raise primary

        def cleanup_browser() -> None:
            monitor.mark_expected_shutdown()
            monitor.terminate_owned(timeout=2)
            if browser.poll() is None:
                browser.wait(timeout=2)
            monitor.stop()

        with self.assertRaises(RuntimeError) as raised:
            run_with_cleanup(fail_browser, cleanup_browser, lambda _error_type: None)

        self.assertIs(raised.exception, primary)
        self.assertIsNotNone(browser.poll())

    def test_browser_failure_is_preserved_when_cleanup_also_fails(self) -> None:
        primary = RuntimeError("browser acceptance failed")
        cleanup_error = OSError("cleanup details must stay sanitized")
        cleanup_reports: list[str] = []

        def fail_browser() -> None:
            raise primary

        def fail_cleanup() -> None:
            raise cleanup_error

        with self.assertRaises(RuntimeError) as raised:
            run_with_cleanup(fail_browser, fail_cleanup, cleanup_reports.append)

        self.assertIs(raised.exception, primary)
        self.assertEqual(cleanup_reports, ["OSError"])

    def test_port_still_held_is_reported_without_signaling_its_owner(self) -> None:
        port = 12345
        still_listening = wait_for_ports_release(
            {port},
            timeout=0.03,
            interval=0.005,
            listener_ports=lambda: {port},
        )

        self.assertEqual(still_listening, {port})


class ConnectTraceTests(unittest.TestCase):
    def test_loopback_connects_are_distinguished_from_external_attempts(self) -> None:
        trace = """\
123 connect(3, {sa_family=AF_INET, sin_port=htons(80), sin_addr=inet_addr(\"127.0.0.1\")}, 16) = 0
123 connect(4, {sa_family=AF_INET6, sin6_port=htons(80), sin6_addr=inet_pton(AF_INET6, \"::1\")}, 28) = 0
123 connect(5, {sa_family=AF_INET, sin_port=htons(443), sin_addr=inet_addr(\"203.0.113.7\")}, 16) = -1 ENETUNREACH
"""

        result = parse_connect_trace(trace.splitlines())

        self.assertEqual(result.loopback_connects, 2)
        self.assertEqual(result.non_loopback_connects, 1)
        self.assertEqual(result.unparsed_ip_connects, 0)


if __name__ == "__main__":
    unittest.main()
