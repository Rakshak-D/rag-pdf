import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, BootstrapApiClient } from "../../api/client";
import type {
  BootstrapInventoryPage,
  BootstrapJobRecord,
  BootstrapPreviewResponse,
  BootstrapStatusResponse,
  IndexSelectionState,
} from "../../api/types";
import { apiErrorText } from "../../components/apiErrorText";
import { IndexSelectionPanel } from "../index-builds/IndexSelectionPanel";
import { getTrustedBootstrapConfiguration } from "./documentsApi";

interface Props {
  visible?: boolean;
  token: string | null;
  status: BootstrapStatusResponse | null;
  sourceManifestHash: string | null;
  sourceCount: number | null;
  onRefreshDocuments: () => void;
}

const INVENTORY_PAGE_SIZE = 25;
const JOB_PAGE_SIZE = 25;
const LOCAL_GATEWAY = import.meta.env.VITE_RAG_LOCAL_GATEWAY === "true";
const terminal = new Set<BootstrapJobRecord["status"]>(["succeeded", "failed", "interrupted"]);

function requestId(): string {
  if (typeof crypto === "undefined" || typeof crypto.getRandomValues !== "function") throw new Error("secure_random_unavailable");
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function isUncertain(error: unknown): boolean {
  return error instanceof ApiError && (
    error.kind === "network" || error.kind === "non_json" || error.kind === "malformed_response" ||
    (error.status === 503 && ["job_admission_failed", "job_store_unavailable"].includes(error.code))
  );
}

function readableStatus(job: BootstrapJobRecord): string {
  switch (job.status) {
    case "queued": return "Queued. The single local worker has not started this build yet.";
    case "running": return "Running. The local worker is building this index.";
    case "succeeded": return "Build completed and the server validated its report. It is not selected automatically.";
    case "failed": return `Build failed${job.error_code ? ` (${job.error_code})` : ""}. Any partial index evidence is preserved.`;
    case "interrupted": return "Build was interrupted. It will not be replayed or repaired automatically.";
  }
}

function JobStatus({ job }: { job: BootstrapJobRecord }) {
  return <div role="group" className={`documents-job documents-job--${job.status}`} aria-label={`Build status ${job.status}`}>
    <div className="documents-job__top"><span className="documents-job__state">{job.status}</span><code>{job.job_id}</code></div>
    <p>{readableStatus(job)}</p>
    <details><summary>View build details</summary><dl>
      <div><dt>Created</dt><dd>{job.created_at}</dd></div>
      {job.build_id ? <div><dt>Build ID</dt><dd><code>{job.build_id}</code></dd></div> : null}
      {job.text_records !== null ? <div><dt>Text records</dt><dd>{job.text_records}</dd></div> : null}
      {job.image_records !== null ? <div><dt>Image records</dt><dd>{job.image_records}</dd></div> : null}
      {job.partial_persistence !== null ? <div><dt>Partial persistence</dt><dd>{job.partial_persistence ? "Yes" : "No"}</dd></div> : null}
    </dl></details>
    <Link to={`/index-builds/${job.job_id}`}>View build details</Link>
  </div>;
}

export function DocumentsIndexWorkflow({ token, status, sourceManifestHash, sourceCount, onRefreshDocuments, visible = true }: Props) {
  const client = useMemo(() => token ? new BootstrapApiClient({ token }) : null, [token]);
  const [config, setConfig] = useState<Record<string, unknown> | null>(null);
  const [configurationLoading, setConfigurationLoading] = useState(false);
  const [configurationError, setConfigurationError] = useState<string | null>(null);
  const [preview, setPreview] = useState<BootstrapPreviewResponse | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [inventoryPage, setInventoryPage] = useState<BootstrapInventoryPage | null>(null);
  const [inventoryOffset, setInventoryOffset] = useState(0);
  const [inventoryLoading, setInventoryLoading] = useState(false);
  const [inventoryError, setInventoryError] = useState<string | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [activeJob, setActiveJob] = useState<BootstrapJobRecord | null>(null);
  const [history, setHistory] = useState<BootstrapJobRecord[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [uncertainRequestId, setUncertainRequestId] = useState<string | null>(null);
  const [uncertainState, setUncertainState] = useState<"idle" | "searching" | "not_found" | "found" | "limit">("idle");
  const [confirmNewAttempt, setConfirmNewAttempt] = useState(false);
  const [selection, setSelection] = useState<IndexSelectionState | null>(null);
  const previewSerial = useRef(0);
  const inventorySerial = useRef(0);
  const historySerial = useRef(0);
  const submitLock = useRef(false);
  const mounted = useRef(true);
  const currentManifest = useRef(sourceManifestHash);
  const refreshedCompletedJob = useRef<string | null>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      previewSerial.current += 1;
      inventorySerial.current += 1;
      historySerial.current += 1;
    };
  }, []);

  useEffect(() => {
    if (currentManifest.current === sourceManifestHash) return;
    currentManifest.current = sourceManifestHash;
    previewSerial.current += 1;
    inventorySerial.current += 1;
    setPreview(null); setPreviewError(null); setPreviewLoading(false); setInventoryPage(null);
    setInventoryOffset(0); setInventoryError(null); setInventoryLoading(false); setAcknowledged(false);
    setSelection(null);
  }, [sourceManifestHash]);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    setConfig(null); setConfigurationError(null); setPreview(null); setInventoryPage(null);
    setSelection(null); setAcknowledged(false); setUncertainRequestId(null); setUncertainState("idle");
    setHistory([]); setHistoryError(null); setActiveJob(null);
    if (!token) { setConfigurationLoading(false); setHistoryLoading(false); return () => { active = false; controller.abort(); }; }
    setConfigurationLoading(true);
    void getTrustedBootstrapConfiguration(token, controller.signal)
      .then((value) => { if (active) setConfig(value.resolved_config); })
      .catch((error: unknown) => { if (active && !controller.signal.aborted) setConfigurationError(apiErrorText(error)); })
      .finally(() => { if (active) setConfigurationLoading(false); });
    setHistoryLoading(true);
    void new BootstrapApiClient({ token }).listJobs({ limit: JOB_PAGE_SIZE, offset: 0 })
      .then((value) => { if (active) { setHistory(value.jobs); setActiveJob(value.jobs.find((job) => !terminal.has(job.status)) ?? null); } })
      .catch((error: unknown) => { if (active) setHistoryError(apiErrorText(error)); })
      .finally(() => { if (active) setHistoryLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [token]);

  useEffect(() => {
    if (!client || !token || !activeJob || terminal.has(activeJob.status)) return;
    let active = true;
    let attempts = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const poll = async () => {
      attempts += 1;
      try {
        const result = await client.getJob(activeJob.job_id);
        if (!active) return;
        setActiveJob(result);
        setHistory((items) => [result, ...items.filter((item) => item.job_id !== result.job_id)].slice(0, JOB_PAGE_SIZE));
        if (!terminal.has(result.status) && attempts < 120) timer = setTimeout(() => { void poll(); }, 2500);
        else if (attempts >= 120 && !terminal.has(result.status)) setActionError("Status polling stopped after five minutes. Use Refresh jobs to read the server record again.");
      } catch (error) {
        if (active) setActionError(`Build status could not be refreshed: ${apiErrorText(error)}`);
      }
    };
    timer = setTimeout(() => { void poll(); }, 1500);
    return () => { active = false; if (timer) clearTimeout(timer); };
  }, [activeJob?.job_id, activeJob?.status, client, token]);

  const refreshHistory = useCallback(async () => {
    if (!client) return;
    const serial = ++historySerial.current;
    setHistoryLoading(true); setHistoryError(null);
    try {
      const result = await client.listJobs({ limit: JOB_PAGE_SIZE, offset: 0 });
      if (mounted.current && historySerial.current === serial) {
        setHistory(result.jobs);
        setActiveJob((current) => result.jobs.find((job) => job.job_id === current?.job_id) ?? result.jobs.find((job) => !terminal.has(job.status)) ?? current);
      }
    } catch (error) {
      if (mounted.current && historySerial.current === serial) setHistoryError(apiErrorText(error));
    } finally {
      if (mounted.current && historySerial.current === serial) setHistoryLoading(false);
    }
  }, [client]);

  useEffect(() => {
    if (activeJob?.status === "succeeded" && refreshedCompletedJob.current !== activeJob.job_id) {
      refreshedCompletedJob.current = activeJob.job_id;
      void createPreview();
    }
  }, [activeJob?.job_id, activeJob?.status]);

  useEffect(() => {
    if (visible && config && client && sourceManifestHash && sourceCount) void createPreview();
  }, [config, client, sourceManifestHash, sourceCount, visible]);

  async function createPreview() {
    if (!client || !config || !sourceManifestHash || !sourceCount) return;
    const serial = ++previewSerial.current;
    inventorySerial.current += 1;
    setPreview(null); setInventoryPage(null); setInventoryError(null); setPreviewError(null); setAcknowledged(false); setPreviewLoading(true);
    try {
      const result = await client.preview({ resolved_config: config });
      if (!mounted.current || previewSerial.current !== serial) return;
      if (result.manifest_hash !== sourceManifestHash || result.document_count !== sourceCount) {
        setPreviewError("The source changed after inventory was read. Refresh the PDF list, then preview again.");
        onRefreshDocuments();
        return;
      }
      setPreview(result);
    } catch (error) {
      if (mounted.current && previewSerial.current === serial) setPreviewError(apiErrorText(error));
    } finally {
      if (mounted.current && previewSerial.current === serial) setPreviewLoading(false);
    }
  }

  async function inspectReports(offset = 0) {
    if (!client || !config || !preview || inventoryLoading || offset < 0 || offset > 512) return;
    const serial = ++inventorySerial.current;
    setInventoryOffset(offset); setInventoryPage(null); setInventoryError(null); setInventoryLoading(true);
    try {
      const result = await client.inspectIndexInventory({
        resolved_config: config,
        expected_manifest_hash: preview.manifest_hash,
        limit: INVENTORY_PAGE_SIZE,
        offset,
      }, preview.index_fingerprint);
      if (!mounted.current || inventorySerial.current !== serial) return;
      if (result.document_manifest_hash !== sourceManifestHash || result.expected_index_fingerprint !== preview.index_fingerprint) {
        setPreview(null); setAcknowledged(false);
        setInventoryError("The source or configuration identity changed during report inspection. Preview again before continuing.");
        onRefreshDocuments();
        return;
      }
      setInventoryPage(result);
    } catch (error) {
      if (mounted.current && inventorySerial.current === serial) {
        setInventoryError(apiErrorText(error));
        if (error instanceof ApiError && ["manifest_changed", "manifest_source_changed", "inventory_identity_changed"].includes(error.code)) {
          setPreview(null); setInventoryPage(null); setAcknowledged(false); onRefreshDocuments();
        }
      }
    } finally {
      if (mounted.current && inventorySerial.current === serial) setInventoryLoading(false);
    }
  }

  useEffect(() => {
    if (preview && client && config) void inspectReports(0);
  }, [preview, client, config]);

  async function submitBuild() {
    if (!client || !token || !config || !preview?.fresh_build_eligible || !acknowledged || !status?.accepting_jobs || submitLock.current || submitting || uncertainRequestId) return;
    submitLock.current = true;
    setSubmitting(true); setActionError(null);
    let id: string;
    try { id = requestId(); }
    catch {
      setActionError("Secure request IDs are unavailable in this browser. Update the browser before submitting a build.");
      submitLock.current = false; setSubmitting(false); return;
    }
    try {
      const result = await client.submitJob({
        resolved_config: config,
        expected_manifest_hash: preview.manifest_hash,
        expected_index_fingerprint: preview.index_fingerprint,
        client_request_id: id,
      });
      if (!mounted.current) return;
      setActiveJob(result.job); setHistory((items) => [result.job, ...items.filter((item) => item.job_id !== result.job.job_id)].slice(0, JOB_PAGE_SIZE));
      setUncertainState("idle");
      void refreshHistory();
    } catch (error) {
      if (!mounted.current) return;
      setActionError(apiErrorText(error));
      if (isUncertain(error)) {
        setUncertainRequestId(id); setUncertainState("idle"); setConfirmNewAttempt(false);
      }
      if (error instanceof ApiError && ["manifest_changed", "index_fingerprint_changed", "confirmation_changed", "index_already_exists", "fresh_index_exists"].includes(error.code)) {
        setPreview(null); setAcknowledged(false); setInventoryPage(null); onRefreshDocuments();
      }
    } finally {
      submitLock.current = false;
      if (mounted.current) setSubmitting(false);
    }
  }

  async function findUncertainJob() {
    if (!client || !uncertainRequestId || uncertainState === "searching") return;
    setUncertainState("searching"); setActionError(null);
    try {
      const pageSize = 100;
      for (let page = 0; page < 100; page += 1) {
        const result = await client.listJobs({ limit: pageSize, offset: page * pageSize });
        if (!mounted.current) return;
        const found = result.jobs.find((item) => item.client_request_id === uncertainRequestId);
        if (found) {
          setActiveJob(found); setHistory((items) => [found, ...items.filter((item) => item.job_id !== found.job_id)].slice(0, JOB_PAGE_SIZE));
          setUncertainState("found");
          return;
        }
        if (result.jobs.length < pageSize) { setUncertainState("not_found"); return; }
      }
      setUncertainState("limit");
    } catch (error) {
      if (!mounted.current) return;
      setUncertainState("idle"); setActionError(apiErrorText(error));
    }
  }

  function allowNewBuildAfterReview() {
    if (uncertainState !== "not_found" || !confirmNewAttempt) return;
    setUncertainRequestId(null); setUncertainState("idle"); setConfirmNewAttempt(false); setActionError(null);
  }

  const currentSelection = Boolean(preview && selection?.selection_status === "selected" &&
    selection.selection?.document_manifest_hash === sourceManifestHash &&
    selection.selection.index_fingerprint === preview.index_fingerprint);
  const hasNextReportPage = Boolean(inventoryPage && inventoryPage.total_candidates !== null && inventoryPage.offset + inventoryPage.candidates.length < inventoryPage.total_candidates);
  const selectionToken = LOCAL_GATEWAY ? token : null;

  return <div role="region" className="documents-workflow" aria-label="Prepare and use a document index">
    <p className="documents-next-step" role="status" aria-label="Document preparation">{sourceCount ? `${sourceCount} ${sourceCount === 1 ? "PDF" : "PDFs"} uploaded. ` : ""}{!sourceCount ? "Add a PDF to begin." : activeJob && ["running", "queued"].includes(activeJob.status) ? "Indexing your PDFs. You can leave this page and return later." : activeJob && ["failed", "interrupted"].includes(activeJob.status) ? "Indexing stopped. Open Build activity for details." : currentSelection ? "Index selected. Ask your PDFs below." : preview?.fresh_build_eligible ? "Your PDFs need an index. Confirm the build below when you are ready." : inventoryPage?.candidates.some(c => c.compatibility === "report_match") ? "A completed index matches your PDFs. Choose Use this index below." : preview?.existing_index && (!inventoryPage || inventoryLoading) ? "Checking existing indexes..." : preview?.existing_index ? "Existing index files need review. See the report checks below." : "Checking your PDF collection…"}</p>
    <section className="documents-workflow__section" aria-labelledby="prepare-index-title">
      <div className="documents-workflow__heading">
        <div><h2 id="prepare-index-title">Prepare for chat</h2></div>
        {token ? <span className="documents-workflow__connection">Build setup available</span> : null}
      </div>
      <p className="documents-workflow__copy">One index covers the whole PDF collection. Adding a PDF requires an updated index.</p>
      {!token ? <p className="documents-workflow__empty">Connect to the bootstrap service above to prepare a build.</p> : null}
      {configurationLoading ? <p role="status">Loading the trusted server configuration…</p> : null}
      {configurationError ? <div className="documents-workflow__error" role="alert"><p>{configurationError}</p><button className="button button--quiet" type="button" onClick={() => { setConfigurationError(null); setConfigurationLoading(true); void getTrustedBootstrapConfiguration(token).then((value) => setConfig(value.resolved_config)).catch((error: unknown) => setConfigurationError(apiErrorText(error))).finally(() => setConfigurationLoading(false)); }}>Retry configuration</button></div> : null}
      {config && token ? <details className="documents-workflow__config-state"><summary>Build settings</summary><div><span aria-hidden="true">✓</span><div><strong>Using the trusted server configuration</strong><small>Default build settings</small></div></div></details> : null}
      <div className="documents-workflow__actions">
        <button className="button button--primary" type="button" onClick={() => void createPreview()} disabled={!token || !config || !sourceManifestHash || !sourceCount || previewLoading || configurationLoading}>
          {previewLoading ? "Checking source…" : "Check documents"}
        </button>
        {!sourceCount && token ? <span className="documents-workflow__hint">Import at least one PDF before previewing.</span> : null}
      </div>
      {previewError ? <p className="documents-workflow__error" role="alert">{previewError}</p> : null}
      {preview ? <>
        <details><summary>View preparation details</summary><dl className="documents-workflow__facts" aria-label="Index preview results">
          <div><dt>Source documents</dt><dd>{preview.document_count}</dd></div>
          <div><dt>Build decision</dt><dd>{preview.fresh_build_eligible ? "A fresh build can be submitted" : preview.existing_index ? "An index already exists" : "Not eligible"}</dd></div>
        </dl></details>
        <p className="documents-workflow__note">No build starts until you confirm.</p>
        {preview.existing_index && !inventoryPage ? <p className="documents-workflow__warning" role="status">Index files found. Select a matching completed build below.</p> : null}
      {token && preview?.fresh_build_eligible ? <div className="documents-workflow__build-confirm">
        <p>Starting a build uses the configured models and may take time. It creates new fingerprint-owned index data only; no existing index is modified.</p>
        <label><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} disabled={!preview.fresh_build_eligible || !status?.accepting_jobs || submitting} /> I reviewed the preview and want to submit one fresh build for this source.</label>
        <button className="button button--primary" type="button" onClick={() => void submitBuild()} disabled={!acknowledged || !status?.accepting_jobs || submitting || Boolean(uncertainRequestId)}>
          {submitting ? "Submitting build…" : "Start index build"}
        </button>
        {status && !status.accepting_jobs ? <p role="status">The bootstrap worker is not accepting jobs right now.</p> : null}
      </div> : null}
        <details open={!preview.fresh_build_eligible} className="documents-existing-indexes"><summary>Existing indexes</summary>
        <div className="documents-workflow__actions">
          <button className="button button--quiet" type="button" onClick={() => void inspectReports(0)} disabled={inventoryLoading || !config}>
            {inventoryLoading ? "Inspecting reports…" : inventoryPage ? "Refresh compatible reports" : "Find existing indexes"}
          </button>
        </div>
        {inventoryError ? <p className="documents-workflow__error" role="alert">{inventoryError}</p> : null}
        {inventoryPage ? <div className="documents-workflow__reports">
          {!inventoryPage.complete ? <p role="alert">The report scan is incomplete ({inventoryPage.reason_code}). No index can be selected from this scan.</p> :
            inventoryPage.candidates.length ? <p role="status">{inventoryPage.candidates.filter((item) => item.compatibility === "report_match").length} compatible indexes on this page.</p> :
              <p role="status">No build reports were found for this source and configuration.</p>}
          {inventoryPage.candidates.filter((item) => item.compatibility !== "report_match").length ? <p className="documents-workflow__hint">Older builds do not cover the current PDFs or settings.</p> : null}
          {hasNextReportPage ? <button className="button button--quiet" type="button" onClick={() => void inspectReports(inventoryOffset + INVENTORY_PAGE_SIZE)} disabled={inventoryLoading}>Next report page</button> : null}
        </div> : null}
        {preview.existing_index && inventoryOffset === 0 && inventoryPage?.complete && !hasNextReportPage && !inventoryPage.candidates.some((item) => item.compatibility === "report_match") ? <div role="alert" className="documents-workflow__warning"><strong>No matching completed index found.</strong><p>Existing files are preserved. Review failed builds below. To retry an incomplete build, use a separate source and index workspace; do not delete the existing files.</p><Link to="/index-builds">Review build configuration and history</Link><details><summary>View report checks</summary>{inventoryPage.candidates.map((item) => <p key={item.candidate_id}>{item.build_id}: {item.reason_code}</p>)}</details></div> : null}
        <IndexSelectionPanel token={selectionToken} preview={preview} resolvedConfig={config} inventoryPage={inventoryPage} onSelectionReadback={setSelection} compact />
        </details>
      </> : null}
      {!LOCAL_GATEWAY && token ? <p className="documents-workflow__hint">In direct development mode, report selection uses the separate ordinary-runtime connection on <Link to="/index-builds">advanced Index Builds</Link>. The bootstrap credential is never sent to the ordinary runtime.</p> : null}
    </section>

    <details open={Boolean(activeJob || uncertainRequestId || actionError || historyError)} className="documents-workflow__activity"><summary>Build activity</summary>
    <section className="documents-workflow__section" aria-labelledby="build-status-title">
      <div className="documents-workflow__heading"><div><h2 id="build-status-title">Index activity</h2></div>
        <button className="button button--quiet" type="button" onClick={() => void refreshHistory()} disabled={!token || historyLoading}>{historyLoading ? "Refreshing…" : "Refresh jobs"}</button>
      </div>
      {historyError ? <p className="documents-workflow__error" role="alert">{historyError}</p> : null}
      {activeJob ? <JobStatus job={activeJob} /> : null}
      {uncertainRequestId ? <div className="documents-workflow__warning" role="group" aria-labelledby="uncertain-build-title">
        <h3 id="uncertain-build-title">Build submission needs confirmation</h3>
        <p>The response was interrupted. The server may have accepted the request. No retry was sent.</p>
        <button className="button button--quiet" type="button" onClick={() => void findUncertainJob()} disabled={uncertainState === "searching" || !token}>
          {uncertainState === "searching" ? "Checking job history…" : "Check whether the build was accepted"}
        </button>
        {uncertainState === "found" ? <p role="status">The original request was found in job history.</p> : null}
        {uncertainState === "not_found" ? <label><input type="checkbox" checked={confirmNewAttempt} onChange={(event) => setConfirmNewAttempt(event.target.checked)} /> I reviewed the checked history and explicitly intend to submit a new build.</label> : null}
        {uncertainState === "not_found" ? <button className="button button--quiet" type="button" disabled={!confirmNewAttempt} onClick={allowNewBuildAfterReview}>Allow a new explicit build submission</button> : null}
        {uncertainState === "limit" ? <p role="alert">The bounded scan checked 10,000 jobs without finding this request. Review more history on the advanced Index Builds page before deciding whether to submit again.</p> : null}
      </div> : null}
      {actionError ? <p className="documents-workflow__error" role="alert">{actionError}</p> : null}
      {token ? history.length ? <ul className="documents-workflow__history" aria-label="Recent index jobs">{history.map((job) => <li key={job.job_id}>
        <Link to={`/index-builds/${job.job_id}`}><span>{job.status}</span><code>{job.job_id}</code></Link>
      </li>)}</ul> : !historyLoading ? <p className="documents-workflow__empty">No builds recorded in this app workspace. Completed indexes may still be available above.</p> : null : <p className="documents-workflow__empty">Connect to bootstrap to inspect build activity.</p>}
      <p className="documents-workflow__hint">Builds are explicit and fresh-only. Failed or interrupted data is preserved for review, not repaired automatically.</p>
    </section>

    </details>
    {currentSelection ? <section className="documents-workflow__section documents-workflow__use" aria-labelledby="use-documents-title">
      <div><h2 id="use-documents-title">Ask your PDFs</h2></div>
      {currentSelection ? <div className="documents-workflow__selected" role="status"><strong>Index selected.</strong><span>Your next question will check retrieval and model availability.</span><Link className="button button--primary" to="/?mode=document">Ask documents</Link></div> : <p className="documents-workflow__copy">Select a completed index above to start Document Chat.</p>}
      {sourceManifestHash && preview && preview.manifest_hash !== sourceManifestHash ? <p className="documents-workflow__warning" role="alert">The source manifest changed. Preview and report compatibility were cleared.</p> : null}
    </section> : null}
  </div>;
}
