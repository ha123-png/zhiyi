import { useEffect, useRef, useState, type ReactNode } from "react";
import { Pause, Play } from "lucide-react";
import { Icon } from "./Icon";
import { parseServerTime } from "../time";
import type { Task, TaskStatus } from "../types";
import { taskExportIsRunning, taskGroup, taskIsActive, taskStatusLabel } from "../taskPresentation";
import { StatusDot, type StatusDotState } from "./StatusDot";
import "./global-task-bar.css";

function elapsedFrom(start: string | number, endTime: number | string) {
  const end = typeof endTime === "number" ? endTime : parseServerTime(endTime);
  const startMs = typeof start === "number" ? start : parseServerTime(start);
  const seconds = Math.max(0, Math.floor((end - startMs) / 1000));
  const minutes = Math.floor(seconds / 60);
  return { display: `${String(minutes).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`, duration: `PT${seconds}S` };
}

interface ActiveVisual {
  state: StatusDotState;
  label: string;
  stage: string;
  timed: boolean;
}

function activeVisual(status: TaskStatus): ActiveVisual {
  switch (status) {
    case "processing":
      return { state: "processing", label: "处理中", stage: "识别中…", timed: true };
    case "validating":
      return { state: "processing", label: "校验中", stage: "检查规则…", timed: true };
    case "paused":
      return { state: "paused", label: "已暂停", stage: "其他文件照常处理，可在任务列表继续此文件。", timed: false };
    case "created":
    case "queued":
      // 任务已写入队列但尚未被消费者取走；不计时（真正开始处理时才从 0 清零计）
      return { state: "queued", label: "入队中", stage: "任务正在入队", timed: false };
    default:
      return { state: "queued", label: "等待中", stage: "", timed: false };
  }
}

export function GlobalTaskCard({
  tasks,
  onPauseQueue,
  onResumeQueue,
  queuePaused = false,
  busy = false,
  onOpenPending,
  onOpenTasks,
  pendingCount,
  failedCount,
  onOpenFailed,
  noticeSlot,
  serviceStatus,
}: {
  tasks: Task[];
  onPauseQueue?: () => void;
  onResumeQueue?: () => void;
  queuePaused?: boolean | null;
  busy?: boolean;
  onOpenPending?: () => void;
  onOpenTasks?: () => void;
  pendingCount?: number;
  failedCount?: number;
  onOpenFailed?: () => void;
  noticeSlot?: ReactNode;
  serviceStatus?: ReactNode;
}) {
  const [now, setNow] = useState(() => Date.now());
  // A missing server start uses the first time this attempt was observed processing.
  const observedStarts = useRef<Map<string, { status: TaskStatus; attempt: string | null | undefined; time: number }>>(new Map());
  // 显示优先级：正在处理 > 已暂停（需要用户操作：恢复/删除）> 最早排队 > 其他活动任务。
  // 暂停的任务优先展示，避免卡片切去显示其他排队任务而被误认为"自己又启动了"。
  const processingTask =
    tasks.find(
      (task) => task.status === "processing" || task.status === "validating",
    );
  const pausedTask = tasks.find((task) => task.status === "paused");
  const nextQueuedTask = tasks
    .filter((task) => task.status === "created" || task.status === "queued")
    .sort((left, right) => parseServerTime(left.created_at) - parseServerTime(right.created_at))[0];
  const activeTask = processingTask
    ?? pausedTask
    ?? nextQueuedTask
    ?? tasks.find((task) => taskIsActive(task));

  // 任务条只展示一个任务：正在处理 > 已暂停 > 最早排队 > 其他活动任务；
  // 排队中不计时；处理中按本次服务端开始时间计时，缺失时从首次观察计时。
  const visual: ActiveVisual | null = activeTask ? taskExportIsRunning(activeTask)
    ? { state: "queued", label: taskStatusLabel(activeTask), stage: "提取结果已保存，正在处理外部文件。", timed: false }
    : activeVisual(activeTask.status) : null;

  const activeIds = new Set(tasks.filter(taskIsActive).map(task => task.id));
  for (const id of observedStarts.current.keys()) if (!activeIds.has(id)) observedStarts.current.delete(id);
  for (const task of tasks) {
    if (!activeIds.has(task.id)) continue;
    const previous = observedStarts.current.get(task.id);
    const processing = task.status === "processing" || task.status === "validating";
    const wasProcessing = previous?.status === "processing" || previous?.status === "validating";
    const time = !previous || (processing && !wasProcessing) || previous.attempt !== task.started_at ? Date.now() : previous.time;
    observedStarts.current.set(task.id, { status: task.status, attempt: task.started_at, time });
  }
  const serverStart = activeTask?.started_at ? parseServerTime(activeTask.started_at) : NaN;
  const timerAnchor = Number.isFinite(serverStart) ? serverStart : activeTask ? observedStarts.current.get(activeTask.id)?.time : undefined;

  useEffect(() => {
    if (!activeTask || !visual?.timed) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [activeTask?.id, activeTask?.status, activeTask?.started_at, visual?.timed]);

  const pending = pendingCount ?? tasks.filter(task => taskGroup(task) === "pending").length;
  const failures = failedCount ?? tasks.filter(task => task.status === "failed").length;
  let view: ActiveVisual = { state: "idle", label: "当前空闲", stage: "可以放入新的文件", timed: false };
  let filename: string | undefined;
  let actions: ReactNode;
  let elapsed: ReturnType<typeof elapsedFrom> | undefined;
  let timerLabel: string | undefined;

  if (queuePaused === true) {
    view = { state: "paused", label: "队列已暂停", stage: "文件提取队列已暂停。启动后恢复所有暂停任务。", timed: false };
    if (onResumeQueue) actions = <button className="btn primary sm" type="button" disabled={busy} onClick={() => onResumeQueue()}><Icon icon={Play} size={13} />启动队列</button>;
  } else if (activeTask && visual) {
    view = visual;
    filename = activeTask.filename;
    if (!taskExportIsRunning(activeTask)) {
      timerLabel = visual.timed || activeTask.status === "paused" ? "处理用时" : "等待开始";
      if (visual.timed || activeTask.status === "paused") elapsed = elapsedFrom(timerAnchor ?? now, visual.timed ? now : activeTask.updated_at);
    }
    if (["created", "queued", "processing", "validating"].includes(activeTask.status) && onPauseQueue) {
      actions = <button className="btn primary sm" type="button" title="暂停整个队列" disabled={busy || queuePaused === null} onClick={() => onPauseQueue()}><Icon icon={Pause} size={13} /> 暂停队列</button>;
    } else if (activeTask.status === "paused" && onOpenTasks) {
      actions = <button className="btn secondary sm" type="button" onClick={onOpenTasks}>查看任务</button>;
    }
  } else if (pending > 0) {
    view = { state: "pending", label: "", stage: "", timed: false };
  }

  const pendingLabel = `${pending} 个待处理事项`;
  const pendingLink = pending > 0 && (onOpenPending
    ? <button className="gtb-failure-link" type="button" onClick={onOpenPending}>{pendingLabel}</button>
    : <span>{pendingLabel}</span>);
  return <section className="gtb-card task-overview-bar" aria-label="当前任务状态" aria-live="polite">
    <div className="gtb-status"><StatusDot state={view.state} healthy={view.state === "processing"} />{view.state === "pending" ? pendingLink : <span>{view.label}</span>}</div>
    <div className="gtb-info">
      {filename && <div className="gtb-file" title={filename}>{filename}</div>}
      <div className="gtb-stage">{view.stage && <span>{view.stage}</span>}{view.state !== "pending" && pendingLink}{failures > 0 && (onOpenFailed
        ? <button className="gtb-failure-link" type="button" onClick={onOpenFailed}>{failures} 个文件失败</button>
        : <span>{failures} 个文件失败</span>)}{noticeSlot}</div>
    </div>
    {serviceStatus}
    {timerLabel && (elapsed
      ? <time className="gtb-timer" dateTime={elapsed.duration} aria-hidden="true"><span className="gtb-timer-label">{timerLabel}</span><span className="gtb-timer-value">{elapsed.display}</span></time>
      : <span className="gtb-timer-label">{timerLabel}</span>)}
    {actions && <div className="gtb-actions">{actions}</div>}
  </section>;
}
