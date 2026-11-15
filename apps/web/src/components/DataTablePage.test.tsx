import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DataTablePage } from "./DataTablePage";

const table = {
  id: "invoice-v1",
  name: "发票",
  template_key: "invoice",
  template_version: "1",
  document_kind: "invoice",
  row_count: 1,
  created_at: "2026-07-30T00:00:00Z",
};

const columns = [
  { key: "seller_name", label: "销售方", value_type: "text", section: "header" },
  { key: "total_amount", label: "合计金额", value_type: "number", section: "item" },
];

function sourceCard(index: number, pending = false) {
  return {id: JSON.stringify({id: `source-${index}`, kind: "file"}), kind: "file", task_id: `source-${index}`, filename: `资料${index}.pdf`,
    first_row_id: index, version: "a".repeat(64), row_count: 1000, item_count: 1000, matched_row_count: 1000, complete: true,
    review_pending: pending, partial_input: false, shared_header_keys: ["seller_name"],
    preview_header: [{key: "seller_name", label: "销售方", value: `记录${index}`, truncated: false}], preview_items: []};
}

describe("DataTablePage", () => {
  it.each(["1,23", "0x10", "   ", "1.0000000000000001", "9007199254740991.1", "9e30"])(
    "saves manual text %j in a numeric column without coercing it",
    async (input) => {
      const writes: string[] = [];
      vi.stubGlobal("fetch", vi.fn(async (url: string | URL, init?: RequestInit) => {
        const path = String(url);
        if (path.endsWith("/tables")) return response([table]);
        if (path.includes("/views") || path.includes("/templates")) return response([]);
        if (path.endsWith("/edit-sessions")) return response({ id: "edit-session" });
        if (path.endsWith("/rows/1") && init?.method === "PATCH") {
          writes.push(String(init.body));
          return response({ id: 1, version: 2, values: { total_amount: 372 } });
        }
        return response({ ...table, columns, page: 1, page_size: 10, rows: [{ id: 1, task_id: null,
          item_index: 1, version: 1, values: { seller_name: "合成供应方", total_amount: 372 }, created_at: "", updated_at: "" }] });
      }));

      render(<DataTablePage />);
      const cell = await screen.findByRole("cell", { name: "编辑 合计金额，当前值 372" });
      fireEvent.click(cell);
      const editor = cell.querySelector<HTMLElement>('[contenteditable="true"]')!;
      editor.textContent = input;
      fireEvent.input(editor);
      fireEvent.keyDown(editor, { key: "Enter" });

      await waitFor(() => expect(writes).toHaveLength(1));
      expect(JSON.parse(writes[0]).changes).toEqual({ total_amount: input });
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    },
  );

  it.each([["1.23", 1.23], ["-12", -12], ["1e3", 1000], ["9007199254740991", Number.MAX_SAFE_INTEGER]])(
    "sends valid JSON number input %s without changing its numeric value",
    async (input, expected) => {
      const writes: Record<string, unknown>[] = [];
      vi.stubGlobal("fetch", vi.fn(async (url: string | URL, init?: RequestInit) => {
        const path = String(url);
        if (path.endsWith("/tables")) return response([table]);
        if (path.includes("/views") || path.includes("/templates")) return response([]);
        if (path.endsWith("/edit-sessions")) return response({ id: "edit-session" });
        if (path.endsWith("/rows/1") && init?.method === "PATCH") {
          writes.push(JSON.parse(String(init.body)));
          return response({ id: 1, version: 2, values: { total_amount: expected } });
        }
        return response({ ...table, columns, page: 1, page_size: 10, rows: [{ id: 1, task_id: null,
          item_index: 1, version: 1, values: { seller_name: "合成供应方", total_amount: 372 }, created_at: "", updated_at: "" }] });
      }));

      render(<DataTablePage />);
      const cell = await screen.findByRole("cell", { name: "编辑 合计金额，当前值 372" });
      fireEvent.click(cell);
      const editor = cell.querySelector<HTMLElement>('[contenteditable="true"]')!;
      editor.textContent = input;
      fireEvent.input(editor);
      fireEvent.keyDown(editor, { key: "Enter" });

      await waitFor(() => expect(writes).toHaveLength(1));
      expect(writes[0]?.changes).toEqual({ total_amount: expected });
      expect(screen.queryByLabelText("未保存输入：发票 · 行 1 · 合计金额")).not.toBeInTheDocument();
    },
  );

  it("sends unsafe integer cells as canonical decimal strings without losing digits", async () => {
    let sentValue: unknown;
    vi.stubGlobal("fetch", vi.fn(async (url: string | URL, init?: RequestInit) => {
      const path = String(url);
      if (path.endsWith("/tables")) return response([table]);
      if (path.includes("/views") || path.includes("/templates")) return response([]);
      if (path.endsWith("/edit-sessions")) return response({ id: "edit-session" });
      if (path.endsWith("/rows/1") && init?.method === "PATCH") {
        sentValue = JSON.parse(String(init.body)).changes.total_amount;
        return response({ id: 1, version: 2, values: { total_amount: "9007199254740993" } });
      }
      return response({ ...table, columns, page: 1, page_size: 10, rows: [{ id: 1, task_id: null,
        item_index: 1, version: 1, values: { seller_name: "合成供应方", total_amount: 372 }, created_at: "", updated_at: "" }] });
    }));

    render(<DataTablePage />);
    const cell = await screen.findByRole("cell", { name: "编辑 合计金额，当前值 372" });
    fireEvent.click(cell);
    const editor = cell.querySelector<HTMLElement>('[contenteditable="true"]')!;
    editor.textContent = "9007199254740993";
    fireEvent.input(editor);
    fireEvent.keyDown(editor, { key: "Enter" });

    await waitFor(() => expect(sentValue).toBe("9007199254740993"));
  });

  it("refreshes visible facts after an assistant approval without reloading the page", async () => {
    let amount = 3200;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      const url = String(input);
      if (url.endsWith("/tables")) return response([table]);
      if (url.endsWith("/views") || url.includes("/templates")) return response([]);
      return response({ ...table, columns, page: 1, page_size: 10, rows: [{ id: 1, task_id: null, item_index: 1, version: 1,
        values: { seller_name: "合成供应方", total_amount: amount }, created_at: "", updated_at: "" }] });
    }));
    render(<DataTablePage />);
    await screen.findByText("3200");
    amount = 3600;
    fireEvent(window, new CustomEvent("zhiyi:assistant-changed", { detail: { table_id: "invoice-v1" } }));
    expect(await screen.findByText("3600")).toBeInTheDocument();
    expect(screen.queryByText("3200")).not.toBeInTheDocument();
  });
  it("keeps pending source information visible in both presentations", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      const url = String(input);
      if (url.endsWith("/tables")) return response([table]);
      if (url.endsWith("/views") || url.includes("/templates")) return response([]);
      if (url.includes("/source-cards?")) return response({table_id: table.id, page: 1, page_size: 6, total: 1, row_count: 1000, groups: [sourceCard(1, true)]});
      return response({ ...table, columns, page: 1, page_size: 10, rows: [{ id: 1, task_id: null, item_index: 1, version: 1,
        review_pending: true, values: { seller_name: "待核对内容", source_filename: "合成资料.txt" }, created_at: "", updated_at: "" }] });
    }));
    render(<DataTablePage />);
    fireEvent.click(await screen.findByRole("button", { name: "仓库事项" }));
    expect(screen.getByText("本页有 1 条来源待核对的数据")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "关闭仓库事项" }));
    fireEvent.click(screen.getByRole("button", { name: "卡片" }));
    expect(await screen.findByText("来源待核对", { selector: ".badge" })).toBeInTheDocument();
    expect(screen.queryByText("本页有 1 条来源待核对的数据")).not.toBeInTheDocument();
  });

  it("paginates cards by complete source independently from the table row page", async () => {
    const sizes: number[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      const url = String(input);
      if (url.endsWith("/tables")) return response([{ ...table, row_count: 6 }]);
      if (url.endsWith("/views") || url.includes("/templates")) return response([]);
      const params = new URL(url, "http://localhost").searchParams;
      const size = Number(params.get("page_size") || 10);
      const page = Number(params.get("page") || 1);
      if (url.includes("/source-cards?")) return response({table_id: table.id, page, page_size: size, total: 14, row_count: 14000,
        groups: Array.from({length: 14}, (_, i) => sourceCard(i + 1)).slice((page - 1) * size, page * size)});
      sizes.push(size);
      return response({ ...table, row_count: 6, columns, presentation: { mode: "card", primary_fields: [], collapsed_fields: [] }, page, page_size: size,
        rows: Array.from({ length: 6 }, (_, i) => ({ id: i + 1, task_id: null, item_index: i, version: 1, values: { seller_name: `记录${i + 1}` }, created_at: "", updated_at: "" })).slice((page - 1) * size, page * size) });
    }));
    render(<DataTablePage />);
    await waitFor(() => expect(screen.getAllByRole("article")).toHaveLength(3));
    expect(sizes.at(-1)).toBe(10);
    fireEvent.click(screen.getByRole("button", { name: "下一页来源" }));
    await waitFor(() => expect(screen.getAllByRole("article")[0]).toHaveTextContent("记录4"));
    const jump = screen.getByRole("spinbutton", { name: "跳转页码来源" });
    fireEvent.change(jump, { target: { value: "6" } });
    expect(screen.getByRole("button", { name: "跳转" })).toBeDisabled();
    fireEvent.change(jump, { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "跳转" }));
    await waitFor(() => expect(screen.getAllByRole("article")[0]).toHaveTextContent("记录13"));
    expect(screen.getAllByRole("article")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "下一页来源" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "表格" }));
    await waitFor(() => expect(sizes.at(-1)).toBe(10));
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    expect(await screen.findByRole("cell", { name: /记录1$/ })).toBeInTheDocument();
  });

  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (input: string | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/tables")) return response([table]);
        if (url.endsWith("/tables/invoice-v1/views")) return response([]);
        if (url.includes("/tables/invoice-v1/views/view-seller?")) {
          return response({
            id: "view-seller",
            table_id: table.id,
            name: "供应商甲",
            field_key: "seller_name",
            field_value: "供应商甲",
            row_count: 1,
            created_at: "2026-07-30T00:00:00Z",
            updated_at: "2026-07-30T00:00:00Z",
            source_table_name: "发票",
            columns,
            page: 1,
            page_size: 20,
            rows: [
              {
                id: 1,
                task_id: "task-1",
                item_index: 0,
                version: 1,
                values: { seller_name: "供应商甲", total_amount: 372 },
                created_at: "2026-07-30T00:00:00Z",
                updated_at: "2026-07-30T00:00:00Z",
              },
            ],
          });
        }
        if (url.endsWith("/tables/invoice-v1/split") && init?.method === "POST") {
          return response([
            {
              id: "view-seller",
              table_id: table.id,
              name: "供应商甲",
              field_key: "seller_name",
              field_value: "供应商甲",
              row_count: 1,
              created_at: "2026-07-30T00:00:00Z",
              updated_at: "2026-07-30T00:00:00Z",
            },
          ]);
        }
        if (url.includes("/tables/invoice-v1?")) {
          return response({
            ...table,
            columns,
            page: 1,
            page_size: 20,
            rows: [
              {
                id: 1,
                task_id: "task-1",
                item_index: 0,
                version: 1,
                values: {
                  seller_name: "一家名称非常长但不应覆盖相邻字段的供应商有限公司",
                  total_amount: 372,
                },
                created_at: "2026-07-30T00:00:00Z",
                updated_at: "2026-07-30T00:00:00Z",
              },
            ],
          });
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
  });

  it.each(['conflict', 'network'] as const)("restores edited DOM after an async %s and allows a later save", async (failure) => {
    let amount = 16;
    let version = 17;
    let complete: ((result: Response) => void) | undefined;
    let reject: ((reason: Error) => void) | undefined;
    const writes: {expected_version: number; changes: Record<string, number>}[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/tables')) return response([table]);
      if (url.endsWith('/views') || url.includes('/templates')) return response([]);
      if (url.endsWith('/edit-sessions')) return response({id: 'edit-session'});
      if (url.endsWith('/seal')) return new Response(null, {status: 204});
      if (url.endsWith('/rows/1') && init?.method === 'PATCH') {
        const write = JSON.parse(String(init.body));
        writes.push(write);
        if (writes.length === 1) return new Promise<Response>((resolve, fail) => {complete = resolve; reject = fail;});
        amount = write.changes.total_amount;
        version += 1;
        return response({id: 1, version, values: {total_amount: amount}});
      }
      return response({...table, columns, page: 1, page_size: 10, rows: [{id: 1, task_id: null,
        item_index: 1, version, values: {seller_name: '合成来源', total_amount: amount}, created_at: '', updated_at: ''}]});
    }));
    render(<DataTablePage />);
    fireEvent.click(await screen.findByRole('cell', {name: '编辑 合计金额，当前值 16'}));
    const editable = screen.getByText('16');
    expect(editable).toHaveAttribute('contenteditable', 'true');
    editable.textContent = '17'; // Browser-owned DOM, not a React value update.
    fireEvent.input(editable);
    fireEvent.keyDown(editable, {key: 'Enter'});
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0]).toMatchObject({expected_version: 17, changes: {total_amount: 17}});
    expect(editable).toHaveTextContent(/^16$/); // Pending is not saved fact either.
    if (failure === 'conflict') {
      amount = 18; version = 18;
      complete?.(new Response(JSON.stringify({detail: '这行数据已经更新，请刷新后再修改'}), {status: 409}));
    } else {
      reject?.(new TypeError('network unavailable'));
    }
    const alert = await screen.findByRole('alert');
    if (failure === 'conflict') expect(alert).toHaveTextContent('本次修改未保存');
    const restored = await screen.findByRole('cell', {name: `编辑 合计金额，当前值 ${amount}`});
    expect(restored).toHaveTextContent(String(amount));
    fireEvent.click(screen.getByRole('button', {name: '仓库事项'}));
    expect(screen.getByLabelText('未保存输入：发票 · 行 1 · 合计金额')).toHaveValue('17');
    fireEvent.click(screen.getByRole('button', {name: '关闭仓库事项'}));
    expect(restored).toHaveTextContent(String(amount));
    fireEvent.click(restored);
    const next = restored.querySelector('[contenteditable="true"]')!;
    next.textContent = '19';
    fireEvent.keyDown(next, {key: 'Enter'});
    await screen.findByRole('cell', {name: '编辑 合计金额，当前值 19'});
    expect(writes).toHaveLength(2);
    expect(writes[1]).toMatchObject({expected_version: failure === 'conflict' ? 18 : 17, changes: {total_amount: 19}});
    expect(screen.queryByLabelText('未保存输入：发票 · 行 1 · 合计金额')).not.toBeInTheDocument();
  });

  it.each(['conflict', 'network'] as const)('keeps copyable long drafts separately after %s, until saved or dismissed', async (failure) => {
    let seller = '原始备注';
    let version = 1;
    let writes = 0;
    const longText = '待保存的长备注 <img src=x onerror=alert(1)>\n'.repeat(60);
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/tables')) return response([table]);
      if (url.endsWith('/views') || url.includes('/templates')) return response([]);
      if (url.endsWith('/edit-sessions')) return response({id: 'draft-session'});
      if (url.endsWith('/seal')) return new Response(null, {status: 204});
      if (url.endsWith('/rows/1') && init?.method === 'PATCH') {
        writes += 1;
        await Promise.resolve();
        if (writes < 4) {
          if (failure === 'network') throw new TypeError('offline');
          seller = '他人已保存的备注'; version += 1;
          return new Response(JSON.stringify({detail: '这行数据已经更新'}), {status: 409});
        }
        seller = JSON.parse(String(init.body)).changes.seller_name;
        version += 1;
        return response({id: 1, version, values: {seller_name: seller}});
      }
      return response({...table, columns, page: 1, page_size: 10, rows: [{id: 1, task_id: null,
        item_index: 1, version, values: {seller_name: seller, total_amount: 16}, created_at: '', updated_at: ''}]});
    }));
    render(<DataTablePage />);
    const edit = async (label: string, value: string, input: string) => {
      const cell = await screen.findByRole('cell', {name: `编辑 ${label}，当前值 ${value}`});
      fireEvent.click(cell);
      const editable = cell.querySelector('[contenteditable="true"]')!;
      editable.textContent = input;
      fireEvent.input(editable);
      fireEvent.keyDown(editable, {key: 'Enter'});
      return cell;
    };
    await edit('销售方', seller, longText);
    const draftLabel = '未保存输入：发票 · 行 1 · 销售方';
    const openNotices = async () => fireEvent.click(await screen.findByRole('button', {name: '仓库事项'}));
    const closeNotices = () => fireEvent.click(screen.getByRole('button', {name: '关闭仓库事项'}));
    await openNotices();
    const draft = screen.getByLabelText(draftLabel);
    expect(draft).toHaveValue(longText);
    expect(draft).toHaveAttribute('readonly');
    expect(document.querySelector('img[src="x"]')).toBeNull();
    closeNotices();
    expect(screen.queryByRole('dialog', {name: '仓库事项'})).not.toBeInTheDocument();
    const restored = await screen.findByRole('cell', {name: `编辑 销售方，当前值 ${seller}`});
    expect(restored).toHaveTextContent(seller);
    await edit('合计金额', '16', '待保存的金额');
    await openNotices();
    expect(await screen.findByLabelText('未保存输入：发票 · 行 1 · 合计金额')).toHaveValue('待保存的金额');
    expect(screen.getByLabelText(draftLabel)).toHaveValue(longText);
    closeNotices();
    await edit('销售方', seller, longText + '第二次输入');
    await openNotices();
    await waitFor(() => expect(screen.getByLabelText(draftLabel)).toHaveValue(longText + '第二次输入'));
    expect(screen.getAllByLabelText(/^未保存输入：/)).toHaveLength(2);
    closeNotices();
    await edit('销售方', seller, '重新核对后的备注');
    await screen.findByRole('cell', {name: '编辑 销售方，当前值 重新核对后的备注'});
    await openNotices();
    expect(screen.queryByLabelText(draftLabel)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: '丢弃未保存输入：发票 · 行 1 · 合计金额'}));
    expect(screen.queryByLabelText(/^未保存输入：/)).not.toBeInTheDocument();
    expect(screen.getByRole('cell', {name: '编辑 合计金额，当前值 16'})).toHaveTextContent('16');
  });

  it("shows one source disclosure in the notices for multiple partial rows from the same task", async () => {
    const originalFetch = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      const original = await originalFetch(String(input), init);
      if (!String(input).includes("/tables/invoice-v1?")) return original;
      const data = await original.json();
      data.rows[0].input_scope = {
        version: 1, rule: "head_tail_v1", coverage: "partial", selected_units: 2, total_units: 10,
        selected: [{ kind: "page", start: 1, end: 1 }, { kind: "page", start: 10, end: 10 }],
        omitted: [{ location: { kind: "page", start: 2, end: 9 }, reason: "input_budget" }],
        text_characters: 0, text_budget: 200, image_budget: 2, notes: [],
      };
      data.rows[0].values.source_filename = "长发票.pdf";
      data.rows.push({ ...data.rows[0], id: 2, item_index: 1 });
      return response(data);
    }));
    render(<DataTablePage />);
    fireEvent.click(await screen.findByRole("button", { name: "仓库事项" }));
    expect(screen.getByRole("dialog", { name: "仓库事项" })).toHaveTextContent("本页有局部读取的数据");
    expect(screen.getAllByText("长发票.pdf · 局部读取")).toHaveLength(1);
    expect(screen.getByText(/第 2–9 页/)).toHaveTextContent("达到设置的读取上限");
  });

  it("exposes a working keyboard column resize handle and full cell value", async () => {
    const { container } = render(<DataTablePage />);

    const handle = await screen.findByRole("separator", {
      name: "调整“销售方”列宽",
    });
    const header = handle.closest("th");
    expect(header).toHaveStyle({ width: "132px" });

    fireEvent.keyDown(handle, { key: "ArrowLeft" });

    expect(header).toHaveStyle({ width: "120px" });
    expect(
      screen.getByTitle("一家名称非常长但不应覆盖相邻字段的供应商有限公司"),
    ).toBeInTheDocument();
    expect(container.querySelector("colgroup")).not.toBeNull();
  });

  it("keeps data unchanged until the row deletion is explicitly confirmed", async () => {
    const originalFetch = globalThis.fetch;
    const mutations: RequestInit[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      if (String(input).includes("/edit-sessions")) return response({id: "edit-session"});
      if (init?.method === "DELETE") { mutations.push(init); return response({deleted_count: 1}); }
      return originalFetch(input, init);
    }));
    const {container} = render(<DataTablePage />);
    await screen.findByText("372");
    const actions = container.querySelector(".row-actions-bar");
    fireEvent.click(screen.getByRole("checkbox", {name: "勾选此行加入批量"}));
    fireEvent.click(screen.getByRole("button", {name: "删除选中"}));
    const confirmation = screen.getByRole("dialog", {name: "删除 1 条明细？"});
    expect(mutations).toHaveLength(0);
    expect(confirmation).toHaveTextContent("最后一条明细");
    expect(confirmation.closest(".data-table-wrap")).toBeNull();
    expect(container.querySelector(".row-actions-bar")).toBe(actions);
    fireEvent.click(within(confirmation).getByRole("button", {name: "取消"}));
    expect(screen.getByText("372")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {name: "删除选中"}));
    fireEvent.click(within(screen.getByRole("dialog", {name: "删除 1 条明细？"})).getByRole("button", {name: "确认删除"}));
    await waitFor(() => expect(mutations).toHaveLength(1));
    expect(JSON.parse(String(mutations[0].body))).toMatchObject({row_ids: [1], expected_versions: {"1": 1}});
  });

  it("lets keyboard users resize the previously inert table list divider", async () => {
    const {container} = render(<DataTablePage />);
    await screen.findByText("372");
    const divider = screen.getByRole("separator", {name: "调整数据表列表宽度"});
    expect(container.querySelector(".sheet-tree")).toHaveStyle({width: "240px"});
    fireEvent.keyDown(divider, {key: "ArrowRight"});
    expect(container.querySelector(".sheet-tree")).toHaveStyle({width: "252px"});
    fireEvent.keyDown(divider, {key: "End"});
    fireEvent.keyDown(divider, {key: "ArrowRight"});
    expect(divider).toHaveAttribute("aria-valuenow", "360");
  });

  it("keeps a fixed action bar for file cards and asks before clearing a selected source", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      const url = String(input);
      const collection = {...table, collection_kind: "files", file_count: 1};
      if (url.endsWith("/tables")) return response([collection]);
      if (url.includes("/views") || url.includes("/templates")) return response([]);
      if (url.includes("/source-cards?")) return response({table_id: table.id, page: 1, page_size: 6, total: 1, row_count: 1000, groups: [sourceCard(1)]});
      return response({...collection, columns, rows: [], page: 1, page_size: 10});
    }));
    const {container} = render(<DataTablePage />);
    await screen.findByText("资料1.pdf");
    const bar = container.querySelector(".row-actions-bar");
    expect(screen.getByRole("button", {name: "清空来源"})).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", {name: "选择完整来源 资料1.pdf"}));
    expect(container.querySelector(".row-actions-bar")).toBe(bar);
    const action = screen.getByRole("button", {name: "清空来源"});
    expect(action).toBeEnabled();
    fireEvent.click(action);
    const confirm = screen.getByRole("dialog", {name: "清空来源"});
    expect(confirm).toHaveTextContent("1000 条当前记录");
    expect(within(confirm).getByRole("list", {name: "将清空的完整来源"})).toHaveTextContent("资料1.pdf · 1000 条");
    expect(confirm).toHaveTextContent("文件原件和修订历史保留");
    expect(confirm.closest(".data-table-wrap")).toBeNull();
  });

  it("reports a populated custom-column deletion refusal without losing its confirmation", async () => {
    const originalFetch = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      if (init?.method === "DELETE") return new Response(JSON.stringify({detail: "这一列仍有当前数据，不能删除。"}), {status: 409});
      const original = await originalFetch(input, init);
      if (!String(input).includes("/tables/invoice-v1?")) return original;
      const body = await original.json();
      body.columns.push({key: "note", label: "备注", value_type: "text", section: "header", user_defined: true});
      return response(body);
    }));
    render(<DataTablePage />);
    await screen.findByText("372");
    fireEvent.click(screen.getByRole("button", {name: "字段设置"}));
    fireEvent.click(screen.getByRole("button", {name: "删除附加列 备注"}));
    const confirm = screen.getByRole("dialog", {name: "删除附加列“备注”？"});
    expect(confirm).toHaveTextContent("没有当前值");
    fireEvent.click(within(confirm).getByRole("button", {name: "确认删除"}));
    expect(await within(confirm).findByRole("alert")).toHaveTextContent("这一列仍有当前数据，不能删除");
    expect(confirm).toBeInTheDocument();
  });

  it("creates split-sheet views without copying fact rows", async () => {
    render(<DataTablePage />);

    const splitButton = await screen.findByRole("button", { name: "分 Sheet" });
    await waitFor(() => expect(splitButton).toBeEnabled());
    fireEvent.click(splitButton);
    fireEvent.change(screen.getByRole("combobox", { name: "用于分类的字段" }), {
      target: { value: "seller_name" },
    });
    fireEvent.click(screen.getByRole("button", { name: "应用分组" }));

    const view = await screen.findByRole("button", { name: /供应商甲/ });
    fireEvent.click(view);

    expect(await screen.findByText("分组 · 发票")).toBeInTheDocument();
  });

  it("disables multi-sheet export until split-sheet views exist", async () => {
    render(<DataTablePage />);

    const exportButton = await screen.findByRole("button", { name: "导出" });
    await waitFor(() => expect(exportButton).toBeEnabled());
    fireEvent.click(exportButton);
    const viewsExport = screen.getByRole("button", { name: /按分 Sheet 导出/ });

    expect(viewsExport).toBeDisabled();
    expect(viewsExport).toHaveAttribute("title", "当前数据表还没有分 Sheet，请先创建分 Sheet");
  });

  it("clears hidden row selection when search changes and allows a fresh selection after repeated searches", async () => {
    render(<DataTablePage />);
    await screen.findByText("372");
    for (const search of ["供应商甲", "", "找不到的内容"]) {
      fireEvent.click(screen.getByRole("checkbox", {name: "勾选此行加入批量"}));
      expect(screen.getByRole("button", {name: "删除选中"})).toBeEnabled();
      fireEvent.change(screen.getByRole("textbox", {name: "搜索当前表"}), {target: {value: search}});
      // A changed scope must not expose the previous query's rows or actions.
      expect(screen.queryByRole("button", {name: "删除选中"})).not.toBeInTheDocument();
      await screen.findByRole("checkbox", {name: "勾选此行加入批量"});
      expect(screen.getByRole("button", {name: "删除选中"})).toBeDisabled();
      expect(screen.getByRole("checkbox", {name: "勾选此行加入批量"})).not.toBeChecked();
      expect(screen.getByRole("status")).toHaveTextContent("搜索范围已改变，已清除之前的选择。");
      await waitFor(() => expect(screen.queryByText("正在加载数据…")).not.toBeInTheDocument());
    }
    expect(vi.mocked(globalThis.fetch).mock.calls.every(([,init]) => init?.method !== "DELETE")).toBe(true);
  });

  it("exports selected details with the page search and reports server rejection in place", async () => {
    const originalFetch = globalThis.fetch;
    const exports: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/export.json")) {
        exports.push(url);
        return new Response(JSON.stringify({detail: "选中明细已变化，请刷新后重新导出。"}), {status: 409});
      }
      return originalFetch(input, init);
    }));
    render(<DataTablePage />);
    await screen.findByText("372");
    fireEvent.change(screen.getByRole("textbox", {name: "搜索当前表"}), {target: {value: "供应商甲"}});
    await waitFor(() => expect(vi.mocked(globalThis.fetch).mock.calls.some(([url]) => String(url).includes("search="))).toBe(true));
    fireEvent.click(screen.getByRole("checkbox", {name: "勾选此行加入批量"}));
    fireEvent.click(screen.getByRole("button", {name: "导出"}));
    expect(screen.getByRole("note")).toHaveTextContent("选中 1 条明细");
    fireEvent.click(screen.getByRole("button", {name: /导出 JSON/}));
    expect(await screen.findByText("选中明细已变化，请刷新后重新导出。")).toBeInTheDocument();
    const query = new URL(exports[0], "http://localhost").searchParams;
    expect(query.get("row_ids")).toBe("1");
    expect(query.get("search")).toBe("供应商甲");
  });

  it("keeps the file and target while asking which worksheet to import", async () => {
    const originalFetch = globalThis.fetch;
    const imports: FormData[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      if (String(input).endsWith("/import-new")) {
        imports.push(init?.body as FormData);
        return imports.length === 1
          ? new Response(JSON.stringify({detail: {code: "select_sheet", sheets: ["第一批", "第二批"]}}), {status: 409})
          : response(table);
      }
      return originalFetch(input, init);
    }));
    const {container} = render(<DataTablePage />);
    await screen.findByText("372");
    fireEvent.change(container.querySelectorAll('input[type="file"]')[1], {target: {files: [new File(["test"], "两页.xlsx")]}});
    expect(await screen.findByRole("dialog", {name: "选择要导入的工作表"})).toBeInTheDocument();
    expect(imports).toHaveLength(1);
    fireEvent.change(screen.getByRole("combobox", {name: "工作表"}), {target: {value: "第二批"}});
    fireEvent.click(screen.getByRole("button", {name: "导入此工作表"}));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(imports).toHaveLength(2);
    expect(imports[1].get("sheet_name")).toBe("第二批");
    expect((imports[1].get("file") as File).name).toBe("两页.xlsx");
  });

  it("removes grouping configuration rather than only switching back to the table", async () => {
    const originalFetch = globalThis.fetch;
    const clear = vi.fn();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      if (String(input).endsWith('/views') && init?.method === 'DELETE') {
        clear(); return new Response(null, {status: 204});
      }
      return originalFetch(input, init);
    }));
    render(<DataTablePage />);
    await screen.findByText("372");
    fireEvent.click(screen.getByRole('button', {name: '分 Sheet'}));
    fireEvent.change(screen.getByRole('combobox', {name: '分组方式'}), {target: {value: 'none'}});
    fireEvent.click(screen.getByRole('button', {name: '应用分组'}));
    expect(await screen.findByText('已取消分组。')).toBeInTheDocument();
    expect(clear).toHaveBeenCalledOnce();
    expect(screen.getByText('372')).toBeInTheDocument();
  });

  it("pages categories and ignores a pending page after grouping is removed", async () => {
    const originalFetch = globalThis.fetch;
    const categories = Array.from({length: 200}, (_, i) => ({id: `group-${i}`, table_id: table.id,
      name: `分类${i}`, field_key: 'seller_name', field_value: String(i), row_count: 1, created_at: '', updated_at: ''}));
    let finishPage: ((response: Response) => void) | undefined;
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/views') && init?.method === 'DELETE') return new Response(null, {status: 204});
      if (url.endsWith('/views')) return response(categories);
      if (url.includes('/views?offset=200')) return new Promise<Response>(resolve => { finishPage = resolve; });
      return originalFetch(input, init);
    }));
    render(<DataTablePage />);
    fireEvent.click(await screen.findByRole('button', {name: '更多分组'}));
    fireEvent.click(screen.getByRole('button', {name: '分 Sheet'}));
    fireEvent.change(screen.getByRole('combobox', {name: '分组方式'}), {target: {value: 'none'}});
    fireEvent.click(screen.getByRole('button', {name: '应用分组'}));
    await screen.findByText('已取消分组。');
    finishPage?.(new Response(JSON.stringify([{...categories[0], id: 'late', name: '过期分类'}])));
    await waitFor(() => expect(screen.queryByRole('button', {name: /分类/})).not.toBeInTheDocument());
    expect(screen.getByText('372')).toBeInTheDocument();
  });

  it("does not let a stale table request overwrite a successful merge", async () => {
    const second = { ...table, id: "receipt-v1", name: "收据", template_key: "receipt" };
    const merged = { ...table, id: "merged-v1", name: "合并结果", template_key: "merged" };
    let tables = [table, second];
    let rejectOldViews: ((reason: Error) => void) | undefined;
    const oldViews = new Promise<never>((_, reject) => {
      rejectOldViews = reject;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (input: string | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/templates?include_inactive=true")) return response([]);
        if (url.endsWith("/tables/merge") && init?.method === "POST") {
          tables = [...tables, merged];
          return response(merged);
        }
        if (url.endsWith("/tables")) return response(tables);
        if (url.endsWith("/tables/invoice-v1/views")) return oldViews;
        if (url.endsWith("/tables/receipt-v1/views") || url.endsWith("/tables/merged-v1/views")) {
          return response([]);
        }
        if (url.includes("/tables/invoice-v1?") || url.includes("/tables/merged-v1?")) {
          const selected = url.includes("merged-v1") ? merged : table;
          return response({ ...selected, columns, page: 1, page_size: 10, rows: [] });
        }
        throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
      }),
    );

    render(<DataTablePage />);
    fireEvent.click(await screen.findByRole("button", { name: "合并表" }));
    fireEvent.click(screen.getByRole("checkbox", { name: /发票/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /收据/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "合并表名称" }), {
      target: { value: "合并结果" },
    });
    fireEvent.click(screen.getByRole("button", { name: "确认合并" }));

    expect(await screen.findByRole("heading", { name: "合并结果" })).toBeInTheDocument();
    rejectOldViews?.(new TypeError("旧请求断开"));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByText("无法连接本地后端服务，请确认 API 已启动。")).not.toBeInTheDocument();
  });
});

function response(body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), {headers: {"Content-Type": "application/json"}}));
}
