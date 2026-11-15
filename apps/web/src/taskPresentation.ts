import type { BatchRetryResult, Task, TaskGroup, TaskStatus, TaskSummary } from "./types";

/** Display groups are disjoint views of server task facts, not another state machine. */
export const TASK_GROUPS: ReadonlyArray<{ id: TaskGroup; label: string }> = [
  { id: "all", label: "全部" }, { id: "active", label: "进行中" },
  { id: "completed", label: "已完成" }, { id: "pending", label: "待确认" },
  { id: "failed", label: "失败" }, { id: "cancelled", label: "已取消" },
];
export const ACTIVE_TASK_STATUSES: readonly TaskStatus[] = ["created", "queued", "processing", "validating", "paused"];
export const taskExportIsRunning = (task: Task) => task.status === "completed"
  && ["awaiting_confirmation", "pending", "exporting"].includes(task.file_export?.status ?? "");
export const taskIsActive = (task: Task) => ACTIVE_TASK_STATUSES.includes(task.status) || taskExportIsRunning(task);
export const taskNeedsExportAction = (task: Task) => task.status === "completed"
  && ["failed", "needs_rebind"].includes(task.file_export?.status ?? "");
export function taskGroup(task: Task): Exclude<TaskGroup, "all"> {
  if (taskIsActive(task)) return "active";
  if (task.status === "failed") return "failed";
  if (task.status === "cancelled") return "cancelled";
  if (task.status === "waiting_for_template" || task.status === "needs_review" || taskNeedsExportAction(task)) return "pending";
  return "completed";
}
export function taskStatusLabel(task: Task): string {
  if (taskExportIsRunning(task)) {
    const operation = task.file_export?.mode === "move" ? "归档" : "导出";
    return task.file_export?.status === "exporting" ? `正在${operation}` : `等待${operation}`;
  }
  if (taskNeedsExportAction(task)) return task.file_export?.mode === "move" ? "归档待处理" : "副本待处理";
  if (task.status === "waiting_for_template") {
    if (task.pending_action === "confirm_input_scope" || task.pending_reason === "input_scope") return "待确认范围";
    if (task.pending_action === "confirm_target_table" || task.pending_reason === "target_table") return "待选择数据表";
    return task.pending_action === "select_template" ? "待选择模板" : "待确认";
  }
  return ({ created: "等待中", queued: "等待中", processing: "处理中", validating: "校验中", paused: "已暂停",
    needs_review: "待核对", completed: "已完成", failed: "失败", cancelled: "已取消" } as const)[task.status];
}
export function taskGroupCounts(summary: TaskSummary | null, tasks: Task[]): Record<TaskGroup, number> {
  if (summary?.group_counts) return summary.group_counts;
  // Old fixtures and older servers have no grouped counts. Never infer totals from a truncated page.
  return { all: summary?.total ?? tasks.length,
    active: tasks.filter(taskIsActive).length, completed: summary?.completed ?? 0,
    pending: (summary?.waiting_for_action ?? 0) + (summary?.needs_review ?? 0) + (summary?.pending_exports ?? 0),
    failed: summary?.failed ?? 0, cancelled: summary?.cancelled ?? 0 };
}
export const taskCanDelete = (task: Task) => ["completed", "needs_review", "failed", "cancelled", "waiting_for_template", "paused"].includes(task.status);
export const taskCanCancel = (task: Task) => ["created", "queued", "processing", "paused", "waiting_for_template", "needs_review", "failed"].includes(task.status);
export const taskCanRetry = (task: Task) => task.status === "failed" || task.status === "cancelled";
export function batchRetryNotice(result: BatchRetryResult): string {
  const base = `已重新提交 ${result.retried.length} 个任务。`;
  if (!result.skipped) return base;
  const capacity = result.skipped_tasks?.filter(item => item.reason === "capacity").length ?? 0;
  const detail = capacity ? `其中 ${capacity} 个受队列容量限制。` : "";
  return `${base}${result.skipped} 个未重试。${detail}`;
}
