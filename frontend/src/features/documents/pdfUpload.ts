import { ApiError, BootstrapApiClient } from "../../api/client";
import type { BootstrapDocumentImportResponse, BootstrapDocumentsResponse } from "../../api/types";
import { apiErrorText } from "../../components/apiErrorText";

export const PDF_FILENAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9 _().-]{0,114}\.pdf$/;

export function validatePdfUpload(file: File | null, maxUploadBytes: number | null): string | null {
  if (!file || !PDF_FILENAME_PATTERN.test(file.name)) {
    return "Use a simple filename with letters, numbers, spaces, parentheses, underscores or hyphens, ending in lowercase .pdf.";
  }
  if (file.size < 1) return "The selected file is empty.";
  if (maxUploadBytes !== null && file.size > maxUploadBytes) {
    return `This file exceeds the configured ${formatBytes(maxUploadBytes)} upload limit.`;
  }
  return null;
}

export function documentUploadFailure(error: unknown): string {
  if (!(error instanceof ApiError)) return "The local document request failed.";
  if (error.kind === "network" || error.kind === "non_json" || error.kind === "malformed_response") {
    return "The upload response could not be confirmed. Refresh the source inventory before trying again.";
  }
  if (error.status === 409 && error.code === "document_name_conflict") return "A PDF with this name or document stem already exists in the trusted source.";
  if (error.status === 409 && error.code === "document_source_busy") return "A source change or index build is in progress. Wait for it to finish, then refresh the inventory.";
  if (error.status === 413 && error.code === "upload_too_large") return "The PDF exceeds the configured upload size limit.";
  if (error.status === 413 && error.code === "pdf_page_limit_exceeded") return "This PDF exceeds the 200-page import limit.";
  if (error.status === 400 && ["pdf_invalid", "pdf_signature_invalid", "pdf_structure_invalid"].includes(error.code)) return "The file is not a readable PDF.";
  if (error.status === 400 && error.code === "pdf_encrypted_or_unsupported") return "Encrypted or unsupported PDFs cannot be imported.";
  if (error.status === 415) return "The server rejected the file content type or encoding.";
  if (error.status !== null && error.status >= 500) return "The server could not confirm the upload. Refresh the inventory before trying again.";
  if (error.status === 400) return "The server rejected the filename or PDF request. Use a simple filename ending in lowercase .pdf.";
  return apiErrorText(error);
}

export function uploadOutcomeUncertain(error: unknown): boolean {
  return !(error instanceof ApiError) || error.kind === "network" || error.kind === "non_json" ||
    error.kind === "malformed_response" || error.status === null || error.status >= 500;
}

export type PdfUploadReadback =
  | { kind: "manifest-confirmed"; upload: BootstrapDocumentImportResponse; inventory: BootstrapDocumentsResponse }
  | { kind: "inventory-confirmed"; upload: BootstrapDocumentImportResponse; inventory: BootstrapDocumentsResponse }
  | { kind: "needs-refresh"; upload: BootstrapDocumentImportResponse; inventory: BootstrapDocumentsResponse | null };

/** Performs exactly one state-changing upload and one authenticated inventory readback. */
export async function uploadPdfWithReadback(
  client: BootstrapApiClient,
  file: File,
  previousInventory: BootstrapDocumentsResponse,
  options: { limit: number; offset: number; signal: AbortSignal },
): Promise<PdfUploadReadback> {
  const upload = await client.uploadDocument(file, { signal: options.signal });
  let inventory: BootstrapDocumentsResponse;
  try {
    inventory = await client.listDocuments({ ...options });
  } catch (error) {
    if (options.signal.aborted) throw error;
    return { kind: "needs-refresh", upload, inventory: null };
  }

  if (upload.outcome === "imported" && upload.manifest_hash === inventory.manifest_hash) {
    return { kind: "manifest-confirmed", upload, inventory };
  }

  const visible = inventory.documents.some((document) =>
    document.document_id === upload.document.document_id && document.sha256 === upload.document.sha256,
  );
  if (visible && inventory.manifest_hash !== previousInventory.manifest_hash) {
    return { kind: "inventory-confirmed", upload, inventory };
  }
  return { kind: "needs-refresh", upload, inventory };
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
}
