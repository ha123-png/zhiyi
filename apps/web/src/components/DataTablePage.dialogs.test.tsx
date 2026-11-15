import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DataTablePage } from "./DataTablePage";

const table = { id: "synthetic", name: "合成数据", template_key: "synthetic", template_version: "1",
  document_kind: "custom", row_count: 1, created_at: "2026-10-31T00:00:00Z" };
const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
function stubReads() {
  vi.stubGlobal("fetch", vi.fn(async (url: string | URL) => {
    const path = String(url);
    if (path.endsWith("/tables")) return json([table, { ...table, id: "second", name: "合成对照" }]);
    if (path.includes("/views") || path.includes("/templates")) return json([]);
    return json({ ...table, columns: [{ key: "customer", label: "客户", value_type: "text", section: "item" }],
      page: 1, page_size: 10, rows: [{ id: 1, task_id: null, item_index: 1, version: 1,
        values: { customer: "合成客户" }, created_at: "", updated_at: "" }] });
  }));
}

describe("warehouse action dialogs", () => {
  it("hands table-menu focus into delete and returns to the surviving trigger", async () => {
    stubReads();
    render(<DataTablePage />);
    const trigger = await screen.findByRole("button", { name: "数据表操作" });
    trigger.focus();
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    fireEvent.click(await screen.findByRole("menuitem", { name: "删除数据表" }));
    const dialog = await screen.findByRole("dialog", { name: "删除“合成数据”？" });
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "删除“合成数据”？" })).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();
  });

  it("hands table-menu focus into the rename input after closing its focus scope", async () => {
    stubReads();
    render(<DataTablePage />);
    const trigger = await screen.findByRole("button", { name: "数据表操作" });
    trigger.focus();
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    fireEvent.click(await screen.findByRole("menuitem", { name: "重命名" }));
    const input = await screen.findByRole("textbox", { name: "表名称" });
    await waitFor(() => expect(input).toHaveFocus());
  });

  it.each(["create", "merge"])("retains the committed %s result when the following catalog refresh fails", async operation => {
    const committed = { ...table, id: "committed", name: "已保存的表" };
    let written = false;
    let writes = 0;
    vi.stubGlobal("fetch", vi.fn(async (url: string | URL, init?: RequestInit) => {
      const path = String(url);
      if (init?.method === "POST" && (path.endsWith("/tables/merge") || path.endsWith("/tables"))) {
        written = true; writes++; return json(committed);
      }
      if (path.endsWith("/tables")) {
        if (written) throw new Error("目录读取暂时中断");
        return json([table, { ...table, id: "second", name: "合成对照" }]);
      }
      if (path.includes("/templates")) return json([{ id: "template1", name: "合成字段模板", engine_kind: "template" }]);
      if (path.includes("/views")) return json([]);
      return json({ ...(path.includes("/committed?") ? committed : table), columns: [], page: 1, page_size: 10, rows: [] });
    }));
    render(<DataTablePage />);
    await screen.findByRole("heading", { name: "合成数据" });
    if (operation === "create") {
      fireEvent.click(screen.getByRole("button", { name: "新建表（基于模板创建空表）" }));
      fireEvent.change(screen.getByLabelText("表名称"), { target: { value: "已保存的表" } });
      await screen.findByRole("option", { name: "合成字段模板" });
      fireEvent.change(screen.getByLabelText("字段模板"), { target: { value: "template1" } });
      fireEvent.click(screen.getByRole("button", { name: "创建表" }));
    } else {
      fireEvent.click(screen.getByRole("button", { name: "合并表" }));
      const dialog = screen.getByRole("dialog");
      within(dialog).getAllByRole("checkbox").forEach(box => fireEvent.click(box));
      fireEvent.change(within(dialog).getByLabelText("合并表名称"), { target: { value: "已保存的表" } });
      fireEvent.click(within(dialog).getByRole("button", { name: "确认合并" }));
    }
    expect(await screen.findByRole("heading", { name: "已保存的表" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /已保存的表/ })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(writes).toBe(1);
    fireEvent.click(await screen.findByRole("button", { name: "仓库事项" }));
    expect(screen.getByRole("dialog", { name: "仓库事项" })).toHaveTextContent("更改已保存，但数据表目录暂未刷新");
    fireEvent.click(screen.getByRole("button", { name: "重新读取数据表目录" }));
    expect(await screen.findByText(/更改已保存，但数据表目录暂未刷新/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /已保存的表/ })).toBeInTheDocument();
    expect(writes).toBe(1);
  });

  it("keeps a pending merge modal open and retains its draft after a failed request", async () => {
    stubReads();
    const read = fetch;
    let release!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn((url: string | URL, init?: RequestInit) =>
      String(url).endsWith("/tables/merge") && init?.method === "POST"
        ? new Promise<Response>(resolve => { release = resolve; }) : read(url, init)));
    render(<DataTablePage />);
    await screen.findByRole("cell", { name: "编辑 客户，当前值 合成客户" });
    const opener = screen.getByRole("button", { name: "合并表" });
    opener.focus();
    fireEvent.click(opener);
    const dialog = screen.getByRole("dialog", { name: "选择需要合并的数据表" });
    within(dialog).getAllByRole("checkbox").forEach(box => fireEvent.click(box));
    fireEvent.change(within(dialog).getByLabelText("合并表名称"), { target: { value: "保留这个合并名称" } });
    const submit = within(dialog).getByRole("button", { name: "确认合并" });
    submit.focus();
    fireEvent.click(submit);
    expect(within(dialog).getByRole("button", { name: "取消" })).toBeDisabled();
    expect(dialog).toHaveFocus();
    fireEvent.keyDown(dialog, { key: "Escape" });
    fireEvent.click(dialog.parentElement!);
    expect(dialog).toBeInTheDocument();
    await act(async () => release(new Response(JSON.stringify({ detail: "合成合并失败" }), { status: 503 })));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("合成合并失败");
    expect(within(dialog).getByLabelText("合并表名称")).toHaveValue("保留这个合并名称");
    expect(within(dialog).getAllByRole("checkbox").every(box => (box as HTMLInputElement).checked)).toBe(true);
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(dialog).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it("distinguishes unavailable templates from an empty catalog and preserves the new table name on retry", async () => {
    stubReads();
    const read = fetch;
    let failed = true;
    vi.stubGlobal("fetch", vi.fn(async (url: string | URL, init?: RequestInit) => {
      if (String(url).includes("/templates")) {
        if (failed) throw new Error("合成模板连接失败");
        return json([{ id: "template1", name: "合成字段模板", engine_kind: "template" }]);
      }
      return read(url, init);
    }));
    render(<DataTablePage />);
    await screen.findByRole("cell", { name: "编辑 客户，当前值 合成客户" });
    fireEvent.click(screen.getByRole("button", { name: "新建表（基于模板创建空表）" }));
    const dialog = screen.getByRole("dialog", { name: "基于模板创建空表" });
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("字段模板读取失败");
    expect(within(dialog).getByRole("combobox", { name: "字段模板" })).toBeDisabled();
    expect(within(dialog).queryByText(/还没有可用的字段模板/)).not.toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("表名称"), { target: { value: "重试后保留草稿" } });
    failed = false;
    fireEvent.click(within(dialog).getByRole("button", { name: "重新读取字段模板" }));
    await within(dialog).findByRole("option", { name: "合成字段模板" });
    expect(within(dialog).getByLabelText("表名称")).toHaveValue("重试后保留草稿");
    expect(within(dialog).getByRole("combobox", { name: "字段模板" })).toBeEnabled();
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
  });

  it.each([
    [/字段设置/, "显示字段"],
    [/^合并表$/, "选择需要合并的数据表"],
    [/^分 Sheet$/, "选择分组方式"],
    [/新建表（基于模板创建空表）/, "基于模板创建空表"],
    [/^新增列$/, "新增附加列"],
  ] as const)("keeps keyboard focus inside %s and returns it after Escape", async (openerName, title) => {
    stubReads();
    render(<DataTablePage />);
    await screen.findByRole("cell", { name: "编辑 客户，当前值 合成客户" });
    const opener = screen.getByRole("button", { name: openerName });
    opener.focus();
    fireEvent.click(opener);
    const dialog = screen.getByRole("dialog", { name: title });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog.contains(document.activeElement)).toBe(true);
    const enabledButtons = within(dialog).getAllByRole("button").filter(button => !(button as HTMLButtonElement).disabled);
    const first = enabledButtons[0];
    const last = enabledButtons[enabledButtons.length - 1];
    first.focus();
    fireEvent.keyDown(first, { key: "Tab", shiftKey: true });
    expect(last).toHaveFocus();
    fireEvent.keyDown(last, { key: "Tab" });
    expect(first).toHaveFocus();
    fireEvent.keyDown(first, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: title })).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });
});
