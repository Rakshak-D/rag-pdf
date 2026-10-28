import type { CatalogArtifactRecord, ExperimentJobRecord, HealthResponse } from "../../api/types";
import { EmptyState } from "../../components/EmptyState";
import { ErrorState } from "../../components/ErrorState";
import { StatusBadge } from "../../components/StatusBadge";

type DataState = "disconnected" | "loading" | "ready" | "error";

interface OverviewPageProps {
  connected: boolean;
  health: HealthResponse | null;
  healthError: string | null;
  dataState: DataState;
  jobs: ExperimentJobRecord[];
  evaluationArtifacts: CatalogArtifactRecord[];
  ablationArtifacts: CatalogArtifactRecord[];
  dataError: string | null;
  refreshing: boolean;
  onRefresh: () => void;
}

function displayDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function JobRow({ job }: { job: ExperimentJobRecord }) {
  return (
    <li className="record-row">
      <div className="record-row__main">
        <span className={`record-kind record-kind--${job.job_type}`}>{job.job_type === "evaluation" ? "EV" : "AB"}</span>
        <div className="record-row__text">
          <strong>{job.benchmark_id}<span className="record-version">v{job.benchmark_version}</span></strong>
          <span className="record-secondary">{job.job_id.slice(0, 8)} · {displayDate(job.created_at)}</span>
        </div>
      </div>
      <StatusBadge status={job.status} />
    </li>
  );
}

function ArtifactRow({ artifact }: { artifact: CatalogArtifactRecord }) {
  const type = artifact.artifact_type === "evaluation_result" ? "Evaluation" : "Ablation";
  const count = artifact.case_count === null ? "Case count unavailable" : `${artifact.case_count} ${artifact.case_count === 1 ? "case" : "cases"}`;
  return (
    <li className="artifact-row">
      <div className="artifact-row__top"><span className="eyebrow">{type}</span><StatusBadge status={artifact.status} /></div>
      <strong className="artifact-row__benchmark">{artifact.benchmark_id}<span className="record-version">v{artifact.benchmark_version}</span></strong>
      <span className="record-secondary">{count} · {formatBytes(artifact.byte_length)}</span>
      <code className="artifact-id" title={artifact.artifact_id}>{artifact.artifact_id.slice(0, 12)}…</code>
    </li>
  );
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function SectionLoading({ label }: { label: string }) {
  return <div className="loading-state" role="status"><span className="loading-spinner" aria-hidden="true" />Loading {label}…</div>;
}

export function OverviewPage({
  connected,
  health,
  healthError,
  dataState,
  jobs,
  evaluationArtifacts,
  ablationArtifacts,
  dataError,
  refreshing,
  onRefresh,
}: OverviewPageProps) {
  const orderedJobs = [...jobs].sort((left, right) => right.created_at.localeCompare(left.created_at));
  const artifacts = [...evaluationArtifacts, ...ablationArtifacts];

  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">OPERATIONS</span>
          <h1>Workbench overview</h1>
          <p className="page-heading__description">A live view of the local experiment runtime and its registered work.</p>
        </div>
        <button className="button button--quiet refresh-button" onClick={onRefresh} disabled={refreshing}>
          <span aria-hidden="true">↻</span>{refreshing ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      <section className="health-strip" aria-label="Backend liveness">
        <div className="health-strip__icon" aria-hidden="true">⌁</div>
        <div className="health-strip__copy">
          <span className="eyebrow">BACKEND LIVENESS</span>
          <strong>{health ? "API process is responding" : healthError ? "API process not reachable" : "Checking backend…"}</strong>
          <small>{health ? `Starlette ${health.api_version} · Liveness only` : healthError ?? "Checking the local /health endpoint"}</small>
        </div>
        <div className="health-strip__status" aria-live="polite">
          {health ? <StatusBadge status="ok" label="Live" /> : healthError ? <StatusBadge status="failed" label="Unavailable" /> : <StatusBadge status="loading" label="Checking" />}
        </div>
      </section>

      <section className="panel" aria-labelledby="jobs-heading">
        <div className="panel__heading">
          <div><span className="eyebrow">PROCESS-LOCAL QUEUE</span><h2 id="jobs-heading">Recent jobs</h2></div>
          {connected && dataState === "ready" ? <span className="count-pill">{jobs.length}</span> : null}
        </div>
        {!connected ? <EmptyState title="Connect to view jobs" detail="Job records are available after the local API token is verified." /> : null}
        {connected && dataState === "loading" ? <SectionLoading label="jobs" /> : null}
        {connected && dataState === "error" ? <ErrorState title="Jobs could not be loaded" detail={dataError ?? "Check the backend connection and refresh."} onRetry={onRefresh} /> : null}
        {connected && dataState === "ready" && orderedJobs.length === 0 ? <EmptyState title="No jobs registered" detail="Submitted evaluations and ablations will appear here." /> : null}
        {connected && dataState === "ready" && orderedJobs.length > 0 ? <ul className="record-list">{orderedJobs.map((job) => <JobRow key={job.job_id} job={job} />)}</ul> : null}
      </section>

      <section className="panel artifacts-panel" aria-labelledby="artifacts-heading">
        <div className="panel__heading">
          <div><span className="eyebrow">CONTENT-ADDRESSED RESULTS</span><h2 id="artifacts-heading">Registered artifacts</h2></div>
          {connected && dataState === "ready" ? <span className="count-pill">{artifacts.length}</span> : null}
        </div>
        <p className="panel__note">Artifact records have no creation timestamp; the catalog does not identify which artifact is newest.</p>
        {!connected ? <EmptyState title="Connect to view artifacts" detail="Evaluation and ablation results are read from the local catalog." /> : null}
        {connected && dataState === "loading" ? <SectionLoading label="artifacts" /> : null}
        {connected && dataState === "error" ? <ErrorState title="Artifacts could not be loaded" detail={dataError ?? "Check the backend connection and refresh."} onRetry={onRefresh} /> : null}
        {connected && dataState === "ready" && artifacts.length === 0 ? <EmptyState title="No result artifacts" detail="Persisted evaluation and ablation results will appear here." /> : null}
        {connected && dataState === "ready" && artifacts.length > 0 ? <ul className="artifact-grid">{artifacts.map((artifact) => <ArtifactRow key={artifact.artifact_id} artifact={artifact} />)}</ul> : null}
      </section>
      <p className="page-footnote">Model status reports adapter state only. It does not indicate index, Ollama, or inference readiness.</p>
    </>
  );
}
