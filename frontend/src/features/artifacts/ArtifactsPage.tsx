import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import type { WorkbenchApiClient } from "../../api/client";
import type { ArtifactStatus, ArtifactType, CatalogArtifactRecord } from "../../api/types";
import { apiErrorText } from "../../components/apiErrorText";
import { StatusBadge } from "../../components/StatusBadge";
import { displayValue, PageHeading, Pagination, ProtectedState } from "../shared";

const statuses: ArtifactStatus[] = ["succeeded", "partial", "failed"];
const pageSize = 20;
type ArtifactFilters = { artifact_type?: ArtifactType; status?: ArtifactStatus; benchmark_id?: string; benchmark_version?: string; run_fingerprint?: string; index_fingerprint?: string };

function bytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

interface ArtifactsPageProps { client: WorkbenchApiClient; connected: boolean; refreshKey: number; onRefresh: () => void; }

export function ArtifactsPage({ client, connected, refreshKey, onRefresh }: ArtifactsPageProps) {
  const [draft, setDraft] = useState({ type: "", status: "", benchmark: "", version: "", run: "", index: "" });
  const [filters, setFilters] = useState<ArtifactFilters>({});
  const [offset, setOffset] = useState(0);
  const [records, setRecords] = useState<CatalogArtifactRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    if (!connected) { setRecords([]); setError(null); setLoading(false); return () => { active = false; }; }
    setLoading(true); setError(null);
    void client.listArtifacts({ ...filters, limit: pageSize + 1, offset }).then((values) => {
      if (active) setRecords(values);
    }).catch((reason: unknown) => {
      if (active) { setRecords([]); setError(apiErrorText(reason)); }
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [client, connected, filters, offset, refreshKey]);

  function apply(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const next: ArtifactFilters = {};
    if (draft.type) next.artifact_type = draft.type as ArtifactType;
    if (draft.status) next.status = draft.status as ArtifactStatus;
    if (draft.benchmark.trim()) next.benchmark_id = draft.benchmark.trim();
    if (draft.version.trim()) next.benchmark_version = draft.version.trim();
    if (draft.run.trim()) next.run_fingerprint = draft.run.trim();
    if (draft.index.trim()) next.index_fingerprint = draft.index.trim();
    setOffset(0); setFilters(next);
  }

  function clear() {
    setDraft({ type: "", status: "", benchmark: "", version: "", run: "", index: "" });
    setFilters({}); setOffset(0);
  }

  const shown = records.slice(0, pageSize);
  const activeFilters = Object.entries(filters).filter(([, value]) => Boolean(value)).length;

  return <>
    <PageHeading eyebrow="IMMUTABLE RESULT CATALOG" title="Artifacts" description="Filter registered evaluation and ablation results by identity and provenance." actions={<button className="button button--quiet" type="button" onClick={onRefresh} disabled={loading}>Refresh</button>} />
    <section className="panel explorer-panel" aria-labelledby="artifact-filters-heading">
      <div className="panel__heading"><div><span className="eyebrow">CATALOG QUERY</span><h2 id="artifact-filters-heading">Filter artifacts</h2></div><span className="active-filter-note">{activeFilters === 0 ? "No filters" : `${activeFilters} active ${activeFilters === 1 ? "filter" : "filters"}`}</span></div>
      <form className="filter-form artifact-filter-form" onSubmit={apply}>
        <label>Artifact type<select value={draft.type} onChange={(event) => setDraft({ ...draft, type: event.target.value })}><option value="">All types</option><option value="evaluation_result">Evaluation result</option><option value="ablation_result">Ablation result</option></select></label>
        <label>Status<select value={draft.status} onChange={(event) => setDraft({ ...draft, status: event.target.value })}><option value="">All statuses</option>{statuses.map((status) => <option key={status} value={status}>{status}</option>)}</select></label>
        <label>Benchmark ID<input value={draft.benchmark} onChange={(event) => setDraft({ ...draft, benchmark: event.target.value })} /></label>
        <label>Benchmark version<input value={draft.version} onChange={(event) => setDraft({ ...draft, version: event.target.value })} /></label>
        <label>Run fingerprint<input value={draft.run} onChange={(event) => setDraft({ ...draft, run: event.target.value })} /></label>
        <label>Index fingerprint<input value={draft.index} onChange={(event) => setDraft({ ...draft, index: event.target.value })} /></label>
        <div className="filter-actions"><button className="button button--primary" type="submit">Apply filters</button><button className="button button--quiet" type="button" onClick={clear}>Clear filters</button></div>
      </form>
    </section>
    <section className="panel" aria-label="Artifact results" aria-live="polite">
      <div className="panel__heading"><div><span className="eyebrow">RESULTS</span><h2>Artifact records</h2></div>{!loading && connected && !error ? <span className="count-pill">{shown.length}</span> : null}</div>
      <ProtectedState connected={connected} loading={loading} error={error} empty={records.length === 0} emptyDetail={activeFilters ? "No artifacts match these filters. Clear filters to view the complete catalog." : "Registered evaluations and ablations will appear here."} retry={onRefresh} />
      {connected && !loading && !error && shown.length > 0 ? <>
        <div className="table-scroll"><table className="data-table"><caption className="sr-only">Registered experiment artifacts</caption><thead><tr><th scope="col">Artifact</th><th scope="col">Benchmark</th><th scope="col">Status</th><th scope="col">Cases</th><th scope="col">Size</th><th scope="col">Artifact ID</th></tr></thead><tbody>{shown.map((artifact) => <ArtifactRow key={artifact.artifact_id} artifact={artifact} />)}</tbody></table></div>
        <Pagination offset={offset} limit={pageSize} count={shown.length} hasMore={records.length > pageSize} onPrevious={() => setOffset(Math.max(0, offset - pageSize))} onNext={() => setOffset(offset + pageSize)} />
      </> : null}
    </section>
  </>;
}

function ArtifactRow({ artifact }: { artifact: CatalogArtifactRecord }) {
  const type = artifact.artifact_type === "evaluation_result" ? "Evaluation" : "Ablation";
  const counts = artifact.case_count === null ? "Not reported" : `${artifact.case_count} Â· ${displayValue(artifact.succeeded_case_count)} ok Â· ${displayValue(artifact.failed_case_count)} failed`;
  return <tr>
    <td><Link className="record-link" to={`/artifacts/${encodeURIComponent(artifact.artifact_id)}`}><span className={`kind-label kind-label--${artifact.artifact_type === "evaluation_result" ? "evaluation" : "ablation"}`}>{type}</span><span className="table-link-label">Open details</span></Link></td>
    <td><strong>{artifact.benchmark_id}</strong><span className="table-subtext">v{artifact.benchmark_version}</span></td>
    <td><StatusBadge status={artifact.status} /></td>
    <td>{counts}</td>
    <td>{bytes(artifact.byte_length)}</td>
    <td><code className="mono-cell" title={artifact.artifact_id}>{artifact.artifact_id}</code><span className="table-subtext">schema {artifact.serialization_schema_version}</span><span className="table-subtext">Creation timestamp unavailable</span></td>
  </tr>;
}
