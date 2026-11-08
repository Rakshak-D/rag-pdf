import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import type { WorkbenchApiClient } from "../../api/client";
import type { CatalogArtifactRecord, CatalogComparisonRecord, CatalogMetricRecord } from "../../api/types";
import { apiErrorText } from "../../components/apiErrorText";
import { EmptyState } from "../../components/EmptyState";
import { ErrorState } from "../../components/ErrorState";
import { StatusBadge } from "../../components/StatusBadge";
import { displayValue, LoadingState, PageHeading } from "../shared";
import { buildAblationComparisonCsv, buildMetricSummaryCsv } from "./export";

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
  return <section className="panel results-section" aria-label={title}><div className="panel__heading"><div><span className="eyebrow">{eyebrow}</span><h2>{title}</h2></div>{actions}</div>{children}</section>;
}

function countLabel(value: number | null): string { return value === null ? "Not reported" : String(value); }

function statusLabel(status: CatalogArtifactRecord["status"]): string {
  return status === "partial" ? "Partially complete" : status === "succeeded" ? "Succeeded" : "Failed";
}

export function ArtifactDetailPage({ client, connected, refreshKey, onRefresh }: ArtifactDetailPageProps) {
  const { artifactId = "" } = useParams();
  const location = useLocation();
  const routeState = location.state as { resultsReturnTo?: unknown } | null;
  const requestedReturnTo = routeState?.resultsReturnTo;
  const resultsReturnTo = typeof requestedReturnTo === "string" &&
    (requestedReturnTo === "/artifacts" || requestedReturnTo.startsWith("/artifacts?"))
    ? requestedReturnTo : "/artifacts";
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

  return <div className="results-detail-page">
    <PageHeading
      eyebrow="SAVED RESEARCH RESULT"
      title={artifact?.benchmark_id ?? "Result details"}
      description={artifact ? `${artifact.artifact_type === "evaluation_result" ? "Evaluation" : "Ablation"} · benchmark version ${artifact.benchmark_version}` : "Inspect a saved evaluation or ablation artifact."}
      actions={<><button className="button button--quiet" type="button" onClick={onRefresh} disabled={metadataLoading || metricsLoading}>Refresh</button><Link className="button button--quiet" to={resultsReturnTo}>Back to results</Link></>}
    />
    {!connected ? <EmptyState title="Connect to inspect this result" detail="Result metadata and measurements are available from the authenticated local catalog." /> : null}
    {connected && metadataLoading ? <LoadingState label="result metadata" /> : null}
    {connected && metadataError ? <ErrorState title="Result metadata could not be loaded" detail={metadataError} onRetry={onRefresh} /> : null}
    {connected && !metadataLoading && !metadataError && !artifact ? <EmptyState title="Result unavailable" detail="It may no longer be registered in the artifact catalog." /> : null}

    {connected && artifact ? <>
      <section className="results-summary" aria-label="Result outcome">
        <div className="results-summary__status"><span>{artifact.artifact_type === "evaluation_result" ? "Evaluation" : "Ablation"}</span><StatusBadge status={artifact.status} label={statusLabel(artifact.status)} /></div>
        <dl className="results-case-counts">
          <div><dt>Cases recorded</dt><dd>{countLabel(artifact.case_count)}</dd></div>
          <div><dt>Succeeded</dt><dd>{countLabel(artifact.succeeded_case_count)}</dd></div>
          <div><dt>Failed</dt><dd>{countLabel(artifact.failed_case_count)}</dd></div>
        </dl>
      </section>

      <Section title="Recorded measurements" eyebrow="METRIC AGGREGATES" actions={metrics.length > 0 ? <button className="button button--quiet button--small" type="button" onClick={exportMetrics}>Download metrics CSV</button> : null}>
        {metricsLoading ? <LoadingState label="measurements" /> : null}
        {metricsError ? <ErrorState title="Measurements could not be loaded" detail={metricsError} onRetry={onRefresh} /> : null}
        {!metricsLoading && !metricsError && metrics.length === 0 ? <EmptyState title="No metric aggregates" detail="The catalog returned no measurements for this result." /> : null}
        {!metricsLoading && !metricsError && metrics.length > 0 ? <>
          <p className="results-note">Values are shown in their reported units. Direction is the metric's optimization intent; it is not an effect size.</p>
          <div className="results-metric-list" aria-label="Metric aggregates">
            {metrics.map((metric) => <MetricRecord key={`${metric.variant_id}:${metric.metric_id}`} metric={metric} />)}
          </div>
        </> : null}
      </Section>

      {artifact.artifact_type === "ablation_result" ? <Section title="Ablation comparisons" eyebrow="COMPARISON ELIGIBILITY" actions={comparisons.length > 0 ? <button className="button button--quiet button--small" type="button" onClick={exportComparisons}>Download comparison CSV</button> : null}>
        {comparisonsLoading ? <LoadingState label="comparison states" /> : null}
        {comparisonsError ? <ErrorState title="Comparison states could not be loaded" detail={comparisonsError} onRetry={onRefresh} /> : null}
        {!comparisonsLoading && !comparisonsError && comparisons.length === 0 ? <EmptyState title="No comparison states" detail="The catalog returned no comparison eligibility records for this run." /> : null}
        {!comparisonsLoading && !comparisonsError && comparisons.length > 0 ? <>
          <p className="results-note">These states describe whether variants can be compared. They do not report a measured improvement.</p>
          <ul className="results-comparison-list">{comparisons.map((item) => <li key={item.metric_id}>
            <code>{item.metric_id}</code>
            <StatusBadge status={item.state} label={item.state === "not_comparable" ? "Not comparable" : item.state === "incomplete" ? "Incomplete" : "Comparable"} />
            {item.reason_code ? <details><summary>Reason</summary><code>{item.reason_code}</code></details> : null}
          </li>)}</ul>
        </> : null}
      </Section> : null}

      <details className="results-disclosure">
        <summary>Methodology and provenance</summary>
        <dl className="results-provenance-grid">
          <Meta label="Artifact ID" value={artifact.artifact_id} mono />
          <Meta label="Run fingerprint" value={artifact.run_fingerprint} mono />
          <Meta label="Evaluation fingerprint" value={artifact.evaluation_fingerprint} mono />
          <Meta label="Document manifest hash" value={artifact.document_manifest_hash} mono />
          <Meta label="Shared index fingerprint" value={artifact.shared_index_fingerprint} mono />
          <Meta label="Serialization schema" value={artifact.serialization_schema_version} />
          <div className="metadata-item metadata-item--wide"><dt>Catalog reference</dt><dd><code>{artifact.relative_path}</code><small>This is a catalog-relative identifier, not a filesystem link.</small></dd></div>
        </dl>
      </details>

      <details className="results-disclosure">
        <summary>Original result data and exports</summary>
        <div className="results-raw-content">
          <p className="results-note">The full payload is loaded only on request and is limited to 8 MiB in the browser.</p>
          {artifact.byte_length > maximumBrowserExportBytes ? <p className="detail-error" role="status">This stored result exceeds the 8 MiB browser limit. It was not requested.</p> : null}
          {artifact.artifact_type === "ablation_result" ? <details className="results-config-disclosure">
            <summary>Enter the original base configuration to verify this ablation payload</summary>
            <label className="config-input">Original compatible base configuration
              <textarea value={baseConfigText} onChange={(event) => setBaseConfigText(event.target.value)} rows={7} spellCheck={false} placeholder="Paste the original resolved configuration JSON" aria-describedby="ablation-config-help" />
              <small id="ablation-config-help">The backend reconstructs and validates the canonical plan. The artifact manifest identity is supplied automatically.</small>
            </label>
          </details> : null}
          <div className="export-actions">
            <button className="button button--quiet" type="button" onClick={() => void loadResult()} disabled={resultLoading || artifact.byte_length > maximumBrowserExportBytes}>{resultLoading ? "Loading result…" : resultLoaded ? "Reload result JSON" : "Load result JSON"}</button>
            {resultLoaded && resultRawJson ? <button className="button button--primary" type="button" onClick={() => download(`${artifact.artifact_id}.json`, "application/json;charset=utf-8", resultRawJson)}>Download original JSON</button> : null}
            {artifact.byte_length <= maximumBrowserExportBytes ? <span className="results-size-note">Stored size: {artifact.byte_length.toLocaleString()} bytes</span> : null}
          </div>
          {resultLoading ? <LoadingState label="result payload" /> : null}
          {resultError ? <div className="result-error" role="alert">{resultError}</div> : null}
          {exportError ? <div className="result-error" role="alert">{exportError}</div> : null}
          {artifact.artifact_type === "ablation_result" && resultLoaded ? variantOutcomes ? <section className="variant-outcomes" aria-labelledby="variant-outcomes-heading">
            <h3 id="variant-outcomes-heading">Variant outcomes</h3>
            <div className="table-scroll"><table className="data-table"><caption className="sr-only">Persisted outcomes for each canonical ablation variant</caption><thead><tr><th scope="col">Variant</th><th scope="col">Outcome</th><th scope="col">Evaluation result</th><th scope="col">Error code</th><th scope="col">Run fingerprint</th></tr></thead><tbody>{variantOutcomes.map((variant) => <tr key={variant.variant_id}><th scope="row"><code>{variant.variant_id}</code></th><td><StatusBadge status={variant.status} /></td><td>{variant.has_evaluation_result ? "Persisted" : "Unavailable"}</td><td>{variant.error_code ? <code>{variant.error_code}</code> : "—"}</td><td><code className="mono-cell">{variant.run_fingerprint}</code></td></tr>)}</tbody></table></div>
          </section> : <p className="detail-error" role="status">Variant outcomes could not be validated. The full result JSON is available for inspection.</p> : null}
          {resultLoaded ? <pre className="result-json" tabIndex={0} aria-label="Serialized artifact result JSON">{resultRawJson ? JSON.stringify(JSON.parse(resultRawJson) as unknown, null, 2) : JSON.stringify(result, null, 2) ?? "null"}</pre> : null}
          {!resultLoaded && !resultLoading && !resultError ? <EmptyState title="Result payload not loaded" detail="Choose Load result JSON when you need the complete serialized artifact." /> : null}
        </div>
      </details>
    </> : null}
  </div>;
}

function MetricRecord({ metric }: { metric: CatalogMetricRecord }) {
  const direction = metric.optimization_direction === "maximize" ? "Maximize" : metric.optimization_direction === "minimize" ? "Minimize" : "Neutral";
  const aggregateLabel = metric.status === "no_eligible_values" ? "No eligible values" : metric.status === "partial" ? "Partial" : "Computed";
  const model = metric.model_identifier;
  const methodology = metric.methodology_note;
  return <article className="results-metric">
    <header className="results-metric__heading">
      <div><span className="results-metric__identity">{metric.variant_id || "Evaluation aggregate"}</span><h3>{metric.metric_name}</h3><code>{metric.metric_id}</code></div>
      <StatusBadge status={metric.status} label={aggregateLabel} />
    </header>
    <div className="results-metric__value"><strong>{metric.value === null ? "Unavailable" : String(metric.value)}</strong><span>{metric.unit || "Unit unavailable"}</span></div>
    <dl className="results-metric__counts">
      <div><dt>Direction</dt><dd>{direction}</dd></div>
      <div><dt>Eligible</dt><dd>{metric.eligible_count}</dd></div>
      <div><dt>Skipped</dt><dd>{metric.skipped_count}</dd></div>
      <div><dt>Failed</dt><dd>{metric.failed_count}</dd></div>
      <div><dt>Unavailable</dt><dd>{metric.unavailable_count}</dd></div>
    </dl>
    {model || methodology || metric.reference_kind || metric.implementation_version ? <details className="results-method-details">
      <summary>Method and model details</summary>
      <dl>
        <Meta label="Model" value={model} />
        <Meta label="Methodology" value={methodology} />
        <Meta label="Reference kind" value={metric.reference_kind} />
        <Meta label="Implementation version" value={metric.implementation_version} />
      </dl>
    </details> : null}
  </article>;
}
