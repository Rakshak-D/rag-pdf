import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiErrorText } from "../../components/apiErrorText";
import { StatusBadge } from "../../components/StatusBadge";
import type { ExperimentJobRecord, JobStatus, JobType } from "../../api/types";
import type { WorkbenchApiClient } from "../../api/client";
import { PageHeading, Pagination, ProtectedState, formatTimestamp } from "../shared";

const statuses: JobStatus[] = ["queued", "running", "succeeded", "partial", "failed", "cancelled", "interrupted"];
const pageSize = 20;

interface JobsPageProps { client: WorkbenchApiClient; connected: boolean; refreshKey: number; onRefresh: () => void; }

export function JobsPage({ client, connected, refreshKey, onRefresh }: JobsPageProps) {
  const [typeDraft, setTypeDraft] = useState("");
  const [statusDraft, setStatusDraft] = useState("");
  const [filters, setFilters] = useState<{ job_type?: JobType; status?: JobStatus }>({});
  const [offset, setOffset] = useState(0);
  const [jobs, setJobs] = useState<ExperimentJobRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    if (!connected) {
      setJobs([]); setError(null); setLoading(false);
      return () => { active = false; };
    }
    setLoading(true); setError(null);
    void client.listJobs({ ...filters, limit: pageSize + 1, offset }).then((records) => {
      if (active) setJobs(records);
    }).catch((reason: unknown) => {
      if (active) { setJobs([]); setError(apiErrorText(reason)); }
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [client, connected, filters, offset, refreshKey]);

  const apply = () => {
    setOffset(0);
    setFilters({
      ...(typeDraft ? { job_type: typeDraft as JobType } : {}),
      ...(statusDraft ? { status: statusDraft as JobStatus } : {}),
    });
  };
  const clear = () => { setTypeDraft(""); setStatusDraft(""); setFilters({}); setOffset(0); };
  const shown = jobs.slice(0, pageSize);
  const hasMore = jobs.length > pageSize;

  return <>
    <PageHeading eyebrow="EXECUTION QUEUE" title="Jobs" description="Inspect queued and completed evaluation or ablation work." actions={<button className="button button--quiet" type="button" onClick={onRefresh} disabled={loading}>Refresh</button>} />
    <section className="panel explorer-panel" aria-labelledby="job-filters-heading">
      <div className="panel__heading"><div><span className="eyebrow">CATALOG QUERY</span><h2 id="job-filters-heading">Filter jobs</h2></div><span className="active-filter-note">{filters.job_type ?? "all types"} Â· {filters.status ?? "all statuses"}</span></div>
      <form className="filter-form" onSubmit={(event) => { event.preventDefault(); apply(); }}>
        <label>Job type<select value={typeDraft} onChange={(event) => setTypeDraft(event.target.value)}><option value="">All types</option><option value="evaluation">Evaluation</option><option value="ablation">Ablation</option></select></label>
        <label>Status<select value={statusDraft} onChange={(event) => setStatusDraft(event.target.value)}><option value="">All statuses</option>{statuses.map((status) => <option key={status} value={status}>{status}</option>)}</select></label>
        <button className="button button--primary" type="submit">Apply filters</button>
        <button className="button button--quiet" type="button" onClick={clear}>Clear</button>
      </form>
      <p className="page-context">Showing up to {pageSize} records per page. Filters and ordering follow the local catalog API.</p>
    </section>
    <section className="panel" aria-label="Job results" aria-live="polite">
      <div className="panel__heading"><div><span className="eyebrow">RESULTS</span><h2>Job records</h2></div>{!loading && connected && !error ? <span className="count-pill">{shown.length}</span> : null}</div>
      <ProtectedState connected={connected} loading={loading} error={error} empty={jobs.length === 0} emptyDetail={Object.keys(filters).length ? "No jobs match these filters. Clear them to see the complete catalog." : "Submitted jobs will appear here."} retry={onRefresh} />
      {connected && !loading && !error && shown.length > 0 ? <>
        <div className="table-scroll"><table className="data-table"><caption className="sr-only">Experiment jobs</caption><thead><tr><th scope="col">Type</th><th scope="col">Benchmark</th><th scope="col">Status</th><th scope="col">Created</th><th scope="col">Job ID</th><th scope="col">Artifact</th><th scope="col"><span className="sr-only">Details</span></th></tr></thead><tbody>{shown.map((job) => <tr key={job.job_id}>
          <td><span className={`kind-label kind-label--${job.job_type}`}>{job.job_type}</span></td>
          <td><strong>{job.benchmark_id}</strong><span className="table-subtext">v{job.benchmark_version}</span></td>
          <td><StatusBadge status={job.status} /></td>
          <td>{formatTimestamp(job.created_at)}</td>
          <td><code className="mono-cell">{job.job_id}</code></td>
          <td>{job.artifact_id ? <Link className="inline-link" to={`/artifacts/${encodeURIComponent(job.artifact_id)}`}>Open artifact</Link> : <span className="muted-cell">Not registered</span>}</td>
          <td><Link className="button button--quiet button--small" to={`/jobs/${encodeURIComponent(job.job_id)}`}>Details</Link></td>
        </tr>)}</tbody></table></div>
        <Pagination offset={offset} limit={pageSize} count={shown.length} hasMore={hasMore} onPrevious={() => setOffset(Math.max(0, offset - pageSize))} onNext={() => setOffset(offset + pageSize)} />
      </> : null}
      {connected && !loading && !error && jobs.length === 0 ? <div className="filter-summary">Page {Math.floor(offset / pageSize) + 1} Â· No records on this page</div> : null}
    </section>
  </>;
}
