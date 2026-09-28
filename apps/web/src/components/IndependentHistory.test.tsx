import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { IndependentHistory } from "./IndependentHistory";
import { getIndependentHistory } from "../api";

vi.mock("../api", () => ({getIndependentHistory: vi.fn()}));

it("keeps deleted content readable without current rows and pages older history", async () => {
  vi.mocked(getIndependentHistory).mockResolvedValue({page: 1, page_size: 1, total: 2, revisions: [{
    id: 42, row_id: 99, version: 2, operation: "delete", before: {amount: 31, present: false, empty: null}, after: null,
    editor: "local-user", created_at: "2026-09-21T00:00:00Z",
  }]});
  render(<IndependentHistory tableId="table" tableName="测试表" columns={[{key: "amount", label: "金额", value_type: "number"}]} onClose={vi.fn()} />);
  const deletion = await screen.findByRole("button", {name: /记录 #99 · 已删除/});
  expect(deletion).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(deletion);
  expect(screen.getByText("以下保留删除前的完整内容。")).toBeInTheDocument();
  expect(screen.getByText("31")).toBeInTheDocument();
  expect(screen.getByText("否")).toBeInTheDocument();
  expect(screen.getByText("empty")).toBeInTheDocument();
  expect(screen.queryByRole("button", {name: /^(删除|恢复)/})).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", {name: "更早记录"}));
  await waitFor(() => expect(getIndependentHistory).toHaveBeenLastCalledWith("table", 2, undefined));
});
