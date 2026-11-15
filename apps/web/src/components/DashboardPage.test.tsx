import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { DashboardSummary } from "../types";
import { expect, it, vi } from "vitest";
import { DashboardPage } from "./DashboardPage";
import * as api from "../api";
import { dashboardApi } from "../dashboard/api";

vi.mock("../api", () => ({ getTaskSummary: vi.fn(), getTables: vi.fn(), getDashboardSummary: vi.fn(), getDashboardTrend: vi.fn(), subscribeTaskEvents: vi.fn(() => () => {}) }));
vi.mock("../dashboard/api", () => ({ dashboardApi: { overview: vi.fn(async () => ({rows_trend: [], templates: [], model_usage: { calls:0, by_purpose:[] }})), cards: vi.fn(async () => ({items:[],limit:4})) } }));
vi.mock("../dashboard/DashboardChart", () => ({ AnimatedNumber: ({value}: {value: number}) => <span>{value}</span>, DashboardChart: () => null, formatNumber: (value: number | undefined) => value == null ? "—" : String(value) }));

it("keeps the latest range when earlier requests finish late and can recover from failure", async () => {
  vi.mocked(api.getTaskSummary).mockResolvedValue({total: 1, completed: 1, needs_review: 0, failed: 0});
  vi.mocked(api.getTables).mockResolvedValue([]);
  vi.mocked(api.getDashboardTrend).mockResolvedValue([]);
  let oldResolve!: (value: DashboardSummary) => void;
  vi.mocked(api.getDashboardSummary).mockResolvedValueOnce({average_elapsed_seconds: 2, processed_count: 1, failed_count: 0, success_rate: 1, new_rows: 7});
  render(<DashboardPage />);
  expect(screen.getByRole("combobox", {name:"时间范围"})).toBeEnabled();
  await screen.findByText("7");
  vi.mocked(api.getDashboardSummary).mockImplementationOnce(() => new Promise(resolve => { oldResolve = resolve; }));
  fireEvent.change(screen.getByRole("combobox", {name: "时间范围"}), {target: {value: "30"}});
  vi.mocked(api.getDashboardSummary).mockResolvedValueOnce({average_elapsed_seconds: 2, processed_count: 1, failed_count: 0, success_rate: 1, new_rows: 90});
  fireEvent.change(screen.getByRole("combobox", {name: "时间范围"}), {target: {value: "90"}});
  await screen.findByText("90");
  await act(async () => oldResolve({average_elapsed_seconds: 2, new_rows: 30, processed_count: 1, failed_count: 0, success_rate: 1}));
  expect(screen.queryByText("30")).not.toBeInTheDocument();
  vi.mocked(api.getDashboardSummary).mockRejectedValueOnce(new Error("synthetic offline"));
  fireEvent.change(screen.getByRole("combobox", {name: "时间范围"}), {target: {value: "1"}});
  fireEvent.click(await screen.findByRole("button", { name: "统计读取事项" }));
  expect(screen.getByRole("dialog", { name: "统计读取事项" })).toHaveTextContent("当前显示「近 90 天」的上次结果");
  vi.mocked(api.getDashboardSummary).mockResolvedValueOnce({average_elapsed_seconds: null, processed_count: 0, failed_count: 0, success_rate: null, new_rows: 1});
  fireEvent.click(screen.getByRole("button", {name: "重新读取"}));
  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
});

it("requests the complete retained history and explains its automatic bucket without changing current totals", async () => {
  vi.clearAllMocks();
  vi.mocked(api.getTaskSummary).mockResolvedValue({total: 2, completed: 2, needs_review: 0, failed: 0});
  vi.mocked(api.getTables).mockResolvedValue([]);
  vi.mocked(api.getDashboardSummary).mockImplementation(async days => ({average_elapsed_seconds: null, processed_count: 2, failed_count: 0, success_rate: 1, new_rows: days === "all" ? 2 : 1}));
  vi.mocked(api.getDashboardTrend).mockImplementation(async days => days === "all" ? [{date:"2024-01", label:"2024-01", total:1, completed:1, failed:0, bucket:"month"}, {date:"2026-09", label:"2026-09", total:1, completed:1, failed:0, bucket:"month"}] : []);
  render(<DashboardPage />);
  const range = screen.getByRole("combobox", {name:"时间范围"});
  expect(screen.getByRole("option", {name:"全部"})).toBeInTheDocument();
  await screen.findByRole("button", {name:/新增记录/});
  fireEvent.change(range, {target:{value:"all"}});
  await screen.findByText("全部保留历史 · 按月");
  expect(range).toHaveValue("all");
  expect(api.getDashboardSummary).toHaveBeenLastCalledWith("all");
  expect(api.getDashboardTrend).toHaveBeenLastCalledWith("all");
  expect(dashboardApi.overview).toHaveBeenLastCalledWith("all");
  expect(screen.getByRole("button", {name:/文件任务.*2.*当前保留/})).toBeInTheDocument();
  const explanation = screen.getByRole("button", {name:"仪表盘统计说明"});
  expect(explanation).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByRole("dialog", {name:"统计说明"})).not.toBeInTheDocument();
  explanation.focus();
  fireEvent.click(explanation);
  expect(explanation).toHaveAttribute("aria-expanded", "true");
  const help = await screen.findByRole("dialog", {name:"统计说明"});
  expect(within(help).getByText(/“全部”覆盖当前保留的完整历史/)).toBeInTheDocument();
  expect(within(help).getByRole("button", {name:"关闭统计说明"})).toHaveFocus();
  fireEvent.keyDown(help, {key:"Escape"});
  expect(screen.queryByRole("dialog", {name:"统计说明"})).not.toBeInTheDocument();
  expect(explanation).toHaveFocus();
  fireEvent.click(explanation);
  fireEvent.mouseDown(screen.getByRole("dialog", {name:"统计说明"}).parentElement!);
  expect(screen.queryByRole("dialog", {name:"统计说明"})).not.toBeInTheDocument();
  expect(explanation).toHaveFocus();
});

it("distinguishes a confirmed empty dashboard from an unresolved initial load", async () => {
  vi.clearAllMocks();
  vi.mocked(api.getTaskSummary).mockResolvedValue({total: 0, completed: 0, needs_review: 0, failed: 0});
  vi.mocked(api.getTables).mockResolvedValue([]);
  vi.mocked(api.getDashboardSummary).mockResolvedValue({average_elapsed_seconds: null, processed_count: 0, failed_count: 0, success_rate: null, new_rows: 0});
  vi.mocked(api.getDashboardTrend).mockResolvedValue([]);
  vi.mocked(dashboardApi.cards).mockResolvedValue({items: [], limit: 4});
  vi.mocked(dashboardApi.overview).mockResolvedValue({rows_trend: [], templates: [], model_usage: {calls: 0, by_purpose: []}} as never);

  render(<DashboardPage />);

  expect(await screen.findByText("还没有自定义统计。选择“添加统计”创建常看的数据视图。")).toBeInTheDocument();
  expect(screen.getByRole("button", {name: /文件任务.*0.*当前保留/})).toBeInTheDocument();
  expect(screen.getByRole("button", {name: "添加统计"})).toBeEnabled();
  expect(screen.queryByText("正在读取我的统计…")).not.toBeInTheDocument();
});

it("audit: keeps the first data trend unknown until overview succeeds and recovers from its failure", async () => {
  vi.clearAllMocks();
  vi.mocked(api.getTaskSummary).mockResolvedValue({total: 7, completed: 7, needs_review: 0, failed: 0});
  vi.mocked(api.getTables).mockResolvedValue([]);
  vi.mocked(api.getDashboardSummary).mockResolvedValue({average_elapsed_seconds: null, processed_count: 7, failed_count: 0, success_rate: 1, new_rows: 7});
  vi.mocked(api.getDashboardTrend).mockResolvedValue([]);
  vi.mocked(dashboardApi.cards).mockResolvedValue({items: [], limit: 4});
  let failOverview!: (reason: Error) => void;
  vi.mocked(dashboardApi.overview).mockReset()
    .mockImplementationOnce(() => new Promise((_, reject) => { failOverview = reject; }))
    .mockResolvedValue({rows_trend: [], templates: [], model_usage: {calls: 0, by_purpose: []}} as never);
  render(<DashboardPage />);
  await screen.findByRole("button", {name: /文件任务.*7.*当前保留/});
  fireEvent.click(screen.getByRole("button", {name: "数据"}));
  const trend = screen.getByRole("region", {name: "期间趋势"});
  expect(within(trend).getByText("—")).toBeInTheDocument();
  expect(within(trend).getByRole("status")).toHaveTextContent("正在读取数据新增趋势");
  await act(async () => { failOverview(new Error("合成数据趋势读取失败")); });
  expect(within(trend).getByRole("alert")).toHaveTextContent("合成数据趋势读取失败");
  expect(within(trend).getByText("—")).toBeInTheDocument();
  fireEvent.click(within(trend).getByRole("button", {name: "重新加载趋势"}));
  await waitFor(() => expect(within(trend).queryByRole("alert")).not.toBeInTheDocument());
  await waitFor(() => expect(trend).toHaveAttribute("aria-busy", "false"));
  expect(within(trend).getByText("0")).toBeInTheDocument();
});
