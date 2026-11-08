import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ComponentProps } from "react";
import { ConversationRail } from "./ConversationRail";
import type { Conversation } from "./conversations";

const items: Conversation[] = [
  { id: "general-a", mode: "general", messages: [{ id: "g-user", role: "user", mode: "general", text: "Compare these two ideas" }], generalTurns: [], question: "unsent general draft" },
  { id: "document-b", mode: "document", messages: [{ id: "d-user", role: "user", mode: "document", text: "Which page describes the orbit?" }], generalTurns: [], question: "" },
  { id: "general-c", mode: "general", messages: [], generalTurns: [], question: "A draft with no sent messages" },
];

function renderRail(overrides: Partial<ComponentProps<typeof ConversationRail>> = {}) {
  const onSelect = vi.fn();
  render(<ConversationRail
    conversations={items}
    activeId="general-a"
    mode="general"
    notice=""
    mobileOpen={false}
    evidenceOpen={false}
    pendingConversationId={null}
    onNew={vi.fn()}
    onSelect={onSelect}
    onDelete={vi.fn()}
    onDismiss={vi.fn()}
    {...overrides}
  />);
  return { onSelect };
}

describe("ConversationRail", () => {
  it("groups conversations by mode and derives titles only from actual messages or drafts", () => {
    renderRail();
    const rail = screen.getByRole("complementary", { name: "Conversations" });
    expect(within(rail).getByRole("heading", { name: "General Chat" })).toBeInTheDocument();
    expect(within(rail).getByRole("heading", { name: "Chat with PDFs" })).toBeInTheDocument();
    expect(within(rail).getByRole("button", { name: "General Chat: Compare these two ideas" })).toHaveAttribute("aria-current", "page");
    expect(within(rail).getByRole("button", { name: "Chat with PDFs: Which page describes the orbit?" })).toBeInTheDocument();
    expect(within(rail).getByRole("button", { name: "General Chat: A draft with no sent messages" })).toBeInTheDocument();
  });

  it("switches conversations with a keyboard-operable button and marks pending work only on its owner", async () => {
    const user = userEvent.setup();
    const { onSelect } = renderRail({ pendingConversationId: "document-b" });
    const target = screen.getByRole("button", { name: "Chat with PDFs: Which page describes the orbit?" });
    for (let index = 0; index < 5; index += 1) await user.tab();
    expect(target).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledWith(items[1]);
    expect(screen.getByLabelText("Response in progress")).toBeInTheDocument();
  });
});
