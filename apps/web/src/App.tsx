import { ModelConnectionState } from "./assistant/ModelConnectionState";
import * as Popover from "@radix-ui/react-popover";
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { AssistantProvider, useAssistant } from "./assistant/AssistantProvider";
import { AssistantDrawer, AssistantPage, AssistantTrigger } from "./assistant/AssistantSurface";
import { assistantApi } from "./assistant/api";
import type { Reference } from "./assistant/types";
import type { TemplateDraft } from "./types";
import { BackupPage } from "./components/BackupPage";
import { ConnectionsPage } from "./components/ConnectionsPage";
import { DataTablePage } from "./components/DataTablePage";
import { ExtractPage } from "./components/ExtractPage";
import { GlobalTaskCard } from "./components/TaskActivity";
import { HistoryPage } from "./components/HistoryPage";
import { GuidePage } from "./components/GuidePage";
import { Onboarding, ONBOARDING_VERSION, onboardingDone } from "./components/Onboarding";
import { SettingsPage } from "./components/SettingsPage";
import { Sidebar } from "./components/Sidebar";
import { TaskQueuePage } from "./components/TaskQueuePage";
import { TemplatesPage } from "./components/TemplatesPage";
import { Toast, useToast } from "./components/Toast";
import {
  deleteTask,
  getConfirmation,
  getOnboardingState,
  getModelStatus,
  getSystemStatus,
  getTasks,
  getTaskSummary,
  pauseQueue,
  resumeQueue,
  subscribeTaskEvents,
  taskEventsConnected,
} from "./api";
import type { TaskEvent } from "./api";
import type { DemoScenario, ModelStatus, NavigationKey, SystemStatus, Task, TaskStatus } from "./types";

const DashboardPage = lazy(() => import("./components/DashboardPage").then(module => ({ default: module.DashboardPage })));
const StatisticBridge = lazy(() => import("./dashboard/StatisticBridge").then(module => ({ default: module.StatisticBridge })));

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
  const [attentionCounts, setAttentionCounts] = useState<{ pending?: number; review?: number }>({});
  const [activePage, setActivePage] = useState<NavigationKey>("workspace");
  const [historyEntry, setHistoryEntry] = useState<"completed" | "problems">("completed");
  const pageWrapRef = useRef<HTMLDivElement>(null);
  const pageScrollPositionsRef = useRef<Partial<Record<NavigationKey, number>>>({});
  // 跳转表竞态守卫：getConfirmation 期间用户切换页面时不再强行跳回数据表
  const navSeqRef = useRef(0);
  const [selectedTask, setSelectedTask] = useState<Task | null>(null);
  const [selectedTableTemplateId, setSelectedTableTemplateId] = useState<string | null>(null);
  const [selectedTableId, setSelectedTableId] = useState<string | null>(null);
  const [tableJumpNotice, setTableJumpNotice] = useState<string | null>(null);
  const [highlightTaskId, setHighlightTaskId] = useState<string | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  // 上传中乐观任务：上传请求发出前立即显示在全局任务条并开始计时，
  // 不等后端创建任务的异步响应（上传完成后由真实任务替换）。
  const [optimisticTasks, setOptimisticTasks] = useState<Task[]>([]);
  const [modelStatus, setModelStatus] = useState<ModelStatus | null>(null);
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null);
  const [onboardingOpen, setOnboardingOpen] = useState(false);
  const [extractionDemo, setExtractionDemo] = useState<DemoScenario | null>(null);
  const { toast, notify, clear } = useToast();
  // 失败/待选模板 toast 去重：同一任务同一状态只通知一次，避免 SSE 重复推送刷屏。
  const notifiedTasksRef = useRef<Set<string>>(new Set());
  const notifyRef = useRef(notify);
  notifyRef.current = notify;

  useEffect(() => {
    applyTheme(
      (localStorage.getItem("theme") as "auto" | "light" | "dark") || "auto",
    );
  }, []);

  useEffect(() => {
    let active = true;
    void getOnboardingState()
      .then((completedVersion) => {
        if (!active) return;
        if (completedVersion >= ONBOARDING_VERSION || onboardingDone()) return;
        setOnboardingOpen(true);
      })
      .catch(() => {
        if (active && !onboardingDone()) setOnboardingOpen(true);
      });
    return () => { active = false; };
  }, []);

  // 顶部只展示仍需用户关注或仍在运行的任务。终态失败属于状态监控/历史，
  // 不能让旧失败轮番占据一个无法真正清空的全局横幅。
  const taskRequest = useRef(0);
  const loadTasks = useCallback(async () => {
    const request = ++taskRequest.current;
    try {
      const [active, exports, summary] = await Promise.all([
        getTasks({ limit: 1000, activeOnly: true }),
        getTasks({ limit: 1000, exportPending: true }),
        getTaskSummary().catch(() => null),
      ]);
      if (request !== taskRequest.current) return;
      setTasks(Array.from(new Map([...active, ...exports].map((task) => [task.id, task])).values()));
      setAttentionCounts({ pending: summary?.waiting_for_action == null || summary?.pending_exports == null ? undefined : summary.waiting_for_action + summary.pending_exports, review: summary?.needs_review });
    } catch {
      // 顶部任务栏静默失败，错误由各页面自行处理
    }
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
            return `「${event.filename}」提取已完成；副本已导出`;
          case "failed":
          case "needs_rebind":
            return `「${event.filename}」提取已完成；副本导出需要处理，请查看待处理事项`;
          case "pending":
          case "awaiting_confirmation":
          case "exporting":
            return `「${event.filename}」提取已完成；副本等待导出`;
          case "skipped":
            return `「${event.filename}」提取已完成；已跳过副本导出`;
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
        return `「${event.filename}」需要你确认，请查看待处理事项`;
      default:
        return null;
    }
  }, []);

  // SSE 事件 → 全局日志 + 失败/待选模板 toast：事件驱动，实时且不受页面切换影响
  const handleTaskEvents = useCallback(
    (events: TaskEvent[]) => {
      const messages: { message: string; taskId?: string }[] = [];
      for (const event of events) {
        const message = statusToLogMessage(event);
        if (message) messages.push({ message, taskId: event.status === "failed" ? event.id : undefined });
        const key = `${event.id}:${event.status}`;
        if (notifiedTasksRef.current.has(key)) continue;
        notifiedTasksRef.current.add(key);
        if (event.status === "failed") {
          const reason = event.failure_message
            ? `：${event.failure_message.slice(0, 90)}${event.failure_message.length > 90 ? "…" : ""}`
            : "";
          notifyRef.current(`「${event.filename}」处理失败${reason}。诊断详情见状态监控。`, "error", 6000);
        } else if (event.status === "waiting_for_template") {
          notifyRef.current(`「${event.filename}」需要你确认，请查看待处理事项`, "info");
        }
      }
      appendTaskLogs(messages);
      void loadTasks();
    },
    [appendTaskLogs, statusToLogMessage, loadTasks],
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
    async (entries: { tempId: string; realId: string; startedAt: number }[]) => {
      await loadTasks();
      setOptimisticTasks([]);
    },
    [loadTasks],
  );

  useEffect(() => {
    loadTasks();
    const assistantChanged = (event: Event) => {
      if ((event as CustomEvent<{ task_id?: string }>).detail?.task_id) void loadTasks();
    };
    window.addEventListener("zhiyi:assistant-changed", assistantChanged);
    void getModelStatus().then(setModelStatus).catch(() => setModelStatus(null));
    void getSystemStatus().then(setSystemStatus).catch(() => setSystemStatus(null));
    // SSE 实时推送：任务状态变化立即刷新全局任务条（上传/停止/完成马上反映）；
    // 低频轮询仅作断线兜底（EventSource 自动重连期间仍有刷新）。
    const unsub =
      typeof EventSource === "undefined"
        ? undefined
        : subscribeTaskEvents(handleTaskEvents);
    let lastRefresh = Date.now();
    const visible = () => { if (!document.hidden) { lastRefresh = Date.now(); void loadTasks(); } };
    document.addEventListener("visibilitychange", visible);
    const timer = window.setInterval(() => {
      if (!document.hidden && (!taskEventsConnected() || Date.now() - lastRefresh >= 30000)) {
        lastRefresh = Date.now(); void loadTasks();
      }
    }, 5000);
    return () => {
      window.removeEventListener("zhiyi:assistant-changed", assistantChanged);
      document.removeEventListener("visibilitychange", visible);
      taskRequest.current += 1;
      window.clearInterval(timer);
      unsub?.();
    };
  }, [loadTasks, handleTaskEvents]);

  const handlePauseTask = useCallback(async (_taskId: string) => {
    try {
      // 暂停是队列级操作：冻结整个队列（当前任务中断、排队任务不再启动）。
      // 状态反馈由全局任务卡承担（显示"已暂停"），不再重复弹 toast。
      await pauseQueue();
      await loadTasks();
    } catch (err) {
      notify(err instanceof Error ? err.message : "暂停队列失败。", "error");
    }
  }, [loadTasks, notify]);

  const handleResumeTask = useCallback(async (_taskId: string) => {
    try {
      await resumeQueue();
      await loadTasks();
    } catch (err) {
      notify(err instanceof Error ? err.message : "启动队列失败。", "error");
    }
  }, [loadTasks, notify]);

  const handleDeleteTask = useCallback(async (taskId: string) => {
    try {
      await deleteTask(taskId);
      await loadTasks();
      notify("任务已删除。", "info");
    } catch (err) {
      notify(err instanceof Error ? err.message : "删除任务失败", "error");
    }
  }, [loadTasks, notify]);

  const navigateTo = useCallback((page: NavigationKey) => {
    const proceed = () => {
      pageScrollPositionsRef.current[activePage] = pageWrapRef.current?.scrollTop ?? 0;
      setActivePage(page);
    };
    if (page === activePage || window.dispatchEvent(new CustomEvent("zhiyi:before-navigate", { cancelable: true, detail: { proceed } }))) proceed();
  }, [activePage]);

  useEffect(() => {
    const target = pageScrollPositionsRef.current[activePage] ?? 0;
    const wrapper = pageWrapRef.current;
    if (!wrapper) return;
    let restored = target === 0;
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
    if (page === "history") setHistoryEntry("completed");
    setExtractionDemo(null);
    if (page === "extract") setSelectedTask(null);
    if (page === "tables") {
      setSelectedTableTemplateId(null);
      setSelectedTableId(null);
      setTableJumpNotice(null);
    } else {
      setHighlightTaskId(null);
    }
    navigateTo(page);
  }, [navigateTo]);

  const refreshModelStatus = useCallback(() => {
    void getModelStatus().then(setModelStatus).catch(() => setModelStatus(null));
    void getSystemStatus().then(setSystemStatus).catch(() => setSystemStatus(null));
  }, []);

  const openTask = useCallback((task: Task) => {
    pageScrollPositionsRef.current[activePage] = pageWrapRef.current?.scrollTop ?? 0;
    setSelectedTask(task);
    setActivePage("extract");
  }, [activePage]);

  // 跳转到数据表：优先用确认记录精确定位（含手动指定表），确认已删则提示数据不存在
  const openTaskData = useCallback(
    async (task: Task) => {
      setSelectedTableTemplateId(task.template_id);
      setSelectedTableId(null);
      setTableJumpNotice(null);
      setHighlightTaskId(task.id);
      // 导航守卫：getConfirmation 期间用户可能已切到别的页，只在仍停留时跳转
      const navSeq = ++navSeqRef.current;
      try {
        const confirmation = await getConfirmation(task.id);
        if (navSeq !== navSeqRef.current) return;
        if (confirmation.row_count === 0) {
          setTableJumpNotice(
            `该文件的数据记录已全部删除，表中不再有此记录；可回到提取页重新"保存到表"追加。`,
          );
        } else {
          setSelectedTableId(confirmation.table_id);
        }
      } catch {
        if (navSeq !== navSeqRef.current) return;
        // 无确认记录：按模板定位（可能从未确认或目标表已删）
        setTableJumpNotice(
          "该文件尚未保存到任何数据表，或目标表已被删除；可回到提取页重新保存到表。",
        );
      }
      navigateTo("tables");
    },
    [navigateTo],
  );

  useEffect(() => {
    function onReference(event: Event) {
      const ref = (event as CustomEvent<Reference & { draft?: TemplateDraft }>).detail;
      assistant.setDrawerOpen(false);
      if (ref.kind === "assistant") { navigateTo("assistant"); return; }
      if (ref.kind === "draft" && ref.draft) { setAssistantDraft(ref.draft); setAssistantTemplate(null); setAssistantTemplateVersion(null); navigateTo("templates"); return; }
      if (ref.kind === "template") { setAssistantTemplate(ref.id); setAssistantTemplateVersion(ref.version ?? null); setAssistantDraft(null); navigateTo("templates"); return; }
      if (ref.kind === "table" || ref.kind === "revisions") { setSelectedTableId(ref.id); setSelectedTableTemplateId(null); setHighlightTaskId(null); setAssistantRow(ref.row_id ?? null); setTableJumpNotice(ref.row_id ? `来自问知意：定位记录 #${ref.row_id}` : "来自问知意的资料引用"); navigateTo("tables"); return; }
      if (ref.kind === "task" || ref.kind === "original") {
        const seq = ++navSeqRef.current;
        void assistantApi<Task>(`/references/tasks/${encodeURIComponent(ref.id)}`).then(task => { if (seq === navSeqRef.current) { setSelectedTask(task); navigateTo("extract"); } }).catch(e => notify(e.message, "error"));
      }
    }
    window.addEventListener("zhiyi:assistant-navigate", onReference);
    return () => window.removeEventListener("zhiyi:assistant-navigate", onReference);
  }, [assistant.setDrawerOpen, navigateTo, notify]);

  return (
    <div className="app-shell">
      <div className="app-frame">
        <Sidebar
          active={activePage}
          modelConnected={modelStatus?.connected ?? null}
          modelName={modelStatus?.configured_model ?? null}
          onNavigate={handleNavigate}
        />

        <main className="app-main">
          <div className="global-task-bar">
            <GlobalTaskCard
              tasks={[...optimisticTasks, ...tasks]}
              onPause={handlePauseTask}
              onResume={handleResumeTask}
              onDelete={handleDeleteTask}
              onOpenPending={() => handleNavigate("workspace")}
              pendingCount={attentionCounts.pending}
              reviewCount={attentionCounts.review}
            />
            {activePage !== "assistant" && <AssistantTrigger />}
            <Popover.Root><Popover.Trigger className="system-status-trigger">
              {systemStatus == null ? "正在连接服务…" : !systemStatus.api.connected ? "服务未连接" : !systemStatus.worker.connected ? "文件处理未启动" : "服务已就绪"}
              <span aria-hidden="true">⌄</span>
            </Popover.Trigger>
            <Popover.Content className="system-status-popover" align="start" sideOffset={8} collisionPadding={16}>
            <strong>连接详情</strong><div className="system-status-summary" role="status"><span>
              {systemStatus
                ? [systemStatus.api, systemStatus.worker]
                    .map((component) => component.message)
                    .join(" · ")
                : "正在检查 API、任务消费者和模型服务。"}</span>
              <span title={modelStatus?.configuration_error || "检查提取方案的模型服务与模型名称；不代表文件提取质量。"}>提取模型：{modelStatus?.configured_model || "未配置"} · {modelStatus == null ? "检查中" : modelStatus.configuration_error ? "配置需检查" : modelStatus.connected ? "服务可达" : "未连接"}</span>
              <ModelConnectionState/>
            </div>
            </Popover.Content></Popover.Root>
          </div>

          <div className={`app-page-wrap ${activePage === "assistant" ? "ask-page-wrap" : ""}`} key={activePage} ref={pageWrapRef}>
            {activePage === "workspace" ? (
              <TaskQueuePage
                logs={taskLogs}
                onOpenData={openTaskData}
                onOpenTask={openTask}
              />
            ) : activePage === "extract" ? (
              <ExtractPage
                initialTask={selectedTask}
                demo={extractionDemo}
                onNavigateHistory={() => navigateTo("history")}
                onTasksChange={loadTasks}
                onUploadStarted={handleUploadStarted}
                onUploadsComplete={handleUploadsComplete}
              />
            ) : activePage === "templates" ? (
              <TemplatesPage initialTemplateId={assistantTemplate} initialVersion={assistantTemplateVersion} initialDraft={assistantDraft} onDraftConsumed={() => setAssistantDraft(null)} />
            ) : activePage === "tables" ? (
              <DataTablePage
                initialTemplateId={selectedTableTemplateId}
                initialTableId={selectedTableId}
                jumpNotice={tableJumpNotice}
                highlightTaskId={highlightTaskId}
                initialRowId={assistantRow}
              />
            ) : activePage === "assistant" ? (
              <AssistantPage />
            ) : activePage === "history" ? (
              <HistoryPage initialTab={historyEntry} onOpenData={openTaskData} onOpenTask={openTask} />
            ) : activePage === "dashboard" ? (
              <Suspense fallback={<div className="view muted">正在打开数据仪表盘…</div>}><DashboardPage onReview={() => { setHistoryEntry("problems"); navigateTo("history"); }} onNavigate={page => { if (page === "history") setHistoryEntry("completed"); navigateTo(page); }} onOpenTable={id => {
                setSelectedTableId(id); setSelectedTableTemplateId(null); setHighlightTaskId(null);
                setAssistantRow(null); setTableJumpNotice(null); navigateTo("tables");
              }} /></Suspense>
            ) : activePage === "backups" ? (
              <BackupPage />
            ) : activePage === "settings" ? (
              <SettingsPage
                onShowOnboarding={() => setOnboardingOpen(true)}
                onModelStatusChanged={refreshModelStatus}
              />
            ) : activePage === "connections" ? (
              <ConnectionsPage />
            ) : activePage === "guide" ? (
              <GuidePage onNavigate={handleNavigate} />
            ) : null}
          </div>
        </main>
      </div>
      <AssistantDrawer />
      <Suspense fallback={null}><StatisticBridge onSaved={() => notify("已添加到仪表盘，聊天中的分析快照保持原样。", "success")} /></Suspense>
      <Onboarding
        open={onboardingOpen}
        modelStatus={modelStatus}
        onNavigate={handleNavigate}
        onShowDemo={(scenario) => {
          setSelectedTask(null);
          setExtractionDemo(scenario);
          navigateTo("extract");
        }}
        onClose={() => setOnboardingOpen(false)}
      />
      <Toast toast={toast} onDismiss={clear} />
    </div>
  );
}
