# Phase 6.18G — First post-preflight browser execution

**Classification:** `BROWSER_EXECUTION_BLOCKED`
**Date:** 2026-11-05

## Checkpoint and single invocation

The run began on `production-evaluation-workbench` at `557dfb91c8873d2af3d1e57ed1bbfc705fc16d19`, matching the feature remote. Protected `main` was `7e9522618296af6a7f25652cc8161009d9a92685`, the fixture blob was `25331e9324182002eb0333a5c56034e684e3c018`, and the only pre-existing worktree change was unstaged `requirements.txt` at SHA-256 `EF3095DBC1A1657CCB5D5FCBA4D2DF245DD5EC9D04B6C2D237823FDFE472734F`. The pre-run nine-file research baseline passed.

The committed launcher and browser harness were used unchanged. Exactly one normal invocation ran:

```powershell
.\rag-env\Scripts\python.exe scripts\authenticated_browser_e2e.py
```

It exited **1**. The outer capture measured **17.846 seconds**; the launcher summary measured **15.922 seconds**. No retry or separate Playwright command was run.

The outer invocation ran from `2026-11-05T08:54:32.5391941Z` through `2026-11-05T08:54:50.3855749Z` (capture attempt `392c0f140ea24dfd9995762bc671a454`; launcher attempt `69e09b941e7f47daaf955d510bcaf2a6`).

## Startup and first failure

All 14 preflight stages succeeded, including repository/requirements checks, Playwright package verification, Edge discovery, port selection, imports, synthetic fixtures/configuration, service-environment preparation, and command preparation.

Both backend processes were started. Their redacted logs record application startup completion and Uvicorn binding to loopback. The ordinary health path reached the listener ownership check: in the launcher, that check runs only after a health response has status 200 and `status: "ok"`. The first stable failure was:

```text
ordinary_backend_listener_count_invalid
```

The source raises this `E2EBlocked` code when the listener query returns a count other than one. The run did not retain the observed count or matching addresses, so the evidence cannot distinguish zero matches from multiple matches. `listener_owners` remained empty because the check failed before the owner record was returned.

The bootstrap backend process also logged startup completion and a loopback bind, but its readiness check was not reached after the ordinary listener check failed. Vite did not start. Edge was discovered at the approved location with file version `156.0.4314.58`, but **Edge was not launched**. Playwright did not start; browser runtime version is unavailable.

## Browser coverage and safety

| Scenario | Result |
|---|---|
| Bootstrap authentication and preview | **Not run** |
| Read-only build-report inventory | **Not run** |
| Ordinary workbench authentication and Jobs/Artifacts | **Not run** |

There were zero browser scenarios, zero Playwright result records, and no browser observations file. No authenticated browser API request or browser security assertion ran. Invalid-token rejection, token isolation/storage, UI state, and the three scenario workflows are all **not run**. The launcher reports zero secret-scan matches.

The ordinary test-owned index directory contains only the expected synthetic build-report fixture; bootstrap index storage is empty. The launcher reports `no_index_or_job_mutation: true`. No index build, experiment/job submission, Chroma operation, or model operation occurred.

## Cleanup and resources

The launcher sent graceful stop signals to both backend process roots. Each process returned exit code 3; their logs show orderly Uvicorn shutdown and process completion. The launcher reports `cleanup_ok: true`, no surviving descendants, and empty listener lists for all three selected ports. A post-run process query found no matching launcher, Vite, Playwright, or backend process; a targeted listener query also found no listener on the two backend ports.

Peak task-owned bytes sampled by the launcher were **109,542**; minimum observed free C: space was **38,568,714,240 bytes**. No disk or time limit was reached. No package or browser download occurred.

## Preservation

The post-run verifier reported `Baseline verified: 9 research files unchanged`. `requirements.txt` retained its expected SHA-256 and remained unstaged. No launcher, browser-test, frontend, backend, package, or notebook source file changed.

The pinned Chroma checkout remains clean at commit `ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda`, tree `8d683102c76eea17c4088f1c5de46a1c7cd1239a`; its `Cargo.lock` SHA-256 remains `D0B2ECF987173344EA347D15D8DF203A3D30D9C237354A8B163AF40F448828D2`. The diagnostic patch remains SHA-256 `276D25F5BA771A33BC4902A904B8C6E2E31C632D02F5BE27FAD44B28CE969920`.

The post-run default `.rustup` inventory matches the Phase 6.17H and Phase 6.18F per-file records: 133 files / 587,883,039 bytes. Default `.cargo` also matches: 4,224 files / 344,573,568 bytes. Relative paths, sizes, modification times, SHA-256 values, and modes match when the mode is read using `Path.stat`, consistent with the reference snapshots. All 19 selected isolated Rust/protoc file hashes match Phase 6.17I. The initial comparison using `DirEntry.stat` displayed mode-only differences for executable files; a follow-up using the reference `Path.stat` semantics resolved those differences. The retained 6.18F snapshot came from a helper that had earlier failed during `fsync` after writing its temporary file; it was used only as corroboration alongside the 6.17H after-snapshot. The post-run inventory used a separate read-only scan with stability checks and a write-handle `fsync`. No Rustup, Cargo, compiler, or installer was run.

## Evidence and decision

The task-owned invocation capture is identified as `rag-pdf-618g-e2e-e7f57b53b00f4c18a57b6652bdb8cd12`; the launcher workspace is `rag-pdf-618a-e2e-6ztcz3ub`. The complete external manifest inventories 25 files (1,317,587 bytes, excluding the manifest) and has SHA-256 `613A0259A26BE4ACADB81CBB81677240BF239AF111CD849DA6011D472FAAB888`.

Key evidence hashes:

- Launcher summary: `F84FF0091DB6093455D33D245ECD34D18FA130F51126DB1D4BED486FB47DB213`
- Launcher detail: `25C8BAAE4873DFA579E2856DA947C6ED571CD46F8EDEFCCA49414ADE158F6F0A`
- Launcher progress: `2C2343B53480EC3DFCE4DE3E8CD079CF3957A575FB284423FF5D5B534E405220`
- Ordinary backend log: `DCEDE59179F24F1B9DC2D3F7F7A4978BD86673EFDA4476058669944975A7025D`
- Bootstrap backend log: `44558FDCCC2E291401FA81E9D22B9B8E304DA337BB031BDDAF2354622B8B1D96`
- Rust/Cargo preservation comparison: `15D682038AD7EB88987E5F63AFC0BC00F3DCF2F7E528A14A31FA5DF0150EAB54`
- Isolated-tool comparison: `2DC6DDAED4782E477A1BA6AC9616499F1489C5A660D814C6F556C6D6B53FC6B8`

This run establishes backend startup but no browser workflow coverage. The next bounded engineering step is a read-only review of the Windows listener ownership query and its missing count/address evidence for `ordinary_backend_listener_count_invalid`. Do not repeat the E2E attempt until that failure is understood and separately authorized.

Phase 6.15D remains C — native build deferred. HNSW and publisher incidents remain unresolved. Dependency adoption remains deferred, Chroma migration remains unauthorized, and unattended deployment remains **NO-GO**.
