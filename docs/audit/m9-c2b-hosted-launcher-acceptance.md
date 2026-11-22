# Hosted model-free launcher acceptance

This check runs the supported local launcher on an isolated Ubuntu CI runner. It requires at least 4 GiB of available physical memory immediately before startup. This threshold applies only to this model-free check; it does not establish capacity for model inference.

The supervisor verifies the tracked Orbit PDF hash, makes disjoint temporary source, index and runtime directories, chooses available loopback ports, and starts `scripts/start_local_app.py`. It checks the four listener owners and health endpoints, then runs a local Playwright browser against the genuine Library and PDF viewer. It signals the launcher to shut down and checks for remaining child processes and listeners. The temporary workspace is removed after shutdown. The browser rejects requests outside the application origin and checks that backend authorization headers are not exposed to browser requests.

CI setup downloads pinned Python dependencies, locked npm dependencies and the Playwright Chromium revision. These setup downloads are separate from browser runtime traffic. The lightweight Python requirements include unpinned transitive dependencies; the direct requirements and frontend lockfile are pinned. No model weights, model runtime packages or Ollama service are installed by this job.

Passing this check demonstrates only the tested model-free startup and document-viewing path. It does not demonstrate real retrieval, index construction, model generation, Windows launcher behavior, or the memory requirements of Gemma. It does not enforce operating-system-wide network isolation for backend services. The current local-machine resource block remains separate.
