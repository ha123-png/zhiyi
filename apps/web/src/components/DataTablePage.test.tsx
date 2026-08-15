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
    fireEvent.click(screen.getByRole("button", { name: "创建分 Sheet" }));

    const view = await screen.findByRole("button", { name: /供应商甲/ });
    fireEvent.click(view);

    expect(await screen.findByText("视图 · 来源 发票")).toBeInTheDocument();
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
