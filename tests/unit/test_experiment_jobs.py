from __future__ import annotations

from pathlib import Path
import sqlite3
from threading import Event, Thread

import pytest

from backend.experiments import BenchmarkCase, BenchmarkSuite, ExperimentArtifactStore, ExperimentCatalog
from backend.services import (
    ExperimentJobError,
    ExperimentJobManager,
    JobCapacityError,
    JobNotFoundError,
    JobStateError,
    ExperimentService,
)
from tests.unit.test_experiment_service import _service


def _suite(query: str) -> BenchmarkSuite:
    return BenchmarkSuite("jobs-bench", "1", (BenchmarkCase("case-1", query, ("text",), text_k=5),))


def test_job_manager_executes_and_persists_evaluation(tmp_path: Path):
    service, plan, query = _service(tmp_path)
    manager = ExperimentJobManager(service, tmp_path / "jobs.sqlite")
    manager.start()
    try:
        submitted = manager.submit_evaluation(plan, _suite(query))
        assert submitted.status in {"queued", "running"}
        for _ in range(100):
            current = manager.get_job(submitted.job_id)
            if current.status in {"succeeded", "partial", "failed"}:
                break
            Event().wait(0.01)
        assert current.status == "succeeded"
        assert current.artifact_id is not None
        assert current.error_code is None
    finally:
        manager.shutdown()


def test_invalid_requests_are_rejected_before_job_rows(tmp_path: Path):
    service, plan, query = _service(tmp_path)
    manager = ExperimentJobManager(service, tmp_path / "jobs.sqlite")
    suite = _suite(query)
    object.__setattr__(plan, "run_fingerprint", "0" * 64)
    with pytest.raises(ExperimentJobError):
        manager.submit_evaluation(plan, suite)
    assert manager.list_jobs() == ()


def test_queue_capacity_and_cancelled_job_do_not_execute(tmp_path: Path):
    service, plan, query = _service(tmp_path)
    entered = Event()
    release = Event()
    calls: list[str] = []
    original = service.run_evaluation

    def blocked(run_plan, suite):
        calls.append(suite.cases[0].case_id)
        entered.set()
        release.wait(2)
        return original(run_plan, suite)

    service.run_evaluation = blocked  # type: ignore[method-assign]
    manager = ExperimentJobManager(service, tmp_path / "jobs.sqlite", max_pending_jobs=1)
    manager.start()
    try:
        first = manager.submit_evaluation(plan, _suite(query))
        assert entered.wait(2)
        second = manager.submit_evaluation(plan, BenchmarkSuite("jobs-bench", "1", (BenchmarkCase("case-2", query + " 2", ("text",), text_k=5),)))
        assert manager.cancel_job(second.job_id).status == "cancelled"
        with pytest.raises(JobCapacityError):
            manager.submit_evaluation(plan, BenchmarkSuite("jobs-bench", "1", (BenchmarkCase("case-3", query + " 3", ("text",), text_k=5),)))
        release.set()
        for _ in range(100):
            if manager.get_job(first.job_id).status in {"succeeded", "failed", "partial"}:
                break
            Event().wait(0.01)
        assert calls == ["case-1"]
    finally:
        release.set()
        manager.shutdown()


def test_cancel_state_and_identifier_validation(tmp_path: Path):
    service, plan, query = _service(tmp_path)
    manager = ExperimentJobManager(service, tmp_path / "jobs.sqlite")
    with pytest.raises(JobNotFoundError):
        manager.get_job("../../escape")
    with pytest.raises(JobStateError):
        manager.submit_evaluation(plan, _suite(query))


def test_restart_marks_stale_rows_interrupted(tmp_path: Path):
    service, plan, query = _service(tmp_path)
    database = tmp_path / "jobs.sqlite"
    ExperimentJobManager(service, database)
    with sqlite3.connect(tmp_path / "jobs.sqlite") as connection:
        connection.execute("INSERT INTO experiment_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("123e4567-e89b-12d3-a456-426614174000", "evaluation", "queued", "2026-01-01T00:00:00+00:00", None, None, "b", "1", plan.run_fingerprint, None, None, None, None, None))
    manager = ExperimentJobManager(service, database)
    manager.start()
    try:
        record = manager.get_job("123e4567-e89b-12d3-a456-426614174000")
        assert record.status == "interrupted"
        assert record.error_code == "process_restarted"
    finally:
        manager.shutdown()


def test_submission_rechecks_shutdown_state_before_admission(tmp_path: Path, monkeypatch):
    service, plan, query = _service(tmp_path)
    manager = ExperimentJobManager(service, tmp_path / "jobs.sqlite")
    manager.start()
    checked = Event()
    resume = Event()
    original_check = manager._validate_manager
    errors = []

    def paused_check():
        original_check()
        checked.set()
        assert resume.wait(2)

    monkeypatch.setattr(manager, "_validate_manager", paused_check)

    def submit():
        try:
            manager.submit_evaluation(plan, _suite(query))
        except BaseException as exc:
            errors.append(exc)

    thread = Thread(target=submit)
    thread.start()
    try:
        assert checked.wait(2)
        manager.shutdown()
        resume.set()
        thread.join(2)
        assert not thread.is_alive()
        assert len(errors) == 1
        assert isinstance(errors[0], JobStateError)
        assert errors[0].error_code == "manager_not_started"
        assert manager.list_jobs() == ()
        assert manager._queue.qsize() == 0
    finally:
        resume.set()
        manager.shutdown()
        thread.join(2)


def test_shutdown_timeout_is_terminal_and_late_completion_cannot_overwrite(tmp_path: Path, monkeypatch):
    service, plan, query = _service(tmp_path)
    entered = Event()
    release = Event()
    late_finish_attempted = Event()
    original_run = service.run_evaluation
    manager = ExperimentJobManager(service, tmp_path / "jobs.sqlite", shutdown_timeout_seconds=0.05)

    def blocking_run(run_plan, suite):
        entered.set()
        assert release.wait(3)
        return original_run(run_plan, suite)

    service.run_evaluation = blocking_run  # type: ignore[method-assign]
    original_finish = manager._finish

    def observe_finish(job_id, **kwargs):
        original_finish(job_id, **kwargs)
        if kwargs["status"] in {"succeeded", "partial", "failed"}:
            late_finish_attempted.set()

    monkeypatch.setattr(manager, "_finish", observe_finish)
    manager.start()
    submitted = manager.submit_evaluation(plan, _suite(query))
    try:
        assert entered.wait(2)
        manager.shutdown()
        interrupted = manager.get_job(submitted.job_id)
        assert interrupted.status == "interrupted"
        assert interrupted.error_code == "shutdown_interrupted"
        with pytest.raises(JobStateError) as error:
            manager.start()
        assert error.value.error_code == "manager_shutdown"
    finally:
        release.set()
        assert late_finish_attempted.wait(5)
        manager.shutdown()
    manager._workers[0].join(2)
    final = manager.get_job(submitted.job_id)
    assert final.status == "interrupted"
    assert final.error_code == "shutdown_interrupted"
    assert final.artifact_id is None


def test_running_start_is_idempotent_and_shutdown_is_idempotent(tmp_path: Path):
    service, _, _ = _service(tmp_path)
    manager = ExperimentJobManager(service, tmp_path / "jobs.sqlite")
    manager.start()
    workers = tuple(manager._workers)
    manager.start()
    assert tuple(manager._workers) == workers
    manager.shutdown()
    manager.shutdown()
    with pytest.raises(JobStateError) as error:
        manager.start()
    assert error.value.error_code == "manager_shutdown"
