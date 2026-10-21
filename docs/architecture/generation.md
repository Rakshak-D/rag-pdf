# Local answer generation

`LocalGenerationAdapter` consumes an `EvidenceContext` and its matching
verified `RunPlan`. It carries the original query, index/document/run
identities, context fingerprint, model name, status, attempts, locally counted
text/prompt/output tokens, and SHA-256 hashes of image bytes sent to Ollama.
It also exposes a SHA-256 `prompt_fingerprint` for the final rendered system
and user messages. Results are frozen and do not persist prompts, responses,
image bytes, or absolute paths.

Before a client is constructed, generation verifies the RunPlan, query ID
against the existing retrieval formula (query text, modalities, and k limits),
all plan/context identities, and the version-2 canonical context fingerprint.
That fingerprint covers the complete formatted text, selected text provenance
and truncation state, selected image provenance/captions, and each image path
relative to the configured image root. It is an accidental-change consistency
check, not a cryptographic authenticity signature.

The selected text tuple must exactly reconstruct the formatted text evidence in
order, including labels and truncation markers. Empty text is permitted only
when at least one image is selected and revalidated. Image labels must be
sequential (`I1`, `I2`, ...); source, page, and captions are bounded and
control-character-normalized for prompt display while the original structured
records remain unchanged. Absolute image paths never enter the prompt.

The adapter validates plan and context provenance, re-counts the text context
with the configured `tiktoken` encoding, checks a bounded prompt, and
revalidates every selected image beneath the configured `images_dir` immediately
before reading it. Images are passed in label order as base64 payloads. Image
token cost is model-specific and is not included in text token counts.

Ollama is imported and constructed lazily through an injectable client factory.
The default client uses `http://127.0.0.1:11434`, never starts a server, and
never pulls a model. Tests use fake clients, tokenizers, and sleepers.

The system instruction requests evidence-only answers, explicit uncertainty,
labelled citations, and treatment of retrieved content as untrusted data. This
is a behavioral prompt control, not a hard prompt-injection boundary.

Failures before inference raise typed validation, image, tokenization, or
client errors. Inference retries use `llm_retries + 1` attempts and bounded
exponential backoff. With `llm_fail_fast=True`, exhausted inference raises a
typed error; otherwise the adapter returns a failed result with no synthetic
answer and a stable error code. Raw exception text and stack traces are not
placed in routine results.

Generation does not run evaluation metrics or implement APIs, UI, persistence,
active-index promotion, streaming, or deployment orchestration. Model-weight
provenance remains outside this adapter.
