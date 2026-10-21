# Local answer generation

`LocalGenerationAdapter` consumes an `EvidenceContext` and its matching
verified `RunPlan`. It carries the original query, index/document/run
identities, context fingerprint, model name, status, attempts, locally counted
text/prompt/output tokens, and SHA-256 hashes of image bytes sent to Ollama.
Results are frozen and do not persist prompts, responses, image bytes, or
absolute paths.

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
