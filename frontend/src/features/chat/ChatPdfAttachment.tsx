import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { BootstrapApiClient } from "../../api/client";
import type { BootstrapDocumentsResponse } from "../../api/types";
import {
  documentUploadFailure,
  uploadOutcomeUncertain,
  uploadPdfWithReadback,
  validatePdfUpload,
} from "../documents/pdfUpload";

interface Props {
  client: BootstrapApiClient;
  inventory: BootstrapDocumentsResponse | null;
  inventoryLoading: boolean;
  inventoryError: string | null;
  tokenAvailable: boolean;
  requestPending: boolean;
  onInventoryChange: (inventory: BootstrapDocumentsResponse) => void;
}

export function ChatPdfAttachment({
  client,
  inventory,
  inventoryLoading,
  inventoryError,
  tokenAvailable,
  requestPending,
  onInventoryChange,
}: Props) {
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const [reviewRequired, setReviewRequired] = useState(false);
  const [pendingDocument, setPendingDocument] = useState<{ document_id: string; sha256: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const sequence = useRef(0);
  const submitting = useRef(false);

  useEffect(() => () => {
    sequence.current += 1;
    controllerRef.current?.abort();
  }, []);

  async function refreshCollection() {
    if (refreshing) return;
    setRefreshing(true);
    setError(null);
    try {
      const value = await client.listDocuments({ limit: 100, offset: 0 });
      onInventoryChange(value);
      if (pendingDocument && value.documents.some((document) => document.document_id === pendingDocument.document_id && document.sha256 === pendingDocument.sha256)) {
        setNeedsRefresh(false);
        setReviewRequired(false);
        setPendingDocument(null);
        setFile(null);
        if (input.current) input.current.value = "";
        setError(null);
        setNotice("The PDF is confirmed in your shared Library. Prepare the collection there before asking questions.");
      } else {
        setReviewRequired(true);
        setNotice("The collection refreshed. Check Library for this PDF before deciding whether to upload again.");
      }
    } catch {
      setError("The PDF collection could not be refreshed. Try again before uploading.");
    } finally {
      setRefreshing(false);
    }
  }

  async function addPdf() {
    if (!tokenAvailable || !file || !inventory || inventoryLoading || inventoryError || requestPending ||
      uploading || submitting.current || needsRefresh) return;
    const validation = validatePdfUpload(file, inventory.max_upload_bytes);
    if (validation) { setError(validation); return; }

    submitting.current = true;
    const request = ++sequence.current;
    const controller = new AbortController();
    controllerRef.current = controller;
    setUploading(true);
    setError(null);
    setNotice(null);
    try {
      const result = await uploadPdfWithReadback(client, file, inventory, {
        limit: 100,
        offset: 0,
        signal: controller.signal,
      });
      if (request !== sequence.current) return;
      setFile(null);
      if (input.current) input.current.value = "";
      if (result.inventory) onInventoryChange(result.inventory);
      if (result.kind === "manifest-confirmed") {
        setNeedsRefresh(false);
        setReviewRequired(false);
        setNotice(`${result.upload.document.relative_path} was added to your shared Library. Prepare the collection in Library before asking questions.`);
      } else if (result.kind === "inventory-confirmed") {
        setNeedsRefresh(false);
        setReviewRequired(false);
        setNotice(`${result.upload.document.relative_path} is confirmed in your shared Library. Prepare the collection in Library before asking questions.`);
      } else {
        setNeedsRefresh(true);
        setPendingDocument({ document_id: result.upload.document.document_id, sha256: result.upload.document.sha256 });
        setError("The upload may have completed, but the collection could not confirm it. Refresh the collection before trying another upload.");
      }
      if (result.kind !== "needs-refresh") {
        setFile(null);
        if (input.current) input.current.value = "";
      }
    } catch (reason) {
      if (request !== sequence.current || controller.signal.aborted) return;
      setError(documentUploadFailure(reason));
      if (uploadOutcomeUncertain(reason)) { setNeedsRefresh(true); setReviewRequired(false); setPendingDocument(null); }
    } finally {
      if (request === sequence.current) {
        submitting.current = false;
        setUploading(false);
        controllerRef.current = null;
      }
    }
  }

  const selectedFileError = validatePdfUpload(file, inventory?.max_upload_bytes ?? null);
  const canUpload = Boolean(tokenAvailable && inventory && !inventoryLoading && !inventoryError &&
    !requestPending && !uploading && !needsRefresh && file && !selectedFileError);

  return <div className="chat-attachment">
    <button
      className="chat-attachment__trigger"
      type="button"
      aria-label="Add a PDF to your shared Library"
      aria-expanded={open}
      aria-controls="chat-pdf-attachment-panel"
      onClick={() => setOpen((value) => !value)}
      disabled={requestPending || uploading}
      title="Add a PDF to your shared Library"
    >
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8.5 12.5 6.7-6.7a3.2 3.2 0 0 1 4.5 4.5l-8.1 8.1a5 5 0 0 1-7.1-7.1l8.1-8.1"/><path d="m7.8 14.2 7.1-7.1"/></svg>
      <span>Attach PDF</span>
    </button>
    {open ? <section id="chat-pdf-attachment-panel" className="chat-attachment__panel" aria-label="Add a PDF to your Library">
      <div className="chat-attachment__heading"><strong>Add a PDF</strong><button type="button" aria-label="Close PDF upload" onClick={() => setOpen(false)}>×</button></div>
      <p className="chat-attachment__collection">
        {inventoryLoading ? "Checking your shared collection…" : inventoryError ? "Your PDF collection is unavailable." : inventory ? `Shared collection · ${inventory.total} ${inventory.total === 1 ? "PDF" : "PDFs"}` : "Connect to your PDF collection in Library."}
      </p>
      <label htmlFor="chat-pdf-file">Choose a PDF</label>
      <input
        ref={input}
        id="chat-pdf-file"
        type="file"
        accept="application/pdf,.pdf"
        disabled={!tokenAvailable || inventoryLoading || Boolean(inventoryError) || uploading || requestPending || needsRefresh}
        onChange={(event) => {
          const chosen = event.currentTarget.files?.[0] ?? null;
          setFile(chosen);
          setError(chosen ? validatePdfUpload(chosen, inventory?.max_upload_bytes ?? null) : null);
          setNotice(null);
        }}
      />
      {file ? <p className="chat-attachment__filename">{file.name}<span>{Math.ceil(file.size / 1024)} KB</span></p> : null}
      {inventory ? <p className="chat-attachment__limits">Up to {formatMiB(inventory.max_upload_bytes)} per PDF · {inventory.max_pages} pages</p> : null}
      <p className="chat-attachment__shared">PDFs are shared across Library. Adding one here does not limit retrieval to this conversation or start preparation.</p>
      {error ? <p className="chat-attachment__error" role="alert">{error}</p> : null}
      {notice ? <p className="chat-attachment__notice" role="status">{notice}</p> : null}
      {uploading ? <p className="chat-attachment__notice" role="status">Adding PDF and confirming the collection…</p> : null}
      <div className="chat-attachment__actions">
        {needsRefresh ? <button className="button button--quiet" type="button" onClick={() => void refreshCollection()} disabled={refreshing}>{refreshing ? "Refreshing…" : "Refresh collection"}</button> : null}
        <button className="button button--primary" type="button" onClick={() => void addPdf()} disabled={!canUpload}>{uploading ? "Adding…" : "Add PDF"}</button>
      </div>
      {reviewRequired && needsRefresh ? <div className="chat-attachment__review">
        <Link to="/documents">Open Library and check the PDF</Link>
        <button type="button" onClick={() => { setNeedsRefresh(false); setReviewRequired(false); setError(null); }} disabled={requestPending}>I checked; allow a retry</button>
      </div> : null}
      {notice ? <Link className="chat-attachment__prepare" to="/documents">Prepare documents in Library</Link> : null}
      {!tokenAvailable ? <Link className="chat-attachment__prepare" to="/documents">Open Library to connect</Link> : null}
    </section> : null}
  </div>;
}

function formatMiB(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}
