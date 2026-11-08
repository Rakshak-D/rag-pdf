import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import type { WorkbenchApiClient } from "../../api/client";
import type { ArtifactStatus, ArtifactType, CatalogArtifactRecord } from "../../api/types";
import { apiErrorText } from "../../components/apiErrorText";
import { EmptyState } from "../../components/EmptyState";
import { ErrorState } from "../../components/ErrorState";
import { LoadingState, Pagination, PageHeading } from "../shared";

const statuses: ArtifactStatus[] = ["succeeded", "partial", "failed"];
const artifactTypes: ArtifactType[] = ["evaluation_result", "ablation_result"];
const pageSize = 20;
type ArtifactFilters = { artifact_type?: ArtifactType; status?: ArtifactStatus; benchmark_id?: string; benchmark_version?: string; run_fingerprint?: string; index_fingerprint?: string };
type FilterDraft = { type: string; status: string; benchmark: string; version: string; run: string; index: string };

const emptyDraft: FilterDraft = { type: "", status: "", benchmark: "", version: "", run: "", index: "" };

function parseFilters(params: URLSearchParams): ArtifactFilters {
  const filters: ArtifactFilters = {};
  const type = params.get("artifact_type");
  const status = params.get("status");
  if (artifactTypes.includes(type as ArtifactType)) filters.artifact_type = type as ArtifactType;
  if (statuses.includes(status as ArtifactStatus)) filters.status = status as ArtifactStatus;
  for (const [key, param] of [["benchmark_id", "benchmark_id"], ["benchmark_version", "benchmark_version"], ["run_fingerprint", "run_fingerprint"], ["index_fingerprint", "index_fingerprint"]] as const) {
    const value = params.get(param)?.trim();
    if (value) filters[key] = value;
  }
  return filters;
}

function draftFromFilters(filters: ArtifactFilters): FilterDraft {
  return {
    type: filters.artifact_type ?? "", status: filters.status ?? "", benchmark: filters.benchmark_id ?? "",
    version: filters.benchmark_version ?? "", run: filters.run_fingerprint ?? "", index: filters.index_fingerprint ?? "",
  };
}

function offsetFrom(params: URLSearchParams): number {
  const value = Number(params.get("offset") ?? 0);
  return Number.isSafeInteger(value) && value >= 0 && value % pageSize === 0 ? value : 0;
}

function buildParams(draft: FilterDraft): URLSearchParams {
  const params = new URLSearchParams();
  if (artifactTypes.includes(draft.type as ArtifactType)) params.set("artifact_type", draft.type);
  if (statuses.includes(draft.status as ArtifactStatus)) params.set("status", draft.status);
  if (draft.benchmark.trim()) params.set("benchmark_id", draft.benchmark.trim());
  if (draft.version.trim()) params.set("benchmark_version", draft.version.trim());
  if (draft.run.trim()) params.set("run_fingerprint", draft.run.trim());
  if (draft.index.trim()) params.set("index_fingerprint", draft.index.trim());
  return params;
}

function caseSummary(artifact: CatalogArtifactRecord): string {
  if (artifact.case_count === null) return "Case count not reported";
  const succeeded = artifact.succeeded_case_count === null ? "—" : String(artifact.succeeded_case_count);
  const failed = artifact.failed_case_count === null ? "—" : String(artifact.failed_case_count);
  return `${artifact.case_count} cases · ${succeeded} succeeded · ${failed} failed`;
}

function statusLabel(status: ArtifactStatus): string {
  return status === "partial" ? "Partially complete" : status === "succeeded" ? "Succeeded" : "Failed";
}

interface ArtifactsPageProps { client: WorkbenchApiClient; connected: boolean; refreshKey: number; onRefresh: () => void; }

export function ArtifactsPage({ client, connected, refreshKey, onRefresh }: ArtifactsPageProps) {
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo(() => parseFilters(searchParams), [searchParams]);
  const offset = offsetFrom(searchParams);
  const [draft, setDraft] = useState<FilterDraft>(() => draftFromFilters(filters));
  const [records, setRecords] = useState<CatalogArtifactRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const activeFilters = Object.values(filters).filter(Boolean).length;
  const advancedFilterCount = [filters.benchmark_version, filters.run_fingerprint, filters.index_fingerprint].filter(Boolean).length;
  const shown = records.slice(0, pageSize);
  const hasMore = records.length > pageSize;

  useEffect(() => { setDraft(draftFromFilters(filters)); }, [filters]);

  useEffect(() => {
    let active = true;
    if (!connected) {
      setRecords([]); setError(null); setLoading(false);
      return () => { active = false; };
    }
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
    setSearchParams(buildParams(draft));
  }

  function clear() {
    setDraft(emptyDraft);
    setSearchParams(new URLSearchParams());
  }

  function changePage(nextOffset: number) {
    const params = buildParams(draftFromFilters(filters));
    if (nextOffset > 0) params.set("offset", String(nextOffset));
    setSearchParams(params);
  }

  const returnTo = `${location.pathname}${location.search}`;

  return <div className="results-page">
    <PageHeading eyebrow="EVALUATION LIBRARY" title="Results" description="Saved benchmark evaluations and ablation runs, with their recorded measurements." actions={<button className="button button--quiet" type="button" onClick={onRefresh} disabled={loading}>Refresh</button>} />

    <section className="results-intro" aria-label="About results">
      <p>Results are persisted research runs. For answers about your PDFs, open <Link to="/">Chat</Link>.</p>
      <details className="results-research-tools">
        <summary>Research tools</summary>
        <nav aria-label="Research tools">
          <Link to="/experiments/evaluation">Evaluate a benchmark</Link>
          <Link to="/experiments/ablation">Run an ablation</Link>
          <Link to="/jobs">View jobs</Link>
        </nav>
      </details>
    </section>

    <section className="results-catalog" aria-labelledby="saved-results-title" aria-live="polite">
      <header className="results-catalog__heading">
        <div><h2 id="saved-results-title">Saved runs</h2><p>Evaluation and ablation artifacts</p></div>
        {connected && !loading && !error && shown.length > 0 ? <span className="results-page-count">{shown.length} {shown.length === 1 ? "result" : "results"} on this page</span> : null}
      </header>

      <form className="results-filters" onSubmit={apply}>
        <div className="results-filter-row">
          <label>Type<select value={draft.type} onChange={(event) => setDraft({ ...draft, type: event.target.value })}><option value="">All types</option><option value="evaluation_result">Evaluation</option><option value="ablation_result">Ablation</option></select></label>
          <label>Status<select value={draft.status} onChange={(event) => setDraft({ ...draft, status: event.target.value })}><option value="">All statuses</option>{statuses.map((status) => <option key={status} value={status}>{statusLabel(status)}</option>)}</select></label>
          <label className="results-filter-benchmark">Benchmark<input value={draft.benchmark} onChange={(event) => setDraft({ ...draft, benchmark: event.target.value })} maxLength={160} /></label>
          <button className="button button--primary" type="submit">Apply filters</button>
          {activeFilters > 0 ? <button className="button button--quiet" type="button" onClick={clear}>Clear</button> : null}
        </div>
        <details className="results-advanced-filters" open={advancedFilterCount > 0}>
          <summary>Advanced filters{advancedFilterCount > 0 ? ` · ${advancedFilterCount} active` : ""}</summary>
          <div className="results-filter-row results-filter-row--advanced">
            <label>Benchmark version<input value={draft.version} onChange={(event) => setDraft({ ...draft, version: event.target.value })} maxLength={80} /></label>
            <label>Run fingerprint<input value={draft.run} onChange={(event) => setDraft({ ...draft, run: event.target.value })} maxLength={64} spellCheck={false} /></label>
            <label>Index fingerprint<input value={draft.index} onChange={(event) => setDraft({ ...draft, index: event.target.value })} maxLength={64} spellCheck={false} /></label>
          </div>
        </details>
      </form>

      {!connected ? <EmptyState title="Connect to view saved results" detail="The evaluation catalog is available through the authenticated local workspace." /> : null}
      {connected && loading ? <LoadingState label="saved results" /> : null}
      {connected && error ? <ErrorState title="Results could not be loaded" detail={error} onRetry={onRefresh} /> : null}
      {connected && !loading && !error && records.length === 0 && offset > 0 ? <div className="results-out-of-range" role="status">
        <EmptyState
          title="No results on this page"
          detail="This page has no records. Return to the first page to continue browsing."
        />
        <button className="button button--quiet" type="button" onClick={() => changePage(0)}>Back to first page</button>
      </div> : null}
      {connected && !loading && !error && records.length === 0 && offset === 0 ? <EmptyState
        title={activeFilters ? "No results match these filters" : "No saved research results"}
        detail={activeFilters ? "Clear one or more filters to broaden the catalog search." : "Saved evaluation and ablation runs will appear here. Asking questions in Chat does not create evaluation results."}
      /> : null}

      {connected && !loading && !error && shown.length > 0 ? <>
        <ol className="results-list" aria-label="Saved evaluation and ablation results">
          {shown.map((artifact) => <ArtifactEntry key={artifact.artifact_id} artifact={artifact} returnTo={returnTo} />)}
        </ol>
        <Pagination offset={offset} limit={pageSize} count={shown.length} hasMore={hasMore} onPrevious={() => changePage(Math.max(0, offset - pageSize))} onNext={() => changePage(offset + pageSize)} />
      </> : null}
    </section>
  </div>;
}

function ArtifactEntry({ artifact, returnTo }: { artifact: CatalogArtifactRecord; returnTo: string }) {
  const type = artifact.artifact_type === "evaluation_result" ? "Evaluation" : "Ablation";
  const accessibleType = artifact.artifact_type === "evaluation_result" ? "evaluation" : "ablation";
  return <li className="results-entry">
    <div className="results-entry__kind" aria-label={`${type} result`}><span aria-hidden="true">{artifact.artifact_type === "evaluation_result" ? "E" : "A"}</span><span>{type}</span></div>
    <div className="results-entry__identity">
      <h3>{artifact.benchmark_id}</h3>
      <p>Benchmark version {artifact.benchmark_version}</p>
    </div>
    <div className="results-entry__outcome">
      <span className={`results-status results-status--${artifact.status}`}><span aria-hidden="true" />{statusLabel(artifact.status)}</span>
      <p>{caseSummary(artifact)}</p>
    </div>
    <div className="results-entry__action">
      <Link to={`/artifacts/${encodeURIComponent(artifact.artifact_id)}`} state={{ resultsReturnTo: returnTo }} aria-label={`Review metrics for ${artifact.benchmark_id} ${accessibleType}`}>
        Review metrics <span aria-hidden="true">→</span>
      </Link>
    </div>
  </li>;
}
