# Hosted model-free launcher acceptance

This check runs the supported local launcher on an isolated Ubuntu CI runner. It requires at least 4 GiB available physical RAM immediately before startup. This is only a model-free startup threshold; it does not qualify memory for Gemma or embedding inference.

## Enforced controls

- The runner must be Linux under GitHub Actions, and `HEAD` must equal the workflow SHA.
- The tracked Orbit PDF must match its pinned SHA-256 before and after copying to a fresh OS-managed temporary source directory.
- Source, index storage, ordinary state and bootstrap state use four disjoint temporary paths. The launcher receives an explicit environment allowlist with state paths set inside that workspace.
- The hosted resource gate fails below 4 GiB free physical RAM.
- The real launcher starts the four services. Their selected listener ports must be owned by observed task processes and bind to `127.0.0.1`; each service must pass its health/readiness request.
- The browser test blocks requests observed by its Playwright page route when they leave the application origin, and aborts observed non-read-only HTTP methods. Service workers are disabled for that test. Its separate Playwright API request context allows only checked same-origin reads, disables redirects, and records the method and path of each call. These checks do not observe unrelated processes or browser activity outside those Playwright contexts.
- The browser reads the index selection and requires `unselected`; the test submits no chat, preparation or build request. Index storage must remain empty.
- Shutdown sends SIGINT to the observed launcher identity. Fallback termination uses only recorded PID plus process-start-time identities. It never kills by executable name or port. Listener release is checked separately; an unowned listener is never terminated.

These controls cover the checked launcher and browser journey. They do not establish a general sandbox for the whole runner.

## Sampled process monitoring

The supervisor samples the launcher descendant tree and the Playwright process tree every 0.25 seconds, retaining PID, start time, process name and parent/depth information. The interval is bounded from 0.05 to 1 second. It detects unexpected root or launcher exits and task-owned descendants with known model-serving process names, including Ollama, llama-server, vLLM and related runtimes. Process arguments are not emitted or stored.

Observed process identities are used for cleanup if the launcher, browser, or a child fails or outlives its parent. Regression tests cover synthetic model identities, unexpected launcher exit, an orphaned child, browser failure, SIGINT timeout, and ports that remain occupied. The primary acceptance error is retained if cleanup also reports an error; only sanitized error types are recorded.

This is periodic observation, not kernel-level process containment. A very short-lived or reparented process can escape a sample. Name-based detection cannot identify every model runtime, and a model loaded inside an ordinary Python process may not have a distinctive process name. A passing run therefore means no known model process was observed in the sampled task trees; it is not proof that all possible model execution is impossible.

## Backend network observation

When `strace` is present, the launcher and its descendants run under `strace -f` with only `connect` syscalls traced. The observer counts loopback, non-loopback and unparseable IP connection attempts. The check fails if it observes a non-loopback or unparseable IP `connect`. Raw trace output is temporary and is not uploaded. A failure artifact, when available, contains only sanitized failure categories, resource values, port numbers, process-monitor counts and network counts; it contains no command arguments or credentials.

This is process-scoped **observation**, not enforcement. It does not block an attempted connection. It does not cover UDP/datagram-only traffic such as DNS `sendto`, every possible syscall, or traffic from processes outside the traced launcher tree. No runner-wide firewall or privileged network policy is changed. The browser route checks separately block external page requests, but they do not cover arbitrary backend egress.

Pinned Python/npm/Chromium setup may use the network before runtime acceptance begins. That setup traffic is distinct from runtime observations. Offline-related environment flags disable known model-hub downloads and common telemetry, but flags alone are not proof of network isolation.

## Evidence boundary

The test uses the genuine local APIs, frontend, gateway and PDF bytes. It does not submit an Ask or General Chat request, prepare an index, load models, run Ollama, or generate an answer. It proves only the checked model-free startup and source-viewer path. It does not prove real retrieval, index construction, model generation, Windows launcher behavior, or safety under model load.

On failure, the supervisor writes a sanitized JSON diagnostic under the hosted runner's temporary directory, which the existing viewer job uploads only on failure. The temporary application workspace and raw connect trace are removed when the check exits.
