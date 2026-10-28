import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import type { CatalogArtifactRecord, CatalogMetricRecord, ExperimentJobRecord } from "../api/types";

const runtime = { components: [{ component_kind: "text", model_identifier: "text-test", state: "unloaded", error_code: null, explicitly_warmed: false }] };
const health = { status: "ok", api_version: "v1" };

function job(overrides: Partial<ExperimentJobRecord> = {}): ExperimentJobRecord {
  return {
    job_id: "job-0001", job_type: "evaluation", status: "queued", created_at: "2026-01-02T03:04:05+00:00",
    started_at: null, finished_at: null, benchmark_id: "synthetic-bench", benchmark_version: "2",
    run_fingerprint: "run-fingerprint", shared_index_fingerprint: null, document_manifest_hash: null,
    artifact_id: null, error_code: null, result_status: null, ...overrides,
  };
}

function artifact(overrides: Partial<CatalogArtifactRecord> = {}): CatalogArtifactRecord {
  return {
    artifact_id: "a".repeat(64), artifact_type: "evaluation_result", relative_path: "evaluation_result/aa/artifact.json",
    byte_length: 2048, serialization_schema_version: "1", benchmark_id: "synthetic-bench", benchmark_version: "2",
    status: "succeeded", run_fingerprint: "run-fingerprint", evaluation_fingerprint: "evaluation-fingerprint",
    document_manifest_hash: null, shared_index_fingerprint: "index-fingerprint", case_count: 2,
    succeeded_case_count: 2, failed_case_count: 0, ...overrides,
  };
}

function metric(overrides: Partial<CatalogMetricRecord> = {}): CatalogMetricRecord {
  return {
    artifact_id: "a".repeat(64), variant_id: "", metric_id: "M1", metric_name: "Recall at 5", status: "computed",
    value: 0, unit: "ratio", optimization_direction: "maximize", reference_kind: "pages", implementation_version: "1",
    eligible_count: 2, skipped_count: 0, failed_count: 0, unavailable_count: 0, model_identifier: null, methodology_note: null,
    ...overrides,
  };
}

function response(value: unknown, status = 200): Response { return Response.json(value, { status }); }

function mount(path: string) {
  const router = createMemoryRouter([{ path: "*", element: <App /> }], { initialEntries: [path] });
  render(<RouterProvider router={router} />);
  return router;
}

async function connect(user: ReturnType<typeof userEvent.setup>, token = "test-token") {
  await user.type(screen.getByLabelText("Bearer token"), token);
  await user.click(screen.getByRole("button", { name: "Connect" }));
  await screen.findByText("Connected");
}

function standardFetch(handler: (path: string, init: RequestInit | undefined) => Response) {
  return vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/health") return response(health);
    if (path === "/api/v1/runtime/models") return response(runtime);
    return handler(path, init);
  });
}

beforeEach(() => vi.stubGlobal("fetch", vi.fn<typeof fetch>()));

describe("jobs and artifacts explorer", () => {
  it("navigates by URL and responds to browser back and forward", async () => {
    const fetcher = standardFetch((path) => path.startsWith("/api/v1/jobs") ? response({ jobs: [] }) : response({ artifacts: [] }));
    vi.stubGlobal("fetch", fetcher);
    const router = mount("/");
    const user = userEvent.setup();
    expect(await screen.findByRole("heading", { name: "Workbench overview" })).toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: "Jobs" }));
    expect(await screen.findByRole("heading", { name: "Jobs", level: 1 })).toBeInTheDocument();
    await router.navigate(-1);
    expect(await screen.findByRole("heading", { name: "Workbench overview" })).toBeInTheDocument();
    await router.navigate(1);
    expect(await screen.findByRole("heading", { name: "Jobs", level: 1 })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Jobs" })).toHaveAttribute("aria-current", "page");
  });

  it("filters jobs, paginates with the bounded API limit, and opens a detail route", async () => {
    const firstPage = Array.from({ length: 21 }, (_, index) => job({ job_id: `job-page-1-${index}`, benchmark_id: `bench-${index}` }));
    const secondPage = [job({ job_id: "job-page-2", benchmark_id: "last-page" })];
    const fetcher = standardFetch((path) => {
      if (path.startsWith("/api/v1/jobs?")) {
        const query = new URLSearchParams(path.split("?")[1]);
        if (query.get("job_type") === "evaluation" && query.get("status") === "failed") return response({ jobs: [job({ status: "failed" })] });
        return response({ jobs: Number(query.get("offset")) === 20 ? secondPage : firstPage });
      }
      if (path === "/api/v1/jobs/job-page-2") return response({ job: secondPage[0] });
      return response({ jobs: [] });
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount("/jobs"); await connect(user);
    expect(await screen.findByText("bench-0")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Job type"), "evaluation");
    await user.selectOptions(screen.getByLabelText("Status"), "failed");
    await user.click(screen.getByRole("button", { name: "Apply filters" }));
    expect(await screen.findByText("failed", { selector: ".status-badge" })).toBeInTheDocument();
    expect(fetcher.mock.calls.map(([input]) => String(input)).some((path) => path.includes("job_type=evaluation") && path.includes("status=failed"))).toBe(true);

    await user.click(screen.getByRole("button", { name: "Clear" }));
    await screen.findByText("bench-0");
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("last-page")).toBeInTheDocument();
    expect(fetcher.mock.calls.map(([input]) => String(input)).some((path) => path.includes("offset=20"))).toBe(true);
    await user.click(screen.getByRole("link", { name: "Details" }));
    expect(await screen.findByRole("heading", { name: "Job details" })).toBeInTheDocument();
  });

  it("renders empty and failed job catalogs without fabricated rows", async () => {
    let fail = false;
    const fetcher = standardFetch((path) => {
      if (path.startsWith("/api/v1/jobs")) return fail ? response({ error: { code: "catalog_unavailable" } }, 503) : response({ jobs: [] });
      return response({ artifacts: [] });
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount("/jobs"); await connect(user);
    expect(await screen.findByText("Submitted jobs will appear here.")).toBeInTheDocument();
    expect(screen.queryByText("fake-benchmark")).not.toBeInTheDocument();
    fail = true;
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByText("The API request failed (catalog_unavailable)." )).toBeInTheDocument();
  });

  it("shows job metadata and confirms queued cancellation, then reflects the persisted state", async () => {
    let cancelled = false;
    const queued = job();
    const done = job({ status: "cancelled", finished_at: "2026-01-02T03:05:00+00:00", error_code: "cancelled_by_user" });
    const fetcher = standardFetch((path, init) => {
      if (path === "/api/v1/jobs/job-0001/cancel") { cancelled = true; expect(init?.method).toBe("POST"); return response({ job: done }); }
      if (path === "/api/v1/jobs/job-0001") return response({ job: cancelled ? done : queued });
      if (path.startsWith("/api/v1/jobs")) return response({ jobs: [] });
      return response({ artifacts: [] });
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount("/jobs/job-0001"); await connect(user);
    expect((await screen.findAllByText("Not recorded")).length).toBeGreaterThan(1);
    expect(screen.getByText("run-fingerprint")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel queued job" }));
    expect(screen.getByRole("group", { name: "Confirm job cancellation" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Confirm cancellation" }));
    expect(await screen.findByText("cancelled", { selector: ".status-badge" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel queued job" })).not.toBeInTheDocument();
  });

  it("reports a queued cancellation race and refreshes when the backend says the job is running", async () => {
    let cancellationAttempted = false;
    const running = job({ status: "running", started_at: "2026-01-02T03:04:10+00:00" });
    const fetcher = standardFetch((path) => {
      if (path === "/api/v1/jobs/job-0001/cancel") { cancellationAttempted = true; return response({ error: { code: "job_not_cancellable" } }, 409); }
      if (path === "/api/v1/jobs/job-0001") return response({ job: cancellationAttempted ? running : job() });
      if (path.startsWith("/api/v1/jobs")) return response({ jobs: [] });
      return response({ artifacts: [] });
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount("/jobs/job-0001"); await connect(user);
    await screen.findByText("run-fingerprint");
    await user.click(screen.getByRole("button", { name: "Cancel queued job" }));
    await user.click(screen.getByRole("button", { name: "Confirm cancellation" }));
    expect(await screen.findByText("This job is no longer queued and cannot be cancelled.")).toBeInTheDocument();
    expect(await screen.findByText("running", { selector: ".status-badge" })).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Confirm job cancellation" })).not.toBeInTheDocument();
  });

  it("does not offer cancellation for terminal jobs and clears protected rows on disconnect", async () => {
    const fetcher = standardFetch((path) => {
      if (path === "/api/v1/jobs/job-terminal") return response({ job: job({ job_id: "job-terminal", status: "failed", error_code: "job_execution_failed" }) });
      if (path.startsWith("/api/v1/jobs")) return response({ jobs: [job({ benchmark_id: "private-benchmark" })] });
      return response({ artifacts: [] });
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount("/jobs/job-terminal"); await connect(user);
    expect(await screen.findByText("job_execution_failed")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /cancel/i })).not.toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: "Back to jobs" }));
    expect(await screen.findByText("private-benchmark")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Disconnect" }));
    expect(await screen.findByText("Connect to view this page")).toBeInTheDocument();
    expect(screen.queryByText("private-benchmark")).not.toBeInTheDocument();
  });

  it("filters artifacts and shows the empty state with a clear action", async () => {
    const record = artifact();
    const fetcher = standardFetch((path) => {
      if (path.startsWith("/api/v1/artifacts?")) {
        const query = new URLSearchParams(path.split("?")[1]);
        return query.get("artifact_type") === "ablation_result" ? response({ artifacts: [] }) : response({ artifacts: [record] });
      }
      return response({ jobs: [] });
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount("/artifacts"); await connect(user);
    expect(await screen.findByText("synthetic-bench")).toBeInTheDocument();
    expect(screen.getByText("2.0 KB")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Artifact type"), "ablation_result");
    await user.click(screen.getByRole("button", { name: "Apply filters" }));
    expect(await screen.findByText("No artifacts match these filters. Clear filters to view the complete catalog.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(await screen.findByText("synthetic-bench")).toBeInTheDocument();
  });

  it("paginates the artifact catalog using the server offset", async () => {
    const first = Array.from({ length: 21 }, (_, index) => artifact({ artifact_id: String(index).padStart(64, "0"), benchmark_id: `artifact-bench-${index}` }));
    const fetcher = standardFetch((path) => {
      if (path.startsWith("/api/v1/artifacts?")) {
        const query = new URLSearchParams(path.split("?")[1]);
        return response({ artifacts: Number(query.get("offset")) === 20 ? [first[20]] : first });
      }
      return response({ jobs: [] });
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount("/artifacts"); await connect(user);
    expect(await screen.findByText("artifact-bench-0")).toBeInTheDocument();
    expect(screen.getByText("artifact-bench-19")).toBeInTheDocument();
    expect(screen.queryByText("artifact-bench-20")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("artifact-bench-20")).toBeInTheDocument();
    expect(fetcher.mock.calls.some(([input]) => String(input).includes("offset=20"))).toBe(true);
  });

  it("loads evaluation metadata and metrics independently, preserves zero/null, and fetches result only on demand", async () => {
    const evalArtifact = artifact();
    const values = [metric(), metric({ metric_id: "M2", metric_name: "Unavailable", status: "no_eligible_values", value: null, eligible_count: 0, unavailable_count: 2 })];
    const fetcher = standardFetch((path) => {
      if (path === `/api/v1/artifacts/${evalArtifact.artifact_id}`) return response(evalArtifact);
      if (path === `/api/v1/artifacts/${evalArtifact.artifact_id}/metrics`) return response({ metrics: values });
      if (path === `/api/v1/artifacts/${evalArtifact.artifact_id}/result`) return response({ status: "verified", result: [1, 2] });
      if (path.startsWith("/api/v1/artifacts")) return response({ artifacts: [] });
      return response({ jobs: [] });
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount(`/artifacts/${evalArtifact.artifact_id}`); await connect(user);
    expect(await screen.findByRole("heading", { name: "Artifact metadata" })).toBeInTheDocument();
    expect(await screen.findByText("No numeric value")).toBeInTheDocument();
    expect(screen.getByText("0", { selector: ".metric-value" })).toBeInTheDocument();
    expect(screen.getByText("no_eligible_values")).toBeInTheDocument();
    expect(fetcher.mock.calls.some(([input]) => String(input).endsWith("/comparisons"))).toBe(false);
    expect(fetcher.mock.calls.some(([input]) => String(input).endsWith("/result"))).toBe(false);
    await user.click(screen.getByRole("button", { name: "Load result JSON" }));
    expect(await screen.findByLabelText("Serialized artifact result JSON")).toHaveTextContent('"status": "verified"');
  });

  it("shows a safe result parsing error instead of rendering malformed response data", async () => {
    const evalArtifact = artifact();
    const fetcher = standardFetch((path) => {
      if (path === `/api/v1/artifacts/${evalArtifact.artifact_id}`) return response(evalArtifact);
      if (path === `/api/v1/artifacts/${evalArtifact.artifact_id}/metrics`) return response({ metrics: [] });
      if (path === `/api/v1/artifacts/${evalArtifact.artifact_id}/result`) return new Response("private raw body", { headers: { "Content-Type": "application/json" } });
      if (path.startsWith("/api/v1/artifacts")) return response({ artifacts: [] });
      return response({ jobs: [] });
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount(`/artifacts/${evalArtifact.artifact_id}`); await connect(user);
    await screen.findByRole("heading", { name: "Artifact metadata" });
    await user.click(screen.getByRole("button", { name: "Load result JSON" }));
    expect(await screen.findByText("The backend returned an unexpected response.")).toBeInTheDocument();
    expect(screen.queryByText("private raw body")).not.toBeInTheDocument();
  });

  it("loads ablation comparisons and requires an object configuration before plan-bound result loading", async () => {
    const ablation = artifact({ artifact_id: "b".repeat(64), artifact_type: "ablation_result", relative_path: "ablation_result/bb/ablation.json", document_manifest_hash: "manifest-hash", evaluation_fingerprint: null });
    const fetcher = standardFetch((path) => {
      if (path === `/api/v1/artifacts/${ablation.artifact_id}`) return response(ablation);
      if (path === `/api/v1/artifacts/${ablation.artifact_id}/metrics`) return response({ metrics: [] });
      if (path === `/api/v1/artifacts/${ablation.artifact_id}/comparisons`) return response({ comparisons: [{ artifact_id: ablation.artifact_id, metric_id: "M4", state: "not_comparable", reason_code: "cross_mode" }] });
      if (path.includes("/result?")) return response({ error: { code: "ablation_plan_mismatch" } }, 409);
      if (path.startsWith("/api/v1/artifacts")) return response({ artifacts: [] });
      return response({ jobs: [] });
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount(`/artifacts/${ablation.artifact_id}`); await connect(user);
    expect(await screen.findByText("not_comparable", { selector: ".status-badge" })).toBeInTheDocument();
    const initialResults = fetcher.mock.calls.filter(([input]) => String(input).includes("/result")).length;
    await user.click(screen.getByRole("button", { name: "Load result JSON" }));
    expect(await screen.findByText("Configuration must be valid JSON containing an object.")).toBeInTheDocument();
    expect(fetcher.mock.calls.filter(([input]) => String(input).includes("/result")).length).toBe(initialResults);
    const configInput = screen.getByRole("textbox", { name: /Original compatible base configuration/ });
    fireEvent.change(configInput, { target: { value: '{"profile":"original"}' } });
    await user.click(screen.getByRole("button", { name: "Load result JSON" }));
    expect(await screen.findByText(/does not match this artifact's accepted ablation plan/)).toBeInTheDocument();
    const resultUrl = fetcher.mock.calls.map(([input]) => String(input)).find((path) => path.includes("/result?"));
    expect(resultUrl).toContain("document_manifest_hash=manifest-hash");
    expect(resultUrl).not.toContain("test-token");
  });

  it("does not put credentials in storage or URLs", async () => {
    const fetcher = standardFetch((path) => path.startsWith("/api/v1/jobs") ? response({ jobs: [] }) : response({ artifacts: [] }));
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount("/jobs"); await connect(user, "never-in-url");
    await waitFor(() => expect(fetcher.mock.calls.some(([input]) => String(input).startsWith("/api/v1/jobs"))).toBe(true));
    expect(fetcher.mock.calls.every(([input]) => !String(input).includes("never-in-url"))).toBe(true);
    expect(window.localStorage.length).toBe(0); expect(window.sessionStorage.length).toBe(0);
  });
});
