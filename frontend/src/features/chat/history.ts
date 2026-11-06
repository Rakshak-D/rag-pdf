import type { ChatTurn } from "../../api/types";

const MAX_TURNS = 8;
const MAX_HISTORY_CHARS = 32_000;

export function boundedChatHistory(turns: readonly ChatTurn[]): ChatTurn[] {
  const retained: ChatTurn[] = [];
  let characterCount = 0;
  for (let index = turns.length - 1; index >= 0 && retained.length < MAX_TURNS; index -= 1) {
    const turn = turns[index];
    if (!turn) continue;
    const turnSize = turn.user.length + turn.assistant.length;
    if (characterCount + turnSize > MAX_HISTORY_CHARS) break;
    retained.push(turn);
    characterCount += turnSize;
  }
  return retained.reverse();
}
