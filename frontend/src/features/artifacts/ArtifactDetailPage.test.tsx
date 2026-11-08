import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { WorkbenchApiClient } from "../../api/client";
import type { CatalogArtifactRecord, CatalogComparisonRecord, CatalogMetricRecord } from "../../api/types";
import { ArtifactDetailPage } from "./ArtifactDetailPage";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function artifact(overrides: Partial<CatalogArtifactRecord> = {}): CatalogArtifactRecord {
  return {
    artifact_id: "a".repeat(64), artifact_type: "evaluation_result", relative_path: "evaluation_result/aa/result.json",
    byte_length: 2048, serialization_schema_version: "1", benchmark_id: "voyager-grounding", benchmark_version: "3",
    status: "partial", run_fingerprint: "b".repeat(64), evaluation_fingerprint: "c".repeat(64),
    document_manifest_hash: null, shared_index_fingerprint: "d".repeat(64), case_count: 4,
    succeeded_case_count: 3, failed_case_count: 1, ...overrides,
  };
}

function metric(overrides: Partial<CatalogMetricRecord> = {}): CatalogMetricRecord {
  return {
    artifact_id: "a".repeat(64), variant_id: "", metric_id: "M1", metric_name: "Precision score", status: "computed",
    value: 0, unit: "ratio", optimization_direction: "maximize", reference_kind: "relevant_pages",
    implementation_version: "2.1", eligible_count: 3, skipped_count: 1, failed_count: 0, unavailable_count: 1,
    model_identifier: "reranker-local", methodology_note: "Computed against annotated source pages.", ...overrides,
  };
}

function resultClient(options: {
  record?: CatalogArtifactRecord;
  metrics?: CatalogMetricRecord[];
  comparisons?: CatalogComparisonRecord[];
  load?: WorkbenchApiClient["loadArtifactResult"];
} = {}): WorkbenchApiClient {
  return {
    getArtifact: vi.fn().mockResolvedValue(options.record ?? artifact()),
    listMetrics: vi.fn().mockResolvedValue(options.metrics ?? [metric()]),
    listComparisons: vi.fn().mockResolvedValue(options.comparisons ?? []),
    loadArtifactResult: options.load ?? vi.fn().mockResolvedValue({ payload: { status: "partial" }, raw_json: "{\"status\":\"partial\"}" }),
  } as unknown as WorkbenchApiClient;
}

function ReturnResults() {
  const location = useLocation();
  return <div><h1>Results catalog</h1><output aria-label="Returned search">{location.search}</output></div>;
}

function mount(client: WorkbenchApiClient, record = artifact(), initialState: unknown = { resultsReturnTo: "/artifacts?status=partial&offset=20" }, connected = true) {
  const routerEntry = { pathname: `/artifacts/${record.artifact_id}`, state: initialState };
  render(<MemoryRouter initialEntries={[routerEntry]}><Routes>
    <Route path="/artifacts/:artifactId" element={<ArtifactDetailPage client={client} connected={connected} refreshKey={0} onRefresh={vi.fn()} />} />
    <Route path="/artifacts" element={<ReturnResults />} />
  </Routes></MemoryRouter>);
}

describe("research result details", () => {
  it("shows exact metric values, units, aggregate status, direction and all availability counts without comparative bars", async () => {
    const records = [
      metric(),
      metric({ variant_id: "2_BM25_only", metric_id: "M2", metric_name: "Latency", status: "no_eligible_values", value: null, unit: "milliseconds", optimization_direction: "minimize", eligible_count: 0, skipped_count: 2, failed_count: 1, unavailable_count: 1, model_identifier: null, methodology_note: null }),
    ];
    const { container } = render(<MemoryRouter initialEntries={[`/artifacts/${"a".repeat(64)}`]}><Routes><Route path="/artifacts/:artifactId" element={<ArtifactDetailPage client={resultClient({ metrics: records })} connected refreshKey={0} onRefresh={vi.fn()} />} /></Routes></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "voyager-grounding" })).toBeInTheDocument();
    const precision = screen.getByRole("heading", { name: "Precision score" }).closest("article")!;
    expect(precision.querySelector(".results-metric__value strong")?.textContent).toBe("0");
    expect(within(precision).getByText("ratio")).toBeInTheDocument();
    expect(within(precision).getByText("Computed")).toBeInTheDocument();
    expect(within(precision).getByText("Maximize")).toBeInTheDocument();
    expect(within(precision).getByText("Eligible").nextElementSibling).toHaveTextContent("3");
    expect(within(precision).getByText("Skipped").nextElementSibling).toHaveTextContent("1");
    await userEvent.setup().click(within(precision).getByText("Method and model details"));
    expect(within(precision).getByText("reranker-local")).toBeInTheDocument();
    expect(within(precision).getByText("Computed against annotated source pages.")).toBeInTheDocument();

    const latency = screen.getByRole("heading", { name: "Latency" }).closest("article")!;
    expect(latency.querySelector(".results-metric__value strong")?.textContent).toBe("Unavailable");
    expect(within(latency).getByText("No eligible values")).toBeInTheDocument();
    expect(within(latency).getByText("Minimize")).toBeInTheDocument();
    expect(container.querySelector(".metric-scale")).toBeNull();
    expect(container.textContent).not.toContain("scaled value");
  });

  it("preserves partial outcomes and returns to the exact filtered results page", async () => {
    const client = resultClient({ record: artifact({ case_count: null, succeeded_case_count: null }) });
    mount(client);
    expect(await screen.findAllByText("Not reported")).toHaveLength(2);
    expect(screen.getByText("Partially complete")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("link", { name: "Back to results" }));
    expect(await screen.findByRole("heading", { name: "Results catalog" })).toBeInTheDocument();
    expect(screen.getByLabelText("Returned search")).toHaveTextContent("status=partial");
    expect(screen.getByLabelText("Returned search")).toHaveTextContent("offset=20");
  });

  it("keeps ablation comparison states distinct from effect sizes and retains failed variants", async () => {
    const record = artifact({ artifact_type: "ablation_result", status: "partial" });
    const comparisons: CatalogComparisonRecord[] = [
      { artifact_id: record.artifact_id, metric_id: "M1", state: "comparable", reason_code: null },
      { artifact_id: record.artifact_id, metric_id: "M2", state: "incomplete", reason_code: "variant_missing_values" },
      { artifact_id: record.artifact_id, metric_id: "M3", state: "not_comparable", reason_code: "different_reference_family" },
    ];
    const metrics = [metric({ artifact_id: record.artifact_id, variant_id: "6_Full_pipeline", status: "partial", failed_count: 2 })];
    const { container } = render(<MemoryRouter initialEntries={[`/artifacts/${record.artifact_id}`]}><Routes><Route path="/artifacts/:artifactId" element={<ArtifactDetailPage client={resultClient({ record, metrics, comparisons })} connected refreshKey={0} onRefresh={vi.fn()} />} /></Routes></MemoryRouter>);
    expect(await screen.findByText("Comparable")).toBeInTheDocument();
    expect(screen.getByText("Incomplete")).toBeInTheDocument();
    expect(screen.getByText("Not comparable")).toBeInTheDocument();
    expect(screen.getByText("These states describe whether variants can be compared. They do not report a measured improvement.")).toBeInTheDocument();
    expect(screen.getByText("6_Full_pipeline")).toBeInTheDocument();
    expect(screen.getByText("Partial")).toBeInTheDocument();
    expect(container.querySelector("svg, canvas, .metric-scale")).toBeNull();
    await userEvent.setup().click(screen.getAllByText("Reason", { exact: true })[0]!);
    expect(screen.getByText("variant_missing_values")).toBeInTheDocument();
  });

  it("keeps full provenance and on-demand JSON behind disclosure and enforces the 8 MiB payload limit", async () => {
    const tooLarge = artifact({ byte_length: 8 * 1024 * 1024 + 1 });
    const client = resultClient({ record: tooLarge });
    mount(client, tooLarge);
    expect(await screen.findByRole("heading", { name: "voyager-grounding" })).toBeInTheDocument();
    const provenanceSummary = screen.getByText("Methodology and provenance");
    const provenanceDetails = provenanceSummary.closest("details");
    expect(provenanceDetails).not.toHaveAttribute("open");
    await userEvent.setup().click(provenanceSummary);
    expect(screen.getByText(tooLarge.artifact_id)).toBeInTheDocument();
    await userEvent.setup().click(screen.getByText("Original result data and exports"));
    expect(screen.getByText("This stored result exceeds the 8 MiB browser limit. It was not requested.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Load result JSON" })).toBeDisabled();
    expect(client.loadArtifactResult).not.toHaveBeenCalled();
  });

  it("uses the plan-bound ablation load contract and exposes verified canonical variant outcomes", async () => {
    const record = artifact({ artifact_type: "ablation_result", document_manifest_hash: "e".repeat(64), shared_index_fingerprint: "f".repeat(64) });
    const variantIds = ["1_Dense_only", "2_BM25_only", "3_Hybrid_static", "4_Hybrid_adaptive", "5_Hybrid_reranker", "6_Full_pipeline"];
    const payload = {
      schema_version: "1", status: record.status, document_manifest_hash: record.document_manifest_hash,
      shared_index_fingerprint: record.shared_index_fingerprint, benchmark_id: record.benchmark_id,
      benchmark_version: record.benchmark_version,
      variants: variantIds.map((variant_id, index) => ({ variant_id, run_fingerprint: String(index + 1).padStart(64, "0"), status: index === 2 ? "failed" : "succeeded", evaluation_result: index === 2 ? null : {}, error_code: index === 2 ? "generation_failed" : null })),
    };
    const load = vi.fn().mockResolvedValue({ payload, raw_json: JSON.stringify(payload) });
    const client = resultClient({ record, load: load as unknown as WorkbenchApiClient["loadArtifactResult"] });
    mount(client, record);
    await screen.findByRole("heading", { name: "voyager-grounding" });
    await userEvent.setup().click(screen.getByText("Original result data and exports"));
    await userEvent.setup().click(screen.getByText("Enter the original base configuration to verify this ablation payload"));
    fireEvent.change(screen.getByRole("textbox", { name: /Original compatible base configuration/ }), { target: { value: '{"source":"original"}' } });
    await userEvent.setup().click(screen.getByRole("button", { name: "Load result JSON" }));
    expect(await screen.findByRole("heading", { name: "Variant outcomes" })).toBeInTheDocument();
    expect(load).toHaveBeenCalledWith(record.artifact_id, "ablation_result", { base_config: { source: "original" }, document_manifest_hash: "e".repeat(64) });
    expect(screen.getByText("1_Dense_only")).toBeInTheDocument();
    expect(screen.getByText("generation_failed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download original JSON" })).toBeInTheDocument();
  });
});
