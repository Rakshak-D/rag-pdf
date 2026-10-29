"""Preview or explicitly execute a confirmed, fresh local index build."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.api.schemas import parse_json_object
from backend.config.paths import resolve_disjoint_paths, resolve_owned_path
from backend.config.schema import ResolvedRunConfig, WorkbenchSafetyPolicy
from backend.engine.manifest import build_document_manifest
from backend.experiments.run_plan import RunPlan, build_run_plan


class BootstrapError(ValueError):
    """A bounded diagnostic that does not include local paths or input."""


@dataclass(frozen=True)
class BootstrapPreview:
    plan: RunPlan
    document_count: int
    index_exists: bool


def _absolute_safe_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise BootstrapError("path_must_be_absolute")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise BootstrapError("symlink_not_allowed")
    return path


def preview(config_path: str | Path, source_root: str | Path, storage_root: str | Path) -> BootstrapPreview:
    """Validate inputs and inspect existing index identity without creating files."""
    try:
        config_file = _absolute_safe_path(config_path)
        source = _absolute_safe_path(source_root)
        storage = _absolute_safe_path(storage_root)
        payload = parse_json_object(config_file.read_text(encoding="utf-8"))
        if set(payload) != set(ResolvedRunConfig.model_fields):
            raise BootstrapError("configuration_incomplete")
        policy = payload.get("workbench_policy")
        if not isinstance(policy, dict) or set(policy) != set(WorkbenchSafetyPolicy.model_fields):
            raise BootstrapError("configuration_incomplete")
        config = ResolvedRunConfig.model_validate_json(json.dumps(payload, allow_nan=False), strict=True)
        if config.reset_collections_on_start or config.workbench_policy.allow_destructive_index_reset or config.workbench_policy.allow_collection_delete:
            raise BootstrapError("destructive_configuration_forbidden")
        config.validate_for_workbench()
        resolve_disjoint_paths(source, storage)
        resolve_disjoint_paths(config.pdf_dir, storage)
        manifest = build_document_manifest(source)
        if not manifest.entries:
            raise BootstrapError("manifest_empty")
        plan = build_run_plan(config, manifest.content_hash)
        index = storage / "indexes" / plan.index_fingerprint
        _absolute_safe_path(index)
        try:
            index.lstat()
            index_exists = True
        except FileNotFoundError:
            index_exists = False
        return BootstrapPreview(plan, len(manifest.entries), index_exists)
    except BootstrapError:
        raise
    except Exception as exc:
        raise BootstrapError("preview_invalid") from exc


def execute_build(config_path: str | Path, source_root: str | Path, storage_root: str | Path, *, expected_manifest_hash: str | None, expected_index_fingerprint: str | None):
    """Run one confirmed fresh build; the builder revalidates under its lease."""
    if expected_manifest_hash is None or expected_index_fingerprint is None:
        raise BootstrapError("confirmations_required")
    try:
        expected_manifest = RunPlan.validate_sha256(expected_manifest_hash)
        expected_index = RunPlan.validate_sha256(expected_index_fingerprint)
    except Exception as exc:
        raise BootstrapError("confirmations_invalid") from exc
    selected = preview(config_path, source_root, storage_root)
    if selected.plan.document_manifest_hash != expected_manifest:
        raise BootstrapError("manifest_changed")
    if selected.plan.index_fingerprint != expected_index:
        raise BootstrapError("index_fingerprint_changed")
    if selected.index_exists:
        raise BootstrapError("index_already_exists")
    from backend.engine.index_builder import FreshIndexExistsError, IndexBuildResult, build_index
    from backend.engine.retrieval import _validate_build_report

    try:
        result = build_index(selected.plan, source_root, storage_root, creation_policy="fresh_only")
        if not isinstance(result, IndexBuildResult) or not result.report_path.is_file():
            raise BootstrapError("build_report_unavailable")
        _absolute_safe_path(result.report_path)
        owned_root = Path(storage_root).expanduser().resolve(strict=False)
        report_path = resolve_owned_path(owned_root, result.report_path)
        relative = report_path.relative_to(owned_root)
        if len(relative.parts) != 3 or relative.parts != ("build-artifacts", result.build_id, "build-report.json") or report_path != result.report_path.resolve(strict=False):
            raise BootstrapError("build_report_invalid")
        stored = json.loads(result.report_path.read_text(encoding="utf-8"))
        if stored != result.to_serializable_dict():
            raise BootstrapError("build_report_mismatch")
        if result.status in {"completed", "completed_with_warnings"}:
            if result.partial_persistence:
                raise BootstrapError("build_report_invalid")
            report = _validate_build_report(storage_root, result.report_path, selected.plan)
            if report.text_records_persisted != result.text_records_persisted or report.image_records_persisted != result.image_records_persisted:
                raise BootstrapError("build_report_mismatch")
            if build_document_manifest(source_root).content_hash != expected_manifest:
                raise BootstrapError("source_changed_after_build")
        return result
    except BootstrapError:
        raise
    except FreshIndexExistsError as exc:
        raise BootstrapError("index_already_exists") from exc
    except Exception as exc:
        raise BootstrapError("build_failed") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preview a plan for a first local index build.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--storage-root", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--expected-manifest-hash")
    parser.add_argument("--expected-index-fingerprint")
    args = parser.parse_args(argv)
    try:
        result = preview(args.config, args.source, args.storage_root)
        print(f"Manifest: {result.plan.document_manifest_hash}")
        print(f"Documents: {result.document_count}")
        print(f"Index fingerprint: {result.plan.index_fingerprint}")
        print(f"Existing index: {'yes' if result.index_exists else 'no'}")
        if args.execute:
            built = execute_build(args.config, args.source, args.storage_root, expected_manifest_hash=args.expected_manifest_hash, expected_index_fingerprint=args.expected_index_fingerprint)
            print(f"Build ID: {built.build_id}")
            print(f"Build status: {built.status}")
            print(f"Extracted pages: {built.extracted_pages}")
            print(f"Text records: {built.text_records_persisted}")
            print(f"Image records: {built.image_records_persisted}")
            print(f"Failure stage: {built.failure_stage or 'none'}")
            print(f"Partial persistence: {'yes' if built.partial_persistence else 'no'}")
            print(f"Build report: {built.report_path}")
            if built.status == "failed":
                print("Build failed; do not configure this report as a successful runtime build.", file=sys.stderr)
                return 2
            print("Select this report manually with RAG_WORKBENCH_BUILD_REPORT_PATH before launching the runtime.")
            return 0
        print("Preview only; no index was built.")
        return 0
    except BootstrapError as exc:
        print(f"Bootstrap error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
