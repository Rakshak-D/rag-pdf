import { useEffect, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, type WorkbenchApiClient } from "../../api/client";
import type { ExperimentJobRecord } from "../../api/types";
import { apiErrorText } from "../../components/apiErrorText";
import { ErrorState } from "../../components/ErrorState";
import { EmptyState } from "../../components/EmptyState";
import { StatusBadge } from "../../components/StatusBadge";
import { PageHeading, displayValue, formatTimestamp, LoadingState } from "../shared";

interface JobDetailPageProps { client: WorkbenchApiClient; connected: boolean; onRefresh: () => void; }

function Metadata({ label, children }: { label: string; children: ReactNode }) {
  return <div className="metadata-item"><dt>{label}</dt><dd>{children}</dd></div>;
}

export function JobDetailPage({ client, connected, onRefresh }: JobDetailPageProps) {
  const { jobId = "" } = useParams();
  const [job, setJob] = useState<ExperimentJobRecord | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const [requestVersion, setRequestVersion] = useState(0);

  useEffect(() => {
    let active = true;
    if (!connected) { setJob(null); setError(null); setLoading(false); return () => { active = false; }; }
    setJob(null); setError(null); setLoading(true);
    void client.getJob(jobId).then((record) => { if (active) { setJob(record); if (record.status !== "queued") setConfirmCancel(false); } }).catch((reason: unknown) => {
      if (active) setError(apiErrorText(reason));
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [client, connected, jobId, requestVersion]);

  async function cancel() {
    setCancelling(true); setCancelError(null);
    try {
      const updated = await client.cancelJob(jobId);
      setJob(updated); setConfirmCancel(false); onRefresh(); setRequestVersion((value) => value + 1);
    } catch (reason) {
      setCancelError(apiErrorText(reason));
      if (reason instanceof ApiError && reason.code === "job_not_cancellable") setRequestVersion((value) => value + 1);
    } finally { setCancelling(false); }
  }

  return <>
    <PageHeading eyebrow="JOB RECORD" title="Job details" description="A persisted job record and its execution provenance." actions={<><button className="button button--quiet" type="button" onClick={() => setRequestVersion((value) => value + 1)} disabled={loading}>Refresh</button><Link className="button button--quiet" to="/jobs">Back to jobs</Link></>} />
    {!connected ? <EmptyState title="Connect to inspect this job" detail="Protected job metadata is not requested until the API token is verified." /> : null}
    {connected && loading ? <LoadingState label="job" /> : null}
    {connected && error ? <ErrorState title="Job could not be loaded" detail={error} onRetry={() => setRequestVersion((value) => value + 1)} /> : null}
    {connected && !loading && !error && !job ? <EmptyState title="Job unavailable" detail="The job may not exist or may no longer be registered." /> : null}
    {connected && job ? <>
      <section className="detail-banner panel">
        <div><span className="eyebrow">{job.job_type.toUpperCase()} Â· {job.job_id}</span><h2>{job.benchmark_id}<span className="record-version">v{job.benchmark_version}</span></h2></div>
        <StatusBadge status={job.status} />
      </section>
      <section className="panel" aria-labelledby="job-metadata-heading">
        <div className="panel__heading"><div><span className="eyebrow">PERSISTED METADATA</span><h2 id="job-metadata-heading">Execution record</h2></div>
          {job.status === "queued" ? <button className="button button--danger" type="button" disabled={cancelling} onClick={() => { setConfirmCancel(true); setCancelError(null); }}>Cancel queued job</button> : null}
        </div>
        {confirmCancel ? <div className="confirm-box" role="group" aria-label="Confirm job cancellation"><p>This only cancels work that is still queued. Running model calls cannot be interrupted.</p><button className="button button--danger" type="button" disabled={cancelling} onClick={() => void cancel()}>{cancelling ? "Cancellingâ€¦" : "Confirm cancellation"}</button><button className="button button--quiet" type="button" disabled={cancelling} onClick={() => setConfirmCancel(false)}>Keep job</button></div> : null}
        {cancelError ? <div className="detail-error" role="alert">{cancelError}</div> : null}
        <dl className="metadata-grid">
          <Metadata label="Created">{formatTimestamp(job.created_at)}</Metadata><Metadata label="Started">{formatTimestamp(job.started_at)}</Metadata><Metadata label="Finished">{formatTimestamp(job.finished_at)}</Metadata>
          <Metadata label="Job type">{job.job_type}</Metadata><Metadata label="Result status">{displayValue(job.result_status)}</Metadata><Metadata label="Worker error code">{displayValue(job.error_code)}</Metadata>
          <Metadata label="Run fingerprint"><code>{displayValue(job.run_fingerprint)}</code></Metadata><Metadata label="Shared index fingerprint"><code>{displayValue(job.shared_index_fingerprint)}</code></Metadata><Metadata label="Document manifest hash"><code>{displayValue(job.document_manifest_hash)}</code></Metadata>
          <Metadata label="Job ID"><code>{job.job_id}</code></Metadata>
          <Metadata label="Registered artifact">{job.artifact_id ? <Link className="inline-link" to={`/artifacts/${encodeURIComponent(job.artifact_id)}`}>{job.artifact_id}</Link> : "Not registered"}</Metadata>
        </dl>
      </section>
      {job.status !== "queued" && job.status !== "running" ? <p className="page-footnote">This job is terminal. Cancellation is unavailable.</p> : null}
    </> : null}
  </>;
}
