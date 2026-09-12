import { describe, expect, it } from "vitest";
import { evidenceDescription } from "./evidence";
import type { Extraction } from "./types";

const base: NonNullable<Extraction["evidence"]>[number] = {
  field_path: "header.title", status: "unavailable", source: "system", location_verified: false,
  page_number: null, region: null, quote: null,
};

describe("source evidence descriptions", () => {
  it("shows native coordinates without claiming the field was located", () => {
    expect(evidenceDescription({ ...base, location: { kind: "sheet_row", container: "归档信息", start: 8, end: 20, columns: 3 } }))
      .toBe("本次输入包含 归档信息!A8:C20；尚未确认该字段的具体位置");
    expect(evidenceDescription({ ...base, location: { kind: "line", container: null, start: 901, end: 1000, columns: null } }))
      .toContain("第 901–1000 行");
  });
  it("does not invent page one or trust unverified model coordinates", () => {
    expect(evidenceDescription({ ...base, status: "page_only" })).not.toContain("第 1 页");
    expect(evidenceDescription({ ...base, status: "located", source: "model_reported", quote: "原文片段" }))
      .toBe("模型提供了来源摘录，位置尚未核验：原文片段");
  });
  it("preserves verified page evidence and invalidates edited values", () => {
    expect(evidenceDescription({ ...base, status: "page_only", page_number: 3, location_verified: true }))
      .toBe("仅确认来自第 3 页，暂无可靠区域");
    expect(evidenceDescription({ ...base, status: "user_edited", location: { kind: "line", start: 1, end: 1, columns: null, container: null } }))
      .toBe("该值经过人工修改，原定位已失效");
  });
});
