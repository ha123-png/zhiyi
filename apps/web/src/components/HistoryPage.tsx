import { deletionNotice } from "../deletionNotice";
import { FileRevisionHistory } from "./FileRevisionHistory";
import { DetailPagination, useDetailPage } from "./DetailPagination";
import { DetailRowAction } from "./DetailRowAction";
import { DetailColumnResizer } from "./DetailColumnResizer";
import { FileHistoryActions } from "./FileHistoryActions";
import { History } from "lucide-react";
import { taskDisplayName, taskTemplateName } from "../taskNames";
import { FileLocation } from "./FileLocation";
import { Disclosure } from "./Disclosure";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { usePointerDrag } from "../usePointerDrag";
import type { ListScrollMemory } from "../useListScroll";
import {
  ArrowRight,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  FileText,
  List,
  Loader2,
  Save,
  Search,
  Table2,
} from "lucide-react";
import {
  ApiError,
  deleteTask,
  getExtraction,
  getOriginalFileUrl,
  getTemplates,
  getTasks,
  getTaskSummary,
  updateReview,
} from "../api";
import type {
  Extraction,
  ExtractionResult,
  Task,
  TaskSummary,
  TemplateValue,
} from "../types";
import { serverDate } from "../time";
import { OriginalDownload } from "./OriginalDownload";
import { DocumentPreview } from "./DocumentPreview";
import { InputScopeDetails } from "./InputScopeDetails";
import { TaskExportDetails } from "./TaskExportDetails";
import { EditableText } from "./EditableText";
import { DeleteTaskDialog } from "./DeleteTaskDialog";
import { Icon } from "./Icon";
import { NoticeDialog } from "./NoticeDialog";
import { Toast, useToast } from "./Toast";
import { manualCellValue } from "../numericInput";
import { useUnsavedChanges } from "../useUnsavedChanges";
import { ValidationResults } from "./ValidationResults";
import { useValidationFieldLocator } from "./useValidationFieldLocator";
import { HISTORY_PAGE_SIZE, HistoryTableGrid } from "./HistoryTableGrid";
import "./history-page.css";

function formatDateTime(iso: string): string {
  const d = serverDate(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function formatElapsed(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${seconds.toFixed(1)} 秒`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes} 分 ${Math.round(seconds % 60)} 秒`;
}

const BUILTIN_LABELS: Record<string, string> = {
  document_type: "单据类型",
  seller_name: "销售方",
  buyer_name: "购买方",
  seller_tax_id: "销售方统一社会信用代码/纳税人识别号",
  buyer_tax_id: "购买方统一社会信用代码/纳税人识别号",
  seller_contact: "供方联系电话",
  buyer_contact: "需方联系电话",
  buyer_address: "收货地址",
  document_number: "发票号码",
  document_date: "开票日期",
  amount_before_tax: "不含税金额",
  tax_amount: "整单税额",
  total_amount: "价税合计",
  total_quantity: "总数量",
  remarks: "备注",
  name: "商品名称",
  specification: "规格型号",
  unit: "单位",
  quantity: "数量",
  unit_price: "单价",
  item_amount: "明细金额",
  tax_rate: "税率",
  item_tax_amount: "明细税额",
  item_remarks: "明细备注",
};

type HistoryTab = "completed" | "problems" | "original";
interface HistoryFilters {
  fileSearch: string;
  templateFilter: string;
  timeFilter: string;
}
const DEFAULT_FILTERS: HistoryFilters = { fileSearch: "", templateFilter: "", timeFilter: "all" };
export interface HistoryListViewState {
  tab: Exclude<HistoryTab, "original">;
  filters: HistoryFilters;
  completedPage: number;
  problemsPage: number;
}

interface HistoryPageProps {
  initialTab?: HistoryTab;
  viewState?: HistoryListViewState;
  onViewStateChange?: (state: HistoryListViewState) => void;
  scrollMemory?: ListScrollMemory;
  onOpenData?: (task: Task) => void;
  onNavigateExtract?: () => void;
}

export function HistoryPage({ onOpenData, onNavigateExtract, initialTab, viewState, onViewStateChange, scrollMemory }: HistoryPageProps = {}) {
  const startPointerDrag = usePointerDrag();
  const savedView = initialTab === undefined ? viewState : undefined;
  const [historyTab, setHistoryTab] = useState<HistoryTab>(initialTab ?? savedView?.tab ?? "completed");
  // 当前页任务：服务端分页，万级历史不一次载入前端内存（ISSUE-067）
  const [loadedOffset, setLoadedOffset] = useState(0);
  const [pageTasks, setPageTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [listReadError, setListReadError] = useState<string | null>(null);
  const [originalReadError, setOriginalReadError] = useState<string | null>(null);
  const [loadedListQuery, setLoadedListQuery] = useState<string | null>(null);

  // 当前筛选下的角标计数和页数。
  const [filteredSummary, setFilteredSummary] = useState<{ scope: string; counts: TaskSummary } | null>(null);

  const [selectedTask, setSelectedTask] = useState<Task | null>(null);
  const [extraction, setExtraction] = useState<Extraction | null>(null);
  const [extractionLoading, setExtractionLoading] = useState(false);
  const [templateNameMap, setTemplateNameMap] = useState<Record<string, string>>({});
  const [columnWidths, setColumnWidths] = useState<Record<number, number>>({});
  const [detailWidths, setDetailWidths] = useState<Record<string, number>>({});

  // 列表筛选与分页
  const [filters, setFilters] = useState(savedView?.filters ?? DEFAULT_FILTERS);
  const { fileSearch, templateFilter, timeFilter } = filters;
  const [completedPage, setCompletedPage] = useState(savedView?.completedPage ?? 1);
  const [problemsPage, setProblemsPage] = useState(savedView?.problemsPage ?? 1);
  const [jumpPageInput, setJumpPageInput] = useState("");
  function updateListFilters(changes: Partial<HistoryFilters>) {
    setFilters(previous => ({ ...previous, ...changes }));
    setCompletedPage(1);
    setProblemsPage(1);
  }
  const listRequest = useRef(0);
  const listPage = historyTab === "problems" ? problemsPage : completedPage;
  const summaryScope = JSON.stringify([fileSearch.trim(), templateFilter, timeFilter]);
  const currentFilteredSummary = filteredSummary?.scope === summaryScope ? filteredSummary.counts : null;
  const listQuery = JSON.stringify([historyTab, listPage, fileSearch, templateFilter, timeFilter]);
  const latestListQuery = useRef(listQuery);
  latestListQuery.current = listQuery;
  const localScrollMemory = useRef<ListScrollMemory>({});
  const returnToFile = useRef<{ query: string; id: string; returnTab: HTMLButtonElement | null } | null>(null);
  const returnedFileButton = useRef<HTMLButtonElement>(null);

  // 原文件 tab 内编辑：草稿模式，点“修订到表”才触发校验并保存
  const [editingCell, setEditingCell] = useState<string | null>(null);
  const [fileHistoryOpen, setFileHistoryOpen] = useState(false);
  const [draftDetailIds, setDraftDetailIds] = useState<number[] | undefined>();
  useEffect(() => { setDraftDetailIds(extraction?.detail_ids); }, [extraction]);
  const [draft, setDraft] = useState<ExtractionResult | null>(null);
  const originalContentRef = useRef<HTMLDivElement>(null);
  const [saving, setSaving] = useState(false);
  const [reviewConflict, setReviewConflict] = useState(false);
  // 原文件加载竞态守卫：快速切换文件时只让最新一次请求写入结果
  const originalSeqRef = useRef(0);
  // 用户忽略的校验问题下标：被忽略的问题仍记录，但不再阻断修订到表
  const [ignoredIssueIndices, setIgnoredIssueIndices] = useState<number[]>([]);
  const { toast, notify, clear } = useToast();
  const [historyNoticeOpen, setHistoryNoticeOpen] = useState(false);
  const [originalNoticeOpen, setOriginalNoticeOpen] = useState(false);
  const [busyTaskId, setBusyTaskId] = useState<string | null>(null);

  // 删除整个文件前确认：原件、当前数据及修订历史一起删除
  const [deleteTarget, setDeleteTarget] = useState<Task | null>(null);
  const unsavedReview = useUnsavedChanges(() => !!extraction && !!draft && (
    editingCell !== null
    || JSON.stringify(draft) !== JSON.stringify(extraction.result)
    || JSON.stringify(draftDetailIds) !== JSON.stringify(extraction.detail_ids)
    || JSON.stringify([...ignoredIssueIndices].sort()) !== JSON.stringify(ignoredIndices(extraction))
  ));

  function ignoredIndices(result: Extraction): number[] {
    return result.validation_issues.flatMap((issue, index) => issue.ignored ? [index] : []).sort();
  }

  function changeTab(tab: HistoryTab, button: HTMLButtonElement) {
    if (tab === historyTab) return;
    unsavedReview.protect(() => {
      if (historyTab === "original" && returnToFile.current) returnToFile.current.returnTab = button;
      setDraft(extraction ? structuredClone(extraction.result) : null);
      setDraftDetailIds(extraction?.detail_ids);
      setIgnoredIssueIndices(extraction ? ignoredIndices(extraction) : []);
      setEditingCell(null);
      setError(null);
      setReviewConflict(false);
      setHistoryNoticeOpen(false);
      setOriginalNoticeOpen(false);
      clear();
      setListReadError(null);
      setOriginalReadError(null);
      setHistoryTab(tab);
    });
  }

  useEffect(() => {
    getTemplates(true)
      .then((templates) =>
        setTemplateNameMap(
          Object.fromEntries(templates.map((t) => [t.id, t.name])),
        ),
      )
      .catch(() => setTemplateNameMap({}));
  }, []);

  // 时间筛选 → since（按 updated_at 下限服务端过滤）
  const timeFilterSince = (): string | undefined => {
    if (timeFilter === "all") return undefined;
    const hours = timeFilter === "today" ? 24 : timeFilter === "7d" ? 168 : 720;
    return new Date(Date.now() - hours * 3600_000).toISOString();
  };

  // 服务端分页 + 筛选（ISSUE-067）：只拉当前页，不把万级历史载入前端内存。
  // 校验问题 = 提取结果已出来但规则不通过（needs_review）；处理失败（failed）不属于校验问题，去状态监控重试。
  const load = async () => {
    if (historyTab === "original" || listQuery !== latestListQuery.current) return;
    const request = ++listRequest.current;
    const current = () => request === listRequest.current && listQuery === latestListQuery.current;
    const isProblems = historyTab === "problems";
    const status = isProblems ? "needs_review" : "completed";
    const rawPage = listPage;
    const count = isProblems
      ? (currentFilteredSummary?.needs_review ?? 0)
      : (currentFilteredSummary?.completed ?? 0);
    const page = Math.min(rawPage, Math.max(1, Math.ceil(count / HISTORY_PAGE_SIZE)));
    const searchText = fileSearch.trim();
    const common = {
      search: searchText || undefined,
      templateId: templateFilter || undefined,
      since: timeFilterSince(),
    };
    try {
      setListReadError(null);
      const [initialList, filtered] = await Promise.all([
        getTasks({
          ...common,
          status,
          limit: HISTORY_PAGE_SIZE,
          offset: (page - 1) * HISTORY_PAGE_SIZE,
        }),
        getTaskSummary(common),
      ]);
      if (!current()) return;
      const settledCount = isProblems ? filtered.needs_review : filtered.completed;
      const settledPage = Math.min(rawPage, Math.max(1, Math.ceil(settledCount / HISTORY_PAGE_SIZE)));
      const list = settledPage === page ? initialList : await getTasks({
        ...common, status, limit: HISTORY_PAGE_SIZE, offset: (settledPage - 1) * HISTORY_PAGE_SIZE,
      });
      if (!current()) return;
      setPageTasks(list);
      setLoadedOffset((settledPage - 1) * HISTORY_PAGE_SIZE);
      setFilteredSummary({ scope: summaryScope, counts: filtered });
      setLoadedListQuery(listQuery);
    } catch (err) {
      if (current()) setListReadError(err instanceof Error ? err.message : "加载失败");
    } finally {
      if (current()) setLoading(false);
    }
  };

  // 切 tab 时先清空列表：避免上一 tab 的记录（如刚忽略过、已移到已完成的记录）在加载间隙闪过
  const prevTabRef = useRef(historyTab);
  useEffect(() => {
    // 250ms 防抖：文件名搜索输入不逐键发请求
    if (prevTabRef.current !== historyTab) {
      prevTabRef.current = historyTab;
      setPageTasks([]);
    }
    setLoading(true);
    const timer = setTimeout(() => void load(), 250);
    return () => { clearTimeout(timer); listRequest.current += 1; };
    // 显式列出触发条件：load 使用最新渲染闭包，避免 filteredSummary 变化引发重复加载
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listQuery]);

  const currentCount = (historyTab === "problems" ? currentFilteredSummary?.needs_review : currentFilteredSummary?.completed) ?? 0;
  const currentPageCount = Math.max(1, Math.ceil(currentCount / HISTORY_PAGE_SIZE));
  const currentPage =
    historyTab === "problems"
      ? Math.min(problemsPage, currentPageCount)
      : Math.min(completedPage, currentPageCount);
  const renderedListQuery = loadedListQuery === listQuery
    ? JSON.stringify([historyTab, currentPage, fileSearch, templateFilter, timeFilter]) : null;
  const pageToRemember = renderedListQuery === null ? listPage : currentPage;
  useEffect(() => {
    if (historyTab === "original") return;
    onViewStateChange?.({
      tab: historyTab, filters,
      completedPage: historyTab === "completed" ? pageToRemember : completedPage,
      problemsPage: historyTab === "problems" ? pageToRemember : problemsPage,
    });
  }, [historyTab, filters, completedPage, problemsPage, pageToRemember, onViewStateChange]);
  useLayoutEffect(() => {
    const target = returnToFile.current;
    if (!target || historyTab === "original" || renderedListQuery === null || loading) return;
    returnToFile.current = null;
    if (target.query === renderedListQuery &&
        (document.activeElement === target.returnTab || document.activeElement === document.body)) {
      returnedFileButton.current?.focus({ preventScroll: true });
    }
  }, [historyTab, renderedListQuery, loading]);
  const hasActiveHistoryFilters = Boolean(fileSearch.trim() || templateFilter || timeFilter !== "all");


  function retryHistoryRead() {
    if (loading) return;
    setLoading(true);
    setHistoryNoticeOpen(false);
    setListReadError(null);
    void load();
  }

  function retryOriginalRead() {
    if (extractionLoading || !selectedTask) return;
    setOriginalReadError(null);
    unsavedReview.protect(() => loadOriginal(selectedTask));
  }

  function templateLabel(t: Task): string {
    return taskTemplateName(t, t.template_id ? templateNameMap[t.template_id] : undefined);
  }

  function openOriginal(task: Task) {
    unsavedReview.protect(() => {
      returnToFile.current = renderedListQuery === null ? null : { query: renderedListQuery, id: task.id, returnTab: null };
      loadOriginal(task);
    });
  }

  function loadOriginal(task: Task) {
    setReviewConflict(false);
    setOriginalNoticeOpen(false);
    clear();
    setSelectedTask(task);
    setHistoryTab("original");
    setExtraction(null);
    setDraft(null);
    setSaving(false);
    setEditingCell(null);
    setExtractionLoading(true);
    setError(null);
    setOriginalReadError(null);
    const seq = ++originalSeqRef.current;
    getExtraction(task.id)
      .then((result) => {
        if (seq !== originalSeqRef.current) return;
        setExtraction(result);
        setDraft(structuredClone(result.result));
        setIgnoredIssueIndices(
          (result.validation_issues ?? [])
            .map((issue, index) => (issue.ignored ? index : -1))
            .filter((index) => index >= 0),
        );
      })
      .catch((err) => {
        if (seq !== originalSeqRef.current) return;
        setOriginalReadError(err instanceof Error ? err.message : "加载提取结果失败");
      })
      .finally(() => {
        if (seq === originalSeqRef.current) setExtractionLoading(false);
      });
  }

  function fieldType(section: "header" | "item", key: string): string | undefined {
    return (extraction?.current_schema ?? extraction?.template?.fields)
      ?.find(entry => entry.section === section && entry.key === key)?.value_type;
  }

  function updateHeaderDraft(key: string, raw: string) {
    if (!draft || saving) return;
    const next = structuredClone(draft);
    const header = ("header" in next ? next.header : next) as Record<string, TemplateValue>;
    header[key] = manualCellValue(raw === "—" ? "" : raw, fieldType("header", key) ?? typeof header[key]);
    setDraft(next);
    setEditingCell(null);
  }

  function updateItemDraft(idx: number, key: string, raw: string) {
    if (!draft || saving) return;
    const next = structuredClone(draft);
    const item = (next as { items: Array<Record<string, TemplateValue>> }).items[idx];
    if (!item) return;
    item[key] = manualCellValue(raw === "—" ? "" : raw, fieldType("item", key) ?? typeof item[key]);
    setDraft(next);
    setEditingCell(null);
  }

  async function handleSaveToTable() {
    if (!selectedTask || !extraction || !draft || saving || selectedTask.id !== extraction.task_id) return;
    const seq = originalSeqRef.current;
    setSaving(true);
    setError(null);
    setReviewConflict(false);
    try {
      const updated = await updateReview(
        selectedTask.id,
        extraction.review_version,
        draft,
        ignoredIssueIndices,
        undefined,
        draftDetailIds,
      );
      if (seq !== originalSeqRef.current) return;
      setExtraction(updated);
      setDraft(structuredClone(updated.result));
      setEditingCell(null);
      setIgnoredIssueIndices(
        (updated.validation_issues ?? [])
          .map((issue, index) => (issue.ignored ? index : -1))
          .filter((index) => index >= 0),
      );
      // 保存后刷新列表：任务可能从"校验问题"变"已完成"，列表状态不滞后
      await load();
    } catch (err) {
      if (seq === originalSeqRef.current) {
        const conflict = err instanceof ApiError && err.status === 409;
        setReviewConflict(conflict);
        setError(conflict ? "这份文件的数据已有更新，本次修改尚未保存。" : err instanceof Error ? err.message : "保存失败");
        notify(conflict ? "修改未保存，点「处理冲突」查看。" : "修改未保存，点「查看原因」查看。", "error");
      }
    } finally {
      if (seq === originalSeqRef.current) setSaving(false);
    }
  }

  function toggleIssueIgnored(index: number) {
    if (saving) return;
    setIgnoredIssueIndices((current) =>
      current.includes(index)
        ? current.filter((i) => i !== index)
        : [...current, index],
    );
  }

  // 问题列表"忽略"：直接忽略该任务全部校验问题并完成确认（不跳转原文件）
  async function handleIgnoreAll(task: Task) {
    setBusyTaskId(task.id);
    setError(null);
    try {
      const extractionData = await getExtraction(task.id);
      const issues = extractionData.validation_issues ?? [];
      const allIndices = issues.flatMap((issue, index) => issue.raw_value == null && !issue.not_checked ? [index] : []);
      if (allIndices.length === 0 && issues.some(issue => issue.raw_value != null)) {
        notify("有字段需要对照原件核对。", "info");
        return;
      }
      await updateReview(
        task.id,
        extractionData.review_version,
        extractionData.result,
        allIndices,
      );
      notify(issues.some(issue => issue.raw_value != null)
        ? "已忽略可跳过的校验，仍有字段需要核对。"
        : `已忽略校验问题并完成：${task.filename}`, "success");
      await load();
    } catch (err) {
      notify(err instanceof Error ? err.message : "忽略并完成失败", "error");
    } finally {
      setBusyTaskId(null);
    }
  }

  // 删除整个文件及其唯一当前数据与历史
  async function handleDeleteTask() {
    if (!deleteTarget) return;
    setBusyTaskId(deleteTarget.id);
    setError(null);
    try {
      const result = await deleteTask(deleteTarget.id);
      setDeleteTarget(null);
      notify(
        deletionNotice(result, `已删除文件及关联数据：${deleteTarget.filename}。`),
        "success",
      );
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除记录失败");
    } finally {
      setBusyTaskId(null);
    }
  }

  function startResize(
    event: React.PointerEvent<HTMLSpanElement>,
    index: number,
    initialWidth: number,
  ) {
    if (event.button !== 0) return;
    event.preventDefault();
    const startX = event.clientX;
    const move = (moveEvent: PointerEvent) => {
      setColumnWidths((current) => ({
        ...current,
        [index]: Math.min(360, Math.max(48, initialWidth + moveEvent.clientX - startX)),
      }));
    };
    startPointerDrag(move, undefined, event.pointerId, event.currentTarget);
  }

  const colWidths = useMemo<Record<number, number>>(
    () => ({
      0: columnWidths[0] ?? 48,
      1: columnWidths[1] ?? 240,
      2: columnWidths[2] ?? 100,
      3: columnWidths[3] ?? 140,
      4: columnWidths[4] ?? (historyTab === "problems" ? 180 : 70),
      5: columnWidths[5] ?? (historyTab === "problems" ? 165 : 85),
      6: columnWidths[6] ?? 165,
    }),
    [columnWidths, historyTab],
  );

  function resizeWithKeyboard(event: React.KeyboardEvent<HTMLSpanElement>, width: number, apply: (value: number) => void) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" ? 48 : event.key === "End" ? 360 : width + (event.key === "ArrowLeft" ? -12 : 12);
    apply(Math.min(360, Math.max(48, next)));
  }

  const renderColumnResizer = (index: number) => (
    <span
      aria-label={`调整第 ${index + 1} 列宽`}
      aria-orientation="vertical"
      className="column-resizer"
      onPointerDown={(event) => startResize(event, index, colWidths[index])}
      onKeyDown={(event) => resizeWithKeyboard(event, colWidths[index], value => setColumnWidths(current => ({ ...current, [index]: value })))}
      aria-valuemin={48} aria-valuemax={360} aria-valuenow={colWidths[index]}
      role="separator"
      tabIndex={0}
    />
  );

  // 抬头/明细字段一律以提取所用模板字段为准（与数据表、提取页同源）：
  // 模板缺失（旧记录/模板已删）时不显示字段，也不回退发票全集，避免送货单冒出税字段。
  const extractionHeaderKeys = useMemo(() => {
    if (!extraction) return [];
    return (extraction.current_schema ?? extraction.template?.fields ?? [])
      .filter((field) => field.section === "header" && field.key)
      .map((field) => field.key as string);
  }, [extraction]);

  const extractionItemKeys = useMemo(() => {
    if (!extraction) return [];
    return (extraction.current_schema ?? extraction.template?.fields ?? [])
      .filter((field) => field.section === "item" && field.key)
      .map((field) => field.key as string);
  }, [extraction]);
  const detailColumnWidth = extractionItemKeys.reduce((width, key) => width + (detailWidths[key] ?? 132), 0);

  function labelFor(key: string): string {
    key = key.replace(/^(?:header\.|items\[\d+\]\.)/, "");
    if (extraction?.current_schema?.length) return extraction.current_schema.find(field => field.key === key)?.label ?? key;
    if (extraction?.template) {
      const field = extraction.template.fields.find((f) => f.key === key);
      if (field) {
        const mapped = field.key && extraction.template.output_mapping?.[field.key];
        return mapped || field.label;
      }
    }
    return BUILTIN_LABELS[key] ?? key;
  }

  function headerValueOf(key: string): string {
    const source = draft ?? extraction?.result;
    if (!source) return "";
    if ("header" in source) return String(source.header[key] ?? "");
    return String((source as unknown as Record<string, unknown>)[key] ?? "");
  }

  const displayItems = draft
    ? (draft as { items?: Array<Record<string, unknown>> }).items ?? []
    : extraction
      ? (extraction.result as { items?: Array<Record<string, unknown>> }).items ?? []
      : [];
  const detailPage = useDetailPage(displayItems.length, selectedTask?.id);
  const locateValidationField = useValidationFieldLocator(originalContentRef, selectedTask?.id);

  const renderTable = (
    headers: string[],
    colgroup: React.ReactNode,
    rows: React.ReactNode[],
    variant?: "problems",
  ) => (
    <HistoryTableGrid
      scrollMemory={scrollMemory ?? localScrollMemory.current}
      scrollSlot={variant === "problems" ? "history-problems" : "history-completed"}
      query={renderedListQuery}>
      <table className={`data-table history-table${variant ? ` ${variant}` : ""}`} style={{ tableLayout: "fixed" }}>
        {colgroup}
        <thead>
          <tr>
            {headers.map((header, index) => (
              <th key={index} aria-label={header || "文件操作"} className={header ? undefined : "history-actions-gutter"} style={header ? { width: `${colWidths[index]}px` } : {width: "48px"}}>
                <span>{header}</span>
                {header && renderColumnResizer(index)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows}
          {!loading && !listReadError && loadedListQuery === listQuery && rows.length > 0 &&
            Array.from({ length: HISTORY_PAGE_SIZE - rows.length }, (_, index) =>
              <tr key={`blank-${index}`} aria-hidden="true" className="history-blank-row">
                {headers.map((_, column) => <td key={column} />)}
              </tr>)}
        </tbody>
      </table>
    </HistoryTableGrid>
  );

  const renderEmptyHistoryState = (tab: "completed" | "problems") => {
    if (loadedListQuery !== listQuery) {
      return (
        <div className="history-empty-state" role="status">
          <strong>暂时无法确认这里是否为空</strong>
          <span>历史读取失败不代表没有记录。请重新读取后再判断。</span>
        </div>
      );
    }
    if (currentCount > 0) {
      return (
        <div className="history-empty-state" role="status">
          <strong>当前页没有记录</strong>
          <span>可以调整页码，或重新读取文件历史。</span>
        </div>
      );
    }
    if (hasActiveHistoryFilters) {
      return (
        <div className="history-empty-state" role="status">
          <strong>没有符合条件的记录</strong>
          <span>请调整文件名、模板或时间条件，或使用上方的“清除筛选”。</span>
        </div>
      );
    }
    if (tab === "problems") {
      return (
        <div className="history-empty-state" role="status">
          <strong>目前没有待确认记录</strong>
          <span>需要核对的提取结果会显示在这里。</span>
        </div>
      );
    }
    return (
      <div className="history-empty-state history-empty-state-first-run" role="status">
        <Icon icon={FileText} size={24} />
        <strong>还没有已完成的文件记录</strong>
        <span>文件处理完成后，记录会显示在这里。</span>
        <div className="history-empty-actions">
          {onNavigateExtract && <button className="btn primary sm" type="button" onClick={onNavigateExtract}>去文件提取</button>}
        </div>
      </div>
    );
  };

  const renderHistoryPager = (
    page: number,
    pageCount: number,
    total: number,
    onPage: (p: number) => void,
  ) => {
    const goToPage = (target: number) => {
      onPage(Math.max(1, Math.min(pageCount, target)));
      setJumpPageInput("");
    };
    const jumpToPage = () => {
      const parsed = Number.parseInt(jumpPageInput, 10);
      if (Number.isFinite(parsed)) goToPage(parsed);
    };
    const pageNumbers = Array.from({ length: pageCount }, (_, i) => i + 1)
      .slice(
        Math.max(0, Math.min(page - 2, pageCount - 5)),
        Math.max(5, Math.min(page + 3, pageCount)),
      );
    return (
    <div className="history-pagination">
      <span className="footer-info">
        {loadedListQuery !== listQuery && !loading
          ? "当前筛选结果数尚未确认；下方仍显示上次成功读取的内容。"
          : loading
            ? <><Icon icon={Loader2} size={13} className="spin" /> 正在加载下一页…</>
            : <>显示 {total === 0 ? 0 : (page - 1) * HISTORY_PAGE_SIZE + 1}-{Math.min(page * HISTORY_PAGE_SIZE, total)} 条，共 {total} 条</>}
      </span>
      <div className="pagination">
        <button className="btn icon-only sm" disabled={page <= 1} onClick={() => goToPage(1)} title="第一页">
          <Icon icon={ChevronsLeft} size={14} />
        </button>
        <button className="btn icon-only sm" disabled={page <= 1} onClick={() => goToPage(page - 1)} title="上一页">
          <Icon icon={ChevronLeft} size={14} />
        </button>
        {pageNumbers.map((p) => (
          <button key={p} className={`btn sm${p === page ? " active" : ""}`} onClick={() => goToPage(p)}>{p}</button>
        ))}
        <button className="btn icon-only sm" disabled={page >= pageCount} onClick={() => goToPage(page + 1)} title="下一页">
          <Icon icon={ChevronRight} size={14} />
        </button>
        <button className="btn icon-only sm" disabled={page >= pageCount} onClick={() => goToPage(pageCount)} title="最后一页">
          <Icon icon={ChevronsRight} size={14} />
        </button>
        <span className="pagination-jump">
          第
          <input
            aria-label="跳转页码"
            min={1}
            max={pageCount}
            type="number"
            value={jumpPageInput}
            placeholder={String(page)}
            onChange={(event) => setJumpPageInput(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Enter") jumpToPage(); }}
          />
          页 / 共 {pageCount} 页
          <button className="btn sm secondary" disabled={!jumpPageInput.trim()} onClick={jumpToPage}>跳转</button>
        </span>
      </div>
    </div>
    );
  };

  return (
    <div className={`view${historyTab === "original" ? "" : " history-list-view"}`}>
      {fileHistoryOpen && selectedTask && <FileRevisionHistory taskId={selectedTask.id} filename={taskDisplayName(selectedTask)} onClose={() => setFileHistoryOpen(false)} />}
      <Toast toast={toast} onDismiss={clear} />


      {historyNoticeOpen && listReadError && historyTab !== "original" && (
        <NoticeDialog title="文件历史事项" onClose={() => setHistoryNoticeOpen(false)}
          actions={<button className="btn primary sm" type="button" onClick={retryHistoryRead} disabled={loading}>重新读取历史</button>}>
            <strong>{loadedListQuery === null
              ? "尚未读取到历史数据，暂时无法判断这里是否为空。"
              : loadedListQuery === listQuery
                ? "重新读取失败，仍显示上次成功读取的结果，可能不是最新状态。"
                : "当前筛选读取失败，仍显示上次成功读取的内容，可能不符合当前条件。"}</strong>
            <p>{listReadError}</p>
        </NoticeDialog>
      )}

      {/* Tab */}
      <div className="history-tabs">
        <button
          className={`hist-tab ${historyTab === "completed" ? "active" : ""}`}
          onClick={event => changeTab("completed", event.currentTarget)}
        >
          已完成 <span className="hist-tab-count">{currentFilteredSummary?.completed ?? "—"}</span>
        </button>
        <button
          className={`hist-tab ${historyTab === "problems" ? "active" : ""}`}
          onClick={event => changeTab("problems", event.currentTarget)}
        >
          校验问题 <span className="hist-tab-count">{currentFilteredSummary?.needs_review ?? "—"}</span>
        </button>
        <button
          className={`hist-tab ${historyTab === "original" ? "active" : ""}`}
          onClick={event => changeTab("original", event.currentTarget)}
        >
          原文件
          {selectedTask ? <span className="hist-tab-count">1</span> : null}
        </button>
        {historyTab !== "original" && listReadError && <button className="btn ghost sm" type="button"
          aria-haspopup="dialog" onClick={() => setHistoryNoticeOpen(true)}>文件历史事项</button>}
      </div>

      {/* 筛选栏：文件名搜索 / 模板 / 时间；原文件 tab 是单文件详情视图，不显示筛选栏 */}
      {historyTab !== "original" && (
        <div className="hist-filter-bar">
          <div className="hist-filter-search">
            <Icon icon={Search} size={14} />
            <input
              aria-label="按文件名搜索"
              className="form-input"
              type="text"
              placeholder="按文件名搜索…"
              value={fileSearch}
              onChange={(event) => updateListFilters({ fileSearch: event.target.value })}
            />
          </div>
          <select
            className="form-select"
            value={templateFilter}
            onChange={(event) => updateListFilters({ templateFilter: event.target.value })}
            aria-label="按模板筛选"
          >
            <option value="">全部模板</option>
            {Object.entries(templateNameMap).map(([id, name]) => (
              <option key={id} value={id}>{name}</option>
            ))}
          </select>
          <select
            className="form-select"
            value={timeFilter}
            onChange={(event) => updateListFilters({ timeFilter: event.target.value })}
            aria-label="按时间筛选"
          >
            <option value="all">全部时间</option>
            <option value="today">今天</option>
            <option value="7d">最近 7 天</option>
            <option value="30d">最近 30 天</option>
          </select>
          {(fileSearch || templateFilter || timeFilter !== "all") && (
            <button
              className="btn ghost sm"
              onClick={() => updateListFilters(DEFAULT_FILTERS)}
            >
              清除筛选
            </button>
          )}
        </div>
      )}



      {historyTab !== "original" && (
        loading && pageTasks.length === 0 ? (
        <div className="card flush history-table-card"><div className="history-empty-state" role="status">
          <Icon icon={Loader2} size={24} className="spin" /><span>正在读取文件记录…</span>
        </div></div>
        ) : (
        <>
          {historyTab === "completed" && (
            <div className="card flush history-table-card" aria-busy={loading}>
              {pageTasks.length === 0 ? renderEmptyHistoryState("completed") : renderTable(
                ["#", "文件名", "模板", "处理方式", "记录数", "提取耗时", "完成时间", ""],
                (
                  <colgroup>
                    <col style={{ width: `${colWidths[0]}px` }} />
                    <col style={{ width: `${colWidths[1]}px` }} />
                    <col style={{ width: `${colWidths[2]}px` }} />
                    <col style={{ width: `${colWidths[3]}px` }} />
                    <col style={{ width: `${colWidths[4]}px` }} />
                    <col style={{ width: `${colWidths[5]}px` }} />
                    <col style={{ width: `${colWidths[6]}px` }} />
                    <col style={{ width: "48px" }} />
                  </colgroup>
                ),
                pageTasks.map((t, idx) => (
                      <tr key={t.id}>
                        <td className="hist-idx">{loadedOffset + idx + 1}</td>
                        <td>
                          <button
                            className="hist-file-btn"
                            ref={returnToFile.current?.id === t.id ? returnedFileButton : undefined}
                            onClick={() => openOriginal(t)}
                            title="查看原文件与提取数据"
                          >
                            <Icon icon={FileText} style={{ width: "14px", height: "14px", flexShrink: 0 }} />
                            <span>{taskDisplayName(t)}</span>
                          </button>
                        </td>
                        <td>{templateLabel(t)}</td>
                        <td><span className="badge muted">{t.processing_model ?? "—"}</span></td>
                        <td className="numeric">{t.record_count ?? 0}</td>
                        <td>{formatElapsed(t.processing_elapsed_seconds)}</td>
                        <td>{t.completed_at ? formatDateTime(t.completed_at) : "—"}</td>
                        <td className="history-actions-gutter"><FileHistoryActions filename={taskDisplayName(t)} onOpen={() => openOriginal(t)}
                          onDelete={() => setDeleteTarget(t)} /></td>
                      </tr>
                    )),
              )}
              {renderHistoryPager(currentPage, currentPageCount, currentCount, setCompletedPage)}
            </div>
          )}

          {historyTab === "problems" && (
            <div className="card flush history-table-card" aria-busy={loading}>
              {pageTasks.length === 0 ? renderEmptyHistoryState("problems") : renderTable(
                ["#", "文件名", "模板", "处理方式", "问题", "处理时间", ""],
                (
                  <colgroup>
                    <col style={{ width: `${colWidths[0]}px` }} />
                    <col style={{ width: `${colWidths[1]}px` }} />
                    <col style={{ width: `${colWidths[2]}px` }} />
                    <col style={{ width: `${colWidths[3]}px` }} />
                    <col style={{ width: `${colWidths[4]}px` }} />
                    <col style={{ width: `${colWidths[5]}px` }} />
                    <col style={{ width: "48px" }} />
                  </colgroup>
                ),
                pageTasks.map((p, idx) => (
                      <tr key={p.id}>
                        <td className="hist-idx">{loadedOffset + idx + 1}</td>
                        <td>
                          <button
                            className="hist-file-btn"
                            ref={returnToFile.current?.id === p.id ? returnedFileButton : undefined}
                            onClick={() => openOriginal(p)}
                            title="查看原文件与提取数据"
                          >
                            <Icon icon={FileText} style={{ width: "14px", height: "14px", flexShrink: 0 }} />
                            <span>{taskDisplayName(p)}</span>
                          </button>
                        </td>
                        <td>{templateLabel(p)}</td>
                        <td><span className="badge muted">{p.processing_model ?? "—"}</span></td>
                        <td>
                          <div className="hist-problem-cell">
                            <span className="hist-problem-type">待确认</span>
                            <span className="hist-problem-detail">提取结果需要人工确认</span>
                          </div>
                        </td>
                        <td>{formatDateTime(p.updated_at)}</td>
                        <td className="history-actions-gutter"><FileHistoryActions filename={taskDisplayName(p)} onOpen={() => openOriginal(p)}
                          onDelete={() => setDeleteTarget(p)} busy={busyTaskId === p.id} onIgnore={() => void handleIgnoreAll(p)} /></td>
                      </tr>
                    )),
                "problems",
              )}
              {renderHistoryPager(currentPage, currentPageCount, currentCount, setProblemsPage)}
            </div>
          )}

        </>
        )
      )}

      {/* 原文件面板：常驻挂载，切 tab 用 CSS 隐藏，避免预览反复重载导致一直转圈 */}
      <div
        className={`card flush history-original-card${historyTab === "original" ? "" : " hidden"}`}
        aria-hidden={historyTab !== "original"}
      >
        {!selectedTask ? (
          <div className="hist-empty" style={{ padding: 48, textAlign: "center" }}>
            从上方记录中选择一个文件查看原文件与提取数据
          </div>
        ) : extractionLoading ? (
          <div style={{ padding: 48, textAlign: "center" }}>
            <Icon icon={Loader2} size={24} className="spin" />
          </div>
        ) : originalReadError && !extraction ? (
          <div className="history-original-read-error" role="alert">
            <strong>暂时无法读取这份文件的提取结果</strong>
            <span>{originalReadError}</span>
            <button className="btn secondary sm" type="button" onClick={retryOriginalRead} disabled={extractionLoading}>
              {extractionLoading ? "正在读取…" : "重新读取提取结果"}
            </button>
          </div>
        ) : (
          <div className="history-original-body" ref={originalContentRef}>
                  <div className="history-action-bar">
                    {extraction?.has_current_data === false && <span className="support">当前数据已清空</span>}
                    {error && <button className="gtb-failure-link" type="button" aria-haspopup="dialog" onClick={() => {
                      clear(); setOriginalNoticeOpen(true);
                    }}>{reviewConflict ? "处理冲突" : "查看原因"}</button>}
                    {extraction ? (
                      <>
                        <button className="btn secondary sm" onClick={() => setFileHistoryOpen(true)}><Icon icon={History} size={14} /> 历史版本</button>
                        <button
                          className="btn primary sm"
                          disabled={saving || extraction.has_current_data === false}
                          onClick={() => void handleSaveToTable()}
                          title="提交本轮修改，形成一个文件修订版本"
                        >
                          <Icon icon={saving ? Loader2 : Save} size={14} className={saving ? "spin" : undefined} />{" "}
                          修订到表
                        </button>
                      </>
                    ) : null}
                    {onOpenData ? (
                      <button className="btn secondary sm" onClick={() => onOpenData(selectedTask)} title="跳转到这张表的数据记录">
                        <Icon icon={Table2} size={14} /> 跳转到表 <Icon icon={ArrowRight} size={13} />
                      </button>
                    ) : null}
                  </div>
                  {originalNoticeOpen && error && <NoticeDialog title={reviewConflict ? "修订冲突" : "修改未保存"}
                    onClose={() => setOriginalNoticeOpen(false)}
                    actions={reviewConflict && <button className="btn primary sm" type="button" disabled={saving} onClick={() => {
                      setOriginalNoticeOpen(false); retryOriginalRead();
                    }}>重新读取</button>}><p>{error}</p></NoticeDialog>}
                  {/* 元信息 */}
                  <div className="history-meta-bar">
                    <div className="hist-meta-item">
                      <span className="hist-meta-label">提取耗时</span>
                      <span className="hist-meta-value">
                        {formatElapsed(extraction?.elapsed_seconds ?? selectedTask.processing_elapsed_seconds)}
                      </span>
                    </div>
                    <div className="hist-meta-item">
                      <span className="hist-meta-label">文件</span>
                      <span className="hist-meta-value">{taskDisplayName(selectedTask)}</span>
                    </div>
                    <div className="hist-meta-item">
                      <span className="hist-meta-label">模板</span>
                      <span className="hist-meta-value">{templateLabel(selectedTask)}</span>
                    </div>
                    <div className="hist-meta-item">
                      <span className="hist-meta-label">状态</span>
                      <span className="hist-meta-value">
                        {selectedTask.status === "needs_review" ? "待确认" : "已完成"}
                      </span>
                    </div>
                    <div className="hist-meta-item">
                      <span className="hist-meta-label">最近更新</span>
                      <span className="hist-meta-value">{formatDateTime(selectedTask.updated_at)}</span>
                    </div>
                    <div className="hist-meta-item">
                      <span className="hist-meta-label">完成时间</span>
                      <span className="hist-meta-value">
                        {selectedTask.completed_at
                          ? formatDateTime(selectedTask.completed_at)
                          : "待确认"}
                      </span>
                    </div>
                  </div>

                  {extraction ? (
                    <div className="history-original-body">
                      {extraction.input_scope?.coverage === "partial" && <p className="small muted">仅处理部分内容</p>}
                      {/* 提取数据（复用提取页样式） */}
                      <div className="extract-data-block">
                        <div className="extract-header-card">
                          <div className="extract-header-title">
                            <Icon icon={FileText} size={18} /> 文件级信息
                          </div>
                          <div className="extract-header-grid">
                            {extractionHeaderKeys.map((key) => (
                              <div
                                className="extract-header-row" data-review-field={`h-${key}`} tabIndex={-1}
                                key={key}
                                onClick={() => { if (!saving) setEditingCell(`h-${key}`); }}
                                title="单击编辑"
                              >
                                <span className="extract-header-label">{labelFor(key)}</span>
                                <EditableText
                                  className="extract-header-value"
                                  editing={editingCell === `h-${key}`}
                                  value={headerValueOf(key)}
                                  onCommit={(raw) => updateHeaderDraft(key, raw)}
                                  onCancel={() => setEditingCell(null)}
                                />
                              </div>
                            ))}
                          </div>
                        </div>

                        {extractionItemKeys.length > 0 ? (
                          <div className="data-table-wrap extract-item-wrap">
                            <div className="extract-item-title">
                              <span>
                                <Icon icon={List} size={18} /> 明细
                              </span>
                              <span className="extract-item-count">
                                {displayItems.length + " 行"}
                              </span>
                            </div>
                            <div className="data-table-scroll">
                              <table className="data-table" style={{ minWidth: detailColumnWidth + 52 }}>
                                <colgroup>
                                  <col style={{ width: 52 }} />
                                  {extractionItemKeys.map(key => <col key={key} style={{ width: detailWidths[key] ?? 132 }} />)}
                                </colgroup>
                                <thead>
                                  <tr>
                                    <th className="detail-row-gutter" scope="col" aria-label="明细序号">#</th>
                                    {extractionItemKeys.map(key => <th key={key}>
                                      <span>{labelFor(key)}</span>
                                      <DetailColumnResizer label={labelFor(key)} width={detailWidths[key] ?? 132}
                                        onResize={width => setDetailWidths(current => ({ ...current, [key]: width }))} />
                                    </th>)}
                                  </tr>
                                </thead>
                                <tbody>
                                  {displayItems.slice(detailPage.start, detailPage.end).map((item, offset) => {
                                    const idx = detailPage.start + offset;
                                    return (
                                    <tr key={idx}>
                                      <td className="detail-row-gutter"><DetailRowAction index={idx + 1} disabled={saving} onDelete={() => {
                                        setDraft(current => current ? {...current, items: current.items.filter((_, index) => index !== idx)} as ExtractionResult : current);
                                        setDraftDetailIds(current => current?.filter((_, index) => index !== idx));
                                        setEditingCell(null);
                                      }} /></td>
                                      {extractionItemKeys.map((key) => (
                                        <td
                                          data-review-field={`i-${idx}-${key}`}
                                          aria-label={`编辑 ${labelFor(key)}，当前值 ${String(item[key] ?? "") || "空"}`}
                                          key={key}
                                          onClick={() => { if (!saving) setEditingCell(`i-${idx}-${key}`); }}
                                          onKeyDown={(event) => {
                                            if (saving || event.target !== event.currentTarget) return;
                                            if (event.key === "Enter" || event.key === "F2") {
                                              event.preventDefault();
                                              setEditingCell(`i-${idx}-${key}`);
                                            }
                                          }}
                                          tabIndex={0}
                                          title="单击编辑"
                                        >
                                          <EditableText
                                            editing={editingCell === `i-${idx}-${key}`}
                                            value={String(item[key] ?? "")}
                                            onCommit={(raw) => updateItemDraft(idx, key, raw)}
                                            onCancel={() => setEditingCell(null)}
                                          />
                                        </td>
                                      ))}
                                    </tr>
                                  ); })}
                                  {displayItems.length === 0 ? (
                                    <tr><td colSpan={extractionItemKeys.length + 1} className="hist-empty">无明细</td></tr>
                                  ) : null}
                                </tbody>
                              </table>
                            </div>
                            <DetailPagination paging={detailPage} count={displayItems.length} disabled={saving} />
                          </div>
                        ) : null}
                      </div>

                      <ValidationResults
                        template={extraction.template}
                        issues={extraction.validation_issues.map((issue, index) => {
                          const match = issue.field.match(/^items\[(\d+)\]\.(.+)$/);
                          const key = match ? match[2] : issue.field.replace(/^header\./, "");
                          return { index, code: issue.code, row: match ? `明细第 ${Number(match[1]) + 1} 行` : "文件级信息",
                            field: labelFor(issue.field), message: issue.message, severity: issue.severity, rawValue: issue.raw_value,
                            valueType: fieldType(match ? "item" : "header", key),
                            notChecked: issue.not_checked, advisory: selectedTask?.status === "completed",
                            ignored: ignoredIssueIndices.includes(index),
                            canLocate: match ? Number(match[1]) < displayItems.length && extractionItemKeys.includes(key) : extractionHeaderKeys.includes(key) };
                        })}
                        disabled={saving}
                        onToggleIgnored={toggleIssueIgnored}
                        onLocate={index => {
                          const issue = extraction.validation_issues[index];
                          const match = issue.field.match(/^items\[(\d+)\]\.(.+)$/);
                          const key = match ? `i-${match[1]}-${match[2]}` : `h-${issue.field.replace(/^header\./, "")}`;
                          if (match) detailPage.setPage(Math.floor(Number(match[1]) / detailPage.size) + 1);
                          locateValidationField(key);
                        }}
                      />
                    </div>
                  ) : null}

                  {/* 原文件 */}
                  <div className="history-original-panel">
                    <div className="history-panel-title">
                      <span>原文件</span>
                      <OriginalDownload key={selectedTask.id} url={getOriginalFileUrl(selectedTask.id)} filename={selectedTask.filename} />
                    </div>
                    <div className="history-panel-body history-preview">
                      <DocumentPreview
                        contentType={selectedTask.content_type}
                        filename={selectedTask.filename}
                        flowPages
                        pageNumber={1}
                        scale={1}
                        url={getOriginalFileUrl(selectedTask.id)}
                      />
                    </div>
                    {extraction && <Disclosure className="processing-details" title="来源与处理详情">
                      <InputScopeDetails scope={extraction.input_scope} />
                      {selectedTask.internal_storage && <Disclosure className="source-section" title={<>知意内部原件 · {selectedTask.internal_storage.status === "classified" ? "已按模板保存" : "归类尚未完成"}</>}>
                        <FileLocation path={selectedTask.internal_storage.absolute_path ?? selectedTask.internal_storage.relative_path ?? selectedTask.internal_storage.error ?? "原件位置正在调整，仍可按任务追溯。"} />
                        <p className="support">这是知意管理的完整原件，供预览、下载和备份。内部目录首次按模板名确定，后续模板改名不会搬动旧文件；明确删除原件或清除全部本地数据会删除它。外部副本独立保留。</p>
                      </Disclosure>}
                      {extraction.file_name && <Disclosure className="source-section" title="文件名称">
                        <p className="source-value" style={{ overflowWrap: "anywhere" }}>上传原名：{selectedTask.filename}</p>
                        <p className="source-value" style={{ overflowWrap: "anywhere" }}>确认后的名称：{extraction.file_name.confirmed_filename ?? "尚未确认，仍保留原名"}</p>
                        <p className="support">{extraction.file_name.explanation}</p>
                      </Disclosure>}
                      <TaskExportDetails key={selectedTask.id} task={selectedTask} />
                    </Disclosure>}
                  </div>
                </div>
              )}
      </div>

      {unsavedReview.dialog}
      <DeleteTaskDialog
        tasks={deleteTarget ? [deleteTarget] : []}
        busy={deleteTarget !== null && busyTaskId === deleteTarget.id}
        error={error}
        onConfirm={() => void handleDeleteTask()}
        onClose={() => { setDeleteTarget(null); setError(null); }}
      />
    </div>
  );
}
