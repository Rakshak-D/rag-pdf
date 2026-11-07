import { describe, expect, it } from "vitest";
import { decodeSession, encodeSession } from "./conversations";
const session = () => ({ version: 1 as const, activeId: "one", conversations: [{ id: "one", mode: "general" as const, question: "Draft", messages: [], generalTurns: [] }] });
describe("conversation session boundary", () => {
  it("restores a versioned session with its active draft", () => { expect(decodeSession(encodeSession(session()))).toEqual(session()); });
  it("rejects corrupt, oversized, extra-field, invalid active and over-limit history records", () => {
    for (const raw of ["{", "x".repeat(256*1024+1), JSON.stringify({ ...session(), token: "private" }), JSON.stringify({ ...session(), activeId: "missing" }), JSON.stringify({ ...session(), version: 2 }), JSON.stringify({ ...session(), conversations: [{ ...session().conversations[0], generalTurns: Array(9).fill({ user: "hi", assistant: "hello" }) }] })]) expect(() => decodeSession(raw)).toThrow();
  });
  it("omits retrieved passages and image references while retaining response provenance", () => {
    const value = session();
    const response = { index_fingerprint: "a".repeat(64), document_manifest_hash: "b".repeat(64), text_evidence: [{ passage: "private extracted passage" }], image_evidence: [{ image_reference: "expiring-image-ref" }] };
    const doc = { ...value, conversations: [{ ...value.conversations[0]!, mode: "document" as const, messages: [{ id: "m", role: "assistant" as const, mode: "document" as const, text: "Answer [T1]", document: response }] }] };
    const raw = encodeSession(doc as unknown as Parameters<typeof encodeSession>[0]);
    expect(raw).not.toContain("private extracted passage"); expect(raw).not.toContain("expiring-image-ref");
    const restored = decodeSession(raw).conversations[0]!.messages[0]!;
    expect(restored.evidenceExpired).toBe(true); expect(restored.indexFingerprint).toBe("a".repeat(64)); expect(restored.document).toBeUndefined();
  });
  it("rejects General history attached to a Document conversation", () => { expect(() => decodeSession(JSON.stringify({ ...session(), conversations: [{ ...session().conversations[0], mode: "document", generalTurns: [{user:"hello",assistant:"world"}] }] }))).toThrow(); });
});
