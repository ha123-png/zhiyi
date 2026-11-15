import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { FileText, Loader2, Search, Upload } from "lucide-react";
import { cancelTask, deleteTask, deleteTasksBatch, getTasks, getTaskSummary, getTemplates, pauseTask, retryTask, retryTasksBatch, resumeTask } from "../api";
import type { ExtractionTemplate, Task, TaskGroup, TaskSummary, TaskTimeWindow } from "../types";
import { useTaskOverview, type TaskOverview } from "../useTaskOverview";
import { useListScroll, type ListScrollMemory } from "../useListScroll";
import { batchRetryNotice, TASK_GROUPS, taskCanDelete, taskCanRetry, taskGroupCounts, taskNeedsExportAction } from "../taskPresentation";
import { deletionNotice } from "../deletionNotice";
import { DeleteTaskDialog } from "./DeleteTaskDialog";
import { Disclosure } from "./Disclosure";
import { Icon } from "./Icon";
import { NoticeDialog } from "./NoticeDialog";
import { Toast, useToast } from "./Toast";
import { TaskFailureDetails } from "./TaskFailureDetails";
import { TaskRecoveryDialog, type TaskRecoveryKind } from "./TaskRecoveryDialog";
import { DuplicateTaskRow, TaskListRow } from "./TaskListRow";
import type { PendingDuplicate } from "./PendingDuplicateDialog";
import "./task-workspace.css";

const PAGE_SIZE = 50;
const NO_DUPLICATES: PendingDuplicate[] = [];
export interface TaskWorkspaceViewState {
  group: TaskGroup;
  searchInput: string;
  page: number;
  timeWindow?: TaskTimeWindow;
}
interface TaskQueuePageProps {
  logs?: { time: string; message: string; taskId?: string }[];
  overview?: TaskOverview;
  onOpenTask?: (task: Task) => void;
  onOpenData?: (task: Task) => void;
  onNavigateExtract?: () => void;
  viewState?: TaskWorkspaceViewState;
  scrollMemory?: ListScrollMemory;
  onViewStateChange?: (state: TaskWorkspaceViewState) => void;
  entryRevision?: number;
  entryGroup?: TaskGroup;
  duplicates?: PendingDuplicate[];
  onOpenDuplicate?: (item: PendingDuplicate) => void;
  onDeleteDuplicates?: (items: readonly PendingDuplicate[]) => void;
  beforeDeleteDuplicates?: (items: readonly PendingDuplicate[]) => Promise<void>;
}
export function TaskQueuePage({ logs = [], overview: suppliedOverview, onOpenTask, onOpenData, onNavigateExtract, viewState, scrollMemory, onViewStateChange, entryRevision = 0, entryGroup = "pending", duplicates = NO_DUPLICATES, onOpenDuplicate, onDeleteDuplicates, beforeDeleteDuplicates }: TaskQueuePageProps) {
  const fallback = useTaskOverview(!suppliedOverview);
  const overview = suppliedOverview ?? fallback;
  const [group, setGroup] = useState<TaskGroup>(viewState?.group ?? "all");
  const [searchInput, setSearchInput] = useState(viewState?.searchInput ?? "");
  const [search, setSearch] = useState(viewState?.searchInput.trim() ?? "");
  const [page, setPage] = useState(viewState?.page ?? 0);
  const [timeWindow, setTimeWindow] = useState<TaskTimeWindow>(viewState?.timeWindow ?? "week");
  const [tasks, setTasks] = useState<Task[]>([]);
  const [listSummary, setListSummary] = useState<TaskSummary | null>(null);
  const [summaryScope, setSummaryScope] = useState<string | null>(null);
  const [readError, setReadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadedQuery, setLoadedQuery] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [deleteDuplicateTargets, setDeleteDuplicateTargets] = useState<PendingDuplicate[]>([]);
  const [busy, setBusy] = useState(false);
  const mutation = useRef(false);
  const localRefresh = useRef(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const { toast, notify, clear } = useToast();
  const [readNoticeOpen, setReadNoticeOpen] = useState(false);
  const [dialog, setDialog] = useState<{ task: Task; kind: TaskRecoveryKind } | null>(null);
  const [deleteTargets, setDeleteTargets] = useState<Task[]>([]);
  const [templates, setTemplates] = useState<ExtractionTemplate[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(true);
  const [templatesError, setTemplatesError] = useState<string | null>(null);
  const request = useRef(0);
  const activeListRequest = useRef<number | null>(null);
  const deferredOverviewRead = useRef(false);
  const latestLoad = useRef<(() => Promise<void>) | null>(null);
  const catalogRequest = useRef(0);
  const listRoot = useRef<HTMLDivElement>(null);
  const focusedRemoval = useRef<number | null>(null);
  const currentTasks = useRef(tasks);
  const currentDialog = useRef(dialog);
  const currentDelete = useRef(deleteTargets);
  currentTasks.current = tasks;
  currentDialog.current = dialog;
  currentDelete.current = deleteTargets;
  const queryKey = `${group}:${page}:${search}:${timeWindow}`;
  const freshPage = loadedQuery === queryKey;
  const localScrollMemory = useRef<ListScrollMemory>({});
  const { viewport, rememberScroll } = useListScroll(scrollMemory ?? localScrollMemory.current, "tasks", freshPage ? queryKey : null);
  const visibleSummary = summaryScope === `${timeWindow}:${search}` ? listSummary : null;
  const counts = taskGroupCounts(visibleSummary, []);
  const matchedDuplicates = duplicates.filter(item => item.name.toLowerCase().includes(search.toLowerCase()));
  const pageDuplicates = page === 0 && (group === "all" || group === "pending") ? matchedDuplicates : NO_DUPLICATES;
  const countKnown = visibleSummary !== null;
  const total = counts[group];
  const totalWithDuplicates = total + (group === "all" || group === "pending" ? matchedDuplicates.length : 0);
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const blocked = busy || loading || !freshPage;
  const previousEntry = useRef(entryRevision);

  useLayoutEffect(() => {
    const scroll = viewport.current;
    const heading = scroll?.querySelector("thead");
    if (!scroll || !heading) return;
    function alignRows() {
      if (!scroll || !heading) return;
      const baseHeight = Number.parseFloat(getComputedStyle(scroll).getPropertyValue("--task-base-row-height"));
      if (!baseHeight) return;
      const available = scroll.clientHeight - heading.offsetHeight;
      const visibleRows = Math.max(1, Math.floor(available / baseHeight));
      scroll.style.setProperty("--task-row-height", `${Math.max(baseHeight, available / visibleRows)}px`);
    }
    const observer = new ResizeObserver(alignRows);
    observer.observe(scroll);
    observer.observe(heading);
    alignRows();
    return () => observer.disconnect();
  }, []);
  const entryChanged = previousEntry.current !== entryRevision;
  useLayoutEffect(() => {
    if (!entryChanged) return;
    previousEntry.current = entryRevision;
    setGroup(entryGroup); setSearchInput(""); setSearch(""); setPage(0); setSelected(new Set());
  }, [entryChanged, entryRevision, entryGroup]);
  useEffect(() => {
    // The render receiving the shortcut still has the previous local query.
    // Persist only after the shortcut has updated those local values.
    if (!entryChanged) onViewStateChange?.({
      group, searchInput, page: searchInput.trim() === search ? page : 0, timeWindow,
    });
  }, [group, searchInput, search, page, timeWindow, onViewStateChange, entryChanged]);

  useEffect(() => {
    if (searchInput.trim() === search) return;
    const timer = window.setTimeout(() => { setSearch(searchInput.trim()); setPage(0); setSelected(new Set()); }, 250);
    return () => clearTimeout(timer);
  }, [searchInput, search]);
  function flushOverviewRead() {
    if (!deferredOverviewRead.current || mutation.current || localRefresh.current || activeListRequest.current !== null) return;
    deferredOverviewRead.current = false;
    void latestLoad.current?.();
  }
  const load = useCallback(async () => {
    const generation = ++request.current;
    activeListRequest.current = generation;
    deferredOverviewRead.current = false;
    setLoading(true);
    try {
      // One rolling UTC boundary per read, shared by the list and its SQL counts.
      // The history page's `since` has a different meaning: it filters every state.
      const terminalSince = timeWindow === "all" ? undefined
        : new Date(Date.now() - (timeWindow === "day" ? 1 : 7) * 86400000).toISOString();
      const scope = { search: search || undefined, terminalSince };
      const [items, summary] = await Promise.all([
        getTasks({ limit: PAGE_SIZE, offset: page * PAGE_SIZE, group, ...scope }),
        getTaskSummary(scope),
      ]);
      if (generation !== request.current) return;
      const matching = items;
      const focusedRow = document.activeElement instanceof HTMLElement ? document.activeElement.closest<HTMLElement>("[data-task-id]") : null;
      const focusedId = focusedRow?.dataset.taskId ?? currentDialog.current?.task.id ?? currentDelete.current[0]?.id;
      if (focusedId && !matching.some(task => task.id === focusedId)) {
        focusedRemoval.current = Math.max(0, currentTasks.current.findIndex(task => task.id === focusedId));
      }
      setTasks(matching); setListSummary(summary); setSummaryScope(`${timeWindow}:${search}`);
      setLoadedQuery(`${group}:${page}:${search}:${timeWindow}`); setReadError(null);
      setSelected(previous => new Set([...previous].filter(id => id.startsWith("duplicate:") || matching.some(task => task.id === id))));
    } catch (reason) { if (generation === request.current) setReadError(reason instanceof Error ? reason.message : "任务读取失败"); }
    finally {
      if (activeListRequest.current === generation) activeListRequest.current = null;
      if (generation === request.current) setLoading(false);
      flushOverviewRead();
    }
  }, [group, page, search, timeWindow]);
  latestLoad.current = load;
  useEffect(() => {
    void load();
    return () => { request.current += 1; activeListRequest.current = null; deferredOverviewRead.current = false; };
  }, [load]);
  useLayoutEffect(() => {
    if (focusedRemoval.current === null || dialog || deleteTargets.length || deleteDuplicateTargets.length || loading) return;
    const buttons = listRoot.current?.querySelectorAll<HTMLButtonElement>(".task-primary-action");
    const preferred = buttons?.length ? buttons[Math.min(focusedRemoval.current, buttons.length - 1)] : undefined;
    const target = preferred && !preferred.disabled ? preferred
      : listRoot.current?.querySelector<HTMLButtonElement>(".task-filter[aria-pressed=true]");
    if (target && !target.disabled) { target.focus({ preventScroll: true }); focusedRemoval.current = null; }
  }, [tasks, dialog, deleteTargets, deleteDuplicateTargets, loading]);
  const previousRevision = useRef(overview.revision);
  useEffect(() => {
    if (previousRevision.current === overview.revision) return;
    previousRevision.current = overview.revision;
    deferredOverviewRead.current = true;
    flushOverviewRead();
  }, [overview.revision, load]);
  useEffect(() => { if (!loading && freshPage && page >= pageCount && page > 0 && !readError) setPage(pageCount - 1); }, [freshPage, loading, page, pageCount, readError]);
  const loadTemplates = useCallback(async () => {
    const generation = ++catalogRequest.current; setTemplatesLoading(true); setTemplatesError(null);
    try { const items = await getTemplates(true); if (generation === catalogRequest.current) setTemplates(items); }
    catch (reason) { if (generation === catalogRequest.current) setTemplatesError(reason instanceof Error ? reason.message : "模板列表读取失败"); }
    finally { if (generation === catalogRequest.current) setTemplatesLoading(false); }
  }, []);
  useEffect(() => { void loadTemplates(); return () => { catalogRequest.current += 1; }; }, [loadTemplates]);
  async function refresh() {
    localRefresh.current = true;
    try { await overview.refresh(); await load(); }
    finally { localRefresh.current = false; flushOverviewRead(); }
  }
  async function mutate(operation: () => Promise<void>) {
    if (mutation.current) return; mutation.current = true; setBusy(true); setActionError(null); clear();
    try { await operation(); await refresh(); }
    catch (reason) {
      const message = reason instanceof Error ? reason.message : "操作失败，请重试。";
      if (deleteTargets.length || deleteDuplicateTargets.length) setActionError(message);
      else notify(message, "error");
    }
    finally { mutation.current = false; setBusy(false); flushOverviewRead(); }
  }
  function openDialog(kind: TaskRecoveryKind, task: Task) { setActionError(null); setDialog({ kind, task }); }
  function openDelete(items: Task[], pending: PendingDuplicate[] = []) {
    setActionError(null); setDeleteTargets(items); setDeleteDuplicateTargets(pending);
  }
  function toggleSelection(id: string) {
    setSelected(previous => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }
  const selectedTasks = tasks.filter(task => selected.has(task.id));
  const selectedDuplicates = pageDuplicates.filter(item => selected.has(`duplicate:${item.id}`));
  const selectedCount = selectedTasks.length + selectedDuplicates.length;
  const selectableCount = tasks.length + pageDuplicates.length;
  const allPageSelected = selectableCount > 0 && selectedCount === selectableCount;
  const selectedRetryable = selectedTasks.filter(taskCanRetry);
  const selectedDeletable = selectedTasks.filter(taskCanDelete);
  function primary(task: Task): { label: string; action: () => void; disabled?: boolean } | undefined {
    if (["queued", "processing"].includes(task.status)) return { label: "暂停", action: () => void mutate(async () => { await pauseTask(task.id); }) };
    if (task.status === "paused") return { label: "继续", disabled: overview.queuePaused !== false, action: () => void mutate(async () => { await resumeTask(task.id); }) };
    if (taskCanRetry(task)) return { label: "重试", action: () => void mutate(async () => { await retryTask(task.id); }) };
    if (taskNeedsExportAction(task)) return { label: "处理", action: () => openDialog("export", task) };
    if (task.status === "waiting_for_template") {
      if (task.pending_action === "confirm_input_scope") return { label: "确认范围", action: () => openDialog("scope", task) };
      if (task.pending_action === "select_template") return { label: "选模板", action: () => openDialog("template", task) };
      if (task.pending_action === "confirm_target_table") return { label: "选数据表", action: () => openDialog("template", task) };
    }
    if (task.has_result && onOpenTask) return { label: task.status === "needs_review" ? "核对" : "对照", action: () => onOpenTask(task) };
  }

  return (
    <div className="view task-workspace" ref={listRoot}>
      <Toast toast={toast} onDismiss={clear} />
      {readNoticeOpen && (readError || overview.error) && <NoticeDialog title="任务读取失败" onClose={() => setReadNoticeOpen(false)}
        actions={<button type="button" className="btn primary sm" disabled={loading} onClick={() => void refresh()}>重新读取</button>}>
        <p>{loadedQuery ? "列表保留上次读取的内容。" : "尚未读取到任务数据。"}</p>
        <p>{readError || overview.error}</p>
      </NoticeDialog>}
      <div className="task-workspace-toolbar">
        {(readError || overview.error) && <button className="btn ghost sm" type="button" aria-haspopup="dialog"
          onClick={() => setReadNoticeOpen(true)}>任务读取失败</button>}
        <label className="task-workspace-period">
          <span>显示范围</span>
          <select className="form-select" value={timeWindow} onChange={event => {
            setTimeWindow(event.target.value as TaskTimeWindow); setPage(0); setSelected(new Set());
          }}>
            <option value="day">最近一天</option>
            <option value="week">最近 7 天</option>
            <option value="all">全部记录</option>
          </select>
        </label>
        <div className="task-workspace-filters" role="group" aria-label="任务状态筛选">
          {TASK_GROUPS.map(item => (
            <button type="button" key={item.id} className="task-filter" aria-pressed={item.id === group}
              onClick={() => { setGroup(item.id); setPage(0); setSelected(new Set()); }}>
              <span>{item.label}</span>
                  <span className="task-filter-count">{countKnown ? counts[item.id] + (item.id === "all" || item.id === "pending" ? matchedDuplicates.length : 0) : "—"}</span>
            </button>
          ))}
        </div>
        <label className="task-workspace-search">
          <Icon icon={Search} size={16} />
          <input value={searchInput} onChange={event => setSearchInput(event.target.value)} placeholder="搜索文件名" aria-label="搜索任务文件名" />
        </label>
      </div>
      <div className="task-workspace-list" aria-busy={loading}>
        <div className="task-workspace-scroll" ref={viewport} onScroll={rememberScroll} role="region" aria-label="任务列表" tabIndex={0}>
          <table className="task-workspace-table">
            <colgroup>
              <col className="task-select-col" /><col /><col className="task-state-col" />
              <col className="task-template-col" /><col className="task-time-col" /><col className="task-actions-col" />
            </colgroup>
            <thead>
              <tr>
                <th><input type="checkbox" aria-label="选择本页任务" disabled={blocked || !selectableCount}
                  checked={allPageSelected}
                  ref={input => { if (input) input.indeterminate = selectedCount > 0 && !allPageSelected; }}
                  onChange={event => setSelected(event.target.checked
                    ? new Set([...tasks.map(task => task.id), ...pageDuplicates.map(item => `duplicate:${item.id}`)]) : new Set())} /></th>
                <th scope="col" aria-label="文件">
                  <span className="task-file-column-label" aria-live="polite">{selectedCount > 0 ? `本页已选 ${selectedCount} 个文件` : "文件"}</span>
                </th>
                <th>状态</th><th>模板</th><th>更新时间</th>
                <th scope="col" aria-label="操作">
                  {selectedRetryable.length > 0 || selectedDeletable.length > 0 || selectedDuplicates.length > 0 ? (
                    <div className="task-batch-actions" role="group" aria-label="选中文件的操作">
                      {selectedRetryable.length > 0 && <button type="button" className="btn secondary xs" disabled={blocked}
                        onClick={() => void mutate(async () => {
                          const result = await retryTasksBatch(selectedRetryable.map(task => task.id));
                          if (result.skipped) notify(batchRetryNotice(result));
                          setSelected(new Set());
                        })}>重试</button>}
                      {(selectedDeletable.length > 0 || selectedDuplicates.length > 0) && <button type="button" className="btn ghost xs danger-btn" disabled={blocked}
                        onClick={() => openDelete(selectedDeletable, selectedDuplicates)}>删除</button>}
                    </div>
                  ) : "操作"}
                </th>
              </tr>
            </thead>
            <tbody>
              {pageDuplicates.map(item => <DuplicateTaskRow key={item.id} item={item} templates={templates} onOpen={onOpenDuplicate}
                selected={selected.has(`duplicate:${item.id}`)} disabled={blocked}
                onSelect={() => toggleSelection(`duplicate:${item.id}`)} onDelete={() => openDelete([], [item])} />)}
              {tasks.map(task => (
                <TaskListRow key={task.id} task={task} templates={templates} selected={selected.has(task.id)}
                  disabled={blocked} primary={primary(task)}
                  onSelect={() => toggleSelection(task.id)}
                  actions={{
                    open: onOpenTask && task.has_result ? () => onOpenTask(task) : undefined,
                    data: onOpenData ? () => onOpenData(task) : undefined,
                    cancel: () => void mutate(async () => {
                      await cancelTask(task.id);
                    }),
                    delete: () => openDelete([task]),
                    retry: () => openDialog("retry", task),
                    details: () => openDialog("details", task),
                  }} />
              ))}
            </tbody>
          </table>
          {!tasks.length && !pageDuplicates.length && (
          <div className="task-workspace-empty">
            {loading ? (
              <><Icon icon={Loader2} size={20} className="spin" /><span>正在读取任务…</span></>
            ) : readError || overview.state !== "ready" ? (
              <span>任务状态尚未确认，请重新读取。</span>
            ) : (
              <>
                <Icon icon={FileText} size={24} />
                <span>{search ? "没有匹配的任务" : group === "all" ? timeWindow === "all" ? "还没有任务" : "此范围内暂无任务" : `没有${TASK_GROUPS.find(item => item.id === group)?.label}任务`}</span>
                {group === "all" && !search && overview.summary?.total === 0 && onNavigateExtract && (
                  <button type="button" className="btn secondary sm" onClick={onNavigateExtract}><Icon icon={Upload} size={15} />开始提取</button>
                )}
              </>
            )}
          </div>
          )}
        </div>
        <div className="task-workspace-footer">
          <span>{loading && !freshPage ? "正在读取目标页…" : countKnown ? `共 ${totalWithDuplicates} 个文件` : "数量待确认"}</span>
          <div>
            <button type="button" className="btn ghost sm" disabled={loading || page === 0}
              onClick={() => { setPage(value => value - 1); setSelected(new Set()); }}>上一页</button>
            <span>{countKnown ? `${page + 1} / ${pageCount}` : "页码待确认"}</span>
            <button type="button" className="btn ghost sm" disabled={loading || !countKnown || page + 1 >= pageCount}
              onClick={() => { setPage(value => value + 1); setSelected(new Set()); }}>下一页</button>
          </div>
        </div>
      </div>
      <Disclosure className="task-workspace-log" title="处理日志">
        <div className="task-log-list">
          {logs.length === 0 ? <p className="support">还没有处理记录，上传文件后这里会显示处理过程。</p> : logs.map((log, index) => (
            <div className="task-log-entry" key={index}>
              <time>{log.time}</time>
              <div>{log.message}{log.taskId && <TaskFailureDetails key={log.taskId} taskId={log.taskId} />}</div>
            </div>
          ))}
        </div>
      </Disclosure>
      {dialog && (
        <TaskRecoveryDialog key={`${dialog.task.id}:${dialog.kind}`} task={dialog.task} kind={dialog.kind}
          templates={templates} templatesLoading={templatesLoading} templatesError={templatesError}
          reloadTemplates={() => void loadTemplates()} onUpdated={refresh} onClose={() => setDialog(null)} />
      )}
      <DeleteTaskDialog tasks={deleteTargets} duplicates={deleteDuplicateTargets}
        busy={busy} error={actionError} onClose={() => { setDeleteTargets([]); setDeleteDuplicateTargets([]); }}
        onConfirm={() => void mutate(async () => {
          const ids = deleteTargets.map(task => task.id);
          await beforeDeleteDuplicates?.(deleteDuplicateTargets);
          const result = ids.length === 1 && !deleteDuplicateTargets.length
            ? { ...await deleteTask(ids[0]), deleted: 1 }
            : await deleteTasksBatch(ids, deleteDuplicateTargets.map(item => item.id));
          if (deleteDuplicateTargets.length) onDeleteDuplicates?.(deleteDuplicateTargets);
          const deleted = result.deleted;
          const skipped = ids.length - deleted;
          setDeleteTargets([]);
          setDeleteDuplicateTargets([]);
          setSelected(new Set());
          const cleared = deleteDuplicateTargets.length ? `已移除 ${deleteDuplicateTargets.length} 条重复待确认。` : "";
          const message = (ids.length ? `已删除 ${deleted} 个文件及关联数据。${skipped ? `${skipped} 个未删除。` : ""}` : "") + cleared;
          notify(deletionNotice(result, message), skipped ? "info" : "success");
        })} />
    </div>
  );
}
