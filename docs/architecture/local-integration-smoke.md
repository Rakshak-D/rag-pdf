# Local runtime HTTP smoke test

Run from the repository root with the configured `rag-env` environment:

```powershell
.\rag-env\Scripts\python.exe scripts/local_runtime_smoke.py
```

To include the Vite development proxy, first run `npm ci` in `frontend/`,
ensure Node is on `PATH`, and add `--vite`. The optional `--timeout` sets a
bounded startup wait in seconds, up to 120. The command uses loopback ports,
a unique temporary directory, synthetic PDF bytes, and isolated SQLite and
artifact locations. Its generated token stays in memory and child environment;
server output is suppressed, and it is not placed in URLs or persisted files.
The harness terminates its children and removes only its own temporary data.

The test launches the real `backend.app` module through a temporary guard that
terminates the process if it tries to create a Chroma client, load an embedding
or reranker model, or generate a response. It verifies unauthenticated health,
missing and invalid credentials, manifest inspection, domain-verified RunPlan
preparation, report compatibility, stale manifest and unsafe configuration
rejection, report mismatch, and empty job/artifact lists. It checks that a
missing configured report causes startup to fail. With `--vite`, it also sends
health and authenticated/unauthenticated API requests through the proxy.
Output lists each successful check; failures return a stable code and nonzero
exit status without printing request data or local paths.

The successful build report is synthetic test metadata written exclusively in
the temporary directory. It does not represent a built index. Report matching
does not inspect persisted collections or prove retrieval readiness. The proxy
check is HTTP integration, not a browser test; no PDF extraction, Chroma query,
model inference, Ollama call, or completed experiment is performed.

The current runtime requires an existing report at startup. For first-time
indexing, an operator must prepare a separate, owned index root and use the
existing index builder to create a plan-bound build and report before pointing
the runtime at that report. A future index-building interface must handle
long-running execution, explicit non-destructive ownership, immutable identity,
failure and partial-persistence states, and report publication without
overwriting existing builds. This smoke test does not implement that interface.
