import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, WorkbenchApiClient } from "../../api/client";
import type { BootstrapInventoryPage, BootstrapPreviewResponse, IndexSelectionState } from "../../api/types";
import { apiErrorText } from "../../components/apiErrorText";

interface Props {
  token: string | null;
  preview: BootstrapPreviewResponse | null;
  resolvedConfig: Record<string, unknown> | null;
  inventoryPage: BootstrapInventoryPage | null;
  onSelectionReadback?: (selection: IndexSelectionState) => void;
  compact?: boolean;
}

function operationId(): string {
  if (typeof crypto === "undefined" || typeof crypto.getRandomValues !== "function") {
    throw new Error("secure_random_unavailable");
  }
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function IndexSelectionPanel({ token, preview, resolvedConfig, inventoryPage, onSelectionReadback, compact = false }: Props) {
  const client = useMemo(() => new WorkbenchApiClient({ token }), [token]);
  const [selection, setSelection] = useState<IndexSelectionState | null>(null);
  const [loading, setLoading] = useState(false);
  const [selecting, setSelecting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const requestSerial = useRef(0);
  const submitLock = useRef(false);

  const refresh = useCallback(async () => {
    if (!token) return;
    const serial = ++requestSerial.current;
    setLoading(true);
    setError(null);
    try {
      const value = await client.getIndexSelection();
      if (requestSerial.current === serial) {
        setSelection(value);
        onSelectionReadback?.(value);
      }
    } catch (cause) {
      if (requestSerial.current === serial) setError(apiErrorText(cause));
    } finally {
      if (requestSerial.current === serial) setLoading(false);
    }
  }, [client, onSelectionReadback, token]);

  useEffect(() => {
    requestSerial.current += 1;
    setSelection(null);
    setSelecting(null);
    setError(null);
    setMessage(null);
    submitLock.current = false;
    if (!token) {
      setLoading(false);
      return () => { requestSerial.current += 1; };
    }
    void refresh();
    return () => { requestSerial.current += 1; };
  }, [token, refresh, preview?.manifest_hash]);

  const candidates = inventoryPage?.complete && preview && resolvedConfig &&
    inventoryPage.document_manifest_hash === preview.manifest_hash &&
    inventoryPage.expected_index_fingerprint === preview.index_fingerprint
    ? inventoryPage.candidates.filter((candidate) => candidate.compatibility === "report_match" &&
      candidate.build_id !== null && candidate.report_reference === `build-artifacts/${candidate.build_id}/build-report.json` &&
      candidate.document_manifest_hash === preview.manifest_hash &&
      candidate.expected_index_fingerprint === preview.index_fingerprint)
    : [];

  const select = useCallback(async (buildId: string) => {
    if (submitLock.current || !token || !preview || !resolvedConfig ||
      !inventoryPage?.complete || !selection || selection.revision === null || selection.selection_status === "invalid") return;
    const candidate = candidates.find((item) => item.build_id === buildId);
    if (!candidate || candidate.build_id === null || candidate.compatibility !== "report_match") return;
    submitLock.current = true;
    setSelecting(buildId);
    setError(null);
    setMessage(null);
    const serial = ++requestSerial.current;
    const matches = (value: IndexSelectionState) => value.selection_status === "selected" &&
      value.selection?.build_id === buildId && value.selection.document_manifest_hash === preview.manifest_hash &&
      value.selection.index_fingerprint === preview.index_fingerprint;
    try {
      const operation = operationId();
      await client.selectIndex({
        resolved_config: resolvedConfig,
        expected_manifest_hash: preview.manifest_hash,
        expected_index_fingerprint: preview.index_fingerprint,
        build_id: buildId,
        expected_revision: selection.revision,
        operation_id: operation,
      });
      if (requestSerial.current === serial) {
        const readback = await client.getIndexSelection();
        if (requestSerial.current !== serial) return;
        setSelection(readback);
        onSelectionReadback?.(readback);
        if (matches(readback)) {
          setMessage("Index selected. You can now ask your PDFs.");
        } else {
          setError("The selection response succeeded, but readback did not confirm this report. Refresh selection before proceeding.");
        }
      }
    } catch (cause) {
      if (requestSerial.current !== serial) return;
      try {
        const current = await client.getIndexSelection();
        if (requestSerial.current !== serial) return;
        setSelection(current);
        onSelectionReadback?.(current);
        if (matches(current)) {
          setMessage("The selection request response was interrupted; the runtime confirms the selected report.");
          return;
        }
      } catch {
        if (requestSerial.current === serial) setError("The selection response was uncertain and the runtime could not confirm it. Refresh selection before making another change.");
        return;
      }
      if (requestSerial.current === serial) {
        setError(cause instanceof ApiError && cause.kind === "network"
          ? "The selection response was uncertain. The runtime readback did not confirm this report; refresh the current selection before deciding what to do next."
          : apiErrorText(cause));
      }
    } finally {
      submitLock.current = false;
      if (requestSerial.current === serial) setSelecting(null);
    }
  }, [candidates, client, inventoryPage, onSelectionReadback, preview, resolvedConfig, selection, token]);

  const selectedForCurrentPreview = Boolean(preview && selection?.selection_status === "selected" &&
    selection.selection?.document_manifest_hash === preview.manifest_hash &&
    selection.selection.index_fingerprint === preview.index_fingerprint);

  const statusText = !token ? "Connect to the ordinary runtime to inspect or change its active report." :
    loading ? "Reading the runtime selection…" : selection?.selection_status === "selected" ? "Index selected." :
      selection?.selection_status === "stale" ? "Documents changed." :
        selection?.selection_status === "invalid" ? "The saved selection record is invalid." :
          selection?.selection_status === "legacy_configured" ? "A legacy report path is configured; it is not a durable UI selection." :
            "No index is selected.";

  return <section className="panel index-selection" aria-labelledby="index-selection-title">
    <div className="bootstrap-detail-heading">
      <div><span className="eyebrow">DOCUMENT CHAT</span><h2 id="index-selection-title">Selected index</h2></div>
      <button className="button button--quiet" type="button" onClick={() => void refresh()} disabled={!token || loading || selecting !== null}>
        {loading ? "Refreshing…" : "Refresh selection"}
      </button>
    </div>
    <p className="muted">{statusText}</p>
    {!token ? <p className="index-selection__hint">The bootstrap connection above is separate. Use the ordinary runtime token to save a selection for evaluations and retrieval.</p> : null}
    {error ? <p className="result-error" role="alert">{error}</p> : null}
    {message ? <p className="index-selection__confirmed" role="status">{message}</p> : null}
    {selectedForCurrentPreview && !compact ? <p className="index-selection__confirmed"><Link to="/?mode=document">Ask documents</Link></p> : null}
    {(selection?.selection_status === "selected" || selection?.selection_status === "stale") && selection.selection ? compact ? <details className="index-selection__technical"><summary>View report identity details</summary><dl className="index-selection__identity">
      <div><dt>Selected build</dt><dd><code>{selection.selection.build_id}</code></dd></div>
      <div><dt>Report status</dt><dd>{selection.selection.report_status}</dd></div>
      <div className="index-selection__wide"><dt>Source manifest</dt><dd><code>{selection.selection.document_manifest_hash}</code></dd></div>
      <div className="index-selection__wide"><dt>Index fingerprint</dt><dd><code>{selection.selection.index_fingerprint}</code></dd></div>
      <div className="index-selection__wide"><dt>Validated report SHA-256</dt><dd><code>{selection.selection.report_sha256}</code></dd></div>
      <div className="index-selection__wide"><dt>Configuration snapshot</dt><dd><code>{selection.selection.configuration_snapshot_hash}</code></dd></div>
      <div className="index-selection__wide"><dt>Run fingerprint</dt><dd><code>{selection.selection.run_fingerprint}</code></dd></div>
      <div><dt>Selection revision</dt><dd>{selection.selection.selection_revision}</dd></div>
    </dl></details> : <dl className="index-selection__identity">
      <div><dt>Selected build</dt><dd><code>{selection.selection.build_id}</code></dd></div>
      <div><dt>Report status</dt><dd>{selection.selection.report_status}</dd></div>
      <div className="index-selection__wide"><dt>Source manifest</dt><dd><code>{selection.selection.document_manifest_hash}</code></dd></div>
      <div className="index-selection__wide"><dt>Index fingerprint</dt><dd><code>{selection.selection.index_fingerprint}</code></dd></div>
      <div className="index-selection__wide"><dt>Validated report SHA-256</dt><dd><code>{selection.selection.report_sha256}</code></dd></div>
      <div className="index-selection__wide"><dt>Configuration snapshot</dt><dd><code>{selection.selection.configuration_snapshot_hash}</code></dd></div>
      <div className="index-selection__wide"><dt>Run fingerprint</dt><dd><code>{selection.selection.run_fingerprint}</code></dd></div>
      <div><dt>Selection revision</dt><dd>{selection.selection.selection_revision}</dd></div>
    </dl> : null}
    {selection?.selection_status === "stale" ? <p className="result-error" role="alert">Your PDFs or build settings changed. Preview the current documents and select a matching index.</p> : null}
    {selection?.selection_status === "invalid" ? <p className="result-error" role="alert">The saved selection is invalid ({selection.reason_code}). It was not replaced automatically. Runtime state needs operator review.</p> : null}
    {selection?.selection_status === "legacy_configured" ? <p className="index-selection__hint">Legacy report: <code>{selection.legacy_report_reference}</code>. The runtime validates this setting for execution. Selecting a report here records a durable explicit choice.</p> : null}
    {token && preview && inventoryPage ? <div className="index-selection__choices">
      <h3>Choose from the current preview</h3>
      {!inventoryPage.complete ? <p className="muted">The report inventory is incomplete. No candidate can be selected from it.</p> :
        candidates.length ? candidates.map((candidate) => {
          const isSelected = selection?.selection_status === "selected" && selection.selection?.build_id === candidate.build_id &&
            selection.selection.document_manifest_hash === preview.manifest_hash && selection.selection.index_fingerprint === preview.index_fingerprint;
          const blocked = selection?.revision === null || selection?.selection_status === "invalid";
          return <div className="index-selection__choice" key={candidate.candidate_id}>
            <div><strong>{compact ? "Compatible completed index" : <code>{candidate.build_id}</code>}</strong><small>{candidate.report_status ?? "Successful report"} · report compatibility only</small></div>
            <button className={isSelected ? "button button--quiet" : "button button--primary"} type="button"
              onClick={() => void select(candidate.build_id!)} disabled={blocked || isSelected || selecting !== null || loading}>
              {selecting === candidate.build_id ? "Confirming…" : isSelected ? "Selected" : compact ? "Use this index" : "Select report"}
            </button>
          </div>;
        }) : <p className="muted">No matching completed index on this page.</p>}
      <details className="index-selection__note"><summary>About index checks</summary><p>Selection verifies the report identity. Retrieval and model availability are checked when you ask a question.</p></details>
    </div> : token && (!preview || !inventoryPage) ? <p className="index-selection__hint">Preview the current configuration and inspect the report inventory to choose a compatible build.</p> : null}
    {!compact ? <div className="index-selection__links"><Link to="/documents">Manage source documents</Link><span>Question answering is a separate next workflow.</span></div> : null}
  </section>;
}
