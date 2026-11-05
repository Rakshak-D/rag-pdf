# Phase 6.18E — Authenticated browser E2E execution

**Classification:** `LAUNCHER_PREFLIGHT_BLOCKED`
**Date:** 2026-11-05

## Authorization and starting checkpoint

This phase authorized one execution of the existing E2E launcher. It started on `production-evaluation-workbench` at `6766168dffaeb4ae0a9fd8bfc4906b1b812ac915`, matching the feature remote. Protected `main` was `7e9522618296af6a7f25652cc8161009d9a92685`, the protected fixture blob was `25331e9324182002eb0333a5c56034e684e3c018`, and the only pre-existing worktree modification was unstaged `requirements.txt` with SHA-256 `EF3095DBC1A1657CCB5D5FCBA4D2DF245DD5EC9D04B6C2D237823FDFE472734F`.

The existing launcher, Playwright configuration/spec, and redacted reporter were used unchanged. Preflight confirmed Python 3.10.11, Node 24.14.0, npm 11.12.1, Playwright Test 1.64.1, the local Vite entry, and installed Edge Stable file metadata version `156.0.4314.58`. The launcher and browser-test sources had no pre-existing worktree changes. No prior task-owned service process was found. C: free space was about 36.3 GiB, above the 25 GiB floor. The pre-run nine-file research baseline passed.

## Single invocation and first failure

The launcher command was executed exactly once:

```powershell
.\rag-env\Scripts\python.exe scripts\authenticated_browser_e2e.py
```

It exited **1** after about **0.525 seconds**. The launcher attempt ID was `7ae53c306aa04ff7857e3e6712c33b64`.

Durable preflight records show these stages succeeded: plan construction, workspace layout, task-budget check, repository identity, token generation, requirements integrity, Playwright dependency verification, Edge discovery, and loopback-port selection/ownership. The first failure was stage `python_imports`, code `preflight_unexpected`, exception class `ModuleNotFoundError`. The launcher did not retain the missing module name, exception message, or traceback. The source shows this stage imports `ResolvedRunConfig`, document-manifest and run-plan helpers, and `notebooks.config`; the evidence does not identify which import raised. No import was repeated to investigate it.

The start record confirms that the committed launcher hash was `C500A6A142C7AC015D8DEB84DDE0F017094249E6C3881F9DCF2B9636952579E0`; the failure record records Edge discovery as `program_files_x86` with file version `156.0.4314.58`. Edge was **not launched**.

The failure record says `fixtures_created: false`; its managed-process state is `started_count: 0`, `active_count: 0`, and `not_required_no_active_managed_children`. Although the generic `subprocess_started` field is true, the preflight uses read-only subprocess checks; no ordinary backend, bootstrap backend, Vite, Playwright, or browser child started. The launcher reached port selection, but failed before Python application imports completed and before service startup. Port selection probes use short-lived sockets that close on return. No service listener was started. The selected port numbers were not retained in the early-failure record, so there is no per-port post-run snapshot.

## Browser scenarios and safety observations

| Scenario / assertion group | Result |
|---|---|
| Bootstrap authentication and preview | **Not run** |
| Read-only build-report inventory | **Not run** |
| Ordinary workbench authentication and Jobs/Artifacts navigation | **Not run** |
| Edge runtime / Playwright browser session | None; Edge file metadata was observed, but no browser runtime version exists |
| Backend, bootstrap service, Vite | Not started |
| Browser HTTP/API requests or authentication assertions | 0; not run |
| Token storage, URL, rendered-text, and token-scope assertions | Not run |
| Build, repair, activation, deletion, experiment, or index submission | None submitted |
| Chroma queries, model inference, or downloads | None |

The launcher generated test tokens in memory during preflight, but no service or browser received them. The durable start/progress/failure records and launcher output contain no token values. Request guards were not exercised because Playwright did not start.

## Resource, cleanup, and preservation

The launcher’s `task_budget` preflight succeeded. Its runtime resource sampler starts after preflight, so no continuous peak sample was created. The launcher workspace contains only three evidence files totaling 4,263 bytes; the external invocation capture contains five files totaling 3,396 bytes, including its evidence manifest. The most recent C: free-space reading was 38,945,857,536 bytes, above the 25 GiB floor. No resource-limit breach was observed; the runtime sampler did not run.

The managed-child record and a post-run process check found no task-owned service, Vite, Playwright, or browser process. No service listener was started. Workspace inspection found no generated PDF fixture, index storage file, or job output. The launcher stopped before fixture creation and before any API request.

After execution, `scripts/verify_baseline.py` reported `Baseline verified: 9 research files unchanged`. The requirements candidate remains unstaged at its expected SHA-256. Default Rustup and Cargo were compared file-by-file with the retained 6.17H baseline: Rustup matched 133 files / 587,883,039 bytes and Cargo matched 4,224 files / 344,573,568 bytes, with no path, size, content-hash, timestamp, or mode differences. All 17 selected isolated Rust/protoc files matched their retained 6.17I hashes. The pinned Chroma source checkout remains clean at commit `ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda`, tree `8d683102c76eea17c4088f1c5de46a1c7cd1239a`, and `Cargo.lock` SHA-256 `D0B2ECF987173344EA347D15D8DF203A3D30D9C237354A8B163AF40F448828D2`. The diagnostic patch remains SHA-256 `276D25F5BA771A33BC4902A904B8C6E2E31C632D02F5BE27FAD44B28CE969920`.

No application source, dependency, notebook, Chroma installation, native tool, or persistent index was modified. No experiment or job was submitted.

## Retained evidence and accounting note

Detailed evidence is retained in external task-owned workspaces `rag-pdf-618a-e2e-ejqycqs2` (launcher) and `rag-pdf-618e-1e1f3c5e7f774541a55e788718908729` (invocation capture). The launcher start, progress, and failure JSON/JSONL records are parseable. Their hashes, along with the invocation output and result records, are in `evidence-manifest.json` (SHA-256 `2DB6976B08C2B91976AECC652E8B792ED679BD8729E4D349F456A3899DA7DFDB`).

The original outer `result.json` is preserved (SHA-256 `90F2EC182DF82950A50F7EC7E17C49C0E61602E37D71D9BA63E5D6BA690342B9`). Its elapsed-time field is invalid because the wrapper converted UTC timestamps as local time. The separate `result-time-reconciled.json` preserves the original timestamps and records the corrected duration, 0.525 seconds (SHA-256 `1B7CA2833B2B3DBEC122F97BBE2C706BC906B4C1E600E86B551435CF0E9BF358`). The raw result was not rewritten.

A capture-record update command failed before the launcher was invoked; it is preserved in the invocation record. After correcting that wrapper-only issue, exactly one launcher invocation occurred. No second launcher attempt was made.

## Decision

This phase is `LAUNCHER_PREFLIGHT_BLOCKED`. It establishes no browser workflow coverage and does not identify the missing Python module. The next bounded engineering decision is to determine which import fails in `python_imports` using a narrowly scoped diagnostic that retains only a safe module identifier, then separately decide whether another browser attempt is warranted. No such diagnostic or rerun is authorized by this report.

Phase 6.18A remains `BROWSER_EXECUTION_BLOCKED`; 6.18B remains `EARLY_FAILURE_CAPTURE_VERIFIED`; 6.18C remains `LAUNCHER_PREFLIGHT_BLOCKED`; and 6.18D remains `EDGE_DISCOVERY_VERIFIED`. HNSW and publisher incidents remain unresolved, native compilation remains deferred, and unattended deployment remains **NO-GO**.
