import { useCallback, useEffect, useRef, useState } from "react";
import { getQueuePaused, getTasks, getTaskSummary, subscribeTaskEvents, taskEventsConnected } from "./api";
import type { Task, TaskSummary } from "./types";

export interface TaskOverview {
  tasks: Task[];
  summary: TaskSummary | null;
  queuePaused: boolean | null;
  state: "loading" | "ready" | "error";
  error: string | null;
  revision: number;
  refresh: () => Promise<void>;
}

/** One owner for global task facts; the task page keeps only its current list query. */
export function useTaskOverview(enabled = true): TaskOverview {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [summary, setSummary] = useState<TaskSummary | null>(null);
  const [queuePaused, setQueuePaused] = useState<boolean | null>(null);
  const [state, setState] = useState<TaskOverview["state"]>("loading");
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const generation = useRef(0);
  const refresh = useCallback(async () => {
    if (!enabled) return;
    const request = ++generation.current;
    try {
      const [active, counts, paused] = await Promise.all([
        getTasks({ limit: 1000, group: "active" }), getTaskSummary(), getQueuePaused().catch(() => null),
      ]);
      if (request !== generation.current) return;
      setTasks(active); setSummary(counts); setQueuePaused(paused); setError(null); setState("ready");
      setRevision(value => value + 1);
    } catch (reason) {
      if (request !== generation.current) return;
      setError(reason instanceof Error ? reason.message : "任务概览读取失败"); setState("error");
    }
  }, [enabled]);
  useEffect(() => {
    if (!enabled) return;
    void refresh();
    const unsubscribe = typeof EventSource === "undefined" ? undefined : subscribeTaskEvents(() => void refresh());
    let lastRefresh = Date.now();
    const visible = () => { if (!document.hidden) { lastRefresh = Date.now(); void refresh(); } };
    const assistantChanged = (event: Event) => { if ((event as CustomEvent<{task_id?: string}>).detail?.task_id) void refresh(); };
    document.addEventListener("visibilitychange", visible);
    window.addEventListener("zhiyi:assistant-changed", assistantChanged);
    const timer = window.setInterval(() => {
      if (!document.hidden && (!taskEventsConnected() || Date.now() - lastRefresh >= 30000)) {
        lastRefresh = Date.now(); void refresh();
      }
    }, 5000);
    return () => { generation.current += 1; unsubscribe?.(); clearInterval(timer);
      document.removeEventListener("visibilitychange", visible); window.removeEventListener("zhiyi:assistant-changed", assistantChanged); };
  }, [enabled, refresh]);
  return { tasks, summary, queuePaused, state, error, revision, refresh };
}
