import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

describe("DataTablePage", () => {
  it("keeps pending source information visible in both presentations", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      const url = String(input);
      if (url.endsWith("/tables")) return response([table]);
      if (url.endsWith("/views") || url.includes("/templates")) return response([]);
      return response({ ...table, columns, page: 1, page_size: 10, rows: [{ id: 1, task_id: null, item_index: 1, version: 1,
        review_pending: true, values: { seller_name: "待核对内容", source_filename: "合成资料.txt" }, created_at: "", updated_at: "" }] });
    }));
    render(<DataTablePage />);
    expect(await screen.findByText("本页有 1 条来源待核对的数据")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "卡片" }));
    expect(await screen.findByText("来源待核对", { selector: ".badge" })).toBeInTheDocument();
    expect(screen.getByText("本页有 1 条来源待核对的数据")).toBeInTheDocument();
  });

  it("uses the measured card capacity and returns table mode to ten rows", async () => {
    const sizes: number[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      const url = String(input);
      if (url.endsWith("/tables")) return response([{ ...table, row_count: 6 }]);
      if (url.endsWith("/views") || url.includes("/templates")) return response([]);
      const params = new URL(url, "http://localhost").searchParams;
      const size = Number(params.get("page_size") || 10);
      const page = Number(params.get("page") || 1);
      sizes.push(size);
      return response({ ...table, row_count: 6, columns, presentation: { mode: "card", primary_fields: [], collapsed_fields: [] }, page, page_size: size,
        rows: Array.from({ length: 6 }, (_, i) => ({ id: i + 1, task_id: null, item_index: i, version: 1, values: { seller_name: `记录${i + 1}` }, created_at: "", updated_at: "" })).slice((page - 1) * size, page * size) });
    }));
    render(<DataTablePage />);
    await waitFor(() => expect(screen.getAllByRole("article")).toHaveLength(2));
    await waitFor(() => expect(sizes.at(-1)).toBe(2));
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    await waitFor(() => expect(screen.getAllByRole("article")[0]).toHaveTextContent("记录3"));
    fireEvent.click(screen.getByRole("button", { name: "表格" }));
    await waitFor(() => expect(sizes.at(-1)).toBe(10));
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
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

  it("shows one collapsed source disclosure for multiple partial rows from the same task", async () => {
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
    const summary = await screen.findByText("本页有局部读取的数据 · 查看来源范围");
    expect(summary.closest("details")).not.toHaveAttribute("open");
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
    fireEvent.click(exportButton);
    const viewsExport = screen.getByRole("button", { name: /导出全部分 Sheet/ });

    expect(viewsExport).toBeDisabled();
    expect(viewsExport).toHaveAttribute("title", "当前数据表还没有分 Sheet，请先创建分 Sheet");
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
    fireEvent.change(screen.getByRole("textbox", { name: "新表名称" }), {
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
  return Promise.resolve({
    ok: true,
    json: async () => body,
  });
}
