import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import type { WorkbenchApiClient } from "../../api/client";
import type { CatalogArtifactRecord } from "../../api/types";
import { ArtifactsPage } from "./ArtifactsPage";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function artifact(overrides: Partial<CatalogArtifactRecord> = {}): CatalogArtifactRecord {
  return {
    artifact_id: "a".repeat(64), artifact_type: "evaluation_result", relative_path: "evaluation_result/aa/result.json",
    byte_length: 2048, serialization_schema_version: "1", benchmark_id: "pdf-qa", benchmark_version: "2",
    status: "succeeded", run_fingerprint: "b".repeat(64), evaluation_fingerprint: "c".repeat(64),
    document_manifest_hash: null, shared_index_fingerprint: "d".repeat(64), case_count: 12,
    succeeded_case_count: 11, failed_case_count: 1, ...overrides,
  };
}

function clientWith(listArtifacts: WorkbenchApiClient["listArtifacts"]): WorkbenchApiClient {
  return { listArtifacts } as unknown as WorkbenchApiClient;
}

function ReturnProbe() {
  const location = useLocation();
  return <output aria-label="Returned results query">{location.search}</output>;
}

function DetailProbe() {
  const location = useLocation();
  const state = location.state as { resultsReturnTo?: unknown } | null;
  const returnTo = typeof state?.resultsReturnTo === "string" ? state.resultsReturnTo : "/artifacts";
  return <><h1>Artifact detail</h1><Link to={returnTo}>Back to results</Link></>;
}

function mount(client: WorkbenchApiClient, initialEntry = "/artifacts", connected = true) {
  const onRefresh = vi.fn();
  function PageRoute() {
    const [refreshKey, setRefreshKey] = useState(0);
    return <><ArtifactsPage client={client} connected={connected} refreshKey={refreshKey} onRefresh={() => { onRefresh(); setRefreshKey((value) => value + 1); }} /><ReturnProbe /></>;
  }
  render(<MemoryRouter initialEntries={[initialEntry]}><Routes>
    <Route path="/artifacts" element={<PageRoute />} />
    <Route path="/artifacts/:artifactId" element={<DetailProbe />} />
  </Routes></MemoryRouter>);
  return onRefresh;
}

describe("Results catalog", () => {
  it("distinguishes a disconnected catalog from an empty saved-results catalog", async () => {
    const listArtifacts = vi.fn().mockResolvedValue([]);
    mount(clientWith(listArtifacts), "/artifacts", false);
    expect(screen.getByText("Connect to view saved results")).toBeInTheDocument();
    expect(listArtifacts).not.toHaveBeenCalled();
    cleanup();

    mount(clientWith(listArtifacts));
    expect(await screen.findByText("No saved research results")).toBeInTheDocument();
    expect(screen.getByText(/asking questions in Chat does not create evaluation results/i)).toBeInTheDocument();
    expect(screen.queryByText("No results match these filters")).not.toBeInTheDocument();
  });

  it("shows a separate filtered-empty state and clears applied filters", async () => {
    const listArtifacts = vi.fn().mockResolvedValue([]);
    mount(clientWith(listArtifacts), "/artifacts?artifact_type=evaluation_result&status=failed&benchmark_id=missing");
    expect(await screen.findByText("No results match these filters")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Type" })).toHaveValue("evaluation_result");
    expect(screen.getByRole("combobox", { name: "Status" })).toHaveValue("failed");
    expect(screen.getByRole("button", { name: "Clear" })).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Clear" }));
    await waitFor(() => expect(listArtifacts).toHaveBeenLastCalledWith({ limit: 21, offset: 0 }));
    expect(screen.getByText("No saved research results")).toBeInTheDocument();
  });

  it("does not describe an empty later page as an empty catalog and returns to the first page", async () => {
    const listArtifacts = vi.fn(async (filters: Parameters<WorkbenchApiClient["listArtifacts"]>[0] = {}) =>
      filters.offset === 0 ? [artifact({ benchmark_id: "earlier-run" })] : []);
    mount(clientWith(listArtifacts), "/artifacts?offset=80");
    expect(await screen.findByText("No results on this page")).toBeInTheDocument();
    expect(screen.queryByText("No saved research results")).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Back to first page" }));
    expect(await screen.findByText("earlier-run")).toBeInTheDocument();
    expect(listArtifacts).toHaveBeenLastCalledWith({ limit: 21, offset: 0 });
  });

  it("applies advanced filters, requests a bounded extra row, and preserves filter context through detail navigation", async () => {
    const first = Array.from({ length: 21 }, (_, index) => artifact({ artifact_id: String(index + 1).padStart(64, "0"), benchmark_id: `benchmark-${index}`, status: "partial" }));
    const final = [artifact({ artifact_id: "f".repeat(64), benchmark_id: "last-page", artifact_type: "ablation_result", case_count: null })];
    const listArtifacts = vi.fn(async (filters: Parameters<WorkbenchApiClient["listArtifacts"]>[0] = {}) => filters.offset === 20 ? final : first);
    mount(clientWith(listArtifacts), "/artifacts?status=partial&benchmark_id=flight-set&run_fingerprint=abc123");
    const resultsList = await screen.findByRole("list", { name: "Saved evaluation and ablation results" });
    const firstRow = within(resultsList).getAllByRole("listitem")[0]!;
    expect(within(firstRow).getByText("12 cases · 11 succeeded · 1 failed")).toBeInTheDocument();
    expect(listArtifacts).toHaveBeenCalledWith({ status: "partial", benchmark_id: "flight-set", run_fingerprint: "abc123", limit: 21, offset: 0 });
    expect(screen.getByText("20 results on this page")).toBeInTheDocument();
    expect(screen.getByText(/Advanced filters.*1 active/)).toBeInTheDocument();

    await userEvent.setup().click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("last-page")).toBeInTheDocument();
    expect(listArtifacts).toHaveBeenLastCalledWith({ status: "partial", benchmark_id: "flight-set", run_fingerprint: "abc123", limit: 21, offset: 20 });
    expect(screen.getByText("Case count not reported")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("link", { name: "Review metrics for last-page ablation" }));
    expect(await screen.findByRole("heading", { name: "Artifact detail" })).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("link", { name: "Back to results" }));
    expect(await screen.findByRole("heading", { name: "Results" })).toBeInTheDocument();
    expect(screen.getByLabelText("Returned results query")).toHaveTextContent("status=partial");
    expect(screen.getByLabelText("Returned results query")).toHaveTextContent("benchmark_id=flight-set");
    expect(screen.getByLabelText("Returned results query")).toHaveTextContent("offset=20");
  });

  it("separates a catalog network error from empty and retries through the page refresh action", async () => {
    let failures = 1;
    const listArtifacts = vi.fn(async () => {
      if (failures-- > 0) throw new Error("temporary network failure");
      return [];
    });
    const onRefresh = mount(clientWith(listArtifacts));
    expect(await screen.findByText("Results could not be loaded")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Retry" }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("No saved research results")).toBeInTheDocument();
  });

  it("shows only real catalog records and uses a page-level count", async () => {
    mount(clientWith(vi.fn().mockResolvedValue([
      artifact({ benchmark_id: "flight-grounding", status: "partial", case_count: null, succeeded_case_count: null, failed_case_count: null }),
      artifact({ artifact_id: "e".repeat(64), artifact_type: "ablation_result", benchmark_id: "hybrid-ablation", status: "failed" }),
    ])));
    const list = await screen.findByRole("list", { name: "Saved evaluation and ablation results" });
    expect(within(list).getByText("flight-grounding")).toBeInTheDocument();
    expect(within(list).getByText("Ablation")).toBeInTheDocument();
    expect(within(list).getByText("Case count not reported")).toBeInTheDocument();
    expect(screen.getByText("2 results on this page")).toBeInTheDocument();
    expect(screen.queryByText(/all results/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Â·|â€”/)).not.toBeInTheDocument();
  });
});
