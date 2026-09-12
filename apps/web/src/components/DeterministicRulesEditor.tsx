import { summarizeRule } from "../ruleDescriptions";
import { PatternRulePreview } from "./PatternRulePreview";
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
  const [severity, setSeverity] = useState<"error" | "warning">("error");
  const [tolerance, setTolerance] = useState("0.01");
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
      if (rule.kind === "enum") {
        const type = available.find(field => pathFor(field) === fieldPath)?.value_type;
        rule.values = rule.values.map(value => type === "number" ? Number(value) : type === "boolean" ? value === "true" : value);
      }
      onChange([...rules, { ...rule, severity, ...(rule.kind === "equation" ? { tolerance: Number(tolerance) } : {}) }]);
    }
  }

  const selectedType = available.find(field => pathFor(field) === fieldPath)?.value_type;
  const validChoice = kind === "range" ? selectedType === "number"
    : kind === "enum" ? splitValues(values).every(value => selectedType === "number" ? Number.isFinite(Number(value)) : selectedType === "boolean" ? ["true", "false"].includes(value) : true)
    : true;
  const validTolerance = !["row_multiply", "sum"].includes(kind) || (tolerance.trim() !== "" && Number.isFinite(Number(tolerance)) && Number(tolerance) >= 0 && Number(tolerance) <= 1000000);
  const canAdd = validChoice && validTolerance && canBuildRule({
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
          <span>由程序检查必填、范围和计算关系，标出需要核对的结果。</span>
        </div>
        <span className="badge neutral">{rules.length} 条</span>
      </div>
      <p className="support">规则只标出需要核对的结果，不修改数据。除“不能为空”外，null 空值跳过检查；需要防止缺失时请同时添加必填规则。0 是有效数字。合计只累加已有数字，缺项请另设必填。</p>

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
        <div className="support">暂无校验规则。</div>
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
              <span className="support">文字区分大小写且精确匹配；数字字段填数字，是否字段填 true、false。</span>
            </label>
          ) : null}

          {kind === "pattern" ? (
            <div>
            <label>
              <span>格式表达式（正则）</span>
              <input className="form-input" disabled={disabled} maxLength={128} onChange={(e) => setPattern(e.target.value)} placeholder="例如：PO-\d{3}" value={pattern} />
            </label>
            <PatternRulePreview pattern={pattern} disabled={disabled} />
            </div>
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

          {["row_multiply", "sum"].includes(kind) ? <label><span>允许误差</span><input className="form-input" disabled={disabled} type="number" min="0" max="1000000" step="0.01" value={tolerance} onChange={event => setTolerance(event.target.value)} /><span className="support">两侧数值差的绝对值不超过此值即通过，不会改变或四舍五入原结果。</span></label> : null}
          <label><span>发现问题时</span><select className="form-select" disabled={disabled} value={severity} onChange={event => setSeverity(event.target.value as "error" | "warning")}><option value="error">错误：需要核对</option><option value="warning">提醒：建议核对</option></select></label>
          {!validChoice || !validTolerance || (kind === "range" && minimum !== "" && maximum !== "" && Number(minimum) > Number(maximum)) ? <p className="support" role="alert">请检查字段类型、允许值、范围上下限或允许误差。</p> : null}
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
  if (state.kind === "range") return Boolean(state.fieldPath && (state.minimum || state.maximum))
    && [state.minimum, state.maximum].every(value => value === "" || Number.isFinite(Number(value)))
    && !(state.minimum !== "" && state.maximum !== "" && Number(state.minimum) > Number(state.maximum));
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
