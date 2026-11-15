import { afterEach, describe, expect, test, vi } from "vitest";

import { getTasks } from "./api";
import { readBatchTasks } from "./batchTasks";
import type { Task } from "./types";

vi.mock("./api", () => ({ getTasks: vi.fn() }));
const query = vi.mocked(getTasks);
const task = (id: string) => ({ id, status: "processing", updated_at: "2026-10-30" }) as Task;
const ids = (count: number) => Array.from({ length: count }, (_, index) => `task-${index}`);
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
afterEach(() => { query.mockReset(); });

describe("readBatchTasks", () => {
  test("empty scope never asks for history", async () => {
    await expect(readBatchTasks([])).resolves.toEqual({ tasks: [], missingIds: [] });
    expect(query).not.toHaveBeenCalled();
  });

  test("queries the old IDs directly, deduplicates, and confirms absence on full success", async () => {
    query.mockResolvedValue([task("old-b"), task("old-a")]);
    await expect(readBatchTasks(["old-a", "old-b", "old-a", "deleted"])).resolves.toEqual({
      tasks: [task("old-a"), task("old-b")], missingIds: ["deleted"],
    });
    expect(query).toHaveBeenCalledExactlyOnceWith({
      taskIds: ["old-a", "old-b", "deleted"], limit: 3, offset: 0, signal: undefined,
    });
  });

  test("chunks a >200 batch with at most two concurrent requests and retains input order", async () => {
    const waits = [deferred<Task[]>(), deferred<Task[]>(), deferred<Task[]>()];
    let active = 0;
    let highestActive = 0;
    query.mockImplementation(() => {
      const index = query.mock.calls.length - 1;
      highestActive = Math.max(highestActive, ++active);
      return waits[index].promise.finally(() => { active--; });
    });
    const selected = ids(205);
    const reading = readBatchTasks([...selected, selected[0]]);
    expect(query).toHaveBeenCalledTimes(2);
    expect(query.mock.calls[0][0]?.taskIds).toEqual(selected.slice(0, 100));
    expect(query.mock.calls[1][0]?.taskIds).toEqual(selected.slice(100, 200));
    waits[1].resolve(selected.slice(100, 200).reverse().map(task));
    await vi.waitFor(() => expect(query).toHaveBeenCalledTimes(3));
    expect(query.mock.calls[2][0]).toEqual({ taskIds: selected.slice(200), limit: 5, offset: 0, signal: undefined });
    waits[2].resolve(selected.slice(200).map(task));
    waits[0].resolve(selected.slice(0, 100).map(task));
    const snapshot = await reading;
    expect(snapshot.tasks.map((value) => value.id)).toEqual(selected);
    expect(snapshot.missingIds).toEqual([]);
    expect(highestActive).toBe(2);
  });

  test("one failed chunk rejects without partial success or scheduling the unread tail", async () => {
    const first = deferred<Task[]>();
    const second = deferred<Task[]>();
    query.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const reading = readBatchTasks(ids(205));
    const failed = expect(reading).rejects.toThrow("backend unavailable");
    first.reject(new Error("backend unavailable"));
    await failed;
    second.resolve([]);
    await Promise.resolve();
    expect(query).toHaveBeenCalledTimes(2);
  });

  test("an endpoint 404 is a read failure, not confirmation that every task was deleted", async () => {
    const failure = Object.assign(new Error("not found"), { status: 404 });
    query.mockRejectedValue(failure);
    await expect(readBatchTasks(["old-file"])).rejects.toBe(failure);
  });

  test.each([[task("unrelated")], [task("old-file"), task("old-file")]])(
    "rejects a foreign/duplicate result instead of returning a false scope", async (...values) => {
      query.mockResolvedValue(values);
      await expect(readBatchTasks(["old-file"])).rejects.toThrow("结果范围不一致");
    },
  );

  test("a cancelled read cannot reconcile absence after a late response", async () => {
    const controller = new AbortController();
    const response = deferred<Task[]>();
    query.mockReturnValue(response.promise);
    const reading = readBatchTasks(["old-file"], controller.signal);
    const cancelled = expect(reading).rejects.toMatchObject({ name: "AbortError" });
    controller.abort();
    response.resolve([]);
    await cancelled;
    expect(query.mock.calls[0][0]?.signal).toBe(controller.signal);
  });

  test("already aborted or invalid identifiers do not issue any requests", async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(readBatchTasks(["old-file"], controller.signal)).rejects.toMatchObject({ name: "AbortError" });
    await expect(readBatchTasks([""])).rejects.toThrow("任务标识无效");
    await expect(readBatchTasks(["x".repeat(65)])).rejects.toThrow("任务标识无效");
    expect(query).not.toHaveBeenCalled();
  });
});
