"""Durable, report-level active index selection for the local runtime."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
from typing import Any, Iterator, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.config.paths import PathContainmentError, resolve_disjoint_paths, resolve_owned_path
from backend.config.schema import ResolvedRunConfig
from backend.engine.manifest import build_document_manifest
from backend.experiments.ablation_plans import create_ablation_plan
from backend.experiments.run_plan import RunPlan, build_run_plan
from backend.services.index_inventory import IndexInventoryService


_BUILD_ID = re.compile(r"^[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OPERATION_ID = re.compile(r"^[0-9a-f]{32}$")
_SCHEMA_VERSION = 1
_MAX_STATE_BYTES = 256 * 1024


class IndexSelectionError(ValueError):
    """Stable selection error that never includes local paths or payloads."""

    def __init__(self, code: str) -> None:
        self.code = code if re.fullmatch(r"[a-z][a-z0-9_]{1,63}", code or "") else "index_selection_error"
        super().__init__(self.code)


class _SelectionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = _SCHEMA_VERSION
    revision: int = Field(ge=1, le=2_147_483_647)
    operation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    storage_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    document_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    index_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    report_reference: str = Field(min_length=1, max_length=96)
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    report_status: Literal["completed", "completed_with_warnings"]
    configuration_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_plan_json: str = Field(min_length=1, max_length=192 * 1024)

    @model_validator(mode="after")
    def validate_reference(self):
        expected = f"build-artifacts/{self.build_id}/build-report.json"
        if self.report_reference != expected:
            raise ValueError("report_reference does not match build_id")
        return self


@dataclass(frozen=True)
class PinnedIndexReport:
    """Server-derived report identity captured for one accepted execution."""

    build_id: str
    report_reference: str
    report_sha256: str
    document_manifest_hash: str
    index_fingerprint: str
    configuration_snapshot_hash: str
    run_fingerprint: str
    revision: int
    storage_root_sha256: str
    report_path: Path = field(repr=False)
    legacy: bool = False


class IndexSelectionService:
    """CAS-protected selection pointer; inspection never opens Chroma."""

    def __init__(
        self,
        source_root: str | Path,
        storage_root: str | Path,
        state_path: str | Path,
        *,
        legacy_report_path: str | Path | None = None,
    ) -> None:
        try:
            self._source_root = Path(source_root).expanduser().resolve(strict=True)
            self._storage_root = Path(storage_root).expanduser().resolve(strict=True)
            self._state_path = Path(state_path).expanduser()
        except OSError as exc:
            raise IndexSelectionError("selection_paths_unavailable") from exc
        if not self._source_root.is_dir() or not self._storage_root.is_dir() or not self._state_path.is_absolute():
            raise IndexSelectionError("selection_paths_invalid")
        if self._state_path.name != "active-index-selection.json" or not self._state_path.parent.is_dir():
            raise IndexSelectionError("selection_state_path_invalid")
        if self._has_symlink_component(self._state_path.parent):
            raise IndexSelectionError("selection_state_path_invalid")
        try:
            resolve_disjoint_paths(self._source_root, self._storage_root, first_label="source", second_label="index storage")
            resolve_disjoint_paths(self._state_path.parent, self._source_root, first_label="selection state", second_label="source")
            resolve_disjoint_paths(self._state_path.parent, self._storage_root, first_label="selection state", second_label="index storage")
        except PathContainmentError as exc:
            raise IndexSelectionError("selection_paths_overlap") from exc
        self._legacy_report_path: Path | None = None
        if legacy_report_path is not None:
            try:
                candidate = resolve_owned_path(self._storage_root, legacy_report_path)
            except PathContainmentError as exc:
                raise IndexSelectionError("build_report_path_invalid") from exc
            try:
                relative = candidate.relative_to(self._storage_root)
            except ValueError as exc:
                raise IndexSelectionError("build_report_path_invalid") from exc
            if (len(relative.parts) != 3 or relative.parts[0] != "build-artifacts" or
                    relative.parts[2] != "build-report.json" or not _BUILD_ID.fullmatch(relative.parts[1])):
                raise IndexSelectionError("build_report_path_invalid")
            self._legacy_report_path = candidate
        self._lock_path = self._state_path.with_name(".active-index-selection.lock")
        self._thread_lock = threading.RLock()
        self._inventory = IndexInventoryService(self._source_root, self._storage_root)
        self._storage_root_sha256 = self._path_identity(self._storage_root)

    @staticmethod
    def _has_symlink_component(path: Path) -> bool:
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current = current / part
            try:
                if current.is_symlink():
                    return True
            except OSError:
                return True
        return False

    @staticmethod
    def _path_identity(path: Path) -> str:
        canonical = os.path.normcase(str(path.resolve(strict=True)))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @contextmanager
    def admission_guard(self) -> Iterator[None]:
        """Serialize selection CAS writes with queued-job selection capture."""
        if self._has_symlink_component(self._lock_path.parent) or self._lock_path.is_symlink():
            raise IndexSelectionError("selection_lock_unavailable")
        with self._thread_lock:
            flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0)
            descriptor = -1
            handle = None
            locked = False
            try:
                descriptor = os.open(self._lock_path, flags, 0o600)
                if os.name != "nt":
                    os.fchmod(descriptor, 0o600)
                if self._lock_path.is_symlink() or not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    os.close(descriptor)
                    descriptor = -1
                    raise IndexSelectionError("selection_lock_unavailable")
                handle = os.fdopen(descriptor, "r+b", closefd=True)
                descriptor = -1
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0, os.SEEK_END)
                    if handle.tell() == 0:
                        handle.write(b"\0")
                        handle.flush()
                        os.fsync(handle.fileno())
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                locked = True
            except IndexSelectionError:
                if handle is not None:
                    handle.close()
                elif descriptor >= 0:
                    os.close(descriptor)
                raise
            except (OSError, ImportError) as exc:
                if handle is not None:
                    handle.close()
                elif descriptor >= 0:
                    os.close(descriptor)
                raise IndexSelectionError("selection_lock_unavailable") from exc
            try:
                yield
            finally:
                if handle is not None:
                    try:
                        if locked and os.name == "nt":
                            import msvcrt

                            handle.seek(0)
                            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                        elif locked:
                            import fcntl

                            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    except (OSError, ImportError) as exc:
                        raise IndexSelectionError("selection_unlock_failed") from exc
                    finally:
                        handle.close()

    @staticmethod
    def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate selection key")
            result[key] = value
        return result

    @staticmethod
    def _reject_constant(_value: str) -> None:
        raise ValueError("invalid selection number")

    def _read_record(self) -> _SelectionRecord | None:
        if self._has_symlink_component(self._state_path.parent) or self._state_path.is_symlink():
            raise IndexSelectionError("selection_state_invalid")
        try:
            before = self._state_path.lstat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise IndexSelectionError("selection_state_unavailable") from exc
        if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_STATE_BYTES:
            raise IndexSelectionError("selection_state_invalid")
        try:
            with self._state_path.open("rb") as handle:
                opened = os.fstat(handle.fileno())
                payload = handle.read(_MAX_STATE_BYTES + 1)
                after = os.fstat(handle.fileno())
            final = self._state_path.lstat()
        except OSError as exc:
            raise IndexSelectionError("selection_state_unavailable") from exc
        signature = lambda info: (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if (len(payload) > _MAX_STATE_BYTES or signature(before) != signature(opened) or
                signature(opened) != signature(after) or signature(after) != signature(final) or
                self._state_path.is_symlink()):
            raise IndexSelectionError("selection_state_invalid")
        try:
            value = json.loads(payload.decode("utf-8", "strict"), object_pairs_hook=self._unique_pairs, parse_constant=self._reject_constant)
            return _SelectionRecord.model_validate(value, strict=True)
        except Exception as exc:
            raise IndexSelectionError("selection_state_invalid") from exc

    @staticmethod
    def _record_plan(record: _SelectionRecord) -> RunPlan:
        try:
            payload = json.loads(record.run_plan_json, object_pairs_hook=IndexSelectionService._unique_pairs,
                                 parse_constant=IndexSelectionService._reject_constant)
            plan = RunPlan.model_validate(payload, strict=True)
            plan.verify_integrity()
        except Exception as exc:
            raise IndexSelectionError("selection_state_invalid") from exc
        if (plan.document_manifest_hash != record.document_manifest_hash or
                plan.index_fingerprint != record.index_fingerprint or
                plan.configuration_snapshot_hash != record.configuration_snapshot_hash or
                plan.run_fingerprint != record.run_fingerprint):
            raise IndexSelectionError("selection_state_invalid")
        return plan

    def _current_manifest(self, expected: str | None = None):
        try:
            manifest = build_document_manifest(self._source_root)
        except Exception as exc:
            raise IndexSelectionError("selection_source_unavailable") from exc
        if not manifest.entries:
            raise IndexSelectionError("manifest_empty")
        if expected is not None and manifest.content_hash != expected:
            raise IndexSelectionError("manifest_changed")
        return manifest

    def _validate_config(self, config: ResolvedRunConfig) -> ResolvedRunConfig:
        if not isinstance(config, ResolvedRunConfig):
            raise IndexSelectionError("configuration_invalid")
        try:
            validated = ResolvedRunConfig.model_validate(config.model_dump(mode="python"), strict=True)
            if (validated.reset_collections_on_start or validated.workbench_policy.allow_destructive_index_reset or
                    validated.workbench_policy.allow_collection_delete):
                raise IndexSelectionError("destructive_configuration_forbidden")
            validated.validate_for_workbench()
            configured_source = Path(validated.pdf_dir).expanduser().resolve(strict=True)
        except IndexSelectionError:
            raise
        except Exception as exc:
            raise IndexSelectionError("configuration_invalid") from exc
        if os.path.normcase(str(configured_source)) != os.path.normcase(str(self._source_root)):
            raise IndexSelectionError("configuration_source_mismatch")
        return validated

    def _candidate_and_digest(self, config: ResolvedRunConfig, manifest_hash: str, build_id: str):
        try:
            candidate = self._inventory.inspect_build_id(config, manifest_hash, build_id)
        except Exception as exc:
            code = getattr(exc, "code", "selection_candidate_invalid")
            raise IndexSelectionError(code if re.fullmatch(r"[a-z][a-z0-9_]{1,63}", str(code)) else "selection_candidate_invalid") from exc
        if candidate.compatibility != "report_match" or candidate.report_reference != f"build-artifacts/{build_id}/build-report.json":
            if candidate.compatibility == "unavailable" and candidate.reason_code == "candidate_unavailable":
                candidate_dir = self._storage_root / "build-artifacts" / build_id
                try:
                    candidate_dir.lstat()
                except FileNotFoundError as exc:
                    raise IndexSelectionError("build_report_not_found") from exc
                except OSError as exc:
                    raise IndexSelectionError("build_report_unavailable") from exc
            codes = {
                "failed": "build_report_failed",
                "incomplete": "build_report_incomplete",
                "mismatch": "build_report_mismatch",
                "unavailable": "build_report_unavailable",
                "invalid": "build_report_invalid",
            }
            raise IndexSelectionError(codes.get(candidate.compatibility, "build_report_invalid"))
        path = self._storage_root / "build-artifacts" / build_id / "build-report.json"
        before = self._inventory._report_file_signature(build_id)
        if before is None or before[0] < 0 or before[3] < 0 or before[3] > self._inventory._max_report_bytes:
            raise IndexSelectionError("build_report_unavailable")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
            with os.fdopen(descriptor, "rb", closefd=True) as handle:
                opened = os.fstat(handle.fileno())
                if not stat.S_ISREG(opened.st_mode):
                    raise IndexSelectionError("build_report_invalid")
                digest = hashlib.sha256()
                total = 0
                while True:
                    chunk = handle.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self._inventory._max_report_bytes:
                        raise IndexSelectionError("build_report_unavailable")
                    digest.update(chunk)
                finished = os.fstat(handle.fileno())
        except IndexSelectionError:
            raise
        except OSError as exc:
            raise IndexSelectionError("build_report_unavailable") from exc
        after = self._inventory._report_file_signature(build_id)
        signature = lambda info: (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if (after != before or signature(opened) != signature(finished) or total != before[3] or path.is_symlink()):
            raise IndexSelectionError("build_report_changed")
        try:
            final_candidate = self._inventory.inspect_build_id(config, manifest_hash, build_id)
        except Exception as exc:
            raise IndexSelectionError("build_report_changed") from exc
        if final_candidate.compatibility != "report_match":
            raise IndexSelectionError("build_report_changed")
        return candidate, digest.hexdigest(), path

    def _validate_record(self, record: _SelectionRecord) -> PinnedIndexReport:
        if record.storage_root_sha256 != self._storage_root_sha256:
            raise IndexSelectionError("selection_storage_root_changed")
        plan = self._record_plan(record)
        config = self._validate_config(plan.resolved_config())
        manifest = self._current_manifest(record.document_manifest_hash)
        candidate, digest, path = self._candidate_and_digest(config, manifest.content_hash, record.build_id)
        if digest != record.report_sha256 or candidate.expected_index_fingerprint != record.index_fingerprint:
            raise IndexSelectionError("selection_report_changed")
        return PinnedIndexReport(
            build_id=record.build_id,
            report_reference=record.report_reference,
            report_sha256=record.report_sha256,
            document_manifest_hash=record.document_manifest_hash,
            index_fingerprint=record.index_fingerprint,
            configuration_snapshot_hash=record.configuration_snapshot_hash,
            run_fingerprint=record.run_fingerprint,
            revision=record.revision,
            storage_root_sha256=record.storage_root_sha256,
            report_path=path,
        )

    @staticmethod
    def _public_identity(record: _SelectionRecord) -> dict[str, Any]:
        plan = IndexSelectionService._record_plan(record)
        return {
            "build_id": record.build_id,
            "report_reference": record.report_reference,
            "document_manifest_hash": record.document_manifest_hash,
            "index_fingerprint": record.index_fingerprint,
            "report_sha256": record.report_sha256,
            "configuration_snapshot_hash": record.configuration_snapshot_hash,
            "run_fingerprint": record.run_fingerprint,
            "report_status": record.report_status,
            "selection_revision": record.revision,
        }

    def _state_for_record(self, record: _SelectionRecord) -> dict[str, Any]:
        try:
            self._validate_record(record)
            status: Literal["selected", "stale"] = "selected"
            reason = "selection_verified"
        except IndexSelectionError as exc:
            status = "stale"
            reason = exc.code
        try:
            identity = self._public_identity(record)
        except IndexSelectionError:
            return {"selection_status": "invalid", "revision": None, "selection": None,
                    "legacy_report_reference": None, "reason_code": "selection_state_invalid",
                    "check_level": "report", "store_checked": False}
        return {"selection_status": status, "revision": record.revision, "selection": identity,
                "legacy_report_reference": None, "reason_code": reason,
                "check_level": "report", "store_checked": False}

    def get_state(self) -> dict[str, Any]:
        try:
            record = self._read_record()
        except IndexSelectionError as exc:
            return {"selection_status": "invalid", "revision": None, "selection": None,
                    "legacy_report_reference": None, "reason_code": exc.code,
                    "check_level": "report", "store_checked": False}
        if record is not None:
            return self._state_for_record(record)
        if self._legacy_report_path is not None:
            relative = self._legacy_report_path.relative_to(self._storage_root).as_posix()
            return {"selection_status": "legacy_configured", "revision": 0, "selection": None,
                    "legacy_report_reference": relative, "reason_code": "legacy_report_not_checked",
                    "check_level": "report", "store_checked": False}
        return {"selection_status": "unselected", "revision": 0, "selection": None,
                "legacy_report_reference": None, "reason_code": "index_not_selected",
                "check_level": "report", "store_checked": False}

    def has_durable_selection(self) -> bool:
        """Return whether a durable selection record exists; invalid state fails closed."""
        return self._read_record() is not None

    def _legacy_pin(self, plan: RunPlan) -> PinnedIndexReport:
        if self._legacy_report_path is None:
            raise IndexSelectionError("index_not_selected")
        relative = self._legacy_report_path.relative_to(self._storage_root)
        build_id = relative.parts[1]
        config = self._validate_config(plan.resolved_config())
        if (plan.document_manifest_hash != self._current_manifest().content_hash or
                not _BUILD_ID.fullmatch(build_id)):
            raise IndexSelectionError("legacy_report_incompatible")
        candidate, digest, path = self._candidate_and_digest(config, plan.document_manifest_hash, build_id)
        if candidate.expected_index_fingerprint != plan.index_fingerprint:
            raise IndexSelectionError("legacy_report_incompatible")
        return PinnedIndexReport(build_id, relative.as_posix(), digest, plan.document_manifest_hash,
                                 plan.index_fingerprint, plan.configuration_snapshot_hash, plan.run_fingerprint,
                                 0, self._storage_root_sha256, path, True)

    def resolve_for_plan(self, plan: RunPlan) -> PinnedIndexReport:
        if not isinstance(plan, RunPlan):
            raise IndexSelectionError("run_plan_invalid")
        try:
            plan.verify_integrity()
            record = self._read_record()
        except IndexSelectionError:
            raise
        except Exception as exc:
            raise IndexSelectionError("run_plan_invalid") from exc
        if record is None:
            return self._legacy_pin(plan)
        try:
            active = self._validate_record(record)
        except IndexSelectionError as exc:
            raise IndexSelectionError("index_selection_stale") from exc
        if (active.document_manifest_hash != plan.document_manifest_hash or
                active.index_fingerprint != plan.index_fingerprint):
            raise IndexSelectionError("index_selection_incompatible")
        self.validate_pinned(plan, active)
        return active

    def resolve_for_config(self, config: ResolvedRunConfig, manifest_hash: str, workflow: str) -> PinnedIndexReport:
        config = self._validate_config(config)
        if workflow not in {"evaluation", "ablation"}:
            raise IndexSelectionError("workflow_invalid")
        manifest = self._current_manifest(manifest_hash)
        try:
            if workflow == "evaluation":
                plan = build_run_plan(config, manifest.content_hash)
            else:
                plan = create_ablation_plan(config, manifest.content_hash).variants[0].run_plan
        except Exception as exc:
            raise IndexSelectionError("run_plan_invalid") from exc
        return self.resolve_for_plan(plan)

    def select(
        self,
        *,
        config: ResolvedRunConfig,
        expected_manifest_hash: str,
        expected_index_fingerprint: str,
        build_id: str,
        expected_revision: int,
        operation_id: str,
    ) -> dict[str, Any]:
        if not isinstance(build_id, str) or not _BUILD_ID.fullmatch(build_id):
            raise IndexSelectionError("build_id_invalid")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0 or expected_revision >= 2_147_483_647:
            raise IndexSelectionError("selection_revision_invalid")
        if not isinstance(operation_id, str) or not _OPERATION_ID.fullmatch(operation_id):
            raise IndexSelectionError("selection_operation_id_invalid")
        if not isinstance(expected_index_fingerprint, str) or not _SHA256.fullmatch(expected_index_fingerprint):
            raise IndexSelectionError("index_fingerprint_invalid")
        if not isinstance(expected_manifest_hash, str) or not _SHA256.fullmatch(expected_manifest_hash):
            raise IndexSelectionError("manifest_hash_invalid")
        with self.admission_guard():
            current = self._read_record()
            current_revision = current.revision if current is not None else 0
            if current is not None and current.operation_id == operation_id:
                validated = self._validate_config(config)
                manifest = self._current_manifest(expected_manifest_hash)
                plan = build_run_plan(validated, manifest.content_hash)
                if (current.build_id != build_id or current.document_manifest_hash != expected_manifest_hash or
                        current.index_fingerprint != expected_index_fingerprint or
                        current.configuration_snapshot_hash != plan.configuration_snapshot_hash or
                        current.run_fingerprint != plan.run_fingerprint):
                    raise IndexSelectionError("selection_operation_conflict")
                state = self._state_for_record(current)
                if state.get("selection_status") != "selected":
                    raise IndexSelectionError("index_selection_stale")
                return state
            if current_revision != expected_revision:
                raise IndexSelectionError("selection_revision_conflict")
            validated = self._validate_config(config)
            manifest = self._current_manifest(expected_manifest_hash)
            try:
                plan = build_run_plan(validated, manifest.content_hash)
                plan.verify_integrity()
            except Exception as exc:
                raise IndexSelectionError("run_plan_invalid") from exc
            if plan.index_fingerprint != expected_index_fingerprint:
                raise IndexSelectionError("index_fingerprint_mismatch")
            candidate, report_digest, _path = self._candidate_and_digest(validated, manifest.content_hash, build_id)
            if candidate.expected_index_fingerprint != plan.index_fingerprint or candidate.document_manifest_hash != manifest.content_hash:
                raise IndexSelectionError("build_report_mismatch")
            final_manifest = self._current_manifest(manifest.content_hash)
            final_candidate, final_digest, _final_path = self._candidate_and_digest(validated, final_manifest.content_hash, build_id)
            if final_candidate.compatibility != "report_match" or final_digest != report_digest:
                raise IndexSelectionError("build_report_changed")
            if current is not None and current_revision >= 2_147_483_646:
                raise IndexSelectionError("selection_revision_exhausted")
            record = _SelectionRecord(
                revision=current_revision + 1,
                operation_id=operation_id,
                storage_root_sha256=self._storage_root_sha256,
                document_manifest_hash=manifest.content_hash,
                index_fingerprint=plan.index_fingerprint,
                build_id=build_id,
                report_reference=f"build-artifacts/{build_id}/build-report.json",
                report_sha256=report_digest,
                report_status=candidate.report_status,
                configuration_snapshot_hash=plan.configuration_snapshot_hash,
                run_fingerprint=plan.run_fingerprint,
                run_plan_json=plan.to_serializable_json(),
            )
            try:
                self._write_record(record)
            except IndexSelectionError:
                try:
                    return self._confirm_written_record(record)
                except IndexSelectionError:
                    pass
                raise IndexSelectionError("selection_commit_uncertain")
            return self._confirm_written_record(record)

    def _confirm_written_record(self, expected: _SelectionRecord) -> dict[str, Any]:
        """Confirm the exact committed payload, not merely a matching revision."""
        actual = self._read_record()
        if actual != expected:
            raise IndexSelectionError("selection_commit_uncertain")
        state = self._state_for_record(actual)
        if state.get("selection_status") != "selected" or state.get("revision") != expected.revision:
            raise IndexSelectionError("selection_commit_uncertain")
        return state

    def _write_record(self, record: _SelectionRecord) -> None:
        if self._has_symlink_component(self._state_path.parent) or self._state_path.is_symlink():
            raise IndexSelectionError("selection_state_invalid")
        payload = json.dumps(record.model_dump(mode="json"), ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")) + "\n"
        if len(payload.encode("utf-8")) > _MAX_STATE_BYTES:
            raise IndexSelectionError("selection_state_too_large")
        descriptor = -1
        temporary: Path | None = None
        try:
            descriptor, raw_path = tempfile.mkstemp(prefix=".active-index-selection-", suffix=".tmp", dir=self._state_path.parent)
            temporary = Path(raw_path)
            if os.name != "nt":
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n", closefd=True) as handle:
                descriptor = -1
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._state_path)
            temporary = None
            if os.name != "nt":
                directory_fd = os.open(self._state_path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except OSError as exc:
            raise IndexSelectionError("selection_write_failed") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def validate_pinned(self, plan: RunPlan, pinned: PinnedIndexReport) -> None:
        if not isinstance(plan, RunPlan) or not isinstance(pinned, PinnedIndexReport):
            raise IndexSelectionError("index_selection_invalid")
        plan.verify_integrity()
        if (pinned.storage_root_sha256 != self._storage_root_sha256 or
                pinned.document_manifest_hash != plan.document_manifest_hash or
                pinned.index_fingerprint != plan.index_fingerprint or
                pinned.report_reference != f"build-artifacts/{pinned.build_id}/build-report.json" or
                pinned.report_path != self._storage_root / pinned.report_reference):
            raise IndexSelectionError("index_selection_incompatible")
        manifest = self._current_manifest(plan.document_manifest_hash)
        config = self._validate_config(plan.resolved_config())
        candidate, digest, _path = self._candidate_and_digest(config, manifest.content_hash, pinned.build_id)
        if (candidate.expected_index_fingerprint != plan.index_fingerprint or digest != pinned.report_sha256):
            raise IndexSelectionError("index_selection_stale")

    def verify_report_pin(self, plan: RunPlan, pinned: PinnedIndexReport) -> None:
        """Recheck source and report identity immediately before retrieval."""
        self.validate_pinned(plan, pinned)

__all__ = ["IndexSelectionError", "PinnedIndexReport", "IndexSelectionService"]
