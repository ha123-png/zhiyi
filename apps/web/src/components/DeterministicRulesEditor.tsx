import { useMemo, useState } from "react";
import type {
  DeterministicRule,
  RuleExpression,
  TemplateField,
} from "../types";

type RuleKind = "required" | "range" | "enum" | "pattern" | "row_multiply" | "sum";

interface Props {
  disabled: boolean;
  fields: TemplateField[];
  rules: DeterministicRule[];
  onChange: (rules: DeterministicRule[]) => void;
}

export function DeterministicRulesEditor({ disabled, fields, rules, onChange }: Props) {
  const available = useMemo(
    () => fields.filter((field): field is TemplateField & { key: string } => Boolean(field.key)),
    [fields],
  );
  const numericHeaders = available.filter(
    (field) => field.section === "header" && field.value_type === "number",
  );
  const numericItems = available.filter(
    (field) => field.section === "item" && field.value_type === "number",
  );
  const [kind, setKind] = useState<RuleKind>("required");
  const [fieldPath, setFieldPath] = useState("");
  const [minimum, setMinimum] = useState("");
  const [maximum, setMaximum] = useState("");
  const [values, setValues] = useState("");
  const [pattern, setPattern] = useState("");
  const [leftField, setLeftField] = useState("");
  const [rightField, setRightField] = useState("");
  const [resultField, setResultField] = useState("");
  const labels = new Map(available.map((field) => [pathFor(field), field.label]));

  function addRule() {
    const rule = buildRule({
      kind,
      fieldPath,
      minimum,
      maximum,
      values,
      pattern,
      leftField,
      rightField,
      resultField,
    });
    if (rule) {
      onChange([...rules, rule]);
    }
  }

  const canAdd = canBuildRule({
    kind,
    fieldPath,
    minimum,
    maximum,
    values,
    pattern,
    leftField,
    rightField,
    resultField,
  });

  return (
    <div className="deterministic-rules-editor">
      <div className="deterministic-rules-head">
        <div>
          <strong>自定义校验规则</strong>
          <span>必填、数字范围、指定值、行内计算（如 数量 × 单价 = 金额）、合计核对（如 明细之和 = 抬头合计）等。字段都由你指定，适用于任何单据；由程序自动执行并报错，不依赖 AI。</span>
        </div>
        <span className="badge neutral">{rules.length} 条</span>
      </div>

      {rules.length > 0 ? (
        <div className="deterministic-rule-list">
          {rules.map((rule, index) => (
            <div className="deterministic-rule-row" key={`${rule.kind}-${index}`}>
              <span>{summarizeRule(rule, labels)}</span>
              <button
                className="btn ghost sm"
                disabled={disabled}
                onClick={() => onChange(rules.filter((_, itemIndex) => itemIndex !== index))}
                type="button"
              >
                删除
              </button>
            </div>
          ))}
        </div>
      ) : (
        <div className="support">暂无自定义校验规则。上面的“AI 理解要求”只会提示 AI，不会自动报错。</div>
      )}

      {available.length === 0 ? (
        <div className="support">字段需要稳定标识才能参与校验，请先点右上角“保存”，再回来添加校验规则。</div>
      ) : (
        <div className="deterministic-rule-builder">
          <label>
            <span>规则类型</span>
            <select
              className="form-select"
              disabled={disabled}
              onChange={(event) => setKind(event.target.value as RuleKind)}
              value={kind}
            >
              <option value="required">不能为空</option>
              <option value="range">数字范围</option>
              <option value="enum">只能是指定值</option>
              <option value="pattern">文字格式（高级）</option>
              <option value="row_multiply">行内计算：A 字段 × B 字段 = C 字段</option>
              <option value="sum">合计核对：明细字段之和 = 抬头字段</option>
            </select>
          </label>

          {kind === "required" || kind === "range" || kind === "enum" || kind === "pattern" ? (
            <label>
              <span>检查字段</span>
              <FieldSelect
                disabled={disabled}
                fields={kind === "range" ? available.filter((f) => f.value_type === "number") : available}
                onChange={setFieldPath}
                value={fieldPath}
              />
            </label>
          ) : null}

          {kind === "range" ? (
            <div className="deterministic-rule-pair">
              <label>
                <span>最小值（可空）</span>
                <input className="form-input" disabled={disabled} onChange={(e) => setMinimum(e.target.value)} type="number" value={minimum} />
              </label>
              <label>
                <span>最大值（可空）</span>
                <input className="form-input" disabled={disabled} onChange={(e) => setMaximum(e.target.value)} type="number" value={maximum} />
              </label>
            </div>
          ) : null}

          {kind === "enum" ? (
            <label>
              <span>允许的值（用中文逗号或英文逗号分开）</span>
              <input className="form-input" disabled={disabled} onChange={(e) => setValues(e.target.value)} placeholder="例如：有效，作废" value={values} />
            </label>
          ) : null}

          {kind === "pattern" ? (
            <label>
              <span>安全格式表达式</span>
              <input className="form-input" disabled={disabled} onChange={(e) => setPattern(e.target.value)} placeholder="例如：PO-\d{3}" value={pattern} />
            </label>
          ) : null}

          {kind === "row_multiply" ? (
            <div className="deterministic-rule-triple">
              <FieldSelect disabled={disabled} fields={numericItems} onChange={setLeftField} placeholder="左操作数字段（如 数量）" value={leftField} />
              <FieldSelect disabled={disabled} fields={numericItems} onChange={setRightField} placeholder="右操作数字段（如 单价）" value={rightField} />
              <FieldSelect disabled={disabled} fields={numericItems} onChange={setResultField} placeholder="结果字段（如 明细金额）" value={resultField} />
            </div>
          ) : null}

          {kind === "sum" ? (
            <div className="deterministic-rule-pair">
              <FieldSelect disabled={disabled} fields={numericItems} onChange={setLeftField} placeholder="明细求和字段（如 明细金额）" value={leftField} />
              <FieldSelect disabled={disabled} fields={numericHeaders} onChange={setResultField} placeholder="抬头合计字段（如 合计金额）" value={resultField} />
            </div>
          ) : null}

          <button className="btn secondary sm" disabled={disabled || !canAdd} onClick={addRule} type="button">
            添加校验规则
          </button>
        </div>
      )}
    </div>
  );
}

function FieldSelect({ disabled, fields, onChange, placeholder = "请选择字段", value }: {
  disabled: boolean;
  fields: Array<TemplateField & { key: string }>;
  onChange: (value: string) => void;
  placeholder?: string;
  value: string;
}) {
  return (
    <select
      aria-label={placeholder}
      className="form-select"
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
      value={value}
    >
      <option value="">{placeholder}</option>
      {fields.map((field) => (
        <option key={pathFor(field)} value={pathFor(field)}>
          {field.label}（{field.section === "header" ? "抬头" : "明细"}）
        </option>
      ))}
    </select>
  );
}

function pathFor(field: TemplateField & { key: string }) {
  return `${field.section === "header" ? "header" : "items[]"}.${field.key}`;
}

interface BuilderState {
  kind: RuleKind;
  fieldPath: string;
  minimum: string;
  maximum: string;
  values: string;
  pattern: string;
  leftField: string;
  rightField: string;
  resultField: string;
}

function canBuildRule(state: BuilderState): boolean {
  if (state.kind === "required") return Boolean(state.fieldPath);
  if (state.kind === "range") return Boolean(state.fieldPath && (state.minimum || state.maximum));
  if (state.kind === "enum") return Boolean(state.fieldPath && splitValues(state.values).length);
  if (state.kind === "pattern") return Boolean(state.fieldPath && state.pattern);
  if (state.kind === "row_multiply") return Boolean(state.leftField && state.rightField && state.resultField);
  return Boolean(state.leftField && state.resultField);
}

function buildRule(state: BuilderState): DeterministicRule | null {
  if (!canBuildRule(state)) return null;
  if (state.kind === "required") return { kind: "required", field: state.fieldPath };
  if (state.kind === "range") {
    return {
      kind: "range",
      field: state.fieldPath,
      minimum: state.minimum ? Number(state.minimum) : null,
      maximum: state.maximum ? Number(state.maximum) : null,
    };
  }
  if (state.kind === "enum") return { kind: "enum", field: state.fieldPath, values: splitValues(state.values) };
  if (state.kind === "pattern") return { kind: "pattern", field: state.fieldPath, pattern: state.pattern };
  if (state.kind === "sum") {
    return equation(
      { op: "sum", path: state.leftField },
      { op: "field", path: state.resultField },
      state.resultField,
    );
  }
  return equation(
    {
      op: "multiply",
      left: { op: "field", path: state.leftField },
      right: { op: "field", path: state.rightField },
    },
    { op: "field", path: state.resultField },
    state.resultField,
  );
}

function equation(left: RuleExpression, right: RuleExpression, field: string): DeterministicRule {
  return { kind: "equation", field, left, right, tolerance: 0.01 };
}

function splitValues(value: string): string[] {
  return value.split(/[，,]/).map((item) => item.trim()).filter(Boolean);
}

function summarizeRule(rule: DeterministicRule, labels: Map<string, string>): string {
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
  return `${expressionText(expression.left, labels)} ${symbols[expression.op]} ${expressionText(expression.right, labels)}`;
}
