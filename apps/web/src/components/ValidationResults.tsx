import { CheckCircle2, EyeOff, ShieldAlert } from "lucide-react";
import { CollapseChevron } from "./CollapseChevron";
import { Icon } from "./Icon";
import type { ExtractionTemplate, TemplateValue } from "../types";
import { summarizeRule } from "../ruleDescriptions";
import "./validation-results.css";

export interface ValidationResultItem {
  index: number;
  row: string;
  field: string;
  message: string;
  ignored: boolean;
  canLocate?: boolean;
  notChecked?: boolean;
  advisory?: boolean;
  severity?: string;
  rawValue?: TemplateValue;
  valueType?: string;
  code?: string;
}

const fieldInputHints: Record<string, string> = {
  date: "请按原件填写完整日期（如 2026-11-20）。",
  number: "请按原件填写数字（如 1234.56，保留可精确表示的位数）。",
  integer: "请按原件填写整数（如 1234）。",
  boolean: "请按原件填写「是」或「否」。",
  text: "请按原件填写，保留编号前导零等原有格式。",
};

export function ValidationResults({ issues, template, disabled = false, ignoreDisabled = false, onToggleIgnored, onLocate, locateLabel = "定位相关字段", open = true, onToggle, contentId }: {
  issues: ValidationResultItem[];
  template?: ExtractionTemplate | null;
  disabled?: boolean;
  ignoreDisabled?: boolean;
  onToggleIgnored?: (index: number) => void;
  onLocate?: (index: number) => void;
  locateLabel?: string;
  open?: boolean;
  onToggle?: () => void;
  contentId?: string;
}) {
  const fieldLabels = new Map(template?.fields.map(field => [
    `${field.section === "header" ? "header" : "items[]"}.${field.key}`, field.label,
  ]));
  function issueText(issue: ValidationResultItem) {
    if (issue.rawValue != null && issue.valueType) return fieldInputHints[issue.valueType] ?? issue.message;
    const ruleIndex = /^template_rule_(\d+)$/.exec(issue.code ?? "");
    const rule = ruleIndex ? template?.deterministic_rules?.[Number(ruleIndex[1]) - 1] : undefined;
    if (rule?.kind === "pattern") return `${issue.message} 需符合模板格式「${rule.pattern}」。`;
    if (issue.notChecked) return `${issue.message} 如需校验，请补齐参与计算的数字。`;
    if (rule?.kind === "equation") return `${issue.message} 请核对 ${summarizeRule(rule, fieldLabels)}。`;
    return issue.message;
  }
  const skippedCount = issues.filter(issue => issue.notChecked).length;
  const ignoredCount = issues.filter(issue => issue.ignored && !issue.notChecked).length;
  const pendingCount = issues.length - ignoredCount - skippedCount;
  const summary = [
    pendingCount ? `${pendingCount} 处需要核对` : "",
    ignoredCount ? `${ignoredCount} 处已忽略` : "",
    skippedCount ? `${skippedCount} 处未校验` : "",
  ].filter(Boolean).join(" · ") || "校验通过";
  const heading = <>
    <Icon icon={issues.length ? ShieldAlert : CheckCircle2} size={15} className={pendingCount ? "validation-alert-icon" : undefined} />
    <span>{summary}</span>
    {onToggle && <CollapseChevron expanded={open} size={15} className="validation-caret" />}
  </>;
  return <section className="validation-panel" aria-label="校验结果">
    {onToggle ? <button className="validation-panel-head" type="button" aria-label={`校验结果：${summary}`}
      aria-expanded={open} aria-controls={contentId} onClick={onToggle}>{heading}</button>
      : <div className="validation-panel-head">{heading}</div>}
    <div id={contentId} className={`collapse${open ? " open" : ""}`} inert={!open} aria-hidden={!open}>
      <div className="collapse-content"><ul className="validation-panel-body">
        {issues.map(issue => <li className={`validation-item${issue.ignored ? " is-ignored" : ""}`} key={issue.index}>
          <div className="validation-description">
            {(issue.row || issue.field) && <strong>{issue.row}{issue.row && issue.field && " · "}{issue.field}</strong>}
            {issue.severity && <span className="validation-severity">{issue.notChecked ? " · 未校验" : issue.severity === "error" && !issue.advisory ? " · 阻断" : " · 提醒"}</span>}
            {issue.rawValue != null && <span> · 模型原值「{String(issue.rawValue)}」</span>}
            <span> · {issueText(issue)}</span>
            {issue.ignored && !issue.notChecked && <span className="validation-ignored-tag"> · 已忽略</span>}
          </div>
          <div className="validation-actions">
            {onToggleIgnored && !issue.notChecked && issue.rawValue == null && <button className="btn ghost xs" type="button" disabled={disabled || ignoreDisabled}
              title={issue.ignored ? "恢复这条校验" : "忽略这条校验，保存后不再阻断确认"}
              onClick={() => onToggleIgnored(issue.index)}><Icon icon={EyeOff} size={13} />{issue.ignored ? "恢复" : "忽略"}</button>}
            {onLocate && issue.canLocate !== false && <button className="btn ghost xs" type="button" disabled={disabled}
              aria-label={locateLabel} onClick={event => { event.stopPropagation(); onLocate(issue.index); }}>定位</button>}
          </div>
        </li>)}
      </ul></div>
    </div>
  </section>;
}
