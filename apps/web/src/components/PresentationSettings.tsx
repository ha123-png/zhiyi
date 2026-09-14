import type { TemplateBehavior, TemplateField } from "../types";
import { InfoHint } from "./InfoHint";
import "./cards.css";

export function defaultBehavior(): TemplateBehavior {
  return { presentation: { mode: "table", title_field: null, primary_fields: [], collapsed_fields: [] }, requires_complete_input: true, suggest_filename: false };
}

export function TemplateNameSettings({ value, disabled, onChange }: { value?: TemplateBehavior; disabled?: boolean; onChange: (value: TemplateBehavior) => void }) {
  const behavior = value ?? defaultBehavior();
  return <div className="form-field template-feature">
    <label className="form-label"><input type="checkbox" checked={behavior.suggest_filename} disabled={disabled} onChange={(event) => onChange({ ...behavior, suggest_filename: event.target.checked })} /> 文件命名</label>
    {behavior.suggest_filename && <>
      <select className="form-select" aria-label="文件命名方式" disabled={disabled} value={behavior.filename_mode ?? "ai"} onChange={event => onChange({ ...behavior, filename_mode: event.target.value as "ai" | "fixed" })}>
        <option value="ai">AI 命名 · 根据内容建议</option>
        <option value="fixed">固定规则 · 日期_模板_序号</option>
      </select>
      <p className="support">{behavior.filename_mode === "fixed" ? "例如 2026-09-14_发票_001.jpg。使用导入日期，同日同模板递增；重试沿用名称。" : "根据内容自动命名，可在额外要求中表达命名偏好；已有意义的原名可保留。"} 上传原名始终保留。</p>
    </>}
  </div>;
}

export function presentationFields(fields: TemplateField[]): TemplateField[] {
  return fields.map((field, index) => ({ ...field, key: field.key || `presentation_field_${index}` }));
}

export function cleanPresentation(value: TemplateBehavior | undefined, fields: TemplateField[]): TemplateBehavior | undefined {
  if (!value) return undefined;
  const refs = new Set(fields.map((f) => `${f.section}.${f.key}`));
  return { ...value, presentation: { ...value.presentation,
    title_field: value.presentation.title_field && refs.has(value.presentation.title_field) ? value.presentation.title_field : null,
    primary_fields: value.presentation.primary_fields.filter((v) => refs.has(v)),
    collapsed_fields: value.presentation.collapsed_fields.filter((v) => refs.has(v)),
  } };
}

export function PresentationSettings({ value, disabled = false, onChange }: {
  value?: TemplateBehavior; disabled?: boolean; onChange: (value: TemplateBehavior) => void;
}) {
  const behavior = value ?? defaultBehavior();
  const presentation = behavior.presentation;
  return <div className="presentation-settings-inline">
    <div className="form-field">
      <div className="form-label">默认展示 <InfoHint label="默认展示说明" text="同一份数据，可在数据仓库切换表格与卡片。整份文件的信息共用，每条明细各自呈现。" /></div>
        <select aria-label="默认展示" className="form-select" disabled={disabled} value={presentation.mode}
          onChange={(event) => onChange({ ...behavior, presentation: { ...presentation, mode: event.target.value as "table" | "card" } })}>
          <option value="table">表格 · 适合核对、汇总数字和明细</option>
          <option value="card">卡片 · 适合逐条阅读内容</option>
        </select>
    </div>
  </div>;
}
