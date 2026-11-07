import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link, useLocation } from "react-router-dom";
import { ApiError, BootstrapApiClient } from "../../api/client";
import type { BootstrapDocumentRecord, BootstrapDocumentsResponse, BootstrapStatusResponse } from "../../api/types";
import { apiErrorText } from "../../components/apiErrorText";
import { BootstrapConnectionPanel } from "../index-builds/BootstrapConnectionPanel";
import { DocumentsIndexWorkflow } from "./DocumentsIndexWorkflow";
import "./documents-workflow.css";

interface Props {
  visible?: boolean;
  token: string | null;
  status: BootstrapStatusResponse | null;
  checking: boolean;
  connectionError: string | null;
  onConnect: (candidate: string) => Promise<boolean>;
  onDisconnect: () => void;
}

const LOCAL_GATEWAY = import.meta.env.VITE_RAG_LOCAL_GATEWAY === "true";
const pageSize = 50;
const filenamePattern = /^[A-Za-z0-9][A-Za-z0-9 _().-]{0,114}\.pdf$/;

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
}

function documentFailure(error: unknown): string {
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

function uploadOutcomeUncertain(error: unknown): boolean {
  return !(error instanceof ApiError) || error.kind === "network" || error.kind === "non_json" ||
    error.kind === "malformed_response" || error.status === null || error.status >= 500;
}

function DocumentRow({ document, manifestHash }: { document: BootstrapDocumentRecord; manifestHash: string }) {
  const query = new URLSearchParams({ manifest_hash: manifestHash, page: "1" });
  const focusId = `document-preview-${document.document_id}`;
  return <tr>
    <th scope="row"><div className="document-row-name"><span className="document-file-mark" aria-hidden="true">PDF</span><div><span className="document-filename">{document.relative_path}</span><details><summary>File details</summary><code className="document-hash" title={document.sha256}>{document.sha256}</code></details></div></div></th>
    <td>{formatBytes(document.size_bytes)}</td>
    <td><span className="document-source-state">In collection</span></td>
    <td><Link id={focusId} className="document-preview-link" to={`/documents/view/${document.document_id}?${query.toString()}`} state={{ returnTo: "/documents", returnFocusId: focusId, documentName: document.relative_path }}>Preview</Link></td>
  </tr>;
}

export function DocumentsPage({ token, status, checking, connectionError, onConnect, onDisconnect, visible = true }: Props) {
  const location = useLocation();
  const client = useMemo(() => new BootstrapApiClient({ token }), [token]);
  const [inventory, setInventory] = useState<BootstrapDocumentsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [inventoryError, setInventoryError] = useState<string | null>(null);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [offset, setOffset] = useState(0);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadNeedsInventoryRefresh, setUploadNeedsInventoryRefresh] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const uploadController = useRef<AbortController | null>(null);
  const uploadSerial = useRef(0);
  const submitting = useRef(false);
  const requiresUploadRefresh = useRef(false);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    if (!token) {
      setInventory(null); setInventoryError(null); setLoading(false);
      return () => { active = false; controller.abort(); };
    }
    setLoading(true); setInventoryError(null);
    void client.listDocuments({ limit: pageSize, offset, signal: controller.signal })
      .then((result) => {
        if (!active) return;
        setInventory(result);
        if (requiresUploadRefresh.current) {
          requiresUploadRefresh.current = false;
          setUploadNeedsInventoryRefresh(false);
          setWarning(null);
          setUploadError(null);
        }
      })
      .catch((error: unknown) => { if (active && !controller.signal.aborted) setInventoryError(apiErrorText(error)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [client, token, offset, refreshVersion]);

  useEffect(() => () => {
    uploadSerial.current += 1;
    uploadController.current?.abort();
  }, []);

  function clearFile() {
    setSelectedFile(null);
    if (fileInput.current) fileInput.current.value = "";
  }

  async function connect(candidate: string): Promise<boolean> {
    const connected = await onConnect(candidate);
    if (connected) {
      requiresUploadRefresh.current = false;
      setUploadNeedsInventoryRefresh(false);
      setInventory(null); setInventoryError(null); setUploadError(null); setFeedback(null); setWarning(null); setOffset(0);
    }
    return connected;
  }

  function disconnect() {
    uploadSerial.current += 1;
    uploadController.current?.abort();
    uploadController.current = null;
    submitting.current = false;
    requiresUploadRefresh.current = false;
    setUploadNeedsInventoryRefresh(false);
    setUploading(false); setUploadError(null); setFeedback(null); setWarning(null); setInventory(null); setInventoryError(null);
    clearFile();
    onDisconnect();
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || !selectedFile || !inventory || submitting.current || requiresUploadRefresh.current || uploadNeedsInventoryRefresh || inventoryError || loading) return;
    if (!filenamePattern.test(selectedFile.name)) {
      setUploadError("Use an ASCII filename with letters, numbers, spaces, parentheses, underscores or hyphens, ending in lowercase .pdf.");
      return;
    }
    if (selectedFile.size < 1) {
      setUploadError("The selected file is empty.");
      return;
    }
    if (selectedFile.size > inventory.max_upload_bytes) {
      setUploadError(`This file is ${formatBytes(selectedFile.size)}; the configured limit is ${formatBytes(inventory.max_upload_bytes)}.`);
      return;
    }

    submitting.current = true;
    const serial = ++uploadSerial.current;
    const controller = new AbortController();
    uploadController.current = controller;
    setUploading(true); setUploadError(null); setFeedback(null); setWarning(null);
    try {
      const result = await client.uploadDocument(selectedFile, { signal: controller.signal });
      if (serial !== uploadSerial.current) return;
      clearFile();
      let refreshed: BootstrapDocumentsResponse | null = null;
      try {
        refreshed = await client.listDocuments({ limit: pageSize, offset, signal: controller.signal });
        if (serial === uploadSerial.current) {
          setInventory(refreshed); setInventoryError(null);
        }
      } catch (error) {
        if (serial === uploadSerial.current && !controller.signal.aborted) setInventoryError(apiErrorText(error));
      }
      if (serial !== uploadSerial.current) return;
      const visible = refreshed?.documents.some((entry) => entry.document_id === result.document.document_id && entry.sha256 === result.document.sha256) ?? false;
      if (result.outcome === "imported") {
        setFeedback(visible
          ? `${result.document.relative_path} was imported and confirmed in the source inventory. It is available for indexing; no index was built.`
          : `${result.document.relative_path} was imported and confirmed by the manifest. The refreshed page contains ${refreshed?.total ?? inventory.total} source documents.`);
        if (!refreshed) setWarning("The import is confirmed, but the inventory refresh failed. Refresh before relying on the displayed list.");
      } else if (visible) {
        setFeedback(`${result.document.relative_path} is now visible in the source inventory.`);
        setWarning("The import response could not confirm the manifest at publication time; the later inventory now confirms the file.");
      } else {
        requiresUploadRefresh.current = true;
        setUploadNeedsInventoryRefresh(true);
        setWarning("The server reports that the PDF was published but could not confirm the updated inventory. Do not upload it again until the inventory is refreshed.");
      }
    } catch (error) {
      if (serial === uploadSerial.current && !controller.signal.aborted) {
        setUploadError(documentFailure(error));
        if (uploadOutcomeUncertain(error)) {
          requiresUploadRefresh.current = true;
          setUploadNeedsInventoryRefresh(true);
          setWarning("The upload response was uncertain. Refresh the source inventory before attempting another upload.");
        }
      }
    } finally {
      if (serial === uploadSerial.current) {
        submitting.current = false; setUploading(false); uploadController.current = null;
      }
    }
  }

  const previouslyVisible = useRef(visible);
  useEffect(() => {
    if (visible && !previouslyVisible.current) setRefreshVersion(value => value + 1);
    previouslyVisible.current = visible;
  }, [visible]);

  useEffect(() => {
    if (!visible || !location.state || typeof location.state !== "object") return;
    const focusId = (location.state as { restoreFocusId?: unknown }).restoreFocusId;
    if (typeof focusId === "string" && /^document-preview-doc_[0-9a-f]{64}$/.test(focusId)) {
      document.getElementById(focusId)?.focus();
    }
  }, [visible, location.key, location.state]);

  const hasNext = Boolean(inventory && offset + inventory.documents.length < inventory.total);
  const maxUploadBytes = inventory?.max_upload_bytes ?? null;

  const canChoose = Boolean(token && inventory && !inventoryError && !uploading && !loading && !uploadNeedsInventoryRefresh);
  return <div className="documents-page">
    <header className="documents-heading"><div><p className="documents-kicker">YOUR WORKSPACE</p><h1>Documents</h1><p>Add PDFs. Prepare your collection. Ask better questions.</p></div><span className="documents-count">{inventoryError ? "Library unavailable" : inventory ? `${inventory.total} ${inventory.total === 1 ? "PDF" : "PDFs"}` : "Library"}</span></header>
    <details open={!LOCAL_GATEWAY || !token || Boolean(connectionError)} className="documents-connection"><summary>Connection details</summary><BootstrapConnectionPanel connected={token !== null} checking={checking} busy={false} status={status} error={connectionError} onConnect={connect} onDisconnect={disconnect} /></details>
    <div className="documents-layout">
      <div className="documents-library">
        <section className={`documents-upload${dragging ? " is-dragging" : ""}`} aria-labelledby="documents-import-title"
          onDragOver={event => { event.preventDefault(); if (canChoose) setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={event => {
            event.preventDefault(); setDragging(false);
            if (!canChoose) return;
            const files = event.dataTransfer.files;
            if (files.length !== 1) { setUploadError("Add one PDF at a time so each upload can be confirmed."); return; }
            setSelectedFile(files.item(0)); setUploadError(null); setFeedback(null);
          }}>
          <div className="documents-upload__heading"><span className="documents-page-icon" aria-hidden="true">PDF</span><div><h2 id="documents-import-title">Add a PDF</h2><p>Choose a file or drop it here.</p></div></div>
          {maxUploadBytes !== null ? <p className="documents-limit">Up to {formatBytes(maxUploadBytes)} per PDF · {inventory?.max_pages} pages · unencrypted files</p> : <p className="documents-limit">Connect to load upload limits.</p>}
          <form onSubmit={event => void submit(event)} className="documents-upload-form">
            <label className="sr-only" htmlFor="document-pdf">PDF file</label>
            <input ref={fileInput} id="document-pdf" type="file" accept="application/pdf,.pdf" disabled={!canChoose} onChange={event => { setSelectedFile(event.currentTarget.files?.[0] ?? null); setUploadError(null); setFeedback(null); setWarning(null); }} />
            {selectedFile ? <p className="documents-selected-file">{selectedFile.name} <span>{formatBytes(selectedFile.size)}</span></p> : null}
            <button className="button button--primary" type="submit" disabled={!canChoose || !selectedFile}>{uploading ? "Adding PDF…" : "Add PDF"}</button>
            {uploading ? <p role="status">Adding and validating your PDF…</p> : null}
            {uploadError ? <p className="documents-error" role="alert">{uploadError}</p> : null}
            {feedback ? <p className="documents-feedback" role="status" aria-label="PDF import">{feedback}</p> : null}
            {warning ? <div className="documents-warning" role="alert"><p>{warning}</p>{uploadNeedsInventoryRefresh ? <button className="button button--quiet" type="button" onClick={() => setRefreshVersion(v => v + 1)} disabled={loading}>Refresh PDF list</button> : null}</div> : null}
          </form>
          <details className="documents-upload-help"><summary>File requirements</summary><p>Use a simple filename ending in lowercase .pdf. Uploads are validated by the server. Existing files are never renamed or replaced.</p></details>
        </section>
        <section className="documents-library-list" aria-labelledby="documents-inventory-title">
          <div className="documents-section-heading"><h2 id="documents-inventory-title">Your PDFs</h2><button className="documents-text-button" type="button" onClick={() => setRefreshVersion(v => v + 1)} disabled={!token || loading || uploading}>{loading ? "Refreshing…" : "Refresh"}</button></div>
          {!token ? <p className="documents-empty">Connect to view your PDFs.</p> : loading && !inventory ? <p role="status" className="documents-empty">Loading your library…</p> : inventoryError ? <div role="alert" className="documents-error"><p>{inventoryError}</p><button className="button button--quiet" type="button" onClick={() => setRefreshVersion(v => v + 1)}>Retry library</button></div> : inventory ? <>
            {inventory.documents.length === 0 ? <div className="documents-empty"><h3>Your library starts here.</h3><p>Add your first PDF above. Preparing it for chat is a separate, confirmed step.</p></div> : <>
              <div className="documents-table-wrap"><table className="documents-table"><thead><tr><th scope="col">Document</th><th scope="col">Size</th><th scope="col">Status</th><th scope="col"><span className="sr-only">Open document</span></th></tr></thead><tbody>{inventory.documents.map(document => <DocumentRow key={document.document_id} document={document} manifestHash={inventory.manifest_hash} />)}</tbody></table></div>
              {inventory.total > pageSize ? <nav className="documents-pagination" aria-label="Document inventory pages"><button className="button button--quiet" type="button" disabled={offset === 0 || loading} onClick={() => setOffset(Math.max(0, offset - pageSize))}>Previous</button><span>{offset + 1}–{offset + inventory.documents.length} of {inventory.total}</span><button className="button button--quiet" type="button" disabled={!hasNext || loading} onClick={() => setOffset(offset + pageSize)}>Next</button></nav> : null}
            </>}
            <p className="documents-library-note">These files form one collection. Adding a PDF does not start a build.</p>
          </> : null}
        </section>
      </div>
      <DocumentsIndexWorkflow visible={visible} token={token} status={status} sourceManifestHash={inventoryError ? null : inventory?.manifest_hash ?? null} sourceCount={inventoryError ? null : inventory?.total ?? null} sourceError={Boolean(inventoryError)} sourceChecking={loading || uploading} onRefreshDocuments={() => setRefreshVersion(v => v + 1)} />
    </div>
    <footer className="documents-footer"><Link to="/index-builds">Advanced build workspace</Link><span>Files and previous indexes are preserved.</span></footer>
  </div>;
}
