import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DataTablePage } from "./DataTablePage";

const tableId = "audit-table";
const table = {
  id: tableId,
  name: "发票仓库",
  template_key: "invoice",
  template_version: "1",
  document_kind: "invoice",
  row_count: 4,
  collection_kind: "table",
  created_at: "2026-01-01T00:00:00Z",
};
const columns = [
  { key: "seller_name", label: "销售方", value_type: "text", section: "header" },
  { key: "invoice_no", label: "单据号码", value_type: "text", section: "item" },
];

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function viewRecord(id: string, name: string, value: string, rowCount: number) {
  return {
    id,
    table_id: tableId,
    name,
    field_key: "seller_name",
    field_value: value,
    row_count: rowCount,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

function tableDetail(rows: Array<Record<string, unknown>>) {
  return { ...table, columns, page: 1, page_size: 10, rows };
}

function row(id: number, seller: string, invoiceNo: string) {
  return {
    id,
    task_id: null,
    item_index: id,
    version: 1,
    shared_header_keys: ["seller_name"],
    values: { seller_name: seller, invoice_no: invoiceNo },
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

const previousCreateObjectURL = URL.createObjectURL;
const previousRevokeObjectURL = URL.revokeObjectURL;
afterEach(() => {
  vi.restoreAllMocks();
  if (previousCreateObjectURL) Object.defineProperty(URL, "createObjectURL", { configurable: true, value: previousCreateObjectURL });
  else delete (URL as unknown as { createObjectURL?: unknown }).createObjectURL;
  if (previousRevokeObjectURL) Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: previousRevokeObjectURL });
  else delete (URL as unknown as { revokeObjectURL?: unknown }).revokeObjectURL;
});

describe("DataTablePage STATS-06 regression", () => {
  it("returns to the whole table when the selected group's last row moves and exports that scope", async () => {
    const rows = [
      row(1, "Alpha", "A-1"),
      row(2, "Alpha", "A-2"),
      row(3, "Beta", "B-1"),
      row(4, "Beta", "B-2"),
    ];
    const records = new Map([
      ["view-alpha", viewRecord("view-alpha", "Alpha", "Alpha", 2)],
      ["view-beta", viewRecord("view-beta", "Beta", "Beta", 2)],
      ["view-gamma", viewRecord("view-gamma", "Gamma", "Gamma", 0)],
    ]);
    const patchIds: number[] = [];
    const exportUrls: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    vi.stubGlobal("URL", URL);
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:audit-export") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const method = init?.method ?? "GET";
      if (url.pathname === "/api/v1/tables") return jsonResponse([table]);
      if (url.pathname === "/api/v1/templates") return jsonResponse([]);
      if (url.pathname.endsWith("/edit-sessions") && method === "POST") return jsonResponse({ id: "edit-session" });
      if (url.pathname.endsWith("/edit-sessions/edit-session/seal") && method === "POST") return new Response(null, { status: 204 });
      if (url.pathname === `/api/v1/tables/${tableId}/views`) {
        const active = [...records.values()].map(view => ({
          ...view,
          row_count: rows.filter(item => item.values.seller_name === view.field_value).length,
        })).filter(view => view.row_count > 0);
        const offset = Number(url.searchParams.get("offset") ?? 0);
        return jsonResponse(active.slice(offset, offset + 200));
      }
      const viewMatch = new RegExp(`/api/v1/tables/${tableId}/views/([^/]+)$`).exec(url.pathname);
      if (viewMatch) {
        const selected = records.get(viewMatch[1]);
        if (!selected) return jsonResponse({ detail: "分组不存在" }, 404);
        const matchedRows = rows.filter(item => item.values.seller_name === selected.field_value);
        return jsonResponse({
          ...tableDetail(matchedRows),
          ...selected,
          row_count: matchedRows.length,
          source_table_name: table.name,
        });
      }
      if (url.pathname === `/api/v1/tables/${tableId}/export.csv`) {
        exportUrls.push(url.toString());
        return new Response("销售方,单据号码\n", {
          status: 200,
          headers: { "Content-Disposition": "attachment; filename*=UTF-8''export.csv" },
        });
      }
      const rowMatch = new RegExp(`/api/v1/tables/${tableId}/rows/(\\d+)$`).exec(url.pathname);
      if (rowMatch && method === "PATCH") {
        const id = Number(rowMatch[1]);
        patchIds.push(id);
        const update = JSON.parse(String(init?.body)) as { changes: Record<string, unknown> };
        const target = rows.find(item => item.id === id)!;
        target.values = { ...target.values, ...update.changes };
        target.version += 1;
        return jsonResponse(target);
      }
      if (url.pathname === `/api/v1/tables/${tableId}`) return jsonResponse(tableDetail(rows));
      return jsonResponse({});
    }));

    render(<DataTablePage />);
    const selector = await screen.findByLabelText("选择数据表或分组") as HTMLSelectElement;
    await screen.findAllByRole("cell", { name: "编辑 销售方，当前值 Alpha" });
    fireEvent.change(selector, { target: { value: "view:view-alpha" } });

    for (const expectedId of [1, 2]) {
      const cell = (await screen.findAllByRole("cell", { name: "编辑 销售方，当前值 Alpha" }))[0];
      fireEvent.click(cell);
      const editor = cell.querySelector<HTMLElement>('[contenteditable="true"]');
      expect(editor).not.toBeNull();
      editor!.textContent = "Gamma";
      fireEvent.input(editor!);
      fireEvent.keyDown(editor!, { key: "Enter" });
      await waitFor(() => expect(patchIds).toContain(expectedId));
      if (expectedId === 1) {
        await waitFor(() => expect(
          screen.getAllByRole("cell", { name: "编辑 销售方，当前值 Alpha" }),
        ).toHaveLength(1));
      }
    }

    await waitFor(() => expect(selector).toHaveValue(`table:${tableId}`));
    await waitFor(() => {
      expect(screen.getAllByRole("cell", { name: "编辑 销售方，当前值 Gamma" })).toHaveLength(2);
      expect(screen.getAllByRole("cell", { name: "编辑 销售方，当前值 Beta" })).toHaveLength(2);
    });
    expect([...records.values()].find(view => view.id === "view-beta")?.row_count).toBe(2);
    expect(selector.querySelector('option[value="view:view-alpha"]')).toBeNull();

    const exportButton = screen.getByRole("button", { name: "导出" });
    await waitFor(() => expect(exportButton).toBeEnabled());
    fireEvent.click(exportButton);
    fireEvent.click(screen.getByRole("button", { name: /导出 CSV/ }));
    await waitFor(() => expect(exportUrls).toHaveLength(1));
    const exportedScope = new URL(exportUrls[0]);
    expect(exportedScope.searchParams.has("view_id")).toBe(false);
  });

  it("keeps a selected view on a later page when refreshing more than 200 views", async () => {
    const allViews = Array.from({ length: 205 }, (_, index) => viewRecord(
      `view-${index}`, `分类${String(index).padStart(3, "0")}`, `value-${index}`, 1,
    ));
    const offsets: number[] = [];
    const lastView = allViews[204];
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname === "/api/v1/tables") return jsonResponse([table]);
      if (url.pathname === "/api/v1/templates") return jsonResponse([]);
      if (url.pathname === `/api/v1/tables/${tableId}/views`) {
        const offset = Number(url.searchParams.get("offset") ?? 0);
        offsets.push(offset);
        return jsonResponse(allViews.slice(offset, offset + 200));
      }
      if (url.pathname === `/api/v1/tables/${tableId}/views/${lastView.id}`) {
        return jsonResponse({ ...tableDetail([row(205, "value-204", "205")]), ...lastView, source_table_name: table.name });
      }
      if (url.pathname === `/api/v1/tables/${tableId}`) return jsonResponse(tableDetail([row(1, "value-0", "1")]));
      return jsonResponse({});
    }));

    render(<DataTablePage />);
    const selector = await screen.findByLabelText("选择数据表或分组") as HTMLSelectElement;
    await screen.findByText("value-0");
    fireEvent.click(await screen.findByRole("button", { name: "更多分组" }));
    await screen.findByRole("option", { name: "分类204" });
    fireEvent.change(selector, { target: { value: `view:${lastView.id}` } });
    await screen.findByRole("heading", { name: "分类204" });

    offsets.length = 0;
    fireEvent(window, new CustomEvent("zhiyi:assistant-changed", { detail: { table_id: tableId } }));
    await waitFor(() => expect(offsets).toContain(200));
    await waitFor(() => expect(selector).toHaveValue(`view:${lastView.id}`));
    expect(await screen.findByRole("heading", { name: "分类204" })).toBeInTheDocument();
  });
});
