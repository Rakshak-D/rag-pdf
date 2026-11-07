import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { ApiError, BootstrapApiClient } from "../../api/client";
import type { BootstrapDocumentRecord, BootstrapDocumentsResponse, BootstrapStatusResponse } from "../../api/types";
import { apiErrorText } from "../../components/apiErrorText";
import { BootstrapConnectionPanel } from "../index-builds/BootstrapConnectionPanel";
import { PageHeading } from "../shared";
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

function DocumentRow({ document }: { document: BootstrapDocumentRecord }) {
  return <tr>
    <th scope="row"><span className="document-file-mark" aria-hidden="true">PDF</span><span className="document-filename">{document.relative_path}</span></th>
    <td>{formatBytes(document.size_bytes)}</td>
    <td><details><summary>View details</summary><code className="document-hash" title={document.sha256}>{document.sha256}</code></details></td>
    <td><span className="document-source-state">Available for indexing</span></td>
  </tr>;
}

export function DocumentsPage({ token, status, checking, connectionError, onConnect, onDisconnect, visible = true }: Props) {
  const client = useMemo(() => new BootstrapApiClient({ token }), [token]);
  const [inventory, setInventory] = useState<BootstrapDocumentsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [inventoryError, setInventoryError] = useState<string | null>(null);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [offset, setOffset] = useState(0);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
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
    if (!token || !selectedFile || !inventory || submitting.current) return;
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

  const hasNext = Boolean(inventory && offset + inventory.documents.length < inventory.total);
  const maxUploadBytes = inventory?.max_upload_bytes ?? null;

  return <div className="documents-page">
    <PageHeading
      eyebrow="YOUR DOCUMENTS"
      title="Documents"
      description="Add PDFs, prepare them for chat, then ask questions."
      actions={<Link className="button button--quiet" to="/index-builds">Advanced job details</Link>}
    />

    <details open={!LOCAL_GATEWAY || !token || Boolean(connectionError)} className="documents-connection"><summary>Connection details</summary><BootstrapConnectionPanel
      connected={token !== null} checking={checking} busy={false} status={status} error={connectionError}
      onConnect={connect} onDisconnect={disconnect}
    /></details>

    <section className="panel documents-import" aria-labelledby="documents-import-title">
      <div className="panel__heading"><div><h2 id="documents-import-title">Import a PDF</h2></div></div>
      <p className="documents-copy">Import PDFs, then prepare an index below.</p>
      {maxUploadBytes !== null ? <p className="documents-limit">Maximum file size {formatBytes(maxUploadBytes)} ({maxUploadBytes.toLocaleString()} bytes) · maximum {inventory?.max_pages} pages · readable, unencrypted PDFs</p>
        : <p className="documents-limit">Connect to the bootstrap service to read its configured upload limit.</p>}
      <form className="documents-upload-form" onSubmit={(event) => { void submit(event); }}>
        <label htmlFor="document-pdf">PDF file</label>
        <input
          ref={fileInput}
          id="document-pdf"
          type="file"
          accept="application/pdf,.pdf"
          disabled={!token || uploading || loading || !inventory || uploadNeedsInventoryRefresh}
          onChange={(event) => {
            setSelectedFile(event.currentTarget.files?.[0] ?? null);
            setUploadError(null); setFeedback(null); setWarning(null);
          }}
        />
        <p className="documents-filename-help">Use a simple ASCII filename with lowercase <code>.pdf</code>; path characters and Windows device names are rejected.</p>
        {selectedFile ? <p className="documents-selected-file">Selected: <strong>{selectedFile.name}</strong> · {formatBytes(selectedFile.size)}</p> : null}
        <div className="documents-upload-actions">
          <button className="button button--primary" type="submit" disabled={!token || !inventory || !selectedFile || uploading || loading || uploadNeedsInventoryRefresh}>
            {uploading ? "Importing PDF…" : "Import PDF"}
          </button>
          {uploading ? <span role="status">Validating and publishing the selected PDF.</span> : null}
        </div>
        {uploadError ? <p className="inline-error" role="alert">{uploadError}</p> : null}
        {feedback ? <p className="documents-feedback" role="status" aria-label="PDF import">{feedback}</p> : null}
        {warning ? <p className="documents-warning" role="alert">{warning}</p> : null}
      </form>
    </section>

    <section className="panel documents-inventory" aria-labelledby="documents-inventory-title">
      <div className="panel__heading">
        <div><h2 id="documents-inventory-title">Imported documents</h2></div>
        <button className="button button--quiet" type="button" onClick={() => setRefreshVersion((value) => value + 1)} disabled={!token || loading || uploading}>Refresh</button>
      </div>
      {!token ? <p className="documents-empty">Connect to the bootstrap service to inspect its PDF source inventory.</p>
        : loading && !inventory ? <p className="documents-empty" role="status">Loading source inventory…</p>
          : inventoryError ? <div className="documents-load-error" role="alert"><p>{inventoryError}</p><button className="button button--quiet" type="button" onClick={() => setRefreshVersion((value) => value + 1)}>Retry inventory</button></div>
            : inventory ? <>
              <div className="documents-inventory-meta"><span>{inventory.total} {inventory.total === 1 ? "document" : "documents"} in the configured source</span><details><summary>Source identity</summary><code>{inventory.manifest_hash}</code></details></div>
              {inventory.documents.length === 0 ? <p className="documents-empty">No PDF documents are present in the trusted source yet.</p> : <>
                <div className="documents-table-wrap"><table className="documents-table">
                  <thead><tr><th scope="col">Document</th><th scope="col">Size</th><th scope="col">Details</th><th scope="col">Source state</th></tr></thead>
                  <tbody>{inventory.documents.map((document) => <DocumentRow key={document.document_id} document={document} />)}</tbody>
                </table></div>
                <nav className="documents-pagination" aria-label="Document inventory pages">
                  <button className="button button--quiet" type="button" disabled={offset === 0 || loading} onClick={() => setOffset(Math.max(0, offset - pageSize))}>Previous</button>
                  <span>Showing {offset + 1}–{offset + inventory.documents.length} of {inventory.total}</span>
                  <button className="button button--quiet" type="button" disabled={!hasNext || loading} onClick={() => setOffset(offset + pageSize)}>Next</button>
                </nav>
              </>}
            </> : null}
      <p className="documents-indexing-note">Importing adds a PDF to your collection. Build or select an index before asking questions.</p>
    </section>

    <DocumentsIndexWorkflow
      visible={visible}
      token={token}
      status={status}
      sourceManifestHash={inventory?.manifest_hash ?? null}
      sourceCount={inventory?.total ?? null}
      onRefreshDocuments={() => setRefreshVersion((value) => value + 1)}
    />
  </div>;
}
