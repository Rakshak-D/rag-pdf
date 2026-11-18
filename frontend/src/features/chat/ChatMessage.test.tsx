import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { AskResponse } from "../../api/types";
import type { Message } from "./conversations";
import { ChatMessage } from "./ChatMessage";

const HASH = "a".repeat(64);
const DOCUMENT_ID = `doc_${"b".repeat(64)}`;

function response(overrides: Partial<AskResponse> = {}): AskResponse {
  return {
    query_id: HASH,
    question: "What does the document say?",
    outcome: "answered",
    answer: "A grounded answer.",
    model_reported_insufficient_evidence: false,
    failure_code: null,
    model_identifier: "local-model",
    index_fingerprint: HASH,
    document_manifest_hash: HASH,
    run_fingerprint: HASH,
    context_fingerprint: HASH,
    selected_build_id: "c".repeat(32),
    selected_report_sha256: HASH,
    selection_revision: 1,
    text_evidence: [
      { label: "T1", document_id: DOCUMENT_ID, source_path: "sample.pdf", page_number: 2,
        passage: "A concise text source quotation.", record_id: "chunk-1", truncated: false },
      { label: "T2", document_id: DOCUMENT_ID, source_path: "sample.pdf", page_number: null,
        passage: "A quotation with no identified page.", record_id: "chunk-2", truncated: false },
    ],
    image_evidence: [
      { label: "I1", document_id: DOCUMENT_ID, source_path: "sample.pdf", page_number: 5,
        caption: "A source figure caption.", record_id: "image-1", image_reference: null, content_type: null, size_bytes: null },
    ],
    referenced_citations: ["T1", "T2", "I1"],
    warnings: [],
    ...overrides,
  };
}

function message(id: string, text: string, document: AskResponse): Message {
  return { id, role: "assistant", mode: "document", text, document };
}

function renderMessage(value: Message) {
  return render(<ChatMessage
    message={value}
    selectedFingerprint={null}
    imageUrls={{}}
    onOpenEvidence={() => undefined}
    onOpenDocument={() => undefined}
    onCopy={() => undefined}
  />);
}

afterEach(() => cleanup());

describe("ChatMessage citations", () => {
  it("names text and figure citations from their actual evidence and exposes the active source region", async () => {
    const user = userEvent.setup();
    renderMessage(message("answer-one", "Text [T1], then figure [I1].", response()));

    const textCitation = screen.getByRole("button", { name: "Text source T1, page 2" });
    const figureCitation = screen.getByRole("button", { name: "Figure source I1, page 5" });
    expect(textCitation).toHaveTextContent("[T1]");
    expect(figureCitation).toHaveTextContent("[I1]");
    expect(textCitation).toHaveAttribute("aria-expanded", "false");

    await user.click(textCitation);
    expect(textCitation).toHaveAttribute("aria-expanded", "true");
    const textRegion = screen.getByRole("region", { name: "Source T1" });
    expect(textCitation).toHaveAttribute("aria-controls", textRegion.id);
    expect(within(textRegion).getByText("A concise text source quotation.")).toBeInTheDocument();

    await user.click(figureCitation);
    expect(textCitation).toHaveAttribute("aria-expanded", "false");
    expect(figureCitation).toHaveAttribute("aria-expanded", "true");
    const figureRegion = screen.getByRole("region", { name: "Source I1" });
    expect(figureCitation).toHaveAttribute("aria-controls", figureRegion.id);
    expect(within(figureRegion).getByText("A source figure caption.")).toBeInTheDocument();
    expect(within(figureRegion).getByRole("button", { name: "Open page 5" })).toBeInTheDocument();
    expect(within(figureRegion).queryByText("A concise text source quotation.")).not.toBeInTheDocument();
  });

  it("announces an unidentified page without exposing the passage in the citation name", async () => {
    const user = userEvent.setup();
    renderMessage(message("answer-two", "Unpaged text [T2].", response()));

    const citation = screen.getByRole("button", { name: "Text source T2, page not identified" });
    expect(citation).toHaveTextContent("[T2]");
    expect(citation.getAttribute("aria-label")).not.toContain("A quotation with no identified page.");

    await user.click(citation);
    const region = screen.getByRole("region", { name: "Source T2" });
    expect(within(region).getAllByText("Page unavailable")).toHaveLength(1);
    expect(within(region).queryByRole("button", { name: /Open page/ })).not.toBeInTheDocument();
  });

  it("keeps reused citation labels scoped to the answer that owns the evidence", async () => {
    const user = userEvent.setup();
    const first = message("answer-first", "First claim [T1].", response());
    const second = message("answer-second", "Second claim [T1].", response({
      text_evidence: [{ label: "T1", document_id: DOCUMENT_ID, source_path: "sample.pdf", page_number: 3,
        passage: "The second answer has different evidence.", record_id: "chunk-second", truncated: false }],
      referenced_citations: ["T1"],
      image_evidence: [],
    }));
    render(<>
      <ChatMessage message={first} selectedFingerprint={null} imageUrls={{}} onOpenEvidence={() => undefined} onOpenDocument={() => undefined} onCopy={() => undefined} />
      <ChatMessage message={second} selectedFingerprint={null} imageUrls={{}} onOpenEvidence={() => undefined} onOpenDocument={() => undefined} onCopy={() => undefined} />
    </>);

    const citations = screen.getAllByRole("button", { name: "Text source T1, page 2" });
    const secondCitation = screen.getByRole("button", { name: "Text source T1, page 3" });
    expect(citations).toHaveLength(1);
    await user.click(secondCitation);

    const region = screen.getByRole("region", { name: "Source T1" });
    expect(region).toHaveAttribute("id", "citation-popover-answer-second");
    expect(within(region).getByText("The second answer has different evidence.")).toBeInTheDocument();
    expect(citations[0]).toHaveAttribute("aria-expanded", "false");
    expect(secondCitation).toHaveAttribute("aria-expanded", "true");
  });

  it("tracks repeated citations by occurrence and restores the initiating occurrence", async () => {
    const user = userEvent.setup();
    const openDocument = vi.fn();
    const answer = message("answer-repeated", "Evidence supports this [T1], and the conclusion also cites it [T1].", response());
    render(<ChatMessage
      message={answer}
      selectedFingerprint={null}
      imageUrls={{}}
      onOpenEvidence={() => undefined}
      onOpenDocument={openDocument}
      onCopy={() => undefined}
    />);

    const citations = screen.getAllByRole("button", { name: "Text source T1, page 2" });
    expect(citations).toHaveLength(2);
    const first = citations[0]!;
    const second = citations[1]!;
    expect(first).toHaveTextContent("[T1]");
    expect(second).toHaveTextContent("[T1]");
    expect(first.id).not.toBe(second.id);

    await user.click(first);
    expect(first).toHaveAttribute("aria-expanded", "true");
    expect(second).toHaveAttribute("aria-expanded", "false");
    const region = screen.getByRole("region", { name: "Source T1" });
    expect(first).toHaveAttribute("aria-controls", region.id);
    expect(second).not.toHaveAttribute("aria-controls");

    await user.click(second);
    expect(first).toHaveAttribute("aria-expanded", "false");
    expect(first).not.toHaveAttribute("aria-controls");
    expect(second).toHaveAttribute("aria-expanded", "true");
    expect(second).toHaveAttribute("aria-controls", region.id);

    await user.click(second);
    expect(screen.queryByRole("region", { name: "Source T1" })).not.toBeInTheDocument();
    expect(second).toHaveAttribute("aria-expanded", "false");

    await user.click(first);
    await user.click(second);
    await user.keyboard("{Escape}");
    expect(second).toHaveFocus();

    await user.click(second);
    await user.click(screen.getByRole("button", { name: "Open page 2" }));
    expect(openDocument).toHaveBeenCalledWith(answer, expect.objectContaining({ label: "T1", page_number: 2 }), second.id);
  });
});
