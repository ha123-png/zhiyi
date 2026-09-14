import { expect, it } from "vitest";
import { applyDeltas, mergePage } from "./stream";
import type { ThreadDetail } from "./types";

const base: ThreadDetail = { id: "t", title: "test", archived: false, profile_id: null, updated_at: "", has_more: true, oldest_position: 60,
  messages: [{ id: "m", role: "assistant", context: {}, created_at: "", parts: [{ type: "text", text: "甲😀" }] }],
  runs: [{ id: "r", message_id: "m", stream_cursor: 1, status: "running", error: null, profile_id: "p", profile_version: 1, model: "m" }], tools: [] };
it("replays beyond a stale checkpoint without duplicating emoji or losing a tool boundary", () => {
  const deltas = [{ sequence: 2, part_index: 0, offset: 2, text: "乙" }, { sequence: 4, part_index: 2, offset: 0, text: "完成" }];
  const first = applyDeltas(base, "r", deltas);
  expect(first.messages[0].parts).toEqual([{ type: "text", text: "甲😀乙" }]);
  expect(applyDeltas(first, "r", deltas)).toBe(first);
  const checkpoint = structuredClone(base);
  checkpoint.partial = true;
  checkpoint.messages[0].parts.push({ type: "tool", id: "tool", name: "catalog", status: "completed", result: {} });
  const result = applyDeltas(mergePage(first, checkpoint), "r", deltas);
  expect(result.messages[0].parts.at(-1)).toEqual({ type: "text", text: "完成" });
  expect(result.messages[0].parts[0]).toEqual({ type: "text", text: "甲😀乙" });
  expect(result.oldest_position).toBe(60);
});
