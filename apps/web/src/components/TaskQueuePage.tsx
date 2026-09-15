import { taskDisplayName } from "../taskNames";
import { TaskFailureDetails } from "./TaskFailureDetails";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  HelpCircle,
  Loader2,
  Play,
  Trash2,
} from "lucide-react";
import {
  deleteTask,
  deleteTasksBatch,
  getTasks,
  getTaskSummary,
  getTables,
  getTemplates,
  retryTask,
  retryTasksBatch,
  selectTaskTemplate,
  subscribeTaskEvents,
  taskEventsConnected,
} from "../api";
import type { DataTableRead, ExtractionTemplate, Task, TaskStatus } from "../types";
import { parseServerTime } from "../time";
import { ConfirmDialog } from "./ConfirmDialog";
import { Icon } from "./Icon";
import { PartialInputAction } from "./InputScopeDetails";
import { TaskExportAction } from "./TaskExportDetails";

// 状态 → 中文文案（对齐原型队列语义：等待中/处理中/已暂停/已完成/失败）
const STATUS_TEXT: Record<TaskStatus, string> = {
  created: "等待中",
  waiting_for_template: "待处理事项",
  queued: "等待中",
  processing: "处理中",
  validating: "校验中",
  needs_review: "待确认",
  completed: "已完成",
  paused: "已暂停",
  cancelled: "已取消",
  failed: "失败",
};

// 状态 → 阶段描述
const STAGE_TEXT: Record<TaskStatus, string> = {
  created: "排队中，尚未开始",
  waiting_for_template: "需要选择模板",
  queued: "排队中，尚未开始",
  processing: "识别中…",
  validating: "校验中…",
  needs_review: "等待确认",
  completed: "已完成",
  paused: "已暂停，可在队列中恢复全部",
  cancelled: "已取消",
  failed: "失败",
};

function formatTime(iso: string): string {
  const now = Date.now();
  const then = parseServerTime(iso);
  const diff = Math.max(0, now - then);
  if (diff < 60_000) return "刚刚";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
  return `${Math.floor(diff / 86_400_000)} 天前`;
}

function templateLabel(t: Task, templateNames: Record<string, string>): string {
  if (t.template_mode === "smart") return "智能匹配";
  if (t.template_id && templateNames[t.template_id]) return templateNames[t.template_id];
  if (t.template_id) return "内置模板";
  if (t.candidate_templates.length > 0) return t.candidate_templates[0].name;
  return t.template_mode;
}

interface TaskQueuePageProps {
  /** 全局处理日志（App 层由 SSE 事件生成，覆盖所有状态变化，页面切换不丢失）。 */
  logs?: { time: string; message: string; taskId?: string }[];
  onOpenTask?: (task: Task) => void;
  onOpenData?: (task: Task) => void;
}

export function TaskQueuePage({
  logs = [],
  onOpenTask,
  onOpenData,
}: TaskQueuePageProps) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [selectedTemplates, setSelectedTemplates] = useState<Record<string, string>>({});
  // 模板 id → 中文名（隐藏 builtin-invoice 等英文 key）
  const [templates, setTemplates] = useState<ExtractionTemplate[]>([]);
  const [templateNames, setTemplateNames] = useState<Record<string, string>>({});
  const [tables, setTables] = useState<DataTableRead[]>([]);
  // 待选模板删除确认（单删）：无数据、不输入文件名
  const [deleteMatchTarget, setDeleteMatchTarget] = useState<Task | null>(null);
  // 待选模板批量删除确认：一次弹窗
  const [batchDeleteMatchOpen, setBatchDeleteMatchOpen] = useState(false);
  // 暂停任务批量删除确认：一次弹窗（防止误删整个队列）
  const [batchDeletePausedOpen, setBatchDeletePausedOpen] = useState(false);
  const [busyTaskId, setBusyTaskId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [retryingAll, setRetryingAll] = useState(false);
  const [exportPage, setExportPage] = useState(0);
  const [waitingPage, setWaitingPage] = useState(0);
  const [exportPendingTasks, setExportPendingTasks] = useState<Task[]>([]);
  const [matchFailedTasks, setMatchFailedTasks] = useState<Task[]>([]);
  const [pendingTotals, setPendingTotals] = useState({ exports: 0, waiting: 0, review: 0, failed: 0 });
  const [reviewTasks, setReviewTasks] = useState<Task[]>([]);
  const [failedTasks, setFailedTasks] = useState<Task[]>([]);
  const [reviewPage, setReviewPage] = useState(0);
  const [failedPage, setFailedPage] = useState(0);
  const requestNumber = useRef(0);
  const pendingPageSize = 50;

  const load = useCallback(async () => {
    const currentRequest = ++requestNumber.current;
    try {
      setError(null);
      // 活动任务必须按状态查询，不能假设它们一定处于最近 N 条；否则大量新历史
      // 会把旧的待选模板/暂停任务挤出列表。近期终态仅用于本页摘要。
      const [active, recent, exports, waiting, summary, reviews, failures] = await Promise.all([
        getTasks({ limit: 1000, activeOnly: true }),
        getTasks({ limit: 500 }),
        getTasks({ limit: pendingPageSize, offset: exportPage * pendingPageSize, exportPending: true }),
        getTasks({ limit: pendingPageSize, offset: waitingPage * pendingPageSize, status: "waiting_for_template" }),
        getTaskSummary(),
        getTasks({ limit: pendingPageSize, offset: reviewPage * pendingPageSize, status: "needs_review" }),
        getTasks({ limit: pendingPageSize, offset: failedPage * pendingPageSize, status: "failed,cancelled" }),
      ]);
      if (currentRequest !== requestNumber.current) return;
      const exportItems = exports.filter((task) => task.status === "completed" && (task.archive_pending || ["failed", "needs_rebind"].includes(task.file_export?.status ?? "")));
      const waitingItems = waiting.filter((task) => task.status === "waiting_for_template");
      setExportPendingTasks(exportItems);
      setMatchFailedTasks(waitingItems);
      const reviewItems = reviews.filter(task => task.status === "needs_review");
      const failedItems = failures.filter(task => ["failed", "cancelled"].includes(task.status));
      setReviewTasks(reviewItems);
      setFailedTasks(failedItems);
      const failedTotal = (summary.failed ?? failedItems.filter(task => task.status === "failed").length) + (summary.cancelled ?? failedItems.filter(task => task.status === "cancelled").length);
      const reviewTotal = summary.needs_review ?? reviewItems.length;
      setPendingTotals({ exports: summary.pending_exports ?? exportItems.length, waiting: summary.waiting_for_action ?? waitingItems.length, review: reviewTotal, failed: failedTotal });
      setReviewPage(page => Math.min(page, Math.max(0, Math.ceil(reviewTotal / pendingPageSize) - 1)));
      setFailedPage(page => Math.min(page, Math.max(0, Math.ceil(failedTotal / pendingPageSize) - 1)));
      setExportPage((page) => Math.min(page, Math.max(0, Math.ceil((summary.pending_exports ?? exports.length) / pendingPageSize) - 1)));
      setWaitingPage((page) => Math.min(page, Math.max(0, Math.ceil((summary.waiting_for_action ?? waiting.length) / pendingPageSize) - 1)));
      const merged = new Map(recent.map((task) => [task.id, task]));
      active.forEach((task) => merged.set(task.id, task));
      exports.forEach((task) => merged.set(task.id, task));
      waiting.forEach((task) => merged.set(task.id, task));
      const all = Array.from(merged.values());
      setTasks(all);
    } catch (err) {
      if (currentRequest !== requestNumber.current) return;
      setError(err instanceof Error ? err.message : "加载任务失败");
    } finally {
      if (currentRequest === requestNumber.current) setLoading(false);
    }
  }, [exportPage, waitingPage, reviewPage, failedPage]);

  useEffect(() => {
    load();
    // SSE 实时推送：状态变化立即刷新（暂停/恢复/完成马上反映）；轮询保留为断线兜底
    const unsub =
      typeof EventSource === "undefined" ? undefined : subscribeTaskEvents(load);
    let lastRefresh = Date.now();
    const visible = () => { if (!document.hidden) { lastRefresh = Date.now(); void load(); } };
    document.addEventListener("visibilitychange", visible);
    const timer = setInterval(() => {
      if (!document.hidden && (!taskEventsConnected() || Date.now() - lastRefresh >= 30000)) {
        lastRefresh = Date.now(); void load();
      }
    }, 1500);
    return () => {
      requestNumber.current += 1;
      document.removeEventListener("visibilitychange", visible);
      clearInterval(timer);
      unsub?.();
    };
  }, [load]);

  useEffect(() => {
    void getTemplates(true)
      .then((list) => {
        const active = list.filter((template) => template.is_active !== false);
        setTemplates(active);
        setTemplateNames(Object.fromEntries(list.map((t) => [t.id, t.name])));
      })
      .catch(() => {});
    void getTables().then(setTables).catch(() => {});
  }, []);

  const toggleCard = (key: string) =>
    setCollapsed((s) => ({ ...s, [key]: !s[key] }));
  const isCardCollapsed = (key: string) => !!collapsed[key];

  const handleRetry = async (taskId: string, useCurrentSettings = false) => {
    try {
      await retryTask(taskId, useCurrentSettings);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "重试任务失败");
    }
  };

  const handleSelectTemplate = async (taskId: string) => {
    const templateId = selectedTemplates[taskId];
    if (!templateId) return;
    try {
      await selectTaskTemplate(taskId, templateId);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "选择模板失败");
    }
  };

  const handleSelectTable = async (taskId: string) => {
    const tableId = selectedTemplates[taskId];
    if (!tableId) return;
    try {
      await selectTaskTemplate(taskId, undefined, tableId);
      await load();
      setNotice("已使用保留的提取结果写入指定表，任务已完成。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "选择数据表失败");
    }
  };

  const handleDelete = async () => {
    if (!deleteMatchTarget) return;
    setBusyTaskId(deleteMatchTarget.id);
    try {
      const result = await deleteTask(deleteMatchTarget.id);
      setDeleteMatchTarget(null);
      await load();
      setNotice(
        result.kept_rows > 0
          ? `任务已删除，数据表中保留 ${result.kept_rows} 行数据（与原文件追溯已断开）。`
          : "任务已删除。",
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除任务失败");
    } finally {
      setBusyTaskId(null);
    }
  };

  /** 失败任务 / 暂停任务：没有产生数据，删除无需确认，直接删 */
  const handleDeleteImmediate = async (taskId: string) => {
    setBusyTaskId(taskId);
    try {
      await deleteTask(taskId);
      await load();
      setNotice("任务已删除。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除任务失败");
    } finally {
      setBusyTaskId(null);
    }
  };

  /** 待选模板批量删除：只弹一次确认，一次请求完成 */
  const handleBatchDeleteMatch = async () => {
    const ids = matchFailedTasks.map((t) => t.id);
    if (ids.length === 0) return;
    setBusyTaskId("batch");
    try {
      const result = await deleteTasksBatch(ids);
      setBatchDeleteMatchOpen(false);
      await load();
      setNotice(
        result.kept_rows > 0
          ? `已删除 ${result.deleted} 个任务，数据表中保留 ${result.kept_rows} 行数据。`
          : `已删除 ${result.deleted} 个任务。`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "批量删除失败");
    } finally {
      setBusyTaskId(null);
    }
  };

  /** 暂停任务批量删除：经确认弹窗后执行 */
  const handleBatchDeletePaused = async () => {
    const ids = activeTasks
      .filter((t) => t.status === "paused")
      .map((t) => t.id);
    if (ids.length === 0) return;
    setBusyTaskId("batch");
    try {
      await deleteTasksBatch(ids);
      setBatchDeletePausedOpen(false);
      await load();
      setNotice(`已删除 ${ids.length} 个暂停任务。`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "批量删除失败");
    } finally {
      setBusyTaskId(null);
    }
  };

  /** 失败任务批量删除：没有产生数据，直接删，不弹确认 */
  const handleBatchDeleteFailed = async () => {
    const ids = failedTasks
      .filter((t) => t.status === "failed" || t.status === "cancelled")
      .map((t) => t.id);
    if (ids.length === 0) return;
    setBusyTaskId("batch");
    try {
      await deleteTasksBatch(ids);
      await load();
      setNotice(`已删除 ${ids.length} 个失败任务。`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "批量删除失败");
    } finally {
      setBusyTaskId(null);
    }
  };

  const handleBatchRetry = async () => {
    const ids = failedTasks
      .filter((t) => t.status === "failed")
      .map((t) => t.id);
    if (ids.length === 0) return;
    setRetryingAll(true);
    try {
      const result = await retryTasksBatch(ids);
      await load();
      setNotice(
        result.skipped > 0
          ? `已重新提交 ${result.retried.length} 个任务，${result.skipped} 个因队列容量跳过。`
          : `已重新提交 ${result.retried.length} 个任务。`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "批量重试失败");
    } finally {
      setRetryingAll(false);
    }
  };

  // 活动任务：processing / queued / created / paused / validating
  const activeTasks = tasks.filter((t) =>
    ["processing", "queued", "created", "paused", "validating"].includes(t.status),
  );
  // 待选模板
  // 最近完成：今天的才叫“最近”（completed / needs_review）
  const todayStart = new Date();
  todayStart.setHours(0, 0, 0, 0);
  const completedTasks = tasks.filter(
    (t) =>
      t.status === "completed" && !t.archive_pending
      && parseServerTime(t.updated_at) >= todayStart.getTime(),
  );
  // 失败

  return (
    <div className="view">
      <div className="page-header">
        <div className="eyebrow">任务</div>
        <h1>状态监控</h1>
        <div className="support">实时查看文档处理状态、失败任务与最近完成记录。</div>
      </div>

      {error && (
        <div className="callout danger" style={{ marginBottom: 16 }}>
          {error}
        </div>
      )}
      {notice && (
        <div className="callout success" style={{ marginBottom: 16 }}>
          {notice}
        </div>
      )}

      <div className="task-queue-grid">
        <div className="task-col">
          {/* Active (活动任务：黑头 + 蓝脉冲点，放最顶) */}
          <div className="task-queue-card is-active">
            <div
              className={`task-queue-header collapsible${isCardCollapsed("active") ? " is-collapsed" : ""}`}
              onClick={() => toggleCard("active")}
            >
              <h3>
                <button type="button" className="task-header-toggle" aria-expanded={!isCardCollapsed("active")} onClick={event => { event.stopPropagation(); toggleCard("active"); }}><Icon icon={Activity} size={15} /> 活动任务</button>
              </h3>
              <div className="header-center">
                <span className="badge live-blue">{activeTasks.length}</span>
                {loading && <Icon icon={Loader2} size={14} className="spin" />}
              </div>
              <div className="header-actions">
                <Icon className="card-caret" icon={ChevronDown} size={16} />
                {activeTasks.some((t) => t.status === "paused") && (
                  <button
                    className="btn ghost sm"
                    title="批量删除全部暂停任务（防止上传错文件，确认后删除）"
                    disabled={busyTaskId !== null}
                    onClick={(e) => {
                      e.stopPropagation();
                      setBatchDeletePausedOpen(true);
                    }}
                  >
                    <Icon icon={Trash2} size={12} /> 删除暂停任务
                  </button>
                )}
              </div>
            </div>
            <div className={"collapsible-region" + (isCardCollapsed("active") ? " is-collapsed" : "")} inert={isCardCollapsed("active")} aria-hidden={isCardCollapsed("active")}>
              <div className="collapsible-inner">
                <div className="task-list">
                  {activeTasks.length === 0 && (
                    <div className="task-empty">暂无活动任务</div>
                  )}
                  {activeTasks.map((t) => (
                    <div className="task-item" key={t.id}>
                      <div className="task-row-top">
                        <span className="task-name" title={taskDisplayName(t)}>{taskDisplayName(t)}</span>
                        <span
                          className={`badge ${["processing", "validating"].includes(t.status) ? "live-blue" : "info"}`}
                        >
                          {STATUS_TEXT[t.status]}
                        </span>
                      </div>
                      <div className="task-row-bottom">
                        <div className="task-meta">
                          <span>{templateLabel(t, templateNames)}</span>
                          <span>·</span>
                          <span>{STAGE_TEXT[t.status]}</span>
                        </div>
                        <div className="task-actions">
                          {t.status === "paused" && (
                            <button
                              className="btn ghost sm danger-btn"
                              title="删除此任务（防止上传错文件，直接删除不确认）"
                              disabled={busyTaskId === t.id}
                              onClick={() => void handleDeleteImmediate(t.id)}
                            >
                              <Icon icon={Trash2} size={12} />
                            </button>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>

          {/* Match failed (待选模板：黄) —— 始终显示卡片，无数据时展示空状态占位 */}
          <div className="task-queue-card is-warning pending-actions-card">
            <div
              className={`task-queue-header collapsible${isCardCollapsed("match") ? " is-collapsed" : ""}`}
              onClick={() => toggleCard("match")}
            >
              <h3>
                <button type="button" className="task-header-toggle" aria-expanded={!isCardCollapsed("match")} onClick={event => { event.stopPropagation(); toggleCard("match"); }}><Icon icon={HelpCircle} size={15} /> 待处理事项</button>
              </h3>
              <div className="header-center">
                <span className="badge warn">{pendingTotals.waiting + pendingTotals.exports + pendingTotals.review + pendingTotals.failed}</span>
              </div>
              <div className="header-actions">
                <Icon className="card-caret" icon={ChevronDown} size={16} />
                {matchFailedTasks.length > 0 && (
                  <button
                    className="btn ghost sm"
                    title="批量删除本页待选模板或待确认范围任务，不包含副本事项"
                    disabled={busyTaskId !== null}
                    onClick={(e) => {
                      e.stopPropagation();
                      setBatchDeleteMatchOpen(true);
                    }}
                  >
                    <Icon icon={Trash2} size={12} /> 删除本页待选任务
                  </button>
                )}
              </div>
            </div>
            <div className={"collapsible-region" + (isCardCollapsed("match") ? " is-collapsed" : "")} inert={isCardCollapsed("match")} aria-hidden={isCardCollapsed("match")}>
              <div className="collapsible-inner">
                <div className="task-list">
                  {matchFailedTasks.length === 0 && exportPendingTasks.length === 0 && reviewTasks.length === 0 && pendingTotals.failed === 0 && (
                    <div className="task-empty">暂无待处理事项</div>
                  )}
                  {reviewTasks.map(task => <div className="task-item" key={`review-${task.id}`}>
                    <div className="task-row-top"><span className="task-name" title={taskDisplayName(task)}>{taskDisplayName(task)}</span><span className="badge warn">待核对</span></div>
                    <div className="task-row-bottom"><span className="small muted">提取结果需要确认，与文件历史的校验问题同步。</span><button className="btn secondary sm" onClick={() => onOpenTask?.(task)}>核对结果</button></div>
                  </div>)}
                  {pendingTotals.review > pendingPageSize && <div className="row" aria-label="待核对事项翻页">
                    <button className="btn secondary sm" disabled={reviewPage === 0} onClick={() => setReviewPage(page => page - 1)}>上一页待核对</button>
                    <span className="small muted">{reviewPage + 1}/{Math.ceil(pendingTotals.review / pendingPageSize)} 页 · 共 {pendingTotals.review} 份</span>
                    <button className="btn secondary sm" disabled={(reviewPage + 1) * pendingPageSize >= pendingTotals.review} onClick={() => setReviewPage(page => page + 1)}>下一页待核对</button>
                  </div>}
                  {pendingTotals.failed > 0 && <button className="btn secondary sm" onClick={() => { setCollapsed(previous => ({ ...previous, failed: false })); document.getElementById("queue-failed")?.scrollIntoView({ block: "start" }); }}>{pendingTotals.failed} 个失败或取消任务待处理 · 查看</button>}
                  {exportPendingTasks.map((task) => <div className="task-item" key={`export-${task.id}`}>
                    <div className="task-row-top"><span className="task-name" title={taskDisplayName(task)}>{taskDisplayName(task)}</span><span className="badge warn">{task.file_export?.mode === "move" ? "归档待处理" : "副本待处理"}</span></div>
                    <TaskExportAction task={task} onUpdated={() => void load()} />
                  </div>)}
                  {pendingTotals.exports > pendingPageSize && <div className="row" aria-label="副本事项翻页">
                    <button className="btn secondary sm" disabled={exportPage === 0} onClick={() => setExportPage((page) => page - 1)}>上一页副本</button>
                    <span className="small muted">副本事项 {exportPage + 1}/{Math.ceil(pendingTotals.exports / pendingPageSize)} 页 · 共 {pendingTotals.exports} 份</span>
                    <button className="btn secondary sm" disabled={(exportPage + 1) * pendingPageSize >= pendingTotals.exports} onClick={() => setExportPage((page) => page + 1)}>下一页副本</button>
                  </div>}
                  {pendingTotals.waiting > pendingPageSize && <div className="row" aria-label="待选事项翻页">
                    <button className="btn secondary sm" disabled={waitingPage === 0} onClick={() => setWaitingPage((page) => page - 1)}>上一页待选</button>
                    <span className="small muted">模板与范围事项 {waitingPage + 1}/{Math.ceil(pendingTotals.waiting / pendingPageSize)} 页 · 共 {pendingTotals.waiting} 份</span>
                    <button className="btn secondary sm" disabled={(waitingPage + 1) * pendingPageSize >= pendingTotals.waiting} onClick={() => setWaitingPage((page) => page + 1)}>下一页待选</button>
                  </div>}
                  {matchFailedTasks.map((t) => {
                    if (t.pending_reason === "input_scope") return <div className="task-item" key={t.id}>
                      <div className="task-row-top"><span className="task-name" title={taskDisplayName(t)}>{taskDisplayName(t)}</span><span className="badge warn">待确认范围</span></div>
                      <PartialInputAction task={t} onUpdated={() => void load()} />
                      <button className="btn ghost sm" onClick={() => onOpenTask?.(t)}>查看任务</button>
                      <button className="btn ghost sm danger-btn" onClick={() => setDeleteMatchTarget(t)}>删除任务</button>
                    </div>;
                    const choosingTable = t.candidate_templates.some((candidate) => candidate.description?.includes("指定表不符"));
                    const options = choosingTable
                      ? tables.filter((table) => {
                          const candidate = t.candidate_templates[0];
                          const template = templates.find((item) => item.id === candidate?.id);
                          if (!candidate || !template) return false;
                          const key = template.builtin_key ?? candidate.id;
                          const version = template.builtin_key ? "builtin-v1" : String(candidate.version);
                          return table.template_key === key && table.template_version === version;
                        }).map((table) => ({ id: table.id, name: table.name }))
                      : [
                          ...t.candidate_templates.map((candidate) => ({ id: candidate.id, name: `${candidate.name}（推荐）` })),
                          ...templates.filter((template) => !t.candidate_templates.some((candidate) => candidate.id === template.id)).map((template) => ({ id: template.id, name: template.name })),
                        ];
                    return (
                      <div className="task-item" key={t.id}>
                        <div className="task-row-top">
                          <span className="task-name" title={taskDisplayName(t)}>{taskDisplayName(t)}</span>
                          <span className="badge warn">待匹配</span>
                        </div>
                        <div className="match-failed-box">
                          <p>{choosingTable ? "提取出字段与指定表不符，请重新选择指定表。" : "智能匹配未命中，请手动选择模板，或重试匹配。"}</p>
                          <div className="row">
                            <select
                              aria-label={choosingTable ? "选择指定表" : "选择模板"}
                              className="form-select"
                              style={{ flex: 1 }}
                              value={selectedTemplates[t.id] ?? ""}
                              onChange={(e) =>
                                setSelectedTemplates((s) => ({
                                  ...s,
                                  [t.id]: e.target.value,
                                }))
                              }
                            >
                              <option value="" disabled>{choosingTable ? "选择兼容的数据表…" : "选择模板…"}</option>
                              {options.map((option) => (
                                <option key={option.id} value={option.id}>{option.name}</option>
                              ))}
                            </select>
                            <button
                              className="btn primary sm"
                              onClick={() => choosingTable ? handleSelectTable(t.id) : handleSelectTemplate(t.id)}
                              disabled={!selectedTemplates[t.id]}
                            >
                              确认
                            </button>
                            {!choosingTable && <button className="btn sm" onClick={() => handleRetry(t.id)}>重试</button>}
                            <button
                              className="btn ghost sm danger-btn"
                              title="删除此任务（没有产生记录，确认后删除）"
                              onClick={() => setDeleteMatchTarget(t)}
                            >
                              <Icon icon={Trash2} size={12} />
                            </button>
                          </div>
                        </div>
                      </div>
                    )})}
                  </div>
                </div>
              </div>
            </div>
          </div>
        <div className="task-col">
          {/* Completed (最近完成：绿) */}
          <div className="task-queue-card is-success">
            <div
              className={`task-queue-header collapsible${isCardCollapsed("completed") ? " is-collapsed" : ""}`}
              onClick={() => toggleCard("completed")}
            >
              <h3>
                <button type="button" className="task-header-toggle" aria-expanded={!isCardCollapsed("completed")} onClick={event => { event.stopPropagation(); toggleCard("completed"); }}><Icon icon={CheckCircle2} size={15} /> 最近完成</button>
              </h3>
              <div className="header-center">
                <span className="badge success">{completedTasks.length}</span>
              </div>
              <div className="header-actions">
                <Icon className="card-caret" icon={ChevronDown} size={16} />
              </div>
            </div>
            <div className={"collapsible-region" + (isCardCollapsed("completed") ? " is-collapsed" : "")} inert={isCardCollapsed("completed")} aria-hidden={isCardCollapsed("completed")}>
              <div className="collapsible-inner">
                <div className="task-list">
                  {completedTasks.length === 0 && (
                    <div className="task-empty">暂无已完成任务</div>
                  )}
                  {completedTasks.map((t) => (
                    <div className="task-item" key={t.id}>
                      <div className="task-row-top">
                        <span className="task-name" title={taskDisplayName(t)}>{taskDisplayName(t)}</span>
                        <span className={`badge ${t.status === "needs_review" ? "warn" : "success"}`}>
                          {STATUS_TEXT[t.status]}
                        </span>
                      </div>
                      <div className="task-row-bottom">
                        <div className="task-meta">
                          <span>{templateLabel(t, templateNames)}</span>
                          <span>·</span>
                          <span>{formatTime(t.updated_at)}</span>
                        </div>
                        <div className="task-actions">
                          <button className="btn ghost sm" onClick={() => onOpenTask?.(t)}>对照</button>
                          <button className="btn ghost sm" onClick={() => onOpenData?.(t)}>数据</button>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>

          {/* Failed (失败任务：红，第三) */}
          <div className="task-queue-card is-danger">
            <div
              className={`task-queue-header collapsible${isCardCollapsed("failed") ? " is-collapsed" : ""}`}
              onClick={() => toggleCard("failed")}
            >
              <h3>
                <button type="button" className="task-header-toggle" aria-expanded={!isCardCollapsed("failed")} onClick={event => { event.stopPropagation(); toggleCard("failed"); }}><Icon icon={AlertTriangle} size={15} /> 失败任务</button>
              </h3>
              <div className="header-center">
                <span className="badge danger" id="queue-failed">{pendingTotals.failed}</span>
              </div>
              <div className="header-actions">
                <Icon className="card-caret" icon={ChevronDown} size={16} />
                {failedTasks.some((t) => t.status === "failed") && (
                  <button
                    className="btn ghost sm"
                    title="重试本页失败任务"
                    disabled={retryingAll}
                    onClick={(e) => {
                      e.stopPropagation();
                      void handleBatchRetry();
                    }}
                  >
                    <Icon icon={retryingAll ? Loader2 : Play} size={12} className={retryingAll ? "spin" : undefined} />
                    重试本页
                  </button>
                )}
                {failedTasks.length > 0 && (
                  <button
                    className="btn ghost sm"
                    title="批量删除本页失败任务（其他页不受影响）"
                    disabled={busyTaskId !== null}
                    onClick={(e) => {
                      e.stopPropagation();
                      void handleBatchDeleteFailed();
                    }}
                  >
                    <Icon icon={Trash2} size={12} /> 删除本页失败任务
                  </button>
                )}
              </div>
            </div>
            <div className={"collapsible-region" + (isCardCollapsed("failed") ? " is-collapsed" : "")} inert={isCardCollapsed("failed")} aria-hidden={isCardCollapsed("failed")}>
              <div className="collapsible-inner">
                <div className="task-list">
                  {failedTasks.length === 0 && (
                    <div className="task-empty">暂无失败任务</div>
                  )}
                  {pendingTotals.failed > pendingPageSize && <div className="row" aria-label="失败事项翻页">
                    <button className="btn secondary sm" disabled={failedPage === 0} onClick={() => setFailedPage(page => page - 1)}>上一页失败任务</button>
                    <span className="small muted">{failedPage + 1}/{Math.ceil(pendingTotals.failed / pendingPageSize)} 页 · 共 {pendingTotals.failed} 个</span>
                    <button className="btn secondary sm" disabled={(failedPage + 1) * pendingPageSize >= pendingTotals.failed} onClick={() => setFailedPage(page => page + 1)}>下一页失败任务</button>
                  </div>}
                  {failedTasks.map((t) => (
                    <div className="task-item" key={t.id}>
                      <div className="task-row-top">
                        <span className="task-name" title={taskDisplayName(t)}>{taskDisplayName(t)}</span>
                        <span className="badge danger">{STATUS_TEXT[t.status]}</span>
                      </div>
                      <div className="task-row-bottom">
                        <div className="task-meta">
                          {t.status === "cancelled"
                            ? "任务已取消，可清除该记录"
                            : (t.failure_message ?? "处理失败，可重试")}
                          {t.status === "failed" && <TaskFailureDetails taskId={t.id} />}
                        </div>
                        <div className="task-actions">
                          {t.status === "failed" && (
                            <><button className="btn ghost sm" onClick={() => handleRetry(t.id)}>重试</button><button className="btn ghost sm" title="使用当前文件读取设置重新准备输入；模型仍沿用任务绑定的方案" onClick={() => handleRetry(t.id, true)}>按当前读取设置重试</button></>
                          )}
                          <button
                            className="btn ghost sm danger-btn"
                            title="删除任务（失败任务没有产生数据，直接删除不确认）"
                            disabled={busyTaskId === t.id}
                            onClick={() => void handleDeleteImmediate(t.id)}
                          >
                            <Icon icon={Trash2} size={12} />
                          </button>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* 处理日志：上传/开始/完成/失败等状态变化实时记录；无记录时也保留面板占位 */}
      <div className="queue-log-panel" aria-label="处理日志">
        <div className="queue-log-title">处理日志</div>
        <div className="queue-log-list">
          {logs.length === 0 ? (
            <div className="queue-log-empty">还没有处理记录，上传文件后这里会实时显示处理过程。</div>
          ) : (
            logs.map((log, index) => (
              <div className="queue-log-entry" key={index}>
                <span className="queue-log-time">{log.time}</span>
                <div className="queue-log-msg">{log.message}{log.taskId && <TaskFailureDetails taskId={log.taskId} />}</div>
              </div>
            ))
          )}
        </div>
      </div>

      {/* 待选模板单删：没有产生记录，弹确认但无需输入文件名 */}
      <ConfirmDialog
        open={deleteMatchTarget !== null}
        title="删除任务"
        description="这个任务还没有产生任何数据记录。确认删除吗？"
        buttonLabel="确认删除"
        busy={busyTaskId === deleteMatchTarget?.id}
        onConfirm={() => void handleDelete()}
        onClose={() => setDeleteMatchTarget(null)}
      />
      {/* 待选模板批量删除：一次弹窗，删除全部 */}
      <ConfirmDialog
        open={batchDeleteMatchOpen}
        title="批量删除任务"
        description={`将删除本页 ${matchFailedTasks.length} 个待选模板或待确认范围任务及知意内部原件。已确认的数据行保留但断开原件追溯；外部副本不受影响。其他页任务不会删除。确认删除吗？`}
        buttonLabel="确认删除"
        busy={busyTaskId === "batch"}
        onConfirm={() => void handleBatchDeleteMatch()}
        onClose={() => setBatchDeleteMatchOpen(false)}
      />
      {/* 暂停任务批量删除：一次弹窗，防止误删整个队列 */}
      <ConfirmDialog
        open={batchDeletePausedOpen}
        title="批量删除暂停任务"
        description={`将删除 ${activeTasks.filter((t) => t.status === "paused").length} 个暂停任务（都没有产生任何数据记录，删除后不可恢复）。确认删除吗？`}
        buttonLabel="确认删除"
        busy={busyTaskId === "batch"}
        onConfirm={() => void handleBatchDeletePaused()}
        onClose={() => setBatchDeletePausedOpen(false)}
      />
    </div>
  );
}
