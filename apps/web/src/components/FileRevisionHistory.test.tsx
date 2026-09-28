import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { FileRevisionHistory } from "./FileRevisionHistory";
import { getFileRevision, getFileRevisions, type FileRevisionDetail, type FileRevisionSummary } from "../api";

vi.mock("../api", () => ({ getFileRevision: vi.fn(), getFileRevisions: vi.fn() }));
const version: FileRevisionSummary = {id: "v5", version: 5, operation: "edit", editor: "user",
  created_at: "2026-09-20T10:00:00Z", sealed: true, modified_fields: 3, deleted_details: 1, added_details: 0};
const details: FileRevisionDetail = {...version, schema: [
  {key: "customer", label: "客户旧名称", section: "header", value_type: "text"},
  {key: "amount", label: "金额", section: "item", value_type: "number"},
], header: {customer: {before: "甲公司", after: "乙公司"}}, items: [
  {before: {id: 4, item_index: 2, values: {amount: 10}}, after: {id: 4, item_index: 2, values: {amount: 12}}},
  {before: {id: 8, item_index: 5, values: {amount: 123.45, product: "已删除商品", active: false}}, after: null},
], page: 1, page_size: 20, total_details: 2};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getFileRevisions).mockResolvedValue({revisions: [version], total: 1});
  vi.mocked(getFileRevision).mockResolvedValue(details);
});

it("groups one file edit and retains full deleted detail under historical field labels", async () => {
  render(<FileRevisionHistory taskId="file-A" filename="A.png" onClose={vi.fn()} />);
  expect(await screen.findByText("客户旧名称")).toBeVisible();
  expect(screen.getByText("甲公司")).toBeVisible();
  expect(screen.getByText("乙公司")).toBeVisible();
  expect(screen.getByText("明细 2")).toBeVisible();
  expect(screen.getByText("明细 5 · 已删除")).toBeVisible();
  expect(screen.getByText("123.45")).toBeVisible();
  expect(screen.getByText("已删除商品")).toBeVisible();
  expect(screen.getByText("否")).toBeVisible();
  expect(screen.queryByRole("button", {name: /^(删除|恢复)/})).not.toBeInTheDocument();
  expect(getFileRevision).toHaveBeenCalledExactlyOnceWith("file-A", "v5", 1);
});

it("loads only the selected version and ignores a response from a previous file", async () => {
  let resolveOld!: (value: FileRevisionDetail) => void;
  vi.mocked(getFileRevision).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }));
  const {rerender} = render(<FileRevisionHistory taskId="file-A" filename="A.png" onClose={vi.fn()} />);
  await waitFor(() => expect(getFileRevision).toHaveBeenCalledWith("file-A", "v5", 1));
  vi.mocked(getFileRevisions).mockResolvedValue({revisions: [{...version, id: "v2", version: 2}], total: 1});
  vi.mocked(getFileRevision).mockResolvedValue({...details, id: "v2", version: 2, header: {customer: {before: "丙", after: "丁"}}, items: []});
  rerender(<FileRevisionHistory taskId="file-B" filename="B.png" onClose={vi.fn()} />);
  expect(await screen.findByText("丁")).toBeVisible();
  resolveOld(details);
  await waitFor(() => expect(screen.queryByText("甲公司")).not.toBeInTheDocument());
  expect(getFileRevision).toHaveBeenLastCalledWith("file-B", "v2", 1);
});

it("loads older revisions only on request", async () => {
  vi.mocked(getFileRevisions).mockResolvedValueOnce({revisions: [version], total: 2})
    .mockResolvedValueOnce({revisions: [{...version, id: "v4", version: 4}], total: 2});
  render(<FileRevisionHistory taskId="file-A" filename="A.png" onClose={vi.fn()} />);
  const more = await screen.findByRole("button", {name: "更早的版本"});
  await waitFor(() => expect(getFileRevision).toHaveBeenCalledTimes(1));
  fireEvent.click(more);
  const older = await screen.findByRole("button", {name: /V4/});
  expect(getFileRevision).toHaveBeenCalledTimes(1);
  fireEvent.click(older);
  await waitFor(() => expect(getFileRevision).toHaveBeenLastCalledWith("file-A", "v4", 1));
});

it("shows the last complete content when a detail was edited and then deleted", async () => {
  vi.mocked(getFileRevision).mockResolvedValue({...details, header: {}, items: [{
    before: {id: 8, item_index: 5, values: {amount: 10, product: "删除的商品"}}, after: null,
    deleted_before: {id: 8, item_index: 5, values: {amount: 99, product: "删除的商品"}},
  }]});
  render(<FileRevisionHistory taskId="file-A" filename="A.png" onClose={vi.fn()} />);
  expect(await screen.findByText("本轮修改后删除，以下保留删除前的完整内容。")).toBeVisible();
  expect(screen.getByText("10")).toBeVisible();
  expect(screen.getAllByText("99")).toHaveLength(2);
  expect(screen.getByText("删除的商品")).toBeVisible();
});
