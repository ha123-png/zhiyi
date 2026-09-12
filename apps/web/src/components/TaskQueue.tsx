import {
  Activity,
  AlertTriangle,
  Ban,
  CheckCircle2,
  ChevronDown,
  HelpCircle,
  LoaderCircle,
  RotateCcw,
  Trash2,
} from "lucide-react";
import { useState } from "react";
import { Icon } from "./Icon";
import { PartialInputAction } from "./InputScopeDetails";
import type { Task, TaskStatus } from "../types";

interface TaskQueueProps {
  tasks: Task[];
  selectedId: string;
  loading: boolean;
  onSelect: (taskId: string) => void;
  onCancel: (taskId: string) => void;
  onRetry: (taskId: string) => void;
  onDelete?: (taskId: string) => void;
  onSelectTemplate?: (taskId: string, templateId: string) => void;
}

type CardTone = "active" | "warning" | "success" | "danger";

interface Group {
  key: string;
  label: string;
  statuses: TaskStatus[];
  empty: string;
  icon: typeof Activity;
  tone: CardTone;
  /** 设计稿：仅 active 不显示计数 badge，其余三组都显示 */
  showCount: boolean;
  /** waiting_for_template 任务的匹配框归属组 */
  hasMatchBox?: boolean;
}

const groups: Group[] = [
  {
    key: "active",
    label: "活动任务",
    statuses: ["created", "queued", "processing", "validating", "paused"],
    empty: "暂无正在处理的文件",
    icon: Activity,
    tone: "active",
    showCount: false,
  },
  {
    key: "review",
    label: "待处理事项",
    statuses: ["waiting_for_template", "needs_review"],
    empty: "暂无需要你处理的文件",
    icon: HelpCircle,
    tone: "warning",
    showCount: true,
    hasMatchBox: true,
  },
  {
    key: "completed",
    label: "最近完成",
    statuses: ["completed"],
    empty: "暂无成功处理的文件",
    icon: CheckCircle2,
    tone: "success",
    showCount: true,
  },
  {
    key: "failed",
    label: "失败任务",
    statuses: ["failed", "cancelled"],
    empty: "暂无失败的文件",
    icon: AlertTriangle,
    tone: "danger",
    showCount: true,
  },
];

const templateNames = {
  smart: "智能匹配",
  manual: "手动选择",
  invoice: "发票",
  delivery: "送货单",
};

const statusHelp: Record<TaskStatus, string> = {
  created: "文件已安全保存",
  waiting_for_template: "需要你确认，请查看待处理事项",
  queued: "已排队，将按顺序处理",
  processing: "正在识别原文件",
  validating: "正在执行字段与领域校验",
  needs_review: "结果已生成，等待核对",
  completed: "结果已写入对应数据表",
  paused: "处理已暂停，进度已保留",
  cancelled: "任务已取消，原文件仍保留",
  failed: "处理未完成，可直接重试",
};

/** 设计稿 badge 文案：精简到 2-4 字 */
const badgeText: Partial<Record<TaskStatus, string>> = {
  created: "已创建",
  queued: "排队中",
  processing: "处理中",
  validating: "校验中",
  paused: "已暂停",
  waiting_for_template: "待处理",
  needs_review: "待审核",
  completed: "完成",
  cancelled: "已取消",
  failed: "失败",
};

/** 设计稿 badge 语义类 */
function badgeClassFor(status: TaskStatus): string {
  switch (status) {
    case "processing":
    case "validating":
    case "queued":
      return "live";
    case "waiting_for_template":
    case "needs_review":
      return "warn";
    case "completed":
      return "success";
    case "failed":
    case "cancelled":
      return "danger";
    default:
      return "";
  }
}

export function TaskQueue({
  tasks,
  selectedId,
  loading,
  onSelect,
  onCancel,
  onRetry,
  onDelete,
  onSelectTemplate,
}: TaskQueueProps) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [templateSelection, setTemplateSelection] = useState<
    Record<string, string>
  >({});

  function toggle(key: string) {
    setCollapsed((current) => ({ ...current, [key]: !current[key] }));
  }

  function renderGroup(group: Group, index: number) {
    const groupTasks = tasks.filter((task) =>
      group.statuses.includes(task.status),
    );
    const isCollapsed = !!collapsed[group.key];
    return (
      <div
        className={`task-queue-card is-${group.tone}${isCollapsed ? " is-collapsed" : ""}`}
        key={group.key}
        style={{ "--i": index } as React.CSSProperties}
      >
        <div
          aria-expanded={!isCollapsed}
          className={`task-queue-header collapsible${isCollapsed ? " is-collapsed" : ""}`}
          onClick={() => toggle(group.key)}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              toggle(group.key);
            }
          }}
          role="button"
          tabIndex={0}
        >
          <h3>
            <Icon icon={group.icon} size={19} />
            {group.label}
          </h3>
          {group.showCount ? (
            <span className={`badge ${group.tone === "warning" ? "warn" : group.tone === "success" ? "success" : group.tone === "danger" ? "danger" : ""}`}>
              {loading ? "—" : groupTasks.length}
            </span>
          ) : null}
          <Icon className="card-caret" icon={ChevronDown} size={16} />
        </div>

        <div className="task-list-body">
          <div className="task-list">
            {!loading && groupTasks.length === 0 ? (
              <div className="task-empty">{group.empty}</div>
            ) : null}
            {groupTasks.map((task) => {
              const isWaiting = task.status === "waiting_for_template";
              return (
                <div
                  className={`task-item${selectedId === task.id ? " selected" : ""}${isWaiting ? " has-match-box" : ""}`}
                  key={task.id}
                >
                  <div className="task-row-top">
                    <button
                      className="task-name"
                      onClick={() => onSelect(task.id)}
                      title={task.filename}
                      type="button"
                    >
                      {task.filename}
                    </button>
                    <span className={`badge ${badgeClassFor(task.status)}`}>
                      {badgeText[task.status] ?? statusHelp[task.status]}
                    </span>
                  </div>

                  {isWaiting && task.pending_reason === "input_scope" ? <PartialInputAction task={task} /> : isWaiting ? (
                    <div className="match-failed-box">
                      <p>智能匹配未命中，请手动选择模板，或重试匹配。</p>
                      <div className="row">
                        <select
                          aria-label="选择模板"
                          className="form-select match-failed-select"
                          disabled={task.candidate_templates.length === 0}
                          onChange={(e) =>
                            setTemplateSelection((s) => ({
                              ...s,
                              [task.id]: e.target.value,
                            }))
                          }
                          style={{ flex: 1 }}
                          value={templateSelection[task.id] ?? ""}
                        >
                          <option value="" disabled>
                            {task.candidate_templates.length === 0
                              ? "暂无可用模板"
                              : "选择模板"}
                          </option>
                          {task.candidate_templates.map((ct) => (
                            <option key={ct.id} value={ct.id}>
                              {ct.name}
                            </option>
                          ))}
                        </select>
                        <button
                          className="btn primary sm"
                          disabled={!templateSelection[task.id]}
                          onClick={() =>
                            templateSelection[task.id] &&
                            onSelectTemplate?.(task.id, templateSelection[task.id])
                          }
                          type="button"
                        >
                          确认
                        </button>
                        <button
                          className="btn sm"
                          onClick={() => onRetry(task.id)}
                          type="button"
                        >
                          重试
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="task-row-bottom">
                      <div className="task-meta">
                        <span>{templateNames[task.template_mode]}</span>
                        <span>·</span>
                        <span>{statusHelp[task.status]}</span>
                        {task.duplicate_of_task_id ? (
                          <>
                            <span>·</span>
                            <span>与已有文件内容相同</span>
                          </>
                        ) : null}
                      </div>
                      <div className="task-actions">
                        {["created", "queued", "processing", "validating", "paused"].includes(
                          task.status,
                        ) ? (
                          <button
                            aria-label={`取消 ${task.filename}`}
                            className="btn ghost sm"
                            onClick={() => onCancel(task.id)}
                            title="取消任务"
                            type="button"
                          >
                            <Icon icon={Ban} size={12} />
                          </button>
                        ) : null}
                        {task.status === "failed" ? (
                          <>
                            <button
                              aria-label={`重试 ${task.filename}`}
                              className="btn ghost sm"
                              onClick={() => onRetry(task.id)}
                              title="重新进入处理队列"
                              type="button"
                            >
                              <Icon icon={RotateCcw} size={12} />
                            </button>
                            {onDelete ? (
                              <button
                                aria-label={`删除 ${task.filename}`}
                                className="btn ghost sm danger"
                                onClick={() => onDelete(task.id)}
                                title="删除任务"
                                type="button"
                              >
                                <Icon icon={Trash2} size={12} />
                              </button>
                            ) : null}
                          </>
                        ) : null}
                        {task.status === "completed" ? (
                          <>
                            <button
                              className="btn ghost sm"
                              onClick={() => onSelect(task.id)}
                              title="对照原文件"
                              type="button"
                            >
                              对照
                            </button>
                            <button
                              className="btn ghost sm"
                              onClick={() => onSelect(task.id)}
                              title="查看数据"
                              type="button"
                            >
                              数据
                            </button>
                          </>
                        ) : null}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
            {loading ? (
              <div className="task-empty is-loading">
                <Icon icon={LoaderCircle} size={17} />
                正在读取任务状态
              </div>
            ) : null}
          </div>
        </div>
      </div>
    );
  }

  return (
    <section className="task-queue-grid" aria-label="任务状态分组" aria-live="polite">
      <div className="task-col">
        {groups.slice(0, 2).map((group, index) => renderGroup(group, index))}
      </div>
      <div className="task-col">
        {groups.slice(2).map((group, index) => renderGroup(group, index + 2))}
      </div>
    </section>
  );
}
