# Phase 6.18B — Browser launcher preflight evidence hardening

## Classification

**`EARLY_FAILURE_CAPTURE_VERIFIED`** for the launcher’s preflight failure-recording contract, based on nine focused synthetic unit tests. This is not browser execution evidence. No browser, backend, Vite process, or E2E workflow was started in this phase.

## Starting checkpoint and preserved history

- Branch: `production-evaluation-workbench`
- Starting local and remote HEAD: `fc007afb3d87c975e5ff59c78e243b9ae774c5c5`
- Protected `main`: `7e9522618296af6a7f25652cc8161009d9a92685`
- Protected fixture blob: `25331e9324182002eb0333a5c56034e684e3c018`
- `requirements.txt` SHA-256: `EF3095DBC1A1657CCB5D5FCBA4D2DF245DD5EC9D04B6C2D237823FDFE472734F`
- Phase 6.18A remains `BROWSER_EXECUTION_BLOCKED`.

The original 6.18A workspace contained six empty directories and did not retain the startup exception. No evidence identifies that original exception, and this phase does not infer it.

## Launcher changes

The launcher now obtains its current Git root, commit, and branch with read-only `git rev-parse` commands. It validates the repository root and the expected feature branch, then records the actual runtime commit. The stale `REPOSITORY_HEAD` constant is removed; no Git mutation is performed by the launcher.

Immediately after creating a unique task workspace, the launcher creates `evidence/launcher-start.json`, flushes and fsyncs it, reads it back, and checks the decoded record. It includes a UUID attempt ID, UTC timestamp, actual commit and branch, launcher source SHA-256, Python version, intended scenario scope, schema version, and initial stage. It contains no tokens, environment dump, process command line, or path inventory.

Every preflight stage appends and fsyncs `stage_started` and its success or failure outcome in `evidence/launcher-progress.jsonl`. The covered sequence includes workspace layout and budget input, token generation, requirements integrity, Playwright package/lock/node-module validation, Edge discovery, loopback port ownership, Python imports, synthetic fixture creation, configuration and manifest generation, service environment construction, and subprocess preparation.

A preflight exception writes and fsyncs `evidence/launcher-failure.json` with the attempt ID, last stage, stable failure code, exception class, subprocess/process state, fixture-created flag, Edge discovery state, and UTC failure time. `subprocess_started` includes the completed read-only Git identity command; `managed_process_state` separately reports service/browser child counts and active cleanup state. Raw exception text, traceback, environment variables, and credentials are omitted. `E2EBlocked` retains its stable gate code; other exceptions use `preflight_unexpected` while retaining only their class name.

If workspace creation or initial evidence writing fails, stdout returns `LAUNCHER_PREFLIGHT_BLOCKED`; when evidence writing fails it uses `EVIDENCE_CAPTURE_FAILED` and explicitly reports `durable_evidence: false`. Failures that occur before a workspace can be created are not described as durably recorded. A normal preflight failure is reported as `LAUNCHER_PREFLIGHT_BLOCKED`, distinct from the historical `BROWSER_EXECUTION_BLOCKED` phase outcome.

Edge discovery now checks only standard Stable locations under `%ProgramFiles(x86)%`, `%ProgramFiles%`, and `%LOCALAPPDATA%`. It records found/unavailable status, resolved executable path, file version metadata, and which allowlisted location matched. File version is read through the Windows version resource API. Edge is never launched, and there is no Chrome/Chromium fallback.

## Synthetic failure-injection coverage

`tests/unit/test_authenticated_browser_launcher.py` contains nine focused tests covering:

- Requirements integrity failure before fixture creation.
- Invalid disk-budget input after the start record.
- Missing Playwright dependency.
- Missing Edge discovery, with no fallback.
- A simulated busy loopback port.
- An unexpected exception during configuration/manifest preflight.
- Durable start-record creation and read-back, including actual Git identity.
- Failure-record write failure returning nonzero `EVIDENCE_CAPTURE_FAILED` without claiming retention.
- Edge allowlist path, resolved path, version, and discovery-source reporting.

Across the injected preflight failures, tests validate parseable nonempty evidence, stage/code attribution, nonzero exit results, `LAUNCHER_PREFLIGHT_BLOCKED`, zero managed child-process PIDs, `fixtures_created: false`, and absence of a synthetic bearer sentinel, exception message, or traceback in evidence. The Git identity test compares the recorded commit to the current checked-out `git rev-parse HEAD` and confirms the stale constant is absent.

These tests exercise the evidence helpers and injected preflight stages. They do not run the full launcher, Edge, Playwright, Vite, either backend, network requests, or real PDF/configuration fixtures.

## Verification

| Command | Result |
|---|---|
| `.\rag-env\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/unit/test_authenticated_browser_launcher.py` | Exit 0; 9 passed. |
| Standard-library AST parse of launcher and new test module | Exit 0; both parsed. |
| `.\rag-env\Scripts\python.exe scripts/verify_baseline.py` | Exit 0; all 9 protected research files unchanged. |
| `git diff --check` | Passed. |

The original Chroma source checkout remains at commit `ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda`, tree `8d683102c76eea17c4088f1c5de46a1c7cd1239a`, with a clean worktree. The retained diagnostic patch remains SHA-256 `276D25F5BA771A33BC4902A904B8C6E2E31C632D02F5BE27FAD44B28CE969920`.

The requirements candidate remains byte-identical and unstaged. No browser or service process was launched; the synthetic port test used an injected listener result and did not bind sockets. No frontend, backend, notebook, package, or native-code change is included in this hardening beyond the specified launcher and unit test.

## Remaining uncertainty and next decision

The precise 6.18A startup exception remains unknown. Nine synthetic tests establish the new evidence-capture behavior but do not prove that Playwright can launch the installed Edge channel or that browser workflows pass.

Because early-failure capture is now verified, the next decision may authorize one separately bounded Phase 6.18C real-browser attempt. That authorization is not exercised here. HNSW and publisher incidents remain unresolved, dependency adoption remains deferred, Chroma migration remains unauthorized, and unattended deployment remains **NO-GO**.
