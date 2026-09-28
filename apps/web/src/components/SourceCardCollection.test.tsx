import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { clearSourceCards, deleteDataRows, getSourceCards, getSourceDetails, updateDataRow } from "../api";
import { SourceCardCollection } from "./SourceCardCollection";
import type { SourceCard, SourceCardDetail } from "../types";

vi.mock("../api", () => ({getSourceCards: vi.fn(), getSourceDetails: vi.fn(), updateDataRow: vi.fn(), clearSourceCards: vi.fn(), deleteDataRows: vi.fn(), getOriginalFileUrl: (id: string) => `/files/${id}`}));
vi.mock("./DocumentPreview", () => ({DocumentPreview: ({filename, scale}: {filename: string; scale: number}) => <div data-testid="document-preview" data-scale={scale}>原件：{filename}</div>}));
vi.mock("./FileRevisionHistory", () => ({FileRevisionHistory: ({taskId}: {taskId: string}) => <div>文件历史：{taskId}</div>}));
const source: SourceCard = {id: '{"id":"file-a","kind":"file"}', kind: "file", task_id: "file-a", filename: "订单.pdf", version: "a".repeat(64),
  first_row_id: 7, row_count: 1000, item_count: 1000, matched_row_count: 1000, complete: true, review_pending: false, partial_input: false,
  shared_header_keys: ["customer"], preview_header: [{key: "customer", label: "客户", value: "甲", truncated: false}],
  preview_items: [{id: 7, item_index: 1, fields: [{key: "quantity", label: "数量", value: 0, truncated: false}]}]};
const detail: SourceCardDetail = {...source, page: 1, page_size: 10, total: 1000, header: {customer: "甲"},
  schema: [{key: "customer", label: "客户", section: "header", value_type: "text"}, {key: "quantity", label: "数量", section: "item", value_type: "number"}],
  rows: [{id: 7, item_index: 1, version: 8, values: {quantity: 0}, task_id: "file-a", created_at: "", updated_at: ""}]};
let tableId = ""; let caseNumber = 0;
beforeEach(() => {
  tableId = `test-table-${++caseNumber}`;
  vi.clearAllMocks(); localStorage.clear();
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  vi.mocked(getSourceCards).mockResolvedValue({table_id: "t", page: 1, page_size: 6, total: 1, row_count: 1000, groups: [source]});
  vi.mocked(getSourceDetails).mockResolvedValue(detail);
  vi.mocked(updateDataRow).mockResolvedValue(detail.rows[0]);
  vi.mocked(clearSourceCards).mockResolvedValue({deleted: 1000, sources: 1});
  vi.mocked(deleteDataRows).mockResolvedValue();
});
function show(props: Partial<Parameters<typeof SourceCardCollection>[0]> = {}) {
  const onChanged = vi.fn(); const onSelect = vi.fn(); const onSelectRow = vi.fn();
  render(<SourceCardCollection tableId={tableId} selected={[]} onSelect={onSelect} onSelectRow={onSelectRow} onChanged={onChanged} {...props} />);
  return {onChanged, onSelect, onSelectRow};
}
async function open() {
  fireEvent.click(await screen.findByRole("button", {name: "查看与编辑"}));
  await waitFor(() => expect(screen.getByRole("button", {name: "编辑共享字段"})).toBeEnabled());
}

it("keeps 1000 details in one selectable source and loads only a page on demand", async () => {
  const callbacks = show();
  expect(await screen.findByText("1000 条明细")).toBeInTheDocument();
  expect(screen.getAllByRole("article")).toHaveLength(1);
  expect(getSourceDetails).not.toHaveBeenCalled();
  expect(screen.getByText("0")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("checkbox", {name: "选择完整来源 订单.pdf"}));
  expect(callbacks.onSelect).toHaveBeenCalledWith([source]);
  await open();
  expect(getSourceDetails).toHaveBeenCalledWith(tableId, source.id, {}, 1);
  fireEvent.click(screen.getByRole("checkbox", {name: "选择明细 1"}));
  expect(callbacks.onSelectRow).toHaveBeenCalledWith(detail.rows[0], true);
  fireEvent.click(screen.getByRole("button", {name: "下一页明细"}));
  await waitFor(() => expect(getSourceDetails).toHaveBeenLastCalledWith(tableId, source.id, {}, 2));
});

it("puts a deep-search hit first in the visible card excerpt", async () => {
  const tailExcerpt = `…${"末尾附近上下文。".repeat(5)}守恒条件。继续核对边界。`;
  vi.mocked(getSourceCards).mockResolvedValue({table_id: "t", page: 1, page_size: 6, total: 1, row_count: 1000,
    groups: [{...source, preview_items: [{...source.preview_items[0], fields: [
      {key: "long_answer", label: "回答", value: tailExcerpt, truncated: true},
    ]}]}]});
  show({scope: {search: "守恒条件"}});
  const hit = await screen.findByRole("mark");
  expect(hit).toHaveTextContent("守恒条件");
  expect(hit.parentElement?.textContent?.startsWith("守恒条件" )).toBe(true);
});

it("does not promote partial search results to complete file selection or clearing", async () => {
  vi.mocked(getSourceCards).mockResolvedValue({table_id: "t", page: 1, page_size: 6, total: 1, row_count: 1,
    groups: [{...source, complete: false, matched_row_count: 1}]});
  vi.mocked(getSourceDetails).mockResolvedValue({...detail, complete: false, total: 1});
  show({scope: {search: "局部"}});
  expect(await screen.findByRole("checkbox", {name: "选择完整来源 订单.pdf"})).toBeDisabled();
  fireEvent.click(screen.getByRole("button", {name: "查看与编辑"}));
  expect(await screen.findByText("当前筛选匹配 1 / 1000 条记录")).toBeInTheDocument();
  expect(screen.getByRole("button", {name: "编辑共享字段"})).toBeDisabled();
  fireEvent.keyDown(screen.getByRole("button", {name: "更多数据操作"}), {key: "ArrowDown"});
  expect(await screen.findByRole("menuitem", {name: "清空此来源数据"})).toHaveAttribute("aria-disabled", "true");
  expect(getSourceDetails).toHaveBeenCalledWith(tableId, source.id, {search: "局部"}, 1);
});

it("edits shared values with their version and reveals deletion only for the edited detail", async () => {
  const {onChanged} = show(); await open();
  expect(screen.queryByRole("button", {name: /删除/})).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", {name: "编辑共享字段"}));
  fireEvent.change(screen.getByRole("textbox", {name: "客户"}), {target: {value: "乙"}});
  fireEvent.click(screen.getByRole("button", {name: "提交修改"}));
  await waitFor(() => expect(updateDataRow).toHaveBeenCalledWith(tableId, 7, 8, {customer: "乙"}, undefined, ["customer"]));
  expect(onChanged).toHaveBeenCalledTimes(1);
  await waitFor(() => expect(screen.getByRole("button", {name: "编辑明细 1"})).toBeEnabled());
  fireEvent.click(screen.getByRole("button", {name: "编辑明细 1"}));
  fireEvent.click(screen.getByRole("button", {name: "删除此明细"}));
  expect(screen.getByRole("dialog", {name: "删除此明细？"})).toHaveTextContent("原件和修订历史仍会保留");
  fireEvent.click(screen.getByRole("button", {name: "确认删除"}));
  await waitFor(() => expect(deleteDataRows).toHaveBeenCalledWith(tableId, [7], undefined, {7: 8}));
});

it("keeps a failed save editable and does not close or report success", async () => {
  vi.mocked(updateDataRow).mockRejectedValue(new Error("这行数据已经更新，请刷新后再修改。"));
  const {onChanged} = show(); await open();
  fireEvent.click(screen.getByRole("button", {name: "编辑明细 1"}));
  fireEvent.change(screen.getByRole("textbox", {name: "数量"}), {target: {value: "12"}});
  fireEvent.click(screen.getByRole("button", {name: "提交修改"}));
  expect(await screen.findByRole("alert")).toHaveTextContent("已经更新");
  expect(screen.getByRole("textbox", {name: "数量"})).toHaveValue("12");
  expect(onChanged).not.toHaveBeenCalled();
});

it("protects unsaved input on close and reread without hiding the data behind an inline warning", async () => {
  vi.mocked(updateDataRow).mockRejectedValue(new Error("其他窗口已修改"));
  show(); await open();
  fireEvent.click(screen.getByRole("button", {name: "编辑明细 1"}));
  fireEvent.change(screen.getByRole("textbox", {name: "数量"}), {target: {value: "12"}});
  expect(screen.getByRole("status")).toHaveTextContent("提交或取消后可切换");
  fireEvent.click(screen.getByRole("button", {name: "关闭来源详情"}));
  const confirmation = screen.getByRole("dialog", {name: "放弃未提交的修改？"});
  fireEvent.keyDown(within(confirmation).getByRole("button", {name: "取消"}), {key: "Escape"});
  expect(screen.queryByRole("dialog", {name: "放弃未提交的修改？"})).not.toBeInTheDocument();
  expect(screen.getByRole("textbox", {name: "数量"})).toHaveValue("12");
  fireEvent.click(screen.getByRole("button", {name: "提交修改"}));
  await screen.findByText("其他窗口已修改");
  const reads = vi.mocked(getSourceDetails).mock.calls.length;
  fireEvent.click(screen.getByRole("button", {name: "重新读取"}));
  expect(getSourceDetails).toHaveBeenCalledTimes(reads);
  fireEvent.click(screen.getByRole("button", {name: "放弃修改"}));
  await waitFor(() => expect(getSourceDetails).toHaveBeenCalledTimes(reads + 1));
  expect(screen.queryByRole("textbox", {name: "数量"})).not.toBeInTheDocument();
});

it("reopens at the last detail page without keeping a stale business-data cache", async () => {
  show(); await open();
  fireEvent.click(screen.getByRole("button", {name: "下一页明细"}));
  await waitFor(() => expect(getSourceDetails).toHaveBeenLastCalledWith(tableId, source.id, {}, 2));
  fireEvent.click(screen.getByRole("button", {name: "关闭来源详情"}));
  vi.mocked(getSourceDetails).mockResolvedValue({...detail, header: {customer: "新数据"}});
  await open();
  expect(getSourceDetails).toHaveBeenLastCalledWith(tableId, source.id, {}, 2);
  expect(within(screen.getByRole("dialog")).getByText("新数据")).toBeInTheDocument();
});

it("closes after deleting the last matched detail without expanding a filtered source", async () => {
  vi.mocked(getSourceDetails).mockResolvedValue({...detail, complete: false, total: 1});
  const {onChanged} = show({scope: {search: "局部"}});
  fireEvent.click(await screen.findByRole("button", {name: "查看与编辑"}));
  fireEvent.click(await screen.findByRole("button", {name: "编辑明细 1"}));
  fireEvent.click(screen.getByRole("button", {name: "删除此明细"}));
  fireEvent.click(screen.getByRole("button", {name: "确认删除"}));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(deleteDataRows).toHaveBeenCalledWith(tableId, [7], undefined, {7: 8});
  expect(onChanged).toHaveBeenCalledTimes(1);
});

it("does not leave stale values visible when a committed edit leaves the filter", async () => {
  vi.mocked(getSourceDetails).mockResolvedValueOnce(detail)
    .mockRejectedValueOnce(Object.assign(new Error("不在当前范围"), {status: 404}));
  show({scope: {search: "0"}}); await open();
  fireEvent.click(screen.getByRole("button", {name: "编辑明细 1"}));
  fireEvent.change(screen.getByRole("textbox", {name: "数量"}), {target: {value: "12"}});
  fireEvent.click(screen.getByRole("button", {name: "提交修改"}));
  expect(await screen.findByText(/当前筛选范围内已没有此来源的数据/)).toHaveAttribute("role", "status");
  expect(screen.queryByRole("button", {name: "编辑明细 1"})).not.toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("clears a whole source using the reviewed group token and retains original viewing", async () => {
  show(); await open();
  fireEvent.click(screen.getByRole("button", {name: "原件对照"}));
  expect(screen.getByText("原件：订单.pdf")).toBeInTheDocument();
  const divider = screen.getByRole("separator", {name: "调整内容与原件宽度"});
  fireEvent.keyDown(divider, {key: "End"}); fireEvent.keyDown(divider, {key: "ArrowRight"});
  expect(divider).toHaveAttribute("aria-valuenow", "70");
  expect(localStorage.getItem("zhiyi-card-split")).toBe("70");
  expect(screen.queryByRole("button", {name: "放大图片原件"})).not.toBeInTheDocument();
  fireEvent.keyDown(screen.getByRole("button", {name: "更多数据操作"}), {key: "ArrowDown"});
  fireEvent.click(await screen.findByRole("menuitem", {name: "清空此来源数据"}));
  fireEvent.click(screen.getByRole("button", {name: "确认清空"}));
  await waitFor(() => expect(clearSourceCards).toHaveBeenCalledWith(tableId, [{id: source.id, version: source.version}], {}));
});

it("zooms image originals from 0.5× to 3× and fit resets both scale and scroll", async () => {
  const image = {...source, id: `image-${tableId}`, task_id: `image-${tableId}`, filename: "扫描件.png"};
  vi.mocked(getSourceCards).mockResolvedValue({table_id: "t", page: 1, page_size: 6, total: 1, row_count: 1, groups: [image]});
  show();
  await open();
  fireEvent.click(screen.getByRole("button", {name: "原件对照"}));
  const scale = () => screen.getByRole("status", {name: "图片原件缩放比例"});
  const zoomOut = screen.getByRole("button", {name: "缩小图片原件"});
  const zoomIn = screen.getByRole("button", {name: "放大图片原件"});
  expect(screen.getByRole("button", {name: "恢复图片原件适配宽度"})).toBeDisabled();
  for (let click = 0; click < 5; click++) fireEvent.click(zoomOut);
  expect(screen.getByTestId("document-preview")).toHaveAttribute("data-scale", "0.5");
  expect(zoomOut).toBeDisabled();
  for (let click = 0; click < 25; click++) fireEvent.click(zoomIn);
  expect(screen.getByTestId("document-preview")).toHaveAttribute("data-scale", "3");
  expect(zoomIn).toBeDisabled();
  const pane = document.querySelector<HTMLElement>("[data-document-scroll]")!;
  pane.scrollTop = 240; pane.scrollLeft = 320;
  fireEvent.scroll(pane);
  fireEvent.click(screen.getByRole("button", {name: "恢复图片原件适配宽度"}));
  expect(screen.getByTestId("document-preview")).toHaveAttribute("data-scale", "1");
  expect(scale()).toHaveTextContent("1.0×");
  expect(pane.scrollTop).toBe(0);
  expect(pane.scrollLeft).toBe(0);
});

it("keeps image scale with its original URL across comparison and detail reopen", async () => {
  const imageA = {...source, id: `image-a-${tableId}`, task_id: `image-a-${tableId}`, filename: "正面.png"};
  const imageB = {...source, id: `image-b-${tableId}`, task_id: `image-b-${tableId}`, filename: "背面.png"};
  vi.mocked(getSourceCards).mockResolvedValue({table_id: "t", page: 1, page_size: 6, total: 2, row_count: 2, groups: [imageA, imageB]});
  show();
  const cards = await screen.findAllByRole("article");
  const openCard = async (index: number) => {
    fireEvent.click(within(cards[index]).getByRole("button", {name: "查看与编辑"}));
    await waitFor(() => expect(screen.getByRole("button", {name: "编辑共享字段"})).toBeEnabled());
  };
  await openCard(0);
  fireEvent.click(screen.getByRole("button", {name: "原件对照"}));
  fireEvent.click(screen.getByRole("button", {name: "放大图片原件"}));
  fireEvent.click(screen.getByRole("button", {name: "放大图片原件"}));
  expect(screen.getByTestId("document-preview")).toHaveAttribute("data-scale", "1.2");
  fireEvent.click(screen.getByRole("button", {name: /收起对照/}));
  fireEvent.click(screen.getByRole("button", {name: "原件对照"}));
  expect(screen.getByTestId("document-preview")).toHaveAttribute("data-scale", "1.2");
  fireEvent.click(screen.getByRole("button", {name: "关闭来源详情"}));

  await openCard(1);
  fireEvent.click(screen.getByRole("button", {name: "原件对照"}));
  expect(screen.getByTestId("document-preview")).toHaveAttribute("data-scale", "1");
  fireEvent.click(screen.getByRole("button", {name: "关闭来源详情"}));

  await openCard(0);
  fireEvent.click(screen.getByRole("button", {name: "原件对照"}));
  expect(screen.getByTestId("document-preview")).toHaveAttribute("data-scale", "1.2");
});

it("hides fields only in the preview and reads full, unfolded long values without inventing originals", async () => {
  const full = "完整正文。".repeat(2000);
  vi.mocked(getSourceCards).mockResolvedValue({table_id: "t", page: 1, page_size: 6, total: 1, row_count: 1,
    groups: [{...source, kind: "group", task_id: null, filename: "独立数据组"}]});
  vi.mocked(getSourceDetails).mockResolvedValue({...detail, task_id: null, header: {customer: full}});
  show({hiddenFields: ["customer"]});
  expect(await screen.findByText("0")).toBeInTheDocument();
  expect(screen.queryByText("甲")).not.toBeInTheDocument();
  await open();
  expect(within(screen.getByRole("dialog")).getByText(full).closest("details")).toBeNull();
  expect(screen.queryByRole("button", {name: "原件对照"})).not.toBeInTheDocument();
  expect(screen.queryByRole("button", {name: "修订历史"})).not.toBeInTheDocument();
});
