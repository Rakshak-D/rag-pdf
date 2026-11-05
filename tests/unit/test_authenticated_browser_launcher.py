from __future__ import annotations

import hashlib
import importlib
from importlib.machinery import PathFinder
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import authenticated_browser_e2e as launcher


SECRET_SENTINEL = "synthetic-bearer-secret-should-never-be-retained"


def _attempt(tmp_path: Path) -> launcher.LauncherAttempt:
    workspace = tmp_path / "attempt"
    workspace.mkdir()
    identity = {
        "repository_root": str(launcher.ROOT),
        "head": "a" * 40,
        "branch": launcher.EXPECTED_BRANCH,
    }
    return launcher._initialize_attempt(
        workspace,
        attempt_id="synthetic-attempt-id",
        git_identity=identity,
        source_sha256="b" * 64,
        python_version="synthetic-python-version",
    )


def _assert_sanitized_failure(
    attempt: launcher.LauncherAttempt,
    expected_stage: str,
    expected_code: str,
) -> dict:
    start_path = attempt.evidence_dir / "launcher-start.json"
    failure_path = attempt.evidence_dir / "launcher-failure.json"
    progress_path = attempt.evidence_dir / "launcher-progress.jsonl"
    assert start_path.is_file() and start_path.stat().st_size > 0
    assert failure_path.is_file() and failure_path.stat().st_size > 0
    start = json.loads(start_path.read_text(encoding="utf-8"))
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    progress = [json.loads(line) for line in progress_path.read_text(encoding="utf-8").splitlines()]
    assert start["attempt_id"] == failure["attempt_id"] == attempt.attempt_id
    assert failure["last_attempted_stage"] == expected_stage
    assert failure["failure_code"] == expected_code
    assert any(row["event"] == "stage_started" and row["stage"] == expected_stage for row in progress)
    assert progress[-1]["event"] == "stage_failed"
    assert failure["fixtures_created"] is False
    assert failure["subprocess_started"] is False
    assert failure["managed_process_state"] == {
        "started_count": 0,
        "active_count": 0,
        "cleanup_status": "not_required_no_active_managed_children",
    }
    assert attempt.managed_child_pids == []
    assert attempt.active_managed_child_pids == []
    serialized = b"".join(path.read_bytes() for path in attempt.evidence_dir.iterdir() if path.is_file())
    assert SECRET_SENTINEL.encode() not in serialized
    assert b"exception_message" not in serialized
    assert b"traceback" not in serialized
    return failure


def _run_one_failure(attempt: launcher.LauncherAttempt, stage: str, operation) -> dict:
    exit_code, result = launcher._execute_preflight(attempt, [(stage, operation)])
    assert exit_code != 0
    assert result["classification"] == "LAUNCHER_PREFLIGHT_BLOCKED"
    assert result["durable_evidence"] is True
    return result


def test_requirements_integrity_failure_is_durable_before_fixtures(tmp_path):
    attempt = _attempt(tmp_path)
    candidate = tmp_path / "requirements.txt"
    candidate.write_text(f"{SECRET_SENTINEL}\n", encoding="utf-8")
    _run_one_failure(
        attempt,
        "requirements_integrity",
        lambda: launcher._verify_requirements(candidate, "0" * 64),
    )
    _assert_sanitized_failure(attempt, "requirements_integrity", "requirements_integrity_mismatch")


def test_invalid_disk_budget_is_captured_after_start_record(tmp_path):
    attempt = _attempt(tmp_path)
    steps = dict(launcher._build_preflight_steps(attempt, -1))
    _run_one_failure(attempt, "task_budget", steps["task_budget"])
    _assert_sanitized_failure(attempt, "task_budget", "prior_disk_accounting_invalid")


def test_missing_playwright_dependency_is_durably_blocked(tmp_path):
    attempt = _attempt(tmp_path)
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text(json.dumps({"devDependencies": {"@playwright/test": "1.64.1"}}))
    (frontend / "package-lock.json").write_text(json.dumps({"packages": {"node_modules/@playwright/test": {"version": "1.64.1"}}}))
    _run_one_failure(attempt, "playwright_dependency_verification", lambda: launcher._verify_playwright(frontend))
    _assert_sanitized_failure(attempt, "playwright_dependency_verification", "playwright_test_dependency_unavailable")


def test_missing_edge_is_durably_blocked_without_fallback(tmp_path, monkeypatch):
    attempt = _attempt(tmp_path)
    monkeypatch.setattr(
        launcher,
        "_discover_edge",
        lambda: (_ for _ in ()).throw(launcher.E2EBlocked("microsoft_edge_stable_unavailable")),
    )
    steps = dict(launcher._build_preflight_steps(attempt, 0))
    _run_one_failure(
        attempt,
        "edge_discovery",
        steps["edge_discovery"],
    )
    failure = _assert_sanitized_failure(attempt, "edge_discovery", "microsoft_edge_stable_unavailable")
    assert failure["edge_discovery"] == {
        "status": "unavailable",
        "path": None,
        "file_version": None,
        "discovery_source": "standard_stable_locations",
    }


def test_edge_discovery_records_resolved_allowlisted_path_and_version(tmp_path):
    program_files = tmp_path / "Program Files"
    edge_path = program_files / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge_path.parent.mkdir(parents=True)
    edge_path.write_bytes(b"synthetic executable placeholder")
    result = launcher._discover_edge(
        {"ProgramFiles": str(program_files)},
        version_reader=lambda path: "156.0.1.2" if path == edge_path.resolve() else pytest.fail("unexpected Edge path"),
    )
    assert result == {
        "status": "found",
        "path": str(edge_path.resolve()),
        "file_version": "156.0.1.2",
        "discovery_source": "program_files",
    }


@pytest.mark.parametrize(
    ("environment_key", "source"),
    [
        ("PROGRAMFILES(X86)", "program_files_x86"),
        ("ProgramFiles(x86)", "program_files_x86"),
        ("programfiles(x86)", "program_files_x86"),
        ("pRoGrAmFiLeS(X86)", "program_files_x86"),
        ("PROGRAMFILES", "program_files"),
        ("localappdata", "local_app_data"),
    ],
)
def test_edge_discovery_matches_environment_keys_case_insensitively(
    tmp_path, environment_key, source
):
    install_root = tmp_path / "synthetic-install-root"
    edge_path = install_root / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge_path.parent.mkdir(parents=True)
    edge_path.write_bytes(b"synthetic executable placeholder")

    result = launcher._discover_edge(
        {environment_key: str(install_root)},
        version_reader=lambda path: "156.0.1.2" if path == edge_path.resolve() else pytest.fail("unexpected Edge path"),
    )

    assert result == {
        "status": "found",
        "path": str(edge_path.resolve()),
        "file_version": "156.0.1.2",
        "discovery_source": source,
    }


def test_edge_discovery_prefers_x86_program_files(tmp_path):
    x86_root = tmp_path / "Program Files x86"
    program_root = tmp_path / "Program Files"
    x86_edge = x86_root / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    program_edge = program_root / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    x86_edge.parent.mkdir(parents=True)
    program_edge.parent.mkdir(parents=True)
    x86_edge.write_bytes(b"x86 placeholder")
    program_edge.write_bytes(b"program placeholder")

    result = launcher._discover_edge(
        {"PROGRAMFILES": str(program_root), "PROGRAMFILES(X86)": str(x86_root)},
        version_reader=lambda path: "test-version",
    )

    assert result["path"] == str(x86_edge.resolve())
    assert result["discovery_source"] == "program_files_x86"


def test_edge_candidate_deduplication_preserves_first_source(tmp_path):
    install_root = tmp_path / "same-install-root"

    candidates = launcher._edge_candidates({
        "ProgramFiles": str(install_root),
        "PROGRAMFILES(X86)": str(install_root),
        "programfiles(x86)": str(install_root),
    })

    assert candidates == [(
        "program_files_x86",
        install_root / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    )]


def test_edge_discovery_rejects_conflicting_case_variants_deterministically(tmp_path):
    x86_root = tmp_path / "Program Files x86"
    edge_path = x86_root / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge_path.parent.mkdir(parents=True)
    edge_path.write_bytes(b"synthetic executable placeholder")

    with pytest.raises(launcher.E2EBlocked) as error:
        launcher._discover_edge({
            "PROGRAMFILES(X86)": str(x86_root),
            "ProgramFiles(x86)": str(tmp_path / "conflicting-x86-root"),
        })

    assert error.value.code == "edge_environment_ambiguous"


def test_edge_discovery_missing_executable_keeps_stable_code(tmp_path):
    with pytest.raises(launcher.E2EBlocked) as error:
        launcher._discover_edge({"PROGRAMFILES": str(tmp_path)})

    assert error.value.code == "microsoft_edge_stable_unavailable"


def test_edge_discovery_does_not_launch_browser_or_child_process(tmp_path, monkeypatch):
    install_root = tmp_path / "Program Files"
    edge_path = install_root / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge_path.parent.mkdir(parents=True)
    edge_path.write_bytes(b"synthetic executable placeholder")

    def unexpected_process(*args, **kwargs):
        pytest.fail("Edge discovery must not launch a child process")

    monkeypatch.setattr(subprocess, "Popen", unexpected_process)
    monkeypatch.setattr(subprocess, "run", unexpected_process)
    result = launcher._discover_edge(
        {"ProgramFiles": str(install_root)},
        version_reader=lambda path: "test-version",
    )

    assert result["status"] == "found"
    assert result["path"] == str(edge_path.resolve())


def test_loopback_port_ownership_failure_is_durably_blocked(tmp_path):
    attempt = _attempt(tmp_path)
    ports = iter((31001, 31002, 31003))
    ownership = {31001: [], 31002: [{"pid": 23}], 31003: []}
    _run_one_failure(
        attempt,
        "loopback_port_selection_and_ownership",
        lambda: launcher._select_loopback_ports(lambda: next(ports), lambda port: ownership[port]),
    )
    _assert_sanitized_failure(
        attempt,
        "loopback_port_selection_and_ownership",
        "dynamically_selected_port_not_free",
    )


def test_unexpected_preflight_exception_keeps_safe_class_and_stage(tmp_path):
    attempt = _attempt(tmp_path)

    def fail_with_secret_message():
        raise RuntimeError(f"sensitive detail {SECRET_SENTINEL}")

    _run_one_failure(attempt, "configuration_and_manifest_generation", fail_with_secret_message)
    failure = _assert_sanitized_failure(
        attempt,
        "configuration_and_manifest_generation",
        "preflight_unexpected",
    )
    assert failure["exception_class"] == "RuntimeError"


def test_start_record_is_durable_readable_and_uses_actual_git_identity(tmp_path):
    identity = launcher._read_git_identity(launcher.ROOT)
    expected_head = subprocess.run(
        ["git", "-C", str(launcher.ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip().lower()
    workspace = tmp_path / "attempt"
    workspace.mkdir()
    source_hash = hashlib.sha256(Path(launcher.__file__).read_bytes()).hexdigest().upper()
    attempt = launcher._initialize_attempt(
        workspace,
        attempt_id="start-record-attempt",
        git_identity=identity,
        source_sha256=source_hash,
        python_version=sys.version,
        subprocess_started=True,
    )
    record = json.loads((attempt.evidence_dir / "launcher-start.json").read_text(encoding="utf-8"))
    assert record == attempt.start_record
    assert record["repository_commit"] == expected_head
    assert record["repository_branch"] == launcher.EXPECTED_BRANCH
    assert record["launcher_source_sha256"] == source_hash
    assert record["python_version"] == sys.version
    assert record["initial_stage"] == "launcher_start"
    assert "REPOSITORY_HEAD" not in Path(launcher.__file__).read_text(encoding="utf-8")
    assert SECRET_SENTINEL not in json.dumps(record)


def test_failure_record_write_error_returns_capture_failed_without_false_success(tmp_path, monkeypatch):
    attempt = _attempt(tmp_path)
    original_write_json = launcher._write_json

    def fail_failure_record(path, payload):
        if path.name == "launcher-failure.json":
            raise OSError(SECRET_SENTINEL)
        return original_write_json(path, payload)

    monkeypatch.setattr(launcher, "_write_json", fail_failure_record)
    exit_code, result = launcher._execute_preflight(
        attempt,
        [("requirements_integrity", lambda: (_ for _ in ()).throw(launcher.E2EBlocked("synthetic_gate_failure")))],
    )
    assert exit_code != 0
    assert result == {
        "classification": "LAUNCHER_PREFLIGHT_BLOCKED",
        "failure_code": "EVIDENCE_CAPTURE_FAILED",
        "exception_class": "OSError",
        "durable_evidence": False,
    }
    assert not (attempt.evidence_dir / "launcher-failure.json").exists()
    assert attempt.managed_child_pids == []
    assert attempt.active_managed_child_pids == []
    retained = b"".join(path.read_bytes() for path in attempt.evidence_dir.iterdir() if path.is_file())
    assert SECRET_SENTINEL.encode() not in retained


def test_direct_script_search_path_without_repository_root_cannot_find_backend():
    scripts_only = [str(launcher.ROOT / "scripts")]
    assert PathFinder.find_spec("backend", scripts_only) is None


def test_repository_root_path_correction_resolves_configuration_imports_without_pythonpath(monkeypatch):
    monkeypatch.delenv("PYTHONPATH", raising=False)
    non_root_entries = [
        entry for entry in sys.path
        if not launcher._same_path(entry, launcher.ROOT)
        and not launcher._same_path(entry, launcher.ROOT / "scripts")
    ]
    monkeypatch.setattr(sys, "path", [str(launcher.ROOT / "scripts"), *non_root_entries])

    launcher._ensure_repository_root_on_sys_path()

    assert Path(sys.path[0]).resolve() == launcher.ROOT.resolve()
    assert sum(launcher._same_path(entry, launcher.ROOT) for entry in sys.path) == 1
    assert importlib.import_module("backend.config").ResolvedRunConfig
    assert importlib.import_module("backend.engine.manifest").build_document_manifest
    assert importlib.import_module("backend.experiments.run_plan").build_run_plan
    assert importlib.import_module("notebooks.config").Config


def test_repository_root_path_correction_removes_equivalent_duplicates(monkeypatch):
    root = launcher.ROOT.resolve()
    monkeypatch.setattr(
        sys,
        "path",
        [str(root / "scripts"), str(root), str(root / "."), str(root).upper(), "external-entry"],
    )

    launcher._ensure_repository_root_on_sys_path(root)

    assert sys.path[0] == str(root)
    assert sum(launcher._same_path(entry, root) for entry in sys.path) == 1
    assert "external-entry" in sys.path


@pytest.mark.parametrize(
    ("module_name", "expected"),
    [
        ("backend.config", "backend.config"),
        (f"bad;{SECRET_SENTINEL}", "module_identifier_redacted"),
        (None, "module_identifier_redacted"),
    ],
)
def test_missing_module_identifier_is_safely_recorded_without_message_or_traceback(
    tmp_path, module_name, expected
):
    attempt = _attempt(tmp_path)

    def fail_import():
        raise ModuleNotFoundError(f"sensitive import text {SECRET_SENTINEL}", name=module_name)

    _run_one_failure(attempt, "python_imports", fail_import)
    failure = _assert_sanitized_failure(attempt, "python_imports", "preflight_unexpected")

    assert failure["exception_class"] == "ModuleNotFoundError"
    assert failure["missing_module"] == expected
    serialized = b"".join(path.read_bytes() for path in attempt.evidence_dir.iterdir() if path.is_file())
    assert SECRET_SENTINEL.encode() not in serialized
    assert b"sensitive import text" not in serialized
    assert b"backend.config" in serialized if expected == "backend.config" else b"backend.config" not in serialized


def _configure_synthetic_preflight_run(tmp_path, monkeypatch, steps):
    attempt = _attempt(tmp_path)
    local_app_data = tmp_path / "local-app-data"
    local_app_data.mkdir()
    (local_app_data / "Temp").mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setattr(launcher, "_create_attempt", lambda _base: attempt)
    monkeypatch.setattr(launcher, "_build_preflight_steps", lambda _attempt, _prior: steps(attempt))
    return attempt


def test_preflight_only_success_writes_distinct_summary_without_spawning(tmp_path, monkeypatch):
    def steps(attempt):
        def discovered_edge():
            attempt.values["edge"] = {
                "status": "found",
                "path": "synthetic-edge-path",
                "file_version": "156.0.1.2",
                "discovery_source": "program_files_x86",
            }

        def fixtures():
            attempt.fixtures_created = True
            attempt.values.update({key: object() for key in ("config", "manifest", "plan", "ordinary_plan")})
            attempt.values["test_command"] = ["node", "npm-cli.js", "run", "test:e2e"]

        return [("edge_discovery", discovered_edge), ("synthetic_fixture_creation", fixtures)]

    attempt = _configure_synthetic_preflight_run(tmp_path, monkeypatch, steps)
    monkeypatch.setattr(launcher, "_spawn", lambda *args, **kwargs: pytest.fail("managed process spawned"))
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("child process spawned"))

    exit_code, result = launcher._run(preflight_only=True)

    assert exit_code == 0
    assert result["classification"] == "PREFLIGHT_READY_NO_BROWSER"
    assert result["fixtures_created"] is True
    assert result["synthetic_configuration_prepared"] is True
    assert result["edge_discovery"]["status"] == "found"
    assert result["managed_process_state"] == {"started_count": 0, "active_count": 0}
    assert result["browser_launch_attempted"] is False
    assert result["service_startup_attempted"] is False
    assert result["http_requests"] == 0
    assert json.loads((attempt.evidence_dir / "launcher-summary.json").read_text(encoding="utf-8")) == result


def test_preflight_only_failure_keeps_stage_and_code_without_spawning(tmp_path, monkeypatch):
    def steps(_attempt):
        def fail():
            raise launcher.E2EBlocked("synthetic_edge_block")

        return [("edge_discovery", fail)]

    attempt = _configure_synthetic_preflight_run(tmp_path, monkeypatch, steps)
    monkeypatch.setattr(launcher, "_spawn", lambda *args, **kwargs: pytest.fail("managed process spawned"))
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("child process spawned"))

    exit_code, result = launcher._run(preflight_only=True)

    assert exit_code != 0
    assert result["classification"] == "LAUNCHER_PREFLIGHT_BLOCKED"
    assert result["failure_code"] == "synthetic_edge_block"
    assert result["last_attempted_stage"] == "edge_discovery"
    assert result["durable_evidence"] is True
    failure = json.loads((attempt.evidence_dir / "launcher-failure.json").read_text(encoding="utf-8"))
    assert failure["failure_code"] == "synthetic_edge_block"
    assert failure["last_attempted_stage"] == "edge_discovery"


@pytest.mark.parametrize("argv, expected_preflight_only", [([], False), (["--preflight-only"], True)])
def test_main_routes_preflight_option_without_changing_default_execution_mode(
    monkeypatch, capsys, argv, expected_preflight_only
):
    calls = []

    def run(prior_task_disk_bytes, *, preflight_only):
        calls.append((prior_task_disk_bytes, preflight_only))
        return 0, {"classification": "PREFLIGHT_READY_NO_BROWSER" if preflight_only else "synthetic_normal_path"}

    monkeypatch.setattr(launcher, "_run", run)

    assert launcher.main(argv) == 0
    assert calls == [(0, expected_preflight_only)]
    assert json.loads(capsys.readouterr().out)["classification"] == (
        "PREFLIGHT_READY_NO_BROWSER" if expected_preflight_only else "synthetic_normal_path"
    )
