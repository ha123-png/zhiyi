import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import {
  Check,
  ChevronDown,
  Download,
  EyeOff,
  FileText,
  LoaderCircle,
  Plus,
  Save,
  ShieldAlert,
  Table2,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import { getOriginalFileUrl, getTableExportUrl, getTemplates } from "../api";
import type {
  Confirmation,
  DocumentResult,
  Extraction,
  ExtractionResult,
  LineItem,
  Task,
  TemplateField,
  ExtractionTemplate,
  TemplateResult,
  TemplateValue,
} from "../types";
import { Icon } from "./Icon";
import { InputScopeDetails, PartialInputAction } from "./InputScopeDetails";

interface ReviewPreviewProps {
  confirmation: Confirmation | null;
  extraction: Extraction | null;
  task: Task | null;
  onConfirm: (expectedVersion: number) => Promise<void>;
  onSave: (
    result: ExtractionResult,
    expectedVersion: number,
    ignoredIndices: number[],
  ) => Promise<void>;
  onSelectTemplate: (taskId: string, templateId: string) => Promise<void>;
}

type HeaderKey = Exclude<keyof DocumentResult, "items">;

interface EditorHeaderField {
  key: HeaderKey;
  label: string;
  type: "text" | "number";
}

interface EditorItemField {
  key: keyof LineItem;
  label: string;
  type: "text" | "number";
  width: string;
}

// 明细列宽：仅布局手感，字段本身一律以模板为准
const ITEM_WIDTH_BY_KEY: Record<string, string> = {
  name: "180px",
  specification: "130px",
  unit: "80px",
  quantity: "96px",
  unit_price: "110px",
  amount: "110px",
  tax_rate: "90px",
  tax_amount: "110px",
};

/** 抬头字段按模板生成：字段、顺序、中文名、数字类型都以模板设置为准（所见即所得）；
 *  模板缺失（旧记录/模板已删）时不显示字段，也不回退发票全集，保证三处一致。 */
function editorHeaderFields(
  template: ExtractionTemplate | null | undefined,
): EditorHeaderField[] {
  const fields = template?.fields.filter(
    (field) => field.section === "header" && field.key,
  );
  if (fields && fields.length > 0) {
    const mapping = template?.output_mapping ?? {};
    return fields.map((field) => ({
      key: field.key as HeaderKey,
      label: mapping[field.key as string] ?? field.label,
      type: field.value_type === "number" ? "number" : "text",
    }));
  }
  return [];
}

/** 明细字段按模板生成：字段、顺序、中文名、数字类型都以模板设置为准。 */
function editorItemFields(
  template: ExtractionTemplate | null | undefined,
): EditorItemField[] {
  const fields = template?.fields.filter(
    (field) => field.section === "item" && field.key,
  );
  if (fields && fields.length > 0) {
    const mapping = template?.output_mapping ?? {};
    return fields.map((field) => ({
      key: field.key as keyof LineItem,
      label: mapping[field.key as string] ?? field.label,
      type: field.value_type === "number" ? "number" : "text",
      width: ITEM_WIDTH_BY_KEY[field.key as string] ?? "120px",
    }));
  }
  return [];
}

/** 新增明细行的空白对象：只含模板 item 字段（所见即所得，不塞模板没有的键）。 */
function blankItemFor(itemFields: EditorItemField[]): LineItem {
  return Object.fromEntries(
    itemFields.map((field) => [field.key, null]),
  ) as unknown as LineItem;
}

function cloneResult(result: ExtractionResult): ExtractionResult {
  return structuredClone(result);
}

function isDocumentResult(result: ExtractionResult): result is DocumentResult {
  return "document_type" in result;
}

function nullableNumber(value: string): number | null {
  if (value.trim() === "") {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function ReviewPreview({
  confirmation,
  extraction,
  onConfirm,
  onSelectTemplate,
  task,
  onSave,
}: ReviewPreviewProps) {
  const [draft, setDraft] = useState<ExtractionResult | null>(null);
  const [saving, setSaving] = useState(false);
  const [confirming, setConfirming] = useState(false);
  // 用户忽略的校验问题下标（被忽略的问题不再阻断确认，但仍记录）
  const [ignoredIndices, setIgnoredIndices] = useState<Set<number>>(new Set());
  const hasUnignoredErrors =
    extraction?.validation_issues.some(
      (issue, index) =>
        issue.severity === "error" && !ignoredIndices.has(index),
    ) ?? false;

  useEffect(() => {
    setDraft(extraction ? cloneResult(extraction.result) : null);
    setIgnoredIndices(
      new Set(
        (extraction?.validation_issues ?? [])
          .map((issue, index) => (issue.ignored ? index : -1))
          .filter((index) => index >= 0),
      ),
    );
  }, [extraction]);

  async function saveDraft() {
    if (!draft || !extraction) {
      return;
    }
    setSaving(true);
    try {
      await onSave(draft, extraction.review_version, [...ignoredIndices]);
    } finally {
      setSaving(false);
    }
  }

  async function confirmDraft() {
    if (!extraction) {
      return;
    }
    setConfirming(true);
    try {
      await onConfirm(extraction.review_version);
    } finally {
      setConfirming(false);
    }
  }

  return (
    <section className="review-panel" aria-labelledby="review-title">
      <div className="review-header">
        <div>
          <span className="eyebrow">
            {confirmation
              ? `已入表 · ${confirmation.table_name}表 · ${confirmation.row_count} 行`
              : extraction
              ? `校验问题 · 第 ${extraction.review_version} 版 · ${extraction.validation_issues.length} 处规则提示`
              : "提取数据"}
          </span>
          <h2 id="review-title">{task?.filename ?? "等待提取结果"}</h2>
        </div>
        <div className="review-actions">
          {confirmation && draft ? (
            <>
              <button
                className="button secondary"
                disabled={saving}
                onClick={() => void saveDraft()}
                type="button"
              >
                <Icon icon={Save} size={15} />
                {saving ? "同步中" : "保存并同步原始表"}
              </button>
              <a
                className="button primary"
                href={getTableExportUrl(confirmation.table_id)}
              >
                <Icon icon={Download} size={15} />
                下载 Excel
              </a>
            </>
          ) : extraction && draft ? (
            <>
              <button
                className="button secondary"
                disabled={saving}
                onClick={() => void saveDraft()}
                type="button"
              >
                <Icon icon={Save} size={15} />
                {saving ? "保存中" : "保存修改"}
              </button>
              <button
                className="button primary"
                disabled={confirming || saving || hasUnignoredErrors}
                onClick={() => void confirmDraft()}
                type="button"
              >
                <Icon icon={Check} size={15} />
                {confirming
                  ? "正在完成"
                  : hasUnignoredErrors
                    ? "修正错误后自动完成"
                    : "忽略提示并完成"}
              </button>
            </>
          ) : null}
        </div>
      </div>

      {!task ? (
        <PendingWorkspace message="拖入文件并匹配模板后，提取结果与校验问题将显示在此。" />
      ) : ["queued", "processing", "validating"].includes(task.status) ? (
        <PendingWorkspace
          message={
            task.status === "queued"
              ? "文件正在等待本地处理。"
              : "本地模型正在识别这份文件，请保持模型服务运行。"
          }
          task={task}
          working
        />
      ) : task.status === "waiting_for_template" ? (
        <PendingWorkspace task={task}>
          {task.pending_reason === "input_scope" ? <PartialInputAction task={task} /> : <TemplateChoice
            onSelect={(templateId) => onSelectTemplate(task.id, templateId)}
            task={task}
          />}
        </PendingWorkspace>
      ) : !extraction || !draft ? (
        <PendingWorkspace
          message={
            task.status === "failed"
              ? "处理失败，原文件仍已保存。请确认本地模型后重试。"
              : "文件已安全保存，尚未开始模型理解。"
          }
          task={task}
        />
      ) : (
        <div className="review-workspace">
          <div className="document-pane">
            <div className="pane-toolbar">
              <span>原文件</span>
              <span>{extraction.model_name} · <strong>{extraction.elapsed_seconds.toFixed(1)} 秒</strong></span>
            </div>
            <div className="document-canvas real-document">
              {task.content_type.startsWith("image/") ? (
                <img alt={task.filename} src={getOriginalFileUrl(task.id)} />
              ) : (
                <div className="review-empty">PDF 预览将在页面渲染接入后显示。</div>
              )}
            </div>
          </div>

          <div className="field-pane">
            {extraction.input_scope?.coverage === "partial" && <p className="small muted">仅处理部分内容</p>}
            <div className="pane-toolbar">
              <span>可编辑结果</span>
              <span>
                {confirmation
                  ? `已进入${confirmation.table_name}表 · ${confirmation.row_count} 行`
                  : extraction.document_kind === "custom"
                    ? extraction.template?.name ?? "自定义模板"
                    : extraction.document_kind === "invoice"
                    ? "发票"
                    : "送货单"}
              </span>
            </div>
            {isDocumentResult(draft) ? (
              <DocumentResultEditor
                headerFields={editorHeaderFields(extraction.template)}
                itemFields={editorItemFields(extraction.template)}
                issues={extraction.validation_issues}
                ignoredIndices={ignoredIndices}
                onToggleIgnore={(index) =>
                  setIgnoredIndices((current) => {
                    const next = new Set(current);
                    if (next.has(index)) {
                      next.delete(index);
                    } else {
                      next.add(index);
                    }
                    return next;
                  })
                }
                onChange={setDraft}
                result={draft}
              />
            ) : extraction.template ? (
              <CustomResultEditor
                disabled={false}
                onChange={setDraft}
                result={draft}
                templateFields={extraction.template.fields}
              />
            ) : (
              <div className="review-empty">没有找到这次处理使用的模板版本。</div>
            )}
            <InputScopeDetails scope={extraction.input_scope} label="来源与处理详情" />
          </div>
        </div>
      )}
    </section>
  );
}

function DocumentResultEditor({
  headerFields,
  itemFields,
  issues,
  ignoredIndices,
  onToggleIgnore,
  onChange,
  result,
}: {
  headerFields: EditorHeaderField[];
  itemFields: EditorItemField[];
  issues: Extraction["validation_issues"];
  ignoredIndices: Set<number>;
  onToggleIgnore: (index: number) => void;
  onChange: (result: DocumentResult) => void;
  result: DocumentResult;
}) {
  const [validationCollapsed, setValidationCollapsed] = useState(false);

  function updateHeader(
    key: HeaderKey,
    type: "text" | "number",
    value: string,
  ) {
    onChange({
      ...result,
      [key]: type === "number" ? nullableNumber(value) : value || null,
    });
  }

  function updateItem(
    index: number,
    key: keyof LineItem,
    type: "text" | "number",
    value: string,
  ) {
    onChange({
      ...result,
      items: result.items.map((item, itemIndex) =>
        itemIndex === index
          ? {
              ...item,
              [key]: type === "number" ? nullableNumber(value) : value || null,
            }
          : item
      ),
    });
  }

  function findItemIssue(index: number, key: keyof LineItem) {
    return issues.find((issue) =>
      issue.field === `items[${index}].${key}`
      || issue.field === `items.${index}.${key}`
    );
  }

  return (
    <fieldset className="extract-result-editor">
      <div className="extract-data-block">
      <section className="extract-header-card">
        <div className="extract-header-title">
          <span>
            <Icon icon={FileText} size={15} />
            抬头（单据头）
          </span>
          <small>{result.document_type || "未识别类型"}</small>
        </div>
        <div className="extract-header-grid">
          {headerFields.map((field) => {
            const issue = issues.find((item) => item.field === field.key);
            return (
              <label
                className={`extract-header-row ${issue ? "has-issue" : ""}`}
                key={field.key}
              >
                <span className="extract-header-label">{field.label}</span>
                <span className="extract-header-value">
                  <input
                    aria-label={field.label}
                    inputMode={field.type === "number" ? "decimal" : undefined}
                    onChange={(event) =>
                      updateHeader(field.key, field.type, event.target.value)
                    }
                    title={issue?.message}
                    type={field.type}
                    value={result[field.key] ?? ""}
                  />
                  {issue ? (
                    <small>
                      <Icon icon={TriangleAlert} size={13} />
                      {issue.message}
                    </small>
                  ) : null}
                </span>
              </label>
            );
          })}
        </div>
      </section>

      <section className="extract-items-section">
        <div className="extract-item-title">
          <span>
            <Icon icon={Table2} size={15} />
            明细（item）
          </span>
          <span className="extract-item-count">{result.items.length} 行</span>
          <button
            className="icon-button compact"
            onClick={() =>
              onChange({ ...result, items: [...result.items, blankItemFor(itemFields)] })
            }
            title="新增明细"
            type="button"
          >
            <Icon icon={Plus} size={14} />
          </button>
        </div>
        <div className="extract-item-table-wrap">
          <table className="extract-item-table">
            <colgroup>
              {itemFields.map((field) => (
                <col key={field.key} style={{ width: field.width }} />
              ))}
              <col style={{ width: "52px" }} />
            </colgroup>
            <thead>
              <tr>
                {itemFields.map((field) => (
                  <th key={field.key} scope="col">{field.label}</th>
                ))}
                <th aria-label="操作" scope="col" />
              </tr>
            </thead>
            <tbody>
              {result.items.map((item, index) => (
                <tr key={index}>
                  {itemFields.map((field) => {
                    const issue = findItemIssue(index, field.key);
                    return (
                      <td className={issue ? "has-issue" : ""} key={field.key}>
                        <input
                          aria-label={`第 ${index + 1} 行${field.label}`}
                          inputMode={field.type === "number" ? "decimal" : undefined}
                          onChange={(event) =>
                            updateItem(
                              index,
                              field.key,
                              field.type,
                              event.target.value,
                            )
                          }
                          title={issue?.message}
                          type={field.type}
                          value={item[field.key] ?? ""}
                        />
                      </td>
                    );
                  })}
                  <td className="extract-item-action">
                    <button
                      className="icon-button compact"
                      onClick={() =>
                        onChange({
                          ...result,
                          items: result.items.filter(
                            (_, itemIndex) => itemIndex !== index,
                          ),
                        })
                      }
                      title={`删除第 ${index + 1} 行`}
                      type="button"
                    >
                      <Icon icon={Trash2} size={13} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {result.items.length === 0 ? (
            <div className="extract-table-empty">暂未识别到明细，可手动新增一行。</div>
          ) : null}
        </div>
      </section>

      <div className={`validation-panel ${validationCollapsed ? "collapsed" : ""}`}>
        <button
          className="validation-panel-head"
          onClick={() => setValidationCollapsed((v) => !v)}
          type="button"
        >
          <Icon
            icon={issues.length ? ShieldAlert : Check}
            size={15}
            className={issues.length ? "is-danger" : "is-success"}
          />
          <span>校验结果</span>
          <span className={`validation-count ${issues.length === 0 ? "zero" : ""}`}>
            {issues.length}
          </span>
          {issues.length === 0 ? (
            <span className="validation-hint">全部通过，无异常</span>
          ) : null}
          <Icon icon={ChevronDown} className="validation-caret" size={15} />
        </button>
        {!validationCollapsed ? (
          <div className="validation-panel-body">
            {issues.length === 0 ? (
              <div className="validation-empty">
                <Icon icon={Check} size={18} className="is-success" />
                <span>本次提取校验全部通过</span>
              </div>
            ) : (
              issues.map((issue, idx) => {
                const rowMatch = issue.field.match(/^items[\[.](\d+)/);
                const isItem = Boolean(rowMatch);
                const rowIndex = rowMatch ? parseInt(rowMatch[1], 10) + 1 : 0;
                const rowTag = isItem ? `明细第 ${rowIndex} 行` : "抬头";
                const fieldKey = issue.field.replace(/^items[\[.]\d+[\].]\./, "");
                const fieldLabel =
                  headerFields.find((f) => f.key === fieldKey)?.label ??
                  itemFields.find((f) => f.key === fieldKey)?.label ??
                  fieldKey;
                return (
                  <div
                    className={`validation-item${ignoredIndices.has(idx) ? " is-ignored" : ""}`}
                    key={idx}
                  >
                    <span className="validation-row-tag">{rowTag}</span>
                    <span className="validation-field">{fieldLabel}</span>
                    <span className="validation-msg">{issue.message}</span>
                    {ignoredIndices.has(idx) && (
                      <span className="validation-ignored-tag">已忽略</span>
                    )}
                    <button
                      className="btn ghost sm"
                      title={
                        ignoredIndices.has(idx)
                          ? "恢复这条校验"
                          : "忽略这条校验，完成后不再阻断"
                      }
                      onClick={() => onToggleIgnore(idx)}
                      type="button"
                    >
                      <Icon icon={EyeOff} size={13} />
                      {ignoredIndices.has(idx) ? "恢复" : "忽略"}
                    </button>
                  </div>
                );
              })
            )}
          </div>
        ) : null}
      </div>
      </div>
    </fieldset>
  );
}

function PendingWorkspace({
  children,
  message,
  task,
  working = false,
}: {
  children?: ReactNode;
  message?: string;
  task?: Task;
  working?: boolean;
}) {
  return (
    <div className="review-workspace pending-workspace">
      <div className="field-pane">
        <div className="pane-toolbar">
          <span>提取数据</span>
          <span>表头与明细</span>
        </div>
        {children ?? (
          <div className="review-empty">
            {working ? <Icon icon={LoaderCircle} size={18} /> : null}
            {message}
          </div>
        )}
      </div>
      <div className="document-pane">
        <div className="pane-toolbar">
          <span>原文件</span>
          <span>{task?.filename ?? "等待上传"}</span>
        </div>
        <div className="document-canvas real-document">
          {task?.content_type.startsWith("image/") ? (
            <img alt={task.filename} src={getOriginalFileUrl(task.id)} />
          ) : (
            <div className="review-empty">
              {task ? "原文件已安全保存。" : "上传文件后，此处显示原文件预览。"}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function TemplateChoice({
  onSelect,
  task,
}: {
  onSelect: (templateId: string) => Promise<void>;
  task: Task;
}) {
  const [selecting, setSelecting] = useState("");
  const [availableTemplates, setAvailableTemplates] = useState<
    ExtractionTemplate[]
  >([]);
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    if (task.candidate_templates.length) {
      return;
    }
    void getTemplates()
      .then(setAvailableTemplates)
      .catch((error) =>
        setLoadError(error instanceof Error ? error.message : "模板读取失败。"),
      );
  }, [task.candidate_templates.length]);

  const choices = task.candidate_templates.length
    ? task.candidate_templates
    : availableTemplates.map((template) => ({
        id: template.id,
        version: template.version,
        name: template.name,
        description: template.description,
      }));
  return (
    <div className="template-choice">
      <span className="eyebrow">需要你确认一次</span>
      <h3>
        {task.candidate_templates.length
          ? "这份文件可能符合多个模板"
          : "没有找到足够匹配的模板"}
      </h3>
      <p>
        {task.candidate_templates.length
          ? "模型没有替你猜。请选择你希望得到的表格类型，原文件会继续处理。"
          : "模型没有强行套用。你可以从现有模板中手动选择，或先创建更合适的模板。"}
      </p>
      {loadError ? <div className="template-message" role="status">{loadError}</div> : null}
      <div className="template-choice-list">
        {choices.map((candidate) => (
          <button
            className="template-choice-card"
            disabled={Boolean(selecting)}
            key={`${candidate.id}-${candidate.version}`}
            onClick={async () => {
              setSelecting(candidate.id);
              try {
                await onSelect(candidate.id);
              } finally {
                setSelecting("");
              }
            }}
            type="button"
          >
            <strong>{candidate.name}</strong>
            <span>{candidate.description || "没有补充用途说明"}</span>
            <em>
              {selecting === candidate.id ? "正在继续处理..." : "使用这个模板"}
            </em>
          </button>
        ))}
      </div>
    </div>
  );
}

function CustomResultEditor({
  disabled,
  onChange,
  result,
  templateFields,
}: {
  disabled: boolean;
  onChange: (result: TemplateResult) => void;
  result: TemplateResult;
  templateFields: TemplateField[];
}) {
  const headerFields = templateFields.filter((field) => field.section === "header");
  const itemFields = templateFields.filter((field) => field.section === "item");

  function updateHeader(field: TemplateField, value: TemplateValue) {
    if (!field.key) {
      return;
    }
    onChange({
      ...result,
      header: { ...result.header, [field.key]: value },
    });
  }

  function updateItem(index: number, field: TemplateField, value: TemplateValue) {
    if (!field.key) {
      return;
    }
    onChange({
      ...result,
      items: result.items.map((item, itemIndex) =>
        itemIndex === index ? { ...item, [field.key as string]: value } : item,
      ),
    });
  }

  const blankItem = Object.fromEntries(
    itemFields.filter((field) => field.key).map((field) => [field.key, null]),
  );

  return (
    <fieldset className="field-list" disabled={disabled}>
      {headerFields.map((field) => field.key ? (
        <label className="field-row" key={field.key}>
          <span>{field.label}</span>
          <TemplateValueControl
            field={field}
            label={field.label}
            onChange={(value) => updateHeader(field, value)}
            value={result.header[field.key] ?? null}
          />
        </label>
      ) : null)}
      {itemFields.length ? (
        <div className="line-items">
          <div className="line-items-header">
            <strong>明细 · {result.items.length} 条</strong>
            <button
              className="icon-button compact"
              onClick={() => onChange({ ...result, items: [...result.items, blankItem] })}
              title="新增明细"
              type="button"
            >
              <Icon icon={Plus} size={14} />
            </button>
          </div>
          {result.items.map((item, index) => (
            <div className="line-item-editor" key={index}>
              <div className="line-item-title">
                <strong>第 {index + 1} 行</strong>
                <button
                  className="icon-button compact"
                  onClick={() =>
                    onChange({
                      ...result,
                      items: result.items.filter((_, itemIndex) => itemIndex !== index),
                    })
                  }
                  title={`删除第 ${index + 1} 行`}
                  type="button"
                >
                  <Icon icon={Trash2} size={13} />
                </button>
              </div>
              <div className="line-item-fields">
                {itemFields.map((field) => field.key ? (
                  <label key={field.key}>
                    <span>{field.label}</span>
                    <TemplateValueControl
                      field={field}
                      label={`第 ${index + 1} 行${field.label}`}
                      onChange={(value) => updateItem(index, field, value)}
                      value={item[field.key] ?? null}
                    />
                  </label>
                ) : null)}
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </fieldset>
  );
}

function TemplateValueControl({
  field,
  label,
  onChange,
  value,
}: {
  field: TemplateField;
  label: string;
  onChange: (value: TemplateValue) => void;
  value: TemplateValue;
}) {
  if (field.value_type === "boolean") {
    return (
      <select
        aria-label={label}
        onChange={(event) =>
          onChange(
            event.target.value === ""
              ? null
              : event.target.value === "true",
          )
        }
        value={value === null ? "" : String(value)}
      >
        <option value="">未填写</option>
        <option value="true">是</option>
        <option value="false">否</option>
      </select>
    );
  }
  return (
    <input
      aria-label={label}
      inputMode={field.value_type === "number" ? "decimal" : undefined}
      onChange={(event) =>
        onChange(
          field.value_type === "number"
            ? nullableNumber(event.target.value)
            : event.target.value || null,
        )
      }
      type={field.value_type === "number" ? "number" : "text"}
      value={value === null ? "" : String(value)}
    />
  );
}
