# Local chat-first application

## Start the app

From a prepared RAG-PDF checkout and Python environment, configure the existing
document source and index storage, then run:

```powershell
python scripts/start_local_app.py
```

The launcher requires Node.js and the frontend's locked npm dependencies. It
starts the ordinary API, bootstrap API, credential-holding gateway, and Vite
only on `127.0.0.1`; the UI is at `http://127.0.0.1:5173`. It preserves the
configured source, index, SQLite, and artifact locations and does not load
embedding or reranking models. Stop the services with Ctrl+C. The launcher
stops only child processes it created. On Windows, Ctrl+C is sent as a console
break to those owned services and shutdown is bounded; the launcher never
stops Ollama or unrelated processes.

Set `RAG_WORKBENCH_DATA_DIR` and `RAG_WORKBENCH_INDEX_STORAGE_ROOT` to the
existing trusted source and index roots. They must be absolute, non-overlapping
paths and must not overlap `RAG_WORKBENCH_LOCAL_STATE_DIR`. The source directory
must already exist. By default, application state is kept under
`%LOCALAPPDATA%\RAG-PDF` on Windows or `$XDG_STATE_HOME/rag-pdf` (falling back
to `~/.local/state/rag-pdf`) on Linux/macOS. Existing supported environment
settings for artifact and SQLite locations remain in effect.

## Local access boundary

The launcher generates distinct random ordinary and bootstrap bearer tokens for
each run. The ordinary API receives only its token, the bootstrap API receives
only its token, and the loopback gateway receives both to inject the appropriate
credential for a fixed allowlist of same-origin API routes. The browser and Vite
receive neither token. Existing authenticated API clients retain bearer-token
mode for tests and independent development. The backend APIs remain
authenticated.

The gateway accepts one configured loopback `Host` and same-origin browser
requests, requires an exact `Origin` on state-changing methods, rejects unsafe
paths and unlisted routes, and uses fixed loopback upstreams. It bounds request
and response bodies and permits only JSON and the supported image content
types. Do not expose the gateway or Vite on a network interface. Loopback and
separate service credentials protect the local application from ordinary
cross-origin browser access; they are not a security boundary against other
processes running as the same operating-system user, who may be able to inspect
or control that user's processes.

## Chat modes and model behavior

General Chat calls the configured local Ollama service through the ordinary
authenticated API. It does not inspect documents, open Chroma, load embedding
models, or attach citations. The default model comes from the existing configured
RunConfig (`llm_model`) and may be overridden for a General Chat request only by
a model reported as installed by the local Ollama API. The Settings page lists
that verified catalog; a selected model applies to the current browser session
and the next request. The configured model is not rewritten, and research
RunPlans are unaffected. Temperature and output length remain server-controlled;
there is no unsupported universal “thinking” switch.

General Chat permits one active generation per ordinary runtime. Ollama owns
its model memory in a separate process. RAG-PDF does not claim that closing a
browser request cancels inference inside Ollama. Start and configure Ollama
separately; no model is downloaded automatically. A missing/unreachable model
is reported as an unavailable service/model, not replaced with another model.

The authenticated ordinary API exposes `GET /api/v1/chat/models`, returning an
availability state, the configured model, whether it is installed, and a
bounded list of installed model names. `POST /api/v1/chat` accepts a strict JSON
object with `question` (1–4,000 characters), optional `history` (up to eight
complete user/assistant turns and 32,000 combined characters), and an optional
installed `model` name. The response contains a general-mode answer and the
model used; it contains no document citations. Requests and responses are
bounded by the ordinary runtime's configured body limit, and responses use
`Cache-Control: no-store`. Stable failures include `ollama_unavailable`,
`chat_model_unavailable`, `ollama_capacity_unavailable`,
`chat_capacity_exhausted`, and `chat_generation_timeout`. The last code reflects
the local HTTP client's timeout result; it does not prove Ollama stopped an
inference already accepted by its server. RAG-PDF does not currently offer a
separate hard server-side generation deadline or inference-cancellation
guarantee.

Document Chat reuses the existing Ask API and requires an explicitly selected
index. It reports actual returned passages, citations, source pages, and
authenticated image evidence. Index selection itself does not prove Chroma or
model readiness. General Chat remains available without documents or an index.

Conversation history is held only in React memory for the active page and
validated/sent with follow-up General Chat requests (up to eight prior turns).
New Chat, navigation away, reload, or closing the tab clears it. No chat history
is written to browser storage or experiment artifacts. Document Chat questions
remain independent, as in the existing Ask contract.

## Existing workflows

The primary navigation is Chat, Documents, Results, and Settings. Documents
continues to the existing document workflow; Results opens persisted artifacts.
Existing `/index-builds`, `/jobs`, `/experiments/*`, artifact-detail, and legacy
`/ask` routes remain available for the workflows that have not yet been
consolidated into the chat-first interface.

For the verified local text-RAG example and its resource limits, see
[`docs/DEMO.md`](../DEMO.md).
