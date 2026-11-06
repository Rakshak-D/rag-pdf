# Linux authenticated browser acceptance

The `Linux authenticated browser acceptance` workflow runs the existing three Playwright scenarios on `ubuntu-24.04`. It starts the ordinary and bootstrap Starlette applications plus Vite from isolated temporary roots, using generated role-specific tokens and a synthetic PDF. The backend entrypoint guards Chroma construction, model loading and generation; no real index or model is used.

CI uses Python 3.10.22 and the exact import-time runtime pins in `scripts/requirements-browser-ci.txt`, plus the pinned frontend lockfile. Platform-specific CUDA, model libraries and Ollama are omitted because the guarded scenarios do not execute those workloads. This is a focused browser-test environment, not a production clean-install qualification.

The Linux runner verifies each selected port through one psutil TCP snapshot and `/proc/net/tcp` plus `/proc/net/tcp6`. It requires a single IPv4 `127.0.0.1` `LISTEN` row, matching socket inode ownership, and a live `/proc` parent chain with stable process start ticks and a task-owned session. HTTP health is checked only after ownership. Cleanup signals verified PIDs through pidfds, then requires successful zero-listener snapshots.

Ordinary and bootstrap child startup also writes a private, bounded JSONL status file in the temporary workspace. It records fixed stage names, safe exception class/module identifiers, stable application error codes and forbidden-operation markers. The runner evidence adds the observed process exit code or signal. Raw tracebacks, exception messages, environment values and tokens are not retained. A status marker never qualifies a service as ready; the runner still requires verified listener ownership and a real HTTP health response.

The synthetic ordinary build report uses the same 32-character lowercase hexadecimal build-ID path contract as index selection. It remains a read-only report fixture; no index is built or opened.

Browser tokens are passed only to the Playwright process environment and are checked against sanitized result files before evidence publication. HTTP and WebSocket traffic is restricted to the exact Vite origin; nonmatching WebSockets are closed. The workflow uses Chromium already installed on the hosted runner; it does not download a browser. The runner generation and Node/Python versions are pinned, while the hosted image's preinstalled Chromium patch version can change. This Linux result does not establish Windows listener ownership or browser acceptance for Documents, Index Selection, Ask, Evaluations or Ablations.

Failed browser results retain only a known scenario title, a fixed failure category and phase, and a source line when Playwright identifies the approved workflow spec. Exception text, stacks, DOM state, console output and arbitrary locations are discarded. The runner validates this bounded schema before classifying a failed scenario or publishing its evidence.

The first bootstrap status authentication exchange has a separate bounded witness for input submission, the browser request event, route interception and decision, HTTP status, response-wait match/timeout, UI authentication error, and page-error occurrence. It stores only fixed event names, so an absent route observation can be distinguished from an absent browser request without retaining URLs, headers, bodies or exception messages.
