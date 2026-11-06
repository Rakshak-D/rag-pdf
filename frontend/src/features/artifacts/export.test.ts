import { describe, expect, it } from "vitest";
import type { CatalogArtifactRecord, CatalogComparisonRecord, CatalogMetricRecord } from "../../api/types";
import { buildAblationComparisonCsv, buildMetricSummaryCsv, metricScalePercent } from "./export";

const artifact: CatalogArtifactRecord = {
  artifact_id: "a".repeat(64), artifact_type: "ablation_result", relative_path: "ablation_result/a/result.json",
  byte_length: 128, serialization_schema_version: "1", benchmark_id: "=formula", benchmark_version: "1",
  status: "partial", run_fingerprint: null, evaluation_fingerprint: null, document_manifest_hash: "b".repeat(64),
  shared_index_fingerprint: "c".repeat(64), case_count: 2, succeeded_case_count: 1, failed_case_count: 1,
};

function metric(overrides: Partial<CatalogMetricRecord> = {}): CatalogMetricRecord {
  return {
    artifact_id: artifact.artifact_id, variant_id: "1_Dense_only", metric_id: "M1", metric_name: "Recall at 5",
    status: "computed", value: 0, unit: "ratio", optimization_direction: "maximize", reference_kind: "pages",
    implementation_version: "1", eligible_count: 1, skipped_count: 0, failed_count: 0, unavailable_count: 0,
    model_identifier: null, methodology_note: null, ...overrides,
  };
}

describe("persisted result exports", () => {
  it("exports stable metric rows, keeps zero and null distinct, and protects formula-leading text", () => {
    const csv = buildMetricSummaryCsv(artifact, [
      metric({ methodology_note: "=HYPERLINK(\"unsafe\")" }),
      metric({ variant_id: "2_BM25_only", metric_id: "M2", metric_name: "No value", value: null, status: "no_eligible_values" }),
    ]);
    expect(csv.split("\r\n")[0]).toBe('"artifact_id","artifact_type","benchmark_id","benchmark_version","result_status","variant_id","run_fingerprint","evaluation_fingerprint","document_manifest_hash","shared_index_fingerprint","metric_id","metric_name","value","unit","aggregate_status","optimization_direction","eligible_count","skipped_count","failed_count","unavailable_count","reference_kind","implementation_version","model_identifier","methodology_note"');
    expect(csv).toContain("\"'=formula\"");
    expect(csv).toContain(`"${artifact.shared_index_fingerprint}"`);
    expect(csv).toContain(",0,\"ratio\",\"computed\"");
    expect(csv).toContain(",,\"ratio\",\"no_eligible_values\"");
    expect(csv).toContain("\"'=HYPERLINK(");
  });

  it("exports authoritative comparison states and reasons without deriving deltas", () => {
    const values: CatalogComparisonRecord[] = [
      { artifact_id: artifact.artifact_id, metric_id: "M1", state: "comparable", reason_code: null },
      { artifact_id: artifact.artifact_id, metric_id: "M4", state: "not_comparable", reason_code: "mode_dependent_score_family" },
    ];
    const csv = buildAblationComparisonCsv(artifact, values);
    expect(csv.split("\r\n")[0]).toContain('"run_fingerprint","document_manifest_hash","shared_index_fingerprint"');
    expect(csv).toContain(`"${artifact.artifact_id}","ablation_result"`);
    expect(csv).toContain('"M1","comparable",""');
    expect(csv).toContain('"M4","not_comparable","mode_dependent_score_family"');
    expect(csv).not.toContain("delta");
  });

  it("scales only available numeric values within the same metric and unit", () => {
    const low = metric({ value: 2 });
    const high = metric({ variant_id: "6_Full_pipeline", value: 8 });
    const otherUnit = metric({ variant_id: "other", unit: "count", value: 100 });
    expect(metricScalePercent(low, [low, high, otherUnit])).toBe(0);
    expect(metricScalePercent(high, [low, high, otherUnit])).toBe(100);
    expect(metricScalePercent(metric({ value: null }), [metric({ value: null }), high])).toBeNull();
  });
});
