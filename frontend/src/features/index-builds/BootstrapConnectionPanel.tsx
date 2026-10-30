import { useState, type FormEvent } from "react";
import type { BootstrapStatusResponse } from "../../api/types";
import { StatusBadge } from "../../components/StatusBadge";

interface Props {
  connected: boolean;
  checking: boolean;
  busy: boolean;
  status: BootstrapStatusResponse | null;
  error: string | null;
  onConnect: (token: string) => Promise<boolean>;
  onDisconnect: () => void;
}

export function BootstrapConnectionPanel({ connected, checking, busy, status, error, onConnect, onDisconnect }: Props) {
  const [draftToken, setDraftToken] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (await onConnect(draftToken)) setDraftToken("");
  }

  return <section className="connection-panel bootstrap-connection" aria-labelledby="bootstrap-connection-title">
    <div className="connection-panel__heading">
      <div><span className="eyebrow">DEDICATED LOCAL SERVICE</span><h2 id="bootstrap-connection-title">Bootstrap API connection</h2></div>
      <StatusBadge status={connected ? "ready" : checking ? "loading" : "unloaded"} label={connected ? "Bootstrap connected" : checking ? "Verifying service" : "Disconnected"} />
    </div>
    {connected ? <div className="connection-panel__connected">
      <p className="muted">Authenticated bootstrap mode · {status?.accepting_jobs ? "accepting jobs" : "not accepting jobs"} · queue capacity {status?.queue_capacity}. This token is separate from the experiment runtime and stays in memory only.</p>
      <button className="button button--quiet" type="button" onClick={onDisconnect} disabled={busy}>Disconnect bootstrap</button>
      {busy ? <p role="status" className="muted">Wait for the single submission response before disconnecting. If it is lost, the request ID will be needed for history lookup.</p> : null}
    </div> : <form className="connection-form" onSubmit={submit}>
      <label htmlFor="bootstrap-api-token">Bootstrap bearer token</label>
      <div className="connection-form__row">
        <input id="bootstrap-api-token" type="password" autoComplete="off" spellCheck={false} value={draftToken}
          onChange={(event) => setDraftToken(event.target.value)} placeholder="Enter the bootstrap service token" />
        <button className="button button--primary" type="submit" disabled={checking || draftToken.length === 0}>
          {checking ? "Verifying…" : "Connect to bootstrap"}
        </button>
      </div>
      <p className="muted">Connection is verified with the protected bootstrap status endpoint. The experiment runtime is not contacted.</p>
      {error ? <p className="inline-error" role="alert">{error}</p> : null}
    </form>}
  </section>;
}
