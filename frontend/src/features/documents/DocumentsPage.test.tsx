import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import App from "../../App";
import { REQUIRED_RESOLVED_CONFIG_FIELDS } from "../index-builds/configValidation";

const status = { mode: "bootstrap", status: "running", accepting_jobs: true, worker_count: 1, queue_capacity: 2 };
const record = {
  document_id: `doc_${"d".repeat(64)}`, relative_path: "Research note.pdf", size_bytes: 17, sha256: "e".repeat(64),
};
function inventory(documents: Array<typeof record> = []) {
  return {
    inventory_status: "available", documents, manifest_hash: "a".repeat(64), total: documents.length,
    limit: 50, offset: 0, complete: true, max_upload_bytes: 1024 * 1024, max_pages: 200,
  };
}
function json(value: unknown, statusCode = 200): Response { return Response.json(value, { status: statusCode }); }
function stubBootstrapFetch(fetcher: ReturnType<typeof vi.fn<typeof fetch>>) {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockImplementation((input, init) => {
    if (String(input) === "/bootstrap-api/v1/bootstrap/configuration") {
      const resolved: Record<string, unknown> = Object.fromEntries(REQUIRED_RESOLVED_CONFIG_FIELDS.map((key) => [key, null]));
      resolved.pdf_dir = "trusted-source";
      resolved.reset_collections_on_start = false;
      resolved.workbench_policy = { allow_destructive_index_reset: false, allow_collection_delete: false };
      return Promise.resolve(json({ schema_version: "1", configuration_source: "trusted_server_defaults", resolved_config: resolved }));
    }
    if (String(input).startsWith("/bootstrap-api/v1/bootstrap/jobs?")) {
      const query = new URLSearchParams(String(input).split("?", 2)[1]);
      return Promise.resolve(json({ jobs: [], limit: Number(query.get("limit")), offset: Number(query.get("offset")) }));
    }
    return fetcher(input, init);
  }));
}
function mount() {
  const router = createMemoryRouter([{ path: "*", element: <App /> }], { initialEntries: ["/documents"] });
  render(<RouterProvider router={router} />);
  return router;
}
async function connect(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Bootstrap bearer token"), "bootstrap-token-for-documents");
  await user.click(screen.getByRole("button", { name: "Connect to bootstrap" }));
  await screen.findByText("Bootstrap connected", { exact: true });
}

describe("Documents page", () => {
  it("shows the disconnected source state and one working Library navigation item", () => {
    const fetcher = vi.fn<typeof fetch>();
    stubBootstrapFetch(fetcher);
    mount();
    expect(screen.getByRole("heading", { name: "Documents" })).toBeInTheDocument();
    expect(screen.getByText("Connect to view your PDFs.")).toBeInTheDocument();
    expect(screen.getByLabelText("PDF file")).toBeDisabled();
    expect(screen.getAllByRole("link", { name: "Library" })).toHaveLength(1);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("authenticates, imports raw PDF bytes, refreshes the server inventory, and disconnects without persistence", async () => {
    let uploaded = false;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/bootstrap-api/v1/bootstrap/status") return json(status);
      if (path.startsWith("/bootstrap-api/v1/bootstrap/documents?") && init?.method !== "POST") {
        return json(uploaded ? { ...inventory([record]), manifest_hash: "b".repeat(64) } : inventory());
      }
      if (path === "/bootstrap-api/v1/bootstrap/documents" && init?.method === "POST") {
        uploaded = true;
        return json({ outcome: "imported", document: record, manifest_hash: "b".repeat(64), max_upload_bytes: 1024 * 1024, error_code: null }, 201);
      }
      return json({ error: { code: "not_found" } }, 404);
    });
    stubBootstrapFetch(fetcher);
    localStorage.clear(); sessionStorage.clear();
    const user = userEvent.setup();
    const router = mount();
    await connect(user);
    await screen.findByText("Your library starts here.");
    const uploadLimit = document.querySelector(".documents-limit");
    expect(uploadLimit?.textContent).toContain("Up to 1.0 MiB");
    expect(uploadLimit?.textContent).toContain("200 pages");

    const file = new File(["%PDF-test payload"], "Research note.pdf", { type: "application/pdf" });
    await user.upload(screen.getByLabelText("PDF file"), file);
    await user.click(screen.getByRole("button", { name: "Add PDF" }));
    expect(await screen.findByRole("status", { name: "PDF import" })).toHaveTextContent("imported and confirmed in the source inventory");
    expect(await screen.findByText("Research note.pdf", { exact: true })).toBeInTheDocument();

    const uploadCall = fetcher.mock.calls.find(([url, init]) => String(url) === "/bootstrap-api/v1/bootstrap/documents" && init?.method === "POST");
    expect(uploadCall).toBeDefined();
    expect(uploadCall?.[1]?.body).toBe(file);
    expect(new Headers(uploadCall?.[1]?.headers).get("Authorization")).toBe("Bearer bootstrap-token-for-documents");
    expect(fetcher.mock.calls.filter(([url]) => String(url).startsWith("/bootstrap-api/v1/bootstrap/documents?")).length).toBeGreaterThanOrEqual(2);
    expect(fetcher.mock.calls.every(([url]) => !String(url).includes("bootstrap-token-for-documents"))).toBe(true);
    expect(localStorage.getItem("token")).toBeNull();
    expect(sessionStorage.getItem("token")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Disconnect bootstrap" }));
    expect(screen.getByLabelText("Bootstrap bearer token")).toHaveValue("");
    expect(screen.getByText("Connect to view your PDFs.")).toBeInTheDocument();
    expect(localStorage.getItem("bootstrapToken")).toBeNull();
    expect(sessionStorage.getItem("bootstrapToken")).toBeNull();

    await user.click(screen.getByRole("link", { name: "Advanced Research" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/research"));
    expect(screen.getByRole("heading", { name: "Advanced Research" })).toBeInTheDocument();
  });

  it("prevents duplicate submissions while an upload is in flight", async () => {
    let releaseUpload: ((response: Response) => void) | undefined;
    let uploaded = false;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/bootstrap-api/v1/bootstrap/status") return json(status);
      if (path.startsWith("/bootstrap-api/v1/bootstrap/documents?") && init?.method !== "POST") return json(uploaded ? { ...inventory([record]), manifest_hash: "b".repeat(64) } : inventory());
      if (path === "/bootstrap-api/v1/bootstrap/documents" && init?.method === "POST") {
        return await new Promise<Response>((resolve) => { releaseUpload = resolve; });
      }
      return json({ error: { code: "not_found" } }, 404);
    });
    stubBootstrapFetch(fetcher);
    const user = userEvent.setup(); mount(); await connect(user);
    await screen.findByText("Your library starts here.");
    await user.upload(screen.getByLabelText("PDF file"), new File(["%PDF-test payload"], "Research note.pdf", { type: "application/pdf" }));
    await user.click(screen.getByRole("button", { name: "Add PDF" }));
    expect(screen.getByRole("button", { name: "Adding PDF…" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Adding PDF…" }));
    expect(fetcher.mock.calls.filter(([url, init]) => String(url) === "/bootstrap-api/v1/bootstrap/documents" && init?.method === "POST")).toHaveLength(1);
    uploaded = true;
    releaseUpload?.(json({ outcome: "imported", document: record, manifest_hash: "b".repeat(64), max_upload_bytes: 1024 * 1024, error_code: null }, 201));
    await screen.findByRole("status", { name: "PDF import" });
  });

  it("renders safe upload errors and inventory failures without inventing documents", async () => {
    let failInventory = true;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/bootstrap-api/v1/bootstrap/status") return json(status);
      if (path.startsWith("/bootstrap-api/v1/bootstrap/documents?") && init?.method !== "POST") {
        return failInventory ? json({ error: { code: "document_manifest_unavailable" } }, 503) : json(inventory());
      }
      if (path === "/bootstrap-api/v1/bootstrap/documents" && init?.method === "POST") {
        return json({ error: { code: "document_name_conflict" } }, 409);
      }
      return json({ error: { code: "not_found" } }, 404);
    });
    stubBootstrapFetch(fetcher);
    const user = userEvent.setup(); mount(); await connect(user);
    expect(await screen.findByRole("alert")).toHaveTextContent("document_manifest_unavailable");
    failInventory = false;
    await user.click(screen.getByRole("button", { name: "Retry library" }));
    await screen.findByText("Your library starts here.");
    await user.upload(screen.getByLabelText("PDF file"), new File(["%PDF-test payload"], "Research note.pdf", { type: "application/pdf" }));
    await user.click(screen.getByRole("button", { name: "Add PDF" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already exists in the trusted source");
  });

  it("does not report a confirmed import when the server could not confirm its manifest", async () => {
    let documentReads = 0;
    const uncertain = {
      outcome: "committed_inventory_unavailable", document: record,
      manifest_hash: null, max_upload_bytes: 1024 * 1024, error_code: "manifest_confirmation_failed",
    };
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/bootstrap-api/v1/bootstrap/status") return json(status);
      if (path.startsWith("/bootstrap-api/v1/bootstrap/documents?") && init?.method !== "POST") {
        documentReads += 1;
        return json(inventory(documentReads >= 3 ? [record] : []));
      }
      if (path === "/bootstrap-api/v1/bootstrap/documents" && init?.method === "POST") return json(uncertain, 503);
      return json({ error: { code: "not_found" } }, 404);
    });
    stubBootstrapFetch(fetcher);
    const user = userEvent.setup(); mount(); await connect(user);
    await screen.findByText("Your library starts here.");
    await user.upload(screen.getByLabelText("PDF file"), new File(["%PDF-test payload"], "Research note.pdf", { type: "application/pdf" }));
    await user.click(screen.getByRole("button", { name: "Add PDF" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be confirmed by the current inventory");
    expect(screen.queryByText(/was imported and confirmed/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add PDF" })).toBeDisabled();
    expect(screen.getByLabelText("PDF file")).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByText("Research note.pdf", { exact: true })).toBeInTheDocument();
    expect(screen.getByLabelText("PDF file")).toBeEnabled();
    await user.upload(screen.getByLabelText("PDF file"), new File(["%PDF-test payload"], "Research note.pdf", { type: "application/pdf" }));
    expect(screen.getByRole("button", { name: "Add PDF" })).toBeEnabled();
  });

  it("rejects unsafe local filenames before making an upload request", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      if (String(input) === "/bootstrap-api/v1/bootstrap/status") return json(status);
      if (String(input).startsWith("/bootstrap-api/v1/bootstrap/documents?") && init?.method !== "POST") return json(inventory());
      return json({ error: { code: "not_found" } }, 404);
    });
    stubBootstrapFetch(fetcher);
    const user = userEvent.setup(); mount(); await connect(user);
    await screen.findByText("Your library starts here.");
    const unsafe = new File(["%PDF"], "bad:name.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("PDF file"), { target: { files: [unsafe] } });
    await user.click(screen.getByRole("button", { name: "Add PDF" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("simple filename");
    expect(fetcher.mock.calls.some(([url, init]) => String(url) === "/bootstrap-api/v1/bootstrap/documents" && init?.method === "POST")).toBe(false);
  });
  it("accepts one dropped PDF only after explicit submission and rejects multiple-file drops", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async input => String(input).endsWith("/status") ? json(status) : json(inventory()));
    stubBootstrapFetch(fetcher); const user = userEvent.setup(); mount(); await connect(user);
    await screen.findByText("Your library starts here.");
    const area = screen.getByRole("heading", { name: "Add a PDF" }).closest("section")!;
    const file = new File(["%PDF-test"], "Dropped.pdf", { type: "application/pdf" });
    fireEvent.drop(area, { dataTransfer: { files: { length: 1, item: () => file } } });
    expect(screen.getByText("Dropped.pdf")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add PDF" })).toBeEnabled();
    expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
    fireEvent.drop(area, { dataTransfer: { files: { length: 2 } } });
    expect(await screen.findByRole("alert")).toHaveTextContent("Add one PDF at a time");
  });

  it("blocks uploads and preparation when a refreshed source inventory cannot be verified", async () => {
    let unavailable = false;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async input => {
      if (String(input).endsWith("/status")) return json(status);
      if (String(input).includes("/documents?")) return unavailable ? json({ error: { code: "document_manifest_unavailable" } }, 503) : json(inventory([record]));
      return json({ error: { code: "not_found" } }, 404);
    });
    stubBootstrapFetch(fetcher); const user = userEvent.setup(); mount(); await connect(user);
    await screen.findByText(record.relative_path, { exact: true });
    unavailable = true; await user.click(screen.getByRole("button", { name: "Refresh" }));
    await screen.findByRole("button", { name: "Retry library" });
    expect(screen.getByLabelText("PDF file")).toBeDisabled();
    expect(screen.getByText("Library unavailable")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Prepare documents" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Ask PDFs/ })).not.toBeInTheDocument();
  });

  it("uses the actual server upload limit before sending PDF bytes", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async input => String(input).endsWith("/status") ? json(status) : json({ ...inventory(), max_upload_bytes: 4 }));
    stubBootstrapFetch(fetcher); const user = userEvent.setup(); mount(); await connect(user);
    await screen.findByText("Your library starts here.");
    await user.upload(screen.getByLabelText("PDF file"), new File(["%PDF-too-large"], "Large.pdf", { type: "application/pdf" }));
    await user.click(screen.getByRole("button", { name: "Add PDF" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/limit|exceeds|large/i);
    expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("opens a library PDF using its real document and manifest identities", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      if (String(input) === "/bootstrap-api/v1/bootstrap/status") return json(status);
      if (String(input).startsWith("/bootstrap-api/v1/bootstrap/documents?")) return json(inventory([record]));
      return json({ error: { code: "document_not_found" } }, 404);
    });
    stubBootstrapFetch(fetcher);
    const user = userEvent.setup();
    const router = mount();
    await connect(user);
    await user.click(await screen.findByRole("link", { name: "Preview" }));
    await waitFor(() => expect(router.state.location.pathname).toBe(`/documents/view/${record.document_id}`));
    expect(router.state.location.search).toBe(`?manifest_hash=${"a".repeat(64)}&page=1`);
    expect(await screen.findByRole("alert")).toHaveTextContent("original PDF is no longer available");
  });

});
