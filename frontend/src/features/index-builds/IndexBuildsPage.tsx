import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, BootstrapApiClient } from "../../api/client";
import type { BootstrapJobRecord, BootstrapPreviewResponse, BootstrapStatusResponse } from "../../api/types";
import { apiErrorText } from "../../components/apiErrorText";
import { StatusBadge } from "../../components/StatusBadge";
import { PageHeading } from "../shared";
import { BootstrapConnectionPanel } from "./BootstrapConnectionPanel";
import { validateResolvedConfig } from "./configValidation";

interface Props {
  token: string | null;
  status: BootstrapStatusResponse | null;
  checking: boolean;
  connectionError: string | null;
  onConnect: (candidate: string) => Promise<boolean>;
  onDisconnect: () => void;
}

const emptyHistory: BootstrapJobRecord[] = [];
const pageSize = 25;

function makeRequestId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  if (typeof crypto === "undefined" || typeof crypto.getRandomValues !== "function") throw new Error("secure_random_unavailable");
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function statusLabel(status: BootstrapJobRecord["status"]): string {
  return ({ queued: "Queued", running: "Running", succeeded: "Succeeded", failed: "Failed", interrupted: "Interrupted" })[status];
}

function statusTone(status: BootstrapJobRecord["status"]): string {
  if (status === "succeeded") return "ready";
  if (status === "failed" || status === "interrupted") return "failed";
  return status === "running" ? "loading" : "unloaded";
}

function JobMetadata({ job }: { job: BootstrapJobRecord }) {
  return <div className="bootstrap-metadata">
    <div><dt>Job ID</dt><dd className="mono-value">{job.job_id}</dd></div>
    <div><dt>Client request ID</dt><dd className="mono-value">{job.client_request_id}</dd></div>
    <div><dt>Document manifest</dt><dd className="mono-value">{job.manifest_hash}</dd></div>
    <div><dt>Index fingerprint</dt><dd className="mono-value">{job.index_fingerprint}</dd></div>
    <div><dt>Created</dt><dd>{job.created_at}</dd></div>
    <div><dt>Started</dt><dd>{job.started_at ?? "Not started"}</dd></div>
    <div><dt>Finished</dt><dd>{job.finished_at ?? "Not finished"}</dd></div>
    <div><dt>Build ID</dt><dd className="mono-value">{job.build_id ?? "Not available"}</dd></div>
    <div><dt>Text records</dt><dd>{job.text_records ?? "Not reported"}</dd></div>
    <div><dt>Image records</dt><dd>{job.image_records ?? "Not reported"}</dd></div>
    <div><dt>Partial persistence</dt><dd>{job.partial_persistence === null ? "Unknown" : job.partial_persistence ? "Yes" : "No"}</dd></div>
    <div><dt>Error code</dt><dd>{job.error_code ?? "None"}</dd></div>
    <div className="bootstrap-metadata__wide"><dt>Build report reference</dt><dd className="mono-value">{job.report_reference ?? "Not available"}</dd></div>
  </div>;
}

export function IndexBuildsPage({ token, status, checking, connectionError, onConnect, onDisconnect }: Props) {
  const { jobId } = useParams();
  const navigate = useNavigate();
  const client = useMemo(() => new BootstrapApiClient({ token }), [token]);
  const [configText, setConfigText] = useState("");
  const validation = useMemo(() => validateResolvedConfig(configText), [configText]);
  const [preview, setPreview] = useState<BootstrapPreviewResponse | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [acceptedJob, setAcceptedJob] = useState<BootstrapJobRecord | null>(null);
  const [uncertainRequestId, setUncertainRequestId] = useState<string | null>(null);
  const [uncertainSearch, setUncertainSearch] = useState<"idle" | "searching" | "not_found" | "found">("idle");
  const [confirmNewIntent, setConfirmNewIntent] = useState(false);
  const [history, setHistory] = useState<BootstrapJobRecord[]>(emptyHistory);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyBusy, setHistoryBusy] = useState(false);
  const [historyOffset, setHistoryOffset] = useState(0);
  const [historyRefresh, setHistoryRefresh] = useState(0);
  const [historyHasNext, setHistoryHasNext] = useState(false);
  const [historyQuery, setHistoryQuery] = useState("");
  const [detail, setDetail] = useState<BootstrapJobRecord | null>(null);
  const [detailBusy, setDetailBusy] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const previewSerial = useRef(0);
  const mounted = useRef(true);
  const currentToken = useRef(token);
  const currentConfig = useRef(configText);
  const submitLock = useRef(false);
  currentToken.current = token;
  currentConfig.current = configText;

  useEffect(() => {
    previewSerial.current += 1;
    setPreview(null);
    setAcknowledged(false);
    setPreviewing(false);
    setActionError(null);
    if (!token) {
      setConfigText("");
      setAcceptedJob(null);
      setUncertainRequestId(null);
      setUncertainSearch("idle");
      setConfirmNewIntent(false);
      setHistory([]);
      setDetail(null);
    }
  }, [token]);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; previewSerial.current += 1; };
  }, []);

  useEffect(() => {
    let active = true;
    if (!token) {
      setHistory([]); setHistoryError(null); setHistoryBusy(false);
      return () => { active = false; };
    }
    setHistoryBusy(true); setHistoryError(null);
    void client.listJobs({ limit: pageSize, offset: historyOffset }).then((response) => {
      if (!active) return;
      setHistory(response.jobs);
      setHistoryHasNext(response.jobs.length === pageSize);
    }).catch((error: unknown) => {
      if (active) setHistoryError(apiErrorText(error));
    }).finally(() => { if (active) setHistoryBusy(false); });
    return () => { active = false; };
  }, [client, historyOffset, historyRefresh, token]);

  useEffect(() => {
    let active = true;
    if (!token || !jobId) {
      setDetail(null); setDetailError(null); setDetailBusy(false);
      return () => { active = false; };
    }
    setDetailBusy(true); setDetailError(null);
    void client.getJob(jobId).then((job) => { if (active) setDetail(job); }).catch((error: unknown) => {
      if (active) { setDetail(null); setDetailError(apiErrorText(error)); }
    }).finally(() => { if (active) setDetailBusy(false); });
    return () => { active = false; };
  }, [client, historyRefresh, jobId, token]);

  function editConfig(value: string) {
    currentConfig.current = value;
    previewSerial.current += 1;
    setConfigText(value);
    setPreview(null);
    setAcknowledged(false);
    setPreviewing(false);
    setActionError(null);
  }

  async function requestPreview() {
    if (!token || !validation.value) return;
    const serial = ++previewSerial.current;
    const capturedToken = token;
    const capturedConfig = configText;
    setPreview(null); setAcknowledged(false); setActionError(null); setPreviewing(true);
    try {
      const result = await client.preview({ resolved_config: validation.value });
      if (previewSerial.current === serial && currentToken.current === capturedToken && currentConfig.current === capturedConfig) setPreview(result);
    } catch (error) {
      if (previewSerial.current === serial && currentToken.current === capturedToken && currentConfig.current === capturedConfig) setActionError(apiErrorText(error));
    } finally {
      if (previewSerial.current === serial && currentToken.current === capturedToken) setPreviewing(false);
    }
  }

  async function submitBuild() {
    if (submitLock.current || !token || !preview || !validation.value || !acknowledged || !preview.fresh_build_eligible || !status?.accepting_jobs || uncertainRequestId) return;
    submitLock.current = true;
    const capturedToken = token;
    const capturedConfig = configText;
    setSubmitting(true); setActionError(null); setAcceptedJob(null);
    let requestId: string | null = null;
    try {
      requestId = makeRequestId();
      const response = await client.submitJob({
        resolved_config: validation.value,
        expected_manifest_hash: preview.manifest_hash,
        expected_index_fingerprint: preview.index_fingerprint,
        client_request_id: requestId,
      });
      if (!mounted.current || currentToken.current !== capturedToken || currentConfig.current !== capturedConfig) return;
      setAcceptedJob(response.job);
      setHistoryOffset(0); setHistoryRefresh((value) => value + 1);
      navigate(`/index-builds/${response.job.job_id}`);
    } catch (error) {
      if (!mounted.current || currentToken.current !== capturedToken) return;
      if (error instanceof Error && error.message === "secure_random_unavailable") {
        setActionError("This browser does not provide a secure random ID generator. Use a modern browser before submitting a build.");
        return;
      }
      setActionError(apiErrorText(error));
      if (error instanceof ApiError && ["manifest_changed", "index_fingerprint_changed", "confirmation_changed", "index_already_exists", "fresh_index_exists"].includes(error.code)) {
        setPreview(null);
        setAcknowledged(false);
      }
      if (error instanceof ApiError && (
        error.kind === "network" || error.kind === "non_json" || error.kind === "malformed_response" ||
        (error.status === 503 && ["job_admission_failed", "job_store_unavailable"].includes(error.code))
      )) {
        if (requestId) setUncertainRequestId(requestId);
        setUncertainSearch("idle");
        setConfirmNewIntent(false);
      }
    } finally {
      submitLock.current = false;
      if (mounted.current && currentToken.current === capturedToken) setSubmitting(false);
    }
  }

  async function inspectUncertainRequest() {
    if (!client || !uncertainRequestId || uncertainSearch === "searching") return;
    const capturedToken = token;
    setUncertainSearch("searching"); setActionError(null);
    try {
      const pageLimit = 100;
      const maximumPages = 100;
      for (let page = 0; page < maximumPages; page += 1) {
        const response = await client.listJobs({ limit: pageLimit, offset: page * pageLimit });
        if (!mounted.current || currentToken.current !== capturedToken) return;
        const match = response.jobs.find((job) => job.client_request_id === uncertainRequestId);
        if (match) {
          setUncertainSearch("found"); setAcceptedJob(match); setHistoryOffset(Math.floor((page * pageLimit) / pageSize) * pageSize);
          navigate(`/index-builds/${match.job_id}`);
          return;
        }
        if (response.jobs.length < pageLimit) { setUncertainSearch("not_found"); return; }
      }
      setUncertainSearch("not_found");
      setActionError("The bounded history scan checked the first 10,000 jobs. The request may be older; review history pages before deciding whether to start a new build.");
    } catch (error) {
      if (!mounted.current || currentToken.current !== capturedToken) return;
      setUncertainSearch("idle"); setActionError(apiErrorText(error));
    }
  }

  const matchingHistory = historyQuery.trim()
    ? history.filter((job) => `${job.client_request_id} ${job.job_id} ${job.status}`.toLowerCase().includes(historyQuery.trim().toLowerCase()))
    : history;
  const canSubmit = Boolean(token && preview?.fresh_build_eligible && validation.value && acknowledged && status?.accepting_jobs && !submitting && !uncertainRequestId);

  return <div className="index-builds-page">
    <PageHeading eyebrow="FRESH-ONLY OPERATIONS" title={jobId ? "Index build details" : "Index Builds"}
      description="Preview the trusted local PDF source, confirm its server-computed identity, and inspect fresh-only build jobs." />
    <BootstrapConnectionPanel connected={token !== null} checking={checking} busy={submitting} status={status} error={connectionError} onConnect={onConnect} onDisconnect={onDisconnect} />

    {!jobId ? <>
      <section className="panel bootstrap-panel" aria-labelledby="bootstrap-preview-title">
        <div className="bootstrap-step"><span>01</span><div><span className="eyebrow">CONFIGURATION</span><h2 id="bootstrap-preview-title">Provide a complete resolved configuration</h2></div></div>
        <p className="muted">Use the full resolved configuration from your trusted local workflow. The browser checks JSON shape and destructive flags; the backend remains authoritative for values and safety.</p>
        <label className="form-field" htmlFor="bootstrap-config">Resolved configuration JSON
          <textarea id="bootstrap-config" className="bootstrap-config" rows={14} spellCheck={false} value={configText} onChange={(event) => editConfig(event.target.value)} disabled={!token || submitting} placeholder="Paste the complete ResolvedRunConfig JSON object" aria-describedby="bootstrap-config-help" />
          <small id="bootstrap-config-help">Configuration remains in page memory and is cleared when bootstrap is disconnected. No defaults are inserted.</small>
        </label>
        {validation.errors.length > 0 && configText.trim() ? <div className="validation-summary" role="alert"><strong>Configuration needs attention</strong><ul>{validation.errors.map((error) => <li key={error}>{error}</li>)}</ul></div> : null}
        <div className="bootstrap-actions">
          <button className="button button--primary" type="button" onClick={() => void requestPreview()} disabled={!token || !validation.value || previewing}>
            {previewing ? "Checking source…" : "Preview index build"}
          </button>
          {previewing ? <span role="status">Reading the current manifest and index identity.</span> : null}
        </div>
        {actionError ? <p className="result-error" role="alert">{actionError}</p> : null}
      </section>

      {preview ? <section className="panel bootstrap-panel" aria-labelledby="bootstrap-identity-title">
        <div className="bootstrap-step"><span>02</span><div><span className="eyebrow">POINT-IN-TIME PREVIEW</span><h2 id="bootstrap-identity-title">Server-computed build identity</h2></div></div>
        <div className="bootstrap-preview-grid">
          <div><span>PDF documents</span><strong>{preview.document_count}</strong></div>
          <div><span>Fresh-build eligibility</span><strong className={preview.fresh_build_eligible ? "identity-positive" : "identity-negative"}>{preview.fresh_build_eligible ? "Eligible to submit" : "Existing index blocks fresh build"}</strong></div>
          <div className="bootstrap-identity-wide"><span>Document manifest hash</span><code>{preview.manifest_hash}</code></div>
          <div className="bootstrap-identity-wide"><span>Index fingerprint</span><code>{preview.index_fingerprint}</code></div>
          <div className="bootstrap-identity-wide"><span>Run fingerprint</span><code>{preview.run_fingerprint}</code></div>
          <div><span>Fingerprint-owned index exists</span><strong>{preview.existing_index ? "Yes" : "No"}</strong></div>
          <div><span>Reason</span><strong className="mono-value">{preview.reason_code}</strong></div>
        </div>
        <p className="bootstrap-note">Preview is a point-in-time observation. It does not reserve the fingerprint or guarantee that execution will be admitted.</p>
        {preview.existing_index ? <p className="result-error" role="alert">An index directory already exists for this fingerprint. Fresh-only submission is disabled. Existing or partial data is never repaired or overwritten here.</p> : null}
        <label className="bootstrap-ack"><input type="checkbox" checked={acknowledged} disabled={!preview.fresh_build_eligible || !token} onChange={(event) => setAcknowledged(event.target.checked)} />
          I explicitly confirm manifest <code>{preview.manifest_hash}</code> and index fingerprint <code>{preview.index_fingerprint}</code> for one fresh-only build.
        </label>
        <div className="bootstrap-actions">
          <button className="button button--primary" type="button" onClick={() => void submitBuild()} disabled={!canSubmit}>{submitting ? "Submitting once…" : "Start fresh build"}</button>
          {status && !status.accepting_jobs ? <span role="status">The bootstrap service is not accepting new jobs.</span> : null}
        </div>
      </section> : null}

      {uncertainRequestId ? <section className="panel bootstrap-warning" aria-labelledby="uncertain-title">
        <span className="eyebrow">SUBMISSION RESPONSE UNCERTAIN</span><h2 id="uncertain-title">The server may have accepted this request</h2>
        <p>Request ID: <code>{uncertainRequestId}</code>. No automatic retry was made. Search the durable job history before deciding on a new build.</p>
        <div className="bootstrap-actions">
          <button className="button button--quiet" type="button" onClick={() => void inspectUncertainRequest()} disabled={uncertainSearch === "searching" || !token}>{uncertainSearch === "searching" ? "Searching job history…" : "Find request ID in job history"}</button>
          {uncertainSearch === "not_found" ? <label className="bootstrap-ack bootstrap-ack--compact"><input type="checkbox" checked={confirmNewIntent} onChange={(event) => setConfirmNewIntent(event.target.checked)} />I reviewed the checked history and explicitly intend a new request despite possible uncertainty.</label> : null}
          {uncertainSearch === "not_found" && confirmNewIntent ? <button className="button button--danger" type="button" onClick={() => { setUncertainRequestId(null); setUncertainSearch("idle"); setConfirmNewIntent(false); setActionError(null); }}>Allow a new explicit build request</button> : null}
        </div>
        {uncertainSearch === "found" ? <p role="status">The original request was found. Its job detail is open below.</p> : null}
      </section> : null}

      {acceptedJob ? <section className="panel bootstrap-accepted" aria-label="Accepted build job">
        <div><span className="eyebrow">JOB ACCEPTED · NOT BUILD COMPLETION</span><h2>{acceptedJob.job_id}</h2><p>Client request ID: <code>{acceptedJob.client_request_id}</code></p></div>
        <StatusBadge status={statusTone(acceptedJob.status)} label={statusLabel(acceptedJob.status)} />
        <Link className="button button--quiet" to={`/index-builds/${acceptedJob.job_id}`}>Open job details</Link>
      </section> : null}
    </> : <section className="panel bootstrap-panel" aria-labelledby="bootstrap-job-title">
      <div className="bootstrap-detail-heading"><div><span className="eyebrow">DURABLE JOB METADATA</span><h2 id="bootstrap-job-title">Build job</h2></div><div className="bootstrap-actions"><button className="button button--quiet" type="button" onClick={() => setHistoryRefresh((value) => value + 1)} disabled={!token || detailBusy}>Refresh job</button><Link className="button button--quiet" to="/index-builds">Back to Index Builds</Link></div></div>
      {detailBusy ? <p role="status">Loading job record…</p> : null}
      {detailError ? <p className="result-error" role="alert">{detailError}</p> : null}
      {detail ? <>
        <div className="bootstrap-job-status"><StatusBadge status={statusTone(detail.status)} label={statusLabel(detail.status)} /><span>{detail.status === "running" ? "The local build worker is executing this fresh-only job." : detail.status === "queued" ? "Accepted and waiting for the single local worker." : detail.status === "succeeded" ? "The builder published a report and the manager validated the result." : detail.status === "failed" ? "The build failed; any partial index evidence is preserved." : "This job was interrupted and will not be replayed automatically."}</span></div>
        <JobMetadata job={detail} />
        {detail.status === "succeeded" ? <div className="bootstrap-success-note"><strong>Manual runtime activation</strong><p>This does not activate an index or start an evaluation. Set the ordinary runtime's existing build-report setting to this report, resolved beneath your trusted index storage root:</p><code>{detail.report_reference}</code><p>Restart the experiment runtime and perform its normal manifest and report checks. A successful build record does not guarantee retrieval or model execution will succeed.</p></div> : null}
        {detail.status === "failed" || detail.status === "interrupted" || detail.partial_persistence === true ? <p className="result-error" role="alert">Failed or partial index data is preserved. This interface does not delete, repair, resume, cancel, or retry builds.</p> : null}
      </> : null}
    </section>}

    <section className="panel bootstrap-panel" aria-labelledby="bootstrap-history-title">
      <div className="bootstrap-detail-heading"><div><span className="eyebrow">SERVER-SIDE HISTORY</span><h2 id="bootstrap-history-title">Recent build jobs</h2></div><button className="button button--quiet" type="button" onClick={() => setHistoryRefresh((value) => value + 1)} disabled={!token || historyBusy}>{historyBusy ? "Refreshing…" : "Refresh history"}</button></div>
      {token ? <>
        <label className="form-field bootstrap-search" htmlFor="bootstrap-job-search">Filter this page by request ID, job ID, or status<input id="bootstrap-job-search" value={historyQuery} onChange={(event) => setHistoryQuery(event.target.value)} /></label>
        {historyError ? <p className="result-error" role="alert">{historyError}</p> : null}
        {historyBusy ? <p role="status">Loading job history…</p> : matchingHistory.length ? <div className="bootstrap-job-list">
          {matchingHistory.map((job) => <Link className="bootstrap-job-row" key={job.job_id} to={`/index-builds/${job.job_id}`}>
            <div><strong>{job.job_id}</strong><small>Request {job.client_request_id}</small><small>Created {job.created_at}</small></div><StatusBadge status={statusTone(job.status)} label={statusLabel(job.status)} />
          </Link>)}
        </div> : <p className="muted">{history.length ? "No jobs on this page match the filter." : "No build jobs are recorded on this page."}</p>}
        <div className="bootstrap-pagination"><button className="button button--quiet" type="button" disabled={!token || historyOffset === 0 || historyBusy} onClick={() => setHistoryOffset((value) => Math.max(0, value - pageSize))}>Previous</button><span>Records {historyOffset + 1}–{historyOffset + history.length}</span><button className="button button--quiet" type="button" disabled={!historyHasNext || historyBusy} onClick={() => setHistoryOffset((value) => value + pageSize)}>Next</button></div>
      </> : <p className="muted">Connect to bootstrap mode to inspect durable job history.</p>}
    </section>
  </div>;
}
