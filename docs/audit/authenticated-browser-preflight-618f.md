# Phase 6.18F — Python import correction and browser preflight

**Classification:** `PREFLIGHT_READY_NO_BROWSER`
**Date:** 2026-11-05

## Starting checkpoint and scope

The work began on `production-evaluation-workbench` at `302fc805e751e7adde2db5a2158ae3fde1b15966`; the feature remote matched. Protected `main` was `7e9522618296af6a7f25652cc8161009d9a92685`, the protected fixture blob was `25331e9324182002eb0333a5c56034e684e3c018`, and the only pre-existing worktree change was the unstaged `requirements.txt` proposal with SHA-256 `EF3095DBC1A1657CCB5D5FCBA4D2DF245DD5EC9D04B6C2D237823FDFE472734F`.

This phase corrected the launcher import path, improved safe attribution for `ModuleNotFoundError`, and added `--preflight-only`. No real browser E2E workflow was run.

## Import diagnosis

Phase 6.18E stopped at `python_imports` with `preflight_unexpected` / `ModuleNotFoundError`. That report did not retain the missing module name, so the exact historical exception cannot be recovered from it.

A bounded diagnostic with the existing Python 3.10.11 interpreter simulated direct-script path semantics: `scripts` was the script search directory and the repository root was absent from `sys.path`. It attempted the four imports in their original order and captured only stage, result, exception class, and a regex-validated `ModuleNotFoundError.name`:

| Import stage | Result | Missing module |
|---|---|---|
| `backend_config` | `ModuleNotFoundError` | `backend` |
| `document_manifest` | `ModuleNotFoundError` | `backend` |
| `run_plan` | `ModuleNotFoundError` | `backend` |
| `notebooks_config` | `ModuleNotFoundError` | `notebooks` |

The repository root was absent throughout the simulated direct-script import attempt. This confirms the repository-root path hypothesis under the reproduced conditions and identifies `backend` as the first failing import there. Because 6.18E did not capture its module name, this does not prove the exact `name` value from that historical invocation.

The launcher now resolves its existing `ROOT`, removes equivalent root entries from `sys.path`, and inserts the resolved root at index zero before the configuration imports. This intentionally gives the checked-out repository packages precedence and does not depend on `PYTHONPATH`. No package or environment changes were made.

For future preflight failures, `ModuleNotFoundError.name` is included in progress and failure records only when it matches `^[A-Za-z_][A-Za-z0-9_.]*$`; otherwise the value is `module_identifier_redacted`. Raw messages and tracebacks remain excluded. Existing `EVIDENCE_CAPTURE_FAILED` handling is unchanged.

## Preflight-only behavior

`--preflight-only` runs the existing preparation stages, checks the ten-minute deadline and 1 GiB task-workspace budget after each stage, and writes a durable `launcher-summary.json`. It returns before the ordinary service-start path. The default invocation still calls the existing full execution path.

The summary uses `PREFLIGHT_READY_NO_BROWSER`; it records completed stages, Edge file discovery, fixture/configuration preparation, the prepared command, resource samples, and zero managed processes. It does not claim browser or E2E success.

## Verification and actual preflight

The focused test module was run once:

```text
.\rag-env\Scripts\python.exe -m pytest -p no:cacheprovider tests/unit/test_authenticated_browser_launcher.py
30 passed in 7.01s; exit code 0
```

The tests cover the reproduced scripts-only import lookup, root insertion and deduplication, imports without `PYTHONPATH`, safe/unsafe module identifiers, the evidence-write failure contract, preflight-only success/failure, the default CLI path, and the existing Edge discovery regressions. Python AST parsing of the launcher and test module succeeded. `git diff --check` passed.

Exactly one actual preflight-only invocation ran:

```powershell
.\rag-env\Scripts\python.exe scripts\authenticated_browser_e2e.py --preflight-only
```

The launcher exited **0** in **2.797 seconds** (outer synchronous capture: 3.251 seconds). All 14 recorded stages succeeded: plan construction, workspace layout, task budget, repository identity, token generation, requirements integrity, Playwright dependency verification, Edge discovery, loopback-port selection/ownership, Python imports, synthetic fixture creation, configuration/manifest generation, service-environment construction, and subprocess preparation.

The actual environment discovered installed Edge Stable from the approved `program_files_x86` location, file version `156.0.4314.58`; Edge was not launched. The four-import stage succeeded. Synthetic PDF fixtures and both configurations/manifests were prepared. The launcher prepared its intended test command but did not execute it.

The durable summary records `PREFLIGHT_READY_NO_BROWSER`, 11,061 sampled task-workspace bytes (peak sample 11,061), zero HTTP requests, zero managed process starts, no service-start attempt, and no browser-launch attempt. A post-run process check found zero matching launcher, Playwright, Vite, or backend processes. Control flow returns before managed process creation; no service listener, Chroma operation, model operation, job, or index build occurred. The workflow scenarios and all browser assertions remain **not run**.

## Preservation

The research baseline passed before and after the changes: `Baseline verified: 9 research files unchanged`. The requirements proposal retained its expected SHA-256 and remained unstaged. The current `.rustup` and `.cargo` trees were inventoried and compared against the Phase 6.17H post-provisioning manifests by relative path, size, modification time, mode, and SHA-256:

| Tree | Files | Bytes | Comparison |
|---|---:|---:|---|
| Default `.rustup` | 133 | 587,883,039 | Exact match; tree SHA-256 `77d00626b63f77a829dfb2ed8e962182717b01c5d5464a273ab88f5a5ca49de5` |
| Default `.cargo` | 4,224 | 344,573,568 | Exact match; tree SHA-256 `88a1c59501b4740bd7c58ff119e1c5a8fd3e4d77297ef2a25e5e6a8a72c95a46` |

All 19 selected Rust/protoc files in the Phase 6.17I verification set still match their recorded SHA-256 values. No Rustup, Cargo, compiler, installer, or dependency operation was run in this phase. The pinned Chroma checkout remains at commit `ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda`, tree `8d683102c76eea17c4088f1c5de46a1c7cd1239a`, with a clean worktree and unchanged `Cargo.lock` SHA-256 `D0B2ECF987173344EA347D15D8DF203A3D30D9C237354A8B163AF40F448828D2`. The diagnostic patch remains SHA-256 `276D25F5BA771A33BC4902A904B8C6E2E31C632D02F5BE27FAD44B28CE969920`.

The retained Phase 6.17H inventory helper exited with `OSError: [Errno 9] Bad file descriptor` when it attempted to `fsync` a read-only descriptor, after writing its temporary inventory. That temporary JSON was parsed and its complete per-file records matched both 6.17H trees. The comparison outputs were separately flushed and hashed; the helper failure and its limitation are retained in the external evidence. No original Rust or Cargo files were modified.

## Evidence and outcome

Detailed files remain outside the repository in task-owned workspaces identified as:

- Import diagnostic: `rag-pdf-618f-import-diagnostic-zpnmo8be`
- Invocation capture and preservation comparison: `rag-pdf-618f-preflight-0df99f451f934c02a8ef313573dd9614`
- Launcher evidence workspace: `rag-pdf-618a-e2e-_gbbfh5j`

The final external evidence manifest is `evidence-manifest-final.json`, SHA-256 `D1D63E6F76869C4E7038E1764E70EED4E5B62F8495D346736B7F52BACE52CB55`. It inventories 16 retained files (1,011,119 bytes, excluding the manifest itself), including the sanitized import diagnostic, launcher start/progress/summary, invocation streams, current default-home snapshot and comparisons. Key hashes: import diagnostic `6D25F8D0E9ED0DEAB754A658B7A15E16565AAC50DBD4C47FCAB31FF1B248B9D4`; launcher summary `BF245EA64AFFECC2A21FFBEE4927492E4734A1D1D699A294F7174ED3A2304108`; launcher progress `E4F643C06CBE48F089AE7DF4DDF450E93D515C88EE111F4B7E5DE563A2B0DC7E`; current default-home comparison `5F5ED8BE2E4048B0B1631035D964AA88D421DC5CDC636B8AB9559BC3A431E020`; isolated-tool comparison `4BA2386B04670213977264BAABE5DDFF8FE7E5B506E7CFA235E555D6EA3B41B3`.

The result is `PREFLIGHT_READY_NO_BROWSER`. It establishes that the corrected launcher can prepare its current local prerequisites and retain a distinct no-browser summary. It does not establish that Edge can launch under Playwright or that any authenticated browser scenario works.

## Next decision

Consider one separately authorized Phase 6.18G real-browser attempt using the existing three sequential scenarios. Do not infer browser success from this preflight result.

Phase 6.15D remains C — native build deferred. HNSW and publisher incidents remain unresolved. Dependency adoption remains deferred, Chroma migration remains unauthorized, and unattended deployment remains **NO-GO**.
