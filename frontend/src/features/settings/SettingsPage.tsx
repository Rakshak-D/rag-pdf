import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { ApiError, WorkbenchApiClient } from "../../api/client";
import type { ChatModelCatalog } from "../../api/types";
import {
  DEFAULT_GENERAL_CHAT_PREFERENCES,
  type GeneralChatPreferences,
  type PreferenceLoadStatus,
} from "./preferences";

interface Props {
  token: string | null;
  preferences: GeneralChatPreferences;
  preferenceLoadStatus: PreferenceLoadStatus;
  onSave: (preferences: GeneralChatPreferences) => boolean;
  onResetUnavailableModel: () => void;
}

interface Draft {
  selectedModel: string | null;
  temperature: number;
  maxOutputTokens: string;
}

function asDraft(preferences: GeneralChatPreferences): Draft {
  return {
    selectedModel: preferences.selectedModel,
    temperature: preferences.temperature,
    maxOutputTokens: String(preferences.maxOutputTokens),
  };
}

function modelListError(reason: unknown): string {
  if (reason instanceof ApiError && reason.code === "authentication_required") {
    return "Connect to the local runtime to inspect installed models.";
  }
  return "The installed model list could not be read.";
}

export function SettingsPage({ token, preferences, preferenceLoadStatus, onSave, onResetUnavailableModel }: Props) {
  const client = useMemo(() => new WorkbenchApiClient({ token }), [token]);
  const [catalog, setCatalog] = useState<ChatModelCatalog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState<Draft>(() => asDraft(preferences));
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [modelFallbackNotice, setModelFallbackNotice] = useState<string | null>(null);

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
      setError(modelListError(reason));
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [client]);

  useEffect(() => setDraft(asDraft(preferences)), [preferences]);

  useEffect(() => {
    if (!catalog || catalog.availability !== "available" || !preferences.selectedModel ||
      catalog.available_models.includes(preferences.selectedModel)) return;
    const unavailable = preferences.selectedModel;
    const fallback = catalog.configured_model_available
      ? ` Using configured default ${catalog.configured_model}.`
      : ` Configured default ${catalog.configured_model} is also not installed.`;
    setModelFallbackNotice(`Saved model ${unavailable} is no longer installed.${fallback}`);
    onResetUnavailableModel();
  }, [catalog, onResetUnavailableModel, preferences.selectedModel]);

  const selectedValue = draft.selectedModel ?? (catalog?.configured_model_available ? catalog.configured_model : "");
  const parsedOutputTokens = Number(draft.maxOutputTokens);
  const outputTokensValid = Number.isInteger(parsedOutputTokens) && parsedOutputTokens >= 32 && parsedOutputTokens <= 2048;
  const canSelectModel = !loading && catalog?.availability === "available";

  function save(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    if (!outputTokensValid) return;
    const saved = onSave({
      selectedModel: draft.selectedModel,
      temperature: draft.temperature,
      maxOutputTokens: parsedOutputTokens,
    });
    setModelFallbackNotice(null);
    setSaveMessage(saved ? "Preferences saved in this browser." : "Preferences apply for this session, but browser storage is unavailable.");
  }

  function resetToDefaults() {
    const defaults = { ...DEFAULT_GENERAL_CHAT_PREFERENCES };
    setDraft(asDraft(defaults));
    setModelFallbackNotice(null);
    setSaveMessage(onSave(defaults)
      ? "General Chat defaults restored and saved in this browser."
      : "General Chat defaults restored for this session; browser storage is unavailable.");
  }

  return <section className="settings-page">
    <header className="simple-page-heading">
      <span className="chat-kicker">LOCAL PREFERENCES</span>
      <h1>Settings</h1>
      <p>Choose how General Chat responds. These preferences do not change Document Chat or research runs.</p>
    </header>

    <form className="settings-form" onSubmit={save} noValidate>
      <section className="settings-section" aria-labelledby="settings-model-title">
        <div className="settings-section__heading">
          <span className="settings-index" aria-hidden="true">01</span>
          <div><h2 id="settings-model-title">General Chat model</h2><p>Choose a model already installed in the configured local Ollama service.</p></div>
        </div>
        {loading ? <p className="settings-status" role="status">Checking installed models…</p> : error ? <p className="settings-error" role="alert">{error}</p> : catalog?.availability === "unavailable" ? <div className="settings-service-state" role="status">
          <span className="chat-dot chat-dot--muted" />
          <p>Ollama is unavailable. The configured default is <code>{catalog.configured_model}</code>; installed models cannot be confirmed right now.</p>
        </div> : catalog ? <>
          <div className="settings-model-summary">
            <span>Configured default</span>
            <code>{catalog.configured_model}</code>
            <span className={catalog.configured_model_available ? "settings-installed" : "settings-uninstalled"}>
              {catalog.configured_model_available ? "Installed" : "Not installed"}
            </span>
          </div>
          <label htmlFor="chat-model">Use this installed model</label>
          <select
            id="chat-model"
            value={selectedValue}
            disabled={!canSelectModel || catalog.available_models.length === 0}
            onChange={(event) => {
              const value = event.currentTarget.value;
              setDraft((current) => ({ ...current, selectedModel: value === catalog.configured_model ? null : value }));
              setSaveMessage(null);
              setModelFallbackNotice(null);
            }}
            aria-describedby="chat-model-help"
          >
            {!selectedValue ? <option value="" disabled>Select an installed model</option> : null}
            {draft.selectedModel && !catalog.available_models.includes(draft.selectedModel)
              ? <option value={draft.selectedModel} disabled>{draft.selectedModel} (not installed)</option>
              : null}
            {catalog.configured_model_available
              ? <option value={catalog.configured_model}>{catalog.configured_model} (configured default)</option>
              : <option value={catalog.configured_model} disabled>{catalog.configured_model} (configured default · not installed)</option>}
            {catalog.available_models.filter((model) => model !== catalog.configured_model).map((model) =>
              <option value={model} key={model}>{model}</option>)}
          </select>
          <p id="chat-model-help" className="settings-note">
            {catalog.available_models.length
              ? `${catalog.available_models.length} installed model${catalog.available_models.length === 1 ? "" : "s"} reported by Ollama.`
              : "No installed models are currently available. Install or configure a model in Ollama, then return here."}
          </p>
        </> : null}
        {modelFallbackNotice ? <p className="settings-warning" role="status">{modelFallbackNotice}</p> : null}
      </section>

      <section className="settings-section" aria-labelledby="settings-response-title">
        <div className="settings-section__heading">
          <span className="settings-index" aria-hidden="true">02</span>
          <div><h2 id="settings-response-title">Response style</h2><p>These values are sent with each new General Chat request.</p></div>
        </div>

        <div className="settings-field">
          <div className="settings-field__top"><label htmlFor="chat-temperature">Temperature</label><output htmlFor="chat-temperature">{draft.temperature.toFixed(1)}</output></div>
          <input
            id="chat-temperature"
            type="range"
            min="0"
            max="2"
            step="0.1"
            value={draft.temperature}
            aria-describedby="chat-temperature-help"
            onChange={(event) => {
              const temperature = Number(event.currentTarget.value);
              setDraft((current) => ({ ...current, temperature }));
              setSaveMessage(null);
            }}
          />
          <p id="chat-temperature-help" className="settings-note">Lower values keep answers more consistent; higher values allow more variation. Supported range: 0.0–2.0.</p>
        </div>

        <div className="settings-field">
          <div className="settings-field__top"><label htmlFor="chat-max-output">Maximum answer length</label><span className="settings-unit">tokens</span></div>
          <input
            id="chat-max-output"
            type="number"
            min="32"
            max="2048"
            step="1"
            inputMode="numeric"
            value={draft.maxOutputTokens}
            aria-invalid={!outputTokensValid}
            aria-describedby="chat-max-output-help chat-max-output-error"
            onChange={(event) => {
              const maxOutputTokens = event.currentTarget.value;
              setDraft((current) => ({ ...current, maxOutputTokens }));
              setSaveMessage(null);
            }}
          />
          <p id="chat-max-output-help" className="settings-note">Maximum generated tokens per reply. Supported range: 32–2048.</p>
          {!outputTokensValid ? <p id="chat-max-output-error" className="settings-error" role="alert">Enter a whole number from 32 to 2048.</p> : null}
        </div>
      </section>

      {preferenceLoadStatus === "invalid" ? <p className="settings-warning" role="status">Saved General Chat preferences were invalid. Safe defaults are active; save to replace them.</p> : null}
      {preferenceLoadStatus === "unavailable" ? <p className="settings-warning" role="status">Browser storage is unavailable. Preferences will last for this session only.</p> : null}
      {saveMessage ? <p className="settings-save-message" role="status" aria-live="polite">{saveMessage}</p> : null}
      <div className="settings-actions">
        <button className="settings-save" type="submit" disabled={!outputTokensValid}>Save preferences</button>
        <button className="settings-reset" type="button" onClick={resetToDefaults}>Reset to defaults</button>
      </div>
      <p className="settings-persistence">Preferences are saved in this browser only. They are not synced between devices and may be removed when browser data is cleared. No credentials or chat history are stored here.</p>
    </form>

    <section className="settings-section settings-section--boundary" aria-labelledby="settings-boundary-title">
      <div className="settings-section__heading">
        <span className="settings-index" aria-hidden="true">03</span>
        <div><h2 id="settings-boundary-title">Advanced Research</h2><p>Saved evaluations, ablations, jobs and detailed index-build tools.</p></div>
      </div>
      <div className="settings-links"><Link to="/research">Open Advanced Research</Link></div>
    </section>
  </section>;
}
