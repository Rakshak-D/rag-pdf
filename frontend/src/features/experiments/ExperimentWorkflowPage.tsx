import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, type WorkbenchApiClient } from "../../api/client";
import type { AblationRequest, BenchmarkCaseInput, BenchmarkSuiteInput, DocumentManifest, ExperimentJobRecord, IndexReadinessResponse, RunPlanInput } from "../../api/types";
import { apiErrorText } from "../../components/apiErrorText";
import { EmptyState } from "../../components/EmptyState";
import { PageHeading } from "../shared";

const identityPattern = /^[A-Za-z][A-Za-z0-9_.-]{0,63}$/;
const versionPattern = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/;
const maximumCases = 100;

interface DraftCase {
  caseId: string;
  query: string;
  modalities: Array<"text" | "image">;
  textK: string;
  imageK: string;
  requiredPages: string;
  referenceText: string;
}

interface SuiteDraft {
  benchmarkId: string;
  benchmarkVersion: string;
  cases: DraftCase[];
}

function blankCase(): DraftCase {
  return { caseId: "", query: "", modalities: [], textK: "", imageK: "", requiredPages: "", referenceText: "" };
}

function blankSuite(): SuiteDraft {
  return { benchmarkId: "", benchmarkVersion: "", cases: [blankCase()] };
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function containsDuplicateObjectKeys(text: string): boolean {
  let position = 0;
  const whitespace = () => { while (/\s/.test(text[position] ?? "")) position += 1; };
  const stringToken = (): string => {
    const start = position++;
    while (position < text.length) {
      const character = text[position++];
      if (character === "\\") position += 1;
      else if (character === '"') break;
    }
    return JSON.parse(text.slice(start, position)) as string;
  };
  const value = (): boolean => {
    whitespace();
    if (text[position] === '"') { stringToken(); return false; }
    if (text[position] === "[") {
      position += 1; whitespace();
      while (text[position] !== "]") {
        if (value()) return true;
        whitespace();
        if (text[position] === ",") { position += 1; whitespace(); continue; }
        break;
      }
      position += 1; return false;
    }
    if (text[position] === "{") {
      position += 1; whitespace();
      const keys = new Set<string>();
      while (text[position] !== "}") {
        const key = stringToken(); whitespace(); position += 1; whitespace();
        if (keys.has(key)) return true;
        keys.add(key);
        if (value()) return true;
        whitespace();
        if (text[position] === ",") { position += 1; whitespace(); continue; }
        break;
      }
      position += 1; return false;
    }
    while (position < text.length && !/[\s,\]}]/.test(text[position]!)) position += 1;
    return false;
  };
  return value();
}

function parseObjectJson(text: string): { value?: Record<string, unknown>; error?: string } {
  if (!text.trim()) return { error: "Enter the required JSON object." };
  let value: unknown;
  try { value = JSON.parse(text) as unknown; }
  catch { return { error: "Enter valid JSON. The input has not been changed." }; }
  if (containsDuplicateObjectKeys(text)) return { error: "Duplicate JSON object keys are not accepted." };
  if (!isObject(value)) return { error: "The JSON value must be an object, not an array or null." };
  return { value };
}

function positiveInteger(text: string): number | undefined | null {
  if (!text.trim()) return undefined;
  if (!/^\d+$/.test(text.trim())) return null;
  const value = Number(text);
  return Number.isSafeInteger(value) && value > 0 ? value : null;
}

function validateConfig(config: Record<string, unknown>, label: string): string[] {
  const errors: string[] = [];
  for (const key of ["text_k", "image_k"]) {
    if (typeof config[key] !== "number" || !Number.isSafeInteger(config[key]) || config[key] <= 0) {
      errors.push(`${label} must include a positive integer ${key} value.`);
    }
  }
  if (config.reset_collections_on_start !== false) {
    errors.push(`${label} must explicitly set reset_collections_on_start to false.`);
  }
  if (config.workbench_policy !== undefined) {
    if (!isObject(config.workbench_policy)) {
      errors.push(`${label} workbench_policy must be an object.`);
    } else {
      for (const key of ["allow_destructive_index_reset", "allow_collection_delete"]) {
        const value = config.workbench_policy[key];
        if (value !== undefined && typeof value !== "boolean") errors.push(`workbench_policy.${key} must be a boolean.`);
        if (value === true) errors.push(`Destructive setting workbench_policy.${key} is not accepted.`);
      }
    }
  }
  return errors;
}

function validateSuite(draft: SuiteDraft, config?: Record<string, unknown>): { value?: BenchmarkSuiteInput; errors: string[] } {
  const errors: string[] = [];
  const benchmarkId = draft.benchmarkId;
  const version = draft.benchmarkVersion;
  if (!identityPattern.test(benchmarkId)) errors.push("Benchmark ID must start with a letter and use at most 64 letters, digits, dots, underscores, or hyphens.");
  if (!versionPattern.test(version)) errors.push("Benchmark version must start with a letter or digit and use at most 80 letters, digits, dots, underscores, or hyphens.");
  if (draft.cases.length < 1 || draft.cases.length > maximumCases) errors.push("A benchmark suite must contain between 1 and 100 cases.");

  const caseIds = new Set<string>();
  const identities = new Set<string>();
  const cases: BenchmarkCaseInput[] = [];
  draft.cases.forEach((item, index) => {
    const row = index + 1;
    const caseId = item.caseId;
    const query = item.query.trim();
    if (!identityPattern.test(caseId)) errors.push(`Case ${row}: ID must start with a letter and contain at most 64 supported characters.`);
    if (caseIds.has(caseId)) errors.push(`Case ${row}: case IDs must be unique.`);
    caseIds.add(caseId);
    if (!query || query.length > 4096) errors.push(`Case ${row}: query is required and must be at most 4096 characters.`);
    if (!item.modalities.length || item.modalities.some((value) => value !== "text" && value !== "image") || new Set(item.modalities).size !== item.modalities.length) {
      errors.push(`Case ${row}: select text, image, or both modalities.`);
    }
    const textK = positiveInteger(item.textK);
    const imageK = positiveInteger(item.imageK);
    if (textK === null) errors.push(`Case ${row}: text_k must be a positive safe integer when supplied.`);
    if (imageK === null) errors.push(`Case ${row}: image_k must be a positive safe integer when supplied.`);
    const pages = item.requiredPages.split(/\r?\n/).map((page) => page.trim()).filter(Boolean);
    if (pages.some((page) => page.length > 32)) errors.push(`Case ${row}: each required page must be at most 32 characters.`);
    if (new Set(pages).size !== pages.length) errors.push(`Case ${row}: required page entries must be unique.`);
    const referenceText = item.referenceText.trim();
    if (referenceText.length > 200_000) errors.push(`Case ${row}: reference text must be at most 200000 characters.`);

    const textDefault = config?.text_k;
    const imageDefault = config?.image_k;
    const effectiveTextK = item.modalities.includes("text") ? (textK ?? textDefault ?? null) : null;
    const effectiveImageK = item.modalities.includes("image") ? (imageK ?? imageDefault ?? null) : null;
    if (query && item.modalities.length && textK !== null && imageK !== null) {
      const identity = JSON.stringify([query, item.modalities, effectiveTextK, effectiveImageK]);
      if (identities.has(identity)) errors.push(`Case ${row}: this query, modality, and retrieval-limit combination is duplicated.`);
      identities.add(identity);
    }

    cases.push({
      case_id: caseId,
      query,
      modalities: item.modalities,
      ...(textK === undefined ? {} : { text_k: textK }),
      ...(imageK === undefined ? {} : { image_k: imageK }),
      required_pages: pages,
      reference_text: referenceText || null,
    });
  });
  if (errors.length) return { errors };
  return { value: { benchmark_id: benchmarkId, benchmark_version: version, cases }, errors };
}

export function ExperimentWorkflowPage({ client, connected, onAccepted }: {
  client: WorkbenchApiClient;
  connected: boolean;
  onAccepted: () => void;
}) {
  const { workflow } = useParams();
  const isEvaluation = workflow === "evaluation";
  const validWorkflow = isEvaluation || workflow === "ablation";
  const [suite, setSuite] = useState<SuiteDraft>(blankSuite);
  const [configText, setConfigText] = useState("");
  const [manifest, setManifest] = useState<DocumentManifest | null>(null);
  const [plan, setPlan] = useState<RunPlanInput | null>(null);
  const [readiness, setReadiness] = useState<IndexReadinessResponse | null>(null);
  const [checkingIndex, setCheckingIndex] = useState(false);
  const [readinessError, setReadinessError] = useState<string | null>(null);
  const [inspecting, setInspecting] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [preparationError, setPreparationError] = useState<string | null>(null);
  const [showValidation, setShowValidation] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [acceptedJob, setAcceptedJob] = useState<ExperimentJobRecord | null>(null);
  const submitLock = useRef(false);
  const inspectLock = useRef(false);
  const prepareLock = useRef(false);
  const prepareSerial = useRef(0);
  const readinessSerial = useRef(0);
  const readinessLock = useRef(false);
  const generation = useRef(0);
  const mounted = useRef(false);
  const connectedRef = useRef(connected);
  connectedRef.current = connected;

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; generation.current += 1; prepareSerial.current += 1; };
  }, []);

  useEffect(() => {
    generation.current += 1;
    prepareSerial.current += 1;
    readinessSerial.current += 1;
    readinessLock.current = false;
    submitLock.current = false; inspectLock.current = false; prepareLock.current = false;
    setSuite(blankSuite()); setConfigText(""); setManifest(null); setPlan(null);
    setInspecting(false); setPreparing(false); setSubmitting(false);
    setReadiness(null); setReadinessError(null); setCheckingIndex(false);
    setShowValidation(false); setSubmitError(null); setPreparationError(null); setAcceptedJob(null);
    return () => { generation.current += 1; };
  }, [client, connected]);

  const baseConfig = useMemo(() => parseObjectJson(configText), [configText]);
  const configErrors = baseConfig.value ? validateConfig(baseConfig.value, "Resolved configuration") : [];
  const suiteValidation = useMemo(() => validateSuite(suite, baseConfig.value), [suite, baseConfig]);
  const workflowErrors = [ ...(baseConfig.error ? [baseConfig.error] : []), ...configErrors,
    ...(!manifest || manifest.empty ? ["Inspect a non-empty document manifest before submitting."] : []),
    ...(isEvaluation && !plan ? ["Prepare a RunPlan before submitting."] : []), ...suiteValidation.errors];
  const canSubmit = connected && validWorkflow && workflowErrors.length === 0 && !submitting;

  async function inspect() {
    if (!connected || inspectLock.current) return;
    inspectLock.current = true; setInspecting(true); setPreparationError(null); setPlan(null); setManifest(null); setReadiness(null); setReadinessError(null); readinessSerial.current += 1; readinessLock.current = false; setCheckingIndex(false);
    const id = ++generation.current;
    try {
      const result = await client.inspectDocuments();
      if (mounted.current && id === generation.current) setManifest(result);
    } catch (error) {
      if (mounted.current && id === generation.current) setPreparationError(apiErrorText(error));
    } finally {
      if (mounted.current && id === generation.current) { inspectLock.current = false; setInspecting(false); }
    }
  }

  async function prepare() {
    if (!connected || !manifest || manifest.empty || !baseConfig.value || configErrors.length || prepareLock.current) return;
    prepareLock.current = true; setPreparing(true); setPlan(null); setPreparationError(null); setReadiness(null); setReadinessError(null); readinessSerial.current += 1; readinessLock.current = false; setCheckingIndex(false);
    const id = ++generation.current;
    const requestId = ++prepareSerial.current;
    try {
      const result = await client.prepareRunPlan({ resolved_config: baseConfig.value, expected_manifest_hash: manifest.content_hash });
      if (result.manifest.entry_count !== manifest.entry_count || result.manifest.schema_version !== manifest.schema_version) throw new ApiError("malformed_response", "malformed_response");
      if (mounted.current && id === generation.current) setPlan(result.run_plan);
    } catch (error) {
      if (mounted.current && requestId === prepareSerial.current && id === generation.current && error instanceof ApiError && error.code === "manifest_changed") {
        setManifest(null); setPlan(null); setPreparationError("Source documents changed. Inspect documents again, then prepare a new RunPlan.");
      } else if (mounted.current && id === generation.current) setPreparationError(apiErrorText(error));
    } finally {
      if (mounted.current && requestId === prepareSerial.current) { prepareLock.current = false; setPreparing(false); }
    }
  }

  async function checkIndex() {
    if (!connected || !manifest || manifest.empty || !baseConfig.value || configErrors.length || (isEvaluation && !plan) || readinessLock.current) return;
    const id = generation.current;
    const serial = ++readinessSerial.current;
    readinessLock.current = true; setCheckingIndex(true); setReadiness(null); setReadinessError(null);
    try {
      const result = await client.checkIndexReadiness({ workflow: isEvaluation ? "evaluation" : "ablation", resolved_config: baseConfig.value, expected_manifest_hash: manifest.content_hash });
      if (isEvaluation && result.expected_index_fingerprint !== plan?.index_fingerprint) throw new ApiError("malformed_response", "malformed_response");
      if (mounted.current && id === generation.current && serial === readinessSerial.current) setReadiness(result);
    } catch (error) {
      if (mounted.current && id === generation.current && serial === readinessSerial.current) {
        if (error instanceof ApiError && error.code === "manifest_changed") { setManifest(null); setPlan(null); }
        setReadinessError(apiErrorText(error));
      }
    } finally {
      if (mounted.current && serial === readinessSerial.current) { readinessLock.current = false; setCheckingIndex(false); }
    }
  }

  function updateCase(index: number, update: Partial<DraftCase>) {
    setSuite((current) => ({ ...current, cases: current.cases.map((item, caseIndex) => caseIndex === index ? { ...item, ...update } : item) }));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setShowValidation(true); setSubmitError(null); setAcceptedJob(null);
    if (!canSubmit || submitLock.current || !suiteValidation.value) return;
    submitLock.current = true; setSubmitting(true);
    const id = generation.current;
    try {
      const accepted = isEvaluation
        ? await client.submitEvaluationJob({ run_plan: plan!, benchmark_suite: suiteValidation.value })
        : await client.submitAblationJob({ base_config: baseConfig.value!, document_manifest_hash: manifest!.content_hash, benchmark_suite: suiteValidation.value } satisfies AblationRequest);
      if (mounted.current && connectedRef.current && id === generation.current) { setAcceptedJob(accepted.job); onAccepted(); }
    } catch (error) {
      if (mounted.current && connectedRef.current && id === generation.current) {
        if (error instanceof ApiError && error.code === "manifest_changed") {
          setManifest(null); setPlan(null); setSubmitError("Source documents changed. Inspect documents again and prepare a new RunPlan.");
        } else if (error instanceof ApiError && error.kind === "network") {
          setSubmitError("The connection failed after submission began. The server may have accepted the job; check Jobs before retrying. No automatic retry was made.");
        } else {
          setSubmitError(apiErrorText(error));
        }
      }
    } finally {
      if (mounted.current && id === generation.current) { submitLock.current = false; setSubmitting(false); }
    }
  }

  if (!validWorkflow) return <section className="panel not-found"><h1>Choose a supported experiment workflow</h1><Link className="button button--primary" to="/experiments/evaluation">Open evaluation</Link></section>;

  return <>
    <PageHeading eyebrow="EXPERIMENT SUBMISSION" title="Experiments" description="Submit a validated benchmark to the existing local job API." actions={<Link className="button button--quiet" to="/jobs">Back to jobs</Link>} />
    <section className="panel experiment-prerequisite" aria-label="Workflow prerequisites">
      <span className="eyebrow">BEFORE YOU SUBMIT</span>
      <p>A compatible built index and matching configuration are required. Inspecting documents and preparing a RunPlan do not build or verify an index.</p>
      <p>Input is checked by this form and validated again by the backend. A queued job is not evidence that the index is ready.</p>
    </section>
    <nav className="workflow-tabs" aria-label="Experiment workflow">
      <Link to="/experiments/evaluation" aria-current={isEvaluation ? "page" : undefined} className={isEvaluation ? "workflow-tab workflow-tab--active" : "workflow-tab"}>Evaluation run</Link>
      <Link to="/experiments/ablation" aria-current={!isEvaluation ? "page" : undefined} className={!isEvaluation ? "workflow-tab workflow-tab--active" : "workflow-tab"}>Ablation run</Link>
    </nav>
    {!connected ? <EmptyState title="Connect before submitting" detail="Verify the local API token above to enable protected job submission. Form data is cleared when disconnected." /> : null}

    <form className="experiment-form" onSubmit={(event) => void submit(event)} noValidate>
      <fieldset disabled={!connected || submitting}>
        <section className="panel" aria-labelledby="documents-heading">
          <h2 id="documents-heading">1. Inspect source documents</h2>
          <p className="panel__note">Inspection reads the configured PDF source. Index availability remains unknown.</p>
          <button type="button" className="button button--quiet" onClick={() => void inspect()} disabled={inspecting}>{inspecting ? "Inspecting…" : "Inspect documents"}</button>
          {manifest && <div role="status"><p>Manifest schema: {manifest.schema_version} · Documents: {manifest.entry_count}</p><p>Manifest hash: <code>{manifest.content_hash}</code></p>{manifest.empty ? <p>No PDF documents found. Add documents before preparing a plan.</p> : <ul>{manifest.entries.map((entry) => <li key={entry.document_id}>{entry.relative_path} — {entry.size_bytes} bytes</li>)}</ul>}</div>}
        </section>
        <section className="panel" aria-labelledby="config-heading">
          <h2 id="config-heading">2. Resolved configuration</h2>
          <p className="panel__note">Supply the complete resolved configuration from the project configuration workflow. The backend validates its full schema and safety policy.</p>
          <label className="form-field">Resolved base configuration JSON object
            <textarea value={configText} onChange={(event) => { generation.current += 1; readinessSerial.current += 1; readinessLock.current = false; setReadiness(null); setReadinessError(null); setCheckingIndex(false); inspectLock.current = false; setInspecting(false); setPlan(null); setConfigText(event.target.value); setShowValidation(true); setPreparationError(null); setSubmitError(null); }} rows={11} spellCheck={false} />
          </label>
          {isEvaluation && <><button type="button" className="button button--quiet" onClick={() => void prepare()} disabled={!manifest || manifest.empty || !baseConfig.value || configErrors.length > 0 || preparing || inspecting}>{preparing ? "Preparing…" : "Prepare RunPlan"}</button>
            {plan && <div role="status"><h3>RunPlan prepared</h3><p>Schema: {plan.plan_schema_version}</p><p>Manifest: <code>{plan.document_manifest_hash}</code></p><p>Configuration snapshot: <code>{plan.configuration_snapshot_hash}</code></p><p>Index fingerprint: <code>{plan.index_fingerprint}</code></p><p>Run fingerprint: <code>{plan.run_fingerprint}</code></p></div>}</>}
          {preparationError && <div className="result-error" role="alert">{preparationError}</div>}
        </section>

        <section className="panel" aria-labelledby="index-heading">
          <h2 id="index-heading">Check configured index report</h2>
          <p>The configured build report can be checked. Persisted vector collections are not verified.</p>
          <button type="button" className="button button--quiet" onClick={() => void checkIndex()} disabled={!manifest || manifest.empty || !baseConfig.value || configErrors.length > 0 || (isEvaluation && !plan) || checkingIndex || inspecting || preparing}>{checkingIndex ? "Checking…" : "Check configured index"}</button>
          {readiness && <div role="status"><p>Report compatibility: <strong>{readiness.status}</strong> ({readiness.reason_code})</p><p>Verification level: {readiness.check_level}; store checked: no.</p><p>Manifest: <code>{readiness.document_manifest_hash}</code></p><p>Expected index fingerprint: <code>{readiness.expected_index_fingerprint}</code></p><p>Reported text records: {readiness.reported_text_records ?? "unavailable"}; image records: {readiness.reported_image_records ?? "unavailable"}.</p><p>{readiness.status === "report_match" ? "The report matches. Execution will check the stored index separately." : "Check the configured build report and source before running."}</p></div>}
          {readinessError && <div className="result-error" role="alert">{readinessError}</div>}
        </section>

        <section className="panel" aria-labelledby="suite-heading">
          <div className="panel__heading"><div><span className="eyebrow">USER-PROVIDED CASES</span><h2 id="suite-heading">Benchmark suite</h2></div><span className="count-pill">{suite.cases.length}/{maximumCases}</span></div>
          <div className="suite-identity-grid">
            <label className="form-field">Benchmark ID<input value={suite.benchmarkId} onChange={(event) => { setSuite((current) => ({ ...current, benchmarkId: event.target.value })); setShowValidation(true); }} autoComplete="off" /></label>
            <label className="form-field">Benchmark version<input value={suite.benchmarkVersion} onChange={(event) => { setSuite((current) => ({ ...current, benchmarkVersion: event.target.value })); setShowValidation(true); }} autoComplete="off" /></label>
          </div>
          {suite.cases.map((item, index) => <fieldset className="benchmark-case" key={index} disabled={!connected || submitting}>
            <legend>Case {index + 1}</legend>
            <div className="case-heading"><span className="eyebrow">BENCHMARK INPUT</span><button className="button button--quiet button--small" type="button" onClick={() => { setSuite((current) => ({ ...current, cases: current.cases.filter((_, caseIndex) => caseIndex !== index) })); setShowValidation(true); }} disabled={suite.cases.length <= 1}>Remove case</button></div>
            <div className="case-grid">
              <label className="form-field">Case ID<input value={item.caseId} onChange={(event) => { updateCase(index, { caseId: event.target.value }); setShowValidation(true); }} autoComplete="off" /></label>
              <div className="form-field"><span>Modalities</span><div className="modality-options">
                {(["text", "image"] as const).map((modality) => <label key={modality}><input type="checkbox" checked={item.modalities.includes(modality)} onChange={(event) => { const modalities = event.target.checked ? [...item.modalities, modality] : item.modalities.filter((value) => value !== modality); updateCase(index, { modalities }); setShowValidation(true); }} />{modality}</label>)}
              </div></div>
              <label className="form-field">Text top-k (optional)<input type="number" min="1" step="1" value={item.textK} onChange={(event) => { updateCase(index, { textK: event.target.value }); setShowValidation(true); }} /></label>
              <label className="form-field">Image top-k (optional)<input type="number" min="1" step="1" value={item.imageK} onChange={(event) => { updateCase(index, { imageK: event.target.value }); setShowValidation(true); }} /></label>
            </div>
            <label className="form-field">Query<textarea value={item.query} onChange={(event) => { updateCase(index, { query: event.target.value }); setShowValidation(true); }} rows={3} maxLength={4096} /></label>
            <div className="case-grid case-grid--secondary">
              <label className="form-field">Required pages (optional, one per line)<textarea value={item.requiredPages} onChange={(event) => { updateCase(index, { requiredPages: event.target.value }); setShowValidation(true); }} rows={3} /></label>
              <label className="form-field">Reference text (optional)<textarea value={item.referenceText} onChange={(event) => { updateCase(index, { referenceText: event.target.value }); setShowValidation(true); }} rows={3} maxLength={200000} /></label>
            </div>
          </fieldset>)}
          <button className="button button--quiet add-case-button" type="button" onClick={() => { setSuite((current) => ({ ...current, cases: [...current.cases, blankCase()] })); setShowValidation(true); }} disabled={!connected || submitting || suite.cases.length >= maximumCases}>Add case</button>
        </section>
      </fieldset>

      {showValidation && workflowErrors.length > 0 ? <section className="validation-summary" aria-label="Input validation" aria-live="polite"><strong>Review these fields before submitting</strong><ul>{workflowErrors.map((message, index) => <li key={`${index}-${message}`}>{message}</li>)}</ul></section> : null}
      {submitError ? <div className="result-error" role="alert">{submitError}</div> : null}
      {acceptedJob ? <section className="submission-success" role="status" aria-live="polite"><div><span className="eyebrow">JOB ACCEPTED</span><h2>{acceptedJob.job_id}</h2><p>Current state: <strong>{acceptedJob.status}</strong>. Execution may still be queued or running.</p></div><Link className="button button--primary" to={`/jobs/${encodeURIComponent(acceptedJob.job_id)}`}>Open job details</Link></section> : null}
      <div className="submission-actions"><button className="button button--primary" type="submit" disabled={!canSubmit}>{submitting ? "Submitting…" : isEvaluation ? "Submit evaluation job" : "Submit ablation job"}</button><span role="status">{submitting ? "Sending one request; it will not be retried automatically." : "Submission is enabled when the required inputs validate."}</span></div>
    </form>
  </>;
}
