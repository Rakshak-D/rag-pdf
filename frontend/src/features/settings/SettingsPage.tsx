import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, WorkbenchApiClient } from "../../api/client";
import type { ChatModelCatalog } from "../../api/types";

interface Props {
  token: string | null;
  selectedModel: string | null;
  onSelectModel: (model: string | null) => void;
}

export function SettingsPage({ token, selectedModel, onSelectModel }: Props) {
  const client = useMemo(() => new WorkbenchApiClient({ token }), [token]);
  const [catalog, setCatalog] = useState<ChatModelCatalog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    setLoading(true);
    void client.chatModels(controller.signal).then((value) => {
      if (!active) return;
      setCatalog(value);
      setError(null);
    }).catch((reason: unknown) => {
      if (!active) return;
      setError(reason instanceof ApiError && reason.code === "authentication_required"
        ? "Connect to the local runtime to inspect installed models."
        : "The configured local Ollama service is unavailable.");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [client]);

  const chosen = selectedModel ?? catalog?.configured_model ?? "";
  return <section className="settings-page">
    <header className="simple-page-heading"><span className="chat-kicker">LOCAL PREFERENCES</span><h1>Settings</h1><p>Choose the local model used for General Chat. Research and document configurations remain unchanged.</p></header>
    <section className="settings-section" aria-labelledby="settings-model-title">
      <div><h2 id="settings-model-title">General Chat model</h2><p>Only models reported by the configured local Ollama service can be selected.</p></div>
      {loading ? <p role="status">Checking local model availability…</p> : error ? <p className="settings-error" role="status">{error}</p> : catalog?.availability === "unavailable" ? <p className="settings-error" role="status">Ollama is not responding. Configured model: <code>{catalog.configured_model}</code></p> : <>
        <label htmlFor="chat-model">Installed model</label>
        <select id="chat-model" value={chosen} onChange={(event) => onSelectModel(event.target.value === catalog?.configured_model ? null : event.target.value)}>
          <option value={catalog?.configured_model ?? ""}>{catalog?.configured_model} (configured default)</option>
          {catalog?.available_models.filter((model) => model !== catalog.configured_model).map((model) => <option value={model} key={model}>{model}</option>)}
        </select>
        {catalog?.available_models.length ? <p className="settings-note">Your choice is held in this tab and resets when you reload. It affects the next General Chat request only.</p> : <p className="settings-error" role="status">No installed model is currently available. General Chat will explain this if you send a message.</p>}
      </>}
    </section>
    <section className="settings-section settings-section--boundary" aria-labelledby="settings-local-title">
      <h2 id="settings-local-title">Local access</h2>
      <p>The local launcher keeps backend credentials in its server processes. This is a loopback trust boundary, not protection from other programs running as your Windows user.</p>
      <div className="settings-links"><Link to="/documents">Manage PDFs</Link><Link to="/index-builds">Review index builds</Link><Link to="/artifacts">Open results</Link></div>
    </section>
  </section>;
}
