import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { DataTablePage } from "./DataTablePage";

it.each(["header", "item"])("keeps the edit-start version for a %s cell when the same DOM receives refreshed facts", async section => {
  const table = { id: "synthetic-table", name: "并发核对", template_key: "synthetic", template_version: "1",
    document_kind: "custom", row_count: 1, created_at: "2026-11-01T00:00:00Z" };
  let version = 1;
  let amount = 1;
  const writes: Record<string, unknown>[] = [];
  const row = () => ({ id: 1, version, values: { amount }, task_id: section === "header" ? "source" : null,
    item_index: 0, shared_header_keys: section === "header" ? ["amount"] : [], created_at: "", updated_at: "" });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/tables")) return Response.json([table]);
    if (url.includes("/views") || url.includes("/templates")) return Response.json([]);
    if (url.endsWith("/edit-sessions")) return Response.json({ id: "synthetic-edit" });
    if (url.endsWith("/seal")) return new Response(null, { status: 204 });
    if (url.endsWith("/rows/1") && init?.method === "PATCH") {
      const body = JSON.parse(init.body as string);
      writes.push(body);
      if (body.expected_version !== version) return Response.json({ detail: "该行已更新" }, { status: 409 });
      amount = body.changes.amount;
      version += 1;
      return Response.json(row());
    }
    return Response.json({ ...table, columns: [{ key: "amount", label: "金额", value_type: "number", section }],
      rows: [row()], page: 1, page_size: 10 });
  }));
  render(<DataTablePage />);
  const cell = await screen.findByRole("cell", { name: "编辑 金额，当前值 1" });
  fireEvent.click(cell);
  const editor = cell.querySelector<HTMLElement>('[contenteditable="true"]')!;
  editor.textContent = "2";
  fireEvent.input(editor);
  amount = 99;
  version = 2;
  fireEvent(window, new CustomEvent("zhiyi:assistant-changed", { detail: { table_id: table.id } }));
  await screen.findByRole("cell", { name: "编辑 金额，当前值 99" });
  expect(cell.querySelector('[contenteditable="true"]')).toBe(editor);
  expect(editor).toHaveTextContent("2");
  fireEvent.keyDown(editor, { key: "Enter" });
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0].expected_version).toBe(1);
  expect(writes[0].expected_shared_header_keys).toEqual(section === "header" ? ["amount"] : []);
  expect(amount).toBe(99);
  fireEvent.click(await screen.findByRole("button", { name: "仓库事项" }));
  expect(await screen.findByLabelText("未保存输入：并发核对 · 行 1 · 金额")).toHaveValue("2");
});
