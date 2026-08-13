import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRight,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  EyeOff,
  FileText,
  List,
  Loader2,
  Save,
  Search,
  ShieldAlert,
  Table2,
  Trash2,
  Wrench,
} from "lucide-react";
import {
  confirmTask,
  deleteTask,
  getExtraction,
  getOriginalFileUrl,
  getTables,
  getTemplates,
  getTasks,
  getTaskSummary,
  updateReview,
} from "../api";
import type {
  DataTableRead,
  Extraction,
  ExtractionResult,
  Task,
  TaskSummary,
  TemplateValue,
} from "../types";
import { serverDate } from "../time";
import { DocumentPreview } from "./DocumentPreview";
import { EditableText } from "./EditableText";
import { ConfirmDialog } from "./ConfirmDialog";
import { Icon } from "./Icon";

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

interface HistoryPageProps {
  onOpenTask?: (task: Task) => void;
  onOpenData?: (task: Task) => void;
}

export function HistoryPage({ onOpenTask, onOpenData }: HistoryPageProps = {}) {
  const [historyTab, setHistoryTab] = useState<HistoryTab>("completed");
  // 当前页任务：服务端分页，万级历史不一次载入前端内存（ISSUE-067）
  const [pageTasks, setPageTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 全局计数（统计条）与"当前筛选下"计数（tab 角标 + 总页数）
  const [summary, setSummary] = useState<TaskSummary | null>(null);
  const [filteredSummary, setFilteredSummary] = useState<TaskSummary | null>(null);

  const [selectedTask, setSelectedTask] = useState<Task | null>(null);
  const [extraction, setExtraction] = useState<Extraction | null>(null);
  const [extractionLoading, setExtractionLoading] = useState(false);
  const [templateNameMap, setTemplateNameMap] = useState<Record<string, string>>({});
  const [tables, setTables] = useState<DataTableRead[]>([]);
  const [targetTableId, setTargetTableId] = useState<string>("");
  const [columnWidths, setColumnWidths] = useState<Record<number, number>>({});
  const [detailWidths, setDetailWidths] = useState<Record<string, number>>({});

  // 列表筛选与分页（一页 10 条，超过翻页）
  const [fileSearch, setFileSearch] = useState("");
  const [templateFilter, setTemplateFilter] = useState("");
  const [timeFilter, setTimeFilter] = useState("all");
  const [completedPage, setCompletedPage] = useState(1);
  const [problemsPage, setProblemsPage] = useState(1);
  const [jumpPageInput, setJumpPageInput] = useState("");

  // 原文件 tab 内编辑：草稿模式，点“保存到表”才触发校验并保存
  const [editingCell, setEditingCell] = useState<string | null>(null);
  const [draft, setDraft] = useState<ExtractionResult | null>(null);
  const [saving, setSaving] = useState(false);
  // 原文件加载竞态守卫：快速切换文件时只让最新一次请求写入结果
  const originalSeqRef = useRef(0);
  // 用户忽略的校验问题下标：被忽略的问题仍记录，但不再阻断保存到表
  const [ignoredIssueIndices, setIgnoredIssueIndices] = useState<number[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyTaskId, setBusyTaskId] = useState<string | null>(null);
  // 删除确认：文件历史删除=删除任务记录，不删除数据表中的数据；需输入文件名确认
  const [deleteTarget, setDeleteTarget] = useState<Task | null>(null);

  useEffect(() => {
    getTemplates(true)
      .then((templates) =>
        setTemplateNameMap(
          Object.fromEntries(templates.map((t) => [t.id, t.name])),
        ),
      )
      .catch(() => setTemplateNameMap({}));
    getTables()
      .then(setTables)
      .catch(() => setTables([]));
  }, []);

  const HISTORY_PAGE_SIZE = 10;

  // 时间筛选 → since（按 updated_at 下限服务端过滤）
  const timeFilterSince = (): string | undefined => {
    if (timeFilter === "all") return undefined;
    const hours = timeFilter === "today" ? 24 : timeFilter === "7d" ? 168 : 720;
    return new Date(Date.now() - hours * 3600_000).toISOString();
  };

  // 服务端分页 + 筛选（ISSUE-067）：只拉当前页，不把万级历史载入前端内存。
  // 校验问题 = 提取结果已出来但规则不通过（needs_review）；处理失败（failed）不属于校验问题，去状态监控重试。
  const load = async () => {
    if (historyTab === "original") return;
    const isProblems = historyTab === "problems";
    const status = isProblems ? "needs_review" : "completed";
    const rawPage = isProblems ? problemsPage : completedPage;
    const count = isProblems
      ? (filteredSummary?.needs_review ?? 0)
      : (filteredSummary?.completed ?? 0);
    const page = Math.min(rawPage, Math.max(1, Math.ceil(count / HISTORY_PAGE_SIZE)));
    const searchText = fileSearch.trim();
    const common = {
      search: searchText || undefined,
      templateId: templateFilter || undefined,
      since: timeFilterSince(),
    };
    try {
      setError(null);
      const [list, globalSummary, filtered] = await Promise.all([
        getTasks({
          ...common,
          status,
          limit: HISTORY_PAGE_SIZE,
          offset: (page - 1) * HISTORY_PAGE_SIZE,
        }),
        getTaskSummary(),
        getTaskSummary(common),
      ]);
      setPageTasks(list);
      setSummary(globalSummary);
      setFilteredSummary(filtered);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
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
    return () => clearTimeout(timer);
    // 显式列出触发条件：load 使用最新渲染闭包，避免 filteredSummary 变化引发重复加载
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [historyTab, completedPage, problemsPage, fileSearch, templateFilter, timeFilter]);

  const currentCount = historyTab === "problems"
    ? (filteredSummary?.needs_review ?? 0)
    : (filteredSummary?.completed ?? 0);
  const currentPageCount = Math.max(1, Math.ceil(currentCount / HISTORY_PAGE_SIZE));
  const currentPage =
    historyTab === "problems"
      ? Math.min(problemsPage, currentPageCount)
      : Math.min(completedPage, currentPageCount);

  function templateLabel(t: Task): string {
    if (t.template_id && templateNameMap[t.template_id]) {
      return templateNameMap[t.template_id];
    }
    if (t.template_mode === "smart") return "智能匹配";
    return t.template_id ?? t.template_mode;
  }

  function openOriginal(task: Task) {
    setSelectedTask(task);
    setHistoryTab("original");
    setExtraction(null);
    setDraft(null);
    setEditingCell(null);
    setExtractionLoading(true);
    setError(null);
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
        setError(err instanceof Error ? err.message : "加载提取结果失败");
      })
      .finally(() => {
        if (seq === originalSeqRef.current) setExtractionLoading(false);
      });
  }

  function editedValue(raw: string, previous: TemplateValue | undefined): TemplateValue {
    const value = raw.trim();
    if (!value || value === "—") return null;
    if (typeof previous === "number") {
      const parsed = Number(value.replaceAll(",", ""));
      return Number.isFinite(parsed) ? parsed : previous;
    }
    if (typeof previous === "boolean") return value === "是" || value.toLowerCase() === "true";
    return value;
  }

  function updateHeaderDraft(key: string, raw: string) {
    setDraft((current) => {
      if (!current) return current;
      const next = structuredClone(current);
      if ("header" in next) {
        next.header[key] = editedValue(raw, next.header[key]);
      } else {
        (next as unknown as Record<string, unknown>)[key] = editedValue(
          raw,
          (next as unknown as Record<string, unknown>)[key] as TemplateValue | undefined,
        );
      }
      return next;
    });
    setEditingCell(null);
  }

  function updateItemDraft(idx: number, key: string, raw: string) {
    setDraft((current) => {
      if (!current) return current;
      const next = structuredClone(current);
      const items = (next as { items: Array<Record<string, TemplateValue>> }).items;
      if (items[idx]) {
        items[idx][key] = editedValue(raw, items[idx][key]);
      }
      return next;
    });
    setEditingCell(null);
  }

  async function handleSaveToTable() {
    if (!selectedTask || !extraction || !draft) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await updateReview(
        selectedTask.id,
        extraction.review_version,
        draft,
        ignoredIssueIndices,
      );
      setExtraction(updated);
      setDraft(structuredClone(updated.result));
      setEditingCell(null);
      setIgnoredIssueIndices(
        (updated.validation_issues ?? [])
          .map((issue, index) => (issue.ignored ? index : -1))
          .filter((index) => index >= 0),
      );
      // 用户显式选择了目标表：确认入表（保存到表 = 镜像同步）
      if (targetTableId) {
        await confirmTask(selectedTask.id, updated.review_version, targetTableId);
      }
      // 保存后刷新列表：任务可能从"校验问题"变"已完成"，列表状态不滞后
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  function toggleIssueIgnored(index: number) {
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
      const allIndices = (extractionData.validation_issues ?? []).map(
        (_issue, index) => index,
      );
      await updateReview(
        task.id,
        extractionData.review_version,
        extractionData.result,
        allIndices,
      );
      setNotice(`已忽略校验问题并完成：${task.filename}`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "忽略并完成失败");
    } finally {
      setBusyTaskId(null);
    }
  }

  // 文件历史删除：删除任务记录与提取结果，不删除数据表中已确认的数据
  async function handleDeleteTask() {
    if (!deleteTarget) return;
    setBusyTaskId(deleteTarget.id);
    setError(null);
    try {
      const result = await deleteTask(deleteTarget.id);
      setDeleteTarget(null);
      setNotice(
        result.kept_rows > 0
          ? `已删除记录：${deleteTarget.filename}。数据表中保留 ${result.kept_rows} 行数据（不再追溯此文件）。`
          : `已删除记录：${deleteTarget.filename}。`,
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
    event.preventDefault();
    const startX = event.clientX;
    const move = (moveEvent: PointerEvent) => {
      setColumnWidths((current) => ({
        ...current,
        [index]: Math.min(360, Math.max(48, initialWidth + moveEvent.clientX - startX)),
      }));
    };
    const finish = () => {
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerup", finish);
    };
    document.addEventListener("pointermove", move);
    document.addEventListener("pointerup", finish);
  }

  function startDetailResize(
    event: React.PointerEvent<HTMLSpanElement>,
    key: string,
    initialWidth: number,
  ) {
    event.preventDefault();
    const startX = event.clientX;
    const move = (moveEvent: PointerEvent) => {
      setDetailWidths((current) => ({
        ...current,
        [key]: Math.min(360, Math.max(48, initialWidth + moveEvent.clientX - startX)),
      }));
    };
    const finish = () => {
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerup", finish);
    };
    document.addEventListener("pointermove", move);
    document.addEventListener("pointerup", finish);
  }

  const colWidths = useMemo<Record<number, number>>(
    () => ({
      0: columnWidths[0] ?? 48,
      1: columnWidths[1] ?? 260,
      2: columnWidths[2] ?? 120,
      3: columnWidths[3] ?? 180,
      4: columnWidths[4] ?? 140,
      5: columnWidths[5] ?? 140,
      6: columnWidths[6] ?? 190,
    }),
    [columnWidths],
  );

  const renderColumnResizer = (index: number) => (
    <span
      aria-label={`调整第 ${index + 1} 列宽`}
      aria-orientation="vertical"
      className="column-resizer"
      onPointerDown={(event) => startResize(event, index, colWidths[index])}
      role="separator"
      tabIndex={0}
    />
  );

  // 抬头/明细字段一律以提取所用模板字段为准（与数据表、提取页同源）：
  // 模板缺失（旧记录/模板已删）时不显示字段，也不回退发票全集，避免送货单冒出税字段。
  const extractionHeaderKeys = useMemo(() => {
    if (!extraction?.template) return [];
    return extraction.template.fields
      .filter((field) => field.section === "header" && field.key)
      .map((field) => field.key as string);
  }, [extraction]);

  const extractionItemKeys = useMemo(() => {
    if (!extraction?.template) return [];
    return extraction.template.fields
      .filter((field) => field.section === "item" && field.key)
      .map((field) => field.key as string);
  }, [extraction]);

  function labelFor(key: string): string {
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

  const renderTable = (
    headers: string[],
    colgroup: React.ReactNode,
    rows: React.ReactNode[],
    variant?: "problems",
  ) => (
    <div className="data-table-wrap">
      <table className={`data-table history-table${variant ? ` ${variant}` : ""}`} style={{ tableLayout: "fixed" }}>
        {colgroup}
        <thead>
          <tr>
            {headers.map((header, index) => (
              <th key={index} style={{ width: `${colWidths[index]}px` }}>
                <span>{header}</span>
                {renderColumnResizer(index)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
  );

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
        {loading ? <><Icon icon={Loader2} size={13} className="spin" /> 正在加载下一页…</> : <>显示 {total === 0 ? 0 : (page - 1) * HISTORY_PAGE_SIZE + 1}-{Math.min(page * HISTORY_PAGE_SIZE, total)} 条，共 {total} 条</>}
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
    <div className="view">
      <div className="page-header">
        <div className="eyebrow">追溯</div>
        <h1>文件历史</h1>
        <div className="support">已处理文件的历史记录 · 点击文件名可回溯查看原文件与提取数据</div>
      </div>

      {error && (
        <div className="callout danger" style={{ marginBottom: 16 }}>{error}</div>
      )}
      {notice && (
        <div className="callout success" style={{ marginBottom: 16 }}>{notice}</div>
      )}

      {/* 累计统计条 */}
      <div className="hist-stats-bar">
        <div className="hist-stat-item">
          <div className="hist-stat-num">{summary?.total ?? 0}</div>
          <div className="hist-stat-label">累计任务</div>
        </div>
        <div className="hist-stat-divider"></div>
        <div className="hist-stat-item">
          <div className="hist-stat-num">{summary?.completed ?? 0}</div>
          <div className="hist-stat-label">已完成</div>
        </div>
        <div className="hist-stat-divider"></div>
        <div className="hist-stat-item">
          <div className="hist-stat-num">{summary?.needs_review ?? 0}</div>
          <div className="hist-stat-label">校验问题</div>
        </div>
      </div>

      {/* Tab */}
      <div className="history-tabs">
        <button
          className={`hist-tab ${historyTab === "completed" ? "active" : ""}`}
          onClick={() => setHistoryTab("completed")}
        >
          已完成 <span className="hist-tab-count">{filteredSummary?.completed ?? 0}</span>
        </button>
        <button
          className={`hist-tab ${historyTab === "problems" ? "active" : ""}`}
          onClick={() => setHistoryTab("problems")}
        >
          校验问题 <span className="hist-tab-count">{filteredSummary?.needs_review ?? 0}</span>
        </button>
        <button
          className={`hist-tab ${historyTab === "original" ? "active" : ""}`}
          onClick={() => setHistoryTab("original")}
        >
          原文件
          {selectedTask ? <span className="hist-tab-count">1</span> : null}
        </button>
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
              onChange={(e) => setFileSearch(e.target.value)}
            />
          </div>
          <select
            className="form-select"
            value={templateFilter}
            onChange={(e) => setTemplateFilter(e.target.value)}
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
            onChange={(e) => setTimeFilter(e.target.value)}
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
              onClick={() => {
                setFileSearch("");
                setTemplateFilter("");
                setTimeFilter("all");
              }}
            >
              清除筛选
            </button>
          )}
        </div>
      )}

      {historyTab !== "original" && (
        loading && pageTasks.length === 0 ? (
        <div className="card flush" style={{ padding: 48, textAlign: "center" }}>
          <Icon icon={Loader2} size={24} className="spin" />
        </div>
        ) : (
        <>
          {historyTab === "completed" && (
            <div className="card flush history-table-card" aria-busy={loading}>
              {renderTable(
                ["#", "文件名", "模板", "处理方式", "记录数", "提取耗时", "完成时间", "操作"],
                (
                  <colgroup>
                    <col style={{ width: `${colWidths[0]}px` }} />
                    <col style={{ width: `${colWidths[1]}px` }} />
                    <col style={{ width: `${colWidths[2]}px` }} />
                    <col style={{ width: `${colWidths[3]}px` }} />
                    <col style={{ width: `${colWidths[4]}px` }} />
                    <col style={{ width: `${colWidths[5]}px` }} />
                    <col style={{ width: "200px" }} />
                    <col style={{ width: "190px" }} />
                  </colgroup>
                ),
                pageTasks.length === 0
                  ? [
                      <tr key="empty">
                        <td colSpan={8} className="hist-empty-row">暂无已完成的任务</td>
                      </tr>,
                    ]
                  : pageTasks.map((t, idx) => (
                      <tr key={t.id}>
                        <td className="hist-idx">{idx + 1}</td>
                        <td>
                          <button
                            className="hist-file-btn"
                            onClick={() => openOriginal(t)}
                            title="查看原文件与提取数据"
                          >
                            <Icon icon={FileText} style={{ width: "14px", height: "14px", flexShrink: 0 }} />
                            <span>{t.filename}</span>
                          </button>
                        </td>
                        <td>{templateLabel(t)}</td>
                        <td><span className="badge muted">{t.processing_model ?? "—"}</span></td>
                        <td className="numeric">{t.record_count ?? 0}</td>
                        <td>{formatElapsed(t.processing_elapsed_seconds)}</td>
                        <td>{t.completed_at ? formatDateTime(t.completed_at) : "—"}</td>
                        <td>
                          <div className="hist-actions">
                            <button className="btn ghost xs" onClick={() => openOriginal(t)}>
                              <Icon icon={FileText} style={{ width: "12px", height: "12px" }} /> 查看原文件
                            </button>
                            <button
                              className="btn ghost xs danger-btn"
                              title="删除该文件的任务记录（数据表中的数据保留）"
                              onClick={() => setDeleteTarget(t)}
                            >
                              <Icon icon={Trash2} style={{ width: "12px", height: "12px" }} /> 删除
                            </button>
                          </div>
                        </td>
                      </tr>
                    )),
              )}
              {renderHistoryPager(currentPage, currentPageCount, currentCount, setCompletedPage)}
            </div>
          )}

          {historyTab === "problems" && (
            <div className="card flush history-table-card" aria-busy={loading}>
              {renderTable(
                ["#", "文件名", "模板", "处理方式", "问题", "处理时间", "操作"],
                (
                  <colgroup>
                    <col style={{ width: `${colWidths[0]}px` }} />
                    <col style={{ width: `${colWidths[1]}px` }} />
                    <col style={{ width: `${colWidths[2]}px` }} />
                    <col style={{ width: `${colWidths[3]}px` }} />
                    <col style={{ width: `${colWidths[4]}px` }} />
                    <col style={{ width: `${colWidths[5]}px` }} />
                    <col style={{ width: "200px" }} />
                  </colgroup>
                ),
                pageTasks.length === 0
                  ? [
                      <tr key="empty">
                        <td colSpan={7} className="hist-empty-row">暂无校验问题</td>
                      </tr>,
                    ]
                  : pageTasks.map((p, idx) => (
                      <tr key={p.id}>
                        <td className="hist-idx">{idx + 1}</td>
                        <td>
                          <button
                            className="hist-file-btn"
                            onClick={() => openOriginal(p)}
                            title="查看原文件与提取数据"
                          >
                            <Icon icon={FileText} style={{ width: "14px", height: "14px", flexShrink: 0 }} />
                            <span>{p.filename}</span>
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
                        <td>
                          <div className="hist-actions">
                            <button
                              className="btn primary xs"
                              onClick={() => openOriginal(p)}
                              title="打开原文件修正提取结果"
                            >
                              <Icon icon={Wrench} style={{ width: "12px", height: "12px" }} /> 修正
                            </button>
                            <button
                              className="btn ghost xs hist-ignore"
                              disabled={busyTaskId === p.id}
                              onClick={() => void handleIgnoreAll(p)}
                              title="忽略该校验问题并直接完成任务（变为已完成）"
                            >
                              <Icon icon={EyeOff} style={{ width: "12px", height: "12px" }} />
                              {busyTaskId === p.id ? "处理中" : "忽略"}
                            </button>
                            <button
                              className="btn ghost xs danger-btn"
                              title="删除该文件的任务记录（数据表中的数据保留）"
                              onClick={() => setDeleteTarget(p)}
                            >
                              <Icon icon={Trash2} style={{ width: "12px", height: "12px" }} /> 删除
                            </button>
                          </div>
                        </td>
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
        ) : (
          <div className="history-original-body">
                  <div className="history-action-bar">
                    {extraction ? (
                      <>
                        <select
                          aria-label="选择保存到的数据表"
                          className="form-select"
                          value={targetTableId}
                          onChange={(e) => setTargetTableId(e.target.value)}
                          title="选择保存到的数据表；默认使用模板唯一表"
                        >
                          <option value="">默认表（按模板）</option>
                          {tables.map((t) => (
                            <option key={t.id} value={t.id}>
                              {t.name}（{t.row_count} 行）
                            </option>
                          ))}
                        </select>
                        <button
                          className="btn primary sm"
                          disabled={saving}
                          onClick={() => void handleSaveToTable()}
                          title="保存修改并通过规则校验后写入数据表"
                        >
                          <Icon icon={saving ? Loader2 : Save} size={14} className={saving ? "spin" : undefined} />{" "}
                          保存到表
                        </button>
                      </>
                    ) : null}
                    {onOpenData ? (
                      <button className="btn secondary sm" onClick={() => onOpenData(selectedTask)} title="跳转到这张表的数据记录">
                        <Icon icon={Table2} size={14} /> 跳转到表 <Icon icon={ArrowRight} size={13} />
                      </button>
                    ) : null}
                  </div>
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
                      <span className="hist-meta-value">{selectedTask.filename}</span>
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
                      <span className="hist-meta-label">处理时间</span>
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
                      {/* 提取数据（复用提取页样式） */}
                      <div className="extract-data-block">
                        <div className="extract-header-card">
                          <div className="extract-header-title">
                            <Icon icon={FileText} size={18} /> 提取数据（抬头）
                          </div>
                          <div className="extract-header-grid">
                            {extractionHeaderKeys.map((key) => (
                              <div
                                className="extract-header-row"
                                key={key}
                                onClick={() => setEditingCell(`h-${key}`)}
                                title="单击编辑"
                              >
                                <span className="extract-header-label">{labelFor(key)}</span>
                                <EditableText
                                  className="extract-header-value"
                                  editing={editingCell === `h-${key}`}
                                  value={headerValueOf(key)}
                                  onCommit={(raw) => updateHeaderDraft(key, raw)}
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
                                {((extraction.result as { items?: unknown[] }).items ?? []).length + " 行"}
                              </span>
                            </div>
                            <div className="data-table-scroll">
                              <table className="data-table" style={{ tableLayout: "fixed" }}>
                                <thead>
                                  <tr>
                                    {extractionItemKeys.map((key) => (
                                      <th
                                        key={key}
                                        style={{ width: `${detailWidths[key] ?? 132}px` }}
                                      >
                                        <span>{labelFor(key)}</span>
                                        <span
                                          aria-label={`调整“${labelFor(key)}”列宽`}
                                          aria-orientation="vertical"
                                          className="column-resizer"
                                          onPointerDown={(event) =>
                                            startDetailResize(event, key, detailWidths[key] ?? 132)
                                          }
                                          role="separator"
                                          tabIndex={0}
                                        />
                                      </th>
                                    ))}
                                  </tr>
                                </thead>
                                <tbody>
                                  {displayItems.map((item, idx) => (
                                    <tr key={idx}>
                                      {extractionItemKeys.map((key) => (
                                        <td
                                          aria-label={`编辑 ${labelFor(key)}，当前值 ${String(item[key] ?? "") || "空"}`}
                                          key={key}
                                          onClick={() => setEditingCell(`i-${idx}-${key}`)}
                                          onKeyDown={(event) => {
                                            if (event.target !== event.currentTarget) return;
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
                                          />
                                        </td>
                                      ))}
                                    </tr>
                                  ))}
                                  {displayItems.length === 0 ? (
                                    <tr><td colSpan={extractionItemKeys.length} className="hist-empty">无明细</td></tr>
                                  ) : null}
                                </tbody>
                              </table>
                            </div>
                          </div>
                        ) : null}
                      </div>

                      {/* 校验结果（复用提取页样式） */}
                      <div className="validation-panel">
                        <div className="validation-panel-head">
                          <Icon
                            icon={ShieldAlert}
                            size={15}
                            style={{ color: "var(--color-danger-600)" }}
                          />
                          <span>校验结果</span>
                          <span
                            className={`validation-count ${
                              extraction.validation_issues.length === 0 ? "zero" : ""
                            }`}
                          >
                            {extraction.validation_issues.length}
                          </span>
                          {extraction.validation_issues.length === 0 && (
                            <span className="validation-hint">全部通过，无异常</span>
                          )}
                        </div>
                        <div className="validation-panel-body">
                          {extraction.validation_issues.length === 0 ? (
                            <div className="validation-empty">
                              <Icon
                                icon={CheckCircle2}
                                size={18}
                                style={{ color: "var(--color-success-600)" }}
                              />
                              <span>本次提取校验全部通过</span>
                            </div>
                          ) : (
                            extraction.validation_issues.map((issue, idx) => (
                              <div
                                className={`validation-item${ignoredIssueIndices.includes(idx) ? " is-ignored" : ""}`}
                                key={idx}
                              >
                                <span className="validation-row-tag">
                                  {issue.severity === "error" ? "阻断" : "提醒"}
                                </span>
                                <span className="validation-field">
                                  {labelFor(issue.field) || issue.field}
                                </span>
                                <span className="validation-msg">{issue.message}</span>
                                {ignoredIssueIndices.includes(idx) && (
                                  <span className="validation-ignored-tag">已忽略</span>
                                )}
                                <button
                                  className="btn ghost sm"
                                  title={ignoredIssueIndices.includes(idx) ? "恢复这条校验" : "忽略这条校验，保存到表时不再阻断"}
                                  onClick={() => toggleIssueIgnored(idx)}
                                  type="button"
                                >
                                  <Icon icon={EyeOff} size={13} />
                                  {ignoredIssueIndices.includes(idx) ? "恢复" : "忽略"}
                                </button>
                              </div>
                            ))
                          )}
                        </div>
                      </div>
                    </div>
                  ) : null}

                  {/* 原文件 */}
                  <div className="history-original-panel">
                    <div className="history-panel-title">原文件</div>
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
                  </div>
                </div>
              )}
      </div>

      {/* 删除确认：删除记录不删表数据，需输入文件名确认 */}
      <ConfirmDialog
        open={deleteTarget !== null}
        title="删除文件记录"
        description={`将删除「${deleteTarget?.filename ?? ""}」的处理记录、提取结果和原文件。数据表中已确认的数据不会删除，会保留在表里，只是不再追溯到这个文件。此操作不可恢复。`}
        confirmText={deleteTarget?.filename ?? ""}
        buttonLabel="确认删除"
        busy={busyTaskId === deleteTarget?.id}
        onConfirm={() => void handleDeleteTask()}
        onClose={() => setDeleteTarget(null)}
      />
    </div>
  );
}
