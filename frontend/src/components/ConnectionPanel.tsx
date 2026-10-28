import { useState, type FormEvent } from "react";
import type { RuntimeModelsResponse } from "../api/types";
import { StatusBadge } from "./StatusBadge";

interface ConnectionPanelProps {
  connected: boolean;
  checking: boolean;
  runtimeModels: RuntimeModelsResponse | null;
  error: string | null;
  onConnect: (token: string) => Promise<boolean>;
  onDisconnect: () => void;
}

export function ConnectionPanel({ connected, checking, runtimeModels, error, onConnect, onDisconnect }: ConnectionPanelProps) {
  const [token, setToken] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const accepted = await onConnect(token);
    if (accepted) setToken("");
  }

  return (
    <section className="connection-panel" aria-labelledby="connection-title">
      <div className="connection-panel__heading">
        <div>
          <span className="eyebrow">LOCAL API</span>
          <h2 id="connection-title">Backend connection</h2>
        </div>
        <StatusBadge status={connected ? "ready" : checking ? "loading" : "unloaded"} label={connected ? "Connected" : checking ? "Checking" : "Disconnected"} />
      </div>
      {connected ? (
        <div className="connection-panel__connected">
          <p className="muted">Bearer token is held in memory for this page session only.</p>
          <button className="button button--quiet" onClick={onDisconnect}>Disconnect</button>
          {runtimeModels?.components.length ? (
            <ul className="model-list" aria-label="Runtime model status">
              {runtimeModels.components.map((model) => (
                <li key={`${model.component_kind}:${model.model_identifier}`}>
                  <span className="model-list__kind">{model.component_kind}</span>
                  <span className="model-list__name">{model.model_identifier}</span>
                  <StatusBadge status={model.state} />
                  {model.error_code ? <span className="model-list__error">{model.error_code}</span> : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="runtime-note">No adapters are registered yet. This status does not indicate index or Ollama readiness.</p>
          )}
        </div>
      ) : (
        <form className="connection-form" onSubmit={submit}>
          <label htmlFor="api-token">Bearer token</label>
          <div className="connection-form__row">
            <input
              id="api-token"
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={token}
              onChange={(event) => setToken(event.target.value)}
              placeholder="Enter your local API token"
              aria-describedby={error ? "connection-error" : "token-help"}
            />
            <button className="button button--primary" type="submit" disabled={checking || token.length === 0}>
              {checking ? "Verifying…" : "Connect"}
            </button>
          </div>
          <p id="token-help" className="muted">The token is not saved and must be re-entered after reload.</p>
          {error ? <p id="connection-error" className="inline-error" role="alert">{error}</p> : null}
        </form>
      )}
    </section>
  );
}
