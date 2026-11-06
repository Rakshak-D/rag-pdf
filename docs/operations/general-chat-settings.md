# General Chat settings

Settings on the Chat page apply only to **General Chat**. They do not change
Document Chat, its selected index, the pinned research RunPlan, retrieval,
embeddings, reranking, or evaluation configuration.

## Available preferences

- **Model:** choose from the models the configured local Ollama service reports
  as installed. The configured default is identified separately. A model that
  is missing is rejected by the server; the application does not download
  weights or substitute another model during inference.
- **Temperature:** a finite number from `0.0` to `2.0`.
- **Maximum answer length:** an integer from `32` to `2048` tokens.

Thinking mode and context-window size are not exposed. The installed-model
catalog does not positively establish those model-specific capabilities, so
the interface cannot safely promise that either option will affect inference.

## Saving and persistence

Choose **Save preferences** to apply the values to subsequent General Chat
requests. **Reset to defaults** immediately saves the configured-default model
choice, temperature `0.7`, and maximum answer length `512`.

The browser stores a versioned, non-secret record at
`localStorage["rag-pdf.general-chat-preferences"]`:

```json
{
  "version": 1,
  "selectedModel": null,
  "temperature": 0.7,
  "maxOutputTokens": 512
}
```

`selectedModel: null` means “use the server-configured default.” A non-null
value is an explicitly selected installed model. The record is local to this
browser profile. It is not synced to other devices and can be removed when
browser data is cleared. It never stores credentials or chat history.

On load, the browser validates the record and resets a malformed or unsupported
version to safe defaults. The selected model is reconciled against the live
installed-model catalog. If it is no longer installed, the selection resets to
the configured default and the interface reports whether that fallback is
installed. The server independently validates every requested model against
the current inventory and validates generation limits for every inference.

## Request behavior

The existing `GET /api/v1/chat/models` endpoint supplies the configured model
and installed-model inventory. General Chat sends the selected model and a
restricted `generation` object to the existing `POST /api/v1/chat` endpoint.
The backend maps `temperature` to Ollama `options.temperature` and
`max_output_tokens` to Ollama `options.num_predict`. If `generation` is
omitted, the existing defaults remain `0.7` and `512`.

The request cannot set Ollama's host, paths, system prompt, tools, or arbitrary
options. The backend retains its server-owned prompt, bounded session history,
installed-model verification, and concurrency limit. General Chat does not
retrieve documents or produce citations.

If Ollama is unavailable, Settings reports the service state separately from a
model that is simply not installed. Chat requests continue to receive the
existing sanitized unavailable or missing-model error codes.
