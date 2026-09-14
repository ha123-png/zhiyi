import type { ThreadDetail } from "./types";

export interface TextDelta { sequence: number; part_index: number; offset: number; text: string }

/** Offsets are Unicode code points, matching Python; replay is idempotent. */
export function applyDeltas(detail: ThreadDetail, runId: string, deltas: TextDelta[]): ThreadDetail {
  const run = detail.runs.find(value => value.id === runId);
  if (!run?.message_id || !deltas.length) return detail;
  let changed = false;
  const messages = detail.messages.map(message => {
    if (message.id !== run.message_id) return message;
    const parts = [...message.parts];
    let edited = false;
    for (const event of deltas) {
      if (event.part_index > parts.length) continue; // A checkpoint supplies tool boundaries.
      const part = parts[event.part_index] ?? { type: "text" as const, text: "" };
      if (part.type !== "text") continue;
      const length = Array.from(part.text).length;
      if (event.offset > length) continue;
      const suffix = Array.from(event.text).slice(Math.max(0, length - event.offset)).join("");
      if (!suffix) continue;
      parts[event.part_index] = { type: "text", text: part.text + suffix };
      edited = changed = true;
    }
    return edited ? { ...message, parts } : message;
  });
  return changed ? { ...detail, messages } : detail;
}

export function mergePage(previous: ThreadDetail, incoming: ThreadDetail, older = false): ThreadDetail {
  const merge = <T extends { id: string }>(old: T[], next: T[]) => {
    const map = new Map(next.map(value => [value.id, value]));
    const ids = new Set(old.map(value => value.id));
    const added = next.filter(value => !ids.has(value.id));
    return older ? [...added, ...old] : [...old.map(value => map.get(value.id) || value), ...added];
  };
  return { ...previous, ...incoming, partial: false,
    has_more: older ? incoming.has_more : previous.has_more,
    oldest_position: older ? incoming.oldest_position : previous.oldest_position,
    messages: merge(previous.messages, incoming.messages), tools: merge(previous.tools, incoming.tools),
    runs: merge(previous.runs, incoming.runs).sort((a,b) => Number(["running", "waiting", "cancelling"].includes(b.status)) - Number(["running", "waiting", "cancelling"].includes(a.status))),
  };
}
