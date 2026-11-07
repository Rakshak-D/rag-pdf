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
  sourceError?: boolean;
  sourceChecking?: boolean;
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

export function DocumentsIndexWorkflow({ token, status, sourceManifestHash, sourceCount, onRefreshDocuments, visible = true, sourceError = false, sourceChecking = false }: Props) {
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
      let result = await client.inspectIndexInventory({
        resolved_config: config,
        expected_manifest_hash: preview.manifest_hash,
        limit: INVENTORY_PAGE_SIZE,
        offset,
      }, preview.index_fingerprint);
      while (result.complete && !result.candidates.some(candidate => candidate.compatibility === "report_match") && result.total_candidates !== null && result.offset + result.candidates.length < result.total_candidates) {
        const next = result.offset + INVENTORY_PAGE_SIZE;
        if (!mounted.current || inventorySerial.current !== serial) return;
        if (next > 512) throw new Error("Report inspection reached its safety limit. Review the advanced build workspace.");
        if (result.document_manifest_hash !== sourceManifestHash || result.expected_index_fingerprint !== preview.index_fingerprint) break;
        result = await client.inspectIndexInventory({ resolved_config: config, expected_manifest_hash: preview.manifest_hash, limit: INVENTORY_PAGE_SIZE, offset: next }, preview.index_fingerprint);
      }
      if (!mounted.current || inventorySerial.current !== serial) return;
      setInventoryOffset(result.offset);
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
    if (sourceChecking || sourceError || !client || !token || !config || !preview?.fresh_build_eligible || !acknowledged || !status?.accepting_jobs || submitLock.current || submitting || uncertainRequestId) return;
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
          setUncertainState("found"); setUncertainRequestId(null); setAcknowledged(false);
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

  const matchingReports = inventoryPage?.complete && inventoryPage.document_manifest_hash === sourceManifestHash && inventoryPage.expected_index_fingerprint === preview?.index_fingerprint && inventoryPage.candidates.some(candidate => candidate.compatibility === "report_match");
  const belongsToCollection = (job: BootstrapJobRecord) => job.manifest_hash === sourceManifestHash && (!preview || job.index_fingerprint === preview.index_fingerprint);
  const currentJob = activeJob && belongsToCollection(activeJob) ? activeJob : history.find(belongsToCollection) ?? null;
  const preparing = currentJob && ["queued", "running"].includes(currentJob.status);
  const failed = currentJob && ["failed", "interrupted"].includes(currentJob.status);
  const stage = !token ? "offline" : sourceError ? "unavailable" : sourceCount === null || sourceChecking ? "checking" : sourceCount === 0 ? "empty" : uncertainRequestId ? "uncertain" : currentSelection ? "selected" : preparing ? "building" : configurationError || previewError || inventoryError || inventoryPage?.complete === false ? "unavailable" : previewLoading || configurationLoading || inventoryLoading || !preview ? "checking" : matchingReports ? "reusable" : failed ? "failed" : preview.fresh_build_eligible ? "needed" : "blocked";
  const heading = { offline: "Connect your workspace", unavailable: "Unable to check documents", checking: "Checking your collection", empty: "Your first PDF awaits", uncertain: "Confirm the last request", selected: "Ready to ask", building: "Preparing your documents", reusable: "Use your prepared collection", failed: "Preparation stopped", needed: "Prepare your documents", blocked: "Preparation needs review" }[stage];

  return <aside className={`documents-workflow documents-workflow--${stage}`} aria-label="Prepare and use a document index" aria-busy={stage === "checking"}>
    <div className="documents-preparation">
      <p className="documents-step">02 / PREPARE</p><h2>{heading}</h2>
      <p className="documents-next-step" role="status" aria-label="Document preparation">{sourceCount !== null ? `${sourceCount} ${sourceCount === 1 ? "PDF" : "PDFs"} in this collection. ` : ""}{stage === "selected" ? "A compatible index is selected. Retrieval and model availability are checked when you ask." : stage === "needed" ? "Create one searchable collection from all your PDFs." : stage === "reusable" ? "A completed preparation matches your PDFs. No new build is needed." : stage === "building" ? "You can leave this page and return while the local worker runs." : stage === "empty" ? "Add a PDF to begin. Nothing is prepared automatically." : stage === "failed" ? "Your files and previous indexes are preserved. Review the failure before trying again." : stage === "blocked" ? "Existing files cannot be safely reused yet. Review the details; they will not be overwritten." : stage === "uncertain" ? "The server may have accepted your request. Check before submitting another." : stage === "offline" ? "Open connection details to connect the local service." : stage === "unavailable" ? "Refresh the PDF list and try checking again." : "Verifying the current PDFs and available preparations."}</p>
      {stage === "empty" ? <button className="button button--primary" type="button" onClick={() => document.getElementById("document-pdf")?.click()}>Choose a PDF</button> : null}
      {stage === "selected" ? <Link className="button button--primary" to="/?mode=document">Ask PDFs <span aria-hidden="true">→</span></Link> : null}
      {stage === "checking" ? <p className="documents-progress" role="status">Checking documents…</p> : null}
      {stage === "unavailable" && !sourceError ? <div role="alert" className="documents-error"><p>{configurationError ?? previewError ?? inventoryError ?? "Completed indexes could not be checked. Try again or review Advanced."}</p><button className="button button--primary" type="button" onClick={() => { if (sourceError) onRefreshDocuments(); else if (!config) { setConfigurationLoading(true); void getTrustedBootstrapConfiguration(token).then(value => { setConfig(value.resolved_config); setConfigurationError(null); }).catch(error => setConfigurationError(apiErrorText(error))).finally(() => setConfigurationLoading(false)); } else void createPreview(); }}>Try again</button></div> : null}
      {stage === "needed" ? <div className="documents-workflow__build-confirm">
        <p>Uses your configured local models and may take a few minutes.</p>
        <label><input type="checkbox" checked={acknowledged} onChange={event => setAcknowledged(event.target.checked)} disabled={submitting || sourceChecking || !status?.accepting_jobs} /> Prepare all {sourceCount} PDFs. Keep existing indexes.</label>
        <button className="button button--primary" type="button" onClick={() => void submitBuild()} disabled={!acknowledged || !status?.accepting_jobs || submitting || Boolean(uncertainRequestId) || sourceChecking}>{submitting ? "Submitting…" : "Prepare documents"}</button>
        {!status?.accepting_jobs ? <p role="alert">The preparation service is not accepting work. Check its connection in Advanced.</p> : null}
      </div> : null}
      {stage === "building" ? <div className="documents-build-progress"><span>{currentJob?.status === "queued" ? "Queued" : "Running"}</span><button className="documents-text-button" type="button" onClick={() => void refreshHistory()} disabled={historyLoading}>Refresh status</button></div> : null}
      {stage === "blocked" || stage === "failed" ? <Link className="button button--quiet" to="/index-builds">Review preparation</Link> : null}
      {uncertainRequestId ? <div className="documents-warning" role="group" aria-labelledby="uncertain-build-title"><h3 id="uncertain-build-title">Build submission needs confirmation</h3><p>No retry was sent.</p><button className="button button--primary" type="button" onClick={() => void findUncertainJob()} disabled={uncertainState === "searching" || !token}>{uncertainState === "searching" ? "Checking…" : "Check whether the build was accepted"}</button>
        {uncertainState === "found" ? <p role="status">The original request was found in job history.</p> : null}
        {uncertainState === "not_found" ? <><label><input type="checkbox" checked={confirmNewAttempt} onChange={event => setConfirmNewAttempt(event.target.checked)} /> I reviewed the history and explicitly intend to submit a new build.</label><button className="button button--quiet" type="button" disabled={!confirmNewAttempt} onClick={allowNewBuildAfterReview}>Allow a new explicit build submission</button></> : null}
        {uncertainState === "limit" ? <p role="alert">The bounded history check reached its limit. Review the advanced build workspace before submitting again.</p> : null}</div> : null}
      {actionError ? <p className="documents-error" role="alert">{actionError}</p> : null}
      <IndexSelectionPanel token={selectionToken} preview={preview} resolvedConfig={config} inventoryPage={inventoryPage} onSelectionReadback={setSelection} interactionDisabled={sourceChecking || sourceError || Boolean(uncertainRequestId) || Boolean(preparing)} compact />
      <p className="documents-preparation-note">Adding a PDF changes the collection. Earlier indexes stay preserved.</p>
    </div>
    <details className="documents-advanced" open={Boolean(failed || historyError)}><summary>Advanced</summary>
      <div className="documents-advanced__body">
        <p>Preparation validates reports without opening Chroma or loading models. Selection is not proof of retrieval readiness.</p>
        {config ? <details><summary>Build configuration</summary><p>Using the trusted server configuration</p><p>Scientific settings come from the server.</p></details> : null}
        {preview ? <dl className="documents-workflow__facts" aria-label="Index preview results"><div><dt>Source documents</dt><dd>{preview.document_count}</dd></div><div><dt>Build decision</dt><dd>{preview.fresh_build_eligible ? "A fresh build can be submitted" : "An index already exists"}</dd></div><div><dt>Index identity</dt><dd><code>{preview.index_fingerprint}</code></dd></div></dl> : null}
        <button className="documents-text-button" type="button" onClick={() => void createPreview()} disabled={!config || !sourceCount || previewLoading}>Check documents</button>
        {preview ? <button className="documents-text-button" type="button" onClick={() => void inspectReports(0)} disabled={inventoryLoading}>Refresh compatible reports</button> : null}
        {inventoryPage ? <div><p>{inventoryPage.complete ? "Report check complete." : "Report check incomplete."}</p>{inventoryPage.candidates.map(item => <p key={item.candidate_id}><code>{item.build_id ?? item.candidate_id}</code> · {item.reason_code}</p>)}{hasNextReportPage ? <button className="documents-text-button" type="button" onClick={() => void inspectReports(inventoryOffset + INVENTORY_PAGE_SIZE)}>More reports</button> : null}</div> : null}
        {stage === "blocked" ? <p>Do not delete or reset existing files. An incomplete preparation requires a separately configured workspace. Custom research configurations can be inspected in the advanced build workspace.</p> : null}
        <div className="documents-section-heading"><h3>Build activity</h3><button className="documents-text-button" type="button" onClick={() => void refreshHistory()} disabled={!token || historyLoading}>Refresh jobs</button></div>
        {historyError ? <p role="alert">{historyError}</p> : null}{currentJob ? <JobStatus job={currentJob} /> : null}
        {history.length ? <ul className="documents-workflow__history">{history.map(job => <li key={job.job_id}><Link to={`/index-builds/${job.job_id}`}>{job.status} · {job.job_id}</Link></li>)}</ul> : <p>No jobs in this app workspace. Completed indexes are checked separately.</p>}
        {!LOCAL_GATEWAY ? <p>Report selection uses the ordinary connection in the <Link to="/index-builds">advanced build workspace</Link>.</p> : null}
      </div>
    </details>
  </aside>;
}
