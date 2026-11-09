import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { BootstrapApiClient } from "../../api/client";
import type { BootstrapDocumentsResponse } from "../../api/types";
import { ChatPdfAttachment } from "./ChatPdfAttachment";

const documentRecord = {
  document_id: `doc_${"d".repeat(64)}`,
  relative_path: "Orbit Notes.pdf",
  size_bytes: 128,
  sha256: "e".repeat(64),
};

function inventory(documents: typeof documentRecord[] = [], manifest = "a".repeat(64)): BootstrapDocumentsResponse {
  return {
    inventory_status: "available", documents, manifest_hash: manifest, total: documents.length,
    limit: 100, offset: 0, complete: true, max_upload_bytes: 1024 * 1024, max_pages: 200,
  };
}

function response(value: unknown, status = 200): Response {
  return Response.json(value, { status });
}

function mount(fetcher: ReturnType<typeof vi.fn<typeof fetch>>, before = inventory()) {
  const onInventoryChange = vi.fn();
  render(<MemoryRouter><ChatPdfAttachment
    client={new BootstrapApiClient({ localGateway: true, fetcher })}
    inventory={before}
    inventoryLoading={false}
    inventoryError={null}
    tokenAvailable
    requestPending={false}
    onInventoryChange={onInventoryChange}
  /></MemoryRouter>);
  return onInventoryChange;
}

describe("Chat PDF attachment", () => {
  it("requires an explicit add, confirms the shared manifest and links to real preparation", async () => {
    let uploaded = false;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const path = String(input);
      if (path.startsWith("/bootstrap-api/v1/bootstrap/documents?") && init?.method !== "POST") {
        return response(uploaded ? inventory([documentRecord], "b".repeat(64)) : inventory());
      }
      if (path === "/bootstrap-api/v1/bootstrap/documents" && init?.method === "POST") {
        uploaded = true;
        return response({ outcome: "imported", document: documentRecord, manifest_hash: "b".repeat(64), max_upload_bytes: 1024 * 1024, error_code: null }, 201);
      }
      return response({ error: { code: "not_found" } }, 404);
    });
    const onInventoryChange = mount(fetcher);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Add a PDF to your shared Library" }));
    expect(screen.getByText(/Shared collection · 0 PDFs/)).toBeInTheDocument();
    expect(screen.getByText(/does not limit retrieval to this conversation/)).toBeInTheDocument();
    const file = new File(["%PDF-test bytes"], "Orbit Notes.pdf", { type: "application/pdf" });
    await user.upload(screen.getByLabelText("Choose a PDF"), file);
    expect(screen.getByText("Orbit Notes.pdf")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add PDF" })).toBeEnabled();
    expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Add PDF" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Prepare the collection in Library");
    expect(screen.getByRole("link", { name: "Prepare documents in Library" })).toHaveAttribute("href", "/documents");
    expect(onInventoryChange).toHaveBeenCalledWith(inventory([documentRecord], "b".repeat(64)));
    const post = fetcher.mock.calls.find(([url, init]) => String(url) === "/bootstrap-api/v1/bootstrap/documents" && init?.method === "POST");
    expect(post?.[1]?.body).toBe(file);
    expect(fetcher.mock.calls.filter(([url, init]) => String(url) === "/bootstrap-api/v1/bootstrap/documents" && init?.method === "POST")).toHaveLength(1);
    expect(fetcher.mock.calls.every(([, init]) => !new Headers(init?.headers).has("Authorization"))).toBe(true);
    expect(fetcher.mock.calls.some(([url]) => String(url).includes("/preview") || String(url).includes("/jobs"))).toBe(false);
  });

  it("rejects invalid filenames locally and never sends them", async () => {
    const fetcher = vi.fn<typeof fetch>();
    mount(fetcher);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Add a PDF to your shared Library" }));
    await user.upload(screen.getByLabelText("Choose a PDF"), new File(["%PDF"], "bad:name.pdf", { type: "application/pdf" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("simple filename");
    expect(screen.getByRole("button", { name: "Add PDF" })).toBeDisabled();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it.each(["empty", "oversized"] as const)("rejects an %s PDF before upload", async (kind) => {
    const fetcher = vi.fn<typeof fetch>();
    mount(fetcher);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Add a PDF to your shared Library" }));
    const file = kind === "empty"
      ? new File([], "Empty.pdf", { type: "application/pdf" })
      : new File([new Uint8Array(1024 * 1024 + 1)], "Large.pdf", { type: "application/pdf" });
    await user.upload(screen.getByLabelText("Choose a PDF"), file);
    expect(await screen.findByRole("alert")).toHaveTextContent(kind === "empty" ? "selected file is empty" : "configured 1.0 MiB upload limit");
    expect(screen.getByRole("button", { name: "Add PDF" })).toBeDisabled();
    expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("supports opening and closing the attachment panel from the keyboard", async () => {
    const fetcher = vi.fn<typeof fetch>();
    mount(fetcher);
    const user = userEvent.setup();
    const trigger = screen.getByRole("button", { name: "Add a PDF to your shared Library" });
    await user.tab();
    expect(trigger).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    const close = screen.getByRole("button", { name: "Close PDF upload" });
    await user.tab();
    expect(close).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });

  it("holds uncertain uploads until explicit inventory refresh without retrying", async () => {
    let refreshed = false;
    let documentReads = 0;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const path = String(input);
      if (path.startsWith("/bootstrap-api/v1/bootstrap/documents?") && init?.method !== "POST") {
        documentReads += 1;
        return response(refreshed && documentReads >= 2 ? inventory([documentRecord], "b".repeat(64)) : inventory());
      }
      if (path === "/bootstrap-api/v1/bootstrap/documents" && init?.method === "POST") {
        return response({ outcome: "committed_inventory_unavailable", document: documentRecord, manifest_hash: null, max_upload_bytes: 1024 * 1024, error_code: "manifest_confirmation_failed" }, 503);
      }
      return response({ error: { code: "not_found" } }, 404);
    });
    mount(fetcher);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Add a PDF to your shared Library" }));
    await user.upload(screen.getByLabelText("Choose a PDF"), new File(["%PDF"], "Orbit Notes.pdf", { type: "application/pdf" }));
    await user.click(screen.getByRole("button", { name: "Add PDF" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("may have completed");
    expect(screen.getByRole("button", { name: "Add PDF" })).toBeDisabled();
    expect(fetcher.mock.calls.filter(([url, init]) => String(url) === "/bootstrap-api/v1/bootstrap/documents" && init?.method === "POST")).toHaveLength(1);
    refreshed = true;
    await user.click(screen.getByRole("button", { name: "Refresh collection" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("PDF is confirmed in your shared Library"));
    expect(screen.getByRole("button", { name: "Add PDF" })).toBeDisabled();
    expect(fetcher.mock.calls.filter(([url, init]) => String(url) === "/bootstrap-api/v1/bootstrap/documents" && init?.method === "POST")).toHaveLength(1);
  });
});
