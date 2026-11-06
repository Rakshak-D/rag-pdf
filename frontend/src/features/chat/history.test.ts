import { describe, expect, it } from "vitest";
import { boundedChatHistory } from "./history";

describe("bounded General Chat history", () => {
  it("retains the newest complete turns within count and character limits", () => {
    const turns = Array.from({ length: 10 }, (_, index) => ({
      user: `question-${index}`,
      assistant: index < 8 ? "a".repeat(4000) : "b".repeat(15_000),
    }));
    const bounded = boundedChatHistory(turns);
    expect(bounded.map((turn) => turn.user)).toEqual(["question-8", "question-9"]);
    expect(bounded.reduce((total, turn) => total + turn.user.length + turn.assistant.length, 0)).toBeLessThanOrEqual(32_000);
    expect(bounded).toHaveLength(2);
  });

  it("caps history at eight turns and returns an independent array", () => {
    const turns = Array.from({ length: 12 }, (_, index) => ({ user: `q${index}`, assistant: `a${index}` }));
    const bounded = boundedChatHistory(turns);
    expect(bounded.map((turn) => turn.user)).toEqual(["q4", "q5", "q6", "q7", "q8", "q9", "q10", "q11"]);
    expect(bounded).not.toBe(turns);
  });
});
