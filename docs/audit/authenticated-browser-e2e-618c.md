# Phase 6.18C — Single authenticated browser E2E attempt

**Classification:** `LAUNCHER_PREFLIGHT_BLOCKED`
**Execution date:** 2026-11-05

## Checkpoint and scope

The attempt started from branch `production-evaluation-workbench` at `81a79519b383d1dfd7ffe855b1f015c14fcde281`. At the start and after the attempt, the feature remote matched that commit; protected `main` was `7e9522618296af6a7f25652cc8161009d9a92685`, and the research fixture blob was `25331e9324182002eb0333a5c56034e684e3c018`. `requirements.txt` remained the only pre-existing worktree change, unstaged, with SHA-256 `EF3095DBC1A1657CCB5D5FCBA4D2DF245DD5EC9D04B6C2D237823FDFE472734F`.

The existing launcher and browser tests were used without modification. The effective command was run exactly once:

```powershell
.\rag-env\Scripts\python.exe scripts\authenticated_browser_e2e.py
```

The launcher returned exit code **1**. The outer invocation record shows one invocation, from `2026-11-05T07:08:44.2716853Z` to `2026-11-05T07:08:45.0416454Z` (about 0.77 seconds). Launcher attempt ID: `823382dd267647aeb308d3b2dab1ee30`; invocation-capture ID: `8592d17c7a354c93932ffb7decb62af9`.

## First failure and preflight progress

The durable failure is `E2EBlocked`, code `microsoft_edge_stable_unavailable`, at stage `edge_discovery`. The preceding recorded stages succeeded: plan construction, workspace layout, task-budget preflight, repository identity, token generation, requirements integrity, and Playwright dependency verification. The run did not reach loopback-port selection, Python application imports, fixture/configuration generation, service environment construction, or subprocess preparation.

The launcher failure record says `fixtures_created: false`. It records `subprocess_started: true`, while the launcher-managed child state is `started_count: 0`, `active_count: 0`, and `not_required_no_active_managed_children`. The launcher performed its preflight repository identity work; no E2E service or browser child was started.

A read-only check after the failure found the Microsoft Edge Stable executable in the standard x86 Program Files location, with file-version metadata `156.0.4314.58`. This is installed-file metadata only: Edge was **not launched**, and no runtime browser version was observed. The launcher record itself reports Edge unavailable with no resolved path or version.

The checked-in `_edge_candidates()` copies the Windows environment mapping into a regular Python dictionary, then looks up mixed-case keys including `ProgramFiles(x86)` and `ProgramFiles`. The existing Python environment exposes these names as `PROGRAMFILES(X86)` and `PROGRAMFILES`; ordinary dictionary lookup is case-sensitive. The installed Edge file is under the x86 Program Files location. This makes environment-key case loss a strongly supported explanation for the failed discovery. It was not traced from inside the failed discovery call, so it remains a code-level explanation rather than a separately captured runtime diagnostic. No correction was made in this phase.

## Browser and service results

| Workflow / boundary | Result |
|---|---|
| Bootstrap authentication and preview | **Not run** |
| Read-only build-report inventory | **Not run** |
| Ordinary workbench authentication | **Not run** |
| Browser / Playwright process or session | None observed; launcher stopped before process preparation |
| Ordinary Starlette backend | Not started |
| Bootstrap Starlette backend | Not started |
| Vite proxy | Not started |
| Browser HTTP/API requests | 0 |
| Invalid-credential rejection | Not run |
| Authenticated request and token-isolation assertions | Not run |
| Browser storage, URL, rendered-text, disconnect/reload assertions | Not run |
| Jobs, index, repair, activation, deletion, experiment, or Chroma operations | None submitted or run |

The launcher generated tokens during preflight, but no service/browser process was started and no token-bearing evidence was recorded. No Playwright assertion passed or failed; all three scenarios remain unexecuted.

## Resource use, cleanup, and preservation

The launcher workspace contains only the three durable evidence files listed below, totaling 3,621 file bytes. The outer invocation capture contains three files totaling 922 bytes. No fixture, summary, Playwright result, browser observation, service log, or resource-sampler record was created. The harness resource-monitoring loop starts after preflight, so no continuous peak measurement exists. The most recent C: free-space reading during final checks was 40,250,056,704 bytes; this is above the configured 25 GiB floor. No attribution is made for changes in free space between readings.

The failure record reports zero active managed children. A post-run process check found no other process associated with either task workspace. Port selection was never reached, so the E2E attempt selected no ports and opened no listeners; no test listener required cleanup.

After the attempt, `scripts/verify_baseline.py` reported `Baseline verified: 9 research files unchanged`. The requirements candidate hash remained exact and unstaged. The default Rustup and Cargo homes were compared file-by-file with the retained 6.17H baseline: Rustup matched 133 files / 587,883,039 bytes and Cargo matched 4,224 files / 344,573,568 bytes, with no path, size, content-hash, modification-time, or mode differences. The 6.17K inventory and report hashes matched their recorded identities. All 17 selected isolated Rust/protoc files retained their 6.17I hashes. The pinned Chroma checkout remained clean at commit `ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda`, tree `8d683102c76eea17c4088f1c5de46a1c7cd1239a`, and `Cargo.lock` SHA-256 `D0B2ECF987173344EA347D15D8DF203A3D30D9C237354A8B163AF40F448828D2`. The diagnostic patch SHA-256 remained `276D25F5BA771A33BC4902A904B8C6E2E31C632D02F5BE27FAD44B28CE969920`.

No application source, browser test, dependency, notebook, Rust tool, Chroma installation, or persistent index was changed. No job was created. The 6.18A `BROWSER_EXECUTION_BLOCKED` result and 6.18B synthetic-only `EARLY_FAILURE_CAPTURE_VERIFIED` result remain historical; this phase establishes a new preflight block, not a browser behavior failure.

## Retained evidence

Detailed records remain in task-owned temporary workspaces identified as `rag-pdf-618a-e2e-h5h6fq0p` (launcher evidence) and `rag-pdf-618c-launcher-1a0076d93c9b4b7bb4655babde56b126` (outer invocation capture). The captured JSON records are parseable and contain no bearer-token values.

| Evidence file | Bytes | SHA-256 |
|---|---:|---|
| `launcher-start.json` | 648 | `F32763738C76C3AEE19DC0AA00FD88B5ED368D4FC252F48FE515CD7581EA805F` |
| `launcher-progress.jsonl` | 2,349 | `21452B15002509203600BD101B9407F468479B243B50116921DE78C8F57A0F36` |
| `launcher-failure.json` | 624 | `C272490F8CE5A58D44CCD4232023A0C2DF94157E9D491BAD1C3D819EFD5D187D` |
| `invocation.json` | 327 | `C72C91D4410A8A961F139374E82A14DE12C5435C49F499E86B95CB498DA3CD4F` |
| `result.json` | 262 | `DF766BEE7FEA3F6BD9001973BBDEA5A07508A7D2686515D84CB75D43E4F75160` |
| `stdout-stderr.log` | 333 | `A8CED8FD712365EB355B9009A5ED7615EFC9B6F7CCE17A4EA932C89819F5E41F` |

The launcher source SHA-256 recorded at start was `AC47A737728F937EFD2D00761438A592B4CFD9440E7F4BC445A8AA2DC68D3EC4`.

## Decision

This phase is `LAUNCHER_PREFLIGHT_BLOCKED`; it provides no browser workflow coverage. The first evidence-supported next task is a narrowly scoped launcher correction and unit test for case-insensitive Windows environment-key handling in Edge discovery. Any subsequent real-browser attempt requires separate authorization and must remain a single bounded invocation. No retry is included here.

HNSW and publisher incidents remain unresolved. Phase 6.15D remains C — native build deferred. Dependency adoption remains deferred, Chroma migration unauthorized, and unattended deployment remains **NO-GO**.
