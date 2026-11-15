import { useRef, type ReactNode } from "react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { MoreHorizontal, Trash2 } from "lucide-react";
import type { ExtractionTemplate, Task } from "../types";
import { taskCanCancel, taskCanDelete, taskGroup, taskStatusLabel } from "../taskPresentation";
import { taskDisplayName, taskTemplateName } from "../taskNames";
import { parseServerTime } from "../time";
import { Icon } from "./Icon";
import { StatusDot, type StatusDotState } from "./StatusDot";
import type { PendingDuplicate } from "./PendingDuplicateDialog";

interface RowActions {
  open?: () => void;
  data?: () => void;
  cancel: () => void;
  delete: () => void;
  retry: () => void;
  details: () => void;
}

function TaskMoreMenu({ name, disabled, children }: {
  name: string; disabled: boolean; children: (choose: (action: () => void) => () => void) => ReactNode;
}) {
  const trigger = useRef<HTMLButtonElement>(null);
  const handoff = useRef<(() => void) | null>(null);
  const choose = (action: () => void) => () => { handoff.current = action; };
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button ref={trigger} className="btn ghost sm icon-only" type="button" disabled={disabled} aria-label={`更多操作 ${name}`}>
          <Icon icon={MoreHorizontal} size={17} />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content className="record-menu" align="end" sideOffset={6} collisionPadding={12}
          onCloseAutoFocus={event => {
            const action = handoff.current;
            if (!action) return;
            event.preventDefault();
            handoff.current = null;
            trigger.current?.focus();
            action();
          }}>
          {children(choose)}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

export function TaskListRow({ task, templates, selected, disabled, primary, onSelect, actions }: {
  task: Task;
  templates: ExtractionTemplate[];
  selected: boolean;
  disabled: boolean;
  primary?: { label: string; action: () => void; disabled?: boolean };
  onSelect: () => void;
  actions: RowActions;
}) {
  const name = taskDisplayName(task);
  const group = taskGroup(task);
  const menuActions = primary?.label === "对照" || primary?.label === "核对" ? { ...actions, open: undefined } : actions;
  const hasMenu = menuActions.open || (actions.data && task.status === "completed") ||
    task.status === "failed" || taskCanCancel(task) || taskCanDelete(task);
  const dotState: StatusDotState = group === "pending" ? "pending"
    : group === "active" ? task.status === "paused" ? "paused"
      : ["created", "queued"].includes(task.status) ? "queued" : "processing"
    : group;
  const template = taskTemplateName(task, task.template_id
    ? templates.find(item => item.id === task.template_id)?.name
    : undefined);
  return <TaskRow id={task.id} group={group} name={name} status={taskStatusLabel(task)} dotState={dotState}
    template={template} updatedAt={task.updated_at} selected={selected}
    selection={<input type="checkbox" aria-label={`选择 ${name}`} disabled={disabled} checked={selected} onChange={onSelect} />}
    primary={primary && { ...primary, disabled: disabled || primary.disabled,
      title: primary.disabled && task.status === "paused" ? "先确认整个队列的运行状态，再继续此任务" : undefined }}
    menu={hasMenu ? <TaskMoreMenu name={name} disabled={disabled}>{choose => <>
      {menuActions.open && <DropdownMenu.Item onSelect={choose(menuActions.open)}>查看原件与结果</DropdownMenu.Item>}
      {actions.data && task.status === "completed" && <DropdownMenu.Item onSelect={choose(actions.data)}>查看数据</DropdownMenu.Item>}
      {task.status === "failed" && <>
        <DropdownMenu.Item onSelect={choose(actions.retry)}>重试选项</DropdownMenu.Item>
        <DropdownMenu.Item onSelect={choose(actions.details)}>诊断详情</DropdownMenu.Item>
      </>}
      {taskCanCancel(task) && <DropdownMenu.Item onSelect={choose(actions.cancel)}>取消任务</DropdownMenu.Item>}
      {taskCanDelete(task) && <DeleteMenuItem onSelect={choose(actions.delete)} />}
    </>}</TaskMoreMenu> : undefined} />;
}

function DeleteMenuItem({ onSelect }: { onSelect: () => void }) {
  return <DropdownMenu.Item className="danger" onSelect={onSelect}><Icon icon={Trash2} size={15} />删除…</DropdownMenu.Item>;
}

export function DuplicateTaskRow({ item, templates, onOpen, selected, disabled, onSelect, onDelete }: {
  item: PendingDuplicate; templates: ExtractionTemplate[]; onOpen?: (item: PendingDuplicate) => void;
  selected: boolean; disabled: boolean; onSelect: () => void; onDelete: () => void;
}) {
  const template = item.selection === "smart" ? "智能匹配"
    : templates.find(template => template.id === item.selection)?.name ?? "模板名称待确认";
  return <TaskRow id={item.id} group="pending" name={item.name} status="重复待确认" dotState="pending"
    template={template} updatedAt={item.createdAt} selected={selected}
    selection={<input type="checkbox" aria-label={`选择重复待确认 ${item.name}`} disabled={disabled} checked={selected} onChange={onSelect} />}
    primary={{ label: "处理", ariaLabel: `处理重复文件 ${item.name}`, action: () => onOpen?.(item), disabled: disabled || !onOpen }}
    menu={<TaskMoreMenu name={item.name} disabled={disabled}>{choose => <DeleteMenuItem onSelect={choose(onDelete)} />}</TaskMoreMenu>} />;
}

function TaskRow({ id, group, name, status, dotState, template, updatedAt, selected, selection, primary, menu }: {
  id: string; group: string; name: string; status: string; dotState: StatusDotState; template: string; updatedAt: string;
  selected?: boolean; selection?: ReactNode; menu?: ReactNode;
  primary?: { label: string; action: () => void; disabled?: boolean; title?: string; ariaLabel?: string };
}) {
  const time = parseServerTime(updatedAt);
  const timeLabel = Number.isFinite(time)
    ? new Date(time).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false })
    : "时间待确认";

  return (
    <tr data-task-id={id} data-task-group={group} className={selected ? "is-selected" : undefined}>
      <td>{selection}</td>
      <td>
        <div className="task-file-name" title={name}>{name}</div>
      </td>
      <td><span className="task-state"><StatusDot state={dotState} />{status}</span></td>
      <td><span className="task-template-name" title={template}>{template}</span></td>
      <td><time dateTime={updatedAt}>{timeLabel}</time></td>
      <td>
        <div className="task-row-actions">
          {primary && <button type="button" className="btn secondary sm task-primary-action" aria-label={primary.ariaLabel ?? `${primary.label} ${name}`} disabled={primary.disabled}
            title={primary.title} onClick={primary.action}>
            {primary.label}
          </button>}
          {menu}
        </div>
      </td>
    </tr>
  );
}
