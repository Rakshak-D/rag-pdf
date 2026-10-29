import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import App from "../../App";

const acceptedJob = {
  job_id: "9de7bd41-7118-4cff-8eca-2d82bf8f7029", job_type: "evaluation", status: "queued",
  created_at: "2026-01-02T03:04:05+00:00", started_at: null, finished_at: null,
  benchmark_id: "bench-local", benchmark_version: "1", run_fingerprint: "b".repeat(64),
  shared_index_fingerprint: null, document_manifest_hash: "a".repeat(64), artifact_id: null,
  error_code: null, result_status: null,
};

function jsonResponse(value: unknown, status = 200): Response { return Response.json(value, { status }); }

function mount(path: string) {
  const router = createMemoryRouter([{ path: "*", element: <App /> }], { initialEntries: [path] });
  render(<RouterProvider router={router} />);
  return router;
}

function fetchWith(handler: (path: string, init?: RequestInit) => Response | Promise<Response>, customPreparation = false) {
  return vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/health") return jsonResponse({ status: "ok", api_version: "v1" });
    if (path === "/api/v1/runtime/models") return jsonResponse({ components: [] });
    if (!customPreparation && path === "/api/v1/documents/manifest") return jsonResponse({ schema_version: "1", content_hash: "a".repeat(64), entry_count: 1, empty: false, entries: [{ relative_path: "paper.pdf", size_bytes: 10, sha256: "b".repeat(64), document_id: "doc_" + "c".repeat(64) }] });
    if (!customPreparation && path === "/api/v1/run-plans") return jsonResponse({ run_plan: JSON.parse(validRunPlan()), manifest: { schema_version: "1", content_hash: "a".repeat(64), entry_count: 1 } });
    return handler(path, init);
  });
}

async function connect(user: ReturnType<typeof userEvent.setup>, token = "submission-test-token") {
  await user.type(screen.getByLabelText("Bearer token"), token);
  await user.click(screen.getByRole("button", { name: "Connect" }));
  await screen.findByText("Connected");
}

async function fillSuite(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Benchmark ID"), "bench-local");
  await user.type(screen.getByLabelText("Benchmark version"), "1");
  await user.type(screen.getByLabelText("Case ID"), "case-one");
  await user.click(screen.getByLabelText("text", { selector: "input[type=checkbox]" }));
  await user.type(screen.getByLabelText("Query"), "Find the stated retention period");
}

function validRunPlan() {
  return JSON.stringify({
    plan_schema_version: "1",
    document_manifest_hash: "a".repeat(64),
    resolved_config_json: JSON.stringify({ text_k: 5, image_k: 5, reset_collections_on_start: false }),
    configuration_snapshot_hash: "b".repeat(64),
    index_fingerprint: "c".repeat(64),
    run_fingerprint: "d".repeat(64),
  });
}

const baseConfig = JSON.stringify({ text_k: 5, image_k: 5, reset_collections_on_start: false });

async function prepareEvaluation(user: ReturnType<typeof userEvent.setup>) {
  fireEvent.change(screen.getByLabelText("Resolved base configuration JSON object"), { target: { value: baseConfig } });
  await user.click(screen.getByRole("button", { name: "Inspect documents" }));
  await screen.findByText(/Documents: 1/);
  await user.click(screen.getByRole("button", { name: "Prepare RunPlan" }));
  await screen.findByText("RunPlan prepared");
}

describe("experiment submission workflows", () => {
  it("navigates to experiments and supports direct evaluation and ablation routes", async () => {
    const fetcher = fetchWith(() => jsonResponse({ jobs: [] }));
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup();
    const router = mount("/");
    await user.click(screen.getByRole("link", { name: "Experiments" }));
    expect(await screen.findByRole("heading", { name: "Experiments" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Evaluation run" })).toHaveAttribute("aria-current", "page");
    await router.navigate("/experiments/ablation");
    expect(await screen.findByRole("heading", { name: "2. Resolved configuration" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("link", { name: "Ablation run" })).toHaveAttribute("aria-current", "page"));
  });

  it("adds and removes blank benchmark cases without inventing case content", async () => {
    vi.stubGlobal("fetch", fetchWith(() => jsonResponse({} , 404)));
    const user = userEvent.setup(); mount("/experiments/evaluation"); await connect(user);
    expect(screen.getByLabelText("Case ID")).toHaveValue("");
    await user.click(screen.getByRole("button", { name: "Add case" }));
    expect(screen.getAllByText(/^Case \d+$/)).toHaveLength(2);
    const removeButtons = screen.getAllByRole("button", { name: "Remove case" });
    await user.click(removeButtons[1]!);
    expect(screen.getAllByText(/^Case \d+$/)).toHaveLength(1);
    expect(screen.getByLabelText("Case ID")).toHaveValue("");
  });

  it("blocks evaluation until documents and a RunPlan are prepared", async () => {
    const fetcher = fetchWith(() => jsonResponse({ error: { code: "request_invalid" } }, 400));
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount("/experiments/evaluation"); await connect(user); await fillSuite(user);
    const submit = screen.getByRole("button", { name: "Submit evaluation job" });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Resolved base configuration JSON object"), { target: { value: "[]" } });
    expect(await screen.findByText("The JSON value must be an object, not an array or null.")).toBeInTheDocument();
    await prepareEvaluation(user);
    expect(submit).toBeEnabled();
    await user.click(submit);
    expect(await screen.findByText("The API request failed (request_invalid).")).toBeInTheDocument();
    expect(screen.queryByText("JOB ACCEPTED")).not.toBeInTheDocument();
  });

  it("rejects bad benchmark IDs, duplicate case IDs, empty modalities, and duplicate query identities", async () => {
    vi.stubGlobal("fetch", fetchWith(() => jsonResponse({ jobs: [] })));
    const user = userEvent.setup(); mount("/experiments/evaluation"); await connect(user);
    await user.type(screen.getByLabelText("Benchmark ID"), "bad id");
    await user.type(screen.getByLabelText("Benchmark version"), "1");
    await user.type(screen.getByLabelText("Case ID"), "case-one");
    await user.type(screen.getByLabelText("Query"), "same question");
    await user.type(screen.getByLabelText("Text top-k (optional)"), "0");
    expect(await screen.findByText(/Benchmark ID must start with a letter/)).toBeInTheDocument();
    expect(screen.getByText(/select text, image, or both modalities/)).toBeInTheDocument();
    expect(screen.getByText(/text_k must be a positive safe integer/)).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Text top-k (optional)"));
    await user.click(screen.getByRole("button", { name: "Add case" }));
    const ids = screen.getAllByLabelText("Case ID");
    await user.type(ids[1]!, "case-one");
    const textOptions = screen.getAllByLabelText("text", { selector: "input[type=checkbox]" });
    await user.click(textOptions[0]!);
    await user.click(textOptions[1]!);
    await user.type(screen.getAllByLabelText("Query")[1]!, "same question");
    expect(screen.getByText(/case IDs must be unique/)).toBeInTheDocument();
    expect(screen.getByText(/query, modality, and retrieval-limit combination is duplicated/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Submit evaluation job" })).toBeDisabled();
  });

  it("caps benchmark cases at the backend maximum of 100", async () => {
    vi.stubGlobal("fetch", fetchWith(() => jsonResponse({ jobs: [] })));
    const user = userEvent.setup(); mount("/experiments/evaluation"); await connect(user);
    const add = screen.getByRole("button", { name: "Add case" });
    for (let index = 1; index < 100; index += 1) fireEvent.click(add);
    expect(screen.getByText("100/100")).toBeInTheDocument();
    expect(add).toBeDisabled();
  }, 20_000);

  it("submits the exact evaluation job shape and links to the returned job", async () => {
    let posted: unknown;
    const fetcher = fetchWith((path, init) => {
      if (path === "/api/v1/jobs/evaluations") { posted = JSON.parse(String(init?.body)) as unknown; return jsonResponse({ job: acceptedJob }, 202); }
      return jsonResponse({ jobs: [] });
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount("/experiments/evaluation"); await connect(user); await fillSuite(user);
    await prepareEvaluation(user);
    await user.click(screen.getByRole("button", { name: "Submit evaluation job" }));
    expect(await screen.findByText(acceptedJob.job_id)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open job details" })).toHaveAttribute("href", `/jobs/${acceptedJob.job_id}`);
    expect(posted).toEqual({
      run_plan: JSON.parse(validRunPlan()),
      benchmark_suite: { benchmark_id: "bench-local", benchmark_version: "1", cases: [{ case_id: "case-one", query: "Find the stated retention period", modalities: ["text"], required_pages: [], reference_text: null }] },
    });
  });

  it("parses ablation JSON as an object and blocks missing or malformed manifest hashes", async () => {
    const fetcher = fetchWith(() => jsonResponse({ jobs: [] }));
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount("/experiments/ablation"); await connect(user); await fillSuite(user);
    const submit = screen.getByRole("button", { name: "Submit ablation job" });
    fireEvent.change(screen.getByLabelText("Resolved base configuration JSON object"), { target: { value: "[]" } });
    expect(await screen.findByText("The JSON value must be an object, not an array or null.")).toBeInTheDocument();
    expect(submit).toBeDisabled();
    await user.clear(screen.getByLabelText("Resolved base configuration JSON object"));
    fireEvent.change(screen.getByLabelText("Resolved base configuration JSON object"), { target: { value: "null" } });
    expect(screen.getByText("The JSON value must be an object, not an array or null.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Resolved base configuration JSON object"), { target: { value: '{"text_k":5,"text_k":6,"image_k":5,"reset_collections_on_start":false}' } });
    expect(screen.getByText("Duplicate JSON object keys are not accepted.")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Resolved base configuration JSON object"));
    fireEvent.change(screen.getByLabelText("Resolved base configuration JSON object"), { target: { value: baseConfig } });
    expect(screen.getByText(/Inspect a non-empty document manifest/)).toBeInTheDocument();
    expect(submit).toBeDisabled();
    expect(fetcher.mock.calls.some(([path]) => String(path) === "/api/v1/jobs/ablations")).toBe(false);
  });

  it("rejects destructive configuration instead of silently rewriting it", async () => {
    vi.stubGlobal("fetch", fetchWith(() => jsonResponse({ jobs: [] })));
    const user = userEvent.setup(); mount("/experiments/ablation"); await connect(user); await fillSuite(user);
    fireEvent.change(screen.getByLabelText("Resolved base configuration JSON object"), { target: { value: JSON.stringify({ text_k: 5, image_k: 5, reset_collections_on_start: true }) } });
    await user.click(screen.getByRole("button", { name: "Inspect documents" }));
    await screen.findByText(/Documents: 1/);
    expect(await screen.findByText(/must explicitly set reset_collections_on_start to false/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Submit ablation job" })).toBeDisabled();
  });

  it("submits ablation input unchanged and uses the returned job record", async () => {
    let posted: unknown;
    const ablationJob = { ...acceptedJob, job_type: "ablation", shared_index_fingerprint: "c".repeat(64) };
    const fetcher = fetchWith((path, init) => {
      if (path === "/api/v1/jobs/ablations") { posted = JSON.parse(String(init?.body)) as unknown; return jsonResponse({ job: ablationJob }, 202); }
      return jsonResponse({ jobs: [] });
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount("/experiments/ablation"); await connect(user); await fillSuite(user);
    fireEvent.change(screen.getByLabelText("Resolved base configuration JSON object"), { target: { value: baseConfig } });
    await user.click(screen.getByRole("button", { name: "Inspect documents" }));
    await screen.findByText(/Documents: 1/);
    await user.click(screen.getByRole("button", { name: "Submit ablation job" }));
    expect(await screen.findByText(ablationJob.job_id)).toBeInTheDocument();
    expect(posted).toEqual({
      base_config: JSON.parse(baseConfig), document_manifest_hash: "a".repeat(64),
      benchmark_suite: { benchmark_id: "bench-local", benchmark_version: "1", cases: [{ case_id: "case-one", query: "Find the stated retention period", modalities: ["text"], required_pages: [], reference_text: null }] },
    });
  });

  it("prevents duplicate posts while a request is pending", async () => {
    let resolvePost: ((value: Response) => void) | undefined;
    const fetcher = fetchWith((path) => path === "/api/v1/jobs/evaluations" ? new Promise<Response>((resolve) => { resolvePost = resolve; }) : jsonResponse({ jobs: [] }));
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount("/experiments/evaluation"); await connect(user); await fillSuite(user);
    await prepareEvaluation(user);
    const submit = screen.getByRole("button", { name: "Submit evaluation job" });
    await user.click(submit);
    await waitFor(() => expect(submit).toBeDisabled());
    expect(fetcher.mock.calls.filter(([path]) => String(path) === "/api/v1/jobs/evaluations")).toHaveLength(1);
    resolvePost?.(jsonResponse({ job: acceptedJob }, 202));
    expect(await screen.findByText(acceptedJob.job_id)).toBeInTheDocument();
  });

  it("does not show a stale accepted job if the user disconnects during submission", async () => {
    let resolvePost: ((value: Response) => void) | undefined;
    const fetcher = fetchWith((path) => path === "/api/v1/jobs/evaluations" ? new Promise<Response>((resolve) => { resolvePost = resolve; }) : jsonResponse({ jobs: [] }));
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount("/experiments/evaluation"); await connect(user); await fillSuite(user);
    await prepareEvaluation(user);
    await user.click(screen.getByRole("button", { name: "Submit evaluation job" }));
    await waitFor(() => expect(resolvePost).toBeDefined());
    await user.click(screen.getByRole("button", { name: "Disconnect" }));
    resolvePost?.(jsonResponse({ job: acceptedJob }, 202));
    expect(await screen.findByText("Connect before submitting")).toBeInTheDocument();
    expect(screen.queryByText("JOB ACCEPTED")).not.toBeInTheDocument();
    expect(screen.queryByText(acceptedJob.job_id)).not.toBeInTheDocument();
  });

  it("reports uncertain network outcomes without retrying or claiming success", async () => {
    let posts = 0;
    const fetcher = fetchWith((path) => {
      if (path === "/api/v1/jobs/evaluations") { posts += 1; throw new Error("private network detail"); }
      return jsonResponse({ jobs: [] });
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount("/experiments/evaluation"); await connect(user); await fillSuite(user);
    await prepareEvaluation(user);
    await user.click(screen.getByRole("button", { name: "Submit evaluation job" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("server may have accepted the job");
    expect(screen.queryByText("JOB ACCEPTED")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).not.toHaveTextContent("private network detail");
    expect(posts).toBe(1);
  });

  it("clears form credentials and payloads on disconnect and never stores them in URLs or browser storage", async () => {
    const fetcher = fetchWith(() => jsonResponse({ jobs: [] }));
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount("/experiments/evaluation"); await connect(user, "secret-token"); await fillSuite(user);
    await prepareEvaluation(user);
    await user.click(screen.getByRole("button", { name: "Disconnect" }));
    expect(await screen.findByText("Connect before submitting")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("secret-token")).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue(baseConfig)).not.toBeInTheDocument();
    expect(window.localStorage.length).toBe(0); expect(window.sessionStorage.length).toBe(0);
    expect(window.location.href).not.toContain("secret-token");
    expect(window.location.href).not.toContain("resolved_config_json");
    expect(fetcher.mock.calls.every(([path]) => !String(path).includes("secret-token"))).toBe(true);
  });

  it("shows authentication failure instead of enabling a protected workflow", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => String(input) === "/health"
      ? jsonResponse({ status: "ok", api_version: "v1" })
      : jsonResponse({ error: { code: "authentication_required" } }, 401));
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount("/experiments/evaluation");
    await user.type(screen.getByLabelText("Bearer token"), "wrong-token");
    await user.click(screen.getByRole("button", { name: "Connect" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Authentication failed");
    expect(screen.getByRole("button", { name: "Submit evaluation job" })).toBeDisabled();
    expect(fetcher.mock.calls.some(([path]) => String(path) === "/api/v1/jobs/evaluations")).toBe(false);
  });

  it("invalidates a prepared plan on configuration edits", async () => {
    const fetcher = fetchWith(() => jsonResponse({ jobs: [] }));
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount("/experiments/evaluation"); await connect(user); await fillSuite(user);
    expect(fetcher.mock.calls.some(([path]) => String(path) === "/api/v1/documents/manifest")).toBe(false);
    await prepareEvaluation(user);
    expect(screen.getByRole("button", { name: "Submit evaluation job" })).toBeEnabled();
    fireEvent.change(screen.getByLabelText("Resolved base configuration JSON object"), { target: { value: baseConfig + " " } });
    expect(screen.queryByText("RunPlan prepared")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Submit evaluation job" })).toBeDisabled();
  });

  it("checks the configured report explicitly without claiming stored collections are verified", async () => {
    const response = { status: "report_match", reason_code: "report_matches", workflow: "evaluation", document_manifest_hash: "a".repeat(64), expected_index_fingerprint: "c".repeat(64), build_id: null, report_status: "completed", reported_text_records: 3, reported_image_records: 0, observed_text_records: null, observed_image_records: null, store_checked: false, check_level: "report" };
    const fetcher = fetchWith((path) => path === "/api/v1/index/readiness" ? jsonResponse(response) : jsonResponse({ jobs: [] }));
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount("/experiments/evaluation"); await connect(user);
    expect(screen.getByRole("button", { name: "Check configured index" })).toBeDisabled();
    await prepareEvaluation(user);
    expect(fetcher.mock.calls.some(([path]) => String(path) === "/api/v1/index/readiness")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Check configured index" }));
    expect(await screen.findByText(/Report compatibility:/)).toHaveTextContent("report_match");
    expect(screen.getByText(/store checked: no/)).toBeInTheDocument();
    expect(fetcher.mock.calls.some(([path]) => String(path) === "/api/v1/jobs/evaluations")).toBe(false);
    fireEvent.change(screen.getByLabelText("Resolved base configuration JSON object"), { target: { value: baseConfig + " " } });
    expect(screen.queryByText(/Report compatibility:/)).not.toBeInTheDocument();
  });

  it("checks the ablation base configuration using the inspected manifest", async () => {
    let request: unknown;
    const response = { status: "report_match", reason_code: "report_matches", workflow: "ablation", document_manifest_hash: "a".repeat(64), expected_index_fingerprint: "c".repeat(64), build_id: null, report_status: "completed", reported_text_records: 2, reported_image_records: 1, observed_text_records: null, observed_image_records: null, store_checked: false, check_level: "report" };
    const fetcher = fetchWith((path, init) => { if (path === "/api/v1/index/readiness") { request = JSON.parse(String(init?.body)); return jsonResponse(response); } return jsonResponse({ jobs: [] }); });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount("/experiments/ablation"); await connect(user);
    fireEvent.change(screen.getByLabelText("Resolved base configuration JSON object"), { target: { value: baseConfig } });
    await user.click(screen.getByRole("button", { name: "Inspect documents" })); await screen.findByText(/Documents: 1/);
    await user.click(screen.getByRole("button", { name: "Check configured index" }));
    expect(await screen.findByText(/Report compatibility:/)).toHaveTextContent("report_match");
    expect(request).toEqual({ workflow: "ablation", resolved_config: JSON.parse(baseConfig), expected_manifest_hash: "a".repeat(64) });
    expect(fetcher.mock.calls.some(([path]) => String(path) === "/api/v1/jobs/ablations")).toBe(false);
  });

  it("rejects a report check for a different prepared index fingerprint", async () => {
    const response = { status: "report_match", reason_code: "report_matches", workflow: "evaluation", document_manifest_hash: "a".repeat(64), expected_index_fingerprint: "e".repeat(64), build_id: null, report_status: "completed", reported_text_records: 2, reported_image_records: 1, observed_text_records: null, observed_image_records: null, store_checked: false, check_level: "report" };
    vi.stubGlobal("fetch", fetchWith((path) => path === "/api/v1/index/readiness" ? jsonResponse(response) : jsonResponse({ jobs: [] })));
    const user = userEvent.setup(); mount("/experiments/evaluation"); await connect(user); await prepareEvaluation(user);
    await user.click(screen.getByRole("button", { name: "Check configured index" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("unexpected response");
    expect(screen.queryByText(/Report compatibility:/)).not.toBeInTheDocument();
  });

  it("does not restore an old report check after configuration changes", async () => {
    let finish: ((response: Response) => void) | undefined;
    const fetcher = fetchWith((path) => path === "/api/v1/index/readiness" ? new Promise<Response>((resolve) => { finish = resolve; }) : jsonResponse({ jobs: [] }));
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount("/experiments/evaluation"); await connect(user); await prepareEvaluation(user);
    await user.click(screen.getByRole("button", { name: "Check configured index" })); await waitFor(() => expect(finish).toBeDefined());
    fireEvent.change(screen.getByLabelText("Resolved base configuration JSON object"), { target: { value: baseConfig + " " } });
    await act(async () => { finish?.(jsonResponse({ status: "report_match", reason_code: "report_matches", workflow: "evaluation", document_manifest_hash: "a".repeat(64), expected_index_fingerprint: "c".repeat(64), build_id: null, report_status: "completed", reported_text_records: 2, reported_image_records: 1, observed_text_records: null, observed_image_records: null, store_checked: false, check_level: "report" })); });
    expect(screen.queryByText(/Report compatibility:/)).not.toBeInTheDocument();
  });

  it("clears a stale manifest after the backend reports a change", async () => {
    const manifest = { schema_version: "1", content_hash: "a".repeat(64), entry_count: 1, empty: false, entries: [{ relative_path: "paper.pdf", size_bytes: 10, sha256: "b".repeat(64), document_id: "doc_" + "c".repeat(64) }] };
    const fetcher = fetchWith((path) => path === "/api/v1/documents/manifest" ? jsonResponse(manifest) : path === "/api/v1/run-plans" ? jsonResponse({ error: { code: "manifest_changed" } }, 409) : jsonResponse({ jobs: [] }), true);
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount("/experiments/evaluation"); await connect(user);
    fireEvent.change(screen.getByLabelText("Resolved base configuration JSON object"), { target: { value: baseConfig } });
    await user.click(screen.getByRole("button", { name: "Inspect documents" }));
    await screen.findByText(/Documents: 1/);
    await user.click(screen.getByRole("button", { name: "Prepare RunPlan" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Source documents changed");
    expect(screen.queryByText(/Documents: 1/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Prepare RunPlan" })).toBeDisabled();
  });

  it("does not restore a manifest from a request completed after disconnect", async () => {
    let finish: ((response: Response) => void) | undefined;
    const fetcher = fetchWith((path) => path === "/api/v1/documents/manifest" ? new Promise<Response>((resolve) => { finish = resolve; }) : jsonResponse({ jobs: [] }), true);
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount("/experiments/evaluation"); await connect(user);
    await user.click(screen.getByRole("button", { name: "Inspect documents" }));
    await waitFor(() => expect(finish).toBeDefined());
    await user.click(screen.getByRole("button", { name: "Disconnect" }));
    finish?.(jsonResponse({ schema_version: "1", content_hash: "a".repeat(64), entry_count: 0, empty: true, entries: [] }));
    await connect(user);
    expect(screen.queryByText(/Documents: 0/)).not.toBeInTheDocument();
  });

  it("blocks preparation for an empty corpus", async () => {
    vi.stubGlobal("fetch", fetchWith((path) => path === "/api/v1/documents/manifest" ? jsonResponse({ schema_version: "1", content_hash: "a".repeat(64), entry_count: 0, empty: true, entries: [] }) : jsonResponse({ jobs: [] }), true));
    const user = userEvent.setup(); mount("/experiments/evaluation"); await connect(user);
    fireEvent.change(screen.getByLabelText("Resolved base configuration JSON object"), { target: { value: baseConfig } });
    await user.click(screen.getByRole("button", { name: "Inspect documents" }));
    expect(await screen.findByText(/No PDF documents found/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Prepare RunPlan" })).toBeDisabled();
  });

  it("ignores a preparation response after the configuration changes", async () => {
    let finish: ((response: Response) => void) | undefined;
    const manifest = { schema_version: "1", content_hash: "a".repeat(64), entry_count: 1, empty: false, entries: [{ relative_path: "paper.pdf", size_bytes: 10, sha256: "b".repeat(64), document_id: "doc_" + "c".repeat(64) }] };
    const fetcher = fetchWith((path) => path === "/api/v1/documents/manifest" ? jsonResponse(manifest) : path === "/api/v1/run-plans" ? new Promise<Response>((resolve) => { finish = resolve; }) : jsonResponse({ jobs: [] }), true);
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount("/experiments/evaluation"); await connect(user);
    fireEvent.change(screen.getByLabelText("Resolved base configuration JSON object"), { target: { value: baseConfig } });
    await user.click(screen.getByRole("button", { name: "Inspect documents" })); await screen.findByText(/Documents: 1/);
    await user.click(screen.getByRole("button", { name: "Prepare RunPlan" }));
    await waitFor(() => expect(finish).toBeDefined());
    expect(fetcher.mock.calls.filter(([path]) => String(path) === "/api/v1/run-plans")).toHaveLength(1);
    fireEvent.change(screen.getByLabelText("Resolved base configuration JSON object"), { target: { value: baseConfig + " " } });
    expect(screen.getByRole("button", { name: "Preparing…" })).toBeDisabled();
    finish?.(jsonResponse({ run_plan: JSON.parse(validRunPlan()), manifest: { schema_version: "1", content_hash: "a".repeat(64), entry_count: 1 } }));
    await waitFor(() => expect(screen.queryByText("RunPlan prepared")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Submit evaluation job" })).toBeDisabled();
  });

  it("ignores an old manifest-changed error after a configuration edit and newer inspection", async () => {
    let finish: ((response: Response) => void) | undefined;
    const manifest = { schema_version: "1", content_hash: "a".repeat(64), entry_count: 1, empty: false, entries: [{ relative_path: "paper.pdf", size_bytes: 10, sha256: "b".repeat(64), document_id: "doc_" + "c".repeat(64) }] };
    let inspections = 0;
    vi.stubGlobal("fetch", fetchWith((path) => path === "/api/v1/documents/manifest" ? jsonResponse({ ...manifest, content_hash: (++inspections === 1 ? "a" : "d").repeat(64) }) : path === "/api/v1/run-plans" ? new Promise<Response>((resolve) => { finish = resolve; }) : jsonResponse({ jobs: [] }), true));
    const user = userEvent.setup(); mount("/experiments/evaluation"); await connect(user);
    fireEvent.change(screen.getByLabelText("Resolved base configuration JSON object"), { target: { value: baseConfig } });
    await user.click(screen.getByRole("button", { name: "Inspect documents" })); await screen.findByText(/Documents: 1/);
    await user.click(screen.getByRole("button", { name: "Prepare RunPlan" })); await waitFor(() => expect(finish).toBeDefined());
    fireEvent.change(screen.getByLabelText("Resolved base configuration JSON object"), { target: { value: baseConfig + " " } });
    await user.click(screen.getByRole("button", { name: "Inspect documents" }));
    await screen.findByText("d".repeat(64));
    finish?.(jsonResponse({ error: { code: "manifest_changed" } }, 409));
    await waitFor(() => expect(screen.getByRole("button", { name: "Prepare RunPlan" })).toBeEnabled());
    expect(screen.getByText("d".repeat(64))).toBeInTheDocument();
    expect(screen.queryByText("Source documents changed. Inspect documents again, then prepare a new RunPlan.")).not.toBeInTheDocument();
  });

  it("does not restore a pending inspection after leaving and returning", async () => {
    let finish: ((response: Response) => void) | undefined;
    const fetcher = fetchWith((path) => path === "/api/v1/documents/manifest" ? new Promise<Response>((resolve) => { finish = resolve; }) : jsonResponse({ jobs: [] }), true);
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); const router = mount("/experiments/evaluation"); await connect(user);
    await user.click(screen.getByRole("button", { name: "Inspect documents" }));
    await waitFor(() => expect(finish).toBeDefined());
    await router.navigate("/jobs");
    await screen.findByRole("heading", { name: "Jobs" });
    finish?.(jsonResponse({ schema_version: "1", content_hash: "a".repeat(64), entry_count: 0, empty: true, entries: [] }));
    await router.navigate("/experiments/evaluation");
    expect(await screen.findByRole("button", { name: "Inspect documents" })).toBeEnabled();
    expect(screen.queryByText(/Documents: 0/)).not.toBeInTheDocument();
  });
});
