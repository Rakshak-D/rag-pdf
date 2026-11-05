# Phase 6.18H — Windows listener ownership diagnostic

**Classification:** `LISTENER_BEHAVIOR_MISMATCH`
**Date:** 2026-11-05

## Starting checkpoint and prior failure

The work started on `production-evaluation-workbench` at local and remote HEAD `5a479a714a8396b86c18a80edfdac936e4e432d8`. Protected `main` was `7e9522618296af6a7f25652cc8161009d9a92685`; the protected fixture blob was `25331e9324182002eb0333a5c56034e684e3c018`. The only starting worktree change was unstaged `requirements.txt`, SHA-256 `EF3095DBC1A1657CCB5D5FCBA4D2DF245DD5EC9D04B6C2D237823FDFE472734F`. The nine-file research baseline passed before edits.

Phase 6.18G completed all launcher preparation stages and started both backend processes. The ordinary health endpoint returned the expected healthy response before the launcher raised `ordinary_backend_listener_count_invalid`. That failure meant the parsed matching listener count was not exactly one; the count, candidate addresses and PIDs were not retained. The prior evidence therefore cannot establish whether that run saw zero or multiple matches.

## Listener-query assumptions

The prior source invoked `netstat -ano -p tcp`, split output into whitespace-delimited fields, retained `TCP` rows whose state token was `LISTENING`, and matched the local endpoint by its text after the final colon. Ownership then required exactly one row, exact local address `127.0.0.1:<port>`, and the expected child PID.

The source establishes those parsing and validation choices. It does not establish what exact row format or state token the 6.18G machine returned. IPv4/IPv6 formatting, localized state text, duplicate rows, foreign listeners, query-output truncation, and timing between HTTP readiness and the subsequent snapshot require actual observations. The 6.18G report retained none of the listener rows.

## Changes

`scripts/authenticated_browser_e2e.py` now extracts a small pure parser for `netstat` TCP rows. It parses numeric IPv4 and bracketed or unbracketed IPv6 endpoints, identifies address family, checks numeric ports and PIDs, and marks malformed or unexpected TCP rows and empty/truncated output as incomplete. It does not deduplicate rows. Only rows for the selected numeric local port count as matching listeners; foreign endpoints are never retained.

The parser keeps at most 32 matching entries while maintaining a separate total count and explicit truncation/completeness flags. The input considered by the parser is capped at 2 MiB; if the captured output exceeds that cap, the observation is incomplete and ownership validation fails closed. The full `netstat` output and stderr are not written to evidence.

The state check still recognizes `LISTENING` by that exact token and skips other five-column TCP states as non-listening. It does not retain or classify the skipped state token. This is a remaining observability limitation: an unrecognized or localized state token could be skipped without making the snapshot incomplete. The probe did not retain raw output, so there is no evidence that this occurred. A follow-up may add a verified state-token allowlist and a sanitized unknown-state count.

Before each managed listener ownership result is accepted or rejected, the launcher appends a sanitized record to `evidence/listener-observations.jsonl`. The record contains the timestamp, safe service label, selected port, expected PID, query status/exit code, output byte count, total match count, bounded local addresses/PIDs/families, validation outcome, and truncation/incomplete flags. The existing JSONL writer flushes and fsyncs. If writing fails, validation stops with `listener_evidence_capture_failed`.

The acceptance rule remains fail-closed: exactly one complete matching listener must bind to `127.0.0.1:<port>` and belong to the expected child PID. Count failures remain distinct from ownership/bind failures; unavailable, failed, or malformed queries also fail closed with distinct query outcomes. No listener is accepted because another row happens to match.

## Focused verification

The focused module ran once:

```text
.\rag-env\Scripts\python.exe -m pytest -p no:cacheprovider tests/unit/test_authenticated_browser_launcher.py
47 passed in 6.88s; exit code 0
```

The added cases exercise zero and one match, expected and unexpected PIDs, multiple and duplicate rows, IPv4 wildcard, IPv6 loopback/wildcard, non-listening rows, malformed PID/address/shape, bounded evidence, secret exclusion, and fail-closed evidence-write errors. Existing launcher and Edge-discovery tests also passed. AST validation passed for both modified Python files. `git diff --check` passed.

## Single real loopback-socket probe

One test-owned TCP socket was bound to `127.0.0.1` on an OS-selected port and listened while the current Python process queried the existing `netstat` helper. The same snapshot was passed into the actual ownership decision, avoiding a second query while the socket was open. After the socket closed, one cleanup query verified that the listener was gone. No HTTP server or application backend was started.

| Observation | Result |
|---|---|
| Selected port / expected process PID | `64378` / `28024` |
| Open-socket query | `success`, exit code `0`, 5,583 stdout bytes |
| Parsed matching listener count | `0` |
| Parsed local addresses / PIDs / families | Empty / empty / empty |
| Parser completeness | Complete; zero malformed TCP rows; no truncation |
| Ownership decision | Rejected as `loopback_probe_listener_count_invalid` |
| After close | Query succeeded; zero matches; listener-gone check passed |

The observed zero matches while the test-owned socket was bound/listening is a listener-query behavior mismatch. It does **not** establish whether `netstat` omitted the socket, the returned row had a format/state the parser did not recognize, or another query/runtime condition applied. Raw `netstat` output was intentionally not retained, so no specific parsing defect or 6.18G cause is proven. The optional `Get-NetTCPConnection` corroboration was not used. The ownership rule was not relaxed.

## Process and repository preservation

The probe process and its short `netstat` query exited. The socket was closed in `finally`, and the post-close query found no matching listener. No Edge, Playwright, Vite, ordinary backend, bootstrap backend, browser workflow, external request, Chroma/model operation, Cargo/Rustup operation, or package installation ran.

The post-probe verifier reported `Baseline verified: 9 research files unchanged`. The requirements proposal remains unstaged at its expected SHA-256. `git diff --check` passed; no files were staged during investigation.

## External evidence

Task-owned evidence is retained outside the repository in workspace `rag-pdf-618h-listener-5483dceb2c02462bb4c0bee781797341`.

| File | Bytes | SHA-256 |
|---|---:|---|
| `loopback-probe-result.json` | 1,244 | `D5DCA9614A50714823FA9C63C334512E576D6BE5A2A800D1582957CBFF0113D3` |
| `listener-observations.jsonl` | 505 | `698B8054C9551A6F2DA0D24A230C10F342C0D2B503F6DC5CC33F93235D064B1A` |
| `loopback_socket_probe.py` | 2,684 | `E1DBE5136F07814FA39FD3C164C345FD4A11B7CC3E2102A61857E2D0AB860B62` |

The modified launcher SHA-256 is `3849A9378FAD1884DF2FC119A685F1D3CA744C4FAAF3A0CBC65C2E5E0F1412BF`; the focused test module SHA-256 is `0E7417A935A60C9C78E4564178C29F263ABDA9A2240686707A37E51A0A7300BC`.

## Decision

Classification is `LISTENER_BEHAVIOR_MISMATCH`. Instrumentation and synthetic regression tests pass, but the single real probe did not observe its own bound listener. The cause remains unresolved, and the 6.18G listener failure must not be attributed to a parser defect based on this evidence.

The next step is a separately authorized, narrowly scoped diagnostic that compares one test-owned socket with a port-filtered Windows listener observation while preserving the same exact-one, loopback-only, expected-PID acceptance rule. Do not repeat the browser E2E attempt until that discrepancy is understood.

Phase 6.15D remains C — native build deferred. HNSW and publisher incidents remain unresolved. Dependency adoption is deferred, Chroma migration remains unauthorized, and unattended deployment remains **NO-GO**.
