import { useEffect, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import type { WorkbenchApiClient } from "../../api/client";
import type { CatalogArtifactRecord, CatalogComparisonRecord, CatalogMetricRecord } from "../../api/types";
import { apiErrorText } from "../../components/apiErrorText";
import { EmptyState } from "../../components/EmptyState";
import { ErrorState } from "../../components/ErrorState";
import { StatusBadge } from "../../components/StatusBadge";
import { displayValue, LoadingState, PageHeading } from "../shared";

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
  const [baseConfigText, setBaseConfigText] = useState("");

  useEffect(() => {
    let active = true;
    if (!connected) {
      setArtifact(null); setMetrics([]); setComparisons([]); setResult(undefined); setResultLoaded(false);
      setMetadataError(null); setMetricsError(null); setComparisonsError(null); setResultError(null);
      setMetadataLoading(false); setMetricsLoading(false); setComparisonsLoading(false); setResultLoading(false);
      setBaseConfigText("");
      return () => { active = false; };
    }
    setArtifact(null); setMetadataError(null); setMetadataLoading(true);
    void client.getArtifact(artifactId).then((record) => { if (active) setArtifact(record); }).catch((reason: unknown) => {
      if (active) setMetadataError(apiErrorText(reason));
    }).finally(() => { if (active) setMetadataLoading(false); });
    return () => { active = false; };
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
    setBaseConfigText(""); setResult(undefined); setResultLoaded(false); setResultError(null);
  }, [artifactId]);

  async function loadResult() {
    if (!artifact) return;
    setResultLoading(true); setResultError(null); setResult(undefined); setResultLoaded(false);
    try {
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
      const payload = await client.loadArtifactResult(artifactId, plan);
      setResult(payload); setResultLoaded(true);
    } catch (reason) {
      setResultError(apiErrorText(reason));
    } finally {
      setResultLoading(false);
    }
  }

  return <>
    <PageHeading eyebrow="IMMUTABLE ARTIFACT" title="Artifact details" description="Inspect catalog metadata, indexed metrics, and the stored result when requested." actions={<><button className="button button--quiet" type="button" onClick={onRefresh} disabled={metadataLoading || metricsLoading}>Refresh</button><Link className="button button--quiet" to="/artifacts">Back to artifacts</Link></>} />
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

    {connected ? <Section title="Metric aggregates" eyebrow="INDEXED METRICS">
      {metricsLoading ? <LoadingState label="metric aggregates" /> : null}
      {metricsError ? <ErrorState title="Metrics could not be loaded" detail={metricsError} onRetry={onRefresh} /> : null}
      {!metricsLoading && !metricsError && metrics.length === 0 ? <EmptyState title="No metric aggregates" detail="The catalog returned no metric rows for this artifact." /> : null}
      {!metricsLoading && !metricsError && metrics.length > 0 ? <div className="table-scroll"><table className="data-table metrics-table"><caption className="sr-only">Metric aggregates for this artifact</caption><thead><tr><th scope="col">Metric</th><th scope="col">Value</th><th scope="col">Unit</th><th scope="col">Status</th><th scope="col">Direction</th><th scope="col">Eligible</th><th scope="col">Skipped</th><th scope="col">Failed</th><th scope="col">Unavailable</th><th scope="col">Methodology / model</th></tr></thead><tbody>{metrics.map((metric) => <MetricRow key={`${metric.variant_id}:${metric.metric_id}`} metric={metric} />)}</tbody></table></div> : null}
    </Section> : null}

    {connected && artifact?.artifact_type === "ablation_result" ? <Section title="Ablation comparisons" eyebrow="M1â€“M19 COMPARISON STATES">
        {comparisonsLoading ? <LoadingState label="comparison rows" /> : null}
        {comparisonsError ? <ErrorState title="Comparisons could not be loaded" detail={comparisonsError} onRetry={onRefresh} /> : null}
        {!comparisonsLoading && !comparisonsError && comparisons.length === 0 ? <EmptyState title="No comparison rows" detail="The catalog returned no comparison states for this artifact." /> : null}
        {!comparisonsLoading && !comparisonsError && comparisons.length > 0 ? <div className="table-scroll"><table className="data-table"><caption className="sr-only">Ablation metric comparisons</caption><thead><tr><th scope="col">Metric ID</th><th scope="col">Comparison state</th><th scope="col">Reason code</th></tr></thead><tbody>{comparisons.map((item) => <tr key={item.metric_id}><td><code className="mono-cell">{item.metric_id}</code></td><td><StatusBadge status={item.state} /></td><td>{displayValue(item.reason_code)}</td></tr>)}</tbody></table></div> : null}
    </Section> : null}

    {connected && artifact ? <Section title="Stored result JSON" eyebrow="EXPLICIT PAYLOAD INSPECTION" actions={<button className="button button--primary" type="button" onClick={() => void loadResult()} disabled={resultLoading}>{resultLoading ? "Loading resultâ€¦" : resultLoaded ? "Reload result" : "Load result JSON"}</button>}>
        <p className="panel__note">Result content is fetched only when requested. It may contain evaluation traces and can be large.</p>
        {artifact.artifact_type === "ablation_result" ? <label className="config-input">Original compatible base configuration (JSON object)
          <textarea value={baseConfigText} onChange={(event) => setBaseConfigText(event.target.value)} rows={7} spellCheck={false} placeholder="Paste the original resolved configuration JSON" aria-describedby="ablation-config-help" />
          <small id="ablation-config-help">Required by the backend to reconstruct and verify the canonical plan. The artifact manifest hash is supplied automatically. This input is not sent until you load the result.</small>
        </label> : null}
        {resultLoading ? <LoadingState label="serialized result" /> : null}
        {resultError ? <div className="result-error" role="alert">{resultError}</div> : null}
        {resultLoaded ? <pre className="result-json" tabIndex={0} aria-label="Serialized artifact result JSON">{JSON.stringify(result, null, 2) ?? "null"}</pre> : null}
        {!resultLoaded && !resultLoading && !resultError ? <EmptyState title="Result not loaded" detail="Choose Load result JSON when you are ready to inspect the complete serialized payload." /> : null}
    </Section> : null}
  </>;
}

function MetricRow({ metric }: { metric: CatalogMetricRecord }) {
  const method = [metric.model_identifier, metric.methodology_note].filter((value): value is string => Boolean(value));
  return <tr>
    <td><strong>{metric.metric_name}</strong><span className="table-subtext"><code>{metric.metric_id}</code>{metric.variant_id ? ` Â· ${metric.variant_id}` : " Â· standalone"}</span></td>
    <td className="metric-value">{metric.value === null ? <span className="muted-cell">No numeric value</span> : String(metric.value)}</td>
    <td>{metric.unit}</td><td><StatusBadge status={metric.status} /></td><td>{metric.optimization_direction}</td>
    <td>{metric.eligible_count}</td><td>{metric.skipped_count}</td><td>{metric.failed_count}</td><td>{metric.unavailable_count}</td>
    <td className="methodology-cell">{method.length ? method.join(" Â· ") : "â€”"}</td>
  </tr>;
}
