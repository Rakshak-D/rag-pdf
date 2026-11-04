# Phase 6.17L — Bounded online Cargo dependency assessment

**Date:** 2026-11-04
**Classification:** `NETWORK_CONTROL_BLOCKED`

## Result

The single online Cargo metadata attempt did not start. The required fail-closed network route, destination allowlist, byte metering, and child-process bypass prevention could not be established with available, permitted host controls. Proxy settings alone would not satisfy the stated gate. I stopped before Cargo and did not acquire dependencies.

This is a blocked execution, not a Cargo resolution failure. No dependency graph was produced and no conclusion about online availability of either locked Git revision follows from this phase.

## Starting identities and preservation

- RAG-PDF branch: `production-evaluation-workbench`
- Starting local HEAD: `38f8ecfd981c7d9dab61783f41e1bcaa95610ecb`
- Remote feature HEAD at the pre-commit check: `38f8ecfd981c7d9dab61783f41e1bcaa95610ecb`
- Protected `main`: `7e9522618296af6a7f25652cc8161009d9a92685` (local and remote)
- Protected fixture blob: `25331e9324182002eb0333a5c56034e684e3c018`
- Candidate `requirements.txt` SHA-256: `EF3095DBC1A1657CCB5D5FCBA4D2DF245DD5EC9D04B6C2D237823FDFE472734F`
- Chroma source commit/tree: `ad34d58038fc1e9b6cf73bab8e1df4a5e98aaeda` / `8d683102c76eea17c4088f1c5de46a1c7cd1239a`
- Chroma source worktree: clean; 2,290 tracked index entries, 730 materialized and hashed, 1,560 sparse-excluded entries recorded by pinned Git blob identity.
- `Cargo.lock` SHA-256: `D0B2ECF987173344EA347D15D8DF203A3D30D9C237354A8B163AF40F448828D2`
- Proposed diagnostic patch SHA-256: `276D25F5BA771A33BC4902A904B8C6E2E31C632D02F5BE27FAD44B28CE969920`

The preflight state matched the expected repository state: `requirements.txt` was the only modified file, unstaged; nothing was staged or untracked. The preflight and final research-baseline verifier runs both reported `Baseline verified: 9 research files unchanged`.

## Durable inventories

Before any network operation, the task created and read back deterministic per-file manifests outside the repository. The manifests include normalized relative paths, sizes, SHA-256 hashes, modification times, modes/attributes, directory metadata, and separate reparse, unreadable, and unstable lists. No reparse points, unreadable files, or unstable files were found. The manifest and verification records were persisted before network use.

| Inventory | Files | Bytes | Manifest file SHA-256 | Canonical manifest SHA-256 |
|---|---:|---:|---|---|
| Default `%USERPROFILE%\.rustup` | 133 | 587,883,039 | `D231170384E3F1795CAA3E018AE81B153B07CD91FE7C80A932F6A0B81B942EE6` | `3476424F4899B01480E717A800204BE27E16EEF382ED5FCFF3BDD15FD430AB46` |
| Default `%USERPROFILE%\.cargo` | 4,224 | 344,573,568 | `4A7A420AB49BF4377011C7DC1DDE6AB4C5689FB65D92EB55A9B91049D992B36D` | `76070E1F66CBB1A00DCD576CE1906A3F0AF309D531D3DED5AF91ABF1A34523E1` |
| Isolated 6.17H Rustup home | 164 | 973,426,839 | `E5FC9A182045935039F968FE12725C32C488BE79C32886ACDAE9C28B62182B7D` | `515ACB51E0E871FD6DB2BC5D8E51151DA2552D39D6321AD507FAF24153225EDF` |
| Isolated Rust 1.92.0 toolchain | 162 | 973,426,673 | `F7EE07D1DA7B279B399CDDEBDB45F7A3442010378CAD32CA6C3A46FD1BCC2293` | `2ADB618D4651CEF9AF74B3A9A0961687730E04D1EFEBD3B653EF418AC2152E1B` |
| Isolated Protoc 36.2 | 22 | 13,411,351 | `0FD176992C6125759F144C12DBC7BA9EBDA35B9B4FD6D169BB5C1F11D661D3C3` | `531EB60CCF514828F84D6E3C5A934A846529AD37B3B98AB31D03A27D49B20510` |
| Pinned Chroma tracked checkout inventory | 2,290 entries | — | `E960282358FC4A23AE196B9A141E7F95B2FB5C6BCA7A6586F511D4DEBF804CE8` | `468A4FD1BF7AC274FE4A3ACF2C1143807A5098DCCC580ED41CE123039B1D1BCF` |

The preflight verification record SHA-256 is `297F9011951B595576084784818A5C7D6CB9B0A542ABB6E6A6E328EED8F0E306`. Its recorded free C: space was 44,265,381,888 bytes.

Postflight manifests matched the preflight file-by-file for every default/isolated tool root: no added, missing, content-changed, or metadata-changed files. All five file inventories had zero unreadable, unstable, or reparse entries. The tracked Chroma manifest also matched exactly, including commit, tree, index entries, sparse exclusions, and clean status. The postflight verification record SHA-256 is `0F51D5AA23CE92ABCE9588E0A003650E6533AF0AE9128F5FC90061C4966F6C0F`.

## Why the network gate failed

Read-only host inspection found no available, permitted egress-isolation mechanism that could both force Cargo and its Git children through a metered allowlist proxy and prevent direct connections:

- Windows Sandbox, Podman, `bwrap`, `firejail`, and WinDivert were unavailable.
- Docker CLI was present, but the Docker service was stopped and its engine pipes were absent.
- `wsl.exe` was present, but no verified task-local network boundary was available; WSL was not invoked.
- The current token was not an administrator. Although the firewall-rule command was available, changing persistent firewall/system settings was outside this authorization.
- HTTP/HTTPS/ALL proxy environment variables were not set. Setting them would not by itself prevent direct egress.

The observations are retained in `network-logs/containment-availability.json` (SHA-256 `F38CA3915AC29592B51D99B868BB2EEBE86CCF0E569855821D56B2D5F8E42074`). No proxy-only substitute was used. The requested synthetic approved-host, denied-host, byte-cap, bypass, and process-tree tests were not run because there was no candidate enforceable route; running proxy-only tests could not satisfy the mandatory no-bypass condition.

## Execution and resource accounting

- Cargo processes: **0**; Cargo commands: **0**. The authorized `cargo metadata --locked --format-version 1 --filter-platform x86_64-pc-windows-msvc` command was not invoked; exit code is not applicable.
- Cargo dependency acquisition traffic: **0 bytes**. No Cargo/Git dependency subprocess ran, no network archive was requested, and no registry/Git source was copied into the task Cargo home. No dependency graph JSON exists.
- The report’s repository remote-head check and later documentation push are separate Git control operations, not Cargo dependency acquisition; their transport bytes were not measured as dependency traffic.
- Task workspace at the postflight measurement: 17 files / 8,468,222 bytes; task `cargo-home` contained 0 files and task `target` contained 0 files. C: free space was 44,172,140,544 bytes at that measurement. No installation or extraction occurred.
- No Cargo watchdog or network byte meter was claimed as tested. There was no Cargo process to time, meter, or terminate.

## Reconciliation of the 6.17J Rustup discrepancy

The retained 6.17J preflight record says 164 files / 973,426,839 bytes with digest `36BC88B7EF0F38272FC4FE6B82F58A2B48647EAA47D32D9B72458CA15FA02DA4`; the 6.17J postflight record says 133 files / 587,883,039 bytes with digest `9F9C2CAD79CF5DE982D35C6BA93CEFB489E09E038325D3683C37312B835A9DFC`. The preflight record did not retain the resolved directory path or a per-file inventory.

Applying the retained 6.17J path/size/content digest method to the current 6.17H isolated Rustup home reproduces the 6.17J preflight digest exactly; applying it to the current default Rustup home reproduces the 6.17J postflight digest exactly. The comparison record is `inventory-before/historical-root-reconciliation.json` (SHA-256 `35A5ADCBFF9F8F25E78D7AB037CB09317FC6077544F193B1D929E0514C9D7F15`). This supports a root-selection or labeling discrepancy as an explanation. It does not establish the directory actually measured in 6.17J or identify the responsible code/process. Preserve Phase 6.17K’s `EVIDENCE_INSUFFICIENT` classification; do not attribute the discrepancy to Cargo.

## Dependency result and limitations

No registry or Git dependency was acquired. No two-crate dependency graph was resolved. The pinned revisions remain:

- `chroma-core/hnswlib` `312991cf9e640d5ccab879906a51e7612ce6fcf1`
- `yoshidan/google-cloud-rust` `b929a5ec83120c0d8788ef049ff77779a577794b`

Phase 6.17J’s one offline metadata command exited 101 because the locked `hnswlib` source was missing from its isolated cache. That is a separate historical offline-cache observation; it does not show that the revision is unavailable online or incompatible. Phase 6.17L provides no online availability evidence.

Historical classifications are preserved: 6.17H `PARTIAL_PROVISIONING`; 6.17J `ENVIRONMENT_OR_INTEGRITY_BLOCKED`; 6.17K `EVIDENCE_INSUFFICIENT`. No compiler, build script, proc macro, protobuf generator, test, dependency installer, or native code was run in 6.17L. The Phase 6.15D native-build deferral remains C.

## Next decision

Before another Cargo attempt, separately approve and provide an enforceable disposable Windows network boundary (for example, a dedicated isolated VM or a reviewed host-level egress control with metering and process-tree kill enforcement). Verify the boundary locally with synthetic tests first. If that control cannot be provided without persistent system changes, pause the native dependency-resolution route rather than retrying Cargo with proxy settings alone.

HNSW and publisher incidents remain unresolved; dependency adoption remains deferred; Chroma migration remains unauthorized; unattended deployment remains **NO-GO**.
