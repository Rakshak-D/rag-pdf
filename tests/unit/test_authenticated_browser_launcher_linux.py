from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
from types import SimpleNamespace

import psutil
import pytest

from scripts import authenticated_browser_e2e_linux as linux


PORT = 54321
OWNER_PID = 30
ROOT = linux.ProcessIdentity(10, 1, 10, 100, "S")
MIDDLE = linux.ProcessIdentity(20, 10, 10, 200, "S")
OWNER = linux.ProcessIdentity(OWNER_PID, 20, 10, 300, "S")
HEADER = "  sl  local_address rem_address   st tx_queue:rx_queue tr:tm->when retrnsmt   uid  timeout inode"


def _proc_row(address: str = "0100007F", port: int = PORT, state: str = "0A", inode: int = 123) -> str:
    return f"  0: {address}:{port:04X} 00000000:0000 {state} 00000000:00000000 00:00000000 00000000 1000 0 {inode}"


def _proc_root(tmp_path: Path, tcp_rows: tuple[str, ...] = (), tcp6_rows: tuple[str, ...] = ()) -> Path:
    root = tmp_path / "proc"
    (root / "net").mkdir(parents=True)
    (root / "net" / "tcp").write_text("\n".join((HEADER, *tcp_rows)) + "\n", encoding="ascii")
    (root / "net" / "tcp6").write_text("\n".join((HEADER, *tcp6_rows)) + "\n", encoding="ascii")
    return root


def _ps_row(
    address: str = "127.0.0.1",
    port: int = PORT,
    pid: int | None = OWNER_PID,
    state: str = psutil.CONN_LISTEN,
    family: int = socket.AF_INET,
    socket_type: int = socket.SOCK_STREAM,
) -> SimpleNamespace:
    return SimpleNamespace(
        family=family,
        type=socket_type,
        laddr=(address, port),
        status=state,
        pid=pid,
    )


def _verifier(
    proc_root: Path,
    connections: list[object] | None = None,
    *,
    identities: dict[int, linux.ProcessIdentity] | None = None,
    inode_targets: tuple[str, ...] = ("socket:[123]",),
    provider=None,
) -> linux.LinuxListenerVerifier:
    rows = connections if connections is not None else [_ps_row()]
    mapping = identities or {10: ROOT, 20: MIDDLE, OWNER_PID: OWNER}
    calls = {"count": 0}

    def query(*, kind: str):
        calls["count"] += 1
        assert kind == "tcp"
        if provider:
            return provider()
        return rows

    verifier = linux.LinuxListenerVerifier(
        proc_root,
        connection_provider=query,
        fd_targets=lambda pid: inode_targets if pid == OWNER_PID else (),
        identity_reader=lambda pid: mapping.get(pid),
    )
    verifier.query_calls = calls
    return verifier


def _valid_snapshot(tmp_path: Path) -> tuple[linux.LinuxListenerVerifier, linux.ListenerSnapshot]:
    proc_root = _proc_root(tmp_path, (_proc_row(),))
    verifier = _verifier(proc_root)
    return verifier, verifier.snapshot(PORT)


def _stat_line(identity: linux.ProcessIdentity, command: str = "test (worker)") -> str:
    tail = [
        "S",
        str(identity.parent_pid),
        "0",
        str(identity.session_id),
        *("0" for _ in range(15)),
        str(identity.start_ticks),
    ]
    return f"{identity.pid} ({command}) {' '.join(tail)}\n"


def _valid_startup_status(path: Path, *, include_health: bool = False) -> linux.StartupStatusRecorder:
    recorder = linux.StartupStatusRecorder(path, "ordinary", require_fresh=True)
    for stage in (
        "guard_initialization", "guard_dependency_import", "guard_installation",
        "runtime_module_import", "runtime_entrypoint",
    ):
        recorder.append(stage, "started")
        recorder.append(stage, "completed")
    recorder.append("server_startup", "started")
    if include_health:
        recorder.append("health_accepted", "health_accepted", reached_http_readiness=True)
    return recorder


def test_startup_status_records_successful_stages_and_http_acceptance(tmp_path: Path) -> None:
    status_path = tmp_path / "ordinary.jsonl"
    _valid_startup_status(status_path, include_health=True)
    records = linux.load_startup_status(status_path, "ordinary")
    assert records[0]["stage"] == "guard_initialization"
    assert records[-1]["event"] == "health_accepted"
    assert records[-1]["reached_http_readiness"] is True
    if os.name == "posix":
        assert status_path.stat().st_mode & 0o077 == 0


def test_startup_status_classifies_missing_module_without_exception_message() -> None:
    try:
        raise ModuleNotFoundError("missing /home/runner/private/token-value", name="chroma.adapters")
    except ModuleNotFoundError as exc:
        classified = linux.safe_exception_metadata(exc)
    assert classified == {
        "exception_class": "ModuleNotFoundError",
        "missing_module": "chroma.adapters",
        "application_error_code": None,
    }


def test_startup_status_classifies_import_error_identifier() -> None:
    exc = ImportError("private path /tmp/secret-value", name="uvicorn.config")
    assert linux.safe_exception_metadata(exc)["missing_module"] == "uvicorn.config"
    assert linux.safe_exception_metadata(exc)["exception_class"] == "ImportError"


def test_startup_status_preserves_stable_runtime_configuration_code() -> None:
    class RuntimeConfigurationError(Exception):
        error_code = "runtime_composition_failed"

    try:
        try:
            raise ModuleNotFoundError("token /home/user/private", name="expected_adapter")
        except ModuleNotFoundError as cause:
            raise RuntimeConfigurationError("private setting /secret") from cause
    except RuntimeConfigurationError as exc:
        classified = linux.safe_exception_metadata(exc)
    assert classified == {
        "exception_class": "ModuleNotFoundError",
        "missing_module": "expected_adapter",
        "application_error_code": "runtime_composition_failed",
    }


def test_startup_status_prefers_specific_root_cause_code() -> None:
    class IndexSelectionError(Exception):
        def __init__(self):
            self.code = "build_report_path_invalid"

    class RuntimeConfigurationError(Exception):
        error_code = "runtime_composition_failed"

    try:
        try:
            raise IndexSelectionError()
        except IndexSelectionError as cause:
            raise RuntimeConfigurationError("redacted") from cause
    except RuntimeConfigurationError as exc:
        classified = linux.safe_exception_metadata(exc)
    assert classified["exception_class"] == "IndexSelectionError"
    assert classified["application_error_code"] == "build_report_path_invalid"


def test_forbidden_operation_is_durably_distinguished_from_other_exit(tmp_path: Path) -> None:
    path = tmp_path / "ordinary.jsonl"
    recorder = _valid_startup_status(path)
    recorder.append("server_startup", "forbidden_operation", forbidden_operation=True, exit_code=91)
    records = linux.load_startup_status(path, "ordinary")
    assert records[-1]["forbidden_operation"] is True
    assert records[-1]["exit_code"] == 91


def test_unexpected_exception_keeps_only_safe_class_and_identifiers(tmp_path: Path) -> None:
    secret = "bearer-token-should-never-appear"
    private_path = "C:/Users/private/person/data.sqlite"
    try:
        raise OSError(f"{secret} {private_path}")
    except OSError as exc:
        classified = linux.safe_exception_metadata(exc)
    path = tmp_path / "ordinary.jsonl"
    recorder = _valid_startup_status(path)
    recorder.append("server_startup", "failed", **classified, exit_code=1)
    content = path.read_text(encoding="utf-8")
    assert "OSError" in content
    assert secret not in content and private_path not in content


@pytest.mark.parametrize(
    ("returncode", "expected"),
    [(2, {"exit_code": 2, "signal_number": None}), (-9, {"exit_code": -9, "signal_number": 9})],
)
def test_process_exit_capture_includes_exit_code_and_signal(returncode, expected) -> None:
    managed = linux.ManagedProcess("ordinary", SimpleNamespace(poll=lambda: returncode), ROOT)
    assert linux.managed_process_exit(managed) == expected


def test_missing_startup_status_is_reported_without_fabricating_health(tmp_path: Path) -> None:
    with pytest.raises(linux.E2EBlocked, match="startup_status_missing"):
        linux.load_startup_status(tmp_path / "ordinary.jsonl", "ordinary")


@pytest.mark.parametrize(
    "payload",
    [b"{not-json}\n", b'{"schema_version":1}\n', b"{\n", b'{"stage":"x","stage":"y"}\n'],
)
def test_malformed_or_partial_startup_status_is_rejected(tmp_path: Path, payload: bytes) -> None:
    path = tmp_path / "ordinary.jsonl"
    path.write_bytes(payload)
    with pytest.raises(linux.E2EBlocked, match="startup_status_(malformed|schema_invalid|identity_invalid)"):
        linux.load_startup_status(path, "ordinary")


def test_duplicate_or_conflicting_startup_records_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "ordinary.jsonl"
    recorder = linux.StartupStatusRecorder(path, "ordinary", require_fresh=True)
    recorder.append("guard_initialization", "started")
    with pytest.raises(linux.E2EBlocked, match="startup_status_duplicate_or_conflicting_record"):
        recorder.append("guard_initialization", "started")


def test_startup_status_write_failure_is_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    recorder = linux.StartupStatusRecorder(tmp_path / "ordinary.jsonl", "ordinary", require_fresh=True)
    monkeypatch.setattr(linux.os, "open", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("secret")))
    with pytest.raises(linux.E2EBlocked, match="startup_status_write_failed"):
        recorder.append("guard_initialization", "started")


def test_status_schema_rejects_unexpected_keys_and_unsafe_identifiers(tmp_path: Path) -> None:
    path = tmp_path / "ordinary.jsonl"
    _valid_startup_status(path)
    rows = linux.load_startup_status(path, "ordinary")
    rows[0]["token"] = "must not be accepted"
    with pytest.raises(linux.E2EBlocked, match="startup_status_schema_invalid"):
        linux.validate_startup_records(rows, "ordinary")
    rows = linux.load_startup_status(path, "ordinary")
    rows[-1]["exception_class"] = "RuntimeError: /private/path"
    with pytest.raises(linux.E2EBlocked, match="startup_status_identifier_invalid"):
        linux.validate_startup_records(rows, "ordinary")


def test_health_marker_cannot_be_written_before_startup_gates(tmp_path: Path) -> None:
    path = tmp_path / "ordinary.jsonl"
    recorder = linux.StartupStatusRecorder(path, "ordinary", require_fresh=True)
    recorder.append("guard_initialization", "started")
    with pytest.raises(linux.E2EBlocked, match="startup_status_health_marker_invalid"):
        recorder.append("health_accepted", "health_accepted", reached_http_readiness=True)


def test_guarded_service_diagnostic_source_compiles_and_keeps_forbidden_guard(tmp_path: Path) -> None:
    generated = tmp_path / "guarded_service.py"
    linux.write_guarded_service(generated)
    source = generated.read_text(encoding="utf-8")
    ast.parse(source)
    assert 'recorder.append(current_stage, "forbidden_operation"' in source
    assert "os._exit(91)" in source
    assert "contextlib.redirect_stderr(diagnostics)" in source
    assert "traceback" not in source.lower()


def test_synthetic_report_uses_the_existing_selection_build_id_contract(tmp_path: Path) -> None:
    from backend.services.index_selection import IndexSelectionService

    paths = linux.create_workspace_layout(tmp_path)
    fixture = linux.build_synthetic_fixture(paths)
    report = json.loads(fixture["report_path"].read_text(encoding="utf-8"))
    assert report["build_id"] == linux.SYNTHETIC_BROWSER_BUILD_ID
    assert fixture["report_path"].parent.name == report["build_id"]
    selection = IndexSelectionService(
        paths["ordinary_source"], paths["ordinary_index"],
        paths["ordinary_state"] / "active-index-selection.json",
        legacy_report_path=fixture["report_path"],
    )
    assert selection is not None


def test_startup_status_path_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.jsonl"
    target.write_text("", encoding="utf-8")
    link = tmp_path / "ordinary.jsonl"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable for this test account")
    with pytest.raises(linux.E2EBlocked, match="startup_status_path_invalid"):
        linux.StartupStatusRecorder(link, "ordinary")


def test_failed_startup_diagnostic_retains_exit_and_cleanup_remains_owned(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class FakePopen:
        returncode = 2

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

    status_path = tmp_path / "ordinary.jsonl"
    recorder = linux.StartupStatusRecorder(status_path, "ordinary", require_fresh=True)
    recorder.append("guard_initialization", "started")
    recorder.append("guard_initialization", "completed")
    recorder.append("guard_dependency_import", "started")
    recorder.append(
        "guard_dependency_import", "failed", exception_class="ModuleNotFoundError",
        missing_module="synthetic_dependency", exit_code=1,
    )
    managed = linux.ManagedProcess("ordinary", FakePopen(), ROOT, PORT, status_path)
    details = linux.startup_exit_details(managed)
    assert details["process_exit"] == {"exit_code": 2, "signal_number": None}
    assert details["status_records"][-1]["missing_module"] == "synthetic_dependency"

    table_calls = {"count": 0}
    tree_calls = {"count": 0}
    def fake_table(_root):
        table_calls["count"] += 1
        return {ROOT.pid: ROOT} if table_calls["count"] == 1 else {}
    def fake_tree(*_args, **_kwargs):
        tree_calls["count"] += 1
        return (ROOT,) if tree_calls["count"] == 1 else ()
    monkeypatch.setattr(linux, "process_table", fake_table)
    monkeypatch.setattr(linux, "task_process_tree", fake_tree)
    signalled = []
    monkeypatch.setattr(linux, "signal_verified_identity", lambda identity, sig, _root: signalled.append(identity.pid) or True)
    monkeypatch.setattr(linux.time, "sleep", lambda _duration: None)
    cleaned = linux.stop_owned_process_tree(managed, tmp_path)
    assert cleaned["root_exited"] is True
    assert signalled == [ROOT.pid]


def test_startup_failure_classification_requires_safe_recorded_cause() -> None:
    assert linux.startup_cause_identified({
        "status_available": True,
        "failure_record": {
            "event": "failed", "exception_class": "ModuleNotFoundError",
            "missing_module": "required_dependency", "application_error_code": None,
        },
    }) is True
    assert linux.startup_cause_identified({
        "status_available": True,
        "failure_record": {"event": "failed", "exception_class": None, "missing_module": None, "application_error_code": None},
    }) is False
    assert linux.startup_cause_identified({
        "status_available": False,
        "failure_record": {"event": "failed", "exception_class": "RuntimeError"},
    }) is False
    assert linux.startup_cause_identified({
        "status_available": True,
        "reached_http_readiness": True,
        "failure_record": {"event": "failed", "exception_class": "RuntimeError"},
    }) is False


def test_proc_stat_captures_creation_parent_and_session_identity() -> None:
    parsed = linux.parse_proc_stat(OWNER.pid, _stat_line(OWNER))
    assert parsed == OWNER


def test_snapshot_queries_psutil_once_and_accepts_verified_indirect_descendant(tmp_path: Path) -> None:
    verifier, snapshot = _valid_snapshot(tmp_path)
    owned = verifier.verify_owned_listener(snapshot, ROOT)
    assert verifier.query_calls["count"] == 1
    assert (owned.address, owned.port, owned.pid, owned.inode) == ("127.0.0.1", PORT, OWNER_PID, 123)
    assert [identity.pid for identity in owned.ancestry] == [10, 20, 30]
    assert owned.sanitized()["ancestry_creation_identities_verified"] is True


def test_zero_listener_requires_complete_matching_successful_snapshots(tmp_path: Path) -> None:
    proc_root = _proc_root(tmp_path)
    verifier = _verifier(proc_root, [])
    snapshot = verifier.snapshot(PORT)
    verifier.assert_zero_listeners(snapshot)
    assert snapshot.sanitized()["proc_listener_count"] == 0
    assert snapshot.sanitized()["psutil_listener_count"] == 0


def test_duplicate_proc_and_psutil_listener_rows_are_rejected(tmp_path: Path) -> None:
    proc_root = _proc_root(tmp_path, (_proc_row(), _proc_row(inode=124)))
    verifier = _verifier(proc_root, [_ps_row(), _ps_row()])
    snapshot = verifier.snapshot(PORT)
    with pytest.raises(linux.E2EBlocked, match="listener_count_invalid"):
        verifier.verify_owned_listener(snapshot, ROOT)


def test_selected_port_mismatch_cannot_be_accepted(tmp_path: Path) -> None:
    proc_root = _proc_root(tmp_path, (_proc_row(port=PORT + 1),))
    verifier = _verifier(proc_root, [_ps_row(port=PORT + 1)])
    with pytest.raises(linux.E2EBlocked, match="listener_count_invalid"):
        verifier.verify_owned_listener(verifier.snapshot(PORT), ROOT)


def test_ipv4_wildcard_is_not_accepted(tmp_path: Path) -> None:
    proc_root = _proc_root(tmp_path, (_proc_row(address="00000000"),))
    verifier = _verifier(proc_root, [_ps_row("0.0.0.0")])
    with pytest.raises(linux.E2EBlocked, match="listener_loopback_required"):
        verifier.verify_owned_listener(verifier.snapshot(PORT), ROOT)


def test_ipv6_listener_is_not_an_ipv4_loopback_substitute(tmp_path: Path) -> None:
    v6_port = f"{PORT:04X}"
    row = f"  0: {'0' * 32}:{v6_port} {'0' * 32}:0000 0A 00000000:00000000 00:00000000 00000000 1000 0 124"
    proc_root = _proc_root(tmp_path, tcp6_rows=(row,))
    verifier = _verifier(
        proc_root,
        [_ps_row("::", family=socket.AF_INET6)],
    )
    with pytest.raises(linux.E2EBlocked, match="listener_ipv4_required"):
        verifier.verify_owned_listener(verifier.snapshot(PORT), ROOT)


def test_conflicting_ipv4_and_ipv6_listeners_fail_exact_count(tmp_path: Path) -> None:
    v6_port = f"{PORT:04X}"
    row = f"  0: {'0' * 32}:{v6_port} {'0' * 32}:0000 0A 00000000:00000000 00:00000000 00000000 1000 0 124"
    proc_root = _proc_root(tmp_path, (_proc_row(),), (row,))
    verifier = _verifier(
        proc_root,
        [_ps_row(), _ps_row("::", family=socket.AF_INET6, pid=OWNER_PID)],
    )
    with pytest.raises(linux.E2EBlocked, match="listener_count_invalid"):
        verifier.verify_owned_listener(verifier.snapshot(PORT), ROOT)


def test_non_listening_state_is_not_counted_as_a_listener(tmp_path: Path) -> None:
    proc_root = _proc_root(tmp_path, (_proc_row(state="01"),))
    verifier = _verifier(proc_root, [_ps_row(state=psutil.CONN_ESTABLISHED)])
    snapshot = verifier.snapshot(PORT)
    verifier.assert_zero_listeners(snapshot)
    with pytest.raises(linux.E2EBlocked, match="listener_count_invalid"):
        verifier.verify_owned_listener(snapshot, ROOT)


def test_unknown_proc_tcp_state_fails_closed(tmp_path: Path) -> None:
    proc_root = _proc_root(tmp_path, (_proc_row(state="0D"),))
    with pytest.raises(linux.E2EBlocked, match="proc_tcp_state_unknown"):
        _verifier(proc_root).snapshot(PORT)


def test_unknown_psutil_state_fails_closed(tmp_path: Path) -> None:
    proc_root = _proc_root(tmp_path)
    verifier = _verifier(proc_root, [_ps_row(state="NONE")])
    with pytest.raises(linux.E2EBlocked, match="psutil_tcp_state_unknown"):
        verifier.snapshot(PORT)


def test_listener_without_pid_is_rejected(tmp_path: Path) -> None:
    proc_root = _proc_root(tmp_path, (_proc_row(),))
    with pytest.raises(linux.E2EBlocked, match="psutil_listener_pid_missing"):
        _verifier(proc_root, [_ps_row(pid=None)]).snapshot(PORT)


def test_foreign_pid_or_missing_socket_inode_ownership_is_rejected(tmp_path: Path) -> None:
    proc_root = _proc_root(tmp_path, (_proc_row(),))
    identities = {
        10: ROOT,
        20: MIDDLE,
        OWNER_PID: linux.ProcessIdentity(OWNER_PID, 1, 99, 300, "S"),
    }
    verifier = _verifier(proc_root, identities=identities)
    with pytest.raises(linux.E2EBlocked, match="process_ancestry_session_mismatch"):
        verifier.verify_owned_listener(verifier.snapshot(PORT), ROOT)

    verifier = _verifier(proc_root, inode_targets=())
    with pytest.raises(linux.E2EBlocked, match="listener_inode_owner_mismatch"):
        verifier.verify_owned_listener(verifier.snapshot(PORT), ROOT)


def test_root_pid_reuse_or_creation_identity_change_is_rejected(tmp_path: Path) -> None:
    proc_root = _proc_root(tmp_path, (_proc_row(),))
    identities = {10: linux.ProcessIdentity(10, 1, 10, 999, "S"), 20: MIDDLE, OWNER_PID: OWNER}
    verifier = _verifier(proc_root, identities=identities)
    with pytest.raises(linux.E2EBlocked, match="process_root_identity_changed"):
        verifier.verify_owned_listener(verifier.snapshot(PORT), ROOT)


def test_descendant_tree_does_not_require_a_direct_child_and_survives_root_exit_snapshot() -> None:
    values = {item.pid: item for item in (ROOT, MIDDLE, OWNER)}
    tree = linux.task_process_tree(ROOT, values)
    assert {item.pid for item in tree} == {10, 20, 30}
    orphaned = {20: MIDDLE, 30: OWNER}
    tree_after_root_exit = linux.task_process_tree(ROOT, orphaned, allow_root_missing=True)
    assert {item.pid for item in tree_after_root_exit} == {20, 30}


def test_incomplete_proc_table_and_psutil_query_failures_are_not_empty_results(tmp_path: Path) -> None:
    root = tmp_path / "proc"
    (root / "net").mkdir(parents=True)
    (root / "net" / "tcp").write_text(HEADER, encoding="ascii")
    verifier = _verifier(root, [])
    with pytest.raises(linux.E2EBlocked, match="proc_tcp_query_failed"):
        verifier.snapshot(PORT)

    root = _proc_root(tmp_path / "other")
    failed = _verifier(root, provider=lambda: (_ for _ in ()).throw(PermissionError("denied")))
    with pytest.raises(linux.E2EBlocked, match="psutil_tcp_query_failed"):
        failed.snapshot(PORT)


def test_proc_table_rejects_malformed_rows_and_invalid_address_or_port() -> None:
    with pytest.raises(linux.E2EBlocked, match="proc_tcp_row_invalid"):
        linux.parse_proc_tcp_table(f"{HEADER}\nnot-a-row\n", socket.AF_INET, PORT)
    with pytest.raises(linux.E2EBlocked, match="proc_tcp_row_invalid"):
        linux.parse_proc_tcp_table(f"{HEADER}\n{_proc_row(address='GG00007F')}\n", socket.AF_INET, PORT)
    with pytest.raises(linux.E2EBlocked, match="listener_port_invalid"):
        linux.parse_proc_tcp_table(HEADER, socket.AF_INET, 0)


def test_psutil_malformed_local_endpoint_and_wrong_protocol_fail_closed() -> None:
    bad_address = _ps_row()
    bad_address.laddr = ("not-an-ip", PORT)
    with pytest.raises(linux.E2EBlocked, match="psutil_local_address_invalid"):
        linux.parse_psutil_tcp_rows([bad_address], PORT)
    with pytest.raises(linux.E2EBlocked, match="psutil_connection_protocol_invalid"):
        linux.parse_psutil_tcp_rows([_ps_row(socket_type=socket.SOCK_DGRAM)], PORT)


def test_pidfd_cleanup_checks_creation_identity_before_signalling(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[int, int]] = []
    closed: list[int] = []
    monkeypatch.setattr(linux.os, "pidfd_open", lambda pid, flags: 55, raising=False)
    monkeypatch.setattr(linux.signal, "pidfd_send_signal", lambda fd, sig: sent.append((fd, sig)), raising=False)
    monkeypatch.setattr(linux.os, "close", lambda fd: closed.append(fd))
    monkeypatch.setattr(linux, "read_process_identity", lambda pid, root: OWNER)
    assert linux.signal_verified_identity(OWNER, signal.SIGTERM, Path("/unused"))
    assert sent == [(55, signal.SIGTERM)]
    assert closed == [55]

    sent.clear()
    monkeypatch.setattr(linux, "read_process_identity", lambda pid, root: linux.ProcessIdentity(30, 20, 10, 999, "S"))
    with pytest.raises(linux.E2EBlocked, match="process_cleanup_identity_mismatch"):
        linux.signal_verified_identity(OWNER, signal.SIGTERM, Path("/unused"))
    assert sent == []


def test_loopback_port_selection_detects_collisions() -> None:
    class FakeSocket:
        def __init__(self, port: int):
            self.port = port
            self.closed = False

        def bind(self, endpoint):
            assert endpoint == ("127.0.0.1", 0)

        def getsockname(self):
            return ("127.0.0.1", self.port)

        def close(self):
            self.closed = True

    sockets = iter((FakeSocket(PORT), FakeSocket(PORT)))
    with pytest.raises(linux.E2EBlocked, match="loopback_port_collision"):
        linux.select_loopback_ports(2, binder=lambda: next(sockets))


def test_workspace_roots_are_temporary_disjoint_and_contained(tmp_path: Path) -> None:
    workspace = tmp_path / "owned"
    workspace.mkdir()
    paths = linux.create_workspace_layout(workspace)
    assert paths["ordinary_source"].is_relative_to(workspace)
    assert paths["bootstrap_source"].is_relative_to(workspace)
    assert paths["ordinary_source"] != paths["bootstrap_source"]
    assert paths["ordinary_index"] != paths["bootstrap_index"]


def test_real_synthetic_pdf_manifest_and_runplan_fixture_uses_isolated_roots(tmp_path: Path) -> None:
    workspace = tmp_path / "fixture-workspace"
    workspace.mkdir()
    paths = linux.create_workspace_layout(workspace)
    fixture = linux.build_synthetic_fixture(paths)
    assert fixture["document_count"] == 1
    assert fixture["pdf_page_count"] == 1
    assert len(fixture["manifest_hash"]) == 64
    assert len(fixture["index_fingerprint"]) == 64
    assert (paths["ordinary_source"] / "synthetic.pdf").is_file()
    assert (paths["bootstrap_source"] / "synthetic.pdf").is_file()
    assert not any(paths["bootstrap_index"].iterdir())


def test_tokens_are_distinct_and_secret_scan_detects_leakage() -> None:
    tokens = linux.generate_test_tokens()
    assert len(set(tokens)) == 3
    assert not linux.contains_secret(b"sanitized result", tokens)
    assert linux.contains_secret(f"Authorization: Bearer {tokens[0]}", tokens)


def test_child_environment_drops_inherited_proxy_and_credential_values(tmp_path: Path) -> None:
    clean = linux.clean_child_environment(
        {
            "PATH": "runner-path",
            "HTTPS_PROXY": "proxy-value",
            "GH_TOKEN": "github-token",
            "AWS_SECRET_ACCESS_KEY": "cloud-secret",
            "TOKEN": "generic-token",
            "RAG_WORKBENCH_API_TOKEN": "old-app-token",
            "E2E_ORDINARY_TOKEN": "old-test-token",
        },
        tmp_path,
    )
    assert clean["PATH"] == "runner-path"
    assert "HTTPS_PROXY" not in clean
    assert "GH_TOKEN" not in clean
    assert "AWS_SECRET_ACCESS_KEY" not in clean
    assert "TOKEN" not in clean
    assert "RAG_WORKBENCH_API_TOKEN" not in clean
    assert "E2E_ORDINARY_TOKEN" not in clean


def test_health_request_uses_direct_proxy_free_loopback_opener(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return b'{"status":"ok"}'

    class FakeOpener:
        def open(self, url, timeout):
            assert url == f"http://127.0.0.1:{PORT}/health"
            assert timeout == 1.5
            return FakeResponse()

    def build_direct_opener(handler):
        assert isinstance(handler, linux.ProxyHandler)
        assert handler.proxies == {}
        return FakeOpener()

    monkeypatch.setattr(linux, "build_opener", build_direct_opener)
    assert linux.http_health(PORT) == (200, {"status": "ok"})


def test_evidence_bundle_is_bounded_hashed_and_rejects_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fsync_calls: list[int] = []
    monkeypatch.setattr(linux.os, "fsync", lambda fd: fsync_calls.append(fd))
    bundle = tmp_path / "evidence"
    manifest_hash = linux.write_evidence_bundle(
        bundle,
        {"classification": "LINUX_BROWSER_E2E_VERIFIED"},
        {"playwright-results.json": b'{"status":"passed"}\n'},
        ("secret-value",),
    )
    manifest_bytes = (bundle / "evidence-manifest.json").read_bytes()
    assert hashlib.sha256(manifest_bytes).hexdigest() == manifest_hash
    assert len(fsync_calls) == 3
    manifest = json.loads(manifest_bytes)
    assert {row["path"] for row in manifest["files"]} == {
        "runner-evidence.json",
        "playwright-results.json",
    }

    with pytest.raises(linux.E2EBlocked, match="evidence_secret_detected"):
        linux.write_evidence_bundle(
            tmp_path / "unsafe",
            {"classification": "failed"},
            {"result.json": b"secret-value"},
            ("secret-value",),
        )


def test_browser_result_requires_exact_three_guard_clean_scenarios() -> None:
    titles = [
        "bootstrap authentication and preview stay isolated and ephemeral",
        "build report inventory is read-only and empty",
        "ordinary workbench authentication and catalogs use only ordinary API token",
    ]
    result = {
        "status": "passed",
        "expected_tests": 3,
        "completed_tests": 3,
        "tests": [{"title": title, "status": "passed", "retry": 0} for title in titles],
    }
    observations = [{"status": "passed", "guard_violations": []} for _ in titles]
    linux.assert_browser_result(result, observations)
    observations[1]["guard_violations"] = ["token_in_url"]
    with pytest.raises(linux.E2EBlocked, match="browser_network_or_auth_guard_failed"):
        linux.assert_browser_result(result, observations)


def test_failure_classification_separates_behavior_from_security_and_setup() -> None:
    assert linux.classify_failure(
        "playwright_result_validation", "playwright_scenario_failed", cleanup_complete=True
    ) == "LINUX_BROWSER_BEHAVIOR_FAILED"
    assert linux.classify_failure(
        "playwright_result_validation", "browser_network_or_auth_guard_failed", cleanup_complete=True
    ) == "INTEGRITY_OR_CLEANUP_BLOCKED"
    assert linux.classify_failure(
        "service_readiness", "listener_inventory_disagreement", cleanup_complete=True
    ) == "INTEGRITY_OR_CLEANUP_BLOCKED"
    assert linux.classify_failure(
        "playwright_execution", "playwright_execution_timeout", cleanup_complete=True
    ) == "LINUX_BROWSER_EXECUTION_BLOCKED"
    assert linux.classify_failure(
        "ordinary_service_start", "process_start_failed", cleanup_complete=True
    ) == "LINUX_BROWSER_EXECUTION_BLOCKED"
    assert linux.classify_failure(
        "playwright_execution", "playwright_scenario_failed", cleanup_complete=False
    ) == "INTEGRITY_OR_CLEANUP_BLOCKED"


def test_linux_runner_rejects_other_platform_before_process_work(tmp_path: Path) -> None:
    with pytest.raises(linux.E2EBlocked, match="linux_runner_required"):
        linux.validate_linux_primitives("win32", tmp_path)


def test_browser_version_validation_trims_runner_whitespace_and_rejects_unknown_output() -> None:
    assert linux.validate_browser_version("Chromium 156.0.8078.0 \n") == "Chromium 156.0.8078.0"
    with pytest.raises(linux.E2EBlocked, match="linux_chromium_version_unavailable"):
        linux.validate_browser_version("browser version unavailable")


def test_script_entrypoint_follows_all_runner_support_definitions() -> None:
    tree = ast.parse(Path(linux.__file__).read_text(encoding="utf-8"))
    entrypoints = [
        node for node in tree.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
    ]
    definitions = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    assert len(entrypoints) == 1
    assert entrypoints[0].lineno > max(node.end_lineno for node in definitions)


def test_linux_prerequisites_require_procfs_and_pidfd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(linux.os, "pidfd_open", lambda pid, flags: 1, raising=False)
    monkeypatch.setattr(linux.signal, "pidfd_send_signal", lambda fd, sig: None, raising=False)
    linux.validate_linux_primitives("linux", tmp_path)
    with pytest.raises(linux.E2EBlocked, match="procfs_unavailable"):
        linux.validate_linux_primitives("linux", tmp_path / "missing")


def test_cleanup_signals_only_the_creation_checked_owned_tree(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class FakePopen:
        def __init__(self):
            self.returncode = None

        def wait(self, timeout=None):
            self.returncode = 0
            return 0

        def poll(self):
            return self.returncode

    identities = {item.pid: item for item in (ROOT, MIDDLE, OWNER)}
    calls = {"tables": 0}

    def fake_table(_proc_root):
        calls["tables"] += 1
        return identities if calls["tables"] == 1 else {}

    signalled = []
    monkeypatch.setattr(linux, "process_table", fake_table)
    monkeypatch.setattr(
        linux,
        "signal_verified_identity",
        lambda identity, sig, _proc_root: signalled.append((identity.pid, sig)) or True,
    )
    managed = linux.ManagedProcess("test", FakePopen(), ROOT)
    evidence = linux.stop_owned_process_tree(managed, tmp_path)
    assert evidence["root_exited"] is True
    assert evidence["captured_descendant_count"] == 2
    assert {pid for pid, _sig in signalled} == {10, 20, 30}
    assert all(sig == signal.SIGTERM for _pid, sig in signalled)
