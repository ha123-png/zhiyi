import { afterEach, expect, test, vi } from "vitest";

import { ApiError, getTasks, getTaskSummary } from "./api";

afterEach(() => { vi.unstubAllGlobals(); });

test("task IDs use repeated encoded query values and preserve all existing filters", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response("[]", { status: 200 }));
  vi.stubGlobal("fetch", fetcher);
  const controller = new AbortController();
  await getTasks({ taskIds: ["a + &", "b"], limit: 2, offset: 1, activeOnly: true,
    exportPending: true, status: "queued", search: "a%", templateId: "template", since: "2026-10-30", signal: controller.signal });
  const [url, init] = fetcher.mock.calls[0];
  const params = new URL(url, "http://127.0.0.1").searchParams;
  expect(params.getAll("task_ids")).toEqual(["a + &", "b"]);
  expect(Object.fromEntries([...params].filter(([key]) => key !== "task_ids"))).toEqual({
    limit: "2", offset: "1", active_only: "true", export_pending: "true", status: "queued",
    search: "a%", template_id: "template", since: "2026-10-30",
  });
  expect(init.signal).toBe(controller.signal);
});

test("explicit empty task scope never becomes an unfiltered query", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  await expect(getTasks({ taskIds: [] })).resolves.toEqual([]);
  expect(fetcher).not.toHaveBeenCalled();
});

test("old callers still get the existing unfiltered paginated API", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response("[]", { status: 200 }));
  vi.stubGlobal("fetch", fetcher);
  await getTasks({ limit: 200 });
  expect(new URL(fetcher.mock.calls[0][0], "http://127.0.0.1").searchParams.toString()).toBe("limit=200");
});

test("monitor terminal windows stay separate from history since in list and summary", async () => {
  const fetcher = vi.fn().mockImplementation(async () => new Response("[]", { status: 200 }));
  vi.stubGlobal("fetch", fetcher);
  const scope = { search: "old & failed", since: "2026-10-01T00:00:00Z", terminalSince: "2026-11-01T08:00:00+08:00" };
  await getTasks(scope);
  await getTaskSummary(scope);
  for (const [input] of fetcher.mock.calls) {
    const params = new URL(String(input), "http://127.0.0.1").searchParams;
    expect(Object.fromEntries(params)).toEqual({ search: scope.search, since: scope.since, terminal_since: scope.terminalSince });
  }
});

test("read failures retain HTTP identity so callers can preserve batch state", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response('{"detail":"暂时不可用"}', { status: 503 }));
  vi.stubGlobal("fetch", fetcher);
  await expect(getTasks({ taskIds: ["old-file"] })).rejects.toEqual(new ApiError("暂时不可用", 503));
});
