import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { Task, TaskSummary } from "./types";

function task(id: string, status: Task["status"]): Task {
  return {
    id, status, filename: `${id}.pdf`, content_type: "application/pdf", size_bytes: 1,
    sha256: id, template_mode: "smart", template_id: null, template_version: null,
    candidate_templates: [], duplicate_of_task_id: null,
    created_at: "2026-11-02T08:00:00Z", updated_at: "2026-11-02T08:00:00Z",
  };
}

function fixture() {
  const failed = Array.from({ length: 101 }, (_, index) => task(`保留失败-${index + 1}`, "failed"));
  const pending = task("待核对的文件", "needs_review");
  const paused = task("单独暂停的文件", "paused");
  let showPaused = false;
  const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
  const requests = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname.endsWith("/tasks/summary")) {
      const counts = { all: 102 + Number(showPaused), active: Number(showPaused), pending: 1, completed: 0, failed: 101, cancelled: 0 };
      return json({ total: counts.all, completed: 0, needs_review: 1, failed: 101, active: counts.active,
        waiting_for_action: 0, pending_exports: 0, group_counts: counts } satisfies TaskSummary);
    }
    if (url.pathname.endsWith("/tasks")) {
      const group = url.searchParams.get("group");
      let rows = group === "active" ? showPaused ? [paused] : []
        : group === "pending" ? [pending] : group === "failed" ? failed : [...failed, pending];
      const search = url.searchParams.get("search");
      if (search) rows = rows.filter(row => row.filename.includes(search));
      const offset = Number(url.searchParams.get("offset") ?? 0);
      return json(rows.slice(offset, offset + Number(url.searchParams.get("limit") ?? 50)));
    }
    if (url.pathname.endsWith("/queue/status")) return json({ paused: false });
    if (url.pathname.endsWith("/user-state/onboarding")) return json({ completed_version: 4 });
    if (url.pathname.endsWith("/models/status")) return json({ connected: true, configured_model: "synthetic-model", available_models: [] });
    if (url.pathname.endsWith("/system/status")) return json({
      api: { connected: true, message: "API 已连接" }, worker: { connected: true, message: "任务消费者在线" },
      model: { connected: true, message: "合成模型状态" },
    });
    return json([]);
  });
  vi.stubGlobal("fetch", requests);
  return { requests, showPaused: () => { showPaused = true; } };
}

function filters() { return screen.getByRole("group", { name: "任务状态筛选" }); }
function globalBar() { return screen.getByRole("region", { name: "当前任务状态" }); }

async function chooseOldQuery() {
  fireEvent.click(within(filters()).getByRole("button", { name: /^失败/ }));
  fireEvent.change(screen.getByRole("textbox", { name: "搜索任务文件名" }), { target: { value: "保留" } });
  await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([input]) => {
    const url = new URL(String(input), "http://localhost");
    return url.pathname.endsWith("/tasks") && url.searchParams.get("group") === "failed" && url.searchParams.get("search") === "保留";
  })).toBe(true));
  await waitFor(() => expect(screen.getByRole("button", { name: "下一页" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await screen.findByText("保留失败-51.pdf");
  await waitFor(() => expect(screen.getByRole("button", { name: "上一页" })).toBeEnabled());
}

async function expectPendingFirstPage() {
  await waitFor(() => expect(within(filters()).getByRole("button", { name: /^待确认/ })).toHaveAttribute("aria-pressed", "true"));
  expect(screen.getByRole("textbox", { name: "搜索任务文件名" })).toHaveValue("");
  expect(await screen.findByText("待核对的文件.pdf")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled();
  expect(screen.queryByText("保留失败-51.pdf")).not.toBeInTheDocument();
}

describe("global pending shortcut", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    HTMLElement.prototype.scrollIntoView = vi.fn();
    vi.stubGlobal("EventSource", class { readyState = 1; addEventListener() {} close() {} });
  });

  it("keeps a duplicate pending across navigation and reload until explicitly skipped", async () => {
    const { requests } = fixture();
    const fallback = requests.getMockImplementation()!;
    let uploads = 0;
    let saved: unknown[] = [];
    requests.mockImplementation(async (input, init?: RequestInit) => {
      if (String(input).includes("/user-state/pending-duplicates")) {
        if (init?.method === "PUT") {
          const item = JSON.parse(String(init.body));
          saved = [item];
          return Response.json(item);
        }
        if (init?.method === "DELETE") {
          saved = [];
          return new Response(null, { status: 204 });
        }
        return Response.json(saved);
      }
      if (String(input).endsWith("/tasks") && init?.method === "POST") {
        uploads++;
        return new Response(JSON.stringify({ detail: { code: "duplicate_upload", message: "重复",
          duplicate_task_id: "已有文件", duplicate_sha256: "a".repeat(64) } }), { status: 409 });
      }
      return fallback(input);
    });
    const first = render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "文件提取" }));
    await screen.findByRole("heading", { name: "提取出的数据" });
    fireEvent.change(document.querySelector('input[type="file"]')!, {
      target: { files: [new File(["abc"], "重复文件.txt")] },
    });
    const entry = await screen.findByRole("button", { name: "2 个待处理事项" });
    fireEvent.click(entry);
    fireEvent.click(await screen.findByRole("button", { name: "处理重复文件 重复文件.txt" }));
    fireEvent.click(within(screen.getByRole("dialog", { name: "重复待确认" })).getByRole("button", { name: "关闭重复待确认" }));
    expect(screen.getByText("重复待确认")).toBeInTheDocument();
    first.unmount();
    sessionStorage.clear();

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "2 个待处理事项" }));
    fireEvent.click(await screen.findByRole("button", { name: "处理重复文件 重复文件.txt" }));
    expect(screen.getByRole("button", { name: "再次提取" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^跳过$/ }));
    await waitFor(() => expect(screen.queryByText("重复待确认")).not.toBeInTheDocument());
    expect(uploads).toBe(1);
  });

  it.each(["mounted", "remounted"])("opens pending with an empty search and first page from a %s workspace", async mode => {
    fixture();
    render(<App />);
    await screen.findByRole("button", { name: "1 个待处理事项" });
    await chooseOldQuery();
    fireEvent.click(screen.getByRole("checkbox", { name: "选择本页任务" }));
    expect(screen.getByText("本页已选 50 个文件")).toBeInTheDocument();
    if (mode === "remounted") {
      fireEvent.click(screen.getByRole("button", { name: "文件提取" }));
      await screen.findByRole("heading", { name: "提取出的数据" });
      expect(screen.queryByRole("group", { name: "任务状态筛选" })).not.toBeInTheDocument();
    }
    fireEvent.click(within(globalBar()).getByRole("button", { name: "1 个待处理事项" }));
    await expectPendingFirstPage();
    expect(screen.queryByText(/本页已选/)).not.toBeInTheDocument();
    // Later ordinary navigation must retain the explicitly chosen pending view.
    fireEvent.click(screen.getByRole("button", { name: "文件提取" }));
    await screen.findByRole("heading", { name: "提取出的数据" });
    fireEvent.click(screen.getByRole("button", { name: "状态监控" }));
    await expectPendingFirstPage();
  });

  it("does not reset a guarded workspace until navigation is allowed", async () => {
    fixture();
    render(<App />);
    await screen.findByRole("button", { name: "1 个待处理事项" });
    await chooseOldQuery();
    let proceed: (() => void) | undefined;
    const guard = (event: Event) => {
      event.preventDefault();
      proceed = (event as CustomEvent<{ proceed: () => void }>).detail.proceed;
    };
    window.addEventListener("zhiyi:before-navigate", guard);
    try {
      fireEvent.click(within(globalBar()).getByRole("button", { name: "1 个待处理事项" }));
      expect(proceed).toBeTypeOf("function");
      expect(within(filters()).getByRole("button", { name: /^失败/ })).toHaveAttribute("aria-pressed", "true");
      expect(screen.getByRole("textbox", { name: "搜索任务文件名" })).toHaveValue("保留");
      expect(screen.getByText("保留失败-51.pdf")).toBeInTheDocument();
      await act(async () => { proceed!(); });
      await expectPendingFirstPage();
    } finally { window.removeEventListener("zhiyi:before-navigate", guard); }
  });

  it("opens failed files on the mounted page and clears the old query, page and selection", async () => {
    fixture();
    render(<App />);
    await screen.findByRole("button", { name: "1 个待处理事项" });
    await chooseOldQuery();
    fireEvent.click(screen.getByRole("checkbox", { name: "选择本页任务" }));
    expect(screen.getByText("本页已选 50 个文件")).toBeInTheDocument();
    fireEvent.click(within(globalBar()).getByRole("button", { name: "101 个文件失败" }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "搜索任务文件名" })).toHaveValue(""));
    expect(within(filters()).getByRole("button", { name: /^失败/ })).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByText("保留失败-1.pdf")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled();
    expect(screen.queryByText(/本页已选/)).not.toBeInTheDocument();
  });

  it("keeps the old filter, search and page for the ordinary paused-task shortcut", async () => {
    const server = fixture();
    render(<App />);
    await screen.findByRole("button", { name: "1 个待处理事项" });
    await chooseOldQuery();
    server.showPaused();
    act(() => { document.dispatchEvent(new Event("visibilitychange")); });
    await screen.findByRole("button", { name: "查看任务" });
    fireEvent.click(screen.getByRole("button", { name: "文件提取" }));
    await screen.findByRole("heading", { name: "提取出的数据" });
    fireEvent.click(within(globalBar()).getByRole("button", { name: "查看任务" }));
    expect(await screen.findByRole("textbox", { name: "搜索任务文件名" })).toHaveValue("保留");
    expect(within(filters()).getByRole("button", { name: /^失败/ })).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByText("保留失败-51.pdf")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上一页" })).toBeEnabled();
  });
});
