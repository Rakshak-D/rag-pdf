import type { CatalogArtifactRecord, CatalogComparisonRecord, CatalogMetricRecord } from "../../api/types";

const MAX_EXPORT_BYTES = 8 * 1024 * 1024;

function csvText(value: string | null): string {
  const text = value ?? "";
  const protectedText = /^[\u0000-\u0020]*[=+\-@]/.test(text) ? `'${text}` : text;
  return `"${protectedText.replaceAll('"', '""')}"`;
}

function csvNumber(value: number | null): string {
  if (value === null) return "";
  if (!Number.isFinite(value)) throw new Error("Metric value is not finite.");
  return String(value);
}

function boundedCsv(rows: string[][]): string {
  const csv = `${rows.map((row) => row.join(",")).join("\r\n")}\r\n`;
  if (new TextEncoder().encode(csv).byteLength > MAX_EXPORT_BYTES) throw new Error("CSV export exceeds the 8 MiB browser limit.");
  return csv;
}

export function buildMetricSummaryCsv(artifact: CatalogArtifactRecord, metrics: CatalogMetricRecord[]): string {
  const header = [
    "artifact_id", "artifact_type", "benchmark_id", "benchmark_version", "result_status", "variant_id",
    "run_fingerprint", "evaluation_fingerprint", "document_manifest_hash", "shared_index_fingerprint",
    "metric_id", "metric_name", "value", "unit", "aggregate_status", "optimization_direction",
    "eligible_count", "skipped_count", "failed_count", "unavailable_count", "reference_kind",
    "implementation_version", "model_identifier", "methodology_note",
  ];
  const rows = metrics.map((metric) => [
    csvText(artifact.artifact_id), csvText(artifact.artifact_type), csvText(artifact.benchmark_id),
    csvText(artifact.benchmark_version), csvText(artifact.status), csvText(metric.variant_id),
    csvText(artifact.run_fingerprint), csvText(artifact.evaluation_fingerprint), csvText(artifact.document_manifest_hash),
    csvText(artifact.shared_index_fingerprint),
    csvText(metric.metric_id), csvText(metric.metric_name), csvNumber(metric.value), csvText(metric.unit),
    csvText(metric.status), csvText(metric.optimization_direction), String(metric.eligible_count),
    String(metric.skipped_count), String(metric.failed_count), String(metric.unavailable_count),
    csvText(metric.reference_kind), csvText(metric.implementation_version), csvText(metric.model_identifier),
    csvText(metric.methodology_note),
  ]);
  return boundedCsv([header.map(csvText), ...rows]);
}

export function buildAblationComparisonCsv(artifact: CatalogArtifactRecord, comparisons: CatalogComparisonRecord[]): string {
  const header = ["artifact_id", "artifact_type", "benchmark_id", "benchmark_version", "result_status", "run_fingerprint", "document_manifest_hash", "shared_index_fingerprint", "metric_id", "comparison_state", "reason_code"];
  const rows = comparisons.map((item) => [
    csvText(artifact.artifact_id), csvText(artifact.artifact_type), csvText(artifact.benchmark_id), csvText(artifact.benchmark_version),
    csvText(artifact.status), csvText(artifact.run_fingerprint), csvText(artifact.document_manifest_hash),
    csvText(artifact.shared_index_fingerprint), csvText(item.metric_id), csvText(item.state), csvText(item.reason_code),
  ]);
  return boundedCsv([header.map(csvText), ...rows]);
}

export function metricScalePercent(metric: CatalogMetricRecord, all: CatalogMetricRecord[]): number | null {
  const peers = all.filter((item) => item.metric_id === metric.metric_id && item.unit === metric.unit &&
    item.value !== null && item.status !== "no_eligible_values");
  if (metric.value === null || peers.length < 2) return null;
  const values = peers.map((item) => item.value!);
  const min = Math.min(...values);
  const max = Math.max(...values);
  if (min === max) return null;
  return Math.max(0, Math.min(100, ((metric.value - min) / (max - min)) * 100));
}
