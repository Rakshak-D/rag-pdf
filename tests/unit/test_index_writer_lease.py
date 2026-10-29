from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import os

import pytest

from backend.config.writer_lease import WriterLease, WriterLeaseError


def _hold_lease(root, fingerprint, ready, release):
    lease = WriterLease.acquire(root, fingerprint)
    try:
        ready.set()
        release.wait(20)
    finally:
        lease.release()


def test_same_process_cannot_reenter_and_capability_is_thread_bound(tmp_path):
    root = tmp_path / "storage"
    fingerprint = "a" * 64
    with WriterLease.acquire(root, fingerprint) as lease:
        with pytest.raises(WriterLeaseError, match="writer_lease_busy"):
            WriterLease.acquire(root, fingerprint)
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(lease.verify, root, fingerprint)
            with pytest.raises(WriterLeaseError, match="writer_lease_owner_invalid"):
                result.result(timeout=10)
        with WriterLease.acquire(root, "b" * 64):
            lease.verify(root, fingerprint)
    with WriterLease.acquire(root, fingerprint):
        pass


def test_independent_process_cannot_acquire_active_lease(tmp_path):
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    root = tmp_path / "storage"
    fingerprint = "a" * 64
    process = context.Process(target=_hold_lease, args=(root, fingerprint, ready, release))
    process.start()
    try:
        assert ready.wait(20), "child did not acquire lease"
        with pytest.raises(WriterLeaseError, match="writer_lease_busy"):
            WriterLease.acquire(root, fingerprint)
        with WriterLease.acquire(root, "b" * 64):
            pass
    finally:
        release.set()
        process.join(20)
        if process.is_alive():
            process.terminate()
            process.join(10)
    assert process.exitcode == 0
    with WriterLease.acquire(root, fingerprint):
        pass


def test_process_termination_leaves_fail_closed_reservation(tmp_path):
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    root = tmp_path / "storage"
    fingerprint = "a" * 64
    process = context.Process(target=_hold_lease, args=(root, fingerprint, ready, release))
    process.start()
    try:
        assert ready.wait(20), "child did not acquire lease"
        process.terminate()
        process.join(10)
        assert not process.is_alive()
        with pytest.raises(WriterLeaseError, match="writer_lease_busy"):
            WriterLease.acquire(root, fingerprint)
    finally:
        if process.is_alive():
            process.terminate()
            process.join(10)


def test_invalid_fingerprint_and_symlinked_namespace_are_rejected(tmp_path):
    root = tmp_path / "storage"
    with pytest.raises(WriterLeaseError, match="writer_lease_identity_invalid"):
        WriterLease.acquire(root, "../invalid")
    root.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    try:
        (root / ".writer-leases").symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    with pytest.raises(WriterLeaseError):
        WriterLease.acquire(root, "a" * 64)
    assert not list(target.iterdir())
