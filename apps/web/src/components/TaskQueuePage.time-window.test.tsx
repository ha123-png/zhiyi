import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Task, TaskSummary } from "../types";
import type { TaskOverview } from "../useTaskOverview";
import { TaskQueuePage } from "./TaskQueuePage";

const NOW = Date.parse("2026-11-02T08:00:00Z");
const DAY = "2026-11-01T08:00:00.000Z";
const WEEK = "2026-10-26T08:00:00.000Z";
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
function task(id: string, status: Task["status"] = "completed"): Task {
  return { id, filename: `${id}.pdf`, content_type: "application/pdf", size_bytes: 1,
    sha256: id, template_mode: "smart", template_id: null, template_version: null,
    candidate_templates: [], duplicate_of_task_id: null, status,
    created_at: "2026-10-01T08:00:00Z", updated_at: "2026-10-01T08:00:00Z" };
}
function summary(count: number): TaskSummary {
  return { total: count, completed: count, needs_review: 0, failed: 0,
    group_counts: { all: count, active: 0, completed: count, pending: 0, failed: 0, cancelled: 0 } };
}
const overview = (revision = 1): TaskOverview => ({ tasks: [], summary: summary(999), queuePaused: false,
  state: "ready", error: null, revision, refresh: vi.fn(async () => {}) });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("defaults to seven rolling days and shares one UTC cutoff for rows, counts, and pagination", async () => {
  vi.spyOn(Date, "now").mockReturnValue(NOW);
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname.endsWith("/templates")) return json([]);
    if (url.pathname.endsWith("/tasks/summary")) return json(summary(51));
    if (url.pathname.endsWith("/tasks")) return json([task(url.searchParams.has("offset") ? "last" : "first")]);
    return json({});
  });
  vi.stubGlobal("fetch", fetcher);
  render(<TaskQueuePage overview={overview()} />);
  await screen.findByText("first.pdf");
  expect(screen.getByRole("combobox", { name: "显示范围" })).toHaveValue("week");
  expect(screen.getByText("共 51 个文件")).toBeInTheDocument();
  expect(screen.queryByText("共 999 个文件")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await screen.findByText("last.pdf");
  const queries = fetcher.mock.calls.map(([input]) => new URL(String(input), "http://localhost"))
    .filter(url => /\/tasks(?:\/summary)?$/.test(url.pathname));
  expect(queries).toHaveLength(4);
  expect(queries.every(url => url.searchParams.get("terminal_since") === WEEK)).toBe(true);
  expect(queries.filter(url => url.pathname.endsWith("/tasks"))[1].searchParams.get("offset")).toBe("50");
  expect(screen.queryByRole("heading", { level: 1, name: "状态监控" })).not.toBeInTheDocument();
  expect(screen.getByRole("region", { name: "任务列表" })).toContainElement(screen.getByRole("table"));
});

it("switches day/all by server scope, clears selection/page, and leaves old failures to the API", async () => {
  vi.spyOn(Date, "now").mockReturnValue(NOW);
  const failed = task("old-failed", "failed");
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname.endsWith("/templates")) return json([]);
    if (url.pathname.endsWith("/tasks/summary")) return json(summary(url.searchParams.has("terminal_since") ? 1 : 2));
    if (url.pathname.endsWith("/tasks")) return json(url.searchParams.has("terminal_since") ? [failed] : [failed, task("old-done")]);
    return json({});
  });
  vi.stubGlobal("fetch", fetcher);
  const persist = vi.fn();
  render(<TaskQueuePage overview={overview()} onViewStateChange={persist} />);
  await screen.findByText("old-failed.pdf");
  fireEvent.click(screen.getByRole("checkbox", { name: "选择 old-failed.pdf" }));
  fireEvent.change(screen.getByRole("combobox", { name: "显示范围" }), { target: { value: "day" } });
  await waitFor(() => expect(fetcher.mock.calls.some(([input]) => new URL(String(input), "http://localhost").searchParams.get("terminal_since") === DAY)).toBe(true));
  await waitFor(() => expect(screen.getByRole("button", { name: "重试 old-failed.pdf" })).toBeEnabled());
  expect(screen.queryByText(/本页已选/)).not.toBeInTheDocument();
  expect(screen.getByText("old-failed.pdf")).toBeInTheDocument();
  fireEvent.change(screen.getByRole("combobox", { name: "显示范围" }), { target: { value: "all" } });
  await screen.findByText("old-done.pdf");
  expect(screen.getByText("共 2 个文件")).toBeInTheDocument();
  expect(persist).toHaveBeenLastCalledWith({ group: "all", searchInput: "", page: 0, timeWindow: "all" });
  const queries = fetcher.mock.calls.map(([input]) => new URL(String(input), "http://localhost"));
  expect(queries.filter(url => /\/tasks(?:\/summary)?$/.test(url.pathname)).slice(-2).every(url => !url.searchParams.has("terminal_since"))).toBe(true);
});

it("refreshes the rolling cutoff together and retains previous content if scoped counts fail", async () => {
  const clock = vi.spyOn(Date, "now").mockReturnValue(NOW);
  let fail = false;
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname.endsWith("/templates")) return json([]);
    if (url.pathname.endsWith("/tasks/summary")) return fail ? json({ detail: "范围计数失败" }, 503) : json(summary(1));
    if (url.pathname.endsWith("/tasks")) return json([task(fail ? "unpaired-new" : "retained")]);
    return json({});
  });
  vi.stubGlobal("fetch", fetcher);
  const view = render(<TaskQueuePage overview={overview()} />);
  await screen.findByText("retained.pdf");
  fail = true; clock.mockReturnValue(NOW + 24 * 60 * 60 * 1000);
  view.rerender(<TaskQueuePage overview={overview(2)} />);
  fireEvent.click(await screen.findByRole("button", { name: "任务读取失败" }));
  expect(screen.getByRole("dialog", { name: "任务读取失败" })).toHaveTextContent("范围计数失败");
  expect(screen.getByText("retained.pdf")).toBeInTheDocument();
  expect(screen.queryByText("unpaired-new.pdf")).not.toBeInTheDocument();
  const last = fetcher.mock.calls.map(([input]) => new URL(String(input), "http://localhost")).slice(-2);
  expect(last.every(url => url.searchParams.get("terminal_since") === "2026-10-27T08:00:00.000Z")).toBe(true);
});

it("ignores an obsolete window response after switching to all records", async () => {
  let resolve!: (value: Response) => void;
  const oldRows = new Promise<Response>(done => { resolve = done; });
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname.endsWith("/templates")) return json([]);
    if (url.pathname.endsWith("/tasks/summary")) return json(summary(url.searchParams.has("terminal_since") ? 1 : 2));
    if (url.pathname.endsWith("/tasks")) return url.searchParams.has("terminal_since") ? oldRows : json([task("all-records")]);
    return json({});
  });
  vi.stubGlobal("fetch", fetcher);
  render(<TaskQueuePage overview={overview()} />);
  fireEvent.change(screen.getByRole("combobox", { name: "显示范围" }), { target: { value: "all" } });
  await screen.findByText("all-records.pdf");
  await act(async () => resolve(json([task("obsolete-window")])));
  expect(screen.queryByText("obsolete-window.pdf")).not.toBeInTheDocument();
  expect(screen.getByText("all-records.pdf")).toBeInTheDocument();
});

it.each(["pending", "failed"] as const)("consumes a mounted %s shortcut without persisting the old query or changing the time window", async group => {
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname.endsWith("/templates")) return json([]);
    if (url.pathname.endsWith("/tasks/summary")) return json({ ...summary(53),
      group_counts: { all: 53, active: 0, completed: 51, pending: 1, failed: 1, cancelled: 0 } });
    if (url.pathname.endsWith("/tasks")) return json([url.searchParams.get("group") === group
      ? task("target", group === "failed" ? "failed" : "needs_review") : task("former")]);
    return json({});
  });
  vi.stubGlobal("fetch", fetcher);
  const persist = vi.fn();
  const saved = { group: "completed" as const, searchInput: "old-query", page: 1, timeWindow: "all" as const };
  const view = render(<TaskQueuePage overview={overview()} viewState={saved} onViewStateChange={persist} />);
  await screen.findByText("former.pdf");
  const alreadyPersisted = persist.mock.calls.length;
  view.rerender(<TaskQueuePage overview={overview()} viewState={saved} onViewStateChange={persist}
    entryRevision={1} entryGroup={group} />);
  await screen.findByText("target.pdf");
  expect(persist.mock.calls.slice(alreadyPersisted).map(([state]) => state)).toEqual([
    { group, searchInput: "", page: 0, timeWindow: "all" },
  ]);
  expect(screen.getByRole("combobox", { name: "显示范围" })).toHaveValue("all");
  expect(screen.getByRole("textbox", { name: "搜索任务文件名" })).toHaveValue("");
  const lastList = fetcher.mock.calls.map(([input]) => new URL(String(input), "http://localhost"))
    .filter(url => url.pathname.endsWith("/tasks")).at(-1)!;
  expect(lastList.searchParams.get("group")).toBe(group);
  expect(lastList.searchParams.has("search") || lastList.searchParams.has("offset") || lastList.searchParams.has("terminal_since")).toBe(false);
});
