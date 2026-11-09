import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

function mount(fetcher: ReturnType<typeof vi.fn<typeof fetch>>, before = inventory(), requestPending = false) {
  const onInventoryChange = vi.fn();
  render(<MemoryRouter><ChatPdfAttachment
    client={new BootstrapApiClient({ localGateway: true, fetcher })}
    inventory={before}
    inventoryLoading={false}
    inventoryError={null}
    tokenAvailable
    requestPending={requestPending}
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
    expect(trigger).toHaveFocus();
  });

  it("dismisses with Escape and restores focus when focus was inside the panel", async () => {
    const fetcher = vi.fn<typeof fetch>();
    mount(fetcher);
    const user = userEvent.setup();
    const trigger = screen.getByRole("button", { name: "Add a PDF to your shared Library" });
    await user.click(trigger);
    await user.tab();
    await user.tab();
    expect(screen.getByLabelText("Choose a PDF")).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByLabelText("Choose a PDF")).not.toBeInTheDocument();
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).toHaveFocus();
  });

  it("keeps panel dismissal focusable while chat generation is pending", async () => {
    const fetcher = vi.fn<typeof fetch>();
    mount(fetcher, inventory(), true);
    const user = userEvent.setup();
    const trigger = screen.getByRole("button", { name: "Add a PDF to your shared Library" });
    expect(trigger).toBeEnabled();
    await user.click(trigger);
    expect(screen.getByLabelText("Choose a PDF")).toBeDisabled();
    expect(screen.getByText("Wait for the current answer to finish before adding a PDF.")).toBeInTheDocument();
    await user.tab();
    const close = screen.getByRole("button", { name: "Close PDF upload" });
    expect(close).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).toHaveFocus();
    expect(close).not.toBeInTheDocument();
  });

  it("does not dismiss or expose the close action while an upload is running", async () => {
    let uploaded = false;
    let releaseUpload: ((value: Response) => void) | undefined;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const path = String(input);
      if (path.startsWith("/bootstrap-api/v1/bootstrap/documents?") && init?.method !== "POST") {
        return response(uploaded ? inventory([documentRecord], "b".repeat(64)) : inventory());
      }
      if (path === "/bootstrap-api/v1/bootstrap/documents" && init?.method === "POST") {
        return await new Promise<Response>((resolve) => {
          releaseUpload = (value) => { uploaded = true; resolve(value); };
        });
      }
      return response({ error: { code: "not_found" } }, 404);
    });
    mount(fetcher);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Add a PDF to your shared Library" }));
    await user.upload(screen.getByLabelText("Choose a PDF"), new File(["%PDF"], "Orbit Notes.pdf", { type: "application/pdf" }));
    await user.click(screen.getByRole("button", { name: "Add PDF" }));
    expect(screen.getByRole("button", { name: "Close PDF upload" })).toBeDisabled();
    await user.keyboard("{Escape}");
    expect(screen.getByLabelText("Choose a PDF")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add a PDF to your shared Library" })).toHaveAttribute("aria-expanded", "true");
    releaseUpload?.(response({ outcome: "imported", document: documentRecord, manifest_hash: "b".repeat(64), max_upload_bytes: 1024 * 1024, error_code: null }, 201));
    expect(await screen.findByText(/was added to your shared Library/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("status")).toHaveFocus());
  });

  it("moves focus to the failure after a rejected upload", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      if (String(input) === "/bootstrap-api/v1/bootstrap/documents" && init?.method === "POST") {
        return response({ error: { code: "document_name_conflict" } }, 409);
      }
      return response(inventory());
    });
    mount(fetcher);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Add a PDF to your shared Library" }));
    await user.upload(screen.getByLabelText("Choose a PDF"), new File(["%PDF"], "Orbit Notes.pdf", { type: "application/pdf" }));
    await user.click(screen.getByRole("button", { name: "Add PDF" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("already exists");
    await waitFor(() => expect(alert).toHaveFocus());
  });

  it("keeps the file, upload, refresh and Library recovery controls keyboard reachable", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      if (String(input) === "/bootstrap-api/v1/bootstrap/documents" && init?.method === "POST") {
        return response({ outcome: "imported", document: documentRecord, manifest_hash: "b".repeat(64), max_upload_bytes: 1024 * 1024, error_code: null }, 201);
      }
      if (String(input).startsWith("/bootstrap-api/v1/bootstrap/documents?") && init?.method !== "POST") {
        return response(inventory());
      }
      return response({ error: { code: "not_found" } }, 404);
    });
    mount(fetcher);
    const user = userEvent.setup();
    const trigger = screen.getByRole("button", { name: "Add a PDF to your shared Library" });
    await user.click(trigger);
    await user.tab();
    await user.tab();
    const fileInput = screen.getByLabelText("Choose a PDF");
    expect(fileInput).toHaveFocus();
    await user.upload(fileInput, new File(["%PDF"], "Orbit Notes.pdf", { type: "application/pdf" }));
    await user.tab();
    const add = screen.getByRole("button", { name: "Add PDF" });
    expect(add).toHaveFocus();
    await user.keyboard("{Enter}");
    const refresh = await screen.findByRole("button", { name: "Refresh collection" });
    await waitFor(() => expect(refresh).toHaveFocus());
    await user.click(refresh);
    expect(await screen.findByText(/Check Library for this PDF/)).toBeInTheDocument();
    await user.tab();
    expect(screen.getByRole("link", { name: "Open Library and check the PDF" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "I checked; allow a retry" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("link", { name: "Prepare documents in Library" })).toHaveFocus();
  });

  it("keeps repeated Add clicks to one upload while the request is pending", async () => {
    let uploaded = false;
    let releaseUpload: ((value: Response) => void) | undefined;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const path = String(input);
      if (path.startsWith("/bootstrap-api/v1/bootstrap/documents?") && init?.method !== "POST") {
        return response(uploaded ? inventory([documentRecord], "b".repeat(64)) : inventory());
      }
      if (path === "/bootstrap-api/v1/bootstrap/documents" && init?.method === "POST") {
        return await new Promise<Response>((resolve) => { releaseUpload = resolve; });
      }
      return response({ error: { code: "not_found" } }, 404);
    });
    mount(fetcher);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Add a PDF to your shared Library" }));
    await user.upload(screen.getByLabelText("Choose a PDF"), new File(["%PDF"], "Orbit Notes.pdf", { type: "application/pdf" }));
    const addButton = screen.getByRole("button", { name: "Add PDF" });
    await user.click(addButton);
    expect(await screen.findByText("Adding PDF and confirming the collection…")).toBeInTheDocument();
    expect(addButton).toBeDisabled();
    fireEvent.click(addButton);
    expect(fetcher.mock.calls.filter(([url, init]) => String(url) === "/bootstrap-api/v1/bootstrap/documents" && init?.method === "POST")).toHaveLength(1);
    uploaded = true;
    releaseUpload?.(response({ outcome: "imported", document: documentRecord, manifest_hash: "b".repeat(64), max_upload_bytes: 1024 * 1024, error_code: null }, 201));
    expect(await screen.findByText(/was added to your shared Library/)).toBeInTheDocument();
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

  it("does not let a manually authorized retry inherit the prior attempt's document identity", async () => {
    const firstDocument = { ...documentRecord, document_id: `doc_${"1".repeat(64)}`, relative_path: "First.pdf", sha256: "1".repeat(64) };
    const secondDocument = { ...documentRecord, document_id: `doc_${"2".repeat(64)}`, relative_path: "Second.pdf", sha256: "2".repeat(64) };
    let uploadCount = 0;
    let inventoryReadCount = 0;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const path = String(input);
      if (path.startsWith("/bootstrap-api/v1/bootstrap/documents?") && init?.method !== "POST") {
        inventoryReadCount += 1;
        if (inventoryReadCount <= 2) return response(inventory());
        return response(inventory([firstDocument], "b".repeat(64)));
      }
      if (path === "/bootstrap-api/v1/bootstrap/documents" && init?.method === "POST") {
        uploadCount += 1;
        const document = uploadCount === 1 ? firstDocument : secondDocument;
        return response({
          outcome: "imported", document,
          manifest_hash: uploadCount === 1 ? "b".repeat(64) : "c".repeat(64),
          max_upload_bytes: 1024 * 1024, error_code: null,
        }, 201);
      }
      return response({ error: { code: "not_found" } }, 404);
    });
    mount(fetcher);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Add a PDF to your shared Library" }));
    await user.upload(screen.getByLabelText("Choose a PDF"), new File(["%PDF first"], "First.pdf", { type: "application/pdf" }));
    await user.click(screen.getByRole("button", { name: "Add PDF" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("may have completed");

    await user.click(screen.getByRole("button", { name: "Refresh collection" }));
    expect(await screen.findByText(/Check Library for this PDF/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "I checked; allow a retry" }));
    expect(screen.getByLabelText("Choose a PDF")).toHaveFocus();
    await user.upload(screen.getByLabelText("Choose a PDF"), new File(["%PDF second"], "Second.pdf", { type: "application/pdf" }));
    await user.click(screen.getByRole("button", { name: "Add PDF" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("may have completed");

    await user.click(screen.getByRole("button", { name: "Refresh collection" }));
    expect(await screen.findByText(/Check Library for this PDF/)).toBeInTheDocument();
    expect(screen.queryByText(/PDF is confirmed in your shared Library/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add PDF" })).toBeDisabled();
    expect(uploadCount).toBe(2);
  });
});
