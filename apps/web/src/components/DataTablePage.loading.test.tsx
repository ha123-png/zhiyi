import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DataTablePage } from "./DataTablePage";

const table = { id: "loading-table", name: "合成表", template_key: "test", template_version: "1",
  document_kind: "test", row_count: 1, created_at: "" };
const detail = { ...table, page: 1, page_size: 10,
  columns: [{ key: "item", label: "项目", value_type: "text", section: "item" }],
  rows: [{ id: 1, task_id: null, item_index: 1, version: 1, review_pending: true,
    values: { item: "原始项目" }, created_at: "", updated_at: "" }] };
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status, headers: { "Content-Type": "application/json" },
});

describe("warehouse read state", () => {
  it.each(["list", "detail"])("distinguishes a failed %s read from empty data and retries in place", async (failedRead) => {
    let failed = true;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname.endsWith("/views") || url.pathname.endsWith("/templates")) return json([]);
      if (url.pathname.endsWith("/tables")) return failed && failedRead === "list" ? json({ detail: "合成读取失败" }, 503) : json([table]);
      return failed && failedRead === "detail" ? json({ detail: "合成读取失败" }, 503) : json(detail);
    }));
    render(<DataTablePage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("合成读取失败");
    expect(screen.queryByText("这张表还没有数据")).not.toBeInTheDocument();
    expect(screen.queryByText("还没有数据表")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "新增一行" })).not.toBeInTheDocument();
    expect(screen.queryByText("共 0 行")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "导出" })).toBeDisabled();
    failed = false;
    fireEvent.click(screen.getByRole("button", { name: failedRead === "list" ? "重新读取数据表" : "重新读取表数据" }));
    expect(await screen.findByRole("cell", { name: "编辑 项目，当前值 原始项目" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "导出" })).toBeEnabled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("hides the previous query's rows and source warnings while a new query fails", async () => {
    let release!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname.endsWith("/tables")) return json([table]);
      if (url.pathname.endsWith("/views") || url.pathname.endsWith("/templates")) return json([]);
      if (url.searchParams.get("search")) return new Promise<Response>(resolve => { release = resolve; });
      return json(detail);
    }));
    render(<DataTablePage />);
    await screen.findByRole("cell", { name: "编辑 项目，当前值 原始项目" });
    fireEvent.click(screen.getByRole("button", { name: "仓库事项" }));
    expect(screen.getByText("本页有 1 条来源待核对的数据")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "关闭仓库事项" }));
    fireEvent.change(screen.getByRole("textbox", { name: "搜索当前表" }), { target: { value: "另一项目" } });
    expect(screen.queryByRole("cell")).not.toBeInTheDocument();
    expect(screen.queryByText("本页有 1 条来源待核对的数据")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("正在读取数据");
    await waitFor(() => expect(release).toBeDefined());
    release(json({ detail: "搜索读取失败" }, 503));
    expect(await screen.findByRole("alert")).toHaveTextContent("搜索读取失败");
    expect(screen.queryByRole("cell")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "导出" })).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "搜索当前表" }), { target: { value: "" } });
    expect(await screen.findByRole("cell", { name: "编辑 项目，当前值 原始项目" })).toBeInTheDocument();
  });

  it("keeps a same-scope edit mounted after refresh failure and can retry after the user ends editing", async () => {
    let failed = false;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname.endsWith("/tables")) return json([table]);
      if (url.pathname.endsWith("/views") || url.pathname.endsWith("/templates")) return json([]);
      return failed ? json({ detail: "刷新失败" }, 503) : json(detail);
    }));
    render(<DataTablePage />);
    const cell = await screen.findByRole("cell", { name: "编辑 项目，当前值 原始项目" });
    fireEvent.click(cell);
    const editor = cell.querySelector<HTMLElement>('[contenteditable="true"]')!;
    editor.textContent = "尚未提交的草稿";
    fireEvent.input(editor);
    failed = true;
    fireEvent(window, new CustomEvent("zhiyi:assistant-changed", { detail: { table_id: table.id } }));
    const notice = await screen.findByRole("button", { name: "仓库事项" });
    expect(cell.querySelector('[contenteditable="true"]')).toBe(editor);
    expect(editor).toHaveTextContent("尚未提交的草稿");
    fireEvent.keyDown(editor, { key: "Escape" });
    fireEvent.click(notice);
    expect(screen.getByRole("dialog", { name: "仓库事项" })).toHaveTextContent("当前保留上次读取的内容");
    failed = false;
    fireEvent.click(screen.getByRole("button", { name: "重新读取表数据" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "仓库事项" })).not.toBeInTheDocument());
    expect(cell).toHaveTextContent("原始项目");
  });
});
