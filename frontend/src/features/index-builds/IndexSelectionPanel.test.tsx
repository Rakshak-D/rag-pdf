import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { IndexSelectionPanel } from "./IndexSelectionPanel";
import type { BootstrapInventoryPage, BootstrapPreviewResponse, IndexSelectionState } from "../../api/types";

const manifestHash = "a".repeat(64);
const indexFingerprint = "b".repeat(64);
const buildId = "c".repeat(32);
const reportHash = "d".repeat(64);
const otherBuildId = "e".repeat(32);
const preview: BootstrapPreviewResponse = {
  document_count: 1,
  manifest_hash: manifestHash,
  index_fingerprint: indexFingerprint,
  run_fingerprint: "f".repeat(64),
  existing_index: false,
  fresh_build_eligible: true,
  reason_code: "fresh_build_available",
};
const config = { pdf_dir: "trusted-source", reset_collections_on_start: false };
const candidate = {
  candidate_id: buildId,
  build_id: buildId,
  report_reference: `build-artifacts/${buildId}/build-report.json`,
  compatibility: "report_match" as const,
  reason_code: "report_matches",
  expected_index_fingerprint: indexFingerprint,
  document_manifest_hash: manifestHash,
  reported_index_fingerprint: indexFingerprint,
  reported_document_manifest_hash: manifestHash,
  report_status: "completed" as const,
  reported_text_records: 14,
  reported_image_records: 0,
  check_level: "report" as const,
  store_checked: false as const,
};
const incompatible = {
  ...candidate,
  candidate_id: otherBuildId,
  build_id: otherBuildId,
  report_reference: `build-artifacts/${otherBuildId}/build-report.json`,
  compatibility: "mismatch" as const,
  reason_code: "identity_mismatch",
};
const inventory: BootstrapInventoryPage = {
  expected_index_fingerprint: indexFingerprint,
  document_manifest_hash: manifestHash,
  candidates: [candidate, incompatible],
  total_candidates: 2,
  offset: 0,
  limit: 25,
  complete: true,
  reason_code: "inventory_complete",
  check_level: "report",
  store_checked: false,
};

function state(status: IndexSelectionState["selection_status"] = "unselected", revision = 0): IndexSelectionState {
  const selection = status === "selected" || status === "stale" ? {
    build_id: buildId,
    report_reference: `build-artifacts/${buildId}/build-report.json`,
    document_manifest_hash: manifestHash,
    index_fingerprint: indexFingerprint,
    report_sha256: reportHash,
    configuration_snapshot_hash: "1".repeat(64),
    run_fingerprint: "2".repeat(64),
    report_status: "completed" as const,
    selection_revision: revision,
  } : null;
  return {
    selection_status: status,
    revision,
    selection,
    legacy_report_reference: null,
    reason_code: status === "unselected" ? "index_not_selected" : status === "stale" ? "manifest_changed" : "selection_verified",
    check_level: "report",
    store_checked: false,
  };
}

function json(value: unknown, status = 200): Response {
  return Response.json(value, { status });
}

function mount(token: string | null, inventoryPage: BootstrapInventoryPage | null = inventory) {
  return render(<MemoryRouter><IndexSelectionPanel token={token} preview={preview} resolvedConfig={config} inventoryPage={inventoryPage} /></MemoryRouter>);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("IndexSelectionPanel", () => {
  it("keeps selection disconnected until an ordinary runtime token is supplied", () => {
    const fetcher = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetcher);
    mount(null);
    expect(screen.getByText("Connect to the ordinary runtime to inspect or change its active report.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Select report" })).not.toBeInTheDocument();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("selects only a compatible candidate with the ordinary token and confirms report-level state", async () => {
    let selected = false;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const path = String(input);
      if (path !== "/api/v1/index/selection") return json({ error: { code: "unexpected_route" } }, 404);
      if (init?.method === "POST") { selected = true; return json(state("selected", 1)); }
      return json(selected ? state("selected", 1) : state());
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup();
    mount("ordinary-test-token");
    expect(await screen.findByText("No index is selected.")).toBeInTheDocument();
    expect(screen.getByText(buildId)).toBeInTheDocument();
    expect(screen.queryByText(otherBuildId)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Manage source documents" })).toHaveAttribute("href", "/documents");
    await user.click(screen.getByRole("button", { name: "Select report" }));
    expect(await screen.findByText("Index selected. You can now ask your PDFs.")).toBeInTheDocument();
    expect(screen.getByText("About index checks")).toBeInTheDocument();
    const posts = fetcher.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(posts).toHaveLength(1);
    expect(JSON.parse(String(posts[0]?.[1]?.body))).toMatchObject({
      build_id: buildId,
      expected_manifest_hash: manifestHash,
      expected_index_fingerprint: indexFingerprint,
      resolved_config: config,
      expected_revision: 0,
    });
    expect(fetcher.mock.calls.every(([path]) => String(path) === "/api/v1/index/selection")).toBe(true);
    expect(fetcher.mock.calls.every(([, init]) => new Headers(init?.headers).get("Authorization") === "Bearer ordinary-test-token")).toBe(true);
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });

  it("reads the selection back after an uncertain response without repeating the mutation", async () => {
    let reads = 0;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (_input, init) => {
      if (init?.method === "POST") throw new TypeError("connection lost");
      reads += 1;
      return json(reads === 1 ? state() : state("selected", 1));
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup();
    mount("ordinary-test-token");
    await screen.findByText("No index is selected.");
    await user.click(screen.getByRole("button", { name: "Select report" }));
    expect(await screen.findByText("The selection request response was interrupted; the runtime confirms the selected report.")).toBeInTheDocument();
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
    expect(fetcher.mock.calls.filter(([, init]) => init?.method !== "POST")).toHaveLength(2);
    expect(reads).toBe(2);
  });

  it("does not expose stale candidates as ready and shows the persisted identity", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(json(state("stale", 2))));
    mount("ordinary-test-token");
    expect(await screen.findByText(/Your PDFs or build settings changed/)).toBeInTheDocument();
    expect(screen.getAllByText(buildId).length).toBeGreaterThan(1);
    expect(screen.queryByText(/Ready to query/i)).not.toBeInTheDocument();
    expect(screen.getByText("Documents changed.")).toBeInTheDocument();
  });

  it("does not offer a candidate when the preview and inventory source identities differ", async () => {
    const mismatchedInventory = { ...inventory, document_manifest_hash: "9".repeat(64) };
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(json(state())));
    mount("ordinary-test-token", mismatchedInventory);
    await screen.findByText("No index is selected.");
    expect(screen.queryByRole("button", { name: "Select report" })).not.toBeInTheDocument();
    expect(screen.getByText(/No matching completed index/)).toBeInTheDocument();
  });

  it("prevents duplicate selection submissions while a request is pending", async () => {
    let selected = false;
    let resolvePost!: (response: Response) => void;
    const fetcher = vi.fn<typeof fetch>().mockImplementation((_input, init) => {
      if (init?.method === "POST") return new Promise<Response>((resolve) => { resolvePost = (response) => { selected = true; resolve(response); }; });
      return Promise.resolve(selected ? json(state("selected", 1)) : json(state()));
    });
    vi.stubGlobal("fetch", fetcher);
    mount("ordinary-test-token");
    await screen.findByText("No index is selected.");
    const button = screen.getByRole("button", { name: "Select report" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
    resolvePost(json(state("selected", 1)));
    await waitFor(() => expect(screen.getByText("Index selected. You can now ask your PDFs.")).toBeInTheDocument());
  });

  it("keeps selection errors visible and does not persist the token", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (_input, init) =>
      init?.method === "POST" ? json({ error: { code: "selection_revision_conflict" } }, 409) : json(state()));
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup();
    mount("ordinary-test-token");
    await screen.findByText("No index is selected.");
    await user.click(screen.getByRole("button", { name: "Select report" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });
});
