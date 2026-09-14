import type { Analysis } from "../assistant/types";
import type { TableColumnDef } from "../types";

export type StatisticDisplay = "auto" | "number" | "line" | "bar" | "donut";
export interface StatisticInput {
  name: string;
  table_id: string;
  metric: "count" | "sum" | "avg";
  metric_field: string | null;
  group_field: string | null;
  time_bucket: "day" | "month" | null;
  date_field: string | null;
  time_range: "dashboard" | "all";
  display: StatisticDisplay;
}
export interface Statistic extends StatisticInput {
  id: string;
  position: number;
  created_at: string;
  updated_at: string;
}
export interface StatisticResult {
  status: "ready" | "empty" | "invalid";
  message?: string;
  display: Exclude<StatisticDisplay, "auto">;
  analysis?: Analysis;
}
export interface StatisticTable {
  id: string;
  name: string;
  columns: TableColumnDef[];
}
export interface ModelUsage {
  calls: number;
  completed: number;
  failed: number;
  interrupted: number;
  elapsed_ms: number;
  elapsed_sample_count: number;
  average_elapsed_ms: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  calls_with_usage: number;
  calls_with_cache_usage: number;
  cached_tokens: number | null;
  cache_hit_ratio: number | null;
  cache_measured_prompt_tokens: number;
}
export interface DashboardOverview {
  rows_trend: { date: string; label?: string; count: number }[];
  trend_bucket?: "day" | "month" | "year";
  trend_interval?: number;
  templates: { id: string; name: string; count: number }[];
  model_usage: ModelUsage & {
    by_purpose: (ModelUsage & { purpose: string; model: string; provider: string })[];
    coverage_start: string | null;
    history_note: string;
  };
}
