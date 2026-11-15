import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { DashboardPage } from "./DashboardPage";
import * as api from "../api";
import { dashboardApi } from "../dashboard/api";

vi.mock("../api", () => ({ getTaskSummary: vi.fn(), getTables: vi.fn(), getDashboardSummary: vi.fn(),
  getDashboardTrend: vi.fn(), subscribeTaskEvents: vi.fn(() => () => {}) }));
vi.mock("../dashboard/api", () => ({ dashboardApi: { overview: vi.fn(), cards: vi.fn() } }));
vi.mock("../dashboard/DashboardChart", async importOriginal => {
  const actual = await importOriginal<typeof import("../dashboard/DashboardChart")>();
  return { ...actual, AnimatedNumber: ({ value }: { value: number }) => <span>{value}</span>, DashboardChart: () => null };
});

it("does not expose binary multiplication noise for an exact model cache percentage", async () => {
  vi.mocked(api.getTaskSummary).mockResolvedValue({ total: 0, completed: 0, needs_review: 0, failed: 0 });
  vi.mocked(api.getTables).mockResolvedValue([]);
  vi.mocked(api.getDashboardSummary).mockResolvedValue({ average_elapsed_seconds: null, processed_count: 0,
    failed_count: 0, success_rate: null, new_rows: 0 });
  vi.mocked(api.getDashboardTrend).mockResolvedValue([]);
  vi.mocked(dashboardApi.cards).mockResolvedValue({ items: [], limit: 4 });
  vi.mocked(dashboardApi.overview).mockResolvedValue({ rows_trend: [], templates: [], model_usage: {
    calls: 1, completed: 1, failed: 0, interrupted: 0, elapsed_ms: 1000, elapsed_sample_count: 1,
    average_elapsed_ms: 1000, prompt_tokens: 100, completion_tokens: 10, total_tokens: 110,
    calls_with_usage: 1, calls_with_cache_usage: 1, cached_tokens: 29, cache_hit_ratio: 0.29,
    cache_measured_prompt_tokens: 100, by_purpose: [], coverage_start: null,
  } });
  render(<DashboardPage />);
  fireEvent.click(await screen.findByRole("button", { name: "使用明细" }));
  expect(await screen.findByText(/缓存读入/)).toHaveTextContent("缓存读入 29%（1 次）");
});

it.each([
  { milliseconds: 2.1, elapsed: 21, calls: 10, seconds: "0.0021", cached: 29, prompt: 100, ratio: 0.29, percent: "29%" },
  { milliseconds: 0.0001, elapsed: 1, calls: 10000, seconds: "0.0000001", cached: 1, prompt: 100000000, ratio: 0.00000001, percent: "0.000001%" },
])("formats derived elapsed units without losing a nonzero measurement: $milliseconds ms", async values => {
  vi.mocked(api.getTaskSummary).mockResolvedValue({ total: 0, completed: 0, needs_review: 0, failed: 0 });
  vi.mocked(api.getTables).mockResolvedValue([]);
  vi.mocked(api.getDashboardSummary).mockResolvedValue({ average_elapsed_seconds: null, processed_count: 0,
    failed_count: 0, success_rate: null, new_rows: 0 });
  vi.mocked(api.getDashboardTrend).mockResolvedValue([]);
  vi.mocked(dashboardApi.cards).mockResolvedValue({ items: [], limit: 4 });
  const usage = { calls: values.calls, completed: values.calls, failed: 0, interrupted: 0,
    elapsed_ms: values.elapsed, elapsed_sample_count: values.calls, average_elapsed_ms: values.milliseconds,
    prompt_tokens: values.prompt, completion_tokens: 10, total_tokens: values.prompt + 10,
    calls_with_usage: 1, calls_with_cache_usage: 1, cached_tokens: values.cached, cache_hit_ratio: values.ratio,
    cache_measured_prompt_tokens: values.prompt };
  vi.mocked(dashboardApi.overview).mockResolvedValue({ rows_trend: [], templates: [], model_usage: {
    ...usage, by_purpose: [{ ...usage, purpose: "assistant", model: "synthetic-derived-timing", provider: "synthetic" }],
    coverage_start: null,
  } });
  render(<DashboardPage />);
  const summary = (await screen.findByText("平均调用耗时")).parentElement?.querySelector("strong");
  expect(summary).toHaveTextContent(`${values.seconds}秒`);
  expect(summary).toHaveAttribute("title", `${values.milliseconds} 毫秒`);
  fireEvent.click(screen.getByRole("button", { name: "使用明细" }));
  expect(await screen.findByRole("cell", { name: `${values.seconds} 秒` })).toHaveAttribute("title", `${values.milliseconds} 毫秒`);
  expect(screen.getByText(/缓存读入/)).toHaveTextContent(`缓存读入 ${values.percent}（1 次）`);
});
