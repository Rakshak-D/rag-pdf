# Phase 6.18D — Windows Edge environment discovery

**Classification:** `EDGE_DISCOVERY_VERIFIED`
**Date:** 2026-11-05

## Starting checkpoint and prior observation

The phase started on `production-evaluation-workbench` at `a0325fc2848a58697993c7c503394292cf6da555`, matching the remote feature branch. Protected `main` was `7e9522618296af6a7f25652cc8161009d9a92685`; the research fixture blob was `25331e9324182002eb0333a5c56034e684e3c018`. The only pre-existing worktree change was unstaged `requirements.txt`, SHA-256 `EF3095DBC1A1657CCB5D5FCBA4D2DF245DD5EC9D04B6C2D237823FDFE472734F`.

Phase 6.18C ran the browser launcher once and stopped at `edge_discovery` with `microsoft_edge_stable_unavailable`. It started no managed child, created no fixtures or listeners, and ran none of the browser scenarios. A subsequent read-only check found installed Edge Stable file metadata version `156.0.4314.58`.

## Correction

The old helper used ordinary dictionary lookups for Windows environment keys. Windows environment names were exposed by this process as uppercase keys, so lookups for mixed-case `ProgramFiles(x86)` and `ProgramFiles` missed the corresponding values.

The launcher now uses a small case-insensitive key lookup. It preserves the existing candidate order—x86 Program Files, Program Files, then local application data—and retains the existing path deduplication, path resolution, file-version lookup, unavailable code, and metadata-error behavior. If multiple case variants for one variable contain different nonempty values, the helper raises the stable code `edge_environment_ambiguous`; it does not include the conflicting values in an error or log. No browser fallback, arbitrary directory search, or browser launch was added.

## Verification

The complete focused test module ran once:

```text
.\rag-env\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp <task-evidence> tests/unit/test_authenticated_browser_launcher.py
```

Result: **exit 0; 20 passed, 0 failed** in 0.38 seconds. This includes all nine pre-existing synthetic tests. New cases cover uppercase, lowercase, and mixed-case x86 Program Files keys; uppercase Program Files; lowercase local application data; x86 precedence; candidate deduplication; deterministic rejection of conflicting case variants; the unchanged missing-Edge code; version and source reporting; and a guard that fails if discovery calls `subprocess.run` or `subprocess.Popen`. Placeholder executables were created only in pytest temporary directories.

AST syntax validation exited 0 for both modified Python files. The launcher source SHA-256 changed from `AC47A737728F937EFD2D00761438A592B4CFD9440E7F4BC445A8AA2DC68D3EC4` to `C500A6A142C7AC015D8DEB84DDE0F017094249E6C3881F9DCF2B9636952579E0`. The test source SHA-256 after the change was `2C5166DBE290D2412ACFDF8CEC0F4AF07AF45645244141CBB2EB7079DA45B00D`.

After the focused tests passed, the corrected `_discover_edge()` function was invoked directly once from the existing Python interpreter, using the current process environment; that invocation exited 0. The call returned `status=found`, discovery source `program_files_x86`, and file version `156.0.4314.58`; its resolved executable exists and is the installed Stable Edge file. The version agrees with the executable's Windows file-version metadata. The exact local path is retained only in the external evidence record.

The direct call imported the launcher module but did not execute the launcher entry point. Discovery checks the candidate file, resolves its path, and reads Windows version metadata. The synthetic no-process test also guards against subprocess launch. **Edge/browser launches: 0.** Launcher-as-script invocations: 0. Playwright, backend, Vite, network, Cargo, and Rustup invocations: 0.

## Preservation and evidence

The nine-file research baseline passed before and after the phase. `requirements.txt` retained its expected SHA-256 and remained unstaged. Only the launcher, its focused unit-test module, and this report are authorized changes. Historical 6.18A, 6.18B, and 6.18C reports were left unchanged.

External evidence is retained under task-owned workspace `rag-pdf-618d-17656870168b4c378ba71878f24fc430`. It contains the pre-run identity and source hash, AST and focused-test output/results, the actual discovery result (including the resolved local path), and a completion record. The completion record SHA-256 is `D68293AA510B75392EE2DEFCCE453FD89C9107D1AB2338DF9119E362EE8A496D`; the actual discovery result SHA-256 is `BACF247957AAD43C61D6231A8847A147AE0F84EBE0DF49FF3DC39F2E05D65856`. No bearer tokens or unrestricted environment values were recorded.

This establishes that the installed Edge executable can be discovered and its version metadata read. It does not prove Edge can launch headlessly, Playwright can connect, or any authenticated browser workflow passes.

## Next decision

`EDGE_DISCOVERY_VERIFIED` supports requesting one separately authorized, bounded Phase 6.18E real-browser attempt with the existing harness. This report grants no such authorization. HNSW and publisher incidents remain unresolved; native build remains deferred; dependency adoption and Chroma migration remain unauthorized; unattended deployment remains **NO-GO**.
