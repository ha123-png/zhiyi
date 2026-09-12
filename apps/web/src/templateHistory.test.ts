import { expect, it } from "vitest";
import { templateDifferences } from "./templateHistory";
import type { RuleExpression, TemplateDraft } from "./types";

const base: TemplateDraft = { name: "记录", description: "", extra_instructions: "保留原文", validation_rules: [], deterministic_rules: [], output_mapping: {}, fields: [
  { key: "a", label: "名称", section: "header", value_type: "text", example: "", instructions: "" },
  { key: "b", label: "日期", section: "item", value_type: "text", example: "", instructions: "" },
] };
it("distinguishes reading order from structural changes and recognizes equivalent legacy hints", () => {
  const legacy = { ...base, extra_instructions: "", validation_rules: ["保留原文"], fields: [...base.fields].reverse() };
  expect(templateDifferences(base, legacy).map(d => d.label)).toEqual(["阅读顺序"]);
  const structural = { ...base, fields: [{ ...base.fields[0], section: "item" as const }, { ...base.fields[1], value_type: "date" as const }] };
  expect(templateDifferences(base, structural).filter(d => d.structural)).toHaveLength(3);
  expect(base.fields[0].section).toBe("header");
});
it("shows rule severity and tolerance changes in readable language", () => {
  const current: TemplateDraft = { ...base, deterministic_rules: [{ kind: "required", field: "header.a", severity: "error" }] };
  const next: TemplateDraft = { ...current, deterministic_rules: [{ kind: "required", field: "header.a", severity: "warning" }] };
  expect(templateDifferences(current, next)[0]).toMatchObject({ label: "程序校验", before: "名称不能为空（错误）", after: "名称不能为空（提醒）" });
});
it("preserves calculation grouping so different equations cannot appear identical", () => {
  const constant = (value: number): RuleExpression => ({ op: "constant", value });
  const first: RuleExpression = { op: "multiply", left: { op: "add", left: constant(1), right: constant(2) }, right: constant(3) };
  const second: RuleExpression = { op: "add", left: constant(1), right: { op: "multiply", left: constant(2), right: constant(3) } };
  const template = (left: RuleExpression): TemplateDraft => ({ ...base, deterministic_rules: [{ kind: "equation", field: "header.a", left, right: constant(9), severity: "error", tolerance: 0.01 }] });
  const differences = templateDifferences(template(first), template(second));
  expect(differences).toHaveLength(1);
  expect(differences[0].before).toContain("((1 + 2) × 3)");
  expect(differences[0].after).toContain("(1 + (2 × 3))");
});
