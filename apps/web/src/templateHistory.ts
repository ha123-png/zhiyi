import type { TemplateDraft } from "./types";
import { summarizeRule } from "./ruleDescriptions";
import { mergeTemplateHints } from "./templateEditing";

export interface TemplateDifference { label: string; before: string; after: string; structural?: boolean }
const fieldId = (f: TemplateDraft["fields"][number]) => `${f.section}:${f.key}`;
const typeNames = { text: "文本", number: "数字", date: "日期", boolean: "是／否" };
export function templateDifferences(current: TemplateDraft, target: TemplateDraft): TemplateDifference[] {
  const changes: TemplateDifference[] = [];
  function add(label: string, before: string, after: string, structural = false) {
    if (before !== after) changes.push({ label, before: before || "未设置", after: after || "未设置", structural });
  }
  add("模板名称", current.name, target.name);
  add("用途说明", current.description, target.description);
  add("额外提示词", mergeTemplateHints(current.extra_instructions, current.validation_rules), mergeTemplateHints(target.extra_instructions, target.validation_rules));
  const mode = (t: TemplateDraft) => t.behavior?.presentation.mode === "card" ? "卡片" : "表格";
  add("默认展示", mode(current), mode(target));
  add("命名方式", current.behavior?.filename_mode === "fixed" ? "固定规则" : "AI 命名", target.behavior?.filename_mode === "fixed" ? "固定规则" : "AI 命名");
  add("文件命名", current.behavior?.suggest_filename ? "开启" : "关闭", target.behavior?.suggest_filename ? "开启" : "关闭");
  const oldFields = new Map(current.fields.map(f => [fieldId(f), f]));
  const newFields = new Map(target.fields.map(f => [fieldId(f), f]));
  for (const [key, f] of oldFields) if (!newFields.has(key)) add(`移除字段 · ${f.label}`, `${f.section === "header" ? "整份文件" : "每条明细"} / ${typeNames[f.value_type]}`, "恢复后不再提取此字段", true);
  for (const [key, f] of newFields) {
    const old = oldFields.get(key);
    if (!old) { add(`加入字段 · ${f.label}`, "不存在", `${f.section === "header" ? "整份文件" : "每条明细"} / ${typeNames[f.value_type]}`, true); continue; }
    add(`字段名称 · ${old.label}`, old.label, f.label);
    add(`字段类型 · ${f.label}`, typeNames[old.value_type], typeNames[f.value_type], true);
    add(`字段要求 · ${f.label}`, old.instructions, f.instructions);
    add(`字段示例 · ${f.label}`, old.example, f.example);
  }
  if (current.fields.map(fieldId).join("|") !== target.fields.map(fieldId).join("|"))
    changes.push({ label: "阅读顺序", before: current.fields.map(f => f.label).join(" → "), after: target.fields.map(f => f.label).join(" → ") });
  const labels = (t: TemplateDraft) => new Map(t.fields.map(f => [`${f.section === "item" ? "items[]" : "header"}.${f.key}`, f.label]));
  const rules = (t: TemplateDraft) => (t.deterministic_rules ?? []).map(r => `${summarizeRule(r, labels(t))}（${r.severity === "warning" ? "提醒" : "错误"}${r.kind === "equation" ? `，容差 ${r.tolerance ?? 0.01}` : ""}${r.kind === "pattern" ? `，格式 ${r.pattern}` : ""}）`).join("\n");
  add("程序校验", rules(current), rules(target));
  const mapping = (t: TemplateDraft) => Object.entries(t.output_mapping).map(([key,value]) => `${t.fields.find(f => f.key === key)?.label ?? key} → ${value}`).join("\n");
  add("导出字段名称", mapping(current), mapping(target));
  return changes;
}
