"""Fail-closed, per-index writer reservations for cooperating processes."""

from __future__ import annotations

import os
from pathlib import Path
import re
import secrets
import threading


class WriterLeaseError(RuntimeError):
    """A writer reservation cannot be acquired or verified."""


def _reject_symlinks(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise WriterLeaseError("writer_lease_unsafe_path")


class WriterLease:
    """Exclusive capability held by one process and thread.

    A crash leaves the reservation in place. No automatic takeover is
    attempted; an operator must investigate before removing a stale marker.
    """

    def __init__(self, root: Path, fingerprint: str, marker: Path, token: str) -> None:
        self._root = root
        self._fingerprint = fingerprint
        self._marker = marker
        self._token = token
        self._pid = os.getpid()
        self._thread = threading.get_ident()
        self._active = True
        self._store_claimed = False

    @classmethod
    def acquire(cls, storage_root: str | Path, fingerprint: str) -> "WriterLease":
        if not isinstance(fingerprint, str) or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
            raise WriterLeaseError("writer_lease_identity_invalid")
        raw_root = Path(storage_root).expanduser()
        if not raw_root.is_absolute():
            raise WriterLeaseError("writer_lease_root_invalid")
        _reject_symlinks(raw_root)
        root = raw_root.resolve(strict=False)
        namespace = root / ".writer-leases"
        try:
            root.mkdir(parents=True, exist_ok=True)
            _reject_symlinks(root)
            namespace.mkdir(exist_ok=True)
            if namespace.is_symlink() or not namespace.is_dir():
                raise WriterLeaseError("writer_lease_unsafe_path")
            marker = namespace / fingerprint
            marker.mkdir()
        except FileExistsError as exc:
            raise WriterLeaseError("writer_lease_busy") from exc
        except WriterLeaseError:
            raise
        except OSError as exc:
            raise WriterLeaseError("writer_lease_unavailable") from exc

        token = secrets.token_hex(32)
        try:
            with (marker / "owner").open("x", encoding="ascii") as stream:
                stream.write(token)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            # An incomplete marker is retained and blocks future writers.
            raise WriterLeaseError("writer_lease_initialization_failed") from exc
        return cls(root, fingerprint, marker, token)

    def verify(self, storage_root: str | Path, fingerprint: str) -> None:
        if not self._active or self._pid != os.getpid() or self._thread != threading.get_ident():
            raise WriterLeaseError("writer_lease_owner_invalid")
        try:
            root = Path(storage_root).expanduser().resolve(strict=False)
            _reject_symlinks(self._marker)
            owner = self._marker / "owner"
            if root != self._root or fingerprint != self._fingerprint or owner.is_symlink() or owner.read_text(encoding="ascii") != self._token:
                raise WriterLeaseError("writer_lease_owner_invalid")
        except (OSError, UnicodeError) as exc:
            raise WriterLeaseError("writer_lease_owner_invalid") from exc

    def release(self) -> None:
        self.verify(self._root, self._fingerprint)
        if self._store_claimed:
            raise WriterLeaseError("writer_lease_store_open")
        try:
            (self._marker / "owner").unlink()
            self._marker.rmdir()
        except OSError as exc:
            raise WriterLeaseError("writer_lease_release_failed") from exc
        finally:
            self._active = False

    def claim_store(self, storage_root: str | Path, fingerprint: str) -> None:
        self.verify(storage_root, fingerprint)
        if self._store_claimed:
            raise WriterLeaseError("writer_lease_store_open")
        self._store_claimed = True

    def release_store(self) -> None:
        self.verify(self._root, self._fingerprint)
        if not self._store_claimed:
            raise WriterLeaseError("writer_lease_store_not_open")
        self._store_claimed = False

    def __enter__(self) -> "WriterLease":
        self.verify(self._root, self._fingerprint)
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()
