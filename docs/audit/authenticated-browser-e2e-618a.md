# Phase 6.18A — Authenticated browser E2E

## Outcome

**Classification: `BROWSER_EXECUTION_BLOCKED`.** The implementation and frontend verification completed, but the one authorized launcher invocation stopped before starting either backend, Vite, or Playwright. There was no real browser session and none of the three browser scenarios ran.

The launcher workspace contained only its six initial empty directories (`bootstrap`, `evidence`, `logs`, `ordinary`, `playwright-results`, `tmp`): zero files and zero bytes. Its code creates synthetic fixtures before spawning child processes. The observed workspace therefore supports that startup stopped before fixture creation and process startup. A post-run process scan found no matching launcher, backend, Vite, or Playwright child. The launcher did not preserve its early exception detail or a durable summary, so the exact early failure is unknown. No second invocation was made.

## Checkpoint and environment

- Starting branch: `production-evaluation-workbench`
- Starting local and remote feature HEAD: `15e15cbea9aef69abbc12253ded514c9387aac51`
- Protected `main`: `7e9522618296af6a7f25652cc8161009d9a92685`
- Protected fixture blob: `25331e9324182002eb0333a5c56034e684e3c018`
- Existing deferred `requirements.txt` SHA-256: `EF3095DBC1A1657CCB5D5FCBA4D2DF245DD5EC9D04B6C2D237823FDFE472734F`
- Node: `v24.14.0`; npm: `11.12.1`
- `@playwright/test`: `1.64.1`
- Installed Microsoft Edge Stable executable metadata: `156.0.4314.58`. Edge was not launched, so no browser runtime version was observed.

Playwright Test was added as the exact pinned development dependency. Browser download was skipped. The bounded package acquisition evidence records 14,708,356 proxy-counted bidirectional stream bytes across the failed and successful npm attempts, below the 250 MiB cap. The measurement excludes DNS and lower-level transport framing. The successful npm record reports 73,371,441 bytes peak additional task-owned footprint and 34,177,773,568 bytes minimum free C: space. The E2E launcher did not reach its own resource sampler.

## Implementation

The harness adds a separate Playwright configuration and `npm run test:e2e`, with one worker, no retries, a 120-second per-test limit, a 10-minute overall launcher limit, Edge channel selection, and no screenshots, video, trace, or credential storage state. A custom reporter records only titles, statuses, and durations. Browser observations are designed to omit bearer values and payloads.

The three serial specs cover:

1. Bootstrap authentication, preview identity checks, disconnect, and reload.
2. Read-only empty build-report inventory, `store_checked: false`, and disconnect/reload.
3. Ordinary workbench authentication, readiness distinction, Jobs/Artifacts navigation, empty catalogs, and disconnect/reload.

The Python launcher is designed to create separate test-owned ordinary and bootstrap backends, Vite proxy targets, synthetic PDF/config/report fixtures, random independent tokens, loopback-only dynamic ports, forbidden-operation guards, and child-process cleanup. The ordinary runtime guard rejects Chroma/model operations. No production application source was changed.

The only launcher-attempt workspace was `%LOCALAPPDATA%\Temp\rag-pdf-618a-e2e-pfw3z6ie`. It has no original runner logs, result JSON, observations, fixtures, or tokens. A post-run inventory record was written there after inspection; its SHA-256 is `AE53863D666AE26340EC903142732C2809E2642F135F0E65439D5D6442752234`. This record is explicitly a later inspection, not original runner output. The empty workspace does not establish a browser version, test result, selected-port list, or the launch exception.

## Scenario and security results

| Scenario | Result | Evidence |
|---|---|---|
| Bootstrap authentication and preview | Not run | Playwright child was not started. |
| Read-only report inventory | Not run | No request or inventory was submitted. |
| Ordinary workbench authentication | Not run | Ordinary backend and browser were not started. |

No browser requests were observed. Token rejection, token isolation, memory-only state, route separation, proxy behavior, and reload/disconnect behavior remain unverified. No browser URL, storage, cookies, or rendered text were available to inspect. The launcher stopped before creating fixtures or starting services; consequently it did not create an index, submit a job, initialize Chroma, invoke a model, or access user PDFs.

There are no owned service/browser processes or listeners evidenced as remaining. Since the launcher did not preserve the selected ephemeral port values or process tree, per-port cleanup evidence is unavailable; source order and the empty workspace indicate those service processes were never spawned.

## Verification

Commands were run from `frontend/` unless noted:

| Command | Result |
|---|---|
| `npm test` | Exit 0; 6 test files, 95 tests passed. |
| `npm run typecheck` | Exit 0. |
| `npm run build` | Exit 0; Vite built 40 modules. Output is ignored under `frontend/dist/`. |
| `python scripts/verify_baseline.py` using `rag-env` | Exit 0; `Baseline verified: 9 research files unchanged`. |
| `git diff --check` | Passed. |

The research baseline was also verified before implementation. The required checks confirm no protected research-file drift. The E2E runner itself was invoked once; Playwright child invocations: **0**. No E2E retry occurred.

## Limitations and next action

The current launcher does not durably record exceptions that occur before its child-process `try/finally` section. That prevented exact attribution of this startup block. The next engineering action is to add a durable launcher-start/early-failure record and retain the stable failure code, then request separate authorization for one new bounded real-browser attempt. Do not infer any browser behavior from the passing Vitest suite, typecheck, or production build.

This checkpoint does not qualify browser authentication, backend readiness, proxy isolation, HNSW reliability, publisher reliability, model availability, or unattended deployment. HNSW and publisher incidents remain unresolved; dependency adoption remains deferred, Chroma migration remains unauthorized, and unattended deployment remains **NO-GO**.
