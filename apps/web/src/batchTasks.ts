import { getTasks } from "./api";
import type { Task } from "./types";

const TASK_IDS_PER_QUERY = 100;
const MAX_CONCURRENT_QUERIES = 2;

export interface BatchTaskSnapshot {
  tasks: Task[];
  missingIds: string[];
}

/** Read the supplied batch, including older tasks, without expanding to history.
 * Absence is confirmed only after every unfiltered ID query succeeds. A failed
 * chunk must never turn an unread task into a deleted task in persisted state.
 * Chunks share the ID scope, not a single database transaction/snapshot time.
 */
export async function readBatchTasks(
  taskIds: readonly string[],
  signal?: AbortSignal,
): Promise<BatchTaskSnapshot> {
  const ids = [...new Set(taskIds)];
  if (ids.length === 0) return { tasks: [], missingIds: [] };
  if (ids.some((id) => !id || [...id].length > 64)) {
    throw new Error("批次中的任务标识无效，请在文件历史中检查。");
  }
  const chunks: string[][] = [];
  for (let index = 0; index < ids.length; index += TASK_IDS_PER_QUERY) {
    chunks.push(ids.slice(index, index + TASK_IDS_PER_QUERY));
  }
  const byId = new Map<string, Task>();
  let next = 0;
  let failed = false;
  const checkAborted = () => { signal?.throwIfAborted(); };
  const worker = async () => {
    while (!failed) {
      checkAborted();
      const chunk = chunks[next++];
      if (!chunk) return;
      try {
        const tasks = await getTasks({ taskIds: chunk, limit: chunk.length, offset: 0, signal });
        checkAborted();
        const expected = new Set(chunk);
        const seen = new Set<string>();
        if (!Array.isArray(tasks) || tasks.some((task) => {
          if (!task || !expected.has(task.id) || seen.has(task.id)) return true;
          seen.add(task.id);
          return false;
        })) {
          throw new Error("读取批次的结果范围不一致，请刷新后重试。");
        }
        for (const task of tasks) byId.set(task.id, task);
      } catch (reason) {
        failed = true;
        throw reason;
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(chunks.length, MAX_CONCURRENT_QUERIES) }, worker));
  checkAborted();
  return {
    tasks: ids.map((id) => byId.get(id)).filter((task): task is Task => task !== undefined),
    missingIds: ids.filter((id) => !byId.has(id)),
  };
}
