import { taskDisplayName } from "../taskNames";
import type { Task } from "../types";
import { ConfirmDialog } from "./ConfirmDialog";
import type { PendingDuplicate } from "./PendingDuplicateDialog";

interface DeleteTaskDialogProps {
  tasks: readonly Task[];
  duplicates?: readonly PendingDuplicate[];
  busy: boolean;
  error: string | null;
  onConfirm: () => void;
  onClose: () => void;
}

export function DeleteTaskDialog({ tasks, duplicates = [], busy, error, onConfirm, onClose }: DeleteTaskDialogProps) {
  const single = tasks.length === 1 && !duplicates.length ? tasks[0] : undefined;
  const hasTasks = tasks.length > 0;
  return (
    <ConfirmDialog
      open={tasks.length + duplicates.length > 0}
      title={single ? "删除文件记录" : hasTasks ? "删除所选文件和数据" : "删除所选记录"}
      titleClassName={hasTasks && !single ? "confirm-danger-title" : undefined}
      confirmText={single ? single.filename : hasTasks ? "删除所选" : undefined}
      buttonLabel="确认删除"
      busy={busy}
      error={error}
      onConfirm={onConfirm}
      onClose={onClose}
      description={
        <>
          <p>{single
            ? `将永久删除「${single.filename}」的处理记录、提取结果、平台内部原件、当前数据和文件修订历史。外部归档文件和独立数据保留。`
            : hasTasks ? "将永久删除所选任务的处理记录、平台内部原件、当前数据和文件修订历史。" : "确认删除以下记录？"}</p>
          {!single && <ul className="task-delete-names">
            {tasks.map(task => <li key={task.id}>{taskDisplayName(task)}</li>)}
            {duplicates.map(item => <li key={`duplicate:${item.id}`}>{item.name}</li>)}
          </ul>}
        </>
      }
    />
  );
}
