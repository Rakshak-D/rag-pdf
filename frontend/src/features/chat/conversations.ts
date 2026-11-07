import { useCallback, useEffect, useState, type SetStateAction } from "react";
import type { AskResponse, ChatTurn } from "../../api/types";
import { boundedChatHistory } from "./history";
export type Mode = "general" | "document";
export type Message = { id: string; role: "user" | "assistant"; mode: Mode; text: string; error?: boolean; document?: AskResponse; indexFingerprint?: string; sourceManifest?: string; evidenceExpired?: boolean };
export type Conversation = { id: string; mode: Mode; messages: Message[]; generalTurns: ChatTurn[]; question: string };
type Session = { version: 1; activeId: string; conversations: Conversation[] };
export const SESSION_KEY = "rag-pdf.conversations.v1";
const MAX_BYTES = 256 * 1024;
const id = () => crypto.randomUUID();
const fresh = (mode: Mode): Conversation => ({ id: id(), mode, messages: [], generalTurns: [], question: "" });
const record = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const keys = (v: Record<string, unknown>, allowed: string[]) => Object.keys(v).every(k => allowed.includes(k));
const text = (v: unknown, n: number): v is string => typeof v === "string" && v.length <= n;
export function decodeSession(raw: string): Session {
  if (new TextEncoder().encode(raw).length > MAX_BYTES) throw Error("session_size");
  const v: unknown = JSON.parse(raw);
  if (!record(v) || !keys(v, ["version", "activeId", "conversations"]) || v.version !== 1 || !text(v.activeId, 80) || !Array.isArray(v.conversations) || !v.conversations.length || v.conversations.length > 10) throw Error("session_invalid");
  const ids = new Set<string>();
  for (const c of v.conversations) {
    if (!record(c) || !keys(c, ["id", "mode", "messages", "generalTurns", "question"]) || !text(c.id, 80) || !c.id || ids.has(c.id) || !["general", "document"].includes(String(c.mode)) || !text(c.question, 4000) || !Array.isArray(c.messages) || c.messages.length > 100 || !Array.isArray(c.generalTurns) || c.generalTurns.length > 8) throw Error("conversation_invalid");
    ids.add(c.id);
    for (const m of c.messages) {
      if (!record(m) || !keys(m, ["id", "role", "mode", "text", "error", "indexFingerprint", "sourceManifest", "evidenceExpired"]) || !text(m.id, 80) || !["user", "assistant"].includes(String(m.role)) || m.mode !== c.mode || !text(m.text, 32000) || (m.error !== undefined && typeof m.error !== "boolean") || (m.evidenceExpired !== undefined && typeof m.evidenceExpired !== "boolean")) throw Error("message_invalid");
      for (const k of ["indexFingerprint", "sourceManifest"]) if (m[k] !== undefined && (typeof m[k] !== "string" || !/^[a-f0-9]{64}$/.test(m[k]))) throw Error("provenance_invalid");
    }
    for (const t of c.generalTurns) if (!record(t) || !keys(t, ["user", "assistant"]) || !text(t.user, 4000) || !text(t.assistant, 32000)) throw Error("history_invalid");
    if ((c.mode === "document" && c.generalTurns.length) || JSON.stringify(boundedChatHistory(c.generalTurns as ChatTurn[])) !== JSON.stringify(c.generalTurns)) throw Error("history_limit");
  }
  if (!ids.has(v.activeId)) throw Error("active_invalid");
  return v as unknown as Session;
}
export function encodeSession(session: Session): string {
  const raw = JSON.stringify({ ...session, conversations: session.conversations.map(c => ({ ...c, messages: c.messages.map(m => {
    const { document, ...safe } = m;
    return document ? { ...safe, indexFingerprint: document.index_fingerprint, sourceManifest: document.document_manifest_hash, evidenceExpired: true } : safe;
  }) })) });
  decodeSession(raw);
  return raw;
}
export function useConversations() {
  const [initial] = useState(() => {
    try { const raw = sessionStorage.getItem(SESSION_KEY); if (raw) { const restored = decodeSession(raw); return { session: restored, notice: restored.conversations.some(c => c.messages.at(-1)?.role === "user") ? "A previous request may still be processing. No retry was sent after reload." : "" }; } }
    catch { return { session: empty(), notice: "Saved conversations could not be restored. This conversation is new." }; }
    return { session: empty(), notice: "" };
  });
  const [session, setSession] = useState(initial.session);
  const [notice, setNotice] = useState(initial.notice);
  useEffect(() => { try { if (session.conversations.every(c => !c.messages.length && !c.question)) sessionStorage.removeItem(SESSION_KEY); else sessionStorage.setItem(SESSION_KEY, encodeSession(session)); } catch { setNotice("This conversation is kept in memory, but cannot be saved for reload. Keep this tab open."); } }, [session]);
  const active = session.conversations.find(c => c.id === session.activeId)!;
  const update = useCallback(<K extends "messages" | "generalTurns" | "question">(key: K, value: SetStateAction<Conversation[K]>) => {
    const target = session.activeId;
    setSession(s => ({ ...s, conversations: s.conversations.map(c => c.id === target ? { ...c, [key]: typeof value === "function" ? (value as (v: Conversation[K]) => Conversation[K])(c[key]) : value } : c) }));
  }, [session.activeId]);
  const create = (mode: Mode) => {
    if (session.conversations.length >= 10) { setNotice("Ten conversations are open. Delete one before starting another."); return; }
    const c = fresh(mode); setSession(s => ({ ...s, activeId: s.conversations.length < 10 ? c.id : s.activeId, conversations: s.conversations.length < 10 ? [...s.conversations, c] : s.conversations }));
  };
  return { active, conversations: session.conversations, notice,
    setMessages: (v: SetStateAction<Message[]>) => update("messages", v),
    setGeneralTurns: (v: SetStateAction<ChatTurn[]>) => update("generalTurns", v),
    setQuestion: (v: SetStateAction<string>) => update("question", v),
    create, select: (activeId: string) => setSession(s => s.conversations.some(c => c.id === activeId) ? { ...s, activeId } : s),
    setMode: (mode: Mode) => { const c = [...session.conversations].reverse().find(c => c.mode === mode); if (c) setSession(s => ({ ...s, activeId: c.id })); else create(mode); },
    remove: () => setSession(s => { const remaining = s.conversations.filter(c => c.id !== s.activeId); if (!remaining.length) remaining.push(fresh(active.mode)); return { ...s, conversations: remaining, activeId: remaining[remaining.length - 1]!.id }; }),
  };
}
function empty(): Session { const c = fresh("general"); return { version: 1, activeId: c.id, conversations: [c] }; }
export type ConversationStore = ReturnType<typeof useConversations>;
