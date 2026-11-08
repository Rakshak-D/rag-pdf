import type { Conversation, Mode } from "./conversations";

interface Props {
  conversations: Conversation[];
  activeId: string;
  mode: Mode;
  notice: string;
  mobileOpen: boolean;
  evidenceOpen: boolean;
  pendingConversationId: string | null;
  onNew: () => void;
  onSelect: (conversation: Conversation) => void;
  onDelete: () => void;
  onDismiss: () => void;
}

function titleFor(conversation: Conversation): string {
  const firstQuestion = conversation.messages.find((message) => message.role === "user")?.text;
  const source = firstQuestion || conversation.question;
  if (!source) return "New conversation";
  const normalized = source.replace(/\s+/g, " ").trim();
  return normalized.length > 48 ? `${normalized.slice(0, 47).trimEnd()}…` : normalized;
}

export function ConversationRail({ conversations, activeId, mode, notice, mobileOpen, evidenceOpen, pendingConversationId, onNew, onSelect, onDelete, onDismiss }: Props) {
  const grouped: Record<Mode, Conversation[]> = {
    general: [...conversations].reverse().filter((conversation) => conversation.mode === "general"),
    document: [...conversations].reverse().filter((conversation) => conversation.mode === "document"),
  };

  return <>
    {mobileOpen ? <button className="chat-rail-scrim" type="button" aria-label="Close conversations" onClick={onDismiss} /> : null}
    <aside className={`chat-rail${mobileOpen ? " chat-rail--open" : ""}`} aria-label="Conversations" aria-hidden={!mobileOpen && undefined} inert={evidenceOpen}>
      <div className="chat-rail__header">
        <div><span className="chat-rail__eyebrow">WORKSPACE</span><h2>Conversations</h2></div>
        <button type="button" className="chat-rail__new" onClick={onNew} aria-label={`New ${mode === "general" ? "General Chat" : "PDF chat"}`} title="Start a new conversation">+</button>
      </div>
      <div className="chat-rail__list">
        {(["general", "document"] as const).map((groupMode) => <section className="chat-rail__group" key={groupMode} aria-labelledby={`chat-group-${groupMode}`}>
          <h3 id={`chat-group-${groupMode}`}>{groupMode === "general" ? "General Chat" : "Chat with PDFs"}</h3>
          {grouped[groupMode].length ? grouped[groupMode].map((conversation) => {
            const title = titleFor(conversation);
            const active = conversation.id === activeId;
            return <div className={`chat-rail__item${active ? " is-active" : ""}`} key={conversation.id}>
              <button
                type="button"
                className="chat-rail__select"
                aria-current={active ? "page" : undefined}
                aria-label={`${groupMode === "general" ? "General Chat" : "Chat with PDFs"}: ${title}`}
                title={title}
                onClick={() => { onSelect(conversation); onDismiss(); }}
              >
                <span className="chat-rail__item-title">{title}</span>
                {conversation.id === pendingConversationId ? <span className="chat-rail__pending" aria-label="Response in progress">Working</span> : null}
              </button>
              {active ? <button type="button" className="chat-rail__delete" aria-label="Delete conversation" title="Delete this conversation" disabled={conversation.id === pendingConversationId} onClick={onDelete}>×</button> : null}
            </div>;
          }) : <p className="chat-rail__empty">No conversations yet</p>}
        </section>)}
      </div>
      <div className="chat-rail__footer">
        {notice ? <p role="status">{notice}</p> : <p>Saved for this browser tab session</p>}
      </div>
    </aside>
  </>;
}
