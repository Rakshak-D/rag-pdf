"""Bounded, read-only discovery of plan-bound index build reports."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Literal

from backend.config.paths import PathContainmentError, resolve_disjoint_paths
from backend.config.schema import ResolvedRunConfig
from backend.engine.manifest import (
    ManifestSourceChangedError,
    ManifestValidationError,
    build_document_manifest,
)
from backend.engine.retrieval import _BuildReportInspection, _inspect_build_report
from backend.experiments.run_plan import RunPlan, build_run_plan


_BUILD_ID = re.compile(r"^[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DEFAULT_MAX_ENTRIES = 512
_DEFAULT_MAX_REPORT_BYTES = 1024 * 1024

InventoryCompatibility = Literal["report_match", "mismatch", "failed", "incomplete", "invalid", "unavailable"]


class IndexInventoryInputError(ValueError):
    """Bounded, path-free error for invalid trusted inputs or source state."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class IndexInventoryCandidate:
    candidate_id: str
    build_id: str | None
    report_reference: str | None
    compatibility: InventoryCompatibility
    reason_code: str
    expected_index_fingerprint: str
    document_manifest_hash: str
    reported_index_fingerprint: str | None = None
    reported_document_manifest_hash: str | None = None
    report_status: str | None = None
    reported_text_records: int | None = None
    reported_image_records: int | None = None
    check_level: Literal["report"] = "report"
    store_checked: Literal[False] = False


@dataclass(frozen=True)
class IndexInventoryPage:
    expected_index_fingerprint: str
    document_manifest_hash: str
    candidates: tuple[IndexInventoryCandidate, ...]
    total_candidates: int | None
    offset: int
    limit: int
    complete: bool
    reason_code: str
    check_level: Literal["report"] = "report"
    store_checked: Literal[False] = False


def _no_symlink_components(path: Path) -> bool:
    """Return False if any existing component is a symlink, without resolving it."""

    candidate = path.expanduser()
    if not candidate.is_absolute():
        raise IndexInventoryInputError("inventory_path_invalid")
    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return True
        except OSError as exc:
            raise IndexInventoryInputError("inventory_path_unavailable") from exc
        if stat.S_ISLNK(info.st_mode):
            return False
    return True


def _canonical_root(value: str | Path, *, must_exist: bool) -> Path:
    if not isinstance(value, (str, Path)):
        raise IndexInventoryInputError("inventory_path_invalid")
    path = Path(value).expanduser()
    if not path.is_absolute() or ".." in path.parts or not _no_symlink_components(path):
        raise IndexInventoryInputError("inventory_path_invalid")
    resolved = path.resolve(strict=False)
    if must_exist and not resolved.is_dir():
        raise IndexInventoryInputError("source_root_unavailable")
    if not must_exist and resolved.exists() and not resolved.is_dir():
        raise IndexInventoryInputError("storage_root_invalid")
    if not must_exist and not resolved.exists() and not resolved.parent.is_dir():
        raise IndexInventoryInputError("storage_root_unavailable")
    return resolved


def _candidate_id(name: str, build_id: str | None) -> str:
    if build_id is not None:
        return build_id
    return "entry-" + hashlib.sha256(os.fsencode(name)).hexdigest()[:16]


def _candidate(
    *,
    name: str,
    build_id: str | None,
    expected_fingerprint: str,
    manifest_hash: str,
    compatibility: InventoryCompatibility,
    reason_code: str,
    inspection: _BuildReportInspection | None = None,
) -> IndexInventoryCandidate:
    compatible = compatibility == "report_match" and inspection is not None
    return IndexInventoryCandidate(
        candidate_id=_candidate_id(name, build_id),
        build_id=build_id,
        report_reference=f"build-artifacts/{build_id}/build-report.json" if build_id is not None else None,
        compatibility=compatibility,
        reason_code=reason_code,
        expected_index_fingerprint=expected_fingerprint,
        document_manifest_hash=manifest_hash,
        reported_index_fingerprint=inspection.index_fingerprint if inspection else None,
        reported_document_manifest_hash=inspection.document_manifest_hash if inspection else None,
        report_status=inspection.report_status if inspection else None,
        reported_text_records=inspection.text_records_persisted if compatible else None,
        reported_image_records=inspection.image_records_persisted if compatible else None,
    )


class IndexInventoryService:
    """Discover report evidence under trusted roots; never inspect Chroma."""

    def __init__(
        self,
        source_root: str | Path,
        storage_root: str | Path,
        *,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        max_report_bytes: int = _DEFAULT_MAX_REPORT_BYTES,
    ) -> None:
        if isinstance(max_entries, bool) or not isinstance(max_entries, int) or not 1 <= max_entries <= 4096:
            raise ValueError("inventory_limit_invalid")
        if isinstance(max_report_bytes, bool) or not isinstance(max_report_bytes, int) or not 1 <= max_report_bytes <= 16 * 1024 * 1024:
            raise ValueError("inventory_report_limit_invalid")
        self._source_root = _canonical_root(source_root, must_exist=True)
        self._storage_root = _canonical_root(storage_root, must_exist=False)
        try:
            resolve_disjoint_paths(self._source_root, self._storage_root, first_label="source", second_label="index storage")
        except PathContainmentError as exc:
            raise IndexInventoryInputError("inventory_roots_overlap") from exc
        self._max_entries = max_entries
        self._max_report_bytes = max_report_bytes

    def _validate_plan(self, config: ResolvedRunConfig, expected_manifest_hash: str) -> tuple[RunPlan, str]:
        if not isinstance(config, ResolvedRunConfig):
            raise IndexInventoryInputError("configuration_invalid")
        try:
            validated = ResolvedRunConfig.model_validate(config.model_dump(mode="python"), strict=True)
            try:
                expected = RunPlan.validate_sha256(expected_manifest_hash)
            except Exception as exc:
                raise IndexInventoryInputError("manifest_hash_invalid") from exc
            if (validated.reset_collections_on_start
                    or validated.workbench_policy.allow_destructive_index_reset
                    or validated.workbench_policy.allow_collection_delete):
                raise IndexInventoryInputError("destructive_configuration_forbidden")
            validated.validate_for_workbench()
            configured_source = _canonical_root(validated.pdf_dir, must_exist=True)
            if os.path.normcase(str(configured_source)) != os.path.normcase(str(self._source_root)):
                raise IndexInventoryInputError("configuration_source_mismatch")
        except IndexInventoryInputError:
            raise
        except Exception as exc:
            raise IndexInventoryInputError("configuration_invalid") from exc
        try:
            manifest = build_document_manifest(self._source_root)
        except ManifestSourceChangedError as exc:
            raise IndexInventoryInputError("manifest_source_changed") from exc
        except ManifestValidationError as exc:
            raise IndexInventoryInputError("manifest_invalid") from exc
        except OSError as exc:
            raise IndexInventoryInputError("manifest_unavailable") from exc
        if not manifest.entries:
            raise IndexInventoryInputError("manifest_empty")
        if manifest.content_hash != expected:
            raise IndexInventoryInputError("manifest_changed")
        try:
            plan = build_run_plan(validated, expected)
            plan.verify_integrity()
        except Exception as exc:
            raise IndexInventoryInputError("run_plan_invalid") from exc
        return plan, manifest.content_hash

    def _fresh_marker_state(self, fingerprint: str) -> Literal["absent", "present", "unsafe", "invalid", "unavailable"]:
        marker = self._storage_root / "indexes" / fingerprint / ".fresh-build-incomplete"
        current = self._storage_root
        parts = marker.relative_to(self._storage_root).parts
        for index, part in enumerate(parts):
            current = current / part
            try:
                info = current.lstat()
            except FileNotFoundError:
                return "absent"
            except OSError:
                return "unavailable"
            if stat.S_ISLNK(info.st_mode):
                return "unsafe"
            if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
                return "invalid"
        return "present"

    def _inspect_candidate(
        self,
        name: str,
        plan: RunPlan,
        manifest_hash: str,
    ) -> IndexInventoryCandidate:
        build_id = name if _BUILD_ID.fullmatch(name) else None
        if build_id is None:
            return _candidate(name=name, build_id=None, expected_fingerprint=plan.index_fingerprint,
                              manifest_hash=manifest_hash, compatibility="invalid", reason_code="build_id_invalid")
        candidate_dir = self._storage_root / "build-artifacts" / build_id
        report_path = candidate_dir / "build-report.json"
        try:
            candidate_path_safe = _no_symlink_components(candidate_dir)
        except IndexInventoryInputError:
            return _candidate(name=name, build_id=build_id, expected_fingerprint=plan.index_fingerprint,
                              manifest_hash=manifest_hash, compatibility="unavailable", reason_code="candidate_unavailable")
        if not candidate_path_safe:
            return _candidate(name=name, build_id=build_id, expected_fingerprint=plan.index_fingerprint,
                              manifest_hash=manifest_hash, compatibility="invalid", reason_code="symlink_path")
        try:
            directory_info = candidate_dir.lstat()
        except FileNotFoundError:
            return _candidate(name=name, build_id=build_id, expected_fingerprint=plan.index_fingerprint,
                              manifest_hash=manifest_hash, compatibility="unavailable", reason_code="candidate_unavailable")
        except OSError:
            return _candidate(name=name, build_id=build_id, expected_fingerprint=plan.index_fingerprint,
                              manifest_hash=manifest_hash, compatibility="unavailable", reason_code="candidate_unavailable")
        if not stat.S_ISDIR(directory_info.st_mode):
            return _candidate(name=name, build_id=build_id, expected_fingerprint=plan.index_fingerprint,
                              manifest_hash=manifest_hash, compatibility="invalid", reason_code="unexpected_entry_type")
        try:
            inspection = _inspect_build_report(
                self._storage_root,
                report_path,
                plan,
                max_bytes=self._max_report_bytes,
                reject_symlinks=True,
                strict_json=True,
            )
        except (OSError, ValueError):
            return _candidate(name=name, build_id=build_id, expected_fingerprint=plan.index_fingerprint,
                              manifest_hash=manifest_hash, compatibility="unavailable", reason_code="report_unavailable")
        if inspection.index_fingerprint is not None:
            marker = self._fresh_marker_state(inspection.index_fingerprint)
            if marker == "unsafe":
                return _candidate(name=name, build_id=build_id, expected_fingerprint=plan.index_fingerprint,
                                  manifest_hash=manifest_hash, compatibility="invalid", reason_code="symlink_path",
                                  inspection=inspection)
            if marker == "invalid":
                return _candidate(name=name, build_id=build_id, expected_fingerprint=plan.index_fingerprint,
                                  manifest_hash=manifest_hash, compatibility="invalid", reason_code="index_path_invalid",
                                  inspection=inspection)
            if marker == "unavailable":
                return _candidate(name=name, build_id=build_id, expected_fingerprint=plan.index_fingerprint,
                                  manifest_hash=manifest_hash, compatibility="unavailable", reason_code="marker_unavailable",
                                  inspection=inspection)
            if marker == "present":
                return _candidate(name=name, build_id=build_id, expected_fingerprint=plan.index_fingerprint,
                                  manifest_hash=manifest_hash, compatibility="incomplete", reason_code="fresh_build_incomplete",
                                  inspection=inspection)
        if inspection.status == "unavailable":
            return _candidate(name=name, build_id=build_id, expected_fingerprint=plan.index_fingerprint,
                              manifest_hash=manifest_hash, compatibility="unavailable", reason_code=inspection.reason_code)
        if inspection.status == "invalid":
            return _candidate(name=name, build_id=build_id, expected_fingerprint=plan.index_fingerprint,
                              manifest_hash=manifest_hash, compatibility="invalid", reason_code=inspection.reason_code,
                              inspection=inspection)
        if inspection.status == "incomplete":
            return _candidate(name=name, build_id=build_id, expected_fingerprint=plan.index_fingerprint,
                              manifest_hash=manifest_hash, compatibility="incomplete", reason_code=inspection.reason_code,
                              inspection=inspection)
        if inspection.status == "failed":
            return _candidate(name=name, build_id=build_id, expected_fingerprint=plan.index_fingerprint,
                              manifest_hash=manifest_hash, compatibility="failed", reason_code=inspection.reason_code,
                              inspection=inspection)
        if inspection.status == "partial":
            return _candidate(name=name, build_id=build_id, expected_fingerprint=plan.index_fingerprint,
                              manifest_hash=manifest_hash, compatibility="incomplete", reason_code=inspection.reason_code,
                              inspection=inspection)
        if inspection.status == "mismatch":
            return _candidate(name=name, build_id=build_id, expected_fingerprint=plan.index_fingerprint,
                              manifest_hash=manifest_hash, compatibility="mismatch", reason_code=inspection.reason_code,
                              inspection=inspection)
        if inspection.status == "compatible":
            return _candidate(name=name, build_id=build_id, expected_fingerprint=plan.index_fingerprint,
                              manifest_hash=manifest_hash, compatibility="report_match", reason_code="report_matches",
                              inspection=inspection)
        return _candidate(name=name, build_id=build_id, expected_fingerprint=plan.index_fingerprint,
                          manifest_hash=manifest_hash, compatibility="invalid", reason_code="report_invalid",
                          inspection=inspection)

    def discover(
        self,
        config: ResolvedRunConfig,
        expected_manifest_hash: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> IndexInventoryPage:
        """Return a stable page over bounded report candidates.

        Ordering is lexical by build-artifact directory name. It is stable for
        pagination and carries no claim about build creation time or recency.
        """

        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise IndexInventoryInputError("pagination_invalid")
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= self._max_entries:
            raise IndexInventoryInputError("pagination_invalid")
        plan, manifest_hash = self._validate_plan(config, expected_manifest_hash)
        root = self._storage_root
        expected = plan.index_fingerprint
        base = dict(expected_index_fingerprint=expected, document_manifest_hash=manifest_hash,
                    offset=offset, limit=limit)
        artifacts = root / "build-artifacts"
        try:
            artifacts_path_safe = _no_symlink_components(artifacts)
        except IndexInventoryInputError:
            return IndexInventoryPage(candidates=(), total_candidates=None, complete=False,
                                      reason_code="inventory_unavailable", **base)
        if not artifacts_path_safe:
            return IndexInventoryPage(candidates=(), total_candidates=None, complete=False,
                                      reason_code="build_artifacts_symlink", **base)
        try:
            artifacts_info = artifacts.lstat()
        except FileNotFoundError:
            return IndexInventoryPage(candidates=(), total_candidates=0, complete=True,
                                      reason_code="no_build_reports", **base)
        except OSError:
            return IndexInventoryPage(candidates=(), total_candidates=None, complete=False,
                                      reason_code="inventory_unavailable", **base)
        if not stat.S_ISDIR(artifacts_info.st_mode):
            return IndexInventoryPage(candidates=(), total_candidates=None, complete=False,
                                      reason_code="build_artifacts_invalid", **base)
        try:
            names: list[str] = []
            with os.scandir(artifacts) as entries:
                for entry in entries:
                    names.append(entry.name)
                    if len(names) > self._max_entries:
                        return IndexInventoryPage(candidates=(), total_candidates=None, complete=False,
                                                  reason_code="inventory_limit_exceeded", **base)
        except OSError:
            return IndexInventoryPage(candidates=(), total_candidates=None, complete=False,
                                      reason_code="inventory_unavailable", **base)
        try:
            artifacts_path_safe = _no_symlink_components(artifacts)
        except IndexInventoryInputError:
            artifacts_path_safe = False
        if not artifacts_path_safe:
            return IndexInventoryPage(candidates=(), total_candidates=None, complete=False,
                                      reason_code="build_artifacts_changed", **base)
        try:
            final_artifacts_info = artifacts.lstat()
        except OSError:
            return IndexInventoryPage(candidates=(), total_candidates=None, complete=False,
                                      reason_code="build_artifacts_changed", **base)
        if (not stat.S_ISDIR(final_artifacts_info.st_mode)
                or artifacts_info.st_dev != final_artifacts_info.st_dev
                or artifacts_info.st_ino != final_artifacts_info.st_ino):
            return IndexInventoryPage(candidates=(), total_candidates=None, complete=False,
                                      reason_code="build_artifacts_changed", **base)
        names.sort()
        candidates = tuple(self._inspect_candidate(name, plan, manifest_hash) for name in names)
        try:
            final_manifest = build_document_manifest(self._source_root)
        except (ManifestSourceChangedError, ManifestValidationError) as exc:
            raise IndexInventoryInputError("manifest_source_changed") from exc
        except OSError as exc:
            raise IndexInventoryInputError("manifest_unavailable") from exc
        if not final_manifest.entries:
            raise IndexInventoryInputError("manifest_empty")
        if final_manifest.content_hash != manifest_hash:
            raise IndexInventoryInputError("manifest_changed")
        return IndexInventoryPage(
            candidates=candidates[offset:offset + limit],
            total_candidates=len(candidates),
            complete=True,
            reason_code="inventory_complete" if candidates else "no_build_reports",
            **base,
        )


__all__ = [
    "IndexInventoryCandidate",
    "IndexInventoryInputError",
    "IndexInventoryPage",
    "IndexInventoryService",
]
