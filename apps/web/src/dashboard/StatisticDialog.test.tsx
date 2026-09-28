import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { StatisticDialog } from "./StatisticDialog";
import { dashboardApi } from "./api";
import type { StatisticResult } from "./types";

vi.mock("./api", () => ({ dashboardApi: { options: vi.fn(), preview: vi.fn(), save: vi.fn() } }));
// This regression concerns request/approval ordering, not chart animation.
vi.mock("./DashboardChart", () => ({ DashboardChart: () => <div>预览图表</div> }));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(dashboardApi.options).mockResolvedValue({ tables: [{ id: "table", name: "合成账本", columns: [
    { key: "amount", label: "金额", value_type: "number", section: "header" },
  ] }] });
});

it("cannot save a new query with an old preview that resolves after the change", async () => {
  const pending: ((result: StatisticResult) => void)[] = [];
  vi.mocked(dashboardApi.preview).mockImplementation(() => new Promise(resolve => pending.push(resolve)));
  const onSaved = vi.fn();
  render(<StatisticDialog onClose={vi.fn()} onSaved={onSaved} />);
  const save = screen.getByRole("button", { name: "保存统计" });
  await waitFor(() => expect(pending).toHaveLength(1));
  expect(vi.mocked(dashboardApi.preview).mock.calls[0][0].metric).toBe("count");
  fireEvent.change(screen.getByRole("combobox", { name: "统计什么" }), { target: { value: "sum" } });
  await waitFor(() => expect(pending).toHaveLength(2));
  expect(vi.mocked(dashboardApi.preview).mock.calls[1][0].metric).toBe("sum");
  // The old request finishes while the new query is still waiting.
  await act(async () => { pending[0]({ status: "empty", display: "number" }); });
  expect(save).toBeDisabled();
  fireEvent.submit(save.closest("form")!);
  expect(dashboardApi.save).not.toHaveBeenCalled();
  // Only the matching response can enable save, and only its query is submitted.
  await act(async () => { pending[1]({ status: "empty", display: "number" }); });
  expect(save).toBeEnabled();
  fireEvent.click(save);
  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  expect(dashboardApi.save).toHaveBeenCalledWith(expect.objectContaining({ metric: "sum", metric_field: "amount" }), undefined, 7);
});

it("invalidates an accepted preview immediately when its configuration changes", async () => {
  vi.mocked(dashboardApi.preview).mockResolvedValueOnce({ status: "empty", display: "number" })
    .mockImplementation(() => new Promise(() => {}));
  render(<StatisticDialog onClose={vi.fn()} onSaved={vi.fn()} />);
  const save = screen.getByRole("button", { name: "保存统计" });
  await waitFor(() => expect(save).toBeEnabled());
  fireEvent.change(screen.getByRole("textbox", { name: "名称" }), { target: { value: "新的统计名称" } });
  expect(save).toBeDisabled();
  fireEvent.submit(save.closest("form")!);
  expect(dashboardApi.save).not.toHaveBeenCalled();
});

it("keeps the chosen numeric field through repeated aggregation changes", async () => {
  vi.mocked(dashboardApi.options).mockResolvedValue({tables: [{id: "table", name: "账本", columns: [
    {key: "quantity", label: "数量", value_type: "number", section: "item"},
    {key: "amount", label: "金额", value_type: "number", section: "item"},
  ]}]});
  vi.mocked(dashboardApi.preview).mockResolvedValue({status: "empty", display: "number"});
  render(<StatisticDialog onClose={vi.fn()} onSaved={vi.fn()} />);
  await waitFor(() => expect(screen.getByRole("button", {name: "保存统计"})).toBeEnabled());
  const metric = screen.getByRole("combobox", {name: "统计什么"});
  fireEvent.change(metric, {target: {value: "sum"}});
  fireEvent.change(screen.getByRole("combobox", {name: "数值字段"}), {target: {value: "amount"}});
  fireEvent.change(screen.getByRole("textbox", {name: "名称"}), {target: {value: "金额观察"}});
  for (const value of ["avg", "sum", "count", "sum", "avg"]) {
    fireEvent.change(metric, {target: {value}});
    if (value !== "count") expect(screen.getByRole("combobox", {name: "数值字段"})).toHaveValue("amount");
    expect(screen.getByRole("textbox", {name: "名称"})).toHaveValue("金额观察");
  }
  await waitFor(() => expect(screen.getByRole("button", {name: "保存统计"})).toBeEnabled());
  fireEvent.click(screen.getByRole("button", {name: "保存统计"}));
  await waitFor(() => expect(dashboardApi.save).toHaveBeenCalledWith(expect.objectContaining({metric: "avg", metric_field: "amount", name: "金额观察"}), undefined, 7));
});
