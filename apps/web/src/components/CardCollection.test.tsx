import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { CardCollection } from "./CardCollection";
import { updateDataRow } from "../api";
import type { DataRowRead, TableColumnDef, TemplateBehavior } from "../types";

vi.mock("../api", () => ({ updateDataRow: vi.fn(), getOriginalFileUrl: (id: string) => `/files/${id}` }));
vi.mock("./DocumentPreview", () => ({ DocumentPreview: ({ filename }: { filename: string }) => <div>原件预览：{filename}</div> }));
const columns: TableColumnDef[] = [
  { key: "subject", label: "科目", value_type: "text", section: "header" },
  { key: "question", label: "题目", value_type: "text", section: "item" },
  { key: "answer", label: "答案", value_type: "text", section: "item" },
  { key: "score", label: "分值", value_type: "number", section: "item" },
];
const row: DataRowRead = { id: 7, task_id: "file-a", item_index: 1, version: 2, created_at: "", updated_at: "",
  values: { subject: "数学", question: "求函数的最大值", answer: "先求导，再讨论边界", score: 0, source_filename: "试卷.pdf" } };
const presentation: TemplateBehavior["presentation"] = { mode: "card", title_field: "item.question", primary_fields: ["header.subject"], collapsed_fields: ["item.answer"] };

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
});

it("shows a match deep inside long content and keeps the full answer in details", () => {
  const answer = "很长的正文。".repeat(1500) + "边界命中" + "后续推导。".repeat(100);
  const { container } = render(<CardCollection tableId="math" rows={[{ ...row, values: { ...row.values, answer } }]}
    columns={columns} selectedRows={[]} onSelect={vi.fn()} onChanged={vi.fn()} searchText="边界命中" />);
  expect(container.querySelector(".content-card-match mark")).toHaveTextContent("边界命中");
  expect(container.querySelector(".content-card-match p")!.textContent!.length).toBeLessThan(200);
  fireEvent.click(screen.getByRole("button", { name: "查看全文" }));
  expect(within(screen.getByRole("dialog")).getByText((_, element) => element?.tagName === "P" && element.textContent === answer)).toBeInTheDocument();
});

it("adjusts the source divider by keyboard and remembers its bounded position", () => {
  show();
  fireEvent.click(screen.getByRole("button", { name: "查看全文" }));
  fireEvent.click(screen.getByRole("button", { name: "查看原件" }));
  const divider = screen.getByRole("separator", { name: "调整内容与原件宽度" });
  fireEvent.keyDown(divider, { key: "End" });
  expect(divider).toHaveAttribute("aria-valuenow", "70");
  fireEvent.keyDown(divider, { key: "ArrowRight" });
  expect(divider).toHaveAttribute("aria-valuenow", "70");
  expect(localStorage.getItem("zhiyi-card-split")).toBe("70");
});

function show(rows = [row], hiddenFields: string[] = []) {
  const changed = vi.fn();
  render(<CardCollection tableId="math" rows={rows} columns={columns} hiddenFields={hiddenFields} presentation={presentation}
    selectedRows={[]} onSelect={vi.fn()} onChanged={changed} highlightTaskId={null} />);
  return changed;
}

it("preserves partial-input disclosure after the original association is removed", () => {
  show([{ ...row, task_id: null, input_scope: {
    version: 1, rule: "head_tail_v1", coverage: "partial", selected_units: 2, total_units: 100,
    selected: [{ kind: "line", container: null, columns: null, start: 1, end: 1 }, { kind: "line", container: null, columns: null, start: 100, end: 100 }],
    omitted: [{ location: { kind: "line", container: null, columns: null, start: 2, end: 99 }, reason: "input_budget" }],
    text_characters: 20, text_budget: 200, image_budget: 1, notes: [],
  } }]);
  expect(screen.getByText("局部读取")).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: "查看全文" })[0]);
  expect(screen.getByText("来源与处理详情 · 局部读取").closest("button")).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByText(/当前内容没有可打开的原件关联/)).toBeInTheDocument();
  expect(screen.queryByText(/完整原件仍保存在知意/)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "查看原件" })).not.toBeInTheDocument();
});

it("shows answers directly even when a legacy template requested collapse", () => {
  show([row, { ...row, id: 8, task_id: "file-b", values: { ...row.values, question: "证明三角形全等", source_filename: "作业.pdf" } }]);
  expect(screen.getAllByRole("article")).toHaveLength(2);
  expect(screen.getAllByText("先求导，再讨论边界")).toHaveLength(2);
  expect(screen.getAllByText("0")).toHaveLength(2);
  fireEvent.click(screen.getAllByRole("button", { name: "查看全文" })[0]);
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  expect(within(screen.getByRole("dialog")).getByText("先求导，再讨论边界").closest("details")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "查看原件" }));
  expect(screen.getByText("原件预览：试卷.pdf")).toBeInTheDocument();
});

it("saves edits through the same versioned row API and informs about shared fields", async () => {
  vi.mocked(updateDataRow).mockResolvedValue({ ...row, version: 3, values: { ...row.values, subject: "高等数学" } });
  const changed = show();
  fireEvent.click(screen.getAllByRole("button", { name: "查看全文" })[0]);
  fireEvent.click(screen.getByRole("button", { name: /^编辑$/ }));
  expect(screen.getByText(/整份文件的信息会同步/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("科目 · 整份文件"), { target: { value: "高等数学" } });
  fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
  await waitFor(() => expect(updateDataRow).toHaveBeenCalledWith("math", 7, 2, { subject: "高等数学" }));
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  expect(within(screen.getByRole("dialog")).getByText("高等数学")).toBeInTheDocument();
});

it("keeps a failed save editable instead of reporting success", async () => {
  vi.mocked(updateDataRow).mockRejectedValue(new Error("这行数据已经更新，请刷新后再修改。"));
  const changed = show();
  fireEvent.click(screen.getAllByRole("button", { name: "查看全文" })[0]);
  fireEvent.click(screen.getByRole("button", { name: /^编辑$/ }));
  fireEvent.change(screen.getByLabelText("答案"), { target: { value: "新的答案" } });
  fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("已经更新");
  expect(screen.getByLabelText("答案")).toHaveValue("新的答案");
  expect(changed).not.toHaveBeenCalled();
});

it("empty manual cards remain editable and do not invent source previews", () => {
  show([{ ...row, task_id: null, values: {} }]);
  expect(screen.getByRole("article")).not.toHaveClass("highlighted");
  fireEvent.click(screen.getByRole("button", { name: "未填写科目" }));
  expect(screen.queryByRole("button", { name: "查看原件" })).not.toBeInTheDocument();
  expect(screen.getByText("暂无内容，可点击编辑填写。")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /^编辑$/ }));
  expect(screen.getByLabelText("题目")).toHaveValue("");
  expect(screen.queryByText(/整份文件的信息会同步/)).not.toBeInTheDocument();
  expect(screen.getByLabelText("科目")).toHaveValue("");
});

it("hiding a list field never removes it from complete details", () => {
  show([row], ["answer"]);
  expect(screen.queryByText("先求导，再讨论边界")).not.toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: "查看全文" })[0]);
  expect(within(screen.getByRole("dialog")).getByText("先求导，再讨论边界")).toBeInTheDocument();
});

it("uses field order instead of legacy title preferences and retains false and zero", () => {
  const { rerender } = render(<CardCollection tableId="math" rows={[row]} columns={columns} presentation={presentation} selectedRows={[]} onSelect={vi.fn()} onChanged={vi.fn()} />);
  expect(screen.getByRole("button", { name: "数学" })).toBeInTheDocument();
  rerender(<CardCollection tableId="math" rows={[{ ...row, values: { ...row.values, flag: false } }]} columns={[{ key: "flag", label: "完成", value_type: "boolean", section: "header" }, columns[3], ...columns.slice(0, 3)]} presentation={presentation} selectedRows={[]} onSelect={vi.fn()} onChanged={vi.fn()} />);
  expect(screen.getByRole("button", { name: "否" })).toBeInTheDocument();
  expect(screen.getByText("0")).toBeInTheDocument();
});
