# M9-C0 Product Acceptance and Readiness Audit

**Audit basis:** product branch commit `0d4c7122e421f340b56ece610b6512accada809d`
**Evidence reviewed:** merged PRs #9 and #10, final product CI run [44285328483](https://github.com/Rakshak-D/rag-pdf/actions/runs/44285328483), current product source and tests, and the operations/architecture documents linked below.
**Scope:** read-only product and operations audit, except for the factual documentation corrections in this PR. No model, index build, research run, deployment or merge was performed.

At preflight, product06 matched the required SHA; protected `main` was
`7e9522618296af6a7f25652cc8161009d9a92685`. PR #9's reviewed head
`a15c384a7ef230920f8ba8e4d67581310310e6c0` merged as
`855f0837ced9ccfb02c6a4314b38cb047f836ade`. PR #10's reviewed head
`b7d096b721bedb6b86434a6dc377a39bf4561204` merged as the required product
head `0d4c7122e421f340b56ece610b6512accada809d`. No additional open PR targeting
product06 was found during preflight. The pre-existing stale product checkout
was left untouched.

## Decision summary

| Decision | Result | Basis |
|---|---|---|
| Controlled local UI demonstration | **PARTIAL** | The final Ubuntu run opened the genuine NASA PDF through the isolated local document service and exercised the production PDF.js viewer. It did not run the full operator launcher or the real Ask/generation path on this commit. |
| Genuine local document workflow | **NOT_TESTED** | No current-commit evidence covers a user import, fresh index build, explicit index selection, live retrieval and model answer as one real workflow. The browser answer flows use deterministic API fixtures. |
| Operational reliability | **PARTIAL** | Current CI covers isolated service readiness, PDF identity/manifest failures, viewer behavior and cleanup. It does not establish restart/recovery behavior for the full local launcher, resource behavior under model load, or persistent user-state recovery. |
| Local beta suitability | **BLOCKED** | Complete the opt-in real-RAG acceptance and operational restart/resource checks before calling the product beta-ready. |
| Public deployment suitability | **BLOCKED** | The product is verified only as a local-first application; current evidence does not establish a supported public deployment or its security controls. A broader security/privacy review is required. |

“PASS” below means a specific bounded check passed; it does not imply end-to-end product readiness. Test fixtures, unit tests, historical demonstrations and current real-service browser checks are identified separately.

## Feature acceptance matrix

Evidence labels: **RSC** = current real-service browser CI; **PB** = production browser test using deterministic response fixtures; **UT/IT** = unit or integration test; **DOC** = current implementation/operations documentation; **HIST** = dated evidence from another run/commit; **NT** = not verified in this audit.

| User journey | Result | Evidence and limit |
|---|---|---|
| 1. Launch local application | **PARTIAL** | **DOC:** [`start_local_app.py`](../../scripts/start_local_app.py) performs loopback startup and service health checks. **RSC:** CI starts an isolated bootstrap API, gateway and Vite viewer stack. **NT:** the complete operator launcher was not run locally on this commit; the audit checkout lacks frontend dependencies and the available Python lacks FastAPI. |
| 2. General Chat without documents | **PARTIAL** | **UT/IT:** service and API tests use a fake Ollama client and assert General Chat does not invoke document retrieval. **HIST:** [`DEMO.md`](../DEMO.md) records a real local model answer from 2026-11-08. **NT:** no real General Chat request was made on this exact product commit. |
| 3. Ollama unavailable / model missing | **PARTIAL** | **PB/UT:** UI and backend contract tests cover unavailable and missing-model states using controlled responses/fakes. **NT:** no live Ollama failure or installed-model-missing check was run. |
| 4. Model discovery and Settings | **PARTIAL** | **DOC/UT:** the UI requests the local model catalog and Settings stores the chosen local model preference; tests exercise response handling. **NT:** no current live catalog discovery was performed. |
| 5. Add a PDF to Library | **PARTIAL** | **UT/PB:** upload validation and recovery behavior have deterministic coverage. **RSC:** the real-service job inventories a controlled copy of the tracked NASA PDF; it does not exercise browser upload. **HIST:** [`DEMO.md`](../DEMO.md) records a prior Orbit PDF import. |
| 6. Explicit preparation and index selection | **PARTIAL** | **UT/IT:** run-plan, manifest, index inventory, writer lease and selection contracts are covered in selected tests. **DOC:** import is separate from fresh-only index preparation and explicit selection. **HIST:** the dated demo records prior prepared reports and selection. **NT:** no fresh index was built or selected on this commit. |
| 7. Document Chat submission | **NOT_TESTED** | **PB:** sample question interactions verify UI-to-citation mapping with contract-valid deterministic responses. **NT:** no real Ask request against a prepared index and no model generation were performed on this commit. |
| 8. Retrieval and returned evidence identity | **PARTIAL** | **UT/IT:** BM25, fusion, source/index contracts and response schemas run in current CI; browser fixtures exercise citation identity. **RSC:** the viewer validates actual document and manifest identity. **NT:** no live retrieval result was produced on this commit. |
| 9. Citation popovers and original source PDF | **PASS, scoped** | **PB:** click-only popovers, accessible citation identity, keyboard/focus behavior and source navigation are covered in the browser suite. **RSC:** the real local viewer returns the tracked PDF bytes through the authenticated loopback gateway. The citation is seeded by the test fixture, not produced by live Ask/retrieval. |
| 10. Highlight accuracy and fallback | **PASS, tested cases only** | **PB:** 24 Playwright cases exercise the actual production PDF.js rendering/matching path, including the three NASA passages at two zoom levels, zoom return, supported synthetic layouts/rotation, and no-highlight fallback. The NASA minimum per-line overlap was 90%; observed minima were T1 95.68% / 98.18%, T2 95.58% / 91.76%, and T3 93.65% / 90.45% at the two effective scales; all reported unrelated-line intersections were zero. These results do not establish accuracy for arbitrary PDFs. |
| 11. Session navigation, reload and expired evidence | **PARTIAL** | **UT/PB:** conversation state, navigation and expired-source messaging have test coverage. **NT:** no cross-process persistence/restart acceptance was run; restored evidence is not proof that the source remains current. |
| 12. Mobile, keyboard and focus | **PARTIAL** | **PB:** Chromium covers mobile sheet behavior, citation keyboard activation, Escape and focus restoration, including modeless 30% and modal 55%/92% states. **NT:** no physical-device or screen-reader session was performed. |
| 13. Failure recovery and safe shutdown | **PARTIAL** | **RSC:** the isolated service test covers readiness, source/manifest failures and task-owned process/data cleanup. **UT:** launcher and lifecycle contracts exist. **NT:** no full local launcher restart, crash-recovery or user-owned state recovery was exercised in this audit. |
| 14. Credentials, storage and network isolation | **PARTIAL** | **DOC/UT/RSC:** loopback binding, gateway credential forwarding, isolated temporary paths and source identity checks are implemented/tested; the final service/browser evidence reports no browser credential header and no external runtime origins. This is not a comprehensive security audit; see Security and operations. |

## Current service evidence versus historical RAG evidence

The final product commit’s [Ubuntu workflow run](https://github.com/Rakshak-D/rag-pdf/actions/runs/44285328483) completed all four configured jobs successfully:

- **Backend tests and Python static analysis:** 163 passed, 2 skipped; 10 Ruff comparator tests passed; 177 baseline findings recorded, 0 new and 0 removed. One warning was reported; the summary did not identify a test failure.
- **Frontend tests, typecheck and production build:** 288 Vitest tests passed; typecheck and build passed.
- **NASA PDF.js highlight accuracy:** 24 Playwright tests passed. The six NASA line-geometry cases and three zoom-return checks met the per-line threshold with zero unrelated-line intersections.
- **Real local PDF document viewer:** 2 browser tests passed against the isolated loopback services. They exercised the actual tracked PDF bytes, inventory/document identity and manifest behavior, plus viewer rendering. The service test recorded 4 pages and cited page 2, rejected invalid/stale identities, and did not submit an index build. The browser evidence reported no browser credential header and no external runtime origins.

This current evidence proves the **source viewer boundary**, not the complete document-QA workflow. The test browser uses deterministic answer/citation fixtures. It does not upload through the user-facing Library, build an index, select a real built index, run retrieval, or generate an answer.

The workflow summary reports two skipped backend cases and one warning but does
not state the skip reasons in its summary. They are not counted here as proof
for an unexecuted path.

[`DEMO.md`](../DEMO.md) contains explicitly dated 2026-11-07/08 observations, including an earlier import, index build/selection and local-model activity. The 2026-11-08 note says Document Chat was not rerun during that revalidation; an earlier Gemma answer remains historical. These records are useful operational history but are **not** current-commit acceptance evidence. The runbook also refers to historical machine-specific workspace/state assumptions; refresh and validate those instructions before using them for a presentation. No existing demo state or index was opened or changed for this audit.

## Controlled service check performed for this audit

No additional local service was launched. The audit worktree had no `frontend/node_modules`, and its available Python environment did not have FastAPI. Installing dependencies or borrowing another worktree’s environment would change the audit conditions, so neither was done. No Ollama call, model load/download, index build or persistent-state mutation occurred.

The qualifying current real-service evidence is therefore the final Ubuntu CI job described above, which uses a temporary source copy, temporary state/storage and loopback-only services. It does not exercise `scripts/start_local_app.py` as an operator would, nor does it prove a completed index exists. Keep these limits explicit in any demo claim.

## CI coverage and next priorities

The current workflow is [`product-pr-ci.yml`](../../.github/workflows/product-pr-ci.yml). It runs for every PR and push targeting product06, without path filters; jobs have read-only repository permissions and bounded timeouts. It uses Ubuntu 24.04, Python 3.10.22 and Node 22.23.4. The four check names are listed above.

Backend pytest selects **13 modules**, not the whole backend suite: API schemas, local gateway, BM25, context contracts, fusion, index inventory, writer lease, ingestion/chunking, manifest, model lifecycle, paths/fingerprint, retrieval contracts and run plans. The documented inventory contains 51 additional unit modules and 30 integration modules outside that pytest selection. The full frontend unit suite is included. The two focused browser jobs cover source geometry and the isolated source viewer; broader authenticated end-to-end workflows are not in this gate. The static M9-A proof checker is also excluded.

Priority additions for a future CI review, subject to a clean dependency and determinism check:

1. Evaluate the fake-backed [`test_general_chat.py`](../../tests/unit/test_general_chat.py) and [`test_generation_contracts.py`](../../tests/unit/test_generation_contracts.py) for low-cost coverage of local chat/generation failure contracts.
2. Evaluate [`test_general_chat_api.py`](../../tests/integration/test_general_chat_api.py) for authenticated API behavior with temporary state and a fake Ollama client.
3. Consider selected index-selection API tests only when their generated PDFs, report fixtures and native dependencies remain isolated and reproducible.
4. Keep model/embedding/reranker tests out of baseline CI unless model assets are pre-provisioned without downloads and with an explicit resource budget. Route OS/process tests to dedicated runners; isolate Chroma/native integration and long-running research/evaluation suites separately.

No CI workflow, dependency, lint baseline or branch rule was changed in this audit. The product branch has no verified classic protection rule: the GitHub branch-protection query reported “Branch not protected,” and the visible repository ruleset was for `main`. Owner configuration is still needed if product06 must require these checks before merge.

## Documentation corrections

This PR makes three focused factual updates:

- [`source-viewer.md`](../operations/source-viewer.md) now distinguishes absent trusted coordinates in the Ask response from the viewer’s validated PDF.js text/geometry matching, and documents the no-highlight fallback.
- [`product-pr-ci.md`](../operations/product-pr-ci.md) now describes the two current focused browser jobs and clarifies that they do not expand the 13-module backend pytest selection.
- [`product-documents.md`](../operations/product-documents.md) preserves the original sprint’s scope while linking to the integrated viewer guide.

[`M9-B-HANDOFF.md`](../design/m9-a-proof/M9-B-HANDOFF.md) is a historical implementation handoff. Its CI-first sequencing was fulfilled by the merged CI foundation; it was not rewritten. The dated demo record remains unchanged and should be refreshed separately before reuse.

## Security, resource and operational limits

The reviewed launcher and service documentation bind local services to loopback. The local gateway forwards separate ordinary/bootstrap credentials; the browser receives neither bearer token. The document viewer uses the authenticated source route and verifies document/manifest identity before exposing bytes. The real-service CI evidence confirms its browser request stayed on loopback and did not expose the credential header. PDF.js scripting and active external content paths are disabled in the viewer configuration.

These are targeted checks, **not** a comprehensive security review. This audit did not test hostile same-user processes, arbitrary malicious PDFs beyond the existing bounded/parser tests, filesystem ACLs on an operator machine, browser extensions, credential compromise, or every local network interface/firewall combination. Manual security review remains necessary before distributing the application.

The resource-admission code and its reserves were not modified. Contract tests run, but this audit did not load a model or stress memory, commit, CUDA, concurrency or shutdown behavior under model load. Do not infer model safety or latency from the no-model viewer CI.

## Operator-required opt-in real-RAG acceptance

Before claiming a genuine local Document Chat demonstration, an operator should run a separately approved, disposable acceptance using the supported launcher and isolated directories:

1. Use a dedicated copy of a permitted sample PDF and fresh, disjoint source, index, ordinary-state and bootstrap-state directories. Verify they do not resolve into existing user data or indexes.
2. Confirm the required local model and embedding/reranker assets are already installed. Keep offline mode enabled; do not pull or download models for this check.
3. Record available RAM, system commit and (if applicable) CUDA memory, and confirm the existing admission gate accepts the intended configuration. Stop if it rejects the workload or the operator cannot establish a safe budget.
4. Start the exact reviewed checkout with the supported local launcher. Confirm only loopback listeners and healthy services. Verify the actual PDF inventory and identity.
5. Import the disposable PDF, explicitly preview and submit a fresh-only build using the supported plan/manifest checks, wait for a completed report, then explicitly select and read back the matching index. Do not treat upload or HTTP 202 as indexing success.
6. Submit a real question. Record the actual configured model, response/error, retrieved evidence identity and page. Open the returned source PDF and verify the highlight or the honest `Passage on page N` fallback. Do not describe a single answer as an accuracy evaluation.
7. Confirm runtime requests remain local, stop the owned services through the documented path, verify the owned processes and ports stopped, and clean only the disposable test directories after preserving the required result record.

This is an opt-in operator gate, not a test performed by this audit. Do not use pre-authored browser fixtures as its result.

## Readiness decision

- **Controlled local UI demo — PARTIAL:** demonstrate only the approved interface and source-viewer behavior with a controlled sample. State that the answer fixture is not live generation unless a real local run has separately been completed.
- **Real local document workflow — NOT_TESTED:** no current evidence for import → fresh preparation → selection → retrieval → genuine generation on `0d4c712…`.
- **Operational reliability — PARTIAL:** isolated service/viewer and deterministic recovery contracts pass, but full-launcher restart, crash recovery, persistent-state recovery and model-load behavior remain unverified.
- **Local beta — BLOCKED:** first complete the opt-in workflow, run restart/resource acceptance and resolve branch-protection expectations.
- **Public deployment — BLOCKED:** public exposure is outside the verified local-only operating model; security, privacy and deployment controls have not been established.

There are no production feature changes in this audit. The report does not authorize a merge, deployment, model operation or research change.
