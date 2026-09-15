import { useEffect, useRef, useState } from "react";
import { Check, FileSearch, Pause, Play, Trash2 } from "lucide-react";
import { Icon } from "./Icon";
import { parseServerTime } from "../time";
import type { Task, TaskStatus } from "../types";

const activeStatuses: TaskStatus[] = [
  "created",
  "queued",
  "processing",
  "validating",
  "paused",
];

/** 本次处理尝试的开始时间：重试/恢复后由后端重置，前端据此从零重新计时 */
function attemptStart(task: Task): string {
  return task.started_at ?? task.created_at;
}

function elapsedFrom(start: string | number, endTime: number | string) {
  const end = typeof endTime === "number" ? endTime : parseServerTime(endTime);
  const startMs = typeof start === "number" ? start : parseServerTime(start);
  const seconds = Math.max(0, Math.floor((end - startMs) / 1000));
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

interface ActiveVisual {
  className: string;
  label: string;
  stage: string;
  timed: boolean;
}

function activeVisual(status: TaskStatus): ActiveVisual {
  switch (status) {
    case "processing":
      return { className: "processing", label: "处理中", stage: "识别中…", timed: true };
    case "validating":
      return { className: "processing", label: "校验中", stage: "检查规则…", timed: true };
    case "paused":
      return { className: "paused", label: "已暂停", stage: "可在状态监控恢复全部", timed: false };
    case "created":
    case "queued":
      // 任务已写入队列但尚未被消费者取走；不计时（真正开始处理时才从 0 清零计）
      return { className: "queued", label: "入队中", stage: "任务正在入队", timed: false };
    default:
      return { className: "queued", label: "等待中", stage: "", timed: false };
  }
}

export function GlobalTaskCard({
  tasks,
  onPause,
  onResume,
  onDelete,
  onOpenPending,
  pendingCount,
  reviewCount,
}: {
  tasks: Task[];
  onPause?: (taskId: string) => void;
  /** 恢复暂停的当前任务，放回队列继续处理 */
  onResume?: (taskId: string) => void;
  /** 删除任务（含原文件）：仅对可删除状态（如已暂停）显示 */
  onDelete?: (taskId: string) => void;
  onOpenPending?: () => void;
  pendingCount?: number;
  reviewCount?: number;
}) {
  const [now, setNow] = useState(() => Date.now());
  // 计时锚点 = 任务真正开始处理的时刻：任务进入处理中/校验中时从 0 清零重新计，
  // 排队等待时间不计入；批量处理时每个任务独立计时。
  const anchorRef = useRef<Map<string, number>>(new Map());
  const stateRef = useRef<
    Map<string, { status: TaskStatus; active: boolean; attempt: string }>
  >(new Map());
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
    ?? tasks.find((task) => activeStatuses.includes(task.status));
  const waitingForTemplate = tasks.filter(
    (task) => task.status === "waiting_for_template" || (task.status === "completed" && (task.archive_pending || ["failed", "needs_rebind"].includes(task.file_export?.status ?? ""))),
  );
  const waitingForReview = tasks.filter((task) => task.status === "needs_review");

  // 任务条只展示一个任务：正在处理 > 已暂停 > 最早排队 > 其他活动任务；
  // 文案与计时由 activeVisual 决定（排队中不计时，处理中从 0 计时）。
  const visual = activeTask ? activeVisual(activeTask.status) : null;

  // 渲染期同步各任务状态，维护计时锚点（ref 读写无渲染副作用）。
  // 计时是每个任务独立的：任务真正开始处理（processing/validating）那一刻
  // 从 0 清零重新计，排队等待的时间不计入；首次出现即处理中（含乐观上传）
  // 则从当前可见时刻起算，保证首帧从 00:00 开始。
  {
    const activeIds = new Set<string>();
    for (const t of tasks) {
      if (activeStatuses.includes(t.status)) activeIds.add(t.id);
    }
    for (const t of tasks) {
      const isActive = activeIds.has(t.id);
      const isProcessing =
        t.status === "processing" || t.status === "validating";
      const prev = stateRef.current.get(t.id);
      const prevProcessing =
        prev !== undefined
        && (prev.status === "processing" || prev.status === "validating");
      const attempt = attemptStart(t);
      if (prev === undefined) {
        if (isActive && isProcessing) anchorRef.current.set(t.id, Date.now());
      } else if (isProcessing && (!prevProcessing || prev.attempt !== attempt)) {
        // 任务真正开始处理时从零重新计时
        anchorRef.current.set(t.id, Date.now());
      }
      stateRef.current.set(t.id, { status: t.status, active: isActive, attempt });
    }
  }
  const timerAnchor = activeTask
    ? anchorRef.current.get(activeTask.id) ?? attemptStart(activeTask)
    : null;

  useEffect(() => {
    if (!activeTask || !visual?.timed) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [activeTask?.id, activeTask?.status, visual?.timed]);

  // 处理中/排队/暂停：完整任务条（文件名 + 状态 + 计时 + 暂停/恢复 + 取消）
  if (activeTask && visual) {
    return (
      <section className={`gtb-card ${visual.className}`} aria-label="当前任务状态" aria-live="polite">
        <div className="gtb-status">
          <span className={`gtb-dot ${visual.className}`} aria-hidden="true" />
          <span>{visual.label}</span>
        </div>
        <div className="gtb-info">
          <div className="gtb-file" title={activeTask.filename}>
            {activeTask.filename}
          </div>
          <div className="gtb-stage">{visual.stage}</div>
        </div>
        <time
          aria-hidden="true"
          className="gtb-timer"
          dateTime={visual.timed ? attemptStart(activeTask) : activeTask.updated_at}
        >
          {visual.timed
            ? `已用 ${elapsedFrom(timerAnchor ?? attemptStart(activeTask), now)}`
            : activeTask.status === "paused"
              ? `已用 ${elapsedFrom(timerAnchor ?? attemptStart(activeTask), activeTask.updated_at)}`
              : "等待开始"}
        </time>
        <div className="gtb-actions">
          {(activeTask.status === "processing" || activeTask.status === "queued") && onPause && (
            <button className="btn primary sm" type="button" title="暂停整个队列" onClick={() => onPause(activeTask.id)}>
              <Icon icon={Pause} size={13} /> 暂停队列
            </button>
          )}
          {activeTask.status === "paused" && onDelete && (
            <button
              className="btn ghost sm danger"
              type="button"
              title="删除此任务（处理未完成，直接删除不确认）"
              onClick={() => onDelete(activeTask.id)}
            >
              <Icon icon={Trash2} size={13} /> 删除
            </button>
          )}
          {activeTask.status === "paused" && onResume && (
            <button className="btn primary sm" type="button" title="启动整个队列" onClick={() => onResume(activeTask.id)}>
              <Icon icon={Play} size={13} /> 启动队列
            </button>
          )}
        </div>
      </section>
    );
  }

  // 待选模板：提醒不计时
  if ((pendingCount ?? waitingForTemplate.length) > 0) {
    return (
      <section className="gtb-card" aria-label="当前任务状态" aria-live="polite">
        <Icon icon={FileSearch} size={17} />
        <div className="gtb-info">
          <div className="gtb-file">有待处理事项</div>
          <div className="gtb-stage">{pendingCount ?? waitingForTemplate.length} 份文件 · 此时不计处理耗时</div>
        </div>
        {onOpenPending && <button className="btn secondary sm" type="button" onClick={onOpenPending}>查看事项</button>}
      </section>
    );
  }

  if ((reviewCount ?? waitingForReview.length) > 0) {
    return (
      <section className="gtb-card" aria-label="当前任务状态" aria-live="polite">
        <Icon icon={Check} size={17} />
        <div className="gtb-info">
          <div className="gtb-file">等待你确认</div>
          <div className="gtb-stage">{reviewCount ?? waitingForReview.length} 份文件需要审核</div>
        </div>
      </section>
    );
  }

  return (
    <section className="gtb-card" aria-label="当前任务状态">
      <div className="gtb-status">
        <span className="gtb-dot" aria-hidden="true" />
        <span>当前空闲</span>
      </div>
      <div className="gtb-info">
        <div className="gtb-stage">可以放入新的文件</div>
      </div>
    </section>
  );
}
