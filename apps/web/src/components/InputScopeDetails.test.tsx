import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { acceptTaskInputScope } from "../api";
import type { InputScope, Task } from "../types";
import { InputScopeDetails, PartialInputAction } from "./InputScopeDetails";
import { PresentationSettings } from "./PresentationSettings";

vi.mock("../api", () => ({ acceptTaskInputScope: vi.fn() }));

const scope: InputScope = {
  version: 1, rule: "head_tail_v1", coverage: "partial", selected_units: 4, total_units: 100,
  selected: [{ kind: "sheet_row", container: "交易", start: 1, end: 2, columns: 8 }, { kind: "sheet_row", container: "交易", start: 99, end: 100, columns: 8 }],
  omitted: [{ location: { kind: "sheet_row", container: "交易", start: 3, end: 98, columns: 8 }, reason: "input_budget" }],
  text_characters: 100, text_budget: 200, image_budget: 1, notes: [],
};
const task = { id: "scope-task", pending_reason: "input_scope", planned_scope: scope } as Task;
beforeEach(() => vi.clearAllMocks());

it("keeps native included and omitted ranges inside collapsed details", () => {
  render(<InputScopeDetails scope={scope} />);
  const summary = screen.getByText("本次提取输入范围 · 局部读取");
  expect(summary.closest("button")).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByText(/交易!A1:H2；交易!A99:H100/)).toBeInTheDocument();
  expect(screen.getByText(/交易!A3:H98/)).toHaveTextContent("达到设置的读取上限");
  expect(screen.queryByText(/第.*页/)).not.toBeInTheDocument();
});

it("requires a specific partial-scope action, and does not retry extraction itself", async () => {
  vi.mocked(acceptTaskInputScope).mockResolvedValue({ ...task, status: "queued" });
  const updated = vi.fn();
  render(<PartialInputAction task={task} onUpdated={updated} />);
  expect(screen.getByText(/尚未进行正式提取/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "仅提取所示范围" }));
  await waitFor(() => expect(updated).toHaveBeenCalledOnce());
  expect(acceptTaskInputScope).toHaveBeenCalledExactlyOnceWith("scope-task");
  expect(screen.getByRole("button", { name: "已确认，等待处理" })).toBeDisabled();
});

it("preserves an actionable confirmation after a failed request", async () => {
  vi.mocked(acceptTaskInputScope).mockRejectedValue(new Error("队列容量不足"));
  render(<PartialInputAction task={task} />);
  fireEvent.click(screen.getByRole("button", { name: "仅提取所示范围" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("队列容量不足");
  expect(screen.getByRole("button", { name: "仅提取所示范围" })).toBeEnabled();
});

it("keeps legacy template defaults simple without a template-level input policy", () => {
  render(<PresentationSettings onChange={vi.fn()} />);
  expect(screen.getByRole("combobox")).toHaveValue("table");
  expect(screen.queryByRole("checkbox", { name: "要求完整输入" })).not.toBeInTheDocument();
});
