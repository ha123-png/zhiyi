import type { Analysis, ChartSpec } from "../assistant/types";
import type { StatisticInput } from "./types";

/** Only exact mappings are offered: a chat snapshot may never widen into an unrelated live query. */
export function statisticFromAnalysis(analysis: Analysis, spec?: ChartSpec): StatisticInput | null {
  const query = analysis.source?.request;
  if (!query || analysis.truncated || query.records || query.search || query.task_id || query.row_ids != null || (query.grain && query.grain !== "auto")) return null;
  if (Array.isArray(query.filters) && query.filters.length) return null;
  if (!Array.isArray(query.dimensions) || query.dimensions.length > 1 || !Array.isArray(query.metrics) || query.metrics.length !== 1) return null;
  const metric = query.metrics[0] as { op?: string; field?: string | null };
  if (!["count", "sum", "avg"].includes(metric.op ?? "") || (metric.op === "count" && metric.field)) return null;
  if (query.time_bucket && !["day", "month"].includes(String(query.time_bucket))) return null;
  const group = query.dimensions[0] as string | undefined;
  return {
    name: spec?.title || `${analysis.source.table_name} · ${analysis.metric_labels?.[analysis.metric_keys?.[0] ?? ""] ?? "记录数"}`,
    table_id: analysis.source.table_id,
    metric: metric.op as StatisticInput["metric"], metric_field: metric.field ?? null,
    group_field: group ?? null, time_bucket: (query.time_bucket as "day" | "month") || null,
    date_field: query.time_bucket ? group ?? null : null, time_range: "all",
    display: !group ? "number" : query.time_bucket ? "line" : spec?.type === "donut" && metric.op !== "avg" ? "donut" : "bar",
  };
}
