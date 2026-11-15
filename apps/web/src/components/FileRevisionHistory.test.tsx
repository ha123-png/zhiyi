import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getFileRevisions).mockResolvedValue({revisions: [version], total: 1});
  vi.mocked(getFileRevision).mockImplementation(async (_taskId, revisionId, page) => ({...details, id: revisionId, page: page ?? 1}));
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
  await act(async () => { resolveOld(details); });
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

it("shows an empty history only after a successful read", async () => {
  vi.mocked(getFileRevisions).mockResolvedValue({revisions: [], total: 0});
  render(<FileRevisionHistory taskId="file-A" filename="A.png" onClose={vi.fn()} />);
  expect(await screen.findByText("还没有修订记录。")).toBeVisible();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(getFileRevision).not.toHaveBeenCalled();
});

it("distinguishes an initial failure from an empty history and retries the same file in place", async () => {
  const initial = deferred<Awaited<ReturnType<typeof getFileRevisions>>>();
  vi.mocked(getFileRevisions).mockReturnValueOnce(initial.promise);
  render(<FileRevisionHistory taskId="file-A" filename="A.png" onClose={vi.fn()} />);
  expect(screen.getByText("读取中…")).toBeVisible();
  expect(screen.queryByText("还没有修订记录。")).not.toBeInTheDocument();
  await act(async () => { initial.reject(new Error("合成版本列表读取失败")); });
  expect(await screen.findByRole("alert")).toHaveTextContent("合成版本列表读取失败");
  expect(screen.queryByText("还没有修订记录。")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", {name: "重新读取版本"}));
  expect(await screen.findByText("乙公司")).toBeVisible();
  expect(getFileRevisions).toHaveBeenNthCalledWith(2, "file-A", 1);
  expect(screen.getByRole("button", {name: /V5/})).toHaveAttribute("aria-pressed", "true");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("retries a failed older-version page without skipping it or replacing the selected revision", async () => {
  const older = {...version, id: "v4", version: 4};
  vi.mocked(getFileRevisions).mockResolvedValueOnce({revisions: [version], total: 3})
    .mockRejectedValueOnce(new Error("合成更早版本读取失败"))
    .mockResolvedValueOnce({revisions: [older], total: 3});
  render(<FileRevisionHistory taskId="file-A" filename="A.png" onClose={vi.fn()} />);
  expect(await screen.findByText("乙公司")).toBeVisible();
  fireEvent.click(screen.getByRole("button", {name: "更早的版本"}));
  expect(await screen.findByRole("alert")).toHaveTextContent("合成更早版本读取失败");
  fireEvent.click(screen.getByRole("button", {name: /^(更早的版本|重新读取更早的版本)$/}));
  await screen.findByRole("button", {name: /V4/});
  expect(getFileRevisions).toHaveBeenLastCalledWith("file-A", 2);
  expect(screen.getByRole("button", {name: /V5/})).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByText("乙公司")).toBeVisible();
  expect(getFileRevision).toHaveBeenCalledTimes(1);
});

it("keeps a list failure visible when another loaded revision's detail succeeds", async () => {
  vi.mocked(getFileRevisions).mockResolvedValueOnce({revisions: [version, {...version, id: "v4", version: 4}], total: 3})
    .mockRejectedValueOnce(new Error("合成更早版本读取失败"));
  render(<FileRevisionHistory taskId="file-A" filename="A.png" onClose={vi.fn()} />);
  await screen.findByText("乙公司");
  fireEvent.click(screen.getByRole("button", {name: "更早的版本"}));
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", {name: /V4/}));
  await waitFor(() => expect(getFileRevision).toHaveBeenLastCalledWith("file-A", "v4", 1));
  expect(screen.getByRole("alert")).toHaveTextContent("合成更早版本读取失败");
});

it("retries a failed selected revision without requiring another version or reopening the dialog", async () => {
  vi.mocked(getFileRevision).mockRejectedValueOnce(new Error("合成变化读取失败"));
  render(<FileRevisionHistory taskId="file-A" filename="A.png" onClose={vi.fn()} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("合成变化读取失败");
  expect(screen.queryByText("还没有修订记录。")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", {name: "重新读取变化"}));
  expect(await screen.findByText("乙公司")).toBeVisible();
  expect(getFileRevision).toHaveBeenNthCalledWith(2, "file-A", "v5", 1);
  expect(screen.getByRole("button", {name: /V5/})).toHaveAttribute("aria-pressed", "true");
  expect(getFileRevisions).toHaveBeenCalledTimes(1);
});

it("retains the detail pager and its focus while a long revision page is loading", async () => {
  const nextPage = deferred<FileRevisionDetail>();
  vi.mocked(getFileRevision).mockResolvedValueOnce({...details, total_details: 41})
    .mockReturnValueOnce(nextPage.promise);
  render(<FileRevisionHistory taskId="file-A" filename="A.png" onClose={vi.fn()} />);
  const next = await screen.findByRole("button", {name: "下一页明细"});
  next.focus();
  fireEvent.click(next);
  expect(next).toBeInTheDocument();
  expect(next).toHaveFocus();
  expect(next).toHaveAttribute("aria-disabled", "true");
  fireEvent.click(next);
  expect(getFileRevision).toHaveBeenCalledTimes(2);
  await act(async () => { nextPage.resolve({...details, page: 2, total_details: 41}); });
  expect(screen.getByText("2 / 3")).toBeVisible();
  expect(screen.getByRole("button", {name: "下一页明细"})).toBe(next);
  expect(next).toHaveFocus();
  expect(next).toHaveAttribute("aria-disabled", "false");
});

it("keeps keyboard focus in the dialog when retry disappears and the final page disables its pager", async () => {
  const retryPage = deferred<FileRevisionDetail>();
  vi.mocked(getFileRevision).mockResolvedValueOnce({...details, total_details: 21})
    .mockRejectedValueOnce(new Error("合成第二页失败"))
    .mockReturnValueOnce(retryPage.promise);
  render(<FileRevisionHistory taskId="file-A" filename="A.png" onClose={vi.fn()} />);
  const next = await screen.findByRole("button", {name: "下一页明细"});
  next.focus();
  fireEvent.click(next);
  const retry = await screen.findByRole("button", {name: "重新读取变化"});
  expect(next).toHaveFocus();
  retry.focus();
  fireEvent.click(retry);
  const content = screen.getByLabelText("修订变化");
  expect(content).toHaveFocus();
  await act(async () => { retryPage.resolve({...details, page: 2, total_details: 21}); });
  expect(next).toBeDisabled();
  expect(content).toHaveFocus();
  fireEvent.keyDown(content, {key: "Tab"});
  expect(screen.getByRole("button", {name: "关闭文件修订历史"})).toHaveFocus();
});

it("does not move focus away from a version selected during a delayed page read", async () => {
  const delayed = deferred<FileRevisionDetail>();
  vi.mocked(getFileRevisions).mockResolvedValue({revisions: [version, {...version, id: "v4", version: 4}], total: 2});
  vi.mocked(getFileRevision).mockResolvedValueOnce({...details, total_details: 21})
    .mockReturnValueOnce(delayed.promise)
    .mockResolvedValueOnce({...details, id: "v4", version: 4});
  render(<FileRevisionHistory taskId="file-A" filename="A.png" onClose={vi.fn()} />);
  const next = await screen.findByRole("button", {name: "下一页明细"});
  next.focus(); fireEvent.click(next);
  const older = screen.getByRole("button", {name: /V4/});
  older.focus(); fireEvent.click(older);
  await waitFor(() => expect(screen.getByRole("heading", {name: /V4 ·/})).toBeVisible());
  await act(async () => { delayed.resolve({...details, page: 2, total_details: 21}); });
  expect(older).toHaveFocus();
  expect(screen.getByRole("heading", {name: /V4 ·/})).toBeVisible();
});

it("keeps the last successful detail page on failure and retries the requested page", async () => {
  vi.mocked(getFileRevision).mockResolvedValueOnce({...details, total_details: 41})
    .mockRejectedValueOnce(new Error("合成第二页变化读取失败"))
    .mockResolvedValueOnce({...details, page: 2, total_details: 41, items: [], header: {customer: {before: "第二页旧值", after: "第二页新值"}}});
  render(<FileRevisionHistory taskId="file-A" filename="A.png" onClose={vi.fn()} />);
  const next = await screen.findByRole("button", {name: "下一页明细"});
  fireEvent.click(next);
  expect(await screen.findByRole("alert")).toHaveTextContent("第 2 页变化读取失败：合成第二页变化读取失败");
  expect(screen.getByText("仍显示第 1 页变化。")).toBeVisible();
  expect(screen.getByText("乙公司")).toBeVisible();
  expect(screen.getByText("1 / 3")).toBeVisible();
  expect(screen.getByRole("button", {name: "上一页明细"})).toBeDisabled();
  fireEvent.click(screen.getByRole("button", {name: "重新读取变化"}));
  expect(await screen.findByText("第二页新值")).toBeVisible();
  expect(screen.queryByText("乙公司")).not.toBeInTheDocument();
  expect(screen.getByText("2 / 3")).toBeVisible();
  expect(getFileRevision).toHaveBeenNthCalledWith(2, "file-A", "v5", 2);
  expect(getFileRevision).toHaveBeenNthCalledWith(3, "file-A", "v5", 2);
});

it("recovers list and detail failures independently without repeating the other read", async () => {
  vi.mocked(getFileRevisions).mockResolvedValueOnce({revisions: [version], total: 2})
    .mockRejectedValueOnce(new Error("合成更早版本读取失败"))
    .mockResolvedValueOnce({revisions: [{...version, id: "v4", version: 4}], total: 2});
  vi.mocked(getFileRevision).mockRejectedValueOnce(new Error("合成变化读取失败"));
  render(<FileRevisionHistory taskId="file-A" filename="A.png" onClose={vi.fn()} />);
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", {name: "更早的版本"}));
  await waitFor(() => expect(screen.getAllByRole("alert")).toHaveLength(2));
  fireEvent.click(screen.getByRole("button", {name: "重新读取更早的版本"}));
  await screen.findByRole("button", {name: /V4/});
  expect(screen.getByRole("alert")).toHaveTextContent("合成变化读取失败");
  expect(getFileRevision).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", {name: "重新读取变化"}));
  expect(await screen.findByText("乙公司")).toBeVisible();
  expect(getFileRevisions).toHaveBeenCalledTimes(3);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("ignores a late revision list after switching to another file", async () => {
  const oldList = deferred<Awaited<ReturnType<typeof getFileRevisions>>>();
  vi.mocked(getFileRevisions).mockReturnValueOnce(oldList.promise)
    .mockResolvedValueOnce({revisions: [{...version, id: "v2", version: 2}], total: 1});
  const view = render(<FileRevisionHistory taskId="file-A" filename="A.png" onClose={vi.fn()} />);
  view.rerender(<FileRevisionHistory taskId="file-B" filename="B.png" onClose={vi.fn()} />);
  await screen.findByRole("button", {name: /V2/});
  await act(async () => { oldList.resolve({revisions: [{...version, id: "v99", version: 99}], total: 1}); });
  expect(screen.queryByRole("button", {name: /V99/})).not.toBeInTheDocument();
  expect(getFileRevision).toHaveBeenCalledExactlyOnceWith("file-B", "v2", 1);
});

it("ignores a late detail response after selecting another revision of the same file", async () => {
  const oldDetail = deferred<FileRevisionDetail>();
  vi.mocked(getFileRevisions).mockResolvedValue({revisions: [version, {...version, id: "v4", version: 4}], total: 2});
  vi.mocked(getFileRevision).mockReturnValueOnce(oldDetail.promise)
    .mockResolvedValueOnce({...details, id: "v4", version: 4, items: [], header: {customer: {before: "丙公司", after: "丁公司"}}});
  render(<FileRevisionHistory taskId="file-A" filename="A.png" onClose={vi.fn()} />);
  await waitFor(() => expect(getFileRevision).toHaveBeenCalledWith("file-A", "v5", 1));
  fireEvent.click(screen.getByRole("button", {name: /V4/}));
  expect(await screen.findByText("丁公司")).toBeVisible();
  await act(async () => { oldDetail.resolve(details); });
  expect(screen.queryByText("甲公司")).not.toBeInTheDocument();
  expect(screen.getByRole("button", {name: /V4/})).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByText("丁公司")).toBeVisible();
});

it("returns focus to the opener when the dialog closes and forwards Escape cancellation", async () => {
  render(<button>查看合成文件历史</button>);
  const opener = screen.getByRole("button", {name: "查看合成文件历史"});
  opener.focus();
  const onClose = vi.fn();
  const view = render(<FileRevisionHistory taskId="file-A" filename="A.png" onClose={onClose} />);
  await screen.findByText("乙公司");
  screen.getByRole("button", {name: "关闭文件修订历史"}).focus();
  const cancel = new Event("cancel", {cancelable: true});
  fireEvent(screen.getByRole("dialog", {name: "文件修订历史"}), cancel);
  expect(cancel.defaultPrevented).toBe(true);
  expect(onClose).toHaveBeenCalledOnce();
  view.unmount();
  expect(opener).toHaveFocus();
});
