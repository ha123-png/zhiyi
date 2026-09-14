import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
  expect(screen.getByRole("heading", {name:"数据仪表盘"})).toBeInTheDocument();
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
  await screen.findByRole("alert");
  vi.mocked(api.getDashboardSummary).mockResolvedValueOnce({average_elapsed_seconds: null, processed_count: 0, failed_count: 0, success_rate: null, new_rows: 1});
  fireEvent.click(screen.getByRole("button", {name: "重新加载"}));
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
  fireEvent.click(explanation);
  expect(explanation).toHaveAttribute("aria-expanded", "true");
  expect(await screen.findByText(/“全部”覆盖当前保留的完整历史/)).toBeInTheDocument();
});
