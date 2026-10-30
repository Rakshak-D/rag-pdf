from __future__ import annotations

import multiprocessing
import json
import queue
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier, Event, Thread

import pytest

from backend.services.bootstrap_jobs import BootstrapJobError, BootstrapJobManager, canonical_request_identity
from backend.engine.manifest import build_document_manifest
from backend.experiments.run_plan import build_run_plan
from backend.engine.index_builder import IndexBuildResult
from tests.integration.test_index_builder import _plan_with_text_and_optional_image


def _owner_process(database, source, storage, ready, release):
    manager = BootstrapJobManager(database, source, storage)
    manager.start()
    ready.set()
    release.wait(20)
    manager.shutdown()


def _crash_owner(database, source, storage, ready):
    import os
    manager = BootstrapJobManager(database, source, storage)
    manager.start()
    ready.set()
    os._exit(0)


def test_state_owner_is_process_exclusive_and_state_roots_are_independent(tmp_path):
    source, _, _ = _plan_with_text_and_optional_image(tmp_path)
    storage = tmp_path / "storage"
    database = tmp_path / "state" / "jobs.sqlite"
    database.parent.mkdir()
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    process = context.Process(target=_owner_process, args=(database, source, storage, ready, release))
    process.start()
    try:
        assert ready.wait(20)
        competing = BootstrapJobManager(database, source, storage)
        with pytest.raises(BootstrapJobError, match="bootstrap_instance_owned"):
            competing.start()
        (tmp_path / "other").mkdir()
        other = BootstrapJobManager(tmp_path / "other" / "jobs.sqlite", source, storage)
        other.start()
        assert other.shutdown()
    finally:
        release.set()
        process.join(20)
        if process.is_alive():
            process.terminate()
            process.join(10)
    assert process.exitcode == 0


def test_constructor_does_not_mutate_state_or_start_worker(tmp_path):
    source, _, _ = _plan_with_text_and_optional_image(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    manager = BootstrapJobManager(state / "jobs.sqlite", source, tmp_path / "storage")
    assert not (state / "jobs.sqlite").exists()
    assert manager._worker is None
    with pytest.raises(BootstrapJobError, match="manager_not_accepting"):
        manager.submit("request-1234567890", None, "a" * 64, "b" * 64)


def test_shutdown_is_idempotent_and_releases_only_owner_reservation(tmp_path):
    source, _, _ = _plan_with_text_and_optional_image(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    manager = BootstrapJobManager(state / "jobs.sqlite", source, tmp_path / "storage")
    manager.start()
    marker = state / ".bootstrap-instance-owner" / "unrelated"
    marker.write_text("preserve")
    assert not manager.shutdown()
    assert marker.read_text() == "preserve"
    assert (state / ".bootstrap-instance-owner").exists()


def test_forced_owner_termination_retains_reservation_and_blocks_restart(tmp_path):
    source, _, _ = _plan_with_text_and_optional_image(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    proc = context.Process(target=_crash_owner, args=(state / "jobs.sqlite", source, tmp_path / "storage", ready))
    proc.start()
    try:
        assert ready.wait(20)
        proc.join(20)
        assert proc.exitcode == 0
        reservation = state / ".bootstrap-instance-owner"
        assert (reservation / "owner").is_file()
        (state / "operator-note").write_text("preserve")
        retry = BootstrapJobManager(state / "jobs.sqlite", source, tmp_path / "storage")
        with pytest.raises(BootstrapJobError, match="bootstrap_instance_owned"):
            retry.start()
        assert (state / "operator-note").read_text() == "preserve"
    finally:
        if proc.is_alive():
            proc.terminate(); proc.join(10)


def test_idempotent_admission_conflict_capacity_shutdown_and_restart(tmp_path, monkeypatch):
    source, config, plan = _plan_with_text_and_optional_image(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    gate = Event()
    monkeypatch.setattr(BootstrapJobManager, "_run", lambda self: gate.wait(20))
    manager = BootstrapJobManager(state / "jobs.sqlite", source, tmp_path / "storage", capacity=1)
    manager.start()
    request_id = "client-request-0001"
    args = (request_id, config, plan.document_manifest_hash, plan.index_fingerprint)
    first = manager.submit(*args)
    assert first.status == "queued"
    assert manager.submit(*args).job_id == first.job_id
    with pytest.raises(BootstrapJobError, match="request_id_conflict"):
        changed = config.model_copy(update={"text_k": config.text_k + 1})
        changed_plan = build_run_plan(changed, plan.document_manifest_hash)
        manager.submit(request_id, changed, plan.document_manifest_hash, changed_plan.index_fingerprint)
    with pytest.raises(BootstrapJobError, match="fingerprint_already_active"):
        manager.submit("client-request-0002", config, plan.document_manifest_hash, plan.index_fingerprint)
    with pytest.raises(BootstrapJobError, match="queue_capacity_exhausted"):
        queued_config = config.model_copy(update={"chunk_max_tokens": config.chunk_max_tokens + 1})
        queued_plan = build_run_plan(queued_config, plan.document_manifest_hash)
        manager.submit("client-request-0003", queued_config, plan.document_manifest_hash, queued_plan.index_fingerprint)
    assert not manager.shutdown(timeout=0)
    with sqlite3.connect(state / "jobs.sqlite") as db:
        row = db.execute("SELECT status, error_code FROM bootstrap_jobs WHERE job_id=?", (first.job_id,)).fetchone()
        schema = [record[1] for record in db.execute("PRAGMA table_info(bootstrap_jobs)")]
    assert row == ("interrupted", "server_shutdown")
    assert "config_json" not in schema
    with pytest.raises(BootstrapJobError, match="manager_not_accepting"):
        manager.submit(*args)
    gate.set()
    assert manager.shutdown(timeout=10)
    restarted = BootstrapJobManager(state / "jobs.sqlite", source, tmp_path / "storage")
    restarted.start()
    assert restarted.get(first.job_id).status == "interrupted"
    assert restarted.shutdown()


def test_canonical_identity_uses_validated_config_and_trusted_namespaces(tmp_path):
    source, config, plan = _plan_with_text_and_optional_image(tmp_path)
    first = canonical_request_identity(config, plan.document_manifest_hash, plan.index_fingerprint, source, tmp_path / "storage")
    reordered = type(config).model_validate(dict(reversed(list(config.model_dump(mode="python").items()))))
    assert canonical_request_identity(reordered, plan.document_manifest_hash, plan.index_fingerprint, source, tmp_path / "storage") == first
    assert canonical_request_identity(config, plan.document_manifest_hash, plan.index_fingerprint, source, tmp_path / "elsewhere") != first


def test_simultaneous_identical_admission_returns_one_durable_job(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    source, config, plan = _plan_with_text_and_optional_image(tmp_path)
    state = tmp_path / "state"; state.mkdir()
    hold = Event()
    monkeypatch.setattr(BootstrapJobManager, "_run", lambda self: hold.wait(20))
    manager = BootstrapJobManager(state / "jobs.sqlite", source, tmp_path / "storage", capacity=2)
    manager.start()
    barrier = Barrier(3)
    request = ("same-request-key-0001", config, plan.document_manifest_hash, plan.index_fingerprint)
    def submit():
        barrier.wait(10)
        return manager.submit(*request)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.submit(submit), pool.submit(submit)
            barrier.wait(10)
            a, b = first.result(timeout=30), second.result(timeout=30)
        assert a.job_id == b.job_id
        with sqlite3.connect(state / "jobs.sqlite") as db:
            assert db.execute("SELECT COUNT(*) FROM bootstrap_jobs").fetchone()[0] == 1
        assert not manager.shutdown(timeout=0)
    finally:
        hold.set()
        manager.shutdown(timeout=10)


def test_database_connection_failure_does_not_ack_or_queue(tmp_path, monkeypatch):
    source, config, plan = _plan_with_text_and_optional_image(tmp_path)
    state = tmp_path / "state"; state.mkdir()
    hold = Event(); monkeypatch.setattr(BootstrapJobManager, "_run", lambda self: hold.wait(20))
    manager = BootstrapJobManager(state / "jobs.sqlite", source, tmp_path / "storage")
    manager.start(); original_connect = sqlite3.connect
    def fail_connect(*_args, **_kwargs): raise sqlite3.OperationalError("injected")
    monkeypatch.setattr(sqlite3, "connect", fail_connect)
    try:
        with pytest.raises(BootstrapJobError, match="job_store_unavailable"):
            manager.submit("connection-failure-0001", config, plan.document_manifest_hash, plan.index_fingerprint)
        assert manager._queue.empty()
    finally:
        monkeypatch.setattr(sqlite3, "connect", original_connect); hold.set(); manager.shutdown(10)
    with original_connect(state / "jobs.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM bootstrap_jobs").fetchone()[0] == 0


def test_queue_failure_compensates_to_durable_interrupted_record(tmp_path, monkeypatch):
    source, config, plan = _plan_with_text_and_optional_image(tmp_path)
    state = tmp_path / "state"; state.mkdir()
    hold = Event(); monkeypatch.setattr(BootstrapJobManager, "_run", lambda self: hold.wait(20))
    manager = BootstrapJobManager(state / "jobs.sqlite", source, tmp_path / "storage")
    manager.start()
    monkeypatch.setattr(manager._queue, "put_nowait", lambda _item: (_ for _ in ()).throw(queue.Full()))
    try:
        with pytest.raises(BootstrapJobError, match="queue_capacity_exhausted"):
            manager.submit("queue-failure-request-01", config, plan.document_manifest_hash, plan.index_fingerprint)
        assert manager._queue.empty()
        rows = manager.list()
        assert len(rows) == 1 and rows[0].status == "interrupted" and rows[0].error_code == "queue_admission_failed"
        assert manager.submit("queue-failure-request-01", config, plan.document_manifest_hash, plan.index_fingerprint).status == "interrupted"
    finally:
        hold.set(); manager.shutdown(10)


def test_commit_exception_is_not_enqueued_and_remains_discoverable(tmp_path, monkeypatch):
    source, config, plan = _plan_with_text_and_optional_image(tmp_path)
    state = tmp_path / "state"; state.mkdir()
    hold = Event(); monkeypatch.setattr(BootstrapJobManager, "_run", lambda self: hold.wait(20))
    manager = BootstrapJobManager(state / "jobs.sqlite", source, tmp_path / "storage")
    manager.start(); real_connect = manager._connect
    class CommitAfterDurable:
        def __init__(self, connection): self.connection = connection
        def __enter__(self): self.connection.__enter__(); return self
        def __exit__(self, *args): return self.connection.__exit__(*args)
        def __getattr__(self, name): return getattr(self.connection, name)
        def commit(self): self.connection.commit(); raise sqlite3.OperationalError("uncertain commit")
    calls = 0
    def injected():
        nonlocal calls
        calls += 1
        connection = real_connect()
        return CommitAfterDurable(connection) if calls == 1 else connection
    monkeypatch.setattr(manager, "_connect", injected)
    try:
        with pytest.raises(BootstrapJobError):
            manager.submit("commit-failure-request-01", config, plan.document_manifest_hash, plan.index_fingerprint)
        assert manager._queue.empty()
        rows = manager.list()
        assert len(rows) == 1 and rows[0].status == "queued"
        with pytest.raises(BootstrapJobError, match="fingerprint_already_active"):
            manager.submit("commit-failure-request-02", config, plan.document_manifest_hash, plan.index_fingerprint)
    finally:
        monkeypatch.setattr(manager, "_connect", real_connect); hold.set(); manager.shutdown(10)
    restarted = BootstrapJobManager(state / "jobs.sqlite", source, tmp_path / "storage")
    restarted.start()
    assert restarted.list()[0].status == "interrupted"
    assert restarted.shutdown()


def test_failed_queue_compensation_leaves_discoverable_fingerprint_block(tmp_path, monkeypatch):
    source, config, plan = _plan_with_text_and_optional_image(tmp_path)
    state = tmp_path / "state"; state.mkdir()
    hold = Event(); monkeypatch.setattr(BootstrapJobManager, "_run", lambda self: hold.wait(20))
    manager = BootstrapJobManager(state / "jobs.sqlite", source, tmp_path / "storage")
    manager.start(); real_connect = manager._connect
    monkeypatch.setattr(manager._queue, "put_nowait", lambda _item: (_ for _ in ()).throw(queue.Full()))
    calls = 0
    def fail_compensation():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise BootstrapJobError("job_store_unavailable")
        return real_connect()
    monkeypatch.setattr(manager, "_connect", fail_compensation)
    try:
        with pytest.raises(BootstrapJobError, match="job_store_unavailable"):
            manager.submit("compensation-failure-0001", config, plan.document_manifest_hash, plan.index_fingerprint)
        assert manager._queue.empty()
        rows = manager.list()
        assert len(rows) == 1 and rows[0].status == "queued"
        with pytest.raises(BootstrapJobError, match="fingerprint_already_active"):
            manager.submit("compensation-failure-0002", config, plan.document_manifest_hash, plan.index_fingerprint)
    finally:
        monkeypatch.setattr(manager, "_connect", real_connect)
        hold.set(); manager.shutdown(10)


def test_manager_executes_real_fresh_build_and_records_verified_result(tmp_path, monkeypatch):
    from backend.experiments.run_plan import build_run_plan
    from backend.engine.manifest import build_document_manifest
    from backend.vectorstore import ChromaVectorStore
    from tests.integration.test_index_builder import _valid_text_adapter

    source, config, _ = _plan_with_text_and_optional_image(tmp_path)
    manifest = build_document_manifest(source)
    plan = build_run_plan(config, manifest.content_hash)
    storage = tmp_path / "storage"
    state = tmp_path / "state"
    state.mkdir()
    manager = BootstrapJobManager(state / "jobs.sqlite", source, storage, adapter_factory=lambda: (_valid_text_adapter(config), None))
    import backend.services.bootstrap_jobs as module
    original_build = module.build_index
    errors = []
    def traced(*args, **kwargs):
        try:
            return original_build(*args, **kwargs)
        except Exception as exc:
            errors.append(repr(exc))
            raise
    monkeypatch.setattr(module, "build_index", traced)
    manager.start()
    try:
        accepted = manager.submit("real-build-request-0001", config, manifest.content_hash, plan.index_fingerprint)
        assert accepted.status == "queued"
        assert manager._completion[accepted.job_id].wait(60)
        result = manager.get(accepted.job_id)
        assert result.status == "succeeded", (result, errors, manager._last_worker_stage, manager._last_worker_error)
        assert result.build_id and result.report_reference == "build-report.json"
        assert result.text_records == 1 and result.partial_persistence is False
        assert not (storage / "indexes" / plan.index_fingerprint / ".fresh-build-incomplete").exists()
        from backend.engine.retrieval import _validate_build_report
        reports = list(storage.glob("build-artifacts/*/build-report.json"))
        validated = _validate_build_report(storage, reports[0], plan)
        assert validated.build_id == result.build_id and validated.text_records_persisted == result.text_records
        store = ChromaVectorStore.open_existing(storage, plan)
        try:
            assert store.count("text") == result.text_records
        finally:
            store.close()
    finally:
        assert manager.shutdown()


def _paused_manager(tmp_path, monkeypatch, *, capacity=2):
    source, config, plan = _plan_with_text_and_optional_image(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    hold = Event()
    monkeypatch.setattr(BootstrapJobManager, "_run", lambda self: hold.wait(30))
    manager = BootstrapJobManager(state / "jobs.sqlite", source, tmp_path / "storage", capacity=capacity)
    manager.start()
    return source, config, plan, state, manager, hold


def _outcome(future):
    try:
        return ("ok", future.result(timeout=30))
    except BootstrapJobError as exc:
        return ("error", exc.code)


@pytest.mark.parametrize("different_config", [False, True], ids=["same-request", "conflicting-request"])
def test_concurrent_same_request_identity_is_linearizable(tmp_path, monkeypatch, different_config):
    source, config, plan, state, manager, hold = _paused_manager(tmp_path, monkeypatch)
    barrier = Barrier(3)
    changed = config.model_copy(update={"chunk_max_tokens": config.chunk_max_tokens + 1})
    changed_plan = build_run_plan(changed, plan.document_manifest_hash)
    requests = [
        ("concurrent-request-key-0001", config, plan.document_manifest_hash, plan.index_fingerprint),
        ("concurrent-request-key-0001", changed, plan.document_manifest_hash, changed_plan.index_fingerprint)
        if different_config else
        ("concurrent-request-key-0001", config, plan.document_manifest_hash, plan.index_fingerprint),
    ]
    def invoke(args):
        barrier.wait(10)
        try:
            return ("ok", manager.submit(*args))
        except BootstrapJobError as exc:
            return ("error", exc.code)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            one = pool.submit(invoke, requests[0]); two = pool.submit(invoke, requests[1])
            barrier.wait(10)
            a, b = one.result(timeout=30), two.result(timeout=30)
        outcomes = [a, b]
        assert sum(kind == "ok" for kind, _ in outcomes) == 2 if not different_config else sum(kind == "ok" for kind, _ in outcomes) == 1
        if different_config:
            assert sorted(value for kind, value in outcomes if kind == "error") == ["request_id_conflict"]
        else:
            assert a[1].job_id == b[1].job_id
        with sqlite3.connect(state / "jobs.sqlite") as db:
            assert db.execute("SELECT COUNT(*) FROM bootstrap_jobs").fetchone()[0] == 1
        assert manager._queue.qsize() == 1
    finally:
        hold.set(); manager.shutdown(10)


def test_concurrent_distinct_ids_for_same_fingerprint_admit_one(tmp_path, monkeypatch):
    _, config, plan, state, manager, hold = _paused_manager(tmp_path, monkeypatch)
    barrier = Barrier(3)
    args = [
        ("fingerprint-request-key-0001", config, plan.document_manifest_hash, plan.index_fingerprint),
        ("fingerprint-request-key-0002", config, plan.document_manifest_hash, plan.index_fingerprint),
    ]
    def invoke(item):
        barrier.wait(10)
        try: return ("ok", manager.submit(*item))
        except BootstrapJobError as exc: return ("error", exc.code)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            one, two = [pool.submit(invoke, item) for item in args]
            barrier.wait(10)
            outcomes = [one.result(timeout=30), two.result(timeout=30)]
        assert sorted(value if kind == "error" else "accepted" for kind, value in outcomes) == ["accepted", "fingerprint_already_active"]
        with sqlite3.connect(state / "jobs.sqlite") as db:
            assert db.execute("SELECT COUNT(*) FROM bootstrap_jobs").fetchone()[0] == 1
        assert manager._queue.qsize() == 1
    finally:
        hold.set(); manager.shutdown(10)


def test_distinct_fingerprints_fit_capacity_and_duplicate_replay_works_when_full(tmp_path, monkeypatch):
    _, config, plan, state, manager, hold = _paused_manager(tmp_path, monkeypatch, capacity=2)
    changed = config.model_copy(update={"chunk_max_tokens": config.chunk_max_tokens + 1})
    other_plan = build_run_plan(changed, plan.document_manifest_hash)
    first = manager.submit("full-queue-request-key-001", config, plan.document_manifest_hash, plan.index_fingerprint)
    second = manager.submit("full-queue-request-key-002", changed, plan.document_manifest_hash, other_plan.index_fingerprint)
    assert manager._queue.full()
    assert manager.submit("full-queue-request-key-001", config, plan.document_manifest_hash, plan.index_fingerprint).job_id == first.job_id
    with sqlite3.connect(state / "jobs.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM bootstrap_jobs WHERE status='queued'").fetchone()[0] == 2
    assert second.job_id != first.job_id
    hold.set(); manager.shutdown(10)


@pytest.mark.parametrize("failure_point", ["before_insert", "during_insert"])
def test_insert_failure_rolls_back_without_acknowledgment_or_queue_item(tmp_path, monkeypatch, failure_point):
    _, config, plan, state, manager, hold = _paused_manager(tmp_path, monkeypatch)
    real_connect = manager._connect
    class ExecuteFailure:
        def __init__(self, db): self.db = db
        def __enter__(self): self.db.__enter__(); return self
        def __exit__(self, *args): return self.db.__exit__(*args)
        def __getattr__(self, name): return getattr(self.db, name)
        def execute(self, sql, *args):
            if (failure_point == "before_insert" and sql == "BEGIN IMMEDIATE") or (failure_point == "during_insert" and sql.startswith("INSERT INTO bootstrap_jobs")):
                raise sqlite3.OperationalError("injected insertion failure")
            return self.db.execute(sql, *args)
    monkeypatch.setattr(manager, "_connect", lambda: ExecuteFailure(real_connect()))
    try:
        with pytest.raises(BootstrapJobError):
            manager.submit("insert-failure-request-001", config, plan.document_manifest_hash, plan.index_fingerprint)
        assert manager._queue.empty()
        monkeypatch.setattr(manager, "_connect", real_connect)
        with sqlite3.connect(state / "jobs.sqlite") as db:
            assert db.execute("SELECT COUNT(*) FROM bootstrap_jobs").fetchone()[0] == 0
        assert manager.submit("insert-failure-request-002", config, plan.document_manifest_hash, plan.index_fingerprint).status == "queued"
        assert manager._queue.qsize() == 1
    finally:
        monkeypatch.setattr(manager, "_connect", real_connect); hold.set(); manager.shutdown(10)


def test_worker_claim_failure_is_durably_failed_without_running_builder(tmp_path, monkeypatch):
    source, config, plan = _plan_with_text_and_optional_image(tmp_path)
    state = tmp_path / "state"; state.mkdir()
    manager = BootstrapJobManager(state / "jobs.sqlite", source, tmp_path / "storage")
    import backend.services.bootstrap_jobs as module
    build_calls = []
    monkeypatch.setattr(module, "build_index", lambda *args, **kwargs: build_calls.append(kwargs))
    manager.start()
    def fail_claim(_job_id): raise BootstrapJobError("job_store_unavailable")
    monkeypatch.setattr(manager, "_claim_job", fail_claim)
    accepted = manager.submit("claim-failure-request-001", config, plan.document_manifest_hash, plan.index_fingerprint)
    try:
        assert manager._completion[accepted.job_id].wait(20)
        row = manager.get(accepted.job_id)
        assert row.status == "failed" and row.error_code == "worker_claim_failed"
        assert build_calls == []
        with sqlite3.connect(state / "jobs.sqlite") as db:
            assert db.execute("SELECT status,error_code FROM bootstrap_jobs WHERE job_id=?", (accepted.job_id,)).fetchone() == ("failed", "worker_claim_failed")
        assert manager.shutdown(10)
    finally:
        if manager._owner_token is not None: manager.shutdown(10)


def test_worker_claim_database_outage_stays_unresolved_and_retains_owner(tmp_path, monkeypatch):
    source, config, plan = _plan_with_text_and_optional_image(tmp_path)
    state = tmp_path / "state"; state.mkdir()
    manager = BootstrapJobManager(state / "jobs.sqlite", source, tmp_path / "storage")
    manager.start()
    entered, release = Event(), Event()
    original_claim, original_connect = manager._claim_job, manager._connect
    def gated_claim(job_id):
        entered.set()
        assert release.wait(20), "claim gate timed out"
        return original_claim(job_id)
    monkeypatch.setattr(manager, "_claim_job", gated_claim)
    accepted = manager.submit("claim-db-outage-request-001", config, plan.document_manifest_hash, plan.index_fingerprint)
    try:
        assert entered.wait(10)
        monkeypatch.setattr(manager, "_connect", lambda: (_ for _ in ()).throw(BootstrapJobError("job_store_unavailable")))
        release.set()
        assert manager._completion[accepted.job_id].wait(10)
        assert manager._worker is not None
        manager._worker.join(10)
        assert not manager._accepting
        assert manager.shutdown(timeout=0) is False
        assert (state / ".bootstrap-instance-owner" / "owner").is_file()
        with sqlite3.connect(state / "jobs.sqlite") as db:
            assert db.execute("SELECT status FROM bootstrap_jobs WHERE job_id=?", (accepted.job_id,)).fetchone() == ("queued",)
        monkeypatch.setattr(manager, "_connect", original_connect)
        assert manager.shutdown(timeout=10)
        with sqlite3.connect(state / "jobs.sqlite") as db:
            assert db.execute("SELECT status,error_code FROM bootstrap_jobs WHERE job_id=?", (accepted.job_id,)).fetchone() == ("interrupted", "server_shutdown")
    finally:
        release.set()
        monkeypatch.setattr(manager, "_connect", original_connect)
        if manager._owner_token is not None: manager.shutdown(10)


def test_worker_claim_and_new_admission_do_not_consume_uncommitted_rows(tmp_path, monkeypatch):
    source, config, plan = _plan_with_text_and_optional_image(tmp_path)
    state = tmp_path / "state"; state.mkdir()
    storage = tmp_path / "storage"
    manager = BootstrapJobManager(state / "jobs.sqlite", source, storage, capacity=2)
    import backend.services.bootstrap_jobs as module
    original_claim = manager._claim_job
    claim_entered, release_claim = Event(), Event()
    claimed = []
    def gated_claim(job_id):
        if not claimed:
            claim_entered.set()
            assert release_claim.wait(20), "worker claim gate timed out"
        row = original_claim(job_id)
        if row is not None: claimed.append(job_id)
        return row
    build_calls = []
    def fail_result(run_plan, _source, owned, **kwargs):
        build_calls.append(kwargs.get("creation_policy"))
        return _fake_result_and_report(run_plan, owned, "failed_status")
    monkeypatch.setattr(manager, "_claim_job", gated_claim)
    monkeypatch.setattr(module, "build_index", fail_result)
    manager.start()
    first = manager.submit("claim-overlap-request-0001", config, plan.document_manifest_hash, plan.index_fingerprint)
    try:
        assert claim_entered.wait(10)
        changed = config.model_copy(update={"chunk_max_tokens": config.chunk_max_tokens + 1})
        changed_plan = build_run_plan(changed, plan.document_manifest_hash)
        second = manager.submit("claim-overlap-request-0002", changed, plan.document_manifest_hash, changed_plan.index_fingerprint)
        with sqlite3.connect(state / "jobs.sqlite") as db:
            assert db.execute("SELECT COUNT(*) FROM bootstrap_jobs WHERE status='queued'").fetchone()[0] == 2
        assert manager._queue.qsize() == 1
        release_claim.set()
        assert manager._completion[first.job_id].wait(20)
        assert manager._completion[second.job_id].wait(20)
        assert manager.get(first.job_id).status == manager.get(second.job_id).status == "failed"
        assert build_calls == ["fresh_only", "fresh_only"]
        assert len(claimed) == 2
        with sqlite3.connect(state / "jobs.sqlite") as db:
            assert db.execute("SELECT COUNT(*) FROM bootstrap_jobs").fetchone()[0] == 2
    finally:
        release_claim.set(); manager.shutdown(10)


def test_shutdown_waiting_for_admission_interrupts_accepted_queued_job(tmp_path, monkeypatch):
    source, config, plan = _plan_with_text_and_optional_image(tmp_path)
    state = tmp_path / "state"; state.mkdir()
    manager = BootstrapJobManager(state / "jobs.sqlite", source, tmp_path / "storage")
    hold_worker = Event(); monkeypatch.setattr(BootstrapJobManager, "_run", lambda self: hold_worker.wait(30))
    manager.start()
    import backend.services.bootstrap_jobs as module
    real_manifest = module.build_document_manifest
    manifest_entered, release_manifest, shutdown_started = Event(), Event(), Event()
    def gated_manifest(root):
        manifest_entered.set()
        assert release_manifest.wait(20), "manifest gate timed out"
        return real_manifest(root)
    monkeypatch.setattr(module, "build_document_manifest", gated_manifest)
    submitted = []
    def submitter(): submitted.append(manager.submit("shutdown-admission-request-01", config, plan.document_manifest_hash, plan.index_fingerprint))
    admission = Thread(target=submitter)
    admission.start()
    assert manifest_entered.wait(10)
    shutdown_result = []
    def shutdownter():
        shutdown_started.set()
        shutdown_result.append(manager.shutdown(0))
    shutdown_thread = Thread(target=shutdownter)
    shutdown_thread.start()
    assert shutdown_started.wait(10)
    release_manifest.set()
    admission.join(20); shutdown_thread.join(20)
    try:
        assert not admission.is_alive() and not shutdown_thread.is_alive()
        assert len(submitted) == 1 and submitted[0].status == "queued"
        assert shutdown_result == [False]  # test worker is deliberately held through join
        with sqlite3.connect(state / "jobs.sqlite") as db:
            assert db.execute("SELECT status,error_code FROM bootstrap_jobs WHERE job_id=?", (submitted[0].job_id,)).fetchone() == ("interrupted", "server_shutdown")
        with pytest.raises(BootstrapJobError, match="manager_not_accepting"):
            manager.submit("shutdown-admission-request-02", config, plan.document_manifest_hash, plan.index_fingerprint)
    finally:
        hold_worker.set()
        assert manager.shutdown(10)


@pytest.mark.parametrize("case", ["builder_exception", "failed_result", "partial_result", "writer_lease", "existing_index", "source_changed"])
def test_worker_failures_are_durable_and_never_retried(tmp_path, monkeypatch, case):
    source, config, plan = _plan_with_text_and_optional_image(tmp_path)
    state = tmp_path / "state"; state.mkdir()
    storage = tmp_path / "storage"
    manager = BootstrapJobManager(state / "jobs.sqlite", source, storage)
    import backend.services.bootstrap_jobs as module
    build_calls = []
    lease = None
    if case == "writer_lease":
        from backend.config.writer_lease import WriterLease
        lease = WriterLease.acquire(storage, plan.index_fingerprint)
    if case == "existing_index":
        existing = storage / "indexes" / plan.index_fingerprint
        existing.mkdir(parents=True)
        (existing / "operator-data").write_bytes(b"preserve")
    if case in {"builder_exception", "failed_result", "partial_result"}:
        def injected(run_plan, _source, owned, **kwargs):
            build_calls.append(kwargs.get("creation_policy"))
            if case == "builder_exception": raise RuntimeError("private path and secret must not escape")
            result = _fake_result_and_report(run_plan, owned, "valid")
            return replace(result, status="failed", failure_stage="vector_persistence", partial_persistence=case == "partial_result")
        monkeypatch.setattr(module, "build_index", injected)
    else:
        original_builder = module.build_index
        def counted(*args, **kwargs):
            build_calls.append(kwargs.get("creation_policy"))
            return original_builder(*args, **kwargs)
        monkeypatch.setattr(module, "build_index", counted)
    manager.start()
    if case == "source_changed":
        original_claim = manager._claim_job
        def mutate_after_claim(job_id):
            row = original_claim(job_id)
            next(source.glob("*.pdf")).write_bytes(b"source changed after admission")
            return row
        monkeypatch.setattr(manager, "_claim_job", mutate_after_claim)
    accepted = manager.submit(f"worker-failure-{case}-0001", config, plan.document_manifest_hash, plan.index_fingerprint)
    try:
        assert manager._completion[accepted.job_id].wait(30)
        record = manager.get(accepted.job_id)
        assert record.status == "failed"
        assert record.error_code in {"build_failed", "confirmation_changed"}
        assert case == "source_changed" and record.error_code == "confirmation_changed" or case != "source_changed" and record.error_code == "build_failed"
        assert record.partial_persistence is (case == "partial_result") if case in {"failed_result", "partial_result"} else record.partial_persistence is None
        assert build_calls == ([] if case == "source_changed" else ["fresh_only"])
        if case == "existing_index":
            assert (storage / "indexes" / plan.index_fingerprint / "operator-data").read_bytes() == b"preserve"
        if case == "writer_lease":
            assert (storage / ".writer-leases" / plan.index_fingerprint / "owner").is_file()
    finally:
        if lease is not None: lease.release()
        manager.shutdown(10)
    with sqlite3.connect(state / "jobs.sqlite") as db:
        assert db.execute("SELECT status FROM bootstrap_jobs WHERE job_id=?", (accepted.job_id,)).fetchone() == ("failed",)


def _fake_result_and_report(plan, storage, case):
    build_id = "build-fixture-001"
    root = storage / "build-artifacts" / build_id
    report_path = root / "build-report.json"
    root.mkdir(parents=True, exist_ok=True)
    result = IndexBuildResult(
        build_id=build_id, artifact_root=root, report_path=report_path,
        index_fingerprint=plan.index_fingerprint, document_manifest_hash=plan.document_manifest_hash,
        status="completed", extracted_pages=1, text_chunks_prepared=1, text_vectors_written=1,
        image_objects_extracted=0, image_vectors_written=0, image_failures=0, caption_fallbacks=0,
        text_embedding_seconds=0.1, image_embedding_seconds=0.0, total_elapsed_seconds=0.2,
        extraction_failures=(), warnings=(), text_records_persisted=1, image_records_persisted=0,
    )
    payload = result.to_serializable_dict()
    if case == "malformed_json":
        report_path.write_text("{", encoding="utf-8")
    else:
        if case == "schema": payload["report_schema_version"] = "unsupported"
        if case == "wrong_build_id": payload["build_id"] = "different-build"
        if case == "wrong_fingerprint": payload["index_fingerprint"] = "f" * 64
        if case == "wrong_manifest": payload["document_manifest_hash"] = "e" * 64
        if case == "failed_status": payload["status"] = "failed"
        if case == "wrong_text_count": payload["text_records_persisted"] = 2
        if case == "wrong_image_count": payload["image_records_persisted"] = 1
        if case == "partial": payload["partial_persistence"] = True
        if case == "result_identity": result = replace(result, index_fingerprint="d" * 64)
        if case == "wrong_hierarchy": payload["build_id"] = "other-build"
        report_path.write_text(json.dumps(payload), encoding="utf-8")
    if case == "incomplete_marker":
        marker = storage / "indexes" / plan.index_fingerprint / ".fresh-build-incomplete"
        marker.parent.mkdir(parents=True, exist_ok=True); marker.write_text("incomplete", encoding="ascii")
    if case == "missing_report": report_path.unlink(missing_ok=True)
    if case == "outside_report":
        outside = storage.parent / "outside" / "build-report.json"
        outside.parent.mkdir(parents=True, exist_ok=True); outside.write_text(json.dumps(payload), encoding="utf-8")
        result = replace(result, report_path=outside)
    if case == "symlink_report":
        outside = storage.parent / "symlink-target.json"
        outside.write_text(json.dumps(payload), encoding="utf-8")
        report_path.unlink(missing_ok=True)
        try: report_path.symlink_to(outside)
        except (OSError, NotImplementedError): return None
    return result


@pytest.mark.parametrize("case", [
    "missing_report", "malformed_json", "schema", "wrong_build_id", "wrong_fingerprint",
    "wrong_manifest", "failed_status", "wrong_text_count", "wrong_image_count", "partial",
    "result_identity", "incomplete_marker", "outside_report", "symlink_report", "wrong_hierarchy",
])
def test_worker_rejects_false_success_reports(tmp_path, monkeypatch, case):
    source, config, plan = _plan_with_text_and_optional_image(tmp_path)
    state = tmp_path / "state"; state.mkdir()
    storage = tmp_path / "storage"
    manager = BootstrapJobManager(state / "jobs.sqlite", source, storage)
    import backend.services.bootstrap_jobs as module
    calls = []
    def forged_build(run_plan, _source, owned_storage, **kwargs):
        calls.append(kwargs.get("creation_policy"))
        return _fake_result_and_report(run_plan, owned_storage, case)
    monkeypatch.setattr(module, "build_index", forged_build)
    manager.start()
    accepted = manager.submit(f"false-success-{case}-0001", config, plan.document_manifest_hash, plan.index_fingerprint)
    try:
        assert manager._completion[accepted.job_id].wait(20)
        record = manager.get(accepted.job_id)
        if case == "symlink_report" and not (storage / "build-artifacts" / "build-fixture-001" / "build-report.json").is_symlink():
            pytest.skip("the current Windows environment does not permit creating a report symlink")
        assert record.status != "succeeded", (case, record)
        assert record.error_code in {"build_failed", "worker_operation_failed"}
        assert calls == ["fresh_only"]
        assert not record.error_code or (str(storage.resolve()) not in record.error_code and "traceback" not in record.error_code.lower())
        if case not in {"missing_report"}:
            assert (storage / "build-artifacts" / "build-fixture-001" / "build-report.json").exists()
    finally:
        manager.shutdown(10)


def test_real_build_with_final_sqlite_failure_preserves_index_and_never_retries(tmp_path, monkeypatch):
    source, config, _ = _plan_with_text_and_optional_image(tmp_path)
    manifest = build_document_manifest(source)
    plan = build_run_plan(config, manifest.content_hash)
    state = tmp_path / "state"; state.mkdir()
    storage = tmp_path / "storage"
    from tests.integration.test_index_builder import _valid_text_adapter
    manager = BootstrapJobManager(state / "jobs.sqlite", source, storage, adapter_factory=lambda: (_valid_text_adapter(config), None))
    import backend.services.bootstrap_jobs as module
    real_builder = module.build_index
    builder_calls = []
    def count_builder(*args, **kwargs):
        builder_calls.append(kwargs.get("creation_policy")); return real_builder(*args, **kwargs)
    monkeypatch.setattr(module, "build_index", count_builder)
    real_connect = manager._connect
    failed_updates = []
    class FinalUpdateFailure:
        def __init__(self, db): self.db = db
        def __enter__(self): self.db.__enter__(); return self
        def __exit__(self, *args): return self.db.__exit__(*args)
        def __getattr__(self, name): return getattr(self.db, name)
        def execute(self, sql, *args):
            if sql.startswith("UPDATE bootstrap_jobs SET status=?,finished_at=?"):
                failed_updates.append(sql)
                raise sqlite3.OperationalError("injected final update failure")
            return self.db.execute(sql, *args)
    monkeypatch.setattr(manager, "_connect", lambda: FinalUpdateFailure(real_connect()))
    manager.start()
    accepted = manager.submit("final-sqlite-failure-0001", config, manifest.content_hash, plan.index_fingerprint)
    try:
        assert manager._completion[accepted.job_id].wait(60)
        assert manager._worker is not None
        manager._worker.join(20)
        assert manager._worker is not None and not manager._worker.is_alive()
        assert len(failed_updates) == 1
        reports = list(storage.glob("build-artifacts/*/build-report.json"))
        assert len(reports) == 1
        report_before = reports[0].read_bytes()
        from backend.engine.retrieval import _validate_build_report
        checked = _validate_build_report(storage, reports[0], plan)
        assert checked.index_fingerprint == plan.index_fingerprint
        assert builder_calls == ["fresh_only"]
        with sqlite3.connect(state / "jobs.sqlite") as db:
            durable = db.execute("SELECT status,error_code FROM bootstrap_jobs WHERE job_id=?", (accepted.job_id,)).fetchone()
        assert durable == ("running", None)
        assert manager._accepting is False
        assert manager.shutdown(10)
        assert reports[0].read_bytes() == report_before
        reopened = BootstrapJobManager(state / "jobs.sqlite", source, storage)
        reopened.start()
        try:
            assert reopened.get(accepted.job_id).status == "interrupted"
            assert builder_calls == ["fresh_only"]
        finally:
            assert reopened.shutdown(10)
    finally:
        if manager._owner_token is not None: manager.shutdown(10)


def test_shutdown_timeout_retains_instance_ownership_until_active_worker_exits(tmp_path, monkeypatch):
    source, config, plan = _plan_with_text_and_optional_image(tmp_path)
    state = tmp_path / "state"; state.mkdir()
    storage = tmp_path / "storage"
    entered, release = Event(), Event()
    import backend.services.bootstrap_jobs as module
    def blocked_build(run_plan, _source, owned, **kwargs):
        assert kwargs.get("creation_policy") == "fresh_only"
        entered.set()
        assert release.wait(20), "controlled worker release timed out"
        return _fake_result_and_report(run_plan, owned, "failed_status")
    monkeypatch.setattr(module, "build_index", blocked_build)
    manager = BootstrapJobManager(state / "jobs.sqlite", source, storage)
    manager.start()
    accepted = manager.submit("shutdown-active-worker-0001", config, plan.document_manifest_hash, plan.index_fingerprint)
    try:
        assert entered.wait(20), "worker did not enter controlled build"
        assert manager.shutdown(timeout=0) is False
        with pytest.raises(BootstrapJobError, match="manager_not_accepting"):
            manager.submit("shutdown-after-close-0001", config, plan.document_manifest_hash, plan.index_fingerprint)
        competing = BootstrapJobManager(state / "jobs.sqlite", source, storage)
        with pytest.raises(BootstrapJobError, match="bootstrap_instance_owned"):
            competing.start()
        assert manager._worker is not None and manager._worker.is_alive()
    finally:
        release.set()
    assert manager._completion[accepted.job_id].wait(20)
    assert manager.shutdown(timeout=20)
    with sqlite3.connect(state / "jobs.sqlite") as db:
        assert db.execute("SELECT status FROM bootstrap_jobs WHERE job_id=?", (accepted.job_id,)).fetchone() == ("failed",)
