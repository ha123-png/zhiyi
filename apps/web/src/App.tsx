import { lazy, Suspense, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { AssistantProvider, useAssistant } from "./assistant/AssistantProvider";
import { AssistantDrawer, AssistantPage, AssistantTrigger } from "./assistant/AssistantSurface";
import { assistantApi } from "./assistant/api";
import type { Reference } from "./assistant/types";
import type { TemplateDraft } from "./types";
import { GlobalTaskCard } from "./components/TaskActivity";
import { useTaskOverview } from "./useTaskOverview";
import type { ListScrollMemory } from "./useListScroll";
import type { HistoryListViewState } from "./components/HistoryPage";
import { PageLoadBoundary } from "./components/PageLoadBoundary";
import { PageEntrance, PageOpening } from "./components/PageTransition";
import { ServiceStatus } from "./components/ServiceStatus";
import { StatusDot } from "./components/StatusDot";
import { Onboarding, ONBOARDING_VERSION, onboardingDone } from "./components/Onboarding";
import { Sidebar } from "./components/Sidebar";
import { TaskQueuePage, type TaskWorkspaceViewState } from "./components/TaskQueuePage";
import { PendingDuplicateDialog, type PendingDuplicate } from "./components/PendingDuplicateDialog";
import { Toast, useToast } from "./components/Toast";
import {
  getConfirmation,
  deletePendingDuplicate,
  getPendingDuplicates,
  savePendingDuplicate,
  type PendingDuplicateRecord,
  getOnboardingState,
  getModelStatus,
  getSystemStatus,
  getTasks,
  pauseQueue,
  resumeQueue,
  subscribeTaskEvents,
} from "./api";
import type { TaskEvent } from "./api";
import type { DemoScenario, ModelStatus, NavigationKey, SystemStatus, Task, TaskGroup } from "./types";
import { useUpdates } from "./useUpdates";
import { taskGroupCounts } from "./taskPresentation";

const pageModules = {
  dashboard: () => import("./components/DashboardPage").then(module => ({ default: module.DashboardPage })),
  backups: () => import("./components/BackupPage").then(module => ({ default: module.BackupPage })),
  connections: () => import("./components/ConnectionsPage").then(module => ({ default: module.ConnectionsPage })),
  tables: () => import("./components/DataTablePage").then(module => ({ default: module.DataTablePage })),
  extract: () => import("./components/ExtractPage").then(module => ({ default: module.ExtractPage })),
  history: () => import("./components/HistoryPage").then(module => ({ default: module.HistoryPage })),
  guide: () => import("./components/GuidePage").then(module => ({ default: module.GuidePage })),
  settings: () => import("./components/SettingsPage").then(module => ({ default: module.SettingsPage })),
  templates: () => import("./components/TemplatesPage").then(module => ({ default: module.TemplatesPage })),
};
function preparePage(page: NavigationKey) {
  // Warm the module on navigation intent; no page is mounted or data read yet.
  if (page in pageModules) void pageModules[page as keyof typeof pageModules]().catch(() => undefined);
}
const DashboardPage = lazy(pageModules.dashboard);
const StatisticBridge = lazy(() => import("./dashboard/StatisticBridge").then(module => ({ default: module.StatisticBridge })));
const BackupPage = lazy(pageModules.backups);
const ConnectionsPage = lazy(pageModules.connections);
const DataTablePage = lazy(pageModules.tables);
const ExtractPage = lazy(pageModules.extract);
const HistoryPage = lazy(pageModules.history);
const GuidePage = lazy(pageModules.guide);
const SettingsPage = lazy(pageModules.settings);
const TemplatesPage = lazy(pageModules.templates);

function applyTheme(theme: "auto" | "light" | "dark") {
  const root = document.documentElement;
  const isDark =
    theme === "dark"
    || (theme === "auto"
      && typeof window.matchMedia === "function"
      && window.matchMedia("(prefers-color-scheme: dark)").matches);
  root.classList.toggle("dark", isDark);
}

export function App() {
  return <AssistantProvider><AppContent /></AssistantProvider>;
}

function AppContent() {
  const assistant = useAssistant();
  const [assistantTemplate, setAssistantTemplate] = useState<string | null>(null);
  const [assistantTemplateVersion, setAssistantTemplateVersion] = useState<number | null>(null);
  const [assistantDraft, setAssistantDraft] = useState<TemplateDraft | null>(null);
  const [assistantRow, setAssistantRow] = useState<number | null>(null);
  const [activePage, setActivePage] = useState<NavigationKey>("workspace");
  const [focusAssistantComposer, setFocusAssistantComposer] = useState(false);
  const [historyEntry, setHistoryEntry] = useState<HistoryListViewState["tab"]>();
  const historyViewState = useRef<HistoryListViewState | undefined>(undefined);
  const rememberHistoryView = useCallback((state: HistoryListViewState) => {
    historyViewState.current = state;
  }, []);
  const pageWrapRef = useRef<HTMLDivElement>(null);
  const pageScrollPositionsRef = useRef<Partial<Record<NavigationKey, number>>>({});
  const listScrollMemory = useRef<ListScrollMemory>({});
  // 跳转表竞态守卫：getConfirmation 期间用户切换页面时不再强行跳回数据表
  const navSeqRef = useRef(0);
  const [selectedTask, setSelectedTask] = useState<Task | null>(null);
  const [selectedTableTemplateId, setSelectedTableTemplateId] = useState<string | null>(null);
  const [selectedTableId, setSelectedTableId] = useState<string | null>(null);
  const [tableJumpNotice, setTableJumpNotice] = useState<string | null>(null);
  const [highlightTaskId, setHighlightTaskId] = useState<string | null>(null);
  const taskOverview = useTaskOverview();
  const [taskViewState, setTaskViewState] = useState<TaskWorkspaceViewState>({ group: "all", searchInput: "", page: 0 });
  const [taskEntryRevision, setTaskEntryRevision] = useState(0);
  const [taskEntryGroup, setTaskEntryGroup] = useState<TaskGroup>("pending");
  const { tasks, state: taskReadState, refresh: loadTasks } = taskOverview;
  const [queueBusy, setQueueBusy] = useState(false);
  const [taskNoticeHost, setTaskNoticeHost] = useState<HTMLSpanElement | null>(null);
  const updates = useUpdates();
  const [duplicates, setDuplicates] = useState<PendingDuplicate[]>(() => {
    try {
      const value = JSON.parse(sessionStorage.getItem("zhiyi-duplicate-uploads") ?? "[]");
      return Array.isArray(value) ? value : [];
    } catch { return []; }
  });
  const [activeDuplicate, setActiveDuplicate] = useState<PendingDuplicate | null>(null);
  const duplicateWrites = useRef(new Map<string, Promise<PendingDuplicateRecord>>());
  const persistedDuplicates = useRef(new Set<string>());
  const removedDuplicates = useRef(new Set<string>());
  const removeDuplicates = useCallback((items: readonly PendingDuplicate[]) => {
    const ids = new Set(items.map(item => item.id));
    for (const id of ids) {
      persistedDuplicates.current.delete(id);
      removedDuplicates.current.add(id);
    }
    setDuplicates(current => current.filter(entry => !ids.has(entry.id)));
    setActiveDuplicate(current => current && ids.has(current.id) ? null : current);
  }, []);
  const queueMutation = useRef(false);
  // 上传中乐观任务：上传请求发出前立即显示在全局任务条并开始计时，
  // 不等后端创建任务的异步响应（上传完成后由真实任务替换）。
  const [optimisticTasks, setOptimisticTasks] = useState<Task[]>([]);
  const [modelStatus, setModelStatus] = useState<ModelStatus | null>(null);
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null);
  const [systemStatusFailed, setSystemStatusFailed] = useState(false);
  const [statusRefresh, setStatusRefresh] = useState(0);
  const statusGeneration = useRef(0);
  const [onboardingOpen, setOnboardingOpen] = useState(false);
  const [onboardingSetupPending, setOnboardingSetupPending] = useState(false);
  const [onboardingSession, setOnboardingSession] = useState(0);
  const [settingsFocusRequest, setSettingsFocusRequest] = useState<{ section: "local" | "ai" | "about"; revision: number }>();
  const [extractionDemo, setExtractionDemo] = useState<DemoScenario | null>(null);
  const { toast, notify, clear } = useToast();

  const rememberDuplicate = useCallback((item: PendingDuplicate) => {
    const write = savePendingDuplicate(item);
    duplicateWrites.current.set(item.id, write);
    void write.then(saved => {
      persistedDuplicates.current.add(item.id);
      setDuplicates(current => current.map(entry => entry.id === item.id ? saved : entry));
    }, () => notify("重复待确认暂未保存到本机，关闭前请先处理。", "error"))
      .finally(() => { if (duplicateWrites.current.get(item.id) === write) duplicateWrites.current.delete(item.id); });
  }, [notify]);

  useEffect(() => {
    let active = true;
    void getPendingDuplicates().then(items => {
      if (!active || !Array.isArray(items)) return;
      for (const item of items) persistedDuplicates.current.add(item.id);
      setDuplicates(current => {
        const known = new Set(current.map(item => item.id));
        return [...current, ...items.filter(item => !known.has(item.id) && !removedDuplicates.current.has(item.id))];
      });
    }).catch(() => { if (active) notify("暂时无法读取重复待确认，重新打开知意后可重试。", "error"); });
    try {
      const legacy = JSON.parse(sessionStorage.getItem("zhiyi-duplicate-uploads") ?? "[]");
      if (Array.isArray(legacy) && legacy.length) {
        void Promise.all(legacy.map(item => savePendingDuplicate(item))).then(items => {
          for (const item of items) persistedDuplicates.current.add(item.id);
          sessionStorage.removeItem("zhiyi-duplicate-uploads");
        }).catch(() => { if (active) notify("部分旧会话待确认尚未保存，请先处理。", "error"); });
      }
    } catch { /* Older sessions may have no accessible pending cache. */ }
    return () => { active = false; };
  }, [notify]);

  useEffect(() => {
    let theme: "auto" | "light" | "dark" = "auto";
    try {
      const saved = localStorage.getItem("theme");
      if (saved === "light" || saved === "dark") theme = saved;
    } catch { /* Optional preferences must not prevent the workspace opening. */ }
    applyTheme(theme);
  }, []);

  useEffect(() => {
    let active = true;
    const generation = statusGeneration.current;
    const current = () => active && generation === statusGeneration.current;
    let checking = false;
    let timer: number | undefined;
    void getModelStatus()
      .then(status => { if (current()) setModelStatus(status); })
      .catch(() => { if (current()) setModelStatus(null); });
    // A restored app can become reachable before its worker has started.
    // Keep that transient state from remaining on screen until a reload.
    const refresh = async () => {
      if (!current() || checking || document.hidden) return;
      checking = true;
      window.clearTimeout(timer);
      try {
        const status = await getSystemStatus();
        if (current()) { setSystemStatus(status); setSystemStatusFailed(false); }
      } catch {
        if (current()) { setSystemStatus(null); setSystemStatusFailed(true); }
      } finally {
        checking = false;
        if (current()) timer = window.setTimeout(() => void refresh(), 15000);
      }
    };
    const visible = () => { if (!document.hidden) void refresh(); };
    void refresh();
    document.addEventListener("visibilitychange", visible);
    return () => {
      active = false;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [statusRefresh]);

  useEffect(() => {
    let active = true;
    void getOnboardingState()
      .then((completedVersion) => {
        if (!active) return;
        if (completedVersion >= ONBOARDING_VERSION || onboardingDone()) return;
        setOnboardingOpen(true);
      })
      // A failed read cannot establish that this is a first visit. The guide
      // remains available in Settings; do not cover service recovery with it.
      .catch(() => undefined);
    return () => { active = false; };
  }, []);

  // 全局处理日志：由后端 SSE 任务事件生成，覆盖入队/开始/校验/完成/失败/暂停/
  // 取消/待选模板等所有状态变化；日志放在 App 层，页面切换不丢失，状态监控页展示。
  const [taskLogs, setTaskLogs] = useState<{ time: string; message: string; taskId?: string }[]>([]);
  const appendTaskLogs = useCallback((messages: { message: string; taskId?: string }[]) => {
    if (messages.length === 0) return;
    const time = new Date().toLocaleTimeString("zh-CN", { hour12: false });
    setTaskLogs((prev) => {
      const next = [...prev, ...messages.map((message) => ({ time, ...message }))];
      return next.length > 100 ? next.slice(next.length - 100) : next;
    });
  }, []);

  const statusToLogMessage = useCallback((event: TaskEvent): string | null => {
    switch (event.status) {
      case "created":
      case "queued":
        return `「${event.filename}」已加入队列`;
      case "processing":
        return `「${event.filename}」开始处理`;
      case "validating":
        return `「${event.filename}」进入规则校验`;
      case "completed":
        switch (event.export_status) {
          case "completed":
            return `「${event.filename}」提取已完成；${event.export_mode === "move" ? "原文件已归档" : "副本已导出"}`;
          case "failed":
          case "needs_rebind":
            return `「${event.filename}」提取已完成；${event.export_mode === "move" ? "原文件归档" : "副本导出"}需要处理，请到状态监控的“待确认”查看`;
          case "pending":
          case "awaiting_confirmation":
          case "exporting":
            return `「${event.filename}」提取已完成；${event.export_mode === "move" ? "原文件等待归档" : "副本等待导出"}`;
          case "skipped":
            return `「${event.filename}」提取已完成；已跳过${event.export_mode === "move" ? "原文件归档" : "副本导出"}`;
          default:
            return `「${event.filename}」提取已完成`;
        }
      case "failed":
        return event.failure_message
          ? `「${event.filename}」处理失败：${event.failure_message}`
          : `「${event.filename}」处理失败`;
      case "paused":
        return `「${event.filename}」已暂停`;
      case "cancelled":
        return `「${event.filename}」已取消`;
      case "needs_review":
        return `「${event.filename}」等待确认`;
      case "waiting_for_template":
        return `「${event.filename}」需要你确认，请到状态监控的“待确认”查看`;
      default:
        return null;
    }
  }, []);

  // 任务事件进入处理日志，失败和待确认事项由顶栏提供入口。
  const handleTaskEvents = useCallback(
    (events: TaskEvent[]) => {
      const messages: { message: string; taskId?: string }[] = [];
      for (const event of events) {
        const message = statusToLogMessage(event);
        if (message) messages.push({ message, taskId: event.status === "failed" ? event.id : undefined });
      }
      appendTaskLogs(messages);
    },
    [appendTaskLogs, statusToLogMessage],
  );

  // 上传请求发出前：立即把文件以乐观任务加入全局任务条并开始计时（不依赖后端响应）
  const handleUploadStarted = useCallback(
    (files: { tempId: string; filename: string }[]) => {
      if (files.length === 0) return;
      const nowIso = new Date().toISOString();
      setOptimisticTasks((prev) => [
        ...prev,
        ...files.map((file) => ({
          id: file.tempId,
          filename: file.filename,
          content_type: "",
          size_bytes: 0,
          sha256: "",
          template_mode: "smart" as const,
          template_id: null,
          template_version: null,
          candidate_templates: [],
          status: "processing" as const,
          duplicate_of_task_id: null,
          created_at: nowIso,
          updated_at: nowIso,
        })),
      ]);
    },
    [],
  );

  // 上传完成：先拉取真实任务（乐观任务仍在，无闪烁），再把乐观任务移除；
  // 真实任务开始处理时由全局任务条从零重新计时（不叠加排队耗时）。
  const handleUploadsComplete = useCallback(
    async () => {
      await loadTasks();
      setOptimisticTasks([]);
    },
    [loadTasks],
  );

  useEffect(() => {
    const unsubscribe = typeof EventSource === "undefined" ? undefined : subscribeTaskEvents(handleTaskEvents);
    return () => unsubscribe?.();
  }, [handleTaskEvents]);

  const handlePauseTask = useCallback(async () => {
    if (queueMutation.current) return;
    queueMutation.current = true; setQueueBusy(true);
    try { await pauseQueue(); await loadTasks(); }
    catch (err) { notify(err instanceof Error ? err.message : "暂停队列失败。", "error"); }
    finally { queueMutation.current = false; setQueueBusy(false); }
  }, [loadTasks, notify]);

  const handleResumeTask = useCallback(async () => {
    if (queueMutation.current) return;
    queueMutation.current = true; setQueueBusy(true);
    try { await resumeQueue(); await loadTasks(); }
    catch (err) { notify(err instanceof Error ? err.message : "启动队列失败。", "error"); }
    finally { queueMutation.current = false; setQueueBusy(false); }
  }, [loadTasks, notify]);

  const navigateTo = useCallback((page: NavigationKey, afterProceed?: () => void) => {
    const proceed = () => {
      navSeqRef.current += 1;
      pageScrollPositionsRef.current[activePage] = pageWrapRef.current?.scrollTop ?? 0;
      setFocusAssistantComposer(false);
      afterProceed?.();
      setActivePage(page);
    };
    if (window.dispatchEvent(new CustomEvent("zhiyi:before-navigate", { cancelable: true, detail: { proceed } }))) proceed();
  }, [activePage]);

  useLayoutEffect(() => {
    const target = pageScrollPositionsRef.current[activePage] ?? 0;
    const wrapper = pageWrapRef.current;
    if (!wrapper) return;
    wrapper.scrollTop = target;
    let restored = Math.abs(wrapper.scrollTop - target) <= 2;
    const restoreWhenReady = () => {
      if (restored) return;
      wrapper.scrollTop = target;
      // 异步列表首屏可能只有加载骨架，浏览器会先把 scrollTop 夹到最大值。
      // 内容高度足够后再判定恢复完成。
      restored = Math.abs(wrapper.scrollTop - target) <= 2;
    };
    const frame = window.requestAnimationFrame(restoreWhenReady);
    const observer = new MutationObserver(restoreWhenReady);
    observer.observe(wrapper, { childList: true, subtree: true });
    const timeout = window.setTimeout(() => {
      restoreWhenReady();
      observer.disconnect();
    }, 2000);
    return () => {
      window.cancelAnimationFrame(frame);
      window.clearTimeout(timeout);
      observer.disconnect();
    };
  }, [activePage]);

  const handleNavigate = useCallback((page: NavigationKey) => {
    navigateTo(page, () => {
      if (page === "history") setHistoryEntry(undefined);
      setExtractionDemo(null);
      if (page === "extract") setSelectedTask(null);
      if (page === "tables") {
        setSelectedTableTemplateId(null);
        setSelectedTableId(null);
        setTableJumpNotice(null);
      } else {
        setHighlightTaskId(null);
      }
    });
  }, [navigateTo]);

  const openTaskGroup = useCallback((group: TaskGroup) => {
    navigateTo("workspace", () => {
      setTaskViewState(current => ({ ...current, group, searchInput: "", page: 0 }));
      setTaskEntryGroup(group);
      setTaskEntryRevision(value => value + 1);
    });
  }, [navigateTo]);

  const refreshModelStatus = useCallback(() => {
    // Invalidate before effect cleanup: a queued response from the old profile
    // must not restore its snapshot while the new profile is being checked.
    statusGeneration.current += 1;
    setModelStatus(null);
    setSystemStatus(null);
    setSystemStatusFailed(false);
    setStatusRefresh(value => value + 1);
  }, []);

  const showDemo = useCallback((scenario: DemoScenario = "business") => {
    navigateTo("extract", () => {
      setSelectedTask(null);
      setExtractionDemo(scenario);
    });
  }, [navigateTo]);

  const openTask = useCallback((task: Task) => {
    navigateTo("extract", () => {
      setSelectedTask(task);
      setExtractionDemo(null);
    });
  }, [navigateTo]);

  // 跳转到数据表：优先用确认记录精确定位（含手动指定表），确认已删则提示数据不存在
  const openTaskData = useCallback(
    async (task: Task) => {
      // 导航守卫：getConfirmation 期间用户可能已切到别的页，只在仍停留时跳转
      const navSeq = ++navSeqRef.current;
      let selectedId: string | null = null;
      let jumpNotice: string | null = null;
      try {
        const confirmation = await getConfirmation(task.id);
        if (navSeq !== navSeqRef.current) return;
        if (confirmation.row_count === 0) {
          jumpNotice = "该文件的当前数据已清空；原文件和修订历史仍保留在文件历史中。";
        } else {
          selectedId = confirmation.table_id;
        }
      } catch {
        if (navSeq !== navSeqRef.current) return;
        // 无确认记录：按模板定位（可能从未确认或目标表已删）
        jumpNotice = "这份文件当前没有表内数据；可在文件历史查看原文件与修订记录。";
      }
      navigateTo("tables", () => {
        setSelectedTableTemplateId(task.template_id);
        setSelectedTableId(selectedId);
        setTableJumpNotice(jumpNotice);
        setHighlightTaskId(task.id);
      });
    },
    [navigateTo],
  );

  useEffect(() => {
    function onReference(event: Event) {
      const ref = (event as CustomEvent<Reference & { draft?: TemplateDraft }>).detail;
      assistant.setDrawerOpen(false);
      if (ref.kind === "assistant") { navigateTo("assistant"); return; }
      if (ref.kind === "draft" && ref.draft) {
        navigateTo("templates", () => { setAssistantDraft(ref.draft!); setAssistantTemplate(null); setAssistantTemplateVersion(null); });
        return;
      }
      if (ref.kind === "template") {
        navigateTo("templates", () => { setAssistantTemplate(ref.id); setAssistantTemplateVersion(ref.version ?? null); setAssistantDraft(null); });
        return;
      }
      if (ref.kind === "table" || ref.kind === "revisions") {
        navigateTo("tables", () => {
          setSelectedTableId(ref.id); setSelectedTableTemplateId(null); setHighlightTaskId(null);
          setAssistantRow(ref.row_id ?? null); setTableJumpNotice(ref.row_id ? "来自问知意：查看所选记录" : "来自问知意的资料引用");
        });
        return;
      }
      if (ref.kind === "task" || ref.kind === "original") {
        const seq = ++navSeqRef.current;
        void assistantApi<Task>(`/references/tasks/${encodeURIComponent(ref.id)}`).then(task => {
          if (seq === navSeqRef.current) navigateTo("extract", () => { setSelectedTask(task); setExtractionDemo(null); });
        }).catch(e => notify(e.message, "error"));
      }
    }
    window.addEventListener("zhiyi:assistant-navigate", onReference);
    return () => window.removeEventListener("zhiyi:assistant-navigate", onReference);
  }, [assistant.setDrawerOpen, navigateTo, notify]);

  const serviceStatus = <ServiceStatus systemStatus={systemStatus} modelStatus={modelStatus} failed={systemStatusFailed} />;
  const summary = taskOverview.summary;
  const pendingCount = summary ? taskGroupCounts(summary, tasks).pending + duplicates.length : undefined;

  const updateNotice = (updates.status?.result?.available || updates.status?.downloaded_version) && !updates.checking
    ? <button className="gtb-failure-link" type="button" onClick={() => navigateTo("settings", () => {
      setSettingsFocusRequest(previous => ({ section: "about", revision: (previous?.revision ?? 0) + 1 }));
    })}>有新版本</button> : null;

  return (
    <div className="app-shell">
      <div className="app-frame">
        <Sidebar
          active={activePage}
          onNavigateIntent={preparePage}
          modelConnected={modelStatus?.connected ?? null}
          modelName={modelStatus?.configured_model ?? null}
          onNavigate={handleNavigate}
        />

        <main className="app-main">
          <div className="global-task-bar">
            {taskReadState !== "ready" && optimisticTasks.length === 0 ? (
              <section className="gtb-card" aria-label="当前任务状态" aria-live="polite">
                <div className="gtb-status">
                  <StatusDot state="unknown" />
                  <span>{taskReadState === "loading" ? "正在读取任务…" : "任务状态未确认"}</span>
                </div>
                <div className="gtb-info"><div className="gtb-stage">
                  {taskReadState === "loading" ? "读取完成后显示当前队列" : "读取失败，暂时无法判断队列是否空闲"}
                  {duplicates.length > 0 && <button className="gtb-failure-link" type="button" onClick={() => openTaskGroup("pending")}>
                    {duplicates.length} 个待处理事项
                  </button>}
                  <span ref={setTaskNoticeHost} className="task-notice-slot" />
                  {updateNotice}
                </div></div>
                {serviceStatus}
                {taskReadState === "error" && <button className="btn ghost sm" type="button" onClick={() => void loadTasks()}>重试任务状态</button>}
              </section>
            ) : <GlobalTaskCard
              tasks={[...optimisticTasks, ...tasks]}
              onPauseQueue={handlePauseTask}
              onResumeQueue={handleResumeTask}
              onOpenPending={() => openTaskGroup("pending")}
              onOpenTasks={() => handleNavigate("workspace")}
              queuePaused={taskOverview.queuePaused}
              busy={queueBusy}
              pendingCount={pendingCount ?? (duplicates.length || undefined)}
              failedCount={taskOverview.summary?.failed}
              onOpenFailed={() => openTaskGroup("failed")}
              noticeSlot={<>
                <span ref={setTaskNoticeHost} className="task-notice-slot" />
                {updateNotice}
              </>}
              serviceStatus={serviceStatus}
            />}
            {activePage !== "assistant" && <AssistantTrigger />}
          </div>

          <div className={`app-page-wrap ${activePage === "assistant" ? "ask-page-wrap" : ""}`} ref={pageWrapRef}>
            <PageLoadBoundary key={activePage}>
            <Suspense fallback={<PageOpening />}>
            <PageEntrance page={activePage} container={pageWrapRef} />
            {activePage === "workspace" ? (
              <TaskQueuePage
                logs={taskLogs}
                overview={taskOverview}
                viewState={taskViewState}
                scrollMemory={listScrollMemory.current}
                onViewStateChange={setTaskViewState}
                entryRevision={taskEntryRevision}
                entryGroup={taskEntryGroup}
                onOpenData={openTaskData}
                onOpenTask={openTask}
                onNavigateExtract={() => handleNavigate("extract")}
                duplicates={duplicates}
                onOpenDuplicate={setActiveDuplicate}
                onDeleteDuplicates={removeDuplicates}
                beforeDeleteDuplicates={async items => {
                  await Promise.allSettled(items.map(item => duplicateWrites.current.get(item.id)));
                }}
              />
            ) : activePage === "extract" ? (
              <ExtractPage
                initialTask={selectedTask}
                demo={extractionDemo}
                onNavigateHistory={() => handleNavigate("history")}
                onTasksChange={loadTasks}
                onUploadStarted={handleUploadStarted}
                onUploadsComplete={handleUploadsComplete}
                noticeHost={taskNoticeHost}
                onDuplicate={item => { rememberDuplicate(item); setDuplicates(current => [...current, item]); }}
              />
            ) : activePage === "templates" ? (
              <TemplatesPage initialTemplateId={assistantTemplate} initialVersion={assistantTemplateVersion} initialDraft={assistantDraft} onDraftConsumed={() => setAssistantDraft(null)} />
            ) : activePage === "tables" ? (
              <DataTablePage
                onNavigateExtract={() => handleNavigate("extract")}
                initialTemplateId={selectedTableTemplateId}
                initialTableId={selectedTableId}
                jumpNotice={tableJumpNotice}
                highlightTaskId={highlightTaskId}
                initialRowId={assistantRow}
              />
            ) : activePage === "assistant" ? (
              <AssistantPage focusComposer={focusAssistantComposer} />
            ) : activePage === "history" ? (
              <HistoryPage initialTab={historyEntry} viewState={historyViewState.current} onViewStateChange={rememberHistoryView}
                scrollMemory={listScrollMemory.current} onOpenData={openTaskData}
                onNavigateExtract={() => handleNavigate("extract")} />
            ) : activePage === "dashboard" ? (
              <DashboardPage onReview={() => navigateTo("history", () => {
                setHistoryEntry("problems");
                delete listScrollMemory.current["history-problems"];
              })} onNavigate={handleNavigate} onOpenTable={id => {
                setSelectedTableId(id); setSelectedTableTemplateId(null); setHighlightTaskId(null);
                setAssistantRow(null); setTableJumpNotice(null); navigateTo("tables");
              }} />
            ) : activePage === "backups" ? (
              <BackupPage />
            ) : activePage === "settings" ? (
              <SettingsPage
                onShowOnboarding={() => { setOnboardingSetupPending(false); setOnboardingSession(value => value + 1); setOnboardingOpen(true); }}
                onResumeOnboarding={onboardingSetupPending ? () => { setOnboardingSetupPending(false); setOnboardingOpen(true); } : undefined}
                settingsFocusRequest={settingsFocusRequest}
                onModelStatusChanged={refreshModelStatus}
                updates={updates}
              />
            ) : activePage === "connections" ? (
              <ConnectionsPage />
            ) : activePage === "guide" ? (
              <GuidePage onNavigate={handleNavigate} />
            ) : null}
            </Suspense>
            </PageLoadBoundary>
          </div>
          {activeDuplicate && <PendingDuplicateDialog key={activeDuplicate.id} item={activeDuplicate}
            onClose={() => setActiveDuplicate(null)}
            onSkip={async () => {
              await duplicateWrites.current.get(activeDuplicate.id)?.catch(() => undefined);
              await deletePendingDuplicate(activeDuplicate.id);
              removeDuplicates([activeDuplicate]);
            }}
            onBeforeRetry={async () => {
              const write = duplicateWrites.current.get(activeDuplicate.id);
              if (write) return (await write).sha256;
              if (persistedDuplicates.current.has(activeDuplicate.id) && activeDuplicate.sha256) return activeDuplicate.sha256;
              return (await savePendingDuplicate(activeDuplicate)).sha256;
            }}
            onQueued={() => { removeDuplicates([activeDuplicate]); void loadTasks(); }}
            onView={duplicates.find(item => item.id === activeDuplicate.id)?.hasResult ? async () => {
              const [existing] = await getTasks({ taskIds: [activeDuplicate.existingTaskId] });
              if (!existing?.has_result) throw new Error("已有任务暂时没有可查看的提取结果。");
              setActiveDuplicate(null);
              openTask(existing);
            } : undefined} />}
        </main>
      </div>
      <AssistantDrawer onExpand={() => navigateTo("assistant", () => setFocusAssistantComposer(true))} />
      <Suspense fallback={null}><StatisticBridge onSaved={() => notify("已添加到仪表盘，聊天中的分析快照保持原样。", "success")} /></Suspense>
      <Onboarding
        key={onboardingSession}
        open={onboardingOpen}
        modelStatus={modelStatus}
        onNavigate={handleNavigate}
        onShowDemo={showDemo}
        onSetup={section => navigateTo("settings", () => {
          try { sessionStorage.setItem("zhiyi-settings-anchor", section); } catch { /* Optional reading preference. */ }
          setSettingsFocusRequest(previous => ({ section, revision: (previous?.revision ?? 0) + 1 }));
          setExtractionDemo(null);
          setHighlightTaskId(null);
          setOnboardingSetupPending(true);
          setOnboardingOpen(false);
        })}
        onClose={() => { setOnboardingSetupPending(false); setOnboardingOpen(false); }}
      />
      <Toast toast={toast} onDismiss={clear} />
    </div>
  );
}
