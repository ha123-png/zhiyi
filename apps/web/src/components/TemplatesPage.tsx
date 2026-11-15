import { Disclosure } from "./Disclosure";
import { useUnsavedChanges } from "../useUnsavedChanges";
import { useModalFocus } from "../useModalFocus";
import { useAssistantPageContext } from "../assistant/AssistantProvider";
import { useEffect, useRef, useState } from "react";
import {
  Archive,
  Copy,
  Download,
  FileText,
  GripVertical,
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
import { desktopApi } from "../desktop";
import { DeterministicRulesEditor } from "./DeterministicRulesEditor";
import { PresentationSettings, TemplateNameSettings, cleanPresentation } from "./PresentationSettings";
import { Icon } from "./Icon";
import { CollapseChevron } from "./CollapseChevron";
import { Toast, useToast } from "./Toast";
import { NoticeDialog } from "./NoticeDialog";
import { TemplateExportSettings, type TemplateExportHandle } from "./TemplateExportSettings";
import { ConfirmDialog } from "./ConfirmDialog";

import { editableTemplate, mergeTemplateHints, moveTemplateField } from "../templateEditing";
import { useFieldSort } from "../useFieldSort";
import { TemplateHistory } from "./TemplateHistory";

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
  fields: [{ ...emptyField, key: `field_${crypto.randomUUID().replaceAll("-", "")}` }],
  validation_rules: [],
  deterministic_rules: [],
  output_mapping: {},
};

const fieldTypeOptions: { value: TemplateField["value_type"]; label: string }[] = [
  { value: "text", label: "文本" },
  { value: "number", label: "数字" },
  { value: "date", label: "日期" },
  { value: "boolean", label: "是非" },
];

export function TemplatesPage({ initialTemplateId = null, initialVersion = null, initialDraft = null, onDraftConsumed }: { initialTemplateId?: string | null; initialVersion?: number | null; initialDraft?: TemplateDraft | null; onDraftConsumed?: () => void } = {}) {
  const appliedReference = useRef("");
  const mountReference = useRef(initialTemplateId ? `${initialTemplateId}:${initialVersion}` : "");
  const handedDraft = useRef(false);
  const consumedDraft = useRef<TemplateDraft | null>(null);
  const editorEpoch = useRef(0);
  const templatesRequest = useRef(0);
  const writeInFlight = useRef(false);
  const [templates, setTemplates] = useState<ExtractionTemplate[]>([]);
  const [templatesLoaded, setTemplatesLoaded] = useState(false);
  const [templatesLoading, setTemplatesLoading] = useState(true);
  const [templatesError, setTemplatesError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [draft, setDraft] = useState<TemplateDraft>(emptyDraft);
  const [version, setVersion] = useState(0);
  useAssistantPageContext("templates", { template_id: selectedId || null, template_version: version || null });
  const savedDraft = useRef<string>(JSON.stringify(emptyDraft));
  const exportSettings = useRef<TemplateExportHandle>(null);
  const [isSystem, setIsSystem] = useState(false);
  const [isActive, setIsActive] = useState(true);
  const [isNew, setIsNew] = useState(true);
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const { toast, notify, clear } = useToast();
  const [listNoticeOpen, setListNoticeOpen] = useState(false);
  const [aiOpen, setAiOpen] = useState(false);
  const [expandedField, setExpandedField] = useState<number | null>(null);
  const importInput = useRef<HTMLInputElement>(null);
  const [fieldAnnouncement, setFieldAnnouncement] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [deleteTargetId, setDeleteTargetId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const deleteInFlight = useRef(false);
  const unsaved = useUnsavedChanges(() => JSON.stringify(draft) !== savedDraft.current || Boolean(exportSettings.current?.isDirty()));
  const currentEditor = useRef({ selectedId, draft });
  currentEditor.current = { selectedId, draft };

  const fieldSort = useFieldSort(draft.fields.map(f => `${f.section}:${f.key}`).join("|"), moveField);

  // AI 生成模板弹窗
  const [aiFiles, setAiFiles] = useState<File[]>([]);
  const [aiRequirement, setAiRequirement] = useState("");
  const [aiGenerating, setAiGenerating] = useState(false);
  const [aiNotice, setAiNotice] = useState<string | null>(null);
  const aiGenerateEpoch = useRef(0);
  const aiGenerateAbort = useRef<AbortController | null>(null);
  const [aiElapsed, setAiElapsed] = useState(0);
  const [aiDraft, setAiDraft] = useState<AiTemplateDraft | null>(null);
  const [aiError, setAiError] = useState<string | null>(null);
  const [aiDraftName, setAiDraftName] = useState("");
  const aiDraftNameInput = useRef<HTMLInputElement>(null);
  const [aiNameError, setAiNameError] = useState(false);
  const [aiDraftDescription, setAiDraftDescription] = useState("");
  const [aiDraftFields, setAiDraftFields] = useState<AiDraftField[]>([]);
  const [aiWithExamples, setAiWithExamples] = useState(true);
  const [aiWithRules, setAiWithRules] = useState(false);
  const [aiSelectedRules, setAiSelectedRules] = useState<number[]>([]);
  const aiFileInput = useRef<HTMLInputElement>(null);
  const [aiProfiles, setAiProfiles] = useState<ModelProfile[]>([]);
  const [aiProfileId, setAiProfileId] = useState("");
  const [aiProfilesState, setAiProfilesState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [aiProfilesError, setAiProfilesError] = useState<string | null>(null);
  const aiProfilesRequest = useRef(0);
  const [discardAiDraftOpen, setDiscardAiDraftOpen] = useState(false);
  const aiDialog = useModalFocus(aiOpen, handleAiClose, aiGenerating || saving);

  // 打开弹窗时加载全局模型方案（AI 生成可独立选择，与任务激活方案解耦）
  useEffect(() => {
    if (!aiOpen) return;
    void loadAiProfiles();
    return () => { aiProfilesRequest.current += 1; };
  }, [aiOpen]);

  async function loadAiProfiles() {
    const request = ++aiProfilesRequest.current;
    setAiProfilesState("loading");
    setAiProfilesError(null);
    try {
      const profiles = await getModelProfiles();
      if (request !== aiProfilesRequest.current) return;
      const usable = profiles.filter((profile) => !profile.is_archived);
      setAiProfiles(usable);
      setAiProfileId(current => usable.some(profile => profile.id === current)
        ? current : usable.find(profile => profile.is_active)?.id ?? usable[0]?.id ?? "");
      setAiProfilesState("ready");
    } catch (reason) {
      if (request !== aiProfilesRequest.current) return;
      setAiProfilesError(reason instanceof Error ? reason.message : "AI 方案读取失败。");
      setAiProfilesState("error");
    }
  }

  useEffect(() => {
    void refreshTemplates(initialTemplateId ?? undefined);
  }, []);

  useEffect(() => {
    if (initialDraft) {
      if (consumedDraft.current === initialDraft) return;
      consumedDraft.current = initialDraft;
      editorEpoch.current += 1;
      handedDraft.current = true;
      setSelectedId(""); setVersion(0); setIsSystem(false); setIsActive(true); setIsNew(true);
      setDraft(initialDraft); savedDraft.current = JSON.stringify({ ...emptyDraft, fields: [] }); setMessage("来自问知意的草稿，尚未保存。请核对字段、规则及文件命名设置。");
      onDraftConsumed?.();
    } else if (initialTemplateId && appliedReference.current !== `${initialTemplateId}:${initialVersion}`) {
      // An initial route reference must not replay over a new draft started
      // while its catalog request was pending. Later explicit references still
      // use the normal unsaved-change protection.
      if (editorEpoch.current > 0 && !appliedReference.current
        && mountReference.current === `${initialTemplateId}:${initialVersion}`) {
        appliedReference.current = `${initialTemplateId}:${initialVersion}`;
        return;
      }
      const selected = templates.find(t => t.id === initialTemplateId);
      if (selected) { selectTemplate(selected); appliedReference.current = `${initialTemplateId}:${initialVersion}`; if (initialVersion) setHistoryOpen(true); }
    }
  }, [initialTemplateId, initialVersion, initialDraft, templates]);

  // AI 生成中的等待计时（每秒跳动）
  useEffect(() => {
    if (!aiGenerating) return;
    setAiElapsed(0);
    const timer = window.setInterval(() => {
      setAiElapsed((seconds) => seconds + 1);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [aiGenerating]);

  useEffect(() => () => {
    templatesRequest.current += 1;
    aiProfilesRequest.current += 1;
    aiGenerateEpoch.current += 1;
    aiGenerateAbort.current?.abort();
    aiGenerateAbort.current = null;
  }, []);

  async function refreshTemplates(preferredId?: string, replaceDraft = false) {
    const request = ++templatesRequest.current;
    const editorAtRequest = editorEpoch.current;
    setTemplatesLoading(true);
    setTemplatesError(null);
    try {
      const next = await getTemplates(true);
      if (request !== templatesRequest.current) return;
      setTemplates(next);
      setTemplatesLoaded(true);
      const editor = currentEditor.current;
      const untouched = editorAtRequest === 0 && editorEpoch.current === 0
        && !editor.selectedId && JSON.stringify(editor.draft) === savedDraft.current;
      const selected =
        next.find((template) => template.id === preferredId)
        ?? next.find((template) => template.id === editor.selectedId)
        ?? next[0];
      if (selected && (replaceDraft && editorAtRequest === editorEpoch.current
        || untouched && !initialDraft && !handedDraft.current)) {
        handedDraft.current = false;
        selectTemplate(selected, true);
        if (selected.id === initialTemplateId) {
          appliedReference.current = `${initialTemplateId}:${initialVersion}`;
          if (initialVersion) setHistoryOpen(true);
        }
      }
    } catch (error) {
      if (request === templatesRequest.current) {
        setTemplatesError(error instanceof Error ? error.message : "模板读取失败。");
      }
    } finally {
      if (request === templatesRequest.current) setTemplatesLoading(false);
    }
  }

  function rememberCommittedTemplate(template: ExtractionTemplate) {
    // A write response is already a committed fact. A later catalog read may
    // fail; retain the returned template so retrying the read never repeats it.
    templatesRequest.current += 1;
    setTemplatesLoading(false);
    setTemplates(items => items.some(item => item.id === template.id)
      ? items.map(item => item.id === template.id ? template : item) : [...items, template]);
    handedDraft.current = false;
    selectTemplate(template, true);
  }

  function selectTemplate(template: ExtractionTemplate, force = false) {
    if (!force) { unsaved.protect(() => selectTemplate(template, true)); return; }
    editorEpoch.current += 1;
    setSelectedId(template.id);
    setVersion(template.version);
    setIsSystem(template.is_system);
    setIsActive(template.is_active ?? true);
    setIsNew(false);
    setDraft(toDraft(template));
    savedDraft.current = JSON.stringify(toDraft(template));
    setExpandedField(null);
    setMessage("");
  }

  function startNew(force = false) {
    if (!force) { unsaved.protect(() => startNew(true)); return; }
    editorEpoch.current += 1;
    setSelectedId("");
    setVersion(0);
    setIsSystem(false);
    setIsActive(true);
    setIsNew(true);
    const next = {
      ...emptyDraft,
      fields: [{ ...emptyField, key: `field_${crypto.randomUUID().replaceAll("-", "")}` }],
      output_mapping: {},
    };
    savedDraft.current = JSON.stringify(next);
    setDraft(next);
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

  function moveField(from: number, to: number) {
    if (isSystem || saving || !isActive || to < 0 || to >= draft.fields.length) return;
    setDraft((current) => ({ ...current, fields: moveTemplateField(current.fields, from, to) }));
    setExpandedField(expanded => expanded === null ? null : expanded === from ? to : from < to && expanded > from && expanded <= to ? expanded - 1 : from > to && expanded >= to && expanded < from ? expanded + 1 : expanded);
    setFieldAnnouncement(`已将${draft.fields[from].label || "字段"}移到第 ${to + 1} 位`);
  }

  async function handleCopy(templateId?: string, confirmed = false) {
    if (writeInFlight.current) return;
    const id = templateId ?? selectedId;
    if (!id) {
      return;
    }
    if (!confirmed) { unsaved.protect(() => void handleCopy(id, true)); return; }
    writeInFlight.current = true;
    setSaving(true);
    try {
      const copied = await copyTemplate(id);
      rememberCommittedTemplate(copied);
      await refreshTemplates(copied.id, true);
      notify("已创建可编辑副本。副本默认不参与智能匹配，可在提取页的匹配范围中勾选。", "success");
    } catch (error) {
      notify(error instanceof Error ? error.message : "复制模板失败。", "error");
    } finally {
      writeInFlight.current = false;
      setSaving(false);
    }
  }

  async function handleArchive(templateId?: string, confirmed = false) {
    if (writeInFlight.current) return;
    const id = templateId ?? selectedId;
    if (!id) return;
    if (!confirmed) { unsaved.protect(() => void handleArchive(id, true)); return; }
    writeInFlight.current = true;
    setSaving(true);
    try {
      const archived = await archiveTemplate(id);
      rememberCommittedTemplate(archived);
      await refreshTemplates(archived.id, true);
      notify("模板已停用；旧任务和旧数据仍可追溯，可随时恢复。", "success");
    } catch (error) {
      notify(error instanceof Error ? error.message : "模板停用失败。", "error");
    } finally {
      writeInFlight.current = false;
      setSaving(false);
    }
  }

  async function handleRestore(templateId?: string, confirmed = false) {
    if (writeInFlight.current) return;
    const id = templateId ?? selectedId;
    if (!id) return;
    if (!confirmed) { unsaved.protect(() => void handleRestore(id, true)); return; }
    writeInFlight.current = true;
    setSaving(true);
    try {
      const restored = await restoreTemplate(id);
      rememberCommittedTemplate(restored);
      await refreshTemplates(restored.id, true);
      notify("模板已恢复，可以继续用于新文件。", "success");
    } catch (error) {
      notify(error instanceof Error ? error.message : "模板恢复失败。", "error");
    } finally {
      writeInFlight.current = false;
      setSaving(false);
    }
  }

  async function handleDelete(discardConfirmed = false) {
    if (!selectedId) return;
    if (!discardConfirmed) { unsaved.protect(() => void handleDelete(true)); return; }
    setDeleteError(null);
    setDeleteTargetId(selectedId);
  }

  async function confirmDelete() {
    const targetId = deleteTargetId;
    if (!targetId || deleteInFlight.current || writeInFlight.current) return;
    deleteInFlight.current = true;
    writeInFlight.current = true;
    setDeleteError(null);
    setSaving(true);
    try {
      await deleteTemplate(targetId);
      notify("模板已删除。", "success");
      templatesRequest.current += 1;
      setTemplates(items => items.filter(item => item.id !== targetId));
      setDeleteTargetId(null);
      setSelectedId("");
      startNew(true);
      await refreshTemplates(undefined, true);
    } catch (error) {
      setDeleteError(error instanceof Error ? error.message : "删除模板失败。");
    } finally {
      deleteInFlight.current = false;
      writeInFlight.current = false;
      setSaving(false);
    }
  }

  // ---- AI 生成模板弹窗 ----

  function resetAiState() {
    aiGenerateEpoch.current += 1;
    aiGenerateAbort.current?.abort();
    aiGenerateAbort.current = null;
    setAiFiles([]);
    setAiRequirement("");
    setAiGenerating(false);
    setAiNotice(null);
    setAiElapsed(0);
    setAiDraft(null);
    setAiError(null);
    setAiDraftName("");
    setAiNameError(false);
    setAiDraftDescription("");
    setAiDraftFields([]);
    setAiWithExamples(true);
    setAiWithRules(false);
    setAiSelectedRules([]);
  }

  function handleAiClose() {
    if (aiGenerating || writeInFlight.current) return;
    if (aiDraft) {
      setDiscardAiDraftOpen(true);
      return;
    }
    setAiOpen(false);
    resetAiState();
  }

  function discardAiDraft() {
    setDiscardAiDraftOpen(false);
    setAiOpen(false);
    resetAiState();
  }

  function handleAiFilesPicked(files: FileList | File[]) {
    const incoming = Array.from(files);
    const picked = incoming.filter(file => /\.(pdf|png|jpe?g|webp)$/i.test(file.name));
    const unsupported = incoming.filter(file => !picked.includes(file));
    setAiError(unsupported.length ? `有 ${unsupported.length} 个样例未添加：仅支持 PDF、PNG、JPG 和 WebP。其他文件可在“文件提取”中处理。` : null);
    // Filenames are not identities: scans from different folders can share a name.
    setAiFiles((current) => [...current, ...picked]);
  }

  function handleAiRemoveFile(index: number) {
    setAiFiles((current) => current.filter((_, i) => i !== index));
  }

  async function handleAiGenerate() {
    if (aiGenerating || aiGenerateAbort.current || writeInFlight.current || aiProfilesState !== "ready") return;
    if (!aiRequirement.trim()) {
      setAiError("请先填写需求描述。");
      return;
    }
    const requestEpoch = ++aiGenerateEpoch.current;
    const controller = new AbortController();
    aiGenerateAbort.current = controller;
    setAiError(null);
    setAiNotice(null);
    setAiGenerating(true);
    try {
      const draft = await generateTemplateDraft(
        aiFiles,
        aiRequirement.trim(),
        aiProfileId || undefined,
        aiWithExamples,
        aiWithRules,
        controller.signal,
      );
      if (requestEpoch !== aiGenerateEpoch.current) return;
      setAiDraft(draft);
      setAiDraftName(draft.name || "");
      setAiNameError(false);
      setAiDraftDescription(draft.description || "");
      setAiDraftFields(
        draft.fields.map((field) => ({ ...field, example: field.example ?? "" })),
      );
      setAiSelectedRules(
        draft.rule_suggestions
          .map((suggestion, index) => suggestion.status === "accepted" ? index : -1)
          .filter((index) => index >= 0),
      );
    } catch (error) {
      if (requestEpoch === aiGenerateEpoch.current) {
        setAiError(error instanceof Error ? error.message : "AI 生成失败，请重试。");
      }
    } finally {
      if (requestEpoch === aiGenerateEpoch.current) {
        aiGenerateAbort.current = null;
        setAiGenerating(false);
      }
    }
  }

  function stopWaitingForAiGeneration() {
    if (!aiGenerating) return;
    aiGenerateEpoch.current += 1;
    aiGenerateAbort.current?.abort();
    aiGenerateAbort.current = null;
    setAiGenerating(false);
    setAiError(null);
    setAiNotice("模型服务可能仍在处理刚才的请求，再次生成会提交新请求。本页会忽略旧请求的结果。");
  }

  function updateAiField(index: number, patch: Partial<AiDraftField>) {
    setAiDraftFields((current) =>
      current.map((field, i) => (i === index ? { ...field, ...patch } : field)),
    );
  }

  /** 按创建来源建议未占用名称；服务端仍负责最终的名称冲突检查。 */
  function uniqueTemplateName(base: string, aiGenerated = true): string {
    const names = new Set(
      templates.map((t) => t.name),
    );
    if (!names.has(base)) return base;
    let candidate = aiGenerated ? `${base}（AI 生成）` : `${base}（2）`;
    let suffix = 2;
    while (names.has(candidate)) {
      candidate = aiGenerated ? `${base}（AI 生成）${suffix}` : `${base}（${suffix + 1}）`;
      suffix += 1;
    }
    return candidate;
  }

  async function handleAiSave() {
    if (writeInFlight.current) return;
    if (!aiDraftName.trim()) {
      setAiNameError(true);
      aiDraftNameInput.current?.focus();
      return;
    }
    const emptyLabel = aiDraftFields.some((field) => !field.label.trim());
    if (aiDraftFields.length === 0 || emptyLabel) {
      setAiError(emptyLabel ? "请填写所有字段名称，或删除空字段。" : "至少需要一个字段。");
      return;
    }
    const requestedName = aiDraftName.trim();
    const finalName = uniqueTemplateName(requestedName);
    writeInFlight.current = true;
    setSaving(true);
    setAiError(null);
    try {
      const saved = await createTemplate({
        name: finalName,
        description: aiDraftDescription.trim(),
        behavior: cleanPresentation(aiDraft?.behavior, aiDraftFields.map((f) => ({ ...f, instructions: "", example: f.example ?? "" }))),
        extra_instructions: "",
        fields: aiDraftFields.map((field) => ({
          label: field.label,
          key: field.key,
          section: field.section,
          example: field.example ?? "",
          instructions: "",
          value_type: field.value_type,
        })),
        validation_rules: [],
        deterministic_rules: (aiDraft?.rule_suggestions ?? [])
          .filter((suggestion, index) => aiSelectedRules.includes(index) && suggestion.rule)
          .map((suggestion) => suggestion.rule!),
        output_mapping: {},
      });
      rememberCommittedTemplate(saved);
      await refreshTemplates(saved.id, true);
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
      writeInFlight.current = false;
      setSaving(false);
    }
  }

  async function handleSave() {
    if (writeInFlight.current) return;
    if (isNew && templates.some(t => t.name.trim() === draft.name.trim())) {
      notify("已有同名模板，请修改名称后创建。原模板不会被覆盖。", "error");
      return;
    }
    writeInFlight.current = true;
    setSaving(true);
    try {
      let savedId = selectedId;
      let savedVersion = version;
      const definitionChanged = isNew || JSON.stringify(draft) !== savedDraft.current;
      if (definitionChanged) {
        const saved = isNew
          ? await createTemplate({ ...draft, behavior: cleanPresentation(draft.behavior, draft.fields) })
          : await updateTemplate(selectedId, version, { ...draft, behavior: cleanPresentation(draft.behavior, draft.fields) }, templates.find(t => t.id === selectedId)?.updated_at);
        savedId = saved.id;
        savedVersion = saved.version;
        templatesRequest.current += 1;
        setTemplatesLoading(false);
        // Keep the successful schema revision if the local binding save fails.
        // Retrying must not create a second version or discard unsaved path input.
        setVersion(saved.version);
        setTemplates(items => items.some(item => item.id === saved.id)
          ? items.map(item => item.id === saved.id ? saved : item)
          : [...items, saved]);
        savedDraft.current = JSON.stringify(draft);
        if (isNew) { setSelectedId(saved.id); setIsNew(false); }
      }
      await exportSettings.current?.save();
      await refreshTemplates(savedId, true);
      notify(definitionChanged ? `模板已保存（第 ${savedVersion} 版）。` : "设置已保存。", "success");
    } catch (error) {
      notify(error instanceof Error ? error.message : "模板保存失败。", "error");
    } finally {
      writeInFlight.current = false;
      setSaving(false);
    }
  }

  async function handleExport() {
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
    const filename = `${draft.name}.template.json`;
    const bridge = desktopApi();
    if (bridge) {
      try {
        const path = await bridge.export_template(filename, payload);
        notify(`模板已导出到 ${path}`, "success");
      } catch (error) {
        notify(error instanceof Error ? error.message : "模板导出失败。", "error");
      }
      return;
    }
    const url = URL.createObjectURL(
      new Blob([payload], { type: "application/json;charset=utf-8" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(url);
    notify("模板已导出为 JSON 文件。", "success");
  }

  async function handleImport(file: File, confirmed = false) {
    if (writeInFlight.current) return;
    if (!confirmed) { unsaved.protect(() => void handleImport(file, true)); return; }
    writeInFlight.current = true;
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
        behavior: imported.behavior,
      });
      rememberCommittedTemplate(saved);
      await refreshTemplates(saved.id, true);
      notify(`已导入模板“${saved.name}”。`, "success");
    } catch (error) {
      notify(error instanceof Error ? error.message : "模板导入失败。", "error");
    } finally {
      writeInFlight.current = false;
      setSaving(false);
      if (importInput.current) {
        importInput.current.value = "";
      }
    }
  }

  const isAutonomous = templates.find(template => template.id === selectedId)?.engine_kind === "autonomous";
  const canSave =
    (!isSystem || isAutonomous)
    && isActive
    && draft.name.trim().length > 0
    && (isAutonomous || draft.fields.length > 0)
    && draft.fields.every((field) => field.label.trim().length > 0);

  const typeLabel = isSystem ? "内置" : isNew ? "新建" : "自定义";
  const supportText = isAutonomous ? "内置模板 · 每份文件各自决定字段" : `${typeLabel}模板${isActive ? "" : " · 已停用"} · ${draft.fields.length} 个字段 · ${draft.description || "尚未填写用途"}`;
  const editorDisabled = isSystem || saving || !isActive;

  return (
    <section className="templates-view">
      {unsaved.dialog}
      {listNoticeOpen && templatesError && <NoticeDialog title="模板读取失败" onClose={() => setListNoticeOpen(false)}
        actions={<button className="btn primary sm" type="button" disabled={templatesLoading || saving}
          onClick={() => { setListNoticeOpen(false); void refreshTemplates(); }}>重新读取</button>}>
        <p>{templatesError}</p>
      </NoticeDialog>}

      <div className="template-layout">
        {/* Left card — list */}
        <div className="card flush">
          <div className="panel-title tpl-list-title">
            <div>
              <h3>所有模板</h3>
              <div className="support" role="status">
                {templatesLoading ? "正在读取模板…" : templatesError
                  ? templatesLoaded || templates.length > 0 ? `已保留 ${templates.length} 个已知模板` : "模板数量暂不可用"
                  : templatesLoaded ? `共 ${templates.length} 个模板` : "模板列表尚未读取"}
              </div>
            </div>
            {templatesError && <button className="link-btn" type="button" aria-haspopup="dialog"
              onClick={() => setListNoticeOpen(true)}>模板读取失败</button>}
          </div>

          <div className="tpl-side-actions">
            <button
              className="btn primary sm"
              disabled={saving}
              onClick={() => {
                // 每次打开都从空白开始，避免残留上次生成的数据
                unsaved.protect(() => { resetAiState(); setAiOpen(true); });
              }}
              title="上传样例文件（可选）+ 需求描述，AI 生成字段草稿"
              type="button"
            >
              <Icon icon={Sparkles} size={13} />
              AI 生成模板
            </button>
            <button
              className="btn secondary sm"
              disabled={saving}
              onClick={() => startNew()}
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
                disabled={!draft.name.trim() || isAutonomous}
                title={isAutonomous ? "自主提取随知意提供，高级设置保存在本机" : undefined}
                onClick={handleExport}
                type="button"
              >
                <Icon icon={Download} size={12} />
                导出
              </button>
            </div>
            <input
              accept="application/json,.json"
              hidden
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
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
                  aria-disabled={saving}
                  onClick={() => { if (!saving) selectTemplate(template); }}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(event) => {
                    if (!saving && (event.key === "Enter" || event.key === " ")) {
                      event.preventDefault();
                      selectTemplate(template);
                    }
                  }}
                >
                  <Icon icon={FileText} size={14} />
                  <div className="tpl-item-main">
                    <div className="tpl-item-name">{template.name}</div>
                    <div className="tpl-item-meta">
                      {template.engine_kind === "autonomous" ? "按文件确定字段" : `${template.fields.length} 字段`} · 第 {template.version} 版
                    </div>
                  </div>
                  <div
                    className="tpl-list-actions"
                    onClick={(event) => event.stopPropagation()}
                  >
                    {template.engine_kind !== "autonomous" && <button
                      className="tpl-act-btn"
                      disabled={saving || template.is_active === false}
                      onClick={() => void handleCopy(template.id)}
                      title="复制"
                      type="button"
                    >
                      <Icon icon={Copy} size={12} />
                    </button>}
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
            {templatesLoaded && !templatesLoading && !templatesError && templates.length === 0 ? (
              <div className="tpl-item-meta" style={{ padding: "16px 10px", textAlign: "center" }}>
                暂无模板
              </div>
            ) : null}
          </div>
        </div>

        {/* Right card — detail editor */}
        <div className="card">
          <div className="panel-title tpl-detail-head">
            <div>
              <h3>{draft.name || "未命名模板"}</h3>
              <div className="support">{supportText}</div>
              {!isNew && <button className="link-btn template-history-link" disabled={saving} onClick={() => setHistoryOpen(true)}>第 {version} 版 · 查看历史</button>}
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
                  title="删除模板入口，保留已有文件、当前数据和历史规则"
                  type="button"
                >
                  <Icon icon={Trash2} size={13} />
                  删除
                </button>
              ) : null}
              {!isSystem ? (
                <button
                  className="btn"
                  disabled={saving || !isActive || !selectedId}
                  onClick={() => void handleCopy()}
                  type="button"
                >
                  <Icon icon={Copy} size={13} />
                  复制
                </button>
              ) : null}
              {isSystem && !isAutonomous && <button className="btn secondary" disabled={saving} onClick={() => void handleSave()} type="button"><Icon icon={Save} size={13} />保存</button>}
              {isSystem && !isAutonomous ? (
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
            {isNew && templates.some(t => t.name.trim() === draft.name.trim()) && <p className="field-hint" role="status">
              已有同名模板。<button type="button" onClick={() => setDraft(current => ({ ...current, name: uniqueTemplateName(current.name.trim(), false) }))}>使用名称“{uniqueTemplateName(draft.name.trim(), false)}”</button>
            </p>}
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

          {isAutonomous ? <section className="autonomous-field-note">
            <h3>字段由 AI 按文件确定</h3>
            <p className="support">无需预设字段或固定校验规则。提取后仍可修订；提取重点、命名和归档可在下方设置。</p>
          </section> : <><PresentationSettings value={draft.behavior} disabled={editorDisabled}
            onChange={(behavior) => setDraft((current) => ({ ...current, behavior }))} />
          <p className="support field-order-hint">字段顺序用于当前表格与文件卡片；历史版本保留当时的名称和顺序。</p>
          <span className="visually-hidden" role="status">{fieldAnnouncement}</span>
          <div className="field-list" ref={fieldSort.list}>
            {fieldSort.placeholder && <div className="field-sort-placeholder" aria-hidden="true" style={fieldSort.placeholder} />}
            <div className="field-row header">
              <span>字段名</span>
              <span>类型</span>
              <span>分区</span>
              <span>操作</span>
            </div>
            {draft.fields.map((field, index) => {
              const expanded = expandedField === index;
              return (
                <div key={`${field.section}:${field.key ?? `new-${index}`}`} data-field-index={index}
                  className="template-field">
                  <div className={`field-row ${expanded ? "has-sub" : ""}`}>
                    <div className="field-name-control">
                      <button type="button" className="field-drag-handle" disabled={editorDisabled}
                        aria-label={`移动字段 ${field.label || index + 1}`} title="拖动排序，或聚焦后按上下方向键移动"
                        onPointerDown={event => fieldSort.onPointerDown(event, index)}
                        onPointerMove={fieldSort.onPointerMove} onPointerUp={fieldSort.onPointerUp}
                        onPointerCancel={fieldSort.onPointerCancel}
                        onLostPointerCapture={fieldSort.onPointerCancel}
                        onKeyDown={(event) => { if (event.key === "ArrowUp" || event.key === "ArrowDown") { event.preventDefault(); moveField(index, index + (event.key === "ArrowUp" ? -1 : 1)); } }}>
                        <Icon icon={GripVertical} size={15} />
                      </button>
                    <input
                      aria-label="字段名"
                      className="form-input"
                      disabled={editorDisabled}
                      onChange={(event) => updateField(index, { label: event.target.value })}
                      placeholder="字段名，例如：供应商名称"
                      value={field.label}
                    />
                    </div>
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
                        aria-expanded={expanded}
                        aria-controls={`template-field-detail-${index}`}
                        className="tpl-act-btn"
                        onClick={() => setExpandedField(expanded ? null : index)}
                        title={expanded ? "收起" : "示例与说明"}
                        type="button"
                      >
                        <CollapseChevron expanded={expanded} />
                      </button>
                      {!isSystem && draft.fields.length > 1 ? (
                        <button
                          aria-label={`删除字段 ${field.label || index + 1}`}
                          className="tpl-act-btn"
                          disabled={editorDisabled}
                          onClick={() => removeField(index)}
                          title="删除字段"
                          type="button"
                        >
                          <Icon icon={X} size={13} />
                        </button>
                      ) : null}
                    </div>
                  </div>
                  <div className={`collapse${expanded ? " open" : ""}`} id={`template-field-detail-${index}`} inert={!expanded} aria-hidden={!expanded}>
                    <div className="collapse-content"><div className="field-row-sub">
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
                    </div></div>
                  </div>
                </div>
              );
            })}
            {!isSystem ? (
              <button
                className="btn ghost sm tpl-add-field"
                disabled={editorDisabled}
                onClick={() =>
                  setDraft((current) => ({
                    ...current,
                    fields: [...current.fields, { ...emptyField, key: `field_${crypto.randomUUID().replaceAll("-", "")}` }],
                  }))
                }
                type="button"
              >
                <Icon icon={Plus} size={13} />
                添加字段
              </button>
            ) : null}
          </div></>}

          <div className="form-field tpl-extra-field">
            <label className="form-label" htmlFor="tpl-extra">额外提示词</label>
            <textarea
              className="form-textarea"
              disabled={saving || !isActive || (isSystem && !isAutonomous)}
              id="tpl-extra"
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  extra_instructions: event.target.value,
                  validation_rules: [],
                }))
              }
              placeholder="补充文件的理解或提取要求，例如：以手写修改后的内容为准"
              rows={2}
              value={mergeTemplateHints(draft.extra_instructions, draft.validation_rules)}
            />
          </div>

          <Disclosure className="template-advanced" title="高级设置">
            <div className="template-advanced-body">
              {!isAutonomous && <DeterministicRulesEditor
                contextKey={JSON.stringify([selectedId, version, draft])}
                disabled={editorDisabled}
                fields={draft.fields}
                onChange={(deterministicRules) =>
                  setDraft((current) => ({
                    ...current,
                    deterministic_rules: deterministicRules,
                  }))
                }
                rules={draft.deterministic_rules}
              />}
          <TemplateNameSettings value={draft.behavior} disabled={saving || !isActive || (isSystem && !isAutonomous)} onChange={(behavior) => setDraft((current) => ({ ...current, behavior }))} />
          {!isNew && selectedId && <TemplateExportSettings ref={exportSettings} key={selectedId} templateId={selectedId} disabled={saving || !isActive} />}
            </div>
          </Disclosure>
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
            role="dialog"
            aria-modal="true"
            aria-label="智能创建提取模板"
            {...aiDialog}
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
                disabled={aiGenerating || saving}
                onClick={handleAiClose}
                type="button"
              >
                <Icon icon={X} size={16} />
              </button>
            </div>
            <div className="modal-body">
              <fieldset disabled={saving} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
              {aiProfilesState === "loading" && <p className="support" role="status">正在读取 AI 方案…</p>}
              {aiProfilesError && <div className="callout danger" role="alert">
                AI 方案读取失败：{aiProfilesError}
                <div className="support">需求、样例和草稿已保留；重新读取成功后才能生成。</div>
                <button className="btn secondary sm" type="button" disabled={aiProfilesState === "loading" || saving}
                  onClick={() => void loadAiProfiles()}>重新读取 AI 方案</button>
              </div>}
              {aiNotice ? (
                <div className="callout info" role="status" style={{ marginBottom: 14 }}>{aiNotice}</div>
              ) : null}
              {aiGenerating ? (
                <div className="ai-generating">
                  <Icon icon={Loader2} size={22} className="spin" />
                  <div>
                    <div className="ai-generating-title">正在分析样例文件，请稍候…</div>
                    <div className="ai-generating-sub">
                      已等待 <strong>{aiElapsed}</strong> 秒 · 可停止等待并返回编辑；模型服务可能仍在处理当前请求
                    </div>
                  </div>
                </div>
              ) : aiDraft ? (
                /* 阶段 3：草稿预览 + 编辑 */
                <div className="ai-draft">
                  {(aiDraft.warnings ?? []).map(warning => <p className="callout" role="status" key={warning}>{warning}</p>)}
                  <div className="form-field">
                    <label className="form-label" htmlFor="ai-draft-name">模板名称</label>
                    <input
                      className="form-input"
                      id="ai-draft-name"
                      ref={aiDraftNameInput}
                      value={aiDraftName}
                      aria-invalid={aiNameError}
                      aria-describedby={aiNameError ? "ai-draft-name-error" : undefined}
                      onChange={(event) => { setAiDraftName(event.target.value); setAiNameError(false); }}
                      placeholder="给模板起个名字"
                    />
                    {aiNameError && <p className="field-error" id="ai-draft-name-error" role="alert">请填写模板名称。</p>}
                  </div>
                  <div className="form-field">
                    <label className="form-label" htmlFor="ai-draft-desc">模板说明</label>
                    <input
                      className="form-input"
                      id="ai-draft-desc"
                      maxLength={512}
                      value={aiDraftDescription}
                      onChange={(event) => setAiDraftDescription(event.target.value)}
                      placeholder="说明适用的文件和要提取的内容"
                    />
                  </div>
                  <PresentationSettings
                    value={aiDraft?.behavior} disabled={saving}
                    onChange={(behavior) => setAiDraft((current) => current ? { ...current, behavior } : current)} />
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
                        { key: `field_${crypto.randomUUID().replaceAll("-", "")}`, label: "", section: "header", example: "", value_type: "text" },
                      ])
                    }
                    type="button"
                  >
                    <Icon icon={Plus} size={13} /> 添加字段
                  </button>
                  <Disclosure className="template-advanced" title="高级设置"><div className="template-advanced-body">
                  <TemplateNameSettings value={aiDraft?.behavior} disabled={saving} onChange={(behavior) => setAiDraft((current) => current ? { ...current, behavior } : current)} />
                  </div></Disclosure>
                  {aiDraft.rule_suggestions.length > 0 ? (
                    <div className="form-field" style={{ marginTop: 16 }}>
                      <div className="ai-draft-label">AI 建议的校验规则</div>
                      <div className="small muted" style={{ marginBottom: 8 }}>
                        合法规则默认勾选，保存前可取消；不安全或引用错误的建议不会进入模板。
                      </div>
                      <div className="ai-rule-suggestions">
                        {aiDraft.rule_suggestions.map((suggestion, index) => (
                          <label className={`ai-examples-toggle${suggestion.status === "rejected" ? " rejected" : ""}`} key={`${suggestion.summary}-${index}`}>
                            <input
                              type="checkbox"
                              disabled={suggestion.status === "rejected"}
                              checked={aiSelectedRules.includes(index)}
                              onChange={(event) => setAiSelectedRules((current) => event.target.checked ? [...current, index] : current.filter((item) => item !== index))}
                            />
                            <span>
                              <strong>{suggestion.summary}</strong>
                              <span className="small muted" style={{ display: "block", marginTop: 2 }}>
                                {suggestion.explanation}
                              </span>
                              <span className="small muted" style={{ display: "block", marginTop: 4 }}>
                                {suggestion.status === "rejected"
                                  ? `已拒绝：${suggestion.reason} 该规则不会进入模板。`
                                  : aiSelectedRules.includes(index)
                                    ? "已勾选：保存后会进入模板，并用于之后处理的文件。"
                                    : "未勾选：这条规则不会进入模板。"}
                              </span>
                            </span>
                          </label>
                        ))}
                      </div>
                    </div>
                  ) : aiWithRules ? <p className="support">未添加校验规则，模板仍可保存。</p> : null}
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
                    <div className="support ai-tpl-upload-help">
                      支持 PDF / 图片 · 每份 PDF 分析首页。关键字段在后续页时，可上传该页图片；也可只填写需求。
                    </div>
                  </div>
                  <input
                    ref={aiFileInput}
                    className="visually-hidden"
                    tabIndex={-1}
                    aria-hidden="true"
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
                          <span title={file.name}>{index + 1}. {file.name}</span>
                          <small className="muted">{Math.max(1, Math.ceil(file.size / 1024))} KB</small>
                          <button
                            aria-label={`移除样例 ${index + 1} ${file.name}`}
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
                      disabled={aiProfilesState !== "ready" || saving}
                      value={aiProfilesState === "ready" ? aiProfileId : ""}
                      onChange={(event) => setAiProfileId(event.target.value)}
                    >
                      {aiProfilesState !== "ready" ? (
                        <option value="">{aiProfilesState === "error" ? "AI 方案暂时无法读取" : "正在读取 AI 方案…"}</option>
                      ) : aiProfiles.length === 0 ? (
                        <option value="">暂无可用模型方案（将使用任务激活方案）</option>
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
                      <Icon icon={Info} size={12} /> 描述越具体，生成结果越贴合；规则建议只使用受控类型，引用错误或不安全的内容会被拒绝。
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
                  <label className="ai-examples-toggle" style={{ marginTop: 10 }}>
                    <input
                      type="checkbox"
                      checked={aiWithRules}
                      onChange={(event) => setAiWithRules(event.target.checked)}
                    />
                    <span>
                      <strong>同时建议校验规则</strong>
                      <span className="small muted" style={{ display: "block", marginTop: 2 }}>
                        可选。AI 只负责建议，系统会逐条检查；保存前由你确认。
                      </span>
                    </span>
                  </label>
                </>
              )}
              </fieldset>
            </div>
            {aiError && <p className="ai-action-error field-error" role="alert">{aiError}</p>}
            <div className="modal-foot">
              {saving && <span className="small muted" role="status">正在保存模板…</span>}
              {aiGenerating ? (
                <>
                  <span className="small muted" style={{ marginRight: "auto" }}>
                    生成中，请稍候…
                  </span>
                  <button className="btn ghost" onClick={stopWaitingForAiGeneration} type="button">
                    停止等待并返回编辑
                  </button>
                </>
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
                    disabled={!aiRequirement.trim() || aiProfilesState !== "ready" || saving}
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
      <ConfirmDialog
        open={deleteTargetId !== null}
        title="确定删除这个模板吗？"
        description="它将不再用于新任务。已有数据表、文件和修订历史保留，已开始的任务仍使用当时的规则。"
        buttonLabel="删除模板"
        cancelLabel="取消"
        busy={saving}
        error={deleteError}
        onClose={() => { setDeleteTargetId(null); setDeleteError(null); }}
        onConfirm={() => { void confirmDelete(); }}
      />
      <ConfirmDialog
        open={discardAiDraftOpen}
        tone="normal"
        title="模板尚未保存"
        description="确定不保存并关闭？"
        eyebrow="未保存的草稿"
        buttonLabel="不保存并关闭"
        onClose={() => setDiscardAiDraftOpen(false)}
        onConfirm={discardAiDraft}
      />
      {historyOpen && templates.find(t => t.id === selectedId) && <TemplateHistory current={templates.find(t => t.id === selectedId)!} initialVersion={selectedId === initialTemplateId ? initialVersion : null}
        dirty={JSON.stringify(draft) !== savedDraft.current} onClose={() => setHistoryOpen(false)}
        onRestored={restored => { setHistoryOpen(false); setTemplates(items => items.map(t => t.id === restored.id ? restored : t)); selectTemplate(restored); notify(`已将第 ${restored.version} 版设为当前版本。`, "success"); }} />}
    </section>
  );
}

function toDraft(template: ExtractionTemplate): TemplateDraft {
  const draft: TemplateDraft = {
    name: template.name,
    description: template.description,
    extra_instructions: template.extra_instructions,
    fields: template.fields.map((field) => ({ ...field })),
    validation_rules: [...template.validation_rules],
    deterministic_rules: structuredClone(template.deterministic_rules ?? []),
    output_mapping: { ...template.output_mapping },
    behavior: template.behavior ? structuredClone(template.behavior) : undefined,
  };
  return template.is_system ? draft : editableTemplate(draft);
}
