import { expect, it } from "vitest";
import { editableTemplate, mergeTemplateHints, moveTemplateField } from "./templateEditing";
import type { TemplateDraft } from "./types";

it("merges legacy hints once without losing text or deterministic rules", () => {
  const draft: TemplateDraft = { name: "记录", description: "", fields: [], output_mapping: {}, extra_instructions: "保留日期格式", validation_rules: ["保留日期格式", "手写优先", "手写优先"], deterministic_rules: [] };
  const edited = editableTemplate(draft);
  expect(edited.extra_instructions).toBe("保留日期格式\n手写优先");
  expect(edited.validation_rules).toEqual([]);
  expect(editableTemplate(edited)).toEqual(edited);
  expect(draft.validation_rules).toHaveLength(3);
  expect(mergeTemplateHints("长".repeat(4000), ["旧要求"])).toHaveLength(4004);
  expect(mergeTemplateHints("保留原文\n特殊情况：\n缺失时留空", ["特殊情况：\n缺失时留空"])).toBe("保留原文\n特殊情况：\n缺失时留空");
});

it("reorders only the field sequence and preserves identity and section", () => {
  const fields = [{ key: "doc", label: "文件", section: "header" as const, instructions: "", example: "", value_type: "text" as const }, { key: "entry", label: "条目", section: "item" as const, instructions: "原文", example: "", value_type: "text" as const }];
  const sorted = moveTemplateField(fields, 1, 0);
  expect(sorted).toEqual([fields[1], fields[0]]);
  expect(sorted[0]).toBe(fields[1]);
  expect(fields[0].key).toBe("doc");
  expect(moveTemplateField(fields, 0, -1)).toBe(fields);
});
