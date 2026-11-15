import { useState } from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Task, TaskSummary } from "../types";
import type { TaskOverview } from "../useTaskOverview";
import { TaskQueuePage, type TaskWorkspaceViewState } from "./TaskQueuePage";
import { TaskRecoveryDialog, type TaskRecoveryKind } from "./TaskRecoveryDialog";

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}

function task(id: string, status: Task["status"], extra: Partial<Task> = {}): Task {
  return {
    id, filename: `${id}.pdf`, content_type: "application/pdf", size_bytes: 1,
    sha256: id, template_mode: "smart", template_id: null, template_version: null,
    candidate_templates: [], duplicate_of_task_id: null, status,
    has_result: status === "completed" || status === "needs_review",
    created_at: "2026-11-02T08:00:00Z", updated_at: "2026-11-02T08:00:00Z", ...extra,
  };
}

function overview(counts: TaskSummary["group_counts"]): TaskOverview {
  return {
    tasks: [], summary: { total: counts!.all, completed: counts!.completed,
      needs_review: counts!.pending, failed: counts!.failed, cancelled: counts!.cancelled,
      group_counts: counts }, queuePaused: false, state: "ready", error: null,
    revision: 1, refresh: vi.fn(async () => {}),
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("independent task-list result checks", () => {
  it("keeps the newly selected pending page when an old next-page request returns late", async () => {
    const oldPage = deferred<Response>();
    const first = Array.from({ length: 50 }, (_, index) => task(`done-${index}`, "completed"));
    const awaiting = task("needs-attention", "needs_review");
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname.endsWith("/templates")) return json([]);
      if (url.pathname.endsWith("/tasks/summary")) return json({ total: 52, completed: 51,
        needs_review: 1, failed: 0, group_counts: { all: 52, active: 0, completed: 51, pending: 1, failed: 0, cancelled: 0 } });
      if (url.pathname.endsWith("/tasks")) {
        if (url.searchParams.get("group") === "pending") return json([awaiting]);
        if (url.searchParams.get("offset") === "50") return oldPage.promise;
        return json(first);
      }
      return json({});
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<TaskQueuePage overview={overview({ all: 52, active: 0, completed: 51, pending: 1, failed: 0, cancelled: 0 })} onOpenTask={vi.fn()} />);
    await screen.findByText("done-0.pdf");
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).includes("offset=50"))).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: /^待确认/ }));
    await screen.findByText("needs-attention.pdf");
    await act(async () => oldPage.resolve(json([task("stale-last-page", "completed")])));
    expect(screen.getByText("needs-attention.pdf")).toBeInTheDocument();
    expect(screen.queryByText("stale-last-page.pdf")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "核对 needs-attention.pdf" })).toBeEnabled();
  });

  it("does not claim zero search results while the search count is unknown or its read fails", async () => {
    const searchCount = deferred<Response>();
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname.endsWith("/templates")) return json([]);
      if (url.pathname.endsWith("/tasks/summary") && url.searchParams.has("search")) return searchCount.promise;
      if (url.pathname.endsWith("/tasks")) return json(url.searchParams.has("search") ? [] : [task("original", "completed")]);
      return json({});
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<TaskQueuePage overview={overview({ all: 1, active: 0, completed: 1, pending: 0, failed: 0, cancelled: 0 })} />);
    await screen.findByText("original.pdf");
    fireEvent.change(screen.getByRole("textbox", { name: "搜索任务文件名" }), { target: { value: "needle" } });
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/tasks/summary?search=needle"))).toBe(true));
    const filters = screen.getByRole("group", { name: "任务状态筛选" });
    expect(within(filters).getAllByText("—")).toHaveLength(6);
    expect(screen.queryByText("共 0 个文件")).not.toBeInTheDocument();
    await act(async () => searchCount.resolve(json({ detail: "合成搜索计数不可用" }, 503)));
    fireEvent.click(await screen.findByRole("button", { name: "任务读取失败" }));
    expect(screen.getByRole("dialog", { name: "任务读取失败" })).toHaveTextContent("合成搜索计数不可用");
    expect(within(filters).getAllByText("—")).toHaveLength(6);
    expect(screen.queryByText("没有匹配的任务")).not.toBeInTheDocument();
  });

  it("returns to the same searched group and second page after leaving the task list", async () => {
    const items = Array.from({ length: 51 }, (_, index) => task(`case-${index}`, "failed"));
    const counts = { all: 51, active: 0, completed: 0, pending: 0, failed: 51, cancelled: 0 };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname.endsWith("/templates")) return json([]);
      if (url.pathname.endsWith("/tasks/summary")) return json({ total: 51, completed: 0,
        needs_review: 0, failed: 51, cancelled: 0, group_counts: counts });
      if (url.pathname.endsWith("/tasks")) {
        const offset = Number(url.searchParams.get("offset") || 0);
        return json(items.slice(offset, offset + 50));
      }
      return json({});
    });
    vi.stubGlobal("fetch", fetchMock);
    function NavigationHarness() {
      const [show, setShow] = useState(true);
      const [saved, setSaved] = useState<TaskWorkspaceViewState>({ group: "all", searchInput: "", page: 0 });
      return <><button type="button" onClick={() => setShow(value => !value)}>{show ? "离开列表" : "返回列表"}</button>
        {show && <TaskQueuePage overview={overview(counts)} viewState={saved} onViewStateChange={setSaved} />}</>;
    }
    render(<NavigationHarness />);
    await screen.findByText("case-0.pdf");
    fireEvent.click(screen.getByRole("button", { name: /^失败/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "搜索任务文件名" }), { target: { value: "case" } });
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).includes("search=case"))).toBe(true));
    await waitFor(() => expect(screen.getByRole("button", { name: "下一页" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    await screen.findByText("case-50.pdf");
    fireEvent.click(screen.getByRole("button", { name: "离开列表" }));
    expect(screen.queryByRole("group", { name: "任务状态筛选" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "返回列表" }));
    expect(await screen.findByText("case-50.pdf")).toBeInTheDocument();
    expect(screen.queryByText("case-0.pdf")).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "搜索任务文件名" })).toHaveValue("case");
    expect(screen.getByRole("button", { name: /^失败/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("refreshes the list after a newer overview event arrives during a local mutation refresh", async () => {
    const oldRefresh = deferred<Response>();
    let reads = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname.endsWith("/templates")) return json([]);
      if (url.pathname.endsWith("/retry")) return json(task("changing", "queued"));
      if (url.pathname.endsWith("/tasks")) {
        reads += 1;
        if (reads === 1) return json([task("changing", "failed")]);
        if (reads === 2) return oldRefresh.promise;
        return json([task("changing", "completed")]);
      }
      return json({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const first = overview({ all: 1, active: 0, completed: 0, pending: 0, failed: 1, cancelled: 0 });
    const view = render(<TaskQueuePage overview={first} onOpenTask={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "重试 changing.pdf" }));
    await waitFor(() => expect(reads).toBe(2));
    // This represents a later event after the second request took its snapshot.
    view.rerender(<TaskQueuePage overview={{ ...first, revision: 2,
      summary: { ...first.summary!, completed: 1, failed: 0,
        group_counts: { all: 1, active: 0, completed: 1, pending: 0, failed: 0, cancelled: 0 } } }} onOpenTask={vi.fn()} />);
    await act(async () => oldRefresh.resolve(json([task("changing", "queued")])));
    expect(await screen.findByRole("button", { name: "对照 changing.pdf" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "暂停 changing.pdf" })).not.toBeInTheDocument();
  });

  it("coalesces later overview events and recovers after the older local read fails", async () => {
    const oldRefresh = deferred<Response>();
    let reads = 0;
    let writes = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname.endsWith("/templates")) return json([]);
      if (url.pathname.endsWith("/retry")) { writes += 1; return json(task("coalesced", "queued")); }
      if (url.pathname.endsWith("/tasks")) {
        reads += 1;
        if (reads === 1) return json([task("coalesced", "failed")]);
        if (reads === 2) return oldRefresh.promise;
        return json([task("coalesced", "completed")]);
      }
      return json({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const first = overview({ all: 1, active: 0, completed: 0, pending: 0, failed: 1, cancelled: 0 });
    const view = render(<TaskQueuePage overview={first} onOpenTask={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "重试 coalesced.pdf" }));
    await waitFor(() => expect(reads).toBe(2));
    view.rerender(<TaskQueuePage overview={{ ...first, revision: 2 }} onOpenTask={vi.fn()} />);
    view.rerender(<TaskQueuePage overview={{ ...first, revision: 3 }} onOpenTask={vi.fn()} />);
    await act(async () => oldRefresh.resolve(json({ detail: "旧读取失败" }, 503)));
    expect(await screen.findByRole("button", { name: "对照 coalesced.pdf" })).toBeEnabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(reads).toBe(3);
    expect(writes).toBe(1);
  });
});

function RecoveryLauncher({ kind, item, onClosed }: { kind: TaskRecoveryKind; item: Task; onClosed: () => void }) {
  const [open, setOpen] = useState(false);
  return <><button type="button" onClick={() => setOpen(true)}>打开处理</button>
    {open && <TaskRecoveryDialog task={item} kind={kind} templates={[]} templatesLoading={false}
      templatesError={null} reloadTemplates={() => {}} onUpdated={async () => {}}
      onClose={() => { setOpen(false); onClosed(); }} />}</>;
}

describe("independent recovery-dialog result checks", () => {
  it.each([
    { kind: "scope" as const, status: "waiting_for_template" as const, action: "仅提取所示范围", endpoint: "/input-scope" },
    { kind: "export" as const, status: "completed" as const, action: "跳过此次副本导出", endpoint: "/export" },
  ])("keeps $kind failure visible when Escape is pressed during a pending request", async ({ kind, status, action, endpoint }) => {
    const reply = deferred<Response>();
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith(endpoint)) return reply.promise;
      return json([]);
    });
    vi.stubGlobal("fetch", fetchMock);
    const closed = vi.fn();
    const item = task("recovery", status, kind === "export" ? {
      file_export: { mode: "copy", status: "failed", parent_path: "D:\\synthetic", destination: null,
        actual_path: null, confirmed_name: null, error_code: "name_conflict", error_message: "目标已有同名文件" },
    } : { pending_reason: "input_scope", pending_action: "confirm_input_scope" });
    render(<RecoveryLauncher kind={kind} item={item} onClosed={closed} />);
    const opener = screen.getByRole("button", { name: "打开处理" });
    opener.focus();
    fireEvent.click(opener);
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: action }));
    expect(within(dialog).getByRole("button", { name: "关闭" })).toBeDisabled();
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(closed).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls.filter(([input]) => String(input).endsWith(endpoint))).toHaveLength(1);
    await act(async () => reply.resolve(json({ detail: "合成操作未成功，请重试" }, 409)));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("合成操作未成功");
    expect(within(dialog).getByRole("button", { name: "关闭" })).toBeEnabled();
    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(opener).toHaveFocus());
    expect(closed).toHaveBeenCalledOnce();
  });
});
