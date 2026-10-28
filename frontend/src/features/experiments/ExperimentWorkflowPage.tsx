import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, type WorkbenchApiClient } from "../../api/client";
import type { AblationRequest, BenchmarkCaseInput, BenchmarkSuiteInput, ExperimentJobRecord, RunPlanInput } from "../../api/types";
import { apiErrorText } from "../../components/apiErrorText";
import { EmptyState } from "../../components/EmptyState";
import { PageHeading } from "../shared";

const identityPattern = /^[A-Za-z][A-Za-z0-9_.-]{0,63}$/;
const versionPattern = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/;
const hashPattern = /^[a-fA-F0-9]{64}$/;
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

function parseRunPlan(text: string): { value?: RunPlanInput; config?: Record<string, unknown>; errors: string[] } {
  const parsed = parseObjectJson(text);
  if (!parsed.value) return { errors: [parsed.error ?? "Enter a serialized RunPlan object."] };
  const plan = parsed.value;
  const expected = ["plan_schema_version", "document_manifest_hash", "resolved_config_json", "configuration_snapshot_hash", "index_fingerprint", "run_fingerprint"];
  const errors: string[] = [];
  if (Object.keys(plan).length !== expected.length || expected.some((key) => !Object.hasOwn(plan, key))) {
    errors.push("RunPlan must contain exactly the six fields in the API schema.");
  }
  if (plan.plan_schema_version !== "1") errors.push("RunPlan schema version must be 1.");
  for (const key of ["document_manifest_hash", "configuration_snapshot_hash", "index_fingerprint", "run_fingerprint"]) {
    if (typeof plan[key] !== "string" || !hashPattern.test(plan[key])) errors.push(`RunPlan ${key} must be a 64-character SHA-256 value.`);
  }
  let config: Record<string, unknown> | undefined;
  if (typeof plan.resolved_config_json !== "string") {
    errors.push("RunPlan resolved_config_json must be a JSON string.");
  } else {
    const configResult = parseObjectJson(plan.resolved_config_json);
    if (!configResult.value) errors.push("RunPlan resolved_config_json must contain a JSON object.");
    else {
      config = configResult.value;
      errors.push(...validateConfig(config, "Resolved configuration"));
    }
  }
  if (typeof plan.plan_schema_version === "string" && typeof plan.document_manifest_hash === "string" &&
    typeof plan.resolved_config_json === "string" && typeof plan.configuration_snapshot_hash === "string" &&
    typeof plan.index_fingerprint === "string" && typeof plan.run_fingerprint === "string" && errors.length === 0) {
    return { value: plan as unknown as RunPlanInput, ...(config ? { config } : {}), errors };
  }
  return { ...(config ? { config } : {}), errors };
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
  const [runPlanText, setRunPlanText] = useState("");
  const [configText, setConfigText] = useState("");
  const [manifestText, setManifestText] = useState("");
  const [showValidation, setShowValidation] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [acceptedJob, setAcceptedJob] = useState<ExperimentJobRecord | null>(null);
  const submitLock = useRef(false);
  const mounted = useRef(false);
  const connectedRef = useRef(connected);
  connectedRef.current = connected;

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; submitLock.current = false; };
  }, []);

  useEffect(() => {
    if (!connected) {
      setSuite(blankSuite()); setRunPlanText(""); setConfigText(""); setManifestText("");
      setShowValidation(false); setSubmitError(null); setAcceptedJob(null);
    }
  }, [connected]);

  const plan = useMemo(() => isEvaluation ? parseRunPlan(runPlanText) : null, [isEvaluation, runPlanText]);
  const baseConfig = useMemo(() => !isEvaluation ? parseObjectJson(configText) : null, [isEvaluation, configText]);
  const configErrors = !isEvaluation && baseConfig?.value ? validateConfig(baseConfig.value, "Base configuration") : [];
  const manifest = manifestText;
  const manifestErrors = !isEvaluation && !hashPattern.test(manifest) ? ["Document manifest hash must be a genuine 64-character SHA-256 value."] : [];
  const suiteValidation = useMemo(() => validateSuite(suite, isEvaluation ? plan?.config : baseConfig?.value), [suite, isEvaluation, plan, baseConfig]);
  const workflowErrors = isEvaluation
    ? [...(plan?.errors ?? []), ...suiteValidation.errors]
    : [...(baseConfig?.error ? [baseConfig.error] : []), ...configErrors, ...manifestErrors, ...suiteValidation.errors];
  const canSubmit = connected && validWorkflow && workflowErrors.length === 0 && !submitting;

  function updateCase(index: number, update: Partial<DraftCase>) {
    setSuite((current) => ({ ...current, cases: current.cases.map((item, caseIndex) => caseIndex === index ? { ...item, ...update } : item) }));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setShowValidation(true); setSubmitError(null); setAcceptedJob(null);
    if (!canSubmit || submitLock.current || !suiteValidation.value) return;
    submitLock.current = true; setSubmitting(true);
    try {
      const accepted = isEvaluation
        ? await client.submitEvaluationJob({ run_plan: plan!.value!, benchmark_suite: suiteValidation.value })
        : await client.submitAblationJob({ base_config: baseConfig!.value!, document_manifest_hash: manifest, benchmark_suite: suiteValidation.value } satisfies AblationRequest);
      if (mounted.current && connectedRef.current) { setAcceptedJob(accepted.job); onAccepted(); }
    } catch (error) {
      if (mounted.current && connectedRef.current) {
        if (error instanceof ApiError && error.kind === "network") {
          setSubmitError("The connection failed after submission began. The server may have accepted the job; check Jobs before retrying. No automatic retry was made.");
        } else {
          setSubmitError(apiErrorText(error));
        }
      }
    } finally {
      submitLock.current = false;
      if (mounted.current) setSubmitting(false);
    }
  }

  if (!validWorkflow) return <section className="panel not-found"><h1>Choose a supported experiment workflow</h1><Link className="button button--primary" to="/experiments/evaluation">Open evaluation</Link></section>;

  return <>
    <PageHeading eyebrow="EXPERIMENT SUBMISSION" title="Experiments" description="Submit a validated benchmark to the existing local job API." actions={<Link className="button button--quiet" to="/jobs">Back to jobs</Link>} />
    <section className="panel experiment-prerequisite" aria-label="Workflow prerequisites">
      <span className="eyebrow">BEFORE YOU SUBMIT</span>
      <p>A compatible built index, its genuine document manifest hash, and a matching configuration are required. This API does not discover manifests, build indexes, or create trusted run plans.</p>
      <p>Input is checked by this form and validated again by the backend. A queued job is not evidence that the index is ready.</p>
    </section>
    <nav className="workflow-tabs" aria-label="Experiment workflow">
      <Link to="/experiments/evaluation" aria-current={isEvaluation ? "page" : undefined} className={isEvaluation ? "workflow-tab workflow-tab--active" : "workflow-tab"}>Evaluation run</Link>
      <Link to="/experiments/ablation" aria-current={!isEvaluation ? "page" : undefined} className={!isEvaluation ? "workflow-tab workflow-tab--active" : "workflow-tab"}>Ablation run</Link>
    </nav>
    {!connected ? <EmptyState title="Connect before submitting" detail="Verify the local API token above to enable protected job submission. Form data is cleared when disconnected." /> : null}

    <form className="experiment-form" onSubmit={(event) => void submit(event)} noValidate>
      <fieldset disabled={!connected || submitting}>
        {isEvaluation ? <section className="panel" aria-labelledby="plan-heading">
          <div className="panel__heading"><div><span className="eyebrow">TRUSTED INPUT</span><h2 id="plan-heading">Serialized run plan</h2></div></div>
          <p className="panel__note">Paste the complete serialized RunPlan created by trusted backend tooling for the intended index and configuration. This UI does not compute or verify its hashes; the backend checks plan integrity and safety.</p>
          <label className="form-field">RunPlan JSON
            <textarea aria-label="RunPlan JSON" value={runPlanText} onChange={(event) => { setRunPlanText(event.target.value); setShowValidation(true); setSubmitError(null); }} rows={9} spellCheck={false} placeholder="Paste a serialized RunPlan JSON object" aria-describedby="run-plan-help" />
            <small id="run-plan-help">The request requires the schema version, manifest hash, canonical configuration JSON string, snapshot hash, index fingerprint, and run fingerprint.</small>
          </label>
        </section> : <section className="panel" aria-labelledby="config-heading">
          <div className="panel__heading"><div><span className="eyebrow">CANONICAL SIX-VARIANT PLAN</span><h2 id="config-heading">Base configuration and manifest</h2></div></div>
          <p className="panel__note">Paste a genuine resolved configuration from trusted local tooling and the SHA-256 of its document manifest. The backend constructs the canonical six variants. This form does not create an index or verify that the configured index exists.</p>
          <label className="form-field">Resolved base configuration JSON object
            <textarea aria-label="Resolved base configuration JSON object" value={configText} onChange={(event) => { setConfigText(event.target.value); setShowValidation(true); setSubmitError(null); }} rows={11} spellCheck={false} placeholder="Paste the full resolved configuration object" />
            <small>It must include positive text_k and image_k values and explicitly set reset_collections_on_start to false. Destructive policy flags are rejected.</small>
          </label>
          <label className="form-field">Document manifest SHA-256
            <input value={manifestText} onChange={(event) => { setManifestText(event.target.value); setShowValidation(true); setSubmitError(null); }} autoComplete="off" spellCheck={false} placeholder="64 hexadecimal characters" />
          </label>
        </section>}

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
