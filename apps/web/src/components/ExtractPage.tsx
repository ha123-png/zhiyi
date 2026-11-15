import type { PendingDuplicate } from "./PendingDuplicateDialog";
import { NoticeDialog } from "./NoticeDialog";
import { Toast, useToast } from "./Toast";
import { createPortal } from "react-dom";
import { useModalFocus } from "../useModalFocus";
import { useUnsavedChanges } from "../useUnsavedChanges";
import { manualCellValue } from "../numericInput";
import { ExtractConfiguration } from "./ExtractConfiguration";
import { DetailRowAction } from "./DetailRowAction";
import { DetailColumnResizer } from "./DetailColumnResizer";
import { DetailPagination, useDetailPage } from "./DetailPagination";
import { FileRevisionHistory } from "./FileRevisionHistory";
import { desktopApi, type NativeImportFile } from "../desktop";
import { taskDisplayName } from "../taskNames";
import { useCallback, useEffect, useRef, useState } from "react";
import { useAssistantPageContext } from "../assistant/AssistantProvider";
import {
  ChevronLeft,
  ChevronRight,
  FileSearch,
  FileText,
  FileUp,
  History,
  List,
  Loader2,
  Maximize2,
  Minus,
  Plus,
  Save,
  ScanSearch,
  UploadCloud,
  X,
} from "lucide-react";
import {
  ApiError,
  DuplicateUploadError,
  getExtraction,
  getOriginalFileUrl,
  getTemplates,
  subscribeTaskEvents,
  updateReview,
  uploadTask,
} from "../api";
import { readBatchTasks } from "../batchTasks";
import type {
  DemoScenario,
  Extraction,
  ExtractionResult,
  ExtractionTemplate,
  DocumentResult,
  LineItem,
  Task,
  TemplateResult,
  TemplateValue,
} from "../types";
import { Icon } from "./Icon";
import { ValidationResults } from "./ValidationResults";
import { useValidationFieldLocator } from "./useValidationFieldLocator";
import { DocumentPreview } from "./DocumentPreview";
import { OriginalDownload } from "./OriginalDownload";
import { InputScopeDetails, PartialInputAction } from "./InputScopeDetails";
import { FileNameConfirmation } from "./FileNameConfirmation";
import { EditableText } from "./EditableText";
import { parseServerTime, serverDate } from "../time";
import { evidenceDescription } from "../evidence";
import { formatNumericValue } from "../numberDisplay";

function formatDateTime(iso: string): string {
  const d = serverDate(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

interface HeaderColumn {
  key: string;
  label: string;
  numeric: boolean;
  valueType?: string;
}

interface ItemColumn {
  key: string;
  label: string;
  numeric: boolean;
  valueType?: string;
}

interface ValidationIssue {
  id: string;
  row: number;
  field: string;
  msg: string;
  index: number;
  ignored: boolean;
}

const documentHeaderLabels: Record<string, string> = {
  document_type: "票据类型",
  document_number: "票据号码",
  document_date: "开票日期",
  buyer_name: "购买方",
  seller_name: "销售方",
  seller_tax_id: "销售方统一社会信用代码/纳税人识别号",
  buyer_tax_id: "购买方统一社会信用代码/纳税人识别号",
  seller_contact: "供方联系电话",
  buyer_contact: "需方联系电话",
  buyer_address: "收货地址",
  amount_before_tax: "不含税金额",
  tax_amount: "税额",
  total_amount: "价税合计",
  total_quantity: "总数量",
  remarks: "备注",
};

const documentItemLabels: Record<string, string> = {
  name: "名称",
  specification: "规格",
  unit: "单位",
  quantity: "数量",
  unit_price: "单价",
  amount: "金额",
  tax_rate: "税率",
  tax_amount: "税额",
  remarks: "备注",
};

/**
 * 抬头列按模板字段动态生成：模板改了字段（如去掉购方/销方税号），
 * 提取表头就跟着变，不残留硬编码的旧列。模板缺失时按结果里实际存在的字段
 * 动态生成（平铺的内置单据结果），保证数据始终可读、不出现空白表。
 */
function headerColumnsFor(extraction: Extraction | null): HeaderColumn[] {
  if (extraction?.current_schema?.length) return extraction.current_schema.filter(field => field.section === "header").map(field => ({
    key: field.key, label: field.label, numeric: field.value_type === "number", valueType: field.value_type,
  }));
  const template = extraction?.template;
  if (template && template.fields.some((f) => f.section === "header" && f.key)) {
    return template.fields
      .filter((f) => f.section === "header" && f.key)
      .map((f) => ({
        key: f.key as string,
        label: template.output_mapping?.[f.key as string] || f.label,
        numeric: f.value_type === "number",
        valueType: f.value_type,
      }));
  }
  if (extraction && isTemplateResult(extraction.result)) {
    const result = extraction.result;
    return Object.keys(result.header).map((key) => ({
      key,
      label: key,
      numeric: typeof result.header[key] === "number",
    }));
  }
  if (extraction) {
    const result = extraction.result as unknown as Record<string, unknown>;
    return Object.keys(result)
      .filter((key) => key !== "items")
      .map((key) => ({ key, label: documentHeaderLabels[key] ?? key, numeric: typeof result[key] === "number" }));
  }
  return [];
}

function itemColumnsFor(extraction: Extraction | null): ItemColumn[] {
  if (extraction?.current_schema?.length) return extraction.current_schema.filter(field => field.section === "item").map(field => ({
    key: field.key, label: field.label, numeric: field.value_type === "number", valueType: field.value_type,
  }));
  const template = extraction?.template;
  if (template && template.fields.some((f) => f.section === "item" && f.key)) {
    return template.fields
      .filter((f) => f.section === "item" && f.key)
      .map((f) => ({
        key: f.key as string,
        label: template.output_mapping?.[f.key as string] || f.label,
        numeric: f.value_type === "number",
        valueType: f.value_type,
      }));
  }
  if (extraction) {
    const result = extraction.result as unknown as {
      items?: Array<Record<string, unknown>>;
    };
    const firstItem = result.items?.[0] ?? {};
    return Object.keys(firstItem).map((key) => ({
      key,
      label: documentItemLabels[key] ?? key,
      numeric: typeof firstItem[key] === "number",
    }));
  }
  return [];
}

const documentHeaderKeys: Record<string, keyof Omit<DocumentResult, "items">> = {
  invoice_no: "document_number",
  invoice_date: "document_date",
  buyer_name: "buyer_name",
  seller_name: "seller_name",
  amount: "total_amount",
  tax_amount: "tax_amount",
  // 内置模板字段 key 与 DocumentResult 字段同名（发票/送货单）
  document_number: "document_number",
  document_date: "document_date",
  amount_before_tax: "amount_before_tax",
  total_amount: "total_amount",
};

const documentItemKeys: Record<string, keyof LineItem> = {
  name: "name",
  spec: "specification",
  unit: "unit",
  qty: "quantity",
  price: "unit_price",
  amount: "amount",
  tax_rate: "tax_rate",
  tax_amount: "tax_amount",
};

function isTemplateResult(result: ExtractionResult): result is TemplateResult {
  return "header" in result;
}

// 批次任务状态：进行中的状态集合 + 展示用中文
const BATCH_ACTIVE_STATUSES = new Set<Task["status"]>([
  "created",
  "queued",
  "processing",
  "validating",
  // 暂停后仍保持 SSE/轮询监听；否则恢复队列时这个 effect 已卸载，
  // 后续完成事件无法刷新提取预览。
  "paused",
  "waiting_for_template",
]);
const BATCH_STATUS_LABEL: Record<string, string> = {
  created: "等待中",
  queued: "等待中",
  processing: "处理中",
  validating: "校验中",
  waiting_for_template: "待处理事项",
  completed: "已完成",
  needs_review: "待确认",
  paused: "已暂停",
  cancelled: "已取消",
  failed: "失败",
};

interface BatchItem {
  task: Task;
}

function formatValue(value: TemplateValue | undefined): string {
  // 空值显示为空字符串，而不是"—"：避免把"空"伪装成数据，也避免编辑时误删
  if (value === null || value === undefined) return "";
  if (typeof value === "number") {
    return Number.isFinite(value) ? formatNumericValue(value) : "";
  }
  if (typeof value === "boolean") return value ? "是" : "否";
  return String(value);
}

/** 提取记录持久化：记住"最后一个文件"的提取结果，刷新页面不丢失；
 *  只有后端服务数据被清掉（任务被删）才失效。 */
const LAST_EXTRACTION_KEY = "last-extraction-record-v1";

interface LastExtractionRecord {
  task: Task;
  extraction: Extraction;
}

function saveLastExtraction(record: LastExtractionRecord) {
  try {
    localStorage.setItem(LAST_EXTRACTION_KEY, JSON.stringify(record));
  } catch {
    // 存储失败不影响主流程
  }
}

function loadLastExtraction(): LastExtractionRecord | null {
  try {
    const raw = localStorage.getItem(LAST_EXTRACTION_KEY);
    if (!raw) return null;
    const record = JSON.parse(raw) as LastExtractionRecord;
    if (!record?.task?.id || !record?.extraction?.task_id) return null;
    return record;
  } catch {
    return null;
  }
}

function clearLastExtraction() {
  try {
    localStorage.removeItem(LAST_EXTRACTION_KEY);
  } catch {
    // 忽略
  }
}

/** 批量上传队列状态持久化：切页/刷新后回到提取页，"本次加入队列"提示不丢 */
const BATCH_KEY = "extract-batch-queue-v1";

function saveBatchIds(ids: string[]) {
  try {
    localStorage.setItem(BATCH_KEY, JSON.stringify(ids));
  } catch {
    // 存储失败不影响主流程
  }
}

function loadBatchIds(): string[] {
  try {
    const raw = localStorage.getItem(BATCH_KEY);
    if (!raw) return [];
    const ids = JSON.parse(raw) as unknown;
    if (!Array.isArray(ids)) return [];
    return ids.filter((id): id is string => typeof id === "string");
  } catch {
    return [];
  }
}

function buildHeaderValues(
  extraction: Extraction,
  columns: HeaderColumn[],
): Record<string, string> {
  const result = extraction.result;
  const values: Record<string, string> = {};

  if (isTemplateResult(result)) {
    for (const col of columns) {
      values[col.key] = formatValue(result.header[col.key]);
    }
  } else {
    // 模板列 key 与 DocumentResult 字段同名时直接取；旧短 key 走映射；都没有则为空
    const source = result as unknown as Record<string, TemplateValue>;
    for (const col of columns) {
      const direct = source[col.key];
      const target = documentHeaderKeys[col.key];
      values[col.key] =
        direct !== undefined
          ? formatValue(direct)
          : target
            ? formatValue(result[target])
            : "";
    }
  }

  return values;
}

function buildItemRows(
  extraction: Extraction,
  columns: ItemColumn[],
): Record<string, string>[] {
  const result = extraction.result;

  if (isTemplateResult(result)) {
    return result.items.map((item) => {
      const row: Record<string, string> = {};
      for (const col of columns) {
        row[col.key] = formatValue(item[col.key]);
      }
      return row;
    });
  }

  return result.items.map((item) => {
    const row: Record<string, string> = {};
    const source = item as unknown as Record<string, TemplateValue>;
    for (const col of columns) {
      const direct = source[col.key];
      const target = documentItemKeys[col.key];
      row[col.key] =
        direct !== undefined
          ? formatValue(direct)
          : target
            ? formatValue(item[target])
            : "";
    }
    return row;
  });
}

function mapValidationIssues(extraction: Extraction): ValidationIssue[] {
  return extraction.validation_issues.map((issue, idx) => {
    const match = issue.field.match(/^items\[(\d+)\]\.(.+)$/);
    if (match) {
      return {
        id: issue.code || `v${idx}`,
        row: parseInt(match[1], 10) + 1,
        field: match[2],
        msg: issue.message,
        index: idx,
        ignored: issue.ignored === true,
      };
    }
    return {
      id: issue.code || `v${idx}`,
      row: 0,
      field: issue.field.replace(/^header\./, ""),
      msg: issue.message,
      index: idx,
      ignored: issue.ignored === true,
    };
  });
}

function headerEvidencePath(extraction: Extraction, key: string): string | null {
  if (isTemplateResult(extraction.result)) return `header.${key}`;
  const mapped = documentHeaderKeys[key];
  if (mapped) return mapped;
  // 模板 key 与 DocumentResult 字段同名时直接用 key 作证据路径，避免多个格子共用 null 路径而捆绑选中
  const result = extraction.result as unknown as Record<string, unknown>;
  return key in result ? key : null;
}

function itemEvidencePath(extraction: Extraction, row: number, key: string): string {
  if (isTemplateResult(extraction.result)) return `items[${row}].${key}`;
  return `items[${row}].${documentItemKeys[key] ?? key}`;
}

// 记住用户上次的模板选择（智能匹配 / 手动 + 模板）：默认沿用上次用过的模式，
// 而不是每次回到页面都重置成智能匹配
const LAST_UPLOAD_MODE_KEY = "last-upload-mode-v1";

function readLastUploadMode(): { mode: "auto" | "manual"; templateId: string } {
  try {
    const raw = localStorage.getItem(LAST_UPLOAD_MODE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as {
        mode?: "auto" | "manual";
        templateId?: string;
      };
      if (parsed.mode === "auto" || parsed.mode === "manual") {
        return {
          mode: parsed.mode,
          templateId:
            typeof parsed.templateId === "string" ? parsed.templateId : "",
        };
      }
    }
  } catch {
    // 本地存储异常不影响默认行为
  }
  return { mode: "auto", templateId: "" };
}

function writeLastUploadMode(mode: "auto" | "manual", templateId: string) {
  try {
    localStorage.setItem(
      LAST_UPLOAD_MODE_KEY,
      JSON.stringify({ mode, templateId }),
    );
  } catch {
    // 本地存储异常时忽略，仅影响下次默认值
  }
}

interface ExtractPageProps {
  initialTask?: Task | null;
  demo?: DemoScenario | null;
  onNavigateHistory?: () => void;
  onTasksChange?: () => void;
  /** 上传请求发出前立即调用：前端先显示文件并开始计时，不等后端响应 */
  onUploadStarted?: (files: { tempId: string; filename: string }[]) => void;
  /** 上传完成后调用：真实任务替换乐观任务，并沿用开始上传的时刻继续计时 */
  onUploadsComplete?: (entries: {
    tempId: string;
    realId: string;
    startedAt: number;
  }[]) => void;
  noticeHost?: HTMLElement | null;
  onDuplicate?: (item: PendingDuplicate) => void;
}

export function ExtractPage({
  initialTask = null,
  demo = null,
  onNavigateHistory,
  onTasksChange,
  onUploadStarted,
  onUploadsComplete,
  noticeHost,
  onDuplicate,
}: ExtractPageProps = {}) {
  const [initialMode] = useState(readLastUploadMode);
  const [matchMode, setMatchMode] = useState<"auto" | "manual">(initialMode.mode);
  const [dragOver, setDragOver] = useState(false);
  const [savedReview, setSavedReview] = useState<{ taskId: string; version: number } | null>(null);
  const [editingExtractCell, setEditingExtractCellState] = useState<string | null>(
    null,
  );
  const editingExtractCellRef = useRef<string | null>(null);
  function setEditingExtractCell(value: string | null) {
    if (value !== null) setSavedReview(null);
    editingExtractCellRef.current = value;
    setEditingExtractCellState(value);
  }
  const [validationPanelOpen, setValidationPanelOpen] = useState(true);
  const [reviewConflict, setReviewConflict] = useState(false);
  const [previewScale, setPreviewScale] = useState(1.0);
  const [previewPage, setPreviewPage] = useState(1);
  const [previewPageCount, setPreviewPageCount] = useState(1);
  const [previewFullscreen, setPreviewFullscreen] = useState(false);
  const previewDialog = useModalFocus(previewFullscreen, () => setPreviewFullscreen(false));
  const [selectedEvidencePath, setSelectedEvidencePath] = useState<string | null>(
    null,
  );
  const [extractValidationIssues, setExtractValidationIssues] = useState<
    ValidationIssue[]
  >([]);

  const [templates, setTemplates] = useState<ExtractionTemplate[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(true);
  const [templatesError, setTemplatesError] = useState<string | null>(null);
  const [templatesReload, setTemplatesReload] = useState(0);
  const templatesRequest = useRef(0);
  const [selectedTemplateId, setSelectedTemplateId] = useState<string>(
    initialMode.templateId,
  );
  const [task, setTask] = useState<Task | null>(null);
  useEffect(() => { setSavedReview(null); }, [task?.id, initialTask?.id, demo]);
  const loadSeqRef = useRef(0);
  useAssistantPageContext("extract", { task_id: task?.id ?? null, template_id: task?.template_id ?? null, template_version: task?.template_version ?? null });
  const [extraction, setExtraction] = useState<Extraction | null>(null);
  const [nameChoices, setNameChoices] = useState<Record<string, string>>({});
  const [fileHistoryOpen, setFileHistoryOpen] = useState(false);
  const [draftDetailIds, setDraftDetailIds] = useState<number[] | undefined>();
  useEffect(() => {
    setDraftDetailIds(extraction?.detail_ids);
  }, [extraction]);
  const [draftResult, setDraftResult] = useState<ExtractionResult | null>(null);
  const [loading, setLoading] = useState(false);
  const uploadInFlight = useRef(false);
  const [uploadErrors, setUploadErrors] = useState<{ file: string; message: string }[]>([]);
  const [noticesOpen, setNoticesOpen] = useState(false);
  const [saveNoticeOpen, setSaveNoticeOpen] = useState(false);
  const [scopeNoticeBusy, setScopeNoticeBusy] = useState(false);
  const { toast, notify, clear } = useToast();
  const [saving, setSavingState] = useState(false);
  const savingRef = useRef(false);
  function setSaving(value: boolean) {
    savingRef.current = value;
    setSavingState(value);
  }
  const [error, setError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  useEffect(() => { setSaveError(null); setSaveNoticeOpen(false); clear(); }, [task?.id, initialTask?.id, clear]);
  // 批量上传：本次放入队列的任务列表（状态随轮询刷新）
  const [batch, setBatch] = useState<BatchItem[]>([]);
  const [batchReadError, setBatchReadError] = useState<string | null>(null);
  const [batchRestoring, setBatchRestoring] = useState(false);
  const [batchRestoreAttempt, setBatchRestoreAttempt] = useState(0);
  const [batchRefreshAttempt, setBatchRefreshAttempt] = useState(0);
  // A new selection starts a new automatic-preview scope. Older tasks remain
  // in the queue but their completed results must not hide this upload's failure.
  const latestUploadIdsRef = useRef<Set<string> | null>(null);
  const autoPreviewScopeRef = useRef(0);
  const initialTaskIdRef = useRef(initialTask?.id ?? null);
  initialTaskIdRef.current = initialTask?.id ?? null;
  const [pendingAutoResult, setPendingAutoResult] = useState<{ task: Task; scope: number; error: string | null } | null>(null);

  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const resultSectionRef = useRef<HTMLDivElement | null>(null);
  const scrolledHistoryTaskRef = useRef<string | null>(null);

  useEffect(() => {
    if (!demo) return;
    const now = new Date().toISOString();
    const scenarios: Record<DemoScenario, { filename: string; source: string; name: string; result: TemplateResult; fields: ExtractionTemplate["fields"]; issues: Extraction["validation_issues"] }> = {
      sentiment: {
        filename: "新闻报道.txt", name: "新闻情感分析",
        source: "某市发布公共交通优化方案，将新增夜间线路并降低换乘成本。多位市民表示期待，但也担心高峰期运力是否充足。",
        result: { header: { 情感倾向: "审慎乐观", 核心依据: "便利性提升获得期待，同时存在运力担忧" }, items: [] },
        fields: [{ key: "情感倾向", label: "情感倾向", section: "header", example: "积极", instructions: "", value_type: "text" }, { key: "核心依据", label: "核心依据", section: "header", example: "", instructions: "", value_type: "text" }], issues: [],
      },
      article: {
        filename: "行业观察.md", name: "文章关键信息",
        source: "# 本地大模型进入实用阶段\n作者：林知远\n文章讨论小型多模态模型如何在普通电脑上承担文档理解任务，并分析隐私、成本与准确率之间的取舍。",
        result: { header: { 标题: "本地大模型进入实用阶段", 作者: "林知远", 摘要: "小型多模态模型正在普通电脑上承担文档理解任务。", 关键词: "本地模型、多模态、文档理解" }, items: [] },
        fields: ["标题", "作者", "摘要", "关键词"].map((label) => ({ key: label, label, section: "header" as const, example: "", instructions: "", value_type: "text" as const })), issues: [],
      },
      grading: {
        filename: "数学作业.txt", name: "作业批改",
        source: "数学作业（八题）\n1. 12+8=20  2. 36÷6=5  3. 7×9=63  4. 45-17=28\n5. 3/4+1/4=1  6. 2.5×4=10  7. 18÷3=6  8. 6²=36\n\n模板额外提示词中的标准答案：1.20；2.6；3.63；4.28；5.1；6.10；7.6；8.36。",
        result: { header: { 批改结果: "7/8", 说明: "标准答案等固定评分口径可以写在字段模板的额外提示词中" }, items: [
          { 题号: 1, 学生答案: "20", 正确答案: "20", 是否正确: true }, { 题号: 2, 学生答案: "5", 正确答案: "6", 是否正确: false },
          { 题号: 3, 学生答案: "63", 正确答案: "63", 是否正确: true }, { 题号: 4, 学生答案: "28", 正确答案: "28", 是否正确: true },
          { 题号: 5, 学生答案: "1", 正确答案: "1", 是否正确: true }, { 题号: 6, 学生答案: "10", 正确答案: "10", 是否正确: true },
          { 题号: 7, 学生答案: "6", 正确答案: "6", 是否正确: true }, { 题号: 8, 学生答案: "36", 正确答案: "36", 是否正确: true },
        ] },
        fields: [
          { key: "批改结果", label: "批改结果", section: "header", example: "7/8", instructions: "", value_type: "text" },
          { key: "说明", label: "标准答案来源", section: "header", example: "模板额外提示词", instructions: "", value_type: "text" },
          ...["题号", "学生答案", "正确答案", "是否正确"].map((label) => ({ key: label, label, section: "item" as const, example: "", instructions: "", value_type: (label === "题号" ? "number" : label === "是否正确" ? "boolean" : "text") as "number" | "boolean" | "text" })),
        ], issues: [],
      },
      mistakes: {
        filename: "错题记录.txt", name: "错题分析",
        source: "原题：一辆汽车 3 小时行驶 180 千米，平均每小时行驶多少千米？\n学生答案：180×3=540（千米）",
        result: { header: {}, items: [{ 原题目: "汽车3小时行驶180千米，求平均速度", 正确解析: "平均速度=总路程÷总时间=180÷3=60千米/小时", 易错点: "把求平均量误写成乘法", 举一反三: "240千米用4小时，平均每小时多少千米？", 举一反三答案: "240÷4=60千米/小时" }] },
        fields: ["原题目", "正确解析", "易错点", "举一反三", "举一反三答案"].map((label) => ({ key: label, label, section: "item" as const, example: "", instructions: "", value_type: "text" as const })), issues: [],
      },
      business: {
        filename: "示例送货单.txt", name: "发票／送货单解析",
        source: "送货单 R-20260815\n供货方：知意科技有限公司\n收货方：示例客户\n打印纸 A4 10箱 单价128元 金额1280元",
        result: { header: { 单据编号: "R-20260815", 供货方: "知意科技有限公司", 收货方: "示例客户", 合计金额: 1280 }, items: [{ 商品: "打印纸 A4", 数量: 10, 单位: "箱", 单价: 128, 金额: 1280 }] },
        fields: [{ key: "单据编号", label: "单据编号", section: "header", example: "", instructions: "", value_type: "text" }, { key: "供货方", label: "供货方", section: "header", example: "", instructions: "", value_type: "text" }, { key: "收货方", label: "收货方", section: "header", example: "", instructions: "", value_type: "text" }, { key: "合计金额", label: "合计金额", section: "header", example: "", instructions: "", value_type: "number" }, ...["商品", "数量", "单位", "单价", "金额"].map((label) => ({ key: label, label, section: "item" as const, example: "", instructions: "", value_type: (["数量", "单价", "金额"].includes(label) ? "number" : "text") as "number" | "text" }))], issues: [],
      },
      rule: {
        filename: "金额异常发票.txt", name: "规则兜底",
        source: "服务费 1000.00元\n税额 60.00元\n价税合计 1160.00元",
        result: { header: { 不含税金额: 1000, 税额: 60, 价税合计: 1160 }, items: [] },
        fields: ["不含税金额", "税额", "价税合计"].map((label) => ({ key: label, label, section: "header" as const, example: "", instructions: "", value_type: "number" as const })),
        issues: [{ code: "amount_mismatch", field: "价税合计", message: "规则校验：不含税金额 + 税额应为 1060.00，与价税合计 1160.00 不一致。", severity: "error" }],
      },
    };
    const scenario = scenarios[demo];
    const demoTask: Task = {
      id: "onboarding-demo",
      filename: scenario.filename,
      content_type: "text/plain",
      size_bytes: 0,
      page_count: 1,
      sha256: "",
      template_mode: "manual",
      template_id: null,
      template_version: null,
      candidate_templates: [],
      status: "completed",
      duplicate_of_task_id: null,
      created_at: now,
      completed_at: now,
      updated_at: now,
    };
    const demoResult: ExtractionResult = scenario.result;
    const demoTemplate: ExtractionTemplate = { id: `demo-${demo}`, version: 1, is_system: true, builtin_key: null, source_template_id: null, name: scenario.name, description: "内置只读演示", extra_instructions: "", fields: scenario.fields, validation_rules: [], deterministic_rules: [], output_mapping: {}, created_at: now, updated_at: now };
    const demoExtraction: Extraction = {
      task_id: demoTask.id,
      document_kind: "custom",
      template_id: demoTemplate.id,
      template_version: 1,
      template: demoTemplate,
      model_name: "内置示例",
      prompt_version: "demo",
      elapsed_seconds: 0,
      review_version: 0,
      original_result: demoResult,
      result: demoResult,
      validation_issues: scenario.issues,
      evidence: [],
    };
    setTask(demoTask);
    setReviewConflict(false);
    setExtraction(demoExtraction);
    setDraftResult(structuredClone(demoResult));
    setExtractValidationIssues(scenario.issues.map((issue, index) => ({ id: `${issue.code}-${index}`, index, row: 0, field: issue.field, msg: issue.message, ignored: false })));
  }, [demo]);

  useEffect(() => {
    if (!demo || !extraction) return;
    const frame = window.requestAnimationFrame(() => {
      resultSectionRef.current?.scrollIntoView({ block: "start", behavior: "auto" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [demo, extraction]);

  // 恢复"本次加入队列"的状态：切页/刷新后回来，批量队列提示不丢
  useEffect(() => {
    if (demo || latestUploadIdsRef.current !== null) return;
    const savedIds = loadBatchIds();
    if (savedIds.length === 0) return;
    const controller = new AbortController();
    let active = true;
    setBatchRestoring(true);
    setBatchReadError(null);
    readBatchTasks(savedIds, controller.signal)
      .then(({ tasks }) => {
        // A delayed restoration must not replace files selected since mount.
        if (!active || latestUploadIdsRef.current !== null) return;
        setBatch(tasks.map(task => ({ task })));
        // Only a complete, successful ID-scoped read can prove IDs are gone.
        saveBatchIds(tasks.map(task => task.id));
      })
      .catch((reason: unknown) => {
        if (active && latestUploadIdsRef.current === null) {
          setBatchReadError(reason instanceof Error ? reason.message : "上次队列暂时无法读取。");
        }
      })
      .finally(() => { if (active) setBatchRestoring(false); });
    return () => { active = false; controller.abort(); };
  }, [demo, batchRestoreAttempt]);

  // Remember which file was open, but reload its current facts from the service.
  // Cached values may predate warehouse edits, deletion, or a backup restore.
  useEffect(() => {
    if (demo || initialTask) return;
    const record = loadLastExtraction();
    if (!record) return;
    const seq = ++loadSeqRef.current;
    setTask(record.task);
    setReviewConflict(false);
    setExtraction(null);
    setDraftResult(null);
    setExtractValidationIssues([]);
    setLoading(true);
    setPreviewPage(1);
    setPreviewPageCount(record.task.page_count ?? 1);
    getExtraction(record.task.id).then((result) => {
      if (seq !== loadSeqRef.current) return;
      setExtraction(result);
      setDraftResult(structuredClone(result.result));
      setExtractValidationIssues(mapValidationIssues(result));
      saveLastExtraction({ task: record.task, extraction: result });
    }).catch((reason: unknown) => {
      if (seq !== loadSeqRef.current) return;
      if (reason instanceof ApiError && reason.status === 404) {
        clearLastExtraction(); setTask(null);
        return;
      }
      setError(reason instanceof Error ? reason.message : "读取上次文件失败，请在文件历史中重新打开。");
    }).finally(() => { if (seq === loadSeqRef.current) setLoading(false); });
  }, [demo]);

  useEffect(() => {
    let active = true;
    const sequence = ++templatesRequest.current;
    setTemplatesLoading(true);
    setTemplatesError(null);
    getTemplates()
      .then((data) => { if (active && sequence === templatesRequest.current) setTemplates(data); })
      .catch((reason) => {
        if (active && sequence === templatesRequest.current) setTemplatesError(reason instanceof Error ? reason.message : "模板读取失败。");
      })
      .finally(() => { if (active && sequence === templatesRequest.current) setTemplatesLoading(false); });
    return () => { active = false; };
  }, [templatesReload]);

  // 上次记忆的手动模板若已被删除/停用，回退到空选择，避免提交无效模板
  useEffect(() => {
    if (
      templates.length > 0 &&
      matchMode === "manual" &&
      selectedTemplateId &&
      !templates.some((t) => t.id === selectedTemplateId)
    ) {
      setSelectedTemplateId("");
    }
  }, [templates, matchMode, selectedTemplateId]);

  useEffect(() => {
    if (!initialTask) return;
    let cancelled = false;
    const seq = ++loadSeqRef.current;
    setLoading(true);
    setSaving(false);
    setError(null);
    setReviewConflict(false);
    setExtraction(null);
    setDraftResult(null);
    setExtractValidationIssues([]);
    setEditingExtractCell(null);
    setTask(initialTask);
    setPreviewPage(1);
    setPreviewPageCount(initialTask.page_count ?? 1);
    setSelectedEvidencePath(null);
    if (initialTask.status === "waiting_for_template" && initialTask.pending_action !== "confirm_target_table") {
      setExtraction(null); setDraftResult(null); setLoading(false);
      setBatch((current) => current.some((item) => item.task.id === initialTask.id) ? current : [...current, { task: initialTask }]);
      return;
    }
    getExtraction(initialTask.id)
      .then((result) => {
        if (cancelled || seq !== loadSeqRef.current) return;
        setExtraction(result);
        setDraftResult(structuredClone(result.result));
        setExtractValidationIssues(mapValidationIssues(result));
      })
      .catch((err) => {
        if (cancelled || seq !== loadSeqRef.current) return;
        if (err instanceof ApiError && err.status === 404 &&
            (initialTask.status === "completed" || initialTask.status === "needs_review")) {
          if (loadLastExtraction()?.task.id === initialTask.id) clearLastExtraction();
          setTask(null);
          return;
        }
        setError(err instanceof Error ? err.message : "读取提取结果失败");
      })
      .finally(() => {
        if (!cancelled && seq === loadSeqRef.current) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [initialTask]);

  useEffect(() => {
    if (!initialTask || extraction?.task_id !== initialTask.id || scrolledHistoryTaskRef.current === initialTask.id) return;
    scrolledHistoryTaskRef.current = initialTask.id;
    resultSectionRef.current?.scrollIntoView({ block: "start", behavior: "auto" });
  }, [initialTask, extraction]);

  const hasExtractData = extraction !== null && task !== null && extraction.task_id === task.id;
  const extractedSourceName = task ? taskDisplayName(task) : "";
  const batchActive = batch.some((b) => BATCH_ACTIVE_STATUSES.has(b.task.status));
  // 批量处理中保留最近完成的结果，下一份完成后再平滑替换；不能因为队列仍活动
  // 就把用户正在核对的结果隐藏掉。
  const showExtractData = hasExtractData;
  const canEditExtraction = !demo && Boolean(task && ["completed", "needs_review"].includes(task.status))
    && extraction?.has_current_data !== false;
  // 待处理任务（含待匹配）：状态条只显示这些，已完成/失败请去状态监控看
  const pendingBatch = batch.filter((b) => BATCH_ACTIVE_STATUSES.has(b.task.status));
  const displayExtraction = hasExtractData && extraction
    ? (draftResult ? { ...extraction, result: draftResult } : extraction)
    : null;
  const headerColumns = headerColumnsFor(displayExtraction);
  const itemColumns = itemColumnsFor(displayExtraction);
  const extractedColumns = [...headerColumns, ...itemColumns];
  const headerValues = displayExtraction ? buildHeaderValues(displayExtraction, headerColumns) : {};
  const itemRows = displayExtraction ? buildItemRows(displayExtraction, itemColumns) : [];
  const [detailWidths, setDetailWidths] = useState<Record<string, number>>({});
  const detailWidth = (key: string) => detailWidths[key] ?? 132;
  const detailPage = useDetailPage(itemRows.length, task?.id);
  const locateValidationField = useValidationFieldLocator(resultSectionRef, task?.id);
  const selectedEvidence = extraction?.evidence?.find(
    (item) => item.field_path === selectedEvidencePath,
  );
  const evidenceLabel = evidenceDescription(selectedEvidence);
  const isReviewDirty = () => {
    if (demo || !task || !extraction || extraction.task_id !== task.id || !draftResult) return false;
    if (editingExtractCellRef.current !== null) return true;
    const baselineIgnored = extraction.validation_issues
      .map((issue, index) => issue.ignored === true ? index : -1)
      .filter((index) => index >= 0);
    const draftIgnored = extractValidationIssues
      .filter((issue) => issue.ignored)
      .map((issue) => issue.index);
    const baselineName = extraction.file_name?.confirmed_filename
      ?? extraction.file_name?.suggested_filename
      ?? task.filename;
    const chosenName = nameChoices[task.id] ?? baselineName;
    return JSON.stringify(draftResult) !== JSON.stringify(extraction.result)
      || JSON.stringify(draftDetailIds) !== JSON.stringify(extraction.detail_ids)
      || JSON.stringify(draftIgnored) !== JSON.stringify(baselineIgnored)
      || chosenName !== baselineName;
  };
  const reviewDirtyRef = useRef(isReviewDirty);
  reviewDirtyRef.current = isReviewDirty;
  const unsavedReview = useUnsavedChanges(() => reviewDirtyRef.current());

  const templateDisplayName = (() => {
    // Upload controls describe the next file, never how a historical result was produced.
    const prefix = task ? (task.template_mode === "smart" ? "智能匹配" : "手动") : null;
    if (extraction?.template) {
      return `${prefix ? `${prefix} · ` : ""}${extraction.template.name}`;
    }
    return "待识别模板";
  })();

  function zoomPreview(delta: number) {
    setPreviewScale((s) => Math.min(3, Math.max(0.5, +(s + delta).toFixed(1))));
  }

  function selectEvidence(path: string | null) {
    setSelectedEvidencePath(path);
    const evidence = extraction?.evidence?.find((item) => item.field_path === path);
    if (evidence?.page_number) setPreviewPage(evidence.page_number);
  }

  function templateSelection(): string {
    if (matchMode === "auto") return "smart";
    return selectedTemplateId;
  }

  /** 读取并展示某个任务的提取结果（批量列表点击 / 轮询到完成时调用）。
   *  用自增序号守卫：并发多次 getExtraction 时只让最新一次写入状态，
   *  避免慢响应晚到把新结果覆盖成旧文件。 */
  // 记录当前自动展示的完成任务。同一轮轮询只选择 updated_at 最新的一份，
  // 避免多个请求争抢；后续任务完成时再替换。
  const autoDisplayedTaskIdRef = useRef<string | null>(null);
  const autoLoadingTaskIdsRef = useRef(new Set<string>());
  const pendingAutoResultRef = useRef(pendingAutoResult);
  pendingAutoResultRef.current = pendingAutoResult;
  const setPendingAutoResultValue = useCallback((value: typeof pendingAutoResult) => {
    pendingAutoResultRef.current = value;
    setPendingAutoResult(value);
  }, []);
  const offerAutoResult = useCallback((loadedTask: Task, scope: number, error: string | null = null) => {
    const current = pendingAutoResultRef.current;
    if (current?.scope === scope && parseServerTime(current.task.updated_at) > parseServerTime(loadedTask.updated_at)) return;
    setPendingAutoResultValue({ task: loadedTask, scope, error: current?.task.id === loadedTask.id && error === null ? current.error : error });
  }, [setPendingAutoResultValue]);
  const isCurrentAutoPreview = useCallback((loadedTask: Task, scope: number, selectedTaskId: string | null) => {
    return scope === autoPreviewScopeRef.current
      && selectedTaskId === initialTaskIdRef.current
      && (latestUploadIdsRef.current === null || latestUploadIdsRef.current.has(loadedTask.id));
  }, []);
  const shouldDeferAutoPreview = useCallback(() => {
    return savingRef.current || uploadInFlight.current
      || editingExtractCellRef.current !== null || reviewDirtyRef.current();
  }, []);
  const loadResultForTask = useCallback(async (loadedTask: Task, explicit = false) => {
    const scope = autoPreviewScopeRef.current;
    const selectedTaskId = initialTaskIdRef.current;
    if (!isCurrentAutoPreview(loadedTask, scope, selectedTaskId)) return;
    // Opening a history item is an explicit selection. A remembered batch must
    // not replace it unless this page has since started a new upload batch.
    if (!explicit && selectedTaskId && latestUploadIdsRef.current === null) return;
    const pending = pendingAutoResultRef.current;
    if (!explicit && pending?.scope === scope) {
      offerAutoResult(loadedTask, scope, pending.task.id === loadedTask.id ? pending.error : null);
      return;
    }
    if (!explicit && shouldDeferAutoPreview()) {
      offerAutoResult(loadedTask, scope);
      return;
    }
    if ((!explicit && autoDisplayedTaskIdRef.current === loadedTask.id) || autoLoadingTaskIdsRef.current.has(loadedTask.id)) return;
    if (savingRef.current || uploadInFlight.current) return;
    autoLoadingTaskIdsRef.current.add(loadedTask.id);
    const seq = ++loadSeqRef.current;
    try {
      const result = await getExtraction(loadedTask.id);
      if (!isCurrentAutoPreview(loadedTask, scope, selectedTaskId)) return;
      if (shouldDeferAutoPreview()) {
        offerAutoResult(loadedTask, scope);
        return;
      }
      const pending = pendingAutoResultRef.current;
      if (pending?.scope === scope && parseServerTime(pending.task.updated_at) > parseServerTime(loadedTask.updated_at)) return;
      if (seq !== loadSeqRef.current) return;
      setTask(loadedTask);
      setReviewConflict(false);
      setExtraction(result);
      setDraftResult(structuredClone(result.result));
      setExtractValidationIssues(mapValidationIssues(result));
      setPreviewPage(1);
      setPreviewPageCount(loadedTask.page_count ?? 1);
      setSelectedEvidencePath(null);
      editingExtractCellRef.current = null;
      setEditingExtractCellState(null);
      setError(null);
      saveLastExtraction({ task: loadedTask, extraction: result });
      autoDisplayedTaskIdRef.current = loadedTask.id;
      if (pendingAutoResultRef.current?.task.id === loadedTask.id) setPendingAutoResultValue(null);
    } catch (err) {
      if (!isCurrentAutoPreview(loadedTask, scope, selectedTaskId)) return;
      if (shouldDeferAutoPreview()) {
        offerAutoResult(loadedTask, scope, err instanceof Error ? err.message : "读取提取结果失败");
        return;
      }
      if (seq !== loadSeqRef.current) return;
      offerAutoResult(loadedTask, scope, err instanceof Error ? err.message : "读取提取结果失败");
    } finally {
      autoLoadingTaskIdsRef.current.delete(loadedTask.id);
    }
  }, [isCurrentAutoPreview, offerAutoResult, setPendingAutoResultValue, shouldDeferAutoPreview]);

  function openPendingAutoResult() {
    const pending = pendingAutoResultRef.current;
    if (!pending || pending.scope !== autoPreviewScopeRef.current || savingRef.current || uploadInFlight.current) return;
    unsavedReview.protect(() => {
      discardCurrentReviewDraft();
      setEditingExtractCell(null);
      void loadResultForTask(pending.task, true);
    });
  }

  function discardCurrentReviewDraft() {
    if (extraction) {
      setDraftResult(structuredClone(extraction.result));
      setDraftDetailIds(extraction.detail_ids);
      setExtractValidationIssues(mapValidationIssues(extraction));
    }
    if (task) {
      setNameChoices((current) => {
        if (!(task.id in current)) return current;
        const next = { ...current };
        delete next[task.id];
        return next;
      });
    }
  }

  /** A new upload replaces the active result, so it must pass the same discard guard as navigation. */
  function handleFiles(files: (File | NativeImportFile)[], duplicatePolicy: "reject" | "allow" = "reject", selectedTemplate?: string) {
    if (files.length === 0) return;
    unsavedReview.protect(() => {
      discardCurrentReviewDraft();
      void processFiles(files, duplicatePolicy, selectedTemplate);
    });
  }

  /** 批量上传：每个文件入队为一个任务，全部进入队列后由后端串行处理。 */
  async function processFiles(files: (File | NativeImportFile)[], duplicatePolicy: "reject" | "allow" = "reject", selectedTemplate?: string) {
    if (files.length === 0 || uploadInFlight.current) return;
    uploadInFlight.current = true;
    setError(null);
    if (duplicatePolicy === "reject") setUploadErrors([]);
    setReviewConflict(false);
    setLoading(true);
    const selection = selectedTemplate ?? templateSelection();
    const startedAt = Date.now();
    // 上传请求发出前：前端立即把文件显示在全局任务条并开始计时，
    // 不等待后端创建任务的异步响应；上传完成后由真实任务无缝替换。
    const tempEntries = files.map((file, index) => ({
      tempId: `optimistic-${startedAt}-${index}`,
      filename: file.name,
    }));
    onUploadStarted?.(tempEntries);
    const uploaded: BatchItem[] = [];
    const completed: { tempId: string; realId: string; startedAt: number }[] = [];
    for (let index = 0; index < files.length; index++) {
      const file = files[index];
      try {
        const uploadedTask = await uploadTask(
          file,
          selection,
          undefined,
          duplicatePolicy,
        );
        uploaded.push({ task: uploadedTask });
        completed.push({
          tempId: tempEntries[index].tempId,
          realId: uploadedTask.id,
          startedAt,
        });
        if (uploaded.length === 1) {
          // A new source must never be paired with the previous file's values.
          autoPreviewScopeRef.current += 1;
          autoDisplayedTaskIdRef.current = null;
          setPendingAutoResultValue(null);
          latestUploadIdsRef.current = new Set();
          setBatchRestoring(false);
          setBatchReadError(null);
          loadSeqRef.current += 1;
          setSaving(false);
          setExtraction(null);
          setDraftResult(null);
          setExtractValidationIssues([]);
          setEditingExtractCell(null);
          setSelectedEvidencePath(null);
          setTask(uploadedTask);
          setPreviewPage(1);
          setPreviewPageCount(uploadedTask.page_count ?? 1);
        }
        latestUploadIdsRef.current!.add(uploadedTask.id);
      } catch (err) {
        if (err instanceof DuplicateUploadError) {
          onDuplicate?.({ id: crypto.randomUUID(), name: file.name, size: file.size,
            selection, existingTaskId: err.taskId, sha256: err.sha256, createdAt: new Date(startedAt).toISOString() });
        } else {
          setUploadErrors(errors => [...errors, { file: file.name, message: err instanceof Error ? err.message : "上传失败" }]);
        }
      }
    }
    uploadInFlight.current = false;
    setLoading(false);
    onUploadsComplete?.(completed);
    if (uploaded.length === 0) return;
    setBatch((prev) => {
      const next = [...prev, ...uploaded];
      saveBatchIds(next.map((b) => b.task.id));
      return next;
    });
    // 立即同步全局任务条：上传成功即刻显示"正在识别"，不等下一次轮询
    onTasksChange?.();
  }

  async function chooseFiles() {
    const pick = desktopApi()?.choose_import_files;
    if (!pick) { fileInputRef.current?.click(); return; }
    try { await handleFiles(await pick()); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "文件选择未能完成，请重试。"); }
  }

  function handleDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    const files = Array.from(e.dataTransfer.files ?? []);
    void handleFiles(files);
  }

  function handleFileInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    void handleFiles(files);
    // 允许重复选择同一文件
    e.target.value = "";
  }

  // 批次任务刷新：以 SSE 事件驱动（任务状态变化时后端推送），低频轮询仅作断线兜底。
  // 拉取后若批次任务内容无变化则不触发重渲染，避免多重刷新导致界面闪烁。
  useEffect(() => {
    const hasActive = batch.some((b) => BATCH_ACTIVE_STATUSES.has(b.task.status));
    if (!batch.length || (!hasActive && batchRefreshAttempt === 0)) return;
    let cancelled = false;
    let inFlight = false;
    let controller: AbortController | undefined;
    const refresh = () => {
      if (inFlight) return;
      inFlight = true;
      const request = new AbortController();
      controller = request;
      void (async () => {
        try {
          const { tasks: items, missingIds } = await readBatchTasks(batch.map(item => item.task.id), request.signal);
          if (cancelled) return;
          setBatchReadError(null);
          const byId = new Map(items.map((t) => [t.id, t]));
          const missing = new Set(missingIds);
          let changed = missing.size > 0;
          const refreshedBatch = batch.filter(item => !missing.has(item.task.id)).map((b) => {
            const cur = byId.get(b.task.id);
            if (cur && (cur.updated_at !== b.task.updated_at || cur.status !== b.task.status)) changed = true;
            return cur ? { ...b, task: cur } : b;
          });
          if (!changed) return;
          setBatch(refreshedBatch);
          saveBatchIds(refreshedBatch.map(item => item.task.id));
          setTask((current) => current ? byId.get(current.id) ?? current : current);
          onTasksChange?.();
          const latestResult = refreshedBatch
            .map((item) => item.task)
            .filter((item) => latestUploadIdsRef.current === null || latestUploadIdsRef.current.has(item.id))
            .filter((item) => item.status === "completed" || item.status === "needs_review")
            .sort((left, right) => parseServerTime(right.updated_at) - parseServerTime(left.updated_at))[0];
          if (latestResult && autoDisplayedTaskIdRef.current !== latestResult.id) {
            void loadResultForTask(latestResult);
          }
        } catch (reason: unknown) {
          if (!cancelled) setBatchReadError(reason instanceof Error ? reason.message : "队列状态暂时无法更新。");
        } finally {
          inFlight = false;
        }
      })();
    };
    const unsub =
      !hasActive || typeof EventSource === "undefined"
        ? undefined
        : subscribeTaskEvents(refresh);
    const timer = hasActive ? window.setInterval(refresh, 5000) : undefined;
    refresh();
    return () => {
      cancelled = true;
      controller?.abort();
      window.clearInterval(timer);
      unsub?.();
    };
  }, [batch, batchRefreshAttempt, initialTask?.id, onTasksChange, loadResultForTask]);

  async function handleSave() {
    setSavedReview(null);
    if (!task || !extraction || !draftResult || task.id !== extraction.task_id || saving || !canEditExtraction) return;
    const seq = ++loadSeqRef.current;
    setSaveError(null);
    setReviewConflict(false);
    setSaving(true);
    try {
      // 用户忽略的校验问题下标随保存提交；被忽略的问题仍记录但不再阻断确认
      const ignoredIndices = extractValidationIssues
        .filter((issue) => issue.ignored)
        .map((issue) => issue.index);
      const updated = await updateReview(
        task.id,
        extraction.review_version,
        draftResult,
        ignoredIndices,
        nameChoices[task.id],
        draftDetailIds,
      );
      if (seq !== loadSeqRef.current) return;
      setExtraction(updated);
      setDraftResult(structuredClone(updated.result));
      setExtractValidationIssues(mapValidationIssues(updated));
      setSavedReview({ taskId: task.id, version: updated.review_version });
    } catch (err) {
      if (seq === loadSeqRef.current) {
        const conflict = err instanceof ApiError && err.status === 409;
        setReviewConflict(conflict);
        setSaveError(conflict ? "这份文件的数据已有更新，本次修改尚未保存。" : err instanceof Error ? err.message : "保存修改失败");
        notify(conflict ? "修改未保存，点「处理冲突」查看。" : "修改未保存，点「查看原因」查看。", "error");
      }
    } finally {
      if (seq === loadSeqRef.current) setSaving(false);
    }
  }

  function toggleIssueIgnored(index: number) {
    setSavedReview(null);
    setExtractValidationIssues((current) =>
      current.map((issue) =>
        issue.index === index ? { ...issue, ignored: !issue.ignored } : issue,
      ),
    );
  }

  function updateHeaderDraft(key: string, raw: string) {
    setSavedReview(null);
    if (!draftResult) return;
    const column = headerColumns.find((item) => item.key === key);
    let previous: TemplateValue | undefined;
    if (isTemplateResult(draftResult)) previous = draftResult.header[key];
    else {
      const target = documentHeaderKeys[key];
      if (target) previous = draftResult[target] as TemplateValue;
    }
    const value = manualCellValue(raw === "—" ? "" : raw, column?.valueType ?? typeof previous);
    setDraftResult((current) => {
      if (!current) return current;
      const next = structuredClone(current);
      if (isTemplateResult(next)) {
        next.header[key] = value;
      } else {
        const target = documentHeaderKeys[key];
        if (target) {
          next[target] = value as never;
        }
      }
      return next;
    });
    setEditingExtractCell(null);
  }

  function updateItemDraft(row: number, key: string, raw: string) {
    setSavedReview(null);
    if (!draftResult) return;
    const column = itemColumns.find((item) => item.key === key);
    let previous: TemplateValue | undefined;
    if (isTemplateResult(draftResult)) previous = draftResult.items[row]?.[key];
    else {
      const target = documentItemKeys[key];
      if (target) previous = draftResult.items[row]?.[target] as TemplateValue | undefined;
    }
    const value = manualCellValue(raw === "—" ? "" : raw, column?.valueType ?? typeof previous);
    setDraftResult((current) => {
      if (!current) return current;
      const next = structuredClone(current);
      if (isTemplateResult(next)) {
        const item = next.items[row];
        if (item) item[key] = value;
      } else {
        const item = next.items[row];
        const target = documentItemKeys[key];
        if (item && target) {
          item[target] = value as never;
        }
      }
      return next;
    });
    setEditingExtractCell(null);
  }

  const demoPreview = demo && task ? (
              <article className="document-text-preview demo-source-preview">
                <div className="small muted">内置原文件预览 · {task.filename}</div>
                <pre>{({
                  sentiment: "某市发布公共交通优化方案，将新增夜间线路并降低换乘成本。多位市民表示期待，但也担心高峰期运力是否充足。",
                  article: "# 本地大模型进入实用阶段\n作者：林知远\n\n文章讨论小型多模态模型如何在普通电脑上承担文档理解任务，并分析隐私、成本与准确率之间的取舍。",
                  grading: "数学作业（八题）\n1. 12+8=20  2. 36÷6=5  3. 7×9=63  4. 45-17=28\n5. 3/4+1/4=1  6. 2.5×4=10  7. 18÷3=6  8. 6²=36\n\n模板额外提示词中的标准答案：1.20；2.6；3.63；4.28；5.1；6.10；7.6；8.36。",
                  mistakes: "原题：一辆汽车 3 小时行驶 180 千米，平均每小时行驶多少千米？\n学生答案：180×3=540（千米）",
                  business: "送货单 R-20260815\n供货方：知意科技有限公司\n收货方：示例客户\n\n打印纸 A4  10箱  单价128元  金额1280元",
                  rule: "金额异常发票\n服务费：1000.00 元\n税额：60.00 元\n价税合计：1160.00 元",
                } as const)[demo]}</pre>
              </article>
  ) : null;

  const resultOffer = pendingAutoResult?.scope === autoPreviewScopeRef.current ? pendingAutoResult : null;
  const uploadNoticeCount = uploadErrors.length;
  const scopeTask = task?.pending_reason === "input_scope" ? task : null;
  const queueLabel = batchRestoring ? "正在读取上次队列…" : batchReadError ? "队列状态待确认"
    : pendingBatch.length ? "队列中 " + pendingBatch.length + " 个任务" : null;
  const hasNotices = Boolean(uploadNoticeCount || error || templatesError || queueLabel || resultOffer || scopeTask);
  useEffect(() => { if (!hasNotices) setNoticesOpen(false); }, [hasNotices]);
  const noticeLabel = uploadNoticeCount ? uploadNoticeCount + " 份文件待处理"
    : scopeTask ? "确认读取范围"
    : resultOffer && !error && !templatesError && !batchReadError ? resultOffer.error ? "结果读取失败" : "新结果可查看"
    : error || templatesError || batchReadError ? "查看提取事项" : queueLabel;
  const noticeButton = hasNotices && <button className="gtb-failure-link" type="button"
    aria-haspopup="dialog" onClick={() => setNoticesOpen(true)}>{noticeLabel}</button>;

  return (
    <div className="view extract-view">
      <Toast toast={toast} onDismiss={clear} />
      {noticeHost ? createPortal(noticeButton, noticeHost) : noticeButton}
      {noticesOpen && hasNotices && <NoticeDialog title="提取事项" busy={scopeNoticeBusy} onClose={() => setNoticesOpen(false)}>
        <ul className="notice-dialog-list">
          {error && <li><p>{error}</p></li>}
          {uploadErrors.map(({ file, message }, index) => <li key={index}><strong>{file}</strong><p>{message}</p></li>)}
          {templatesError && <li><p>{templatesError}</p>
            <button className="btn secondary sm" type="button" disabled={templatesLoading}
              onClick={() => setTemplatesReload(value => value + 1)}>重新读取模板</button></li>}
          {batchReadError && <li><strong>{queueLabel}</strong><p>{batchReadError}</p>
            <button className="btn secondary sm" type="button" disabled={batchRestoring} onClick={() => {
              if (batch.length) setBatchRefreshAttempt(value => value + 1);
              else setBatchRestoreAttempt(value => value + 1);
            }}>重新读取队列</button></li>}
          {queueLabel && !batchReadError && <li><strong>{queueLabel}</strong></li>}
          {pendingBatch.map(({ task: queued }) => <li key={queued.id}>
            <strong>{taskDisplayName(queued)}</strong>
            <span> · {queued.archive_pending ? "归档待处理" : BATCH_STATUS_LABEL[queued.status]}</span>
          </li>)}
          {scopeTask && <li><PartialInputAction key={scopeTask.id} task={scopeTask} onBusyChange={setScopeNoticeBusy} onUpdated={updated => {
            setTask(current => current?.id === updated.id ? updated : current);
            setBatch(current => current.map(entry => entry.task.id === updated.id ? { task: updated } : entry));
            setNoticesOpen(false); onTasksChange?.();
          }} /></li>}
          {resultOffer && <li><strong>{taskDisplayName(resultOffer.task)}</strong>
            {resultOffer.error && <p>{resultOffer.error}</p>}
            <button className="btn secondary sm" type="button" disabled={saving || loading} onClick={() => {
              setNoticesOpen(false);
              openPendingAutoResult();
            }}>{resultOffer.error ? "重新读取结果" : "查看结果"}</button></li>}
        </ul>
      </NoticeDialog>}
      {unsavedReview.dialog}
      {fileHistoryOpen && task && <FileRevisionHistory taskId={task.id} filename={taskDisplayName(task)} onClose={() => setFileHistoryOpen(false)} />}
      <ExtractConfiguration
            templates={templates}
            automatic={matchMode === "auto"}
            selectedTemplateId={selectedTemplateId}
            onSelect={value => {
              const mode = value === "smart" ? "auto" : "manual";
              setMatchMode(mode);
              if (mode === "manual") setSelectedTemplateId(value);
              writeLastUploadMode(mode, mode === "manual" ? value : selectedTemplateId);
            }}
            templateReadState={templatesLoading ? "loading" : templatesError ? "error" : "ready"}
            onUpdated={items => {
              // A modal may have read or changed the pool while the page's
              // initial read is still pending. Keep that newer result.
              templatesRequest.current += 1;
              setTemplates(items);
              setTemplatesLoading(false);
              setTemplatesError(null);
            }}
            onNavigateHistory={onNavigateHistory}
      />
      <div className="extract-layout">
        {/* 1. 拖入区 */}
        <div
          aria-label="上传文件区域，按回车选择文件"
          className={`drop-zone ${dragOver ? "drag-over" : ""}`}
          onClick={() => {
            if (!loading) void chooseFiles();
          }}
          onKeyDown={(e) => {
            if ((e.key === "Enter" || e.key === " ") && !loading) {
              e.preventDefault();
              void chooseFiles();
            }
          }}
          role="button"
          tabIndex={0}
          onDragEnter={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={(e) => {
            e.preventDefault();
            setDragOver(false);
          }}
          onDragOver={(e) => e.preventDefault()}
          onDrop={handleDrop}
        >
          <input
            type="file"
            multiple
            ref={fileInputRef}
            style={{ display: "none" }}
            onChange={handleFileInputChange}
              accept=".pdf,.docx,.xlsx,.png,.jpg,.jpeg,.webp,.bmp,.tiff,.gif,.txt,.md"
          />
          <div className="icon-wrap">
            <Icon icon={UploadCloud} size={32} />
          </div>
          <h3>点击或拖拽文件到此处</h3>
          <p>支持 PDF、Word、Excel、图片、文本，可多选批量上传。单文件不超过 50 MB。</p>
          {desktopApi()?.choose_import_files && <p className="support">需要原文件归档时，请使用“选择文件”导入。</p>}
          <button
            className="btn secondary sm"
            style={{ marginTop: "calc(var(--space-4) * 3)" }}
            onClick={(e) => {
              e.stopPropagation();
              if (!loading) void chooseFiles();
            }}
            disabled={loading}
          >
            <Icon icon={FileUp} size={15} /> 选择文件
          </button>
          {loading && (
            <div
              className="drop-progress"
              style={{
                marginTop: 12,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 8,
                color: "var(--color-text-muted)",
                fontSize: "0.875rem",
              }}
            >
              <Icon icon={Loader2} size={15} className="spin" />
              <span>正在识别…</span>
            </div>
          )}
        </div>

        {/* 2. 提取数据 + 校验 + 保存 */}
        <div className="extract-result-section" ref={resultSectionRef}>
          <div className="panel-title">
            <div>
              <h3>提取出的数据</h3>
              {extraction?.has_current_data === false && <p className="support">当前数据已清空</p>}
              {showExtractData && (
                <div className="support">
                  来源：<span>{extractedSourceName}</span> · 模板：
                  <span>{templateDisplayName}</span>
                  {task?.autonomous_fallback && <span> · 本次未匹配普通模板，使用自主提取兜底</span>}
                  {!demo && extraction && (
                    <>
                      {" · "}本次提取耗时：
                      <strong>{extraction.elapsed_seconds.toFixed(1)} 秒</strong>
                    </>
                  )}
                  {!demo && task?.status === "completed" && task.completed_at && (
                    <>
                      {" · "}完成时间：
                      <span>{formatDateTime(task.completed_at)}</span>
                    </>
                  )}
                </div>
              )}

            </div>
            {showExtractData && !demo && (
              <div className="sheet-detail-actions">
                <span className="small muted" role="status" aria-live="polite" style={{ minWidth: "3em" }}>
                  {saveError ? <button className="gtb-failure-link" type="button" aria-haspopup="dialog"
                    onClick={() => { clear(); setSaveNoticeOpen(true); }}>{reviewConflict ? "处理冲突" : "查看原因"}</button>
                    : !saving && savedReview?.taskId === task?.id && savedReview?.version === extraction?.review_version ? "已保存" : ""}
                </span>
                <button className="btn secondary sm" onClick={() => setFileHistoryOpen(true)}><Icon icon={History} size={14} /> 历史版本</button>
                <button
                  className="btn primary sm"
                  onClick={handleSave}
                  disabled={saving || !canEditExtraction}
                  title={canEditExtraction ? "提交本轮修改，形成一个文件修订版本" : "此任务当前只读，已有数据和历史仍可查看"}
                >
                  <Icon
                    icon={saving ? Loader2 : Save}
                    size={14}
                    className={saving ? "spin" : undefined}
                  />{" "}
                  修订到表
                </button>
              </div>
            )}
          </div>

          {saveNoticeOpen && saveError && <NoticeDialog title={reviewConflict ? "修订冲突" : "修改未保存"}
            onClose={() => setSaveNoticeOpen(false)}
            actions={reviewConflict && task && <button className="btn primary sm" type="button" disabled={saving || loading}
              onClick={() => {
                setSaveNoticeOpen(false);
                unsavedReview.protect(() => {
                  discardCurrentReviewDraft(); setReviewConflict(false); setSaveError(null); void loadResultForTask(task, true);
                });
              }}>重新读取</button>}>
            <p>{saveError}</p>
          </NoticeDialog>}

          {extraction?.input_scope?.coverage === "partial" && <p className="small muted">仅处理部分内容</p>}
          {task && extraction?.file_name && <FileNameConfirmation original={task.filename} state={extraction.file_name}
            value={nameChoices[task.id]} disabled={saving || !canEditExtraction} onChange={(name) => { setSavedReview(null); setNameChoices((current) => ({ ...current, [task.id]: name })); }} />}
          {!showExtractData && (
            <div className="extract-empty">
              {loading || batchActive ? (
                // 计时信息与长句提示由顶部全局任务条承担，这里只保留转圈
                <Icon icon={Loader2} size={40} className="spin" />
              ) : (
                <>
                  <Icon icon={ScanSearch} size={40} />
                  <p>等待提取</p>
                  <span>拖入文件并匹配模板后，提取结果表与校验问题将显示在此</span>
                </>
              )}
            </div>
          )}

          {showExtractData && (
            <div
              className="extract-data-block"
              onClick={(e) => {
                // 点击格子/输入框以外的空白处：取消字段选中与编辑态
                const target = e.target as HTMLElement;
                if (!target.closest(".evidence-selectable")) {
                  setSelectedEvidencePath(null);
                  setEditingExtractCell(null);
                }
              }}
            >
              {demo && <div className="callout success"><strong>内置提取示例 · 只读</strong>　对照原件查看整理结果。</div>}
              {!demo && !canEditExtraction && <p className="support">此任务当前只读。原件、已有数据和历史仍可查看。</p>}
              {/* 抬头卡片（单据头字段，一单一行） */}
              <div className="extract-header-card">
                <div className="extract-header-title">
                  <Icon icon={FileText} size={18} /> 文件级信息
                </div>
                <div className="extract-header-grid">
                  {headerColumns.map((col) => {
                    const evidencePath = headerEvidencePath(extraction, col.key);
                    return (
                    <div
                      data-review-field={`h-${col.key}`}
                      aria-label={`${canEditExtraction ? "编辑" : "查看"}字段 ${col.label}`}
                      className={`extract-header-row evidence-selectable ${
                        evidencePath === selectedEvidencePath ? "is-evidence-selected" : ""
                      }`}
                      key={col.key}
                      onClick={() => {
                        selectEvidence(evidencePath);
                        if (canEditExtraction && !saving) setEditingExtractCell(`h-${col.key}`);
                      }}
                      onKeyDown={(e) => {
                        if (e.target !== e.currentTarget) return;
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          selectEvidence(evidencePath);
                          if (canEditExtraction && !saving) setEditingExtractCell(`h-${col.key}`);
                        }
                      }}
                      role="button"
                      tabIndex={0}
                    >
                      <span className="extract-header-label">{col.label}</span>
                      <EditableText
                        className={`extract-header-value ${
                          extractValidationIssues.some(
                            (i) => i.row === 0 && i.field === col.key,
                          )
                            ? "cell-warn"
                            : ""
                        }`}
                        editing={editingExtractCell === `h-${col.key}`}
                        value={headerValues[col.key]}
                        onCancel={() => setEditingExtractCell(null)}
                        onCommit={(raw) => updateHeaderDraft(col.key, raw)}
                      />
                    </div>
                    );
                  })}
                </div>
              </div>

              {/* 明细表（多行 item） */}
              <div className="data-table-wrap extract-item-wrap">
                <div className="extract-item-title">
                  <span>
                    <Icon icon={List} size={18} /> 明细
                  </span>
                  <span className="extract-item-count">
                    {itemRows.length + " 行"}
                  </span>
                </div>
                <div className="data-table-scroll">
                  <table className="data-table" style={{ minWidth: 52 + itemColumns.reduce((sum, column) => sum + detailWidth(column.key), 0) }}>
                    <colgroup>
                      <col style={{ width: 52 }} />
                      {itemColumns.map(column => <col key={column.key} style={{ width: detailWidth(column.key) }} />)}
                    </colgroup>
                    <thead>
                      <tr>
                        <th className="detail-row-gutter" scope="col" aria-label="明细序号">#</th>
                        {itemColumns.map((col) => (
                          <th
                            key={col.key}
                            style={{
                              textAlign: col.numeric ? "right" : "left",
                            }}
                          >
                            <span>{col.label}</span>
                            <DetailColumnResizer label={col.label} width={detailWidth(col.key)}
                              onResize={width => setDetailWidths(previous => ({ ...previous, [col.key]: width }))} />
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {itemRows.slice(detailPage.start, detailPage.end).map((row, offset) => {
                        const idx = detailPage.start + offset;
                        return (
                        <tr
                          key={idx}
                          className={
                            extractValidationIssues.some(
                              (i) => i.row === idx + 1,
                            )
                              ? "has-issue"
                              : ""
                          }
                        >
                          <td className="detail-row-gutter">{!canEditExtraction ? idx + 1 : <DetailRowAction index={idx + 1} disabled={saving} onDelete={() => {
                            setSavedReview(null);
                            setDraftResult(current => current ? {...current, items: current.items.filter((_, index) => index !== idx)} as ExtractionResult : current);
                            setDraftDetailIds(current => current?.filter((_, index) => index !== idx));
                            setEditingExtractCell(null); setSelectedEvidencePath(null);
                          }} />}</td>
                          {itemColumns.map((col) => {
                            const evidencePath = itemEvidencePath(extraction, idx, col.key);
                            return (
                            <td
                              data-review-field={`${idx}-${col.key}`}
                              aria-label={`${canEditExtraction ? "编辑" : "查看"} ${col.label}，当前值 ${row[col.key] || "空"}`}
                              key={col.key}
                              className={`${col.numeric ? "numeric" : ""} ${
                                extractValidationIssues.some(
                                  (i) =>
                                    i.row === idx + 1 &&
                                    i.field === col.key,
                                )
                                  ? "cell-warn"
                                  : ""
                              } evidence-selectable ${
                                evidencePath === selectedEvidencePath
                                  ? "is-evidence-selected"
                                  : ""
                              }`}
                              onClick={() => {
                                selectEvidence(evidencePath);
                                if (canEditExtraction && !saving) setEditingExtractCell(`${idx}-${col.key}`);
                              }}
                              onKeyDown={(event) => {
                                if (event.target !== event.currentTarget) return;
                                if (event.key === "Enter" || event.key === "F2") {
                                  event.preventDefault();
                                  selectEvidence(evidencePath);
                                  if (canEditExtraction && !saving) setEditingExtractCell(`${idx}-${col.key}`);
                                }
                              }}
                              tabIndex={0}
                            >
                              <EditableText
                                editing={editingExtractCell === `${idx}-${col.key}`}
                                value={row[col.key]}
                                onCancel={() => setEditingExtractCell(null)}
                                onCommit={(raw) => updateItemDraft(idx, col.key, raw)}
                              />
                            </td>
                            );
                          })}
                        </tr>
                      ); })}
                    </tbody>
                  </table>
                </div>
                <DetailPagination paging={detailPage} count={itemRows.length} disabled={saving} />
              </div>

              <ValidationResults
                template={extraction.template}
                issues={extractValidationIssues.map(issue => ({
                  index: issue.index,
                  code: issue.id,
                  row: issue.row === 0 ? "文件级信息" : `明细第 ${issue.row} 行`,
                  field: extractedColumns.find(column => column.key === issue.field)?.label
                    || extraction.template?.fields.find(field => field.key === issue.field)?.label || issue.field,
                  message: issue.msg,
                  ignored: issue.ignored,
                  severity: extraction.validation_issues[issue.index]?.severity,
                  rawValue: extraction.validation_issues[issue.index]?.raw_value,
                  valueType: (issue.row === 0 ? headerColumns : itemColumns).find(column => column.key === issue.field)?.valueType,
                  notChecked: extraction.validation_issues[issue.index]?.not_checked, advisory: task?.status === "completed",
                  canLocate: issue.row === 0 ? headerColumns.some(column => column.key === issue.field)
                    : issue.row <= itemRows.length && itemColumns.some(column => column.key === issue.field),
                }))}
                disabled={saving}
                ignoreDisabled={!canEditExtraction}
                onToggleIgnored={demo ? undefined : toggleIssueIgnored}
                onLocate={index => {
                  const issue = extractValidationIssues.find(item => item.index === index);
                  if (!issue) return;
                  const key = issue.row === 0 ? `h-${issue.field}` : `${issue.row - 1}-${issue.field}`;
                  selectEvidence(issue.row === 0 ? headerEvidencePath(extraction, issue.field)
                    : itemEvidencePath(extraction, issue.row - 1, issue.field));
                  if (issue.row > 0) detailPage.setPage(Math.floor((issue.row - 1) / detailPage.size) + 1);
                  if (canEditExtraction) setEditingExtractCell(key);
                  locateValidationField(key);
                }}
                locateLabel={canEditExtraction ? "编辑相关字段" : "定位相关字段"}
                open={validationPanelOpen}
                onToggle={() => setValidationPanelOpen(open => !open)}
                contentId="extract-validation-content"
              />
            </div>
          )}
        </div>

        {/* 3. 原文件预览 */}
        <div className="preview-pane">
          <div className="preview-pane-header">
            <h3>
              <Icon icon={FileText} size={18} /> 原文件
            </h3>
            <div className="preview-pane-actions">
              {hasExtractData && !demo && task && (task.size_bytes ?? 0) > 0 &&
                <OriginalDownload key={task.id} url={getOriginalFileUrl(task.id)} filename={task.filename} />}
              {hasExtractData && previewPageCount > 1 && (
                <>
                  <button
                    aria-label="上一页"
                    className="btn ghost sm icon-only"
                    disabled={previewPage <= 1}
                    onClick={() => setPreviewPage((page) => Math.max(1, page - 1))}
                  >
                    <Icon icon={ChevronLeft} size={14} />
                  </button>
                  <span className="preview-page-info">
                    {previewPage} / {previewPageCount} 页
                  </span>
                  <button
                    aria-label="下一页"
                    className="btn ghost sm icon-only"
                    disabled={previewPage >= previewPageCount}
                    onClick={() =>
                      setPreviewPage((page) => Math.min(previewPageCount, page + 1))
                    }
                  >
                    <Icon icon={ChevronRight} size={14} />
                  </button>
                </>
              )}
              <button
                aria-label="缩小原文件"
                className="btn ghost sm"
                onClick={() => zoomPreview(-0.1)}
                disabled={!hasExtractData || previewScale <= 0.5}
              >
                <Icon icon={Minus} size={14} />
              </button>
              <span className="preview-page-info">
                {previewScale.toFixed(1) + "×"}
              </span>
              <button
                aria-label="放大原文件"
                className="btn ghost sm"
                onClick={() => zoomPreview(0.1)}
                disabled={!hasExtractData || previewScale >= 3}
              >
                <Icon icon={Plus} size={14} />
              </button>
              <button
                aria-label="打开原文件全屏预览"
                title="全屏预览"
                className="btn ghost sm"
                onClick={() => setPreviewFullscreen(true)}
                disabled={!hasExtractData}
              >
                <Icon icon={Maximize2} size={14} />
              </button>
            </div>
          </div>
          <div className="preview-pane-body">
            {!hasExtractData && (
              <div className="preview-empty">
                <Icon icon={FileSearch} size={40} />
                <p>上传文件后，此处显示原文件预览</p>
              </div>
            )}
            {hasExtractData && demoPreview}
            {hasExtractData && !demo && (task.size_bytes ?? 0) > 0 && (
              <div className="document-preview-shell">
                {evidenceLabel && <div className={`evidence-status ${selectedEvidence?.status}`} aria-live="polite">{evidenceLabel}</div>}
                <div
                  className="document-preview-viewport"
                  onDoubleClick={() => setPreviewFullscreen(true)}
                >
                  <DocumentPreview
                    contentType={task.content_type}
                    filename={task.filename}
                    onPageCount={setPreviewPageCount}
                    pageNumber={previewPage}
                    region={
                      selectedEvidence?.status === "located" &&
                      selectedEvidence.location_verified
                        ? selectedEvidence.region
                        : null
                    }
                    scale={previewScale}
                    url={getOriginalFileUrl(task.id)}
                  />
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {extraction?.input_scope && <InputScopeDetails scope={extraction.input_scope} label="来源与处理详情" />}

      {/* 原文件放大模态 */}
      {previewFullscreen && (
        <div
          className="modal-overlay"
          onClick={(e) => {
            if (e.target === e.currentTarget) setPreviewFullscreen(false);
          }}
        >
          <div
            className="preview-fullscreen"
            role="dialog"
            aria-modal="true"
            aria-label="原文件全屏预览"
            {...previewDialog}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="preview-fullscreen-head">
              <span className="preview-fullscreen-title">
                {extractedSourceName}
              </span>
              <div className="preview-pane-actions">
                {!demo && task && (task.size_bytes ?? 0) > 0 &&
                  <OriginalDownload key={task.id} url={getOriginalFileUrl(task.id)} filename={task.filename} />}
                {previewPageCount > 1 && (
                  <>
                    <button
                      aria-label="上一页"
                      className="btn ghost sm icon-only"
                      disabled={previewPage <= 1}
                      onClick={() => setPreviewPage((page) => Math.max(1, page - 1))}
                    >
                      <Icon icon={ChevronLeft} size={14} />
                    </button>
                    <span className="preview-page-info">
                      {previewPage} / {previewPageCount} 页
                    </span>
                    <button
                      aria-label="下一页"
                      className="btn ghost sm icon-only"
                      disabled={previewPage >= previewPageCount}
                      onClick={() =>
                        setPreviewPage((page) => Math.min(previewPageCount, page + 1))
                      }
                    >
                      <Icon icon={ChevronRight} size={14} />
                    </button>
                  </>
                )}
                <button
                  aria-label="缩小原文件"
                  className="btn ghost sm"
                  disabled={previewScale <= 0.5}
                  onClick={() => zoomPreview(-0.1)}
                >
                  <Icon icon={Minus} size={14} />
                </button>
                <span className="preview-page-info">
                  {previewScale.toFixed(1) + "×"}
                </span>
                <button
                  aria-label="放大原文件"
                  className="btn ghost sm"
                  disabled={previewScale >= 3}
                  onClick={() => zoomPreview(0.1)}
                >
                  <Icon icon={Plus} size={14} />
                </button>
                <button
                  aria-label="关闭全屏预览"
                  className="btn ghost sm icon-only"
                  onClick={() => setPreviewFullscreen(false)}
                >
                  <Icon icon={X} size={16} />
                </button>
              </div>
            </div>
            <div className="preview-fullscreen-body">
              <div className="document-preview-shell fullscreen">
                {evidenceLabel && <div className={`evidence-status ${selectedEvidence?.status}`} aria-live="polite">{evidenceLabel}</div>}
                {demo ? demoPreview : <DocumentPreview
                  contentType={task?.content_type ?? ""}
                  filename={task?.filename ?? extractedSourceName}
                  onPageCount={setPreviewPageCount}
                  pageNumber={previewPage}
                  region={
                    selectedEvidence?.status === "located" &&
                    selectedEvidence.location_verified
                      ? selectedEvidence.region
                      : null
                  }
                  scale={previewScale}
                  url={task ? getOriginalFileUrl(task.id) : ""}
                />}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
