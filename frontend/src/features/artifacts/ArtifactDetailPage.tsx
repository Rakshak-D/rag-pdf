import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import type { WorkbenchApiClient } from "../../api/client";
import type { CatalogArtifactRecord, CatalogComparisonRecord, CatalogMetricRecord } from "../../api/types";
import { apiErrorText } from "../../components/apiErrorText";
import { EmptyState } from "../../components/EmptyState";
import { ErrorState } from "../../components/ErrorState";
import { StatusBadge } from "../../components/StatusBadge";
import { displayValue, LoadingState, PageHeading } from "../shared";
import { buildAblationComparisonCsv, buildMetricSummaryCsv, metricScalePercent } from "./export";

const maximumBrowserExportBytes = 8 * 1024 * 1024;
const canonicalVariantIds = ["1_Dense_only", "2_BM25_only", "3_Hybrid_static", "4_Hybrid_adaptive", "5_Hybrid_reranker", "6_Full_pipeline"] as const;

interface VariantOutcomeView {
  variant_id: typeof canonicalVariantIds[number];
  run_fingerprint: string;
  status: "succeeded" | "partial" | "failed";
  error_code: string | null;
  has_evaluation_result: boolean;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseVariantOutcomes(value: unknown, artifact: CatalogArtifactRecord): VariantOutcomeView[] | null {
  if (!isRecord(value) || value.schema_version !== "1" || value.status !== artifact.status ||
    value.document_manifest_hash !== artifact.document_manifest_hash || value.shared_index_fingerprint !== artifact.shared_index_fingerprint ||
    value.benchmark_id !== artifact.benchmark_id || value.benchmark_version !== artifact.benchmark_version ||
    !Array.isArray(value.variants) || value.variants.length !== canonicalVariantIds.length) return null;
  const outcomes: VariantOutcomeView[] = [];
  for (const [index, item] of value.variants.entries()) {
    const variantId = canonicalVariantIds[index];
    if (!isRecord(item) || Object.keys(item).length !== 5 ||
      !Object.hasOwn(item, "variant_id") || !Object.hasOwn(item, "run_fingerprint") ||
      !Object.hasOwn(item, "status") || !Object.hasOwn(item, "evaluation_result") || !Object.hasOwn(item, "error_code") ||
      !variantId || item.variant_id !== variantId || typeof item.run_fingerprint !== "string" || !/^[0-9a-f]{64}$/.test(item.run_fingerprint) ||
      (item.status !== "succeeded" && item.status !== "partial" && item.status !== "failed") ||
      (item.evaluation_result !== null && !isRecord(item.evaluation_result)) ||
      (item.error_code !== null && (typeof item.error_code !== "string" || !/^[a-z][a-z0-9_.-]{0,63}$/.test(item.error_code))) ||
      (item.evaluation_result === null && item.status !== "failed")) return null;
    outcomes.push({
      variant_id: variantId,
      run_fingerprint: item.run_fingerprint,
      status: item.status,
      error_code: item.error_code,
      has_evaluation_result: item.evaluation_result !== null,
    });
  }
  return outcomes;
}

interface ArtifactDetailPageProps { client: WorkbenchApiClient; connected: boolean; refreshKey: number; onRefresh: () => void; }

function Meta({ label, value, mono = false }: { label: string; value: string | number | null; mono?: boolean }) {
  return <div className="metadata-item"><dt>{label}</dt><dd>{mono ? <code>{displayValue(value)}</code> : displayValue(value)}</dd></div>;
}

function Section({ title, eyebrow, children, actions }: { title: string; eyebrow: string; children: ReactNode; actions?: ReactNode }) {
  return <section className="panel" aria-label={title}><div className="panel__heading"><div><span className="eyebrow">{eyebrow}</span><h2>{title}</h2></div>{actions}</div>{children}</section>;
}

export function ArtifactDetailPage({ client, connected, refreshKey, onRefresh }: ArtifactDetailPageProps) {
  const { artifactId = "" } = useParams();
  const [artifact, setArtifact] = useState<CatalogArtifactRecord | null>(null);
  const [metadataLoading, setMetadataLoading] = useState(false);
  const [metadataError, setMetadataError] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<CatalogMetricRecord[]>([]);
  const [metricsLoading, setMetricsLoading] = useState(false);
  const [metricsError, setMetricsError] = useState<string | null>(null);
  const [comparisons, setComparisons] = useState<CatalogComparisonRecord[]>([]);
  const [comparisonsLoading, setComparisonsLoading] = useState(false);
  const [comparisonsError, setComparisonsError] = useState<string | null>(null);
  const [result, setResult] = useState<unknown>(undefined);
  const [resultLoaded, setResultLoaded] = useState(false);
  const [resultLoading, setResultLoading] = useState(false);
  const [resultError, setResultError] = useState<string | null>(null);
  const [resultRawJson, setResultRawJson] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [baseConfigText, setBaseConfigText] = useState("");
  const resultRequestSerial = useRef(0);
  const variantOutcomes = artifact?.artifact_type === "ablation_result" && resultLoaded ? parseVariantOutcomes(result, artifact) : null;

  useEffect(() => {
    let active = true;
    if (!connected) {
      setArtifact(null); setMetrics([]); setComparisons([]); setResult(undefined); setResultRawJson(null); setResultLoaded(false);
      setMetadataError(null); setMetricsError(null); setComparisonsError(null); setResultError(null);
      setMetadataLoading(false); setMetricsLoading(false); setComparisonsLoading(false); setResultLoading(false);
      setBaseConfigText("");
      return () => { active = false; resultRequestSerial.current += 1; };
    }
    setArtifact(null); setMetadataError(null); setMetadataLoading(true);
    void client.getArtifact(artifactId).then((record) => { if (active) setArtifact(record); }).catch((reason: unknown) => {
      if (active) setMetadataError(apiErrorText(reason));
    }).finally(() => { if (active) setMetadataLoading(false); });
    return () => { active = false; resultRequestSerial.current += 1; };
  }, [client, connected, artifactId, refreshKey]);

  useEffect(() => {
    let active = true;
    if (!connected) { setMetrics([]); setMetricsError(null); setMetricsLoading(false); return () => { active = false; }; }
    setMetrics([]); setMetricsError(null); setMetricsLoading(true);
    void client.listMetrics(artifactId).then((values) => { if (active) setMetrics(values); }).catch((reason: unknown) => {
      if (active) setMetricsError(apiErrorText(reason));
    }).finally(() => { if (active) setMetricsLoading(false); });
    return () => { active = false; };
  }, [client, connected, artifactId, refreshKey]);

  useEffect(() => {
    let active = true;
    if (!connected || artifact?.artifact_type !== "ablation_result") {
      setComparisons([]); setComparisonsError(null); setComparisonsLoading(false);
      return () => { active = false; };
    }
    setComparisons([]); setComparisonsError(null); setComparisonsLoading(true);
    void client.listComparisons(artifactId).then((values) => { if (active) setComparisons(values); }).catch((reason: unknown) => {
      if (active) setComparisonsError(apiErrorText(reason));
    }).finally(() => { if (active) setComparisonsLoading(false); });
    return () => { active = false; };
  }, [client, connected, artifact?.artifact_type, artifactId, refreshKey]);

  useEffect(() => {
    setBaseConfigText(""); setResult(undefined); setResultRawJson(null); setResultLoaded(false); setResultError(null); setExportError(null);
  }, [artifactId]);

  async function loadResult() {
    if (!artifact) return;
    const serial = ++resultRequestSerial.current;
    setResultLoading(true); setResultError(null); setResult(undefined); setResultRawJson(null); setResultLoaded(false); setExportError(null);
    try {
      if (artifact.byte_length > maximumBrowserExportBytes) {
        setResultError("This stored result exceeds the 8 MiB browser viewing limit. It was not requested.");
        return;
      }
      let plan: { base_config: Record<string, unknown>; document_manifest_hash: string } | undefined;
      if (artifact.artifact_type === "ablation_result") {
        let parsed: unknown;
        try { parsed = JSON.parse(baseConfigText) as unknown; }
        catch { setResultError("Configuration must be valid JSON containing an object."); return; }
        if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
          setResultError("Configuration must be a JSON object and must match the original accepted base configuration.");
          return;
        }
        if (!artifact.document_manifest_hash) {
          setResultError("The artifact has no manifest identity for plan-bound loading.");
          return;
        }
        plan = { base_config: parsed as Record<string, unknown>, document_manifest_hash: artifact.document_manifest_hash };
      }
      const stored = await client.loadArtifactResult(artifactId, artifact.artifact_type, plan);
      if (resultRequestSerial.current === serial) {
        setResult(stored.payload); setResultRawJson(stored.raw_json); setResultLoaded(true);
      }
    } catch (reason) {
      if (resultRequestSerial.current === serial) setResultError(apiErrorText(reason));
    } finally {
      if (resultRequestSerial.current === serial) setResultLoading(false);
    }
  }

  function download(name: string, contentType: string, text: string) {
    setExportError(null);
    const blob = new Blob([text], { type: contentType });
    if (blob.size > maximumBrowserExportBytes) {
      setExportError("This export exceeds the 8 MiB browser download limit.");
      return;
    }
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url; anchor.download = name; anchor.hidden = true;
    document.body.append(anchor); anchor.click(); anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  function exportMetrics() {
    if (!artifact) return;
    try { download(`${artifact.artifact_id}-metrics.csv`, "text/csv;charset=utf-8", buildMetricSummaryCsv(artifact, metrics)); }
    catch (error) { setExportError(error instanceof Error ? error.message : "Metric CSV export failed."); }
  }

  function exportComparisons() {
    if (!artifact) return;
    try { download(`${artifact.artifact_id}-comparisons.csv`, "text/csv;charset=utf-8", buildAblationComparisonCsv(artifact, comparisons)); }
    catch (error) { setExportError(error instanceof Error ? error.message : "Comparison CSV export failed."); }
  }

  return <>
    <PageHeading eyebrow="PERSISTED RESEARCH RESULT" title="Result details" description="Inspect stored identity, metric aggregates, comparison states, and the original result payload." actions={<><button className="button button--quiet" type="button" onClick={onRefresh} disabled={metadataLoading || metricsLoading}>Refresh</button><Link className="button button--quiet" to="/artifacts">Back to results</Link></>} />
    {!connected ? <EmptyState title="Connect to inspect this artifact" detail="Artifact metadata and result payloads are protected API data." /> : null}
    {connected && metadataLoading ? <LoadingState label="artifact metadata" /> : null}
    {connected && metadataError ? <ErrorState title="Artifact metadata could not be loaded" detail={metadataError} onRetry={onRefresh} /> : null}
    {connected && !metadataLoading && !metadataError && !artifact ? <EmptyState title="Artifact unavailable" detail="It may not be registered, or its record may have been removed." /> : null}

    {connected && artifact ? <>
      <section className="detail-banner panel">
        <div><span className="eyebrow">{artifact.artifact_type === "evaluation_result" ? "EVALUATION RESULT" : "ABLATION RESULT"}</span><h2>{artifact.benchmark_id}<span className="record-version">v{artifact.benchmark_version}</span></h2><code className="detail-id">{artifact.artifact_id}</code></div>
        <StatusBadge status={artifact.status} />
      </section>
      <Section title="Artifact metadata" eyebrow="CATALOG RECORD">
        <dl className="metadata-grid">
          <Meta label="Artifact type" value={artifact.artifact_type} /><Meta label="Result status" value={artifact.status} /><Meta label="Benchmark" value={artifact.benchmark_id} /><Meta label="Benchmark version" value={artifact.benchmark_version} />
          <Meta label="Case count" value={artifact.case_count} /><Meta label="Succeeded cases" value={artifact.succeeded_case_count} /><Meta label="Failed cases" value={artifact.failed_case_count} /><Meta label="Serialized size" value={`${artifact.byte_length.toLocaleString()} bytes`} />
          <Meta label="Serialization schema" value={artifact.serialization_schema_version} /><Meta label="Run fingerprint" value={artifact.run_fingerprint} mono /><Meta label="Evaluation fingerprint" value={artifact.evaluation_fingerprint} mono />
          <Meta label="Document manifest hash" value={artifact.document_manifest_hash} mono /><Meta label="Shared index fingerprint" value={artifact.shared_index_fingerprint} mono />
          <div className="metadata-item metadata-item--wide"><dt>Store-relative reference</dt><dd><code>{artifact.relative_path}</code><small>Catalog reference only; not a local filesystem link.</small></dd></div>
        </dl>
      </Section>
    </> : null}

    {connected ? <Section title="Metric aggregates" eyebrow="INDEXED METRICS" actions={metrics.length > 0 ? <button className="button button--quiet button--small" type="button" onClick={exportMetrics}>Download metric summary CSV</button> : null}>
      {metricsLoading ? <LoadingState label="metric aggregates" /> : null}
      {metricsError ? <ErrorState title="Metrics could not be loaded" detail={metricsError} onRetry={onRefresh} /> : null}
      {!metricsLoading && !metricsError && metrics.length === 0 ? <EmptyState title="No metric aggregates" detail="The catalog returned no metric rows for this artifact." /> : null}
      {!metricsLoading && !metricsError && metrics.length > 0 ? <><p className="page-context">Values and availability counts come from the catalog. Bars appear only for numeric ablation values sharing a metric and unit; they show within-metric scale, not improvement.</p><div className="table-scroll"><table className="data-table metrics-table"><caption className="sr-only">Metric aggregates for this artifact</caption><thead><tr><th scope="col">Metric</th><th scope="col">Value</th><th scope="col">Unit</th><th scope="col">Status</th><th scope="col">Direction</th><th scope="col">Eligible</th><th scope="col">Skipped</th><th scope="col">Failed</th><th scope="col">Unavailable</th><th scope="col">Methodology / model</th></tr></thead><tbody>{metrics.map((metric) => <MetricRow key={`${metric.variant_id}:${metric.metric_id}`} metric={metric} allMetrics={metrics} showScale={artifact?.artifact_type === "ablation_result"} />)}</tbody></table></div></> : null}
    </Section> : null}

    {connected && artifact?.artifact_type === "ablation_result" ? <Section title="Ablation comparisons" eyebrow="CATALOG COMPARISON STATES" actions={comparisons.length > 0 ? <button className="button button--quiet button--small" type="button" onClick={exportComparisons}>Download comparison CSV</button> : null}>
        {comparisonsLoading ? <LoadingState label="comparison rows" /> : null}
        {comparisonsError ? <ErrorState title="Comparisons could not be loaded" detail={comparisonsError} onRetry={onRefresh} /> : null}
        {!comparisonsLoading && !comparisonsError && comparisons.length === 0 ? <EmptyState title="No comparison rows" detail="The catalog returned no comparison states for this artifact." /> : null}
        {!comparisonsLoading && !comparisonsError && comparisons.length > 0 ? <div className="table-scroll"><table className="data-table"><caption className="sr-only">Ablation metric comparisons</caption><thead><tr><th scope="col">Metric ID</th><th scope="col">Comparison state</th><th scope="col">Reason code</th></tr></thead><tbody>{comparisons.map((item) => <tr key={item.metric_id}><td><code className="mono-cell">{item.metric_id}</code></td><td><StatusBadge status={item.state} /></td><td>{displayValue(item.reason_code)}</td></tr>)}</tbody></table></div> : null}
    </Section> : null}

    {connected && artifact ? <Section title="Stored result JSON" eyebrow="EXPLICIT PAYLOAD INSPECTION" actions={<div className="export-actions"><button className="button button--quiet" type="button" onClick={() => void loadResult()} disabled={resultLoading || artifact.byte_length > maximumBrowserExportBytes}>{resultLoading ? "Loading result…" : resultLoaded ? "Reload result JSON" : "Load result JSON"}</button>{resultLoaded && resultRawJson ? <button className="button button--primary" type="button" onClick={() => download(`${artifact.artifact_id}.json`, "application/json;charset=utf-8", resultRawJson)}>Download original result JSON</button> : null}</div>}>
        <p className="panel__note">The result is fetched only on request, with an 8 MiB browser limit. JSON download preserves the server-returned serialized artifact.</p>
        {artifact.byte_length > maximumBrowserExportBytes ? <p className="detail-error" role="status">Stored result exceeds the 8 MiB browser limit and will not be requested.</p> : null}
        {artifact.artifact_type === "ablation_result" ? <label className="config-input">Original compatible base configuration (JSON object)
          <textarea value={baseConfigText} onChange={(event) => setBaseConfigText(event.target.value)} rows={7} spellCheck={false} placeholder="Paste the original resolved configuration JSON" aria-describedby="ablation-config-help" />
          <small id="ablation-config-help">Required by the backend to reconstruct and verify the canonical plan. The artifact manifest hash is supplied automatically. This input is not sent until you load the result.</small>
        </label> : null}
        {resultLoading ? <LoadingState label="serialized result" /> : null}
        {resultError ? <div className="result-error" role="alert">{resultError}</div> : null}
        {exportError ? <div className="result-error" role="alert">{exportError}</div> : null}
        {artifact.artifact_type === "ablation_result" && resultLoaded ? variantOutcomes ? <section className="variant-outcomes" aria-labelledby="variant-outcomes-heading"><h3 id="variant-outcomes-heading">Variant outcomes</h3><div className="table-scroll"><table className="data-table"><caption className="sr-only">Persisted outcome for each canonical ablation variant</caption><thead><tr><th scope="col">Variant</th><th scope="col">Outcome</th><th scope="col">Evaluation result</th><th scope="col">Error code</th><th scope="col">Run fingerprint</th></tr></thead><tbody>{variantOutcomes.map((variant) => <tr key={variant.variant_id}><th scope="row"><code>{variant.variant_id}</code></th><td><StatusBadge status={variant.status} /></td><td>{variant.has_evaluation_result ? "Persisted" : "Unavailable"}</td><td>{variant.error_code ? <code>{variant.error_code}</code> : "—"}</td><td><code className="mono-cell">{variant.run_fingerprint}</code></td></tr>)}</tbody></table></div></section> : <p className="detail-error" role="status">Variant outcomes could not be validated in this response. Use the complete result JSON below.</p> : null}
        {resultLoaded ? <pre className="result-json" tabIndex={0} aria-label="Serialized artifact result JSON">{resultRawJson ? JSON.stringify(JSON.parse(resultRawJson) as unknown, null, 2) : JSON.stringify(result, null, 2) ?? "null"}</pre> : null}
        {!resultLoaded && !resultLoading && !resultError ? <EmptyState title="Result not loaded" detail="Choose Load result JSON when you are ready to inspect the complete serialized payload." /> : null}
    </Section> : null}
  </>;
}

function MetricRow({ metric, allMetrics, showScale }: { metric: CatalogMetricRecord; allMetrics: CatalogMetricRecord[]; showScale: boolean }) {
  const method = [metric.model_identifier, metric.methodology_note].filter((value): value is string => Boolean(value));
  const scale = showScale ? metricScalePercent(metric, allMetrics) : null;
  return <tr>
    <td><strong>{metric.metric_name}</strong><span className="table-subtext"><code>{metric.metric_id}</code>{metric.variant_id ? ` · ${metric.variant_id}` : " · standalone"}</span></td>
    <td className="metric-value">{metric.value === null ? <span className="muted-cell">No numeric value</span> : String(metric.value)}{scale !== null ? <span className="metric-scale" aria-label={`${metric.variant_id} scaled value ${scale.toFixed(1)} percent`}><span style={{ width: `${scale}%` }} /></span> : null}</td>
    <td>{metric.unit}</td><td><StatusBadge status={metric.status} /></td><td>{metric.optimization_direction}</td>
    <td>{metric.eligible_count}</td><td>{metric.skipped_count}</td><td>{metric.failed_count}</td><td>{metric.unavailable_count}</td>
    <td className="methodology-cell">{method.length ? method.join(" Â· ") : "â€”"}</td>
  </tr>;
}
