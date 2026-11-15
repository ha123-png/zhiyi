import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DataTablePage } from "./DataTablePage";
import { HistoryPage } from "./HistoryPage";

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status, headers: { "Content-Type": "application/json" },
});
const counts = (total = 0) => ({ total, completed: total, needs_review: 0, failed: 0 });
const pages = ["warehouse", "history"] as const;
const table = { id: "existing", name: "已有工作记录", template_key: "custom", template_version: "1",
  document_kind: "custom", row_count: 1, created_at: "" };
const task = { id: "existing-file", filename: "已有文件.txt", content_type: "text/plain", size_bytes: 1,
  page_count: 1, sha256: "existing", template_mode: "manual", template_id: null, template_version: null,
  candidate_templates: [], status: "completed", duplicate_of_task_id: null, created_at: "2026-11-01T00:00:00Z",
  completed_at: "2026-11-01T00:00:01Z", updated_at: "2026-11-01T00:00:01Z" };

function show(page: typeof pages[number], upload = vi.fn()) {
  return page === "warehouse"
    ? render(<DataTablePage onNavigateExtract={upload} />)
    : render(<HistoryPage onNavigateExtract={upload} />);
}

afterEach(() => { vi.unstubAllGlobals(); });

describe("visible first-use examples", () => {
  it("shares editable sample data between the regular warehouse table and card without server writes", async () => {
    let release!: (response: Response) => void;
    const fetch = vi.fn((input: string | URL) => {
      const path = new URL(String(input), "http://localhost").pathname;
      if (path.endsWith("/tasks/summary")) return Promise.resolve(json({ ...counts(), total: 1 }));
      if (path.endsWith("/tables")) return new Promise<Response>(resolve => { release = resolve; });
      return Promise.resolve(json([]));
    });
    vi.stubGlobal("fetch", fetch);
    show("warehouse");
    expect(screen.queryByRole("heading", { name: "示例送货单" })).not.toBeInTheDocument();
    await waitFor(() => expect(release).toBeDefined());
    await act(async () => { release(json([])); });
    expect(await screen.findByRole("heading", { name: "示例送货单" })).toBeInTheDocument();
    expect(screen.getByText(/提取首条真实数据后隐藏/)).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(within(table).getByRole("columnheader", { name: /^客户/ })).toBeInTheDocument();
    expect(within(table).getByRole("cell", { name: /商品，当前值 打印纸/ })).toBeInTheDocument();
    fireEvent.click(within(table).getByRole("cell", { name: /客户，当前值 云栖设计工作室/ }));
    const editor = table.querySelector<HTMLElement>('[contenteditable="true"]')!;
    editor.textContent = "示例客户修改";
    fireEvent.blur(editor);
    expect(within(table).getByText("示例客户修改")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "新增一行" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "卡片" }));
    const cards = await screen.findAllByRole("article");
    expect(cards).toHaveLength(2);
    expect(cards[0]).toHaveTextContent("示例送货单-001.pdf");
    expect(cards[0]).toHaveTextContent("示例客户修改");
    fireEvent.click(within(cards[0]).getByRole("button", { name: "查看与编辑" }));
    const detail = screen.getByRole("dialog");
    fireEvent.click(within(detail).getAllByRole("group", { name: "编辑 商品" })[0]);
    const cardEditor = within(detail).getByRole("textbox", { name: "商品" });
    cardEditor.textContent = "示例商品修改";
    fireEvent.blur(cardEditor);
    expect(within(detail).getByText("示例商品修改")).toBeInTheDocument();
    fireEvent.click(within(detail).getByRole("button", { name: "关闭来源详情" }));
    fireEvent.click(screen.getByRole("button", { name: "表格" }));
    expect(screen.getByRole("cell", { name: /商品，当前值 示例商品修改/ })).toBeInTheDocument();
    expect(fetch.mock.calls.every(([url]) => !String(url).includes("/tables/warehouse-example"))).toBe(true);
  });

  it("replaces the example with the first extraction while the warehouse is open", async () => {
    let eventStream: EventTarget;
    vi.stubGlobal("EventSource", class extends EventTarget {
      constructor() { super(); eventStream = this; }
      close() {}
    });
    let finished = false;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      const path = new URL(String(input), "http://localhost").pathname;
      if (path.endsWith("/tasks/summary")) return json(counts(finished ? 1 : 0));
      if (path.endsWith("/tables")) return json(finished ? [table] : []);
      if (!path.endsWith("/tables/existing")) return json([]);
      return json({ ...table, page: 1, page_size: 10,
        columns: [{ key: "note", label: "记录", value_type: "text", section: "item" }],
        rows: [{ id: 1, task_id: "existing-file", item_index: 1, version: 1,
          values: { note: "第一份提取数据" }, created_at: "", updated_at: "" }] });
    }));
    show("warehouse");
    expect(await screen.findByRole("heading", { name: "示例送货单" })).toBeInTheDocument();
    await act(async () => {
      finished = true;
      eventStream.dispatchEvent(new MessageEvent("task", { data: JSON.stringify([{ id: task.id, status: "completed" }]) }));
    });
    expect(await screen.findByText("第一份提取数据")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "示例送货单" })).not.toBeInTheDocument();
  });

  it.each(pages)("does not show examples when the first %s read fails", async (page) => {
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      const path = new URL(String(input), "http://localhost").pathname;
      if (path.endsWith("/templates")) return json([]);
      if (path.endsWith("/tasks/summary")) return json(counts());
      if (path.endsWith(page === "warehouse" ? "/tables" : "/tasks")) return json({ detail: "读取失败" }, 503);
      return json([]);
    }));
    show(page);
    if (page === "history") expect(await screen.findByRole("button", { name: "文件历史事项" })).toBeInTheDocument();
    else expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "示例送货单" })).not.toBeInTheDocument();
    if (page === "history") {
      expect(screen.getByRole("textbox", { name: "按文件名搜索" })).toBeInTheDocument();
      expect(screen.queryByText("累计任务")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: /^已完成/ })).toHaveTextContent("—");
    }
  });

  it.each(pages)("does not mix examples with existing %s data", async (page) => {
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      const path = new URL(String(input), "http://localhost").pathname;
      if (path.endsWith("/templates") || path.endsWith("/views")) return json([]);
      if (path.endsWith("/tasks/summary")) return json(counts(1));
      if (path.endsWith("/tables")) return json([table]);
      if (path.endsWith("/tasks")) return json([task]);
      return json({ ...table, page: 1, page_size: 10,
        columns: [{ key: "note", label: "记录", value_type: "text", section: "item" }],
        rows: [{ id: 1, task_id: null, item_index: 1, version: 1,
          values: { note: "已有文件的数据" }, created_at: "", updated_at: "" }] });
    }));
    show(page);
    expect(await screen.findByText(page === "warehouse" ? "已有文件的数据" : "已有文件.txt")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "示例送货单" })).not.toBeInTheDocument();
    if (page === "history") {
      expect(screen.getByRole("textbox", { name: "按文件名搜索" })).toBeInTheDocument();
      expect(screen.queryByText("累计任务")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: /^已完成/ })).toHaveTextContent("1");
      expect(screen.getByLabelText("跳转页码")).toBeInTheDocument();
    }
  });

  it.each(["occupied", "unreadable"])("does not infer an empty workspace from an empty warehouse when history is %s", async (history) => {
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      const path = new URL(String(input), "http://localhost").pathname;
      if (path.endsWith("/tasks/summary")) return history === "unreadable" ? json({ detail: "历史读取失败" }, 503) : json(counts(1));
      return json([]);
    }));
    show("warehouse");
    expect(await screen.findByText("还没有数据表")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "示例送货单" })).not.toBeInTheDocument();
  });
});
