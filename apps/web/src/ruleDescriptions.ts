import type { DeterministicRule, RuleExpression } from "./types";

export function summarizeRule(rule: DeterministicRule, labels: Map<string, string>): string {
  const label = labels.get(rule.field) ?? rule.field;
  if (rule.kind === "required") return `${label}不能为空`;
  if (rule.kind === "range") return `${label}范围：${rule.minimum ?? "不限"} ～ ${rule.maximum ?? "不限"}`;
  if (rule.kind === "enum") return `${label}只能是：${rule.values.join("、")}`;
  if (rule.kind === "pattern") return `${label}需符合指定文字格式`;
  return `${expressionText(rule.left, labels)} = ${expressionText(rule.right, labels)}`;
}

function expressionText(expression: RuleExpression, labels: Map<string, string>): string {
  if (expression.op === "field") return labels.get(expression.path) ?? expression.path;
  if (expression.op === "sum") {
    return `${labels.get(expression.path) ?? expression.path}之和`;
  }
  if (expression.op === "constant") return String(expression.value);
  const symbols = { add: "+", subtract: "−", multiply: "×", divide: "÷" };
  return `(${expressionText(expression.left, labels)} ${symbols[expression.op]} ${expressionText(expression.right, labels)})`;
}
