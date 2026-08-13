import { useEffect, useRef, useState } from "react";
import {
  Archive,
  ChevronDown,
  ChevronRight,
  Copy,
  Download,
  FileText,
  Info,
  Loader2,
  Plus,
  RotateCcw,
  Save,
  Sparkles,
  Trash2,
  Upload,
  UploadCloud,
  X,
} from "lucide-react";
import {
  archiveTemplate,
  copyTemplate,
  createTemplate,
  deleteTemplate,
  generateTemplateDraft,
  getModelProfiles,
  getTemplates,
  restoreTemplate,
  updateTemplate,
} from "../api";
import type {
  AiDraftField,
  AiTemplateDraft,
  ExtractionTemplate,
  ModelProfile,
  TemplateDraft,
  TemplateField,
} from "../types";
import { DeterministicRulesEditor } from "./DeterministicRulesEditor";
import { Icon } from "./Icon";
import { Toast, useToast } from "./Toast";

const emptyField: TemplateField = {
  label: "",
  section: "header",
  example: "",
  instructions: "",
  value_type: "text",
};

const emptyDraft: TemplateDraft = {
  name: "",
  description: "",
  extra_instructions: "",
  fields: [{ ...emptyField }],
  validation_rules: [],
  deterministic_rules: [],
  output_mapping: {},
};

const fieldTypeOptions: { value: TemplateField["value_type"]; label: string }[] = [
  { value: "text", label: "text" },
  { value: "number", label: "number" },
  { value: "date", label: "date" },
  { value: "boolean", label: "boolean" },
];

export function TemplatesPage() {
  const [templates, setTemplates] = useState<ExtractionTemplate[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [draft, setDraft] = useState<TemplateDraft>(emptyDraft);
  const [version, setVersion] = useState(0);
  const [isSystem, setIsSystem] = useState(false);
  const [isActive, setIsActive] = useState(true);
  const [isNew, setIsNew] = useState(true);
  const [message, setMessage] = useState("正在读取模板...");
  const [saving, setSaving] = useState(false);
  const { toast, notify, clear } = useToast();
  const [aiOpen, setAiOpen] = useState(false);
  const [expandedField, setExpandedField] = useState<number | null>(null);
  const importInput = useRef<HTMLInputElement>(null);

  // AI 生成模板弹窗
  const [aiFiles, setAiFiles] = useState<File[]>([]);
  const [aiRequirement, setAiRequirement] = useState("");
  const [aiGenerating, setAiGenerating] = useState(false);
  const [aiElapsed, setAiElapsed] = useState(0);
  const [aiDraft, setAiDraft] = useState<AiTemplateDraft | null>(null);
  const [aiError, setAiError] = useState<string | null>(null);
  const [aiDraftName, setAiDraftName] = useState("");
  const [aiDraftDescription, setAiDraftDescription] = useState("");
  const [aiDraftFields, setAiDraftFields] = useState<AiDraftField[]>([]);
  const [aiWithExamples, setAiWithExamples] = useState(true);
  const aiFileInput = useRef<HTMLInputElement>(null);
  const [aiProfiles, setAiProfiles] = useState<ModelProfile[]>([]);
  const [aiProfileId, setAiProfileId] = useState("");
  const [aiProfilesLoaded, setAiProfilesLoaded] = useState(false);

  // 打开弹窗时加载全局模型方案（AI 生成可独立选择，与任务激活方案解耦）
  useEffect(() => {
    if (!aiOpen || aiProfilesLoaded) return;
    getModelProfiles()
      .then((profiles) => {
        const usable = profiles.filter((profile) => !profile.is_archived);
        setAiProfiles(usable);
        const active = usable.find((profile) => profile.is_active);
        setAiProfileId(active?.id ?? usable[0]?.id ?? "");
        setAiProfilesLoaded(true);
      })
      .catch(() => setAiProfilesLoaded(true));
  }, [aiOpen, aiProfilesLoaded]);

  useEffect(() => {
    void refreshTemplates();
  }, []);

  // AI 生成中的等待计时（每秒跳动）
  useEffect(() => {
    if (!aiGenerating) return;
    setAiElapsed(0);
    const timer = window.setInterval(() => {
      setAiElapsed((seconds) => seconds + 1);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [aiGenerating]);

  async function refreshTemplates(preferredId?: string) {
    try {
      const next = await getTemplates(true);
      setTemplates(next);
      const selected =
        next.find((template) => template.id === preferredId)
        ?? next.find((template) => template.id === selectedId)
        ?? next[0];
      if (selected) {
        selectTemplate(selected);
      }
      setMessage("");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "模板读取失败。");
    }
  }

  function selectTemplate(template: ExtractionTemplate) {
    setSelectedId(template.id);
    setVersion(template.version);
    setIsSystem(template.is_system);
    setIsActive(template.is_active ?? true);
    setIsNew(false);
    setDraft(toDraft(template));
    setExpandedField(null);
    setMessage("");
  }

  function startNew() {
    setSelectedId("");
    setVersion(0);
    setIsSystem(false);
    setIsActive(true);
    setIsNew(true);
    setDraft({
      ...emptyDraft,
      fields: [{ ...emptyField }],
      output_mapping: {},
    });
    setExpandedField(null);
    setMessage("给模板起个名字，再写下你想从每份文件中得到什么。");
  }

  function updateField(index: number, patch: Partial<TemplateField>) {
    setDraft((current) => ({
      ...current,
      fields: current.fields.map((field, fieldIndex) =>
        fieldIndex === index ? { ...field, ...patch } : field,
      ),
    }));
  }

  function removeField(index: number) {
    setDraft((current) => ({
      ...current,
      fields: current.fields.filter((_, fieldIndex) => fieldIndex !== index),
    }));
    setExpandedField(null);
  }

  async function handleCopy(templateId?: string) {
    const id = templateId ?? selectedId;
    if (!id) {
      return;
    }
    setSaving(true);
    try {
      const copied = await copyTemplate(id);
      await refreshTemplates(copied.id);
      notify("已创建可编辑副本，内置模板保持不变。", "success");
    } catch (error) {
      notify(error instanceof Error ? error.message : "复制模板失败。", "error");
    } finally {
      setSaving(false);
    }
  }

  async function handleArchive(templateId?: string) {
    const id = templateId ?? selectedId;
    if (!id) return;
    setSaving(true);
    try {
      const archived = await archiveTemplate(id);
      await refreshTemplates(archived.id);
      notify("模板已停用；旧任务和旧数据仍可追溯，可随时恢复。", "success");
    } catch (error) {
      notify(error instanceof Error ? error.message : "模板停用失败。", "error");
    } finally {
      setSaving(false);
    }
  }

  async function handleRestore(templateId?: string) {
    const id = templateId ?? selectedId;
    if (!id) return;
    setSaving(true);
    try {
      const restored = await restoreTemplate(id);
      await refreshTemplates(restored.id);
      notify("模板已恢复，可以继续用于新文件。", "success");
    } catch (error) {
      notify(error instanceof Error ? error.message : "模板恢复失败。", "error");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!selectedId) return;
    const confirmed = window.confirm(
      "确定删除这个模板吗？删除后不可恢复。\n已被任务或数据表引用的模板会被系统拒绝，只能停用。",
    );
    if (!confirmed) return;
    setSaving(true);
    try {
      await deleteTemplate(selectedId);
      notify("模板已删除。", "success");
      setSelectedId("");
      startNew();
      await refreshTemplates();
    } catch (error) {
      notify(error instanceof Error ? error.message : "删除模板失败。", "error");
    } finally {
      setSaving(false);
    }
  }

  // ---- AI 生成模板弹窗 ----

  function resetAiState() {
    setAiFiles([]);
    setAiRequirement("");
    setAiGenerating(false);
    setAiElapsed(0);
    setAiDraft(null);
    setAiError(null);
    setAiDraftName("");
    setAiDraftDescription("");
    setAiDraftFields([]);
    setAiWithExamples(true);
  }

  function handleAiClose() {
    if (aiGenerating) return; // 生成中禁止关闭
    if (aiDraft && !window.confirm("模板尚未保存，确定不保存并关闭？")) return;
    setAiOpen(false);
    resetAiState();
  }

  function handleAiFilesPicked(files: FileList | File[]) {
    const picked = Array.from(files).filter(
      (file) =>
        /\.(pdf|png|jpe?g|webp)$/i.test(file.name) &&
        !aiFiles.some((existing) => existing.name === file.name),
    );
    setAiFiles((current) => [...current, ...picked]);
  }

  function handleAiRemoveFile(index: number) {
    setAiFiles((current) => current.filter((_, i) => i !== index));
  }

  async function handleAiGenerate() {
    if (aiGenerating) return;
    if (!aiRequirement.trim()) {
      setAiError("请先填写需求描述。");
      return;
    }
    setAiError(null);
    setAiGenerating(true);
    try {
      const draft = await generateTemplateDraft(
        aiFiles,
        aiRequirement.trim(),
        aiProfileId || undefined,
        aiWithExamples,
      );
      setAiDraft(draft);
      setAiDraftName(draft.name || "");
      setAiDraftDescription(draft.description || "");
      setAiDraftFields(
        draft.fields.map((field) => ({ ...field, example: field.example ?? "" })),
      );
    } catch (error) {
      setAiError(error instanceof Error ? error.message : "AI 生成失败，请重试。");
    } finally {
      setAiGenerating(false);
    }
  }

  function updateAiField(index: number, patch: Partial<AiDraftField>) {
    setAiDraftFields((current) =>
      current.map((field, i) => (i === index ? { ...field, ...patch } : field)),
    );
  }

  /** 内置模板占用"发票/送货单"等常用名：AI 生成名撞名时自动追加后缀，保证保存总能成功。 */
  function uniqueTemplateName(base: string): string {
    const names = new Set(
      templates.filter((t) => t.is_active !== false).map((t) => t.name),
    );
    if (!names.has(base)) return base;
    let candidate = `${base}（AI 生成）`;
    let suffix = 2;
    while (names.has(candidate)) {
      candidate = `${base}（AI 生成）${suffix}`;
      suffix += 1;
    }
    return candidate;
  }

  async function handleAiSave() {
    if (!aiDraftName.trim()) {
      setAiError("请先填写模板名称。");
      return;
    }
    const emptyLabel = aiDraftFields.some((field) => !field.label.trim());
    if (aiDraftFields.length === 0 || emptyLabel) {
      setAiError(emptyLabel ? "请填写所有字段名称，或删除空字段。" : "至少需要一个字段。");
      return;
    }
    const requestedName = aiDraftName.trim();
    const finalName = uniqueTemplateName(requestedName);
    setSaving(true);
    setAiError(null);
    try {
      const saved = await createTemplate({
        name: finalName,
        description: aiDraftDescription.trim() || aiDraft?.description || "",
        extra_instructions: "",
        fields: aiDraftFields.map((field) => ({
          label: field.label,
          section: field.section,
          example: field.example ?? "",
          instructions: "",
          value_type: field.value_type,
        })),
        validation_rules: [],
        deterministic_rules: [],
        output_mapping: {},
      });
      await refreshTemplates(saved.id);
      notify(
        finalName === requestedName
          ? "模板已保存。"
          : `“${requestedName}”已存在，已保存为“${finalName}”。`,
        "success",
      );
      setAiOpen(false);
      resetAiState();
    } catch (error) {
      setAiError(error instanceof Error ? error.message : "保存模板失败。");
    } finally {
      setSaving(false);
    }
  }

  async function handleSave() {
    setSaving(true);
    try {
      const saved = isNew
        ? await createTemplate(draft)
        : await updateTemplate(selectedId, version, draft);
      await refreshTemplates(saved.id);
      notify(
        isNew
          ? "模板已创建。接入文件处理将在智能匹配阶段完成。"
          : `已保存为第 ${saved.version} 版，旧任务和旧数据不受影响。`,
        "success",
      );
    } catch (error) {
      notify(error instanceof Error ? error.message : "模板保存失败。", "error");
    } finally {
      setSaving(false);
    }
  }

  function handleExport() {
    if (!draft.name.trim()) {
      notify("当前模板还没有名称，不能导出。", "error");
      return;
    }
    const payload = JSON.stringify(
      {
        format: "document-pipeline-template",
        version: 1,
        template: draft,
      },
      null,
      2,
    );
    const url = URL.createObjectURL(
      new Blob([payload], { type: "application/json;charset=utf-8" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `${draft.name}.template.json`;
    link.click();
    URL.revokeObjectURL(url);
    notify("模板已导出为 JSON 文件。", "success");
  }

  async function handleImport(file: File) {
    setSaving(true);
    try {
      const parsed = JSON.parse(await file.text()) as {
        template?: TemplateDraft;
      } & Partial<TemplateDraft>;
      const imported = parsed.template ?? parsed;
      if (
        typeof imported.name !== "string"
        || !Array.isArray(imported.fields)
        || imported.fields.length === 0
      ) {
        throw new Error("这个文件不是有效的字段模板。");
      }
      const saved = await createTemplate({
        name: imported.name,
        description: imported.description ?? "",
        extra_instructions: imported.extra_instructions ?? "",
        fields: imported.fields,
        validation_rules: imported.validation_rules ?? [],
        deterministic_rules: imported.deterministic_rules ?? [],
        output_mapping: imported.output_mapping ?? {},
      });
      await refreshTemplates(saved.id);
      notify(`已导入模板“${saved.name}”。`, "success");
    } catch (error) {
      notify(error instanceof Error ? error.message : "模板导入失败。", "error");
    } finally {
      setSaving(false);
      if (importInput.current) {
        importInput.current.value = "";
      }
    }
  }

  const canSave =
    !isSystem
    && isActive
    && draft.name.trim().length > 0
    && draft.fields.length > 0
    && draft.fields.every((field) => field.label.trim().length > 0);

  const typeLabel = isSystem ? "内置" : isNew ? "新建" : `第 ${version} 版`;
  const supportText = `${typeLabel}模板${isActive ? "" : " · 已停用"} · ${draft.fields.length} 个字段 · ${draft.description || "尚未填写用途"}`;
  const editorDisabled = isSystem || saving || !isActive;

  return (
    <section className="templates-view">
      <header className="page-header">
        <span className="eyebrow">配置</span>
        <h1>模板管理</h1>
        <p className="support">
          管理提取模板 · 保存会创建新版本，已处理文件和表格不受影响
        </p>
      </header>

      <div className="template-layout">
        {/* Left card — list */}
        <div className="card flush">
          <div className="panel-title">
            <div>
              <h3>所有模板</h3>
              <div className="support">共 {templates.length} 个模板</div>
            </div>
          </div>

          <div className="tpl-side-actions">
            <button
              className="btn primary sm"
              onClick={() => {
                // 每次打开都从空白开始，避免残留上次生成的数据
                resetAiState();
                setAiOpen(true);
              }}
              title="上传样例文件（可选）+ 需求描述，AI 生成字段草稿"
              type="button"
            >
              <Icon icon={Sparkles} size={13} />
              AI 生成模板
            </button>
            <button
              className="btn secondary sm"
              onClick={startNew}
              type="button"
            >
              <Icon icon={Plus} size={13} />
              新建空白模板
            </button>
            <div className="tpl-io-row">
              <button
                className="btn ghost xs"
                disabled={saving}
                onClick={() => importInput.current?.click()}
                type="button"
              >
                <Icon icon={Upload} size={12} />
                导入
              </button>
              <button
                className="btn ghost xs"
                disabled={!draft.name.trim()}
                onClick={handleExport}
                type="button"
              >
                <Icon icon={Download} size={12} />
                导出
              </button>
            </div>
            <input
              accept="application/json,.json"
              className="visually-hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) {
                  void handleImport(file);
                }
              }}
              ref={importInput}
              type="file"
            />
          </div>

          <div className="template-list">
            {templates.map((template) => {
              const active = selectedId === template.id && !isNew;
              return (
                <div
                  className={`template-list-item ${active ? "active" : ""} ${template.is_active === false ? "inactive" : ""}`}
                  key={template.id}
                  onClick={() => selectTemplate(template)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      selectTemplate(template);
                    }
                  }}
                >
                  <Icon icon={FileText} size={14} />
                  <div className="tpl-item-main">
                    <div className="tpl-item-name">{template.name}</div>
                    <div className="tpl-item-meta">
                      {template.fields.length} 字段 · {template.is_system ? "内置" : `第 ${template.version} 版`}
                    </div>
                  </div>
                  <div
                    className="tpl-list-actions"
                    onClick={(event) => event.stopPropagation()}
                  >
                    <button
                      className="tpl-act-btn"
                      disabled={saving || template.is_active === false}
                      onClick={() => void handleCopy(template.id)}
                      title="复制"
                      type="button"
                    >
                      <Icon icon={Copy} size={12} />
                    </button>
                    {!template.is_system ? (
                      <button
                        className="tpl-act-btn"
                        disabled={saving}
                        onClick={() => template.is_active === false
                          ? void handleRestore(template.id)
                          : void handleArchive(template.id)}
                        title={template.is_active === false ? "恢复" : "停用"}
                        type="button"
                      >
                        <Icon icon={template.is_active === false ? RotateCcw : Archive} size={12} />
                      </button>
                    ) : null}
                  </div>
                  {template.is_system ? <span className="tpl-badge">内置</span> : null}
                  {template.is_active === false ? <span className="tpl-badge">已停用</span> : null}
                </div>
              );
            })}
            {templates.length === 0 ? (
              <div className="tpl-item-meta" style={{ padding: "16px 10px", textAlign: "center" }}>
                暂无模板
              </div>
            ) : null}
          </div>
        </div>

        {/* Right card — detail editor */}
        <div className="card">
          <div className="panel-title">
            <div>
              <h3>{draft.name || "未命名模板"}</h3>
              <div className="support">{supportText}</div>
            </div>
            <div className="tpl-detail-actions">
              {!isSystem && !isNew ? (
                <button
                  className="btn ghost"
                  disabled={saving}
                  onClick={() => isActive ? void handleArchive() : void handleRestore()}
                  title={isActive ? "停用模板" : "恢复模板"}
                  type="button"
                >
                  <Icon icon={isActive ? Archive : RotateCcw} size={13} />
                  {isActive ? "停用" : "恢复"}
                </button>
              ) : null}
              {!isSystem && !isNew ? (
                <button
                  className="btn danger-soft"
                  disabled={saving}
                  onClick={() => void handleDelete()}
                  title="物理删除；已被任务或数据表引用的模板会被拒绝，只能停用"
                  type="button"
                >
                  <Icon icon={Trash2} size={13} />
                  删除
                </button>
              ) : null}
              {!isSystem ? (
                <button
                  className="btn"
                  disabled={saving || !isActive || (!isNew && !selectedId)}
                  onClick={() => void handleCopy()}
                  type="button"
                >
                  <Icon icon={Copy} size={13} />
                  复制
                </button>
              ) : null}
              {isSystem ? (
                <button
                  className="btn primary"
                  disabled={saving}
                  onClick={() => void handleCopy()}
                  type="button"
                >
                  <Icon icon={Copy} size={13} />
                  复制后编辑
                </button>
              ) : (
                <button
                  className="btn primary"
                  disabled={!canSave || saving}
                  onClick={() => void handleSave()}
                  type="button"
                >
                  <Icon icon={Save} size={13} />
                  {isNew ? "创建模板" : "保存"}
                </button>
              )}
            </div>
          </div>

          {message ? <div className="template-message" role="status">{message}</div> : null}

          <div className="form-field tpl-name-field">
            <label className="form-label" htmlFor="tpl-name">模板名称</label>
            <input
              className="form-input"
              disabled={editorDisabled}
              id="tpl-name"
              maxLength={128}
              onChange={(event) =>
                setDraft((current) => ({ ...current, name: event.target.value }))
              }
              placeholder="例如：门店送货单"
              value={draft.name}
            />
          </div>

          <div className="form-field tpl-desc-field">
            <label className="form-label" htmlFor="tpl-desc">用途说明</label>
            <input
              className="form-input"
              disabled={editorDisabled}
              id="tpl-desc"
              maxLength={512}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  description: event.target.value,
                }))
              }
              placeholder="例如：整理各门店收到的送货单，按供应商和商品查看"
              value={draft.description}
            />
          </div>

          <div className="field-list">
            <div className="field-row header">
              <span>字段名</span>
              <span>类型</span>
              <span>分区</span>
              <span>操作</span>
            </div>
            {draft.fields.map((field, index) => {
              const expanded = expandedField === index;
              return (
                <div key={`${field.section}:${field.key ?? `new-${index}`}`}>
                  <div className={`field-row ${expanded ? "has-sub" : ""}`}>
                    <input
                      aria-label="字段名"
                      className="form-input"
                      disabled={editorDisabled}
                      onChange={(event) => updateField(index, { label: event.target.value })}
                      placeholder="字段名，例如：供应商名称"
                      value={field.label}
                    />
                    <select
                      aria-label="字段类型"
                      className="form-input"
                      disabled={editorDisabled}
                      onChange={(event) =>
                        updateField(index, {
                          value_type: event.target.value as TemplateField["value_type"],
                        })
                      }
                      value={field.value_type}
                    >
                      {fieldTypeOptions.map((opt) => (
                        <option key={opt.value} value={opt.value}>{opt.label}</option>
                      ))}
                    </select>
                    <select
                      aria-label="字段分区"
                      className="form-input"
                      disabled={editorDisabled}
                      onChange={(event) =>
                        updateField(index, {
                          section: event.target.value as TemplateField["section"],
                        })
                      }
                      value={field.section}
                    >
                      <option value="header">表头</option>
                      <option value="item">明细</option>
                    </select>
                    <div style={{ display: "flex", gap: 2, justifyContent: "flex-end" }}>
                      <button
                        aria-label={expanded ? "收起示例" : "展开示例"}
                        className="tpl-act-btn"
                        onClick={() => setExpandedField(expanded ? null : index)}
                        title={expanded ? "收起" : "示例与说明"}
                        type="button"
                      >
                        <Icon
                          icon={ChevronRight}
                          size={14}
                          style={{
                            transform: expanded ? "rotate(90deg)" : "none",
                            transition: "transform var(--dur-fast) var(--ease-out)",
                          }}
                        />
                      </button>
                      {!isSystem && draft.fields.length > 1 ? (
                        <button
                          aria-label={`删除字段 ${field.label || index + 1}`}
                          className="tpl-act-btn"
                          onClick={() => removeField(index)}
                          title="删除字段"
                          type="button"
                        >
                          <Icon icon={X} size={13} />
                        </button>
                      ) : null}
                    </div>
                  </div>
                  {expanded ? (
                    <div className="field-row-sub">
                      <div className="form-field">
                        <label className="form-label" htmlFor={`field-example-${index}`}>示例（可选）</label>
                        <input
                          aria-label={`字段 ${index + 1} 示例`}
                          className="form-input"
                          disabled={editorDisabled}
                          id={`field-example-${index}`}
                          onChange={(event) => updateField(index, { example: event.target.value })}
                          placeholder="例如：XX商贸"
                          value={field.example}
                        />
                      </div>
                      <div className="form-field">
                        <label className="form-label" htmlFor={`field-instructions-${index}`}>说明（可选）</label>
                        <input
                          aria-label={`字段 ${index + 1} 说明`}
                          className="form-input"
                          disabled={editorDisabled}
                          id={`field-instructions-${index}`}
                          onChange={(event) =>
                            updateField(index, { instructions: event.target.value })
                          }
                          placeholder="有特殊含义时再说明"
                          value={field.instructions}
                        />
                      </div>
                    </div>
                  ) : null}
                </div>
              );
            })}
            {!isSystem ? (
              <button
                className="btn ghost sm tpl-add-field"
                onClick={() =>
                  setDraft((current) => ({
                    ...current,
                    fields: [...current.fields, { ...emptyField }],
                  }))
                }
                type="button"
              >
                <Icon icon={Plus} size={13} />
                添加字段
              </button>
            ) : null}
          </div>

          <div className="form-field tpl-extra-field">
            <label className="form-label" htmlFor="tpl-extra">额外提示词</label>
            <textarea
              className="form-textarea"
              disabled={editorDisabled}
              id="tpl-extra"
              maxLength={4000}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  extra_instructions: event.target.value,
                }))
              }
              placeholder="针对此模板的补充提示，持久化保存。例如：发票可能为扫描件，注意识别手写金额"
              rows={2}
              value={draft.extra_instructions}
            />
          </div>

          <details className="template-advanced collapse">
            <summary>
              <span>高级设置 · AI 理解要求 / 自定义校验规则</span>
              <Icon icon={ChevronDown} size={14} className="template-advanced-caret" />
            </summary>
            <div className="collapse-content">
              <div className="template-advanced-body">
              <div className="form-field">
                <label className="form-label" htmlFor="tpl-rules">
                  给 AI 的理解要求（不会自动报错，每行一条）
                </label>
                <textarea
                  className="form-textarea"
                  disabled={editorDisabled}
                  id="tpl-rules"
                  onChange={(event) =>
                    setDraft((current) => ({
                      ...current,
                      validation_rules: event.target.value
                        .split("\n")
                        .map((line) => line.trim())
                        .filter(Boolean),
                    }))
                  }
                  placeholder="例如：如果有手写修改，以手写内容为准"
                  rows={3}
                  value={draft.validation_rules.join("\n")}
                />
                <span className="support">
                  这里帮助 AI 理解文件，但不能保证执行。金额、必填等硬性要求请添加为下方系统检查。
                </span>
              </div>
              <DeterministicRulesEditor
                disabled={editorDisabled}
                fields={draft.fields}
                onChange={(deterministicRules) =>
                  setDraft((current) => ({
                    ...current,
                    deterministic_rules: deterministicRules,
                  }))
                }
                rules={draft.deterministic_rules}
              />
            </div>
            </div>
          </details>
        </div>
      </div>

      {/* AI 生成模板弹窗：上传 → 生成（计时/禁关）→ 草稿预览编辑 → 保存 */}
      {aiOpen ? (
        <div
          className="modal-overlay"
          onClick={handleAiClose}
          style={{ display: "flex" }}
        >
          <div
            className="modal-card ai-tpl-modal"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="modal-head">
              <div>
                <div className="modal-eyebrow">
                  <Icon icon={Sparkles} size={15} />
                  AI 生成模板
                </div>
                <h3>智能创建提取模板</h3>
                <div className="support">
                  {aiDraft
                    ? "检查并修改 AI 生成的字段草稿，确认后保存为模板"
                    : "上传样例文件（可选），或直接输入需求描述，AI 自动生成模板草稿"}
                </div>
              </div>
              <button
                aria-label="关闭"
                className="btn ghost sm icon-only"
                disabled={aiGenerating}
                onClick={handleAiClose}
                type="button"
              >
                <Icon icon={X} size={16} />
              </button>
            </div>
            <div className="modal-body">
              {aiError ? (
                <div className="callout danger" style={{ marginBottom: 14 }}>{aiError}</div>
              ) : null}
              {aiGenerating ? (
                <div className="ai-generating">
                  <Icon icon={Loader2} size={22} className="spin" />
                  <div>
                    <div className="ai-generating-title">正在分析样例文件，请稍候…</div>
                    <div className="ai-generating-sub">
                      已等待 <strong>{aiElapsed}</strong> 秒 · 生成过程中请勿关闭窗口
                    </div>
                  </div>
                </div>
              ) : aiDraft ? (
                /* 阶段 3：草稿预览 + 编辑 */
                <div className="ai-draft">
                  <div className="form-field">
                    <label className="form-label" htmlFor="ai-draft-name">模板名称</label>
                    <input
                      className="form-input"
                      id="ai-draft-name"
                      value={aiDraftName}
                      onChange={(event) => setAiDraftName(event.target.value)}
                      placeholder="给模板起个名字"
                    />
                  </div>
                  <div className="form-field">
                    <label className="form-label" htmlFor="ai-draft-desc">模板说明</label>
                    <input
                      className="form-input"
                      id="ai-draft-desc"
                      maxLength={512}
                      value={aiDraftDescription}
                      onChange={(event) => setAiDraftDescription(event.target.value)}
                      placeholder="说明这份模板适用什么文件、要提取什么（AI 已生成，可修改）"
                    />
                  </div>
                  <div className="ai-draft-label">字段（可修改、删除、新增）</div>
                  <div className="ai-draft-fields">
                    {aiDraftFields.map((field, index) => (
                      <div className="ai-draft-field" key={index}>
                        <input
                          aria-label="字段名称"
                          className="form-input"
                          value={field.label}
                          onChange={(event) => updateAiField(index, { label: event.target.value })}
                          placeholder="字段名称"
                        />
                        <select
                          aria-label="出现方式"
                          className="form-select"
                          value={field.section}
                          onChange={(event) =>
                            updateAiField(index, { section: event.target.value as "header" | "item" })
                          }
                        >
                          <option value="header">每份文件一次</option>
                          <option value="item">每条明细重复</option>
                        </select>
                        <select
                          aria-label="字段类型"
                          className="form-select"
                          value={field.value_type}
                          onChange={(event) =>
                            updateAiField(index, {
                              value_type: event.target.value as AiDraftField["value_type"],
                            })
                          }
                        >
                          {fieldTypeOptions.map((option) => (
                            <option key={option.value} value={option.value}>{option.label}</option>
                          ))}
                        </select>
                        <input
                          aria-label="示例值"
                          className="form-input"
                          value={field.example}
                          onChange={(event) => updateAiField(index, { example: event.target.value })}
                          placeholder="示例（可选）"
                        />
                        <button
                          aria-label={`删除字段 ${field.label || index + 1}`}
                          className="btn danger-soft sm icon-only"
                          onClick={() =>
                            setAiDraftFields((current) =>
                              current.filter((_, i) => i !== index),
                            )
                          }
                          type="button"
                        >
                          <Icon icon={Trash2} size={13} />
                        </button>
                      </div>
                    ))}
                  </div>
                  <button
                    className="btn ghost sm"
                    onClick={() =>
                      setAiDraftFields((current) => [
                        ...current,
                        { label: "", section: "header", example: "", value_type: "text" },
                      ])
                    }
                    type="button"
                  >
                    <Icon icon={Plus} size={13} /> 添加字段
                  </button>
                </div>
              ) : (
                /* 阶段 1：上传 + 需求描述 */
                <>
                  <div
                    className="ai-tpl-upload"
                    onClick={() => aiFileInput.current?.click()}
                    onDragOver={(event) => event.preventDefault()}
                    onDrop={(event) => {
                      event.preventDefault();
                      handleAiFilesPicked(event.dataTransfer.files);
                    }}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        aiFileInput.current?.click();
                      }
                    }}
                  >
                    <Icon icon={UploadCloud} size={36} style={{ color: "var(--color-text-muted)" }} />
                    <div style={{ fontWeight: 600, fontSize: "0.875rem", marginTop: 8 }}>
                      拖拽文件到此处，或点击选择
                    </div>
                    <div className="tpl-item-meta" style={{ marginTop: 4 }}>
                      支持 PDF / 图片 · 可上传多个样例（可不传，仅凭需求描述生成）
                    </div>
                  </div>
                  <input
                    ref={aiFileInput}
                    className="visually-hidden"
                    type="file"
                    multiple
                    accept=".pdf,.png,.jpg,.jpeg,.webp"
                    onChange={(event) => {
                      if (event.target.files) handleAiFilesPicked(event.target.files);
                      event.target.value = "";
                    }}
                  />
                  {aiFiles.length > 0 ? (
                    <div className="ai-file-list">
                      {aiFiles.map((file, index) => (
                        <div className="ai-file-item" key={`${file.name}-${index}`}>
                          <Icon icon={FileText} size={13} />
                          <span>{file.name}</span>
                          <button
                            aria-label={`移除 ${file.name}`}
                            className="btn ghost xs icon-only"
                            onClick={() => handleAiRemoveFile(index)}
                            type="button"
                          >
                            <Icon icon={X} size={12} />
                          </button>
                        </div>
                      ))}
                    </div>
                  ) : null}
                  <div className="form-field" style={{ marginTop: 14 }}>
                    <label className="form-label" htmlFor="ai-profile">AI 方案</label>
                    <select
                      className="form-select"
                      id="ai-profile"
                      value={aiProfileId}
                      onChange={(event) => setAiProfileId(event.target.value)}
                    >
                      {aiProfiles.length === 0 ? (
                        <option value="">未配置模型方案（将使用任务激活方案）</option>
                      ) : (
                        aiProfiles.map((profile) => (
                          <option key={profile.id} value={profile.id}>
                            {profile.name}
                            {profile.is_active ? "（当前激活）" : ""} · {profile.provider} / {profile.model_name}
                          </option>
                        ))
                      )}
                    </select>
                    <div className="small muted" style={{ marginTop: 6 }}>
                      仅用于本次 AI 生成，不改变任务的激活方案；方案在"设置 → AI 服务配置"统一管理。
                    </div>
                  </div>
                  <div className="form-field" style={{ marginTop: 14 }}>
                    <label className="form-label" htmlFor="ai-req">需求描述</label>
                    <textarea
                      className="form-textarea"
                      id="ai-req"
                      rows={3}
                      value={aiRequirement}
                      onChange={(event) => setAiRequirement(event.target.value)}
                      placeholder="说明这类文件需要提取什么。例如：这是一张增值税发票，需要提取发票号码、购销双方名称、价税合计、税率，以及明细行的商品名称、数量、单价"
                    />
                    <div className="small muted" style={{ marginTop: 6 }}>
                      <Icon icon={Info} size={12} /> 描述越具体，生成的字段越贴合；AI 只生成字段结构，校验规则需保存后手动添加。
                    </div>
                  </div>
                  <label className="ai-examples-toggle" style={{ marginTop: 14 }}>
                    <input
                      type="checkbox"
                      checked={aiWithExamples}
                      onChange={(event) => setAiWithExamples(event.target.checked)}
                    />
                    <span>
                      <strong>生成示例值</strong>
                      <span className="small muted" style={{ display: "block", marginTop: 2 }}>
                        勾选后为每个字段生成典型示例；示例会进入提取提示词（基于样例文件，非中性），不勾选则全部留空
                      </span>
                    </span>
                  </label>
                </>
              )}
            </div>
            <div className="modal-foot">
              {aiGenerating ? (
                <span className="small muted" style={{ marginLeft: "auto" }}>
                  生成中，请稍候…
                </span>
              ) : aiDraft ? (
                <>
                  <button
                    className="btn ghost"
                    disabled={saving}
                    onClick={() => {
                      setAiDraft(null);
                      setAiError(null);
                    }}
                    type="button"
                  >
                    <Icon icon={RotateCcw} size={13} /> 重新生成
                  </button>
                  <button
                    className="btn primary"
                    disabled={saving}
                    onClick={() => void handleAiSave()}
                    type="button"
                  >
                    <Icon icon={Save} size={13} /> 保存模板
                  </button>
                </>
              ) : (
                <>
                  <button className="btn ghost" onClick={handleAiClose} type="button">
                    取消
                  </button>
                  <button
                    className="btn primary"
                    disabled={!aiRequirement.trim()}
                    onClick={() => void handleAiGenerate()}
                    type="button"
                  >
                    <Icon icon={Sparkles} size={14} /> 生成模板
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      ) : null}
      <Toast toast={toast} onDismiss={clear} />
    </section>
  );
}

function toDraft(template: ExtractionTemplate): TemplateDraft {
  return {
    name: template.name,
    description: template.description,
    extra_instructions: template.extra_instructions,
    fields: template.fields.map((field) => ({ ...field })),
    validation_rules: [...template.validation_rules],
    deterministic_rules: structuredClone(template.deterministic_rules ?? []),
    output_mapping: { ...template.output_mapping },
  };
}
