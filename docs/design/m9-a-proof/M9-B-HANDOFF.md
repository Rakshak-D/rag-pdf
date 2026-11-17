# M9-B implementation handoff

M9-B is a separate implementation milestone. This handoff records the binding requirements for the approved R5 design; it does not authorize implementation. Start from the then-verified product branch tip, create an isolated feature branch and worktree, and use a separate pull request. Do not include M9-B code in PR #6.

## 1. Citation popovers

- Open a source popover only on citation click or tap, never on hover.
- Place it directly below the paragraph containing the citation without covering the cited sentence.
- Escape closes it. Include an `Open page N` action that opens the real PDF viewer at the cited page.
- Keep keyboard operation and focus behavior accessible.

## 2. Active citation visibility

When the desktop viewer or mobile sheet opens, keep the initiating citation visible above it and turn its ring lime. The visible passage must match that exact citation. Test all three sample-question flows against their original source passages.

## 3. Production PDF.js highlight accuracy

Poppler-derived proof coordinates do not verify production PDF.js conversion. Test the real NASA PDF at two zoom levels for the Link caption, stream-flow evidence, and John Klein water evidence. Require at least 90% overlap between each rendered highlight and its expected text box, no highlight on another text line, and the correct page and source text. Verify scaling, offsets, and rotation where applicable. If a text region cannot be located reliably, show the page with `Passage on page N` and omit the highlight; never draw an uncertain or incorrect box.

## 4. Citation spacing

Keep one ordinary word space between a citation chip and the following sentence. Avoid a conspicuous gap.

## 5. First-run alignment

At 1440px, vertically center the suggested questions, document pill, and composer group against the sample page's center line. Preserve the approved document-first composition.

## 6. Viewer close control

Show the close-control focus ring only under `:focus-visible`. Use the established control corner radius, retain keyboard access, and restore focus to the control that opened the viewer.

## 7. Real status behavior

Do not show success or ready text simply because the interface loaded. When Ollama is unreachable, show the approved failure message; `Check again` must call a real backend health check and report its actual result. Keep retrieval and document evidence accessible where the backend can genuinely provide them without generation.

## 8. Preserve the approved visual design

Keep the serif answers, lime document evidence, warm-paper PDF, floating toolbar, mobile 30%, 55%, and 92% snaps, and at least 36px separation between the user bubble and viewer in the approved desktop composition. Retrieved passages and match information belong inside the PDF viewer. Keep the no-tagline and no-`Model ready` rules. Use responsive tests rather than hard-coded desktop coordinates.

## 9. Provisional brand

Retain the working title `RAG-PDF` and marked-page A as the provisional app icon. Do not select a permanent logo or rename the product.

## 10. CI is the first M9-B task

Before substantive product UI integration, establish product-branch pull-request CI with bounded, reproducible checks:

- An actual lint command or a documented static-analysis equivalent.
- Backend and frontend tests.
- Frontend typecheck.
- Production frontend build.

Reuse existing scripts where practical. The current frontend package has `typecheck`, `test`, `test:e2e`, and `build`, but no `lint` script; verify scripts again at implementation time. Baseline CI must not require a cloud model, model download, external service, or resource-gate bypass. Make the resulting status a visible merge requirement only through supported branch protection or rulesets and owner-authorized settings.

The existing `Linux authenticated browser acceptance` workflow is push-triggered only for `production-evaluation-workbench` and selected paths. It does not provide product-branch PR CI. PR #6 currently has no reported checks, and the product branch has no verified required-status-check rule. Do not infer CI coverage from that unrelated workflow.

There is a sequencing decision for the owner: either authorize a small pre-merge product CI change separately, or explicitly grant a one-time exception to merge the static M9-A proof after equivalent manual validation and establish permanent CI at the start of M9-B. Do not choose either route without approval. Keep M9-B's CI work in its own feature branch and PR.
