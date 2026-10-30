"""Read-only, explicit-build-ID report preflight for manual runtime handoff."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.api.schemas import parse_json_object
from backend.config.schema import ResolvedRunConfig, WorkbenchSafetyPolicy
from backend.services.index_inventory import (
    IndexInventoryCandidate,
    IndexInventoryInputError,
    IndexInventoryService,
)


_CONFIG_MAX_BYTES = 2 * 1024 * 1024
_INDEX_MANIFEST_MAX_BYTES = 64 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BUILD_ID = re.compile(r"^[0-9a-f]{32}$")


class PreflightError(ValueError):
    """Stable, path-free preflight failure code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _path_has_symlink(path: Path) -> bool:
    if not path.is_absolute() or ".." in path.parts:
        return True
    current = Path(path.anchor)
    try:
        for part in path.parts[1:]:
            current = current / part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode):
                return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise PreflightError("storage_unavailable") from exc
    return False


def _safe_existing_directory(value: str | Path, code: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise PreflightError(code)
    if _path_has_symlink(path):
        raise PreflightError(code)
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_dir():
            raise PreflightError(code)
        return resolved
    except PreflightError:
        raise
    except OSError as exc:
        raise PreflightError(code) from exc


def _read_config(path_value: str | Path) -> ResolvedRunConfig:
    path = Path(path_value).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise PreflightError("configuration_invalid")
    if _path_has_symlink(path):
        raise PreflightError("configuration_invalid")
    descriptor = -1
    try:
        descriptor = os.open(os.fspath(path), os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > _CONFIG_MAX_BYTES:
            raise PreflightError("configuration_invalid")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            raw = stream.read(_CONFIG_MAX_BYTES + 1)
            after = os.fstat(stream.fileno())
        current = path.lstat()
        if (
            len(raw) > _CONFIG_MAX_BYTES
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or after.st_dev != current.st_dev
            or after.st_ino != current.st_ino
            or stat.S_ISLNK(current.st_mode)
        ):
            raise PreflightError("configuration_invalid")
        payload = parse_json_object(raw.decode("utf-8"))
        if set(payload) != set(ResolvedRunConfig.model_fields):
            raise PreflightError("configuration_invalid")
        policy = payload.get("workbench_policy")
        if not isinstance(policy, dict) or set(policy) != set(WorkbenchSafetyPolicy.model_fields):
            raise PreflightError("configuration_invalid")
        config = ResolvedRunConfig.model_validate_json(
            json.dumps(payload, ensure_ascii=False, allow_nan=False), strict=True
        )
        if (
            config.reset_collections_on_start
            or config.workbench_policy.allow_destructive_index_reset
            or config.workbench_policy.allow_collection_delete
        ):
            raise PreflightError("destructive_configuration_forbidden")
        config.validate_for_workbench()
        return config
    except PreflightError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise PreflightError("configuration_invalid") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _lstat_signature(path: Path) -> tuple[int, int, int, int, int, int] | None:
    if _path_has_symlink(path):
        raise PreflightError("index_directory_unsafe")
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except NotADirectoryError as exc:
        raise PreflightError("index_directory_unsafe") from exc
    except OSError as exc:
        raise PreflightError("storage_unavailable") from exc
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read_index_manifest(path: Path) -> tuple[dict[str, Any], tuple[int, int, int, int, int, int]]:
    if _path_has_symlink(path):
        raise PreflightError("index_directory_unsafe")
    descriptor = -1
    try:
        descriptor = os.open(os.fspath(path), os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > _INDEX_MANIFEST_MAX_BYTES:
            raise PreflightError("index_manifest_unavailable")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            raw = stream.read(_INDEX_MANIFEST_MAX_BYTES + 1)
            after = os.fstat(stream.fileno())
        current = path.lstat()
        if (
            len(raw) > _INDEX_MANIFEST_MAX_BYTES
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or after.st_dev != current.st_dev
            or after.st_ino != current.st_ino
            or stat.S_ISLNK(current.st_mode)
        ):
            raise PreflightError("index_changed_during_inspection")
        payload = parse_json_object(raw.decode("utf-8"))
        digest = hashlib.sha256(raw).hexdigest()
        signature = (after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        return {"payload": payload, "digest": digest}, signature
    except PreflightError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise PreflightError("index_manifest_unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _observe_index(storage_root: Path, fingerprint: str, manifest_hash: str) -> tuple[dict[str, str], tuple[Any, ...]]:
    """Read only fixed hierarchy metadata; never opens the Chroma database."""

    indexes = storage_root / "indexes"
    index_root = indexes / fingerprint
    manifest_path = index_root / "index-manifest.json"
    chroma_path = index_root / "chroma"
    marker_path = index_root / ".fresh-build-incomplete"
    lease_namespace = storage_root / ".writer-leases"
    lease_path = lease_namespace / fingerprint

    root_signature = _lstat_signature(index_root)
    if root_signature is None:
        raise PreflightError("index_directory_missing")
    if not stat.S_ISDIR(root_signature[2]):
        raise PreflightError("index_directory_unsafe")

    marker_signature = _lstat_signature(marker_path)
    if marker_signature is not None:
        raise PreflightError("incomplete_marker_present")

    lease_namespace_signature = _lstat_signature(lease_namespace)
    if lease_namespace_signature is not None and not stat.S_ISDIR(lease_namespace_signature[2]):
        raise PreflightError("index_directory_unsafe")
    lease_signature = _lstat_signature(lease_path)
    if lease_signature is not None:
        raise PreflightError("writer_reservation_present")

    manifest_result, manifest_signature = _read_index_manifest(manifest_path)
    payload = manifest_result["payload"]
    from backend.vectorstore.chroma_store import _IndexStoreManifest

    if set(payload) != set(_IndexStoreManifest.model_fields):
        raise PreflightError("index_manifest_mismatch")
    try:
        identity = _IndexStoreManifest.model_validate(payload, strict=True)
    except Exception as exc:
        raise PreflightError("index_manifest_mismatch") from exc
    expected_identity = _IndexStoreManifest(
        index_fingerprint=fingerprint,
        document_manifest_hash=manifest_hash,
    )
    if identity != expected_identity:
        raise PreflightError("index_manifest_mismatch")

    chroma_signature = _lstat_signature(chroma_path)
    if chroma_signature is None or not stat.S_ISDIR(chroma_signature[2]):
        raise PreflightError("chroma_directory_missing")

    observation = {
        "index_directory": "present",
        "index_manifest": "identity_matches",
        "chroma_directory": "present_not_opened",
        "writer_reservation": "absent_at_check",
    }
    signature = (root_signature, manifest_signature, chroma_signature, marker_signature,
                 lease_namespace_signature, lease_signature, manifest_result["digest"])
    return observation, signature


def _candidate_error(candidate: IndexInventoryCandidate) -> str:
    if candidate.compatibility == "mismatch":
        return "report_identity_mismatch"
    if candidate.compatibility == "failed":
        return "report_failed"
    if candidate.compatibility == "incomplete":
        if candidate.reason_code == "fresh_build_incomplete":
            return "incomplete_marker_present"
        if candidate.reason_code == "report_too_large":
            return "report_oversized"
        return "report_partial"
    if candidate.compatibility == "unavailable":
        if candidate.reason_code in {"report_unavailable", "candidate_unavailable"}:
            return "report_missing"
        return "storage_unavailable"
    if candidate.compatibility == "invalid":
        if candidate.reason_code == "index_path_unsafe":
            return "index_directory_unsafe"
        if candidate.reason_code in {"symlink_path", "report_outside_storage", "report_location_invalid"}:
            return "report_path_unsafe"
        if candidate.reason_code == "report_changed_during_inspection":
            return "report_changed_during_inspection"
        return "report_corrupt"
    return "report_corrupt"


def run_preflight(
    *,
    config_path: str | Path,
    source_root: str | Path,
    storage_root: str | Path,
    build_id: str,
    expected_manifest_hash: str,
    expected_index_fingerprint: str,
) -> dict[str, Any]:
    if not config_path:
        raise PreflightError("configuration_missing")
    if not build_id or _BUILD_ID.fullmatch(build_id) is None:
        raise PreflightError("build_id_invalid")
    if not isinstance(expected_manifest_hash, str) or _SHA256.fullmatch(expected_manifest_hash) is None:
        raise PreflightError("manifest_confirmation_invalid")
    if not isinstance(expected_index_fingerprint, str) or _SHA256.fullmatch(expected_index_fingerprint) is None:
        raise PreflightError("index_fingerprint_confirmation_invalid")

    source = _safe_existing_directory(source_root, "source_root_invalid")
    storage = _safe_existing_directory(storage_root, "storage_root_invalid")
    config = _read_config(config_path)
    try:
        service = IndexInventoryService(source, storage)
    except IndexInventoryInputError as exc:
        if exc.code == "inventory_roots_overlap":
            raise PreflightError("source_storage_overlap") from exc
        if exc.code.startswith("source_root"):
            raise PreflightError("source_root_invalid") from exc
        raise PreflightError("storage_root_invalid") from exc

    if source == storage or source in storage.parents or storage in source.parents:
        raise PreflightError("source_storage_overlap")

    initial_report_signature = service._report_file_signature(build_id)
    try:
        candidate = service.inspect_build_id(config, expected_manifest_hash, build_id)
    except IndexInventoryInputError as exc:
        if exc.code == "manifest_changed":
            raise PreflightError("manifest_changed") from exc
        if exc.code in {"manifest_unavailable", "manifest_source_changed"}:
            raise PreflightError("storage_unavailable") from exc
        if exc.code == "manifest_empty":
            raise PreflightError("manifest_changed") from exc
        if exc.code == "configuration_source_mismatch":
            raise PreflightError("source_root_invalid") from exc
        if exc.code == "destructive_configuration_forbidden":
            raise PreflightError("destructive_configuration_forbidden") from exc
        if exc.code == "manifest_hash_invalid":
            raise PreflightError("manifest_confirmation_invalid") from exc
        raise PreflightError("configuration_invalid") from exc

    if candidate.document_manifest_hash != expected_manifest_hash:
        raise PreflightError("manifest_changed")
    if candidate.expected_index_fingerprint != expected_index_fingerprint:
        raise PreflightError("index_fingerprint_changed")
    if candidate.compatibility != "report_match":
        if candidate.compatibility == "unavailable":
            if initial_report_signature is None:
                raise PreflightError("report_missing")
            if initial_report_signature[0] == -2:
                raise PreflightError("storage_unavailable")
            if initial_report_signature[0] >= 0 and not stat.S_ISREG(initial_report_signature[2]):
                raise PreflightError("report_corrupt")
            raise PreflightError("storage_unavailable")
        raise PreflightError(_candidate_error(candidate))
    if candidate.build_id != build_id or candidate.report_reference != f"build-artifacts/{build_id}/build-report.json":
        raise PreflightError("report_path_unsafe")
    if candidate.reported_index_fingerprint != expected_index_fingerprint or candidate.reported_document_manifest_hash != expected_manifest_hash:
        raise PreflightError("report_identity_mismatch")
    if candidate.reported_text_records is None or candidate.reported_image_records is None:
        raise PreflightError("report_corrupt")

    first_observation, first_index_signature = _observe_index(storage, expected_index_fingerprint, expected_manifest_hash)
    # Revalidate the report and source after filesystem metadata inspection.
    try:
        final_candidate = service.inspect_build_id(config, expected_manifest_hash, build_id)
    except IndexInventoryInputError as exc:
        if exc.code in {"manifest_changed", "manifest_source_changed", "manifest_empty"}:
            raise PreflightError("manifest_changed") from exc
        raise PreflightError("storage_unavailable") from exc
    final_report_signature = service._report_file_signature(build_id)
    second_observation, second_index_signature = _observe_index(storage, expected_index_fingerprint, expected_manifest_hash)
    if initial_report_signature != final_report_signature or final_candidate != candidate:
        raise PreflightError("report_changed_during_inspection")
    if first_index_signature != second_index_signature or first_observation != second_observation:
        raise PreflightError("index_changed_during_inspection")
    if final_candidate.compatibility != "report_match":
        raise PreflightError(_candidate_error(final_candidate))

    return {
        "result": "report_compatible_for_manual_review",
        "build_id": build_id,
        "report_reference": candidate.report_reference,
        "report_compatibility": candidate.compatibility,
        "reason_code": candidate.reason_code,
        "document_manifest_hash": candidate.document_manifest_hash,
        "index_fingerprint": candidate.expected_index_fingerprint,
        "reported_text_records": candidate.reported_text_records,
        "reported_image_records": candidate.reported_image_records,
        "report_status": candidate.report_status,
        "check_level": "report",
        "store_checked": False,
        "filesystem_observation": first_observation,
        "evidence_time": "point_in_time",
        "warning": "This does not verify Chroma collections, retrieval, or model readiness; source and storage may change after this command exits.",
        "manual_runtime_steps": [
            "Set RAG_WORKBENCH_INDEX_STORAGE_ROOT to the trusted storage root supplied to this command.",
            "Set RAG_WORKBENCH_BUILD_REPORT_PATH to that root joined with the relative report_reference above.",
            "Restart the ordinary experiment runtime and use its own readiness and retrieval validation.",
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only report compatibility preflight for a manual runtime handoff.")
    parser.add_argument("--config", help="Complete resolved configuration JSON file")
    parser.add_argument("--source", help="Trusted absolute PDF source directory")
    parser.add_argument("--storage-root", help="Trusted absolute index storage directory")
    parser.add_argument("--build-id", help="Explicit 32-character lowercase build ID")
    parser.add_argument("--expected-manifest-hash", help="Confirmed lowercase SHA-256 manifest identity")
    parser.add_argument("--expected-index-fingerprint", help="Confirmed lowercase SHA-256 index identity")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.config:
        print("preflight_status: blocked\nerror_code: configuration_missing", file=sys.stderr)
        return 2
    required = (args.source, args.storage_root, args.build_id, args.expected_manifest_hash, args.expected_index_fingerprint)
    if any(value is None for value in required):
        print("preflight_status: blocked\nerror_code: required_input_missing", file=sys.stderr)
        return 2
    try:
        result = run_preflight(
            config_path=args.config,
            source_root=args.source,
            storage_root=args.storage_root,
            build_id=args.build_id,
            expected_manifest_hash=args.expected_manifest_hash,
            expected_index_fingerprint=args.expected_index_fingerprint,
        )
    except PreflightError as exc:
        print(f"preflight_status: blocked\nerror_code: {exc.code}", file=sys.stderr)
        return 3 if exc.code in {"storage_unavailable", "report_missing", "index_manifest_unavailable"} else 2
    except Exception:
        print("preflight_status: blocked\nerror_code: preflight_unavailable", file=sys.stderr)
        return 3
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
