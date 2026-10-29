"""Read-only compatibility inspection of one configured build report."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Literal

from backend.config.schema import ResolvedRunConfig
from backend.engine.manifest import build_document_manifest
from backend.engine.retrieval import (
    RetrievalReportError,
    RetrievalReportFailed,
    RetrievalReportMismatch,
    RetrievalReportPartial,
    RetrievalReportUnavailable,
    _validate_build_report,
)
from backend.experiments.ablation_plans import AblationPlanError, create_ablation_plan
from backend.experiments.run_plan import RunPlan, build_run_plan


class IndexReadinessInputError(ValueError):
    """Invalid or changed inputs, represented by a bounded code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class IndexReportCompatibility:
    status: Literal["report_match", "mismatch", "unavailable", "indeterminate"]
    reason_code: str
    workflow: Literal["evaluation", "ablation"]
    document_manifest_hash: str
    expected_index_fingerprint: str
    build_id: str | None = None
    report_status: str | None = None
    reported_text_records: int | None = None
    reported_image_records: int | None = None
    observed_text_records: int | None = None
    observed_image_records: int | None = None
    store_checked: bool = False
    check_level: str = "report"


class IndexReadinessService:
    """Inspect only a server-configured source and report; never open Chroma."""

    def __init__(self, source_root: str | Path, storage_root: str | Path, report_path: str | Path) -> None:
        if any(not isinstance(value, (str, Path)) or not Path(value).is_absolute() for value in (source_root, storage_root, report_path)):
            raise ValueError("readiness_paths_invalid")
        self._source_root = Path(source_root)
        self._storage_root = Path(storage_root)
        self._report_path = Path(report_path)

    def check(self, config: ResolvedRunConfig, expected_manifest_hash: str, workflow: str) -> IndexReportCompatibility:
        if workflow not in {"evaluation", "ablation"} or not isinstance(config, ResolvedRunConfig):
            raise IndexReadinessInputError("request_invalid")
        try:
            config = ResolvedRunConfig.model_validate(config.model_dump(mode="python"))
            expected = RunPlan.validate_sha256(expected_manifest_hash)
            if config.reset_collections_on_start or config.workbench_policy.allow_destructive_index_reset or config.workbench_policy.allow_collection_delete:
                raise IndexReadinessInputError("destructive_configuration_forbidden")
            config.validate_for_workbench()
        except IndexReadinessInputError:
            raise
        except Exception as exc:
            raise IndexReadinessInputError("configuration_invalid") from exc
        manifest = build_document_manifest(self._source_root)
        if not manifest.entries:
            raise IndexReadinessInputError("manifest_empty")
        if manifest.content_hash != expected:
            raise IndexReadinessInputError("manifest_changed")
        if workflow == "evaluation":
            plan = build_run_plan(config, expected)
        else:
            try:
                ablation = create_ablation_plan(config, expected)
            except AblationPlanError as exc:
                raise IndexReadinessInputError("ablation_plan_invalid") from exc
            plan = ablation.variants[0].run_plan
            if plan.index_fingerprint != ablation.shared_index_fingerprint:
                raise IndexReadinessInputError("ablation_plan_invalid")
        base = dict(workflow=workflow, document_manifest_hash=expected, expected_index_fingerprint=plan.index_fingerprint)
        try:
            report = _validate_build_report(self._storage_root, self._report_path, plan)
        except RetrievalReportUnavailable:
            return IndexReportCompatibility(status="unavailable", reason_code="report_unavailable", **base)
        except OSError:
            return IndexReportCompatibility(status="unavailable", reason_code="report_unavailable", **base)
        except RetrievalReportMismatch:
            return IndexReportCompatibility(status="mismatch", reason_code="identity_mismatch", **base)
        except RetrievalReportFailed:
            return IndexReportCompatibility(status="mismatch", reason_code="build_failed", **base)
        except RetrievalReportPartial:
            return IndexReportCompatibility(status="mismatch", reason_code="partial_persistence", **base)
        except RetrievalReportError:
            return IndexReportCompatibility(status="indeterminate", reason_code="report_invalid", **base)
        safe_id = report.build_id if re.fullmatch(r"[0-9a-f]{32}", report.build_id) else None
        return IndexReportCompatibility(status="report_match", reason_code="report_matches", build_id=safe_id, report_status=report.status, reported_text_records=report.text_records_persisted, reported_image_records=report.image_records_persisted, **base)
