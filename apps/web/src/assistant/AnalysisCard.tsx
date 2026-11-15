import { motion, useReducedMotion } from "motion/react";
import { Fold } from "./Interaction";
import { memo, useEffect, useId, useRef, useState } from "react";
import {
  Area,
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Expand, X, Database, ArrowUpRight } from "lucide-react";
import type { Analysis, ChartSpec, ChartType, Reference } from "./types";
import { statisticFromAnalysis } from "../dashboard/pinAnalysis";
import { formatNumericValue } from "../numberDisplay";

const colors = Array.from({ length: 6 }, (_, i) => `var(--ask-chart-${i})`);
const visited = new Set<string>();
const labels: Record<ChartType, string> = {
  bar: "柱状图",
  horizontal_bar: "条形图",
  line: "折线图",
  area: "面积图",
  stacked_bar: "堆叠柱状图",
  composed: "柱线组合",
  pie: "饼图",
  donut: "环形图",
  scatter: "散点图",
  metric: "指标卡",
  table: "数据表",
};
const fmt = (value: unknown) =>
  value == null
    ? "—"
    : typeof value === "number"
      ? formatNumericValue(value)
      : String(value);
const metricName = (key: string) =>
  key
    .replace(/^count:\*$/, "记录数")
    .replace(/^sum:/, "合计 · ")
    .replace(/^avg:/, "平均 · ")
    .replace(/^min:/, "最小 · ")
    .replace(/^max:/, "最大 · ")
    .replace(/^distinct:/, "去重数 · ")
    .replace(/^missing:/, "缺失数 · ")
    .replace(/^duplicates:/, "重复数 · ");

/** Project the query schema for readers; never rewrite the saved request. */
function readableAnalysisQuery(analysis: Analysis): Record<string, string> {
  const request = analysis.source.request;
  const details: Record<string, string> = {};
  const dimensionLabels = (analysis as Analysis & { dimension_labels?: Record<string, string> }).dimension_labels;
  const fieldLabel = (field: string) => analysis.columns?.find(column => column.key === field)?.label
    || dimensionLabels?.[field] || field;
  const operators: Record<string, string> = {
    eq: "等于", ne: "不等于", contains: "包含", gt: "大于", gte: "不小于", lt: "小于", lte: "不大于", empty: "未填写",
  };
  const aggregates: Record<string, string> = {
    count: "记录数", sum: "合计", avg: "平均", min: "最小", max: "最大",
    distinct: "去重数", missing: "缺失数", duplicates: "重复数",
  };
  const metricLabel = (key: string) => {
    if (analysis.metric_labels?.[key]) return analysis.metric_labels[key];
    if (key === "row_id" && !analysis.columns?.some(column => column.key === key) && !dimensionLabels?.[key]) return "记录顺序";
    const separator = key.indexOf(":");
    const operation = key.slice(0, separator);
    if (separator > 0 && aggregates[operation]) {
      const field = key.slice(separator + 1);
      return field === "*" ? aggregates[operation] : `${aggregates[operation]} · ${fieldLabel(field)}`;
    }
    return fieldLabel(key);
  };
  const object = (value: unknown): Record<string, unknown> =>
    value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
  if (Array.isArray(request.filters)) {
    const filters = request.filters.flatMap(value => {
      const rule = object(value);
      if (typeof rule.field !== "string" || typeof rule.op !== "string" || !operators[rule.op]) return [];
      const criterion = `${fieldLabel(rule.field)} ${operators[rule.op]}`;
      if (rule.op !== "empty" && ["string", "number", "boolean"].includes(typeof rule.value)) {
        return [`${criterion} ${String(rule.value)}`];
      }
      return [criterion];
    });
    if (filters.length) details["筛选条件"] = filters.join(" · ");
  }
  if (Array.isArray(request.dimensions) && request.dimensions.length) {
    details["分组字段"] = request.dimensions.filter((value): value is string => typeof value === "string").map(fieldLabel).join(" · ");
  }
  if (Array.isArray(request.metrics)) {
    const metrics = request.metrics.flatMap(value => {
      const metric = object(value);
      if (typeof metric.op !== "string" || !aggregates[metric.op]) return [];
      return [metricLabel(`${metric.op}:${typeof metric.field === "string" ? metric.field : "*"}`)];
    });
    if (metrics.length) details["统计指标"] = metrics.join(" · ");
  }
  const buckets: Record<string, string> = { day: "日", month: "月", year: "年" };
  if (typeof request.time_bucket === "string" && buckets[request.time_bucket]) details["时间粒度"] = buckets[request.time_bucket];
  const grains: Record<string, string> = { auto: "自动", row: "按明细", document: "按文件" };
  if (typeof request.grain === "string" && grains[request.grain]) details["统计粒度"] = grains[request.grain];
  if (typeof request.sort === "string" && request.sort) details["排序"] = `${metricLabel(request.sort)} · ${request.descending === false ? "升序" : "降序"}`;
  if (typeof request.limit === "number" && Number.isFinite(request.limit)) details["最多展示"] = String(request.limit);
  if (typeof request.records === "boolean") details["结果类型"] = request.records ? "明细记录" : "汇总统计";
  if (typeof request.search === "string" && request.search) details["搜索"] = request.search;
  const selectedRows = Array.isArray(request.row_ids) ? new Set(request.row_ids).size : null;
  const selectedFile = typeof request.task_id === "string" && Boolean(request.task_id);
  if (selectedRows !== null) details["资料范围"] = `${selectedFile ? "所选文件内的" : ""}${selectedRows} 条指定记录`;
  else if (selectedFile) details["资料范围"] = "所选文件";
  return details;
}

function AnalysisCardView({
  analysis,
  spec,
  navigate,
  refresh,
  updated = false,
}: {
  analysis: Analysis;
  spec?: ChartSpec;
  navigate: (r: Reference) => void;
  refresh?: () => void | Promise<void>;
  updated?: boolean;
}) {
  const data = analysis.data ?? [];
  const reduced = useReducedMotion();
  const [hovered, setHovered] = useState<number | null>(null);
  const [tooltipHost, setTooltipHost] = useState<HTMLDivElement | null>(null);
  const [expandedTooltipHost, setExpandedTooltipHost] = useState<HTMLDivElement | null>(null);
  const animationKey = `${analysis.analysis_id}:${spec ? "chart" : "analysis"}`;
  const fresh = useRef(!visited.has(animationKey)).current;
  useEffect(() => {
    if (analysis.analysis_id) visited.add(animationKey);
    if (visited.size > 3000) visited.delete(visited.values().next().value!);
  }, [animationKey, analysis.analysis_id]);
  const series = spec?.series ?? analysis.metric_keys ?? [];
  const mixedCounts = series.some((s) => s.startsWith("count:")) && series.some((s) => !s.startsWith("count:"));
  const x = spec?.x ?? "label";
  const [type, setType] = useState<ChartType>(
    spec?.type ?? (data.length === 1 ? "metric" : "table"),
  );
  const dialog = useRef<HTMLDialogElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [changedChart, setChangedChart] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  useEffect(() => {
    if (expanded) dialog.current?.showModal();
  }, [expanded]);
  const uid = useId().replaceAll(":", "");
  const name = (key: string) =>
    spec?.series_labels?.[key] ||
    analysis.metric_labels?.[key] ||
    analysis.columns?.find((c) => c.key === key)?.label ||
    metricName(key);
  const numeric = (key: string) =>
    data.every((row) => row[key] == null || typeof row[key] === "number");
  const available = (candidate: ChartType) => {
    if (!data.length || !series.length) return candidate === "table";
    if (candidate === "metric") return data.length === 1;
    if (data.length === 1) return ["table", "bar", "horizontal_bar", "composed", "stacked_bar"].includes(candidate);
    if (mixedCounts) return candidate === "composed" || candidate === "table";
    if (candidate === "line" || candidate === "area")
      return (
        data.length > 1 &&
        (data.every((r) => /^\d{4}-\d{2}/.test(String(r[x]))) || numeric(x))
      );
    if (candidate === "scatter")
      return numeric(x) && data.some((row) => typeof row[x] === "number");
    if (candidate === "pie" || candidate === "donut")
      return (
        series.length === 1 &&
        !series.some((s) => /^(avg|min|max|distinct|duplicates):/.test(s)) &&
        !analysis.truncated &&
        data.every((row) => Number(row[series[0]] ?? 0) >= 0) &&
        data.some((row) => Number(row[series[0]]) > 0)
      );
    if (candidate === "composed" || candidate === "stacked_bar")
      return series.length > 1;
    return true;
  };
  const actualType = mixedCounts && data.length > 1 && type !== "table" ? "composed" : available(type)
    ? type
    : data.length === 1
      ? "metric"
      : "table";
  const title =
    actualType === "metric" && /趋势|走势/.test(spec?.title || "")
      ? `${analysis.source.table_name} · 汇总`
      : spec?.title || `${analysis.source.table_name} · 分析结果`;
  function table() {
    const keys = data.length
      ? [x, ...series.filter((s) => s !== x)]
      : (analysis.columns?.map((c) => c.key) ?? []);
    return (
      <div
        className="ask-data-scroll"
        tabIndex={0}
        aria-label="分析数据，可横向滚动"
      >
        <table>
          <caption className="sr-only">{title}的底层数据</caption>
          <thead>
            <tr>
              {keys.map((k) => (
                <th key={k} scope="col">{k === x ? (analysis.columns?.find(c => c.key === x)?.label || "分组") : name(k)}</th>
              ))}
              {analysis.rows && <th>来源</th>}
            </tr>
          </thead>
          <tbody>
            {data.map((row, i) => (
              <tr key={i}>
                {keys.map((k) => (
                  <td key={k}><span className="ask-cell-value" title={fmt(row[k])}>{fmt(row[k])}</span></td>
                ))}
                {analysis.rows && <td>—</td>}
              </tr>
            ))}
            {analysis.rows?.map((row) => (
              <tr key={row.row_id}>
                {keys.map((k) => (
                  <td key={k}><span className="ask-cell-value" title={fmt(row.values[k])}>{fmt(row.values[k])}</span></td>
                ))}
                <td>
                  <button
                    type="button"
                    onClick={() =>
                      navigate({
                        kind: "table",
                        id: analysis.source.table_id,
                        row_id: row.row_id,
                      })
                    }
                  >
                    查看记录 <ArrowUpRight size={12} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!data.length && !analysis.rows?.length && <p>当前范围没有记录。</p>}
      </div>
    );
  }
  function graphic(expanded: boolean) {
    if (actualType === "table") return table();
    if (actualType === "metric")
      return (
        <div>
        {data[0]?.[x] && data[0][x] !== "全部" && <p className="ask-metric-category">{fmt(data[0][x])}</p>}
        <dl className="ask-metrics">
          {series.map((s) => {
            const text = fmt(data[0]?.[s]);
            return <div key={s}>
              <dt>{name(s)}</dt>
              <dd className={text.length >= 12 ? "long-number" : undefined}>{text}</dd>
            </div>;
          })}
        </dl>
        </div>
      );
    if (
      actualType === "horizontal_bar" &&
      series.length === 1 &&
      data.every((r) => Number(r[series[0]] ?? 0) >= 0)
    ) {
      const max = Math.max(...data.map((r) => Number(r[series[0]] ?? 0)), 1);
      return (
        <div
          className="ask-ranks"
          role="img"
          aria-label={`${title}，条形图；可查看底层数据。`}
        >
          {data.map((row, i) => (
            <div key={String(row[x]) + i} className="ask-rank-row">
              <span className="ask-rank-index">
                {String(i + 1).padStart(2, "0")}
              </span>
              <span className="ask-rank-name">{fmt(row[x])}</span>
              <strong className="ask-rank-value">{fmt(row[series[0]])}</strong>
              <div className="ask-rank-track">
                <motion.div
                  className="ask-rank-fill"
                  initial={(fresh || changedChart) && !reduced && !expanded ? { width: 0 } : false}
                  animate={{
                    width: `${(Number(row[series[0]] ?? 0) / max) * 100}%`,
                  }}
                  transition={{
                    duration: reduced ? 0 : 0.22,
                    delay: 0,
                    ease: [0.22, 1, 0.36, 1],
                  }}
                />
              </div>
            </div>
          ))}
        </div>
      );
    }
    const height = expanded
      ? "clamp(240px, calc(90dvh - 312px), 460px)"
      : actualType === "horizontal_bar"
        ? Math.min(520, Math.max(260, data.length * 30))
        : 280;
    const common = {
      data,
      margin: { left: 2, right: 18, top: 16, bottom: 8 },
      accessibilityLayer: true,
    };
    const tooltip = (
      <Tooltip
        isAnimationActive={false}
        portal={expanded ? expandedTooltipHost : tooltipHost}
        position={{ x: 0, y: 0 }}
        wrapperStyle={{ width: "100%", pointerEvents: "none" }}
        filterNull={false}
        cursor={
          actualType === "bar" ||
          actualType === "horizontal_bar" ||
          actualType === "stacked_bar" ||
          actualType === "composed"
            ? false
            : { stroke: "var(--border)", strokeDasharray: "3 3" }
        }
        content={({ active, payload, label }) =>
          active && payload?.length ? (
            <div className="ask-chart-tooltip">
              <strong title={fmt(label ?? payload[0]?.name)}>{fmt(label ?? payload[0]?.name)}</strong>
              {payload.map((p, i) => (
                <p key={i}>
                  <span title={name(String(p.dataKey ?? p.name))}>{name(String(p.dataKey ?? p.name))}</span>
                  <b>{fmt(p.value)}</b>
                </p>
              ))}
            </div>
          ) : null
        }
      />
    );
    let chart;
    if (actualType === "pie" || actualType === "donut")
      chart = (
        <PieChart accessibilityLayer>
          <Pie
            data={data}
            dataKey={series[0]}
            nameKey={x}
            innerRadius={actualType === "donut" ? "68%" : 0}
            outerRadius="88%"
            paddingAngle={data.length <= 12 ? 3 : 0}
            cornerRadius={4}
            stroke="var(--card)"
            onMouseEnter={(_, index) => setHovered(index)}
            onMouseLeave={() => setHovered(null)}
            isAnimationActive={!reduced && (fresh || changedChart) && !expanded}
            animationDuration={220}
            animationEasing="ease-out"
          >
            {data.map((_, i) => (
              <Cell key={i} fill={colors[i % colors.length]} opacity={hovered == null || hovered === i ? 1 : 0.45} />
            ))}
          </Pie>
          {actualType !== "donut" && tooltip}
        </PieChart>
      );
    else if (actualType === "scatter")
      chart = (
        <ScatterChart {...common}>
          <CartesianGrid stroke="var(--border)" strokeDasharray="2 5" />
          <XAxis
            type="number"
            dataKey={x}
            name={name(x)}
            tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
          />
          <YAxis
            type="number"
            dataKey={series[0]}
            name={name(series[0])}
            tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
          />
          {tooltip}
          <Scatter
            data={data}
            fill={colors[0]}
            isAnimationActive={!reduced && (fresh || changedChart) && !expanded}
            animationDuration={220}
            animationEasing="ease-out"
          />
        </ScatterChart>
      );
    else
      chart = (
        <ComposedChart
          {...common}
          layout={actualType === "horizontal_bar" ? "vertical" : "horizontal"}
        >
          <defs>
            {series.map((s, i) => (
              <linearGradient
                key={s}
                id={`${uid}-${expanded}-${i}`}
                x1="0"
                y1="0"
                x2="0"
                y2="1"
              >
                <stop
                  offset="0%"
                  stopColor={colors[i % colors.length]}
                  stopOpacity={0.3}
                />
                <stop
                  offset="100%"
                  stopColor={colors[i % colors.length]}
                  stopOpacity={0.03}
                />
              </linearGradient>
            ))}
          </defs>
          <CartesianGrid
            stroke="var(--border)"
            strokeDasharray="2 5"
            vertical={false}
          />
          <XAxis
            type={actualType === "horizontal_bar" ? "number" : "category"}
            dataKey={actualType === "horizontal_bar" ? undefined : x}
            tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
            tickFormatter={(v) =>
              String(v).length > 14 ? String(v).slice(0, 14) + "…" : String(v)
            }
            minTickGap={20}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            type={actualType === "horizontal_bar" ? "category" : "number"}
            dataKey={actualType === "horizontal_bar" ? x : undefined}
            axisLine={false}
            tickLine={false}
            width={actualType === "horizontal_bar" ? 90 : 65}
            tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
            tickFormatter={(v) =>
              typeof v === "number"
                ? new Intl.NumberFormat("zh-CN", {
                    notation: "compact",
                  }).format(v)
                : String(v).slice(0, 12)
            }
          />
          {tooltip}

          {mixedCounts && <YAxis yAxisId="count" orientation="right" allowDecimals={false} width={45}
            axisLine={false} tickLine={false} tick={{fill: "var(--muted-foreground)", fontSize: 11}}
          />}
          {series.map((s, i) =>
            actualType === "line" || (actualType === "composed" && (mixedCounts ? s.startsWith("count:") : i > 0)) ? (
              <Line
                key={s}
                yAxisId={mixedCounts && s.startsWith("count:") ? "count" : 0}
                dataKey={s}
                stroke={colors[i % colors.length]}
                strokeWidth={2.5}
                dot={
                  data.length < 12
                    ? { r: 3, strokeWidth: 2, fill: "var(--card)" }
                    : false
                }
                activeDot={{ r: 5, strokeWidth: 3, stroke: "var(--card)" }}
                strokeDasharray={i ? "5 4" : undefined}
                isAnimationActive={!reduced && (fresh || changedChart) && !expanded}
                animationDuration={220}
                animationEasing="ease-out"
                connectNulls={false}
              />
            ) : actualType === "area" ? (
              <Area
                key={s}
                dataKey={s}
                stroke={colors[i % colors.length]}
                fill={`url(#${uid}-${expanded}-${i})`}
                strokeWidth={2.5}
                isAnimationActive={!reduced && (fresh || changedChart) && !expanded}
                animationDuration={220}
                animationEasing="ease-out"
                connectNulls={false}
              />
            ) : (
              <Bar
                key={s}
                dataKey={s}
                fill={colors[i % colors.length]}
                maxBarSize={40}
                radius={actualType === "stacked_bar" ? 0 : 3}
                stackId={actualType === "stacked_bar" ? "total" : undefined}
                isAnimationActive={!reduced && (fresh || changedChart) && !expanded}
                animationDuration={220}
                animationEasing="ease-out"
              />
            ),
          )}
        </ComposedChart>
      );
    return (
      <div
        className={`ask-chart-canvas ${actualType === "donut" ? "ask-donut-wrap" : ""}`}
        role="img"
        aria-label={`${title}，${labels[actualType]}；可切换到数据表阅读具体数值。`}
      >
        {actualType !== "donut" && <div className="ask-chart-readout" ref={expanded ? setExpandedTooltipHost : setTooltipHost}>
          <span className="ask-chart-readout-hint">移至图形或使用方向键查看数值</span>
        </div>}
        {mixedCounts && <div className="ask-chart-axis-labels" aria-hidden="true"><span>数值</span><span>记录数</span></div>}
        <div style={{ height }}>
          <ResponsiveContainer width="100%" height="100%">
            {chart}
          </ResponsiveContainer>
        </div>
        {actualType === "donut" && (
          <div className="ask-donut-center">
            <span title={hovered != null ? fmt(data[hovered]?.[x]) : name(series[0])}>
              {hovered != null ? fmt(data[hovered]?.[x]) : name(series[0])}
            </span>
            <strong>
              {fmt(
                hovered != null
                  ? data[hovered]?.[series[0]]
                  : analysis.totals?.[series[0]] ?? data.reduce(
                      (sum, row) => sum + Number(row[series[0]] ?? 0),
                      0,
                    ),
              )}
            </strong>
            <small>
              {hovered != null
                ? `${((Number(data[hovered]?.[series[0]] ?? 0) / data.reduce((sum, row) => sum + Number(row[series[0]] ?? 0), 0)) * 100).toFixed(1)}%`
                : "完整分组"}
            </small>
          </div>
        )}
      </div>
    );
  }
  const queryDetails = Object.entries(readableAnalysisQuery(analysis));
  const sourceTask = analysis.source.request.task_id;
  const legend = actualType !== "table" && actualType !== "metric" && (
        <div className="ask-chart-legend">
          {(actualType === "pie" || actualType === "donut"
            ? data.map((r) => String(r[x]))
            : series.map(name)
          ).map((label, i) => (
            actualType === "pie" || actualType === "donut" ?
              <button type="button" key={label + i} title={`${label} · ${fmt(data[i]?.[series[0]])}`}
                aria-label={`${label}：${fmt(data[i]?.[series[0]])}`}
                onFocus={() => setHovered(i)} onBlur={() => setHovered(null)}
                onMouseEnter={() => setHovered(i)} onMouseLeave={() => setHovered(null)}>
                <i style={{ background: colors[i % colors.length] }} />
                <span>{label}</span>
                <b>{fmt(data[i]?.[series[0]])}</b>
              </button> : <span key={label + i} title={label}>
                <svg className="ask-series-symbol" width="25" height="12" viewBox="0 0 25 12" aria-hidden="true" data-series-kind={actualType === "line" || (actualType === "composed" && (mixedCounts ? series[i].startsWith("count:") : i > 0)) ? "line" : actualType === "area" ? "area" : actualType === "scatter" ? "scatter" : "bar"}>
                  {actualType === "line" || (actualType === "composed" && (mixedCounts ? series[i].startsWith("count:") : i > 0)) ? <>
                    <line x1="1" x2="24" y1="6" y2="6" stroke={colors[i % colors.length]} strokeWidth="2" strokeDasharray={i ? "5 4" : undefined} />
                    <circle cx="12.5" cy="6" r="2.5" fill="var(--card)" stroke={colors[i % colors.length]} strokeWidth="1.5" />
                  </> : actualType === "area" ? <>
                    <path d="M1 10V5L9 2L16 6L24 3V10Z" fill={colors[i % colors.length]} fillOpacity=".2" />
                    <path d="M1 5L9 2L16 6L24 3" fill="none" stroke={colors[i % colors.length]} strokeWidth="2" />
                  </> : actualType === "scatter" ? <circle cx="12.5" cy="6" r="3" fill={colors[i % colors.length]} />
                    : <rect x="6" y="1" width="13" height="10" rx="1" fill={colors[i % colors.length]} />}
                </svg>{label}
              </span>
          ))}
        </div>
      );
  return (
    <section className="ask-analysis">
      <header>
        <div>
          <h3 title={title}>{title}</h3>
        </div>
        <div className="ask-chart-actions">
          <select
            aria-label="图表展示方式"
            value={actualType}
            onChange={(e) => { setChangedChart(true); setHovered(null); setType(e.target.value as ChartType); }}
          >
            {Object.entries(labels)
              .filter(([k]) => available(k as ChartType))
              .map(([k, label]) => (
                <option value={k} key={k}>
                  {label}
                </option>
              ))}
          </select>
          <button
            type="button"
            title="展开图表"
            aria-label={`展开${title}`}
            onClick={() => setExpanded(true)}
          >
            <Expand size={15} />
          </button>
        </div>
      </header>
      <p className="ask-chart-description">
        {analysis.source.row_count} 条记录 · {analysis.source.document_count}{" "}
        份来源
        {mixedCounts && actualType === "composed" && " · 左轴：数值；右轴：记录数"}
        {updated && <time dateTime={analysis.source.generated_at}> · 更新于 {new Date(analysis.source.generated_at).toLocaleString("zh-CN")}</time>}
      </p>
      <motion.div key={actualType} initial={fresh || changedChart ? { opacity: .65 } : false}
        animate={{ opacity: 1 }} transition={{ duration: reduced ? 0 : .18 }}>
        {graphic(false)}
      </motion.div>
      {legend}
      {[...new Set(analysis.warnings)].map(warning =>
        <p className="ask-quality-summary" key={warning}>{warning.replace(/：有 (\d+) 个非空值无法作为数字，已排除；未按零计算。$/, "中有 $1 条非数字值未计入。")}</p>)}
      {analysis.truncated && <p className="ask-quality-summary">{analysis.rows ? `共 ${analysis.source.row_count} 条记录，预览 ${analysis.rows.length} 条。` : `共 ${analysis.group_count ?? "多"} 组，展示前 ${data.length} 组；统计总计包含完整查询范围。`}</p>}
      <Fold title="来源" className="ask-analysis-details">
      <div className="ask-analysis-evidence">
      {actualType !== "table" && actualType !== "metric" && <Fold title="查看明细">{table()}</Fold>}
      {(analysis.group_count ?? data.length) > 1 && analysis.totals && (
        <div className="ask-totals">
          <span>完整查询范围</span>
          {series.map((key) => (
            <span key={key}>
              {name(key)} <strong>{fmt(analysis.totals?.[key])}</strong>
            </span>
          ))}
        </div>
      )}

      <div className="ask-analysis-source">
      <footer>
        <button
          className="ask-resource-link"
          type="button"
          onClick={() =>
            navigate({ kind: "table", id: analysis.source.table_id })
          }
        >
          <Database size={13} />
          {analysis.source.table_name}
          <ArrowUpRight size={12} />
        </button>
        {typeof sourceTask === "string" && sourceTask && <button className="ask-resource-link" type="button"
          onClick={() => navigate({ kind: "task", id: sourceTask })}>查看所选原文件 <ArrowUpRight size={12} /></button>}
        <time dateTime={analysis.source.generated_at}>
          生成于 {new Date(analysis.source.generated_at).toLocaleString("zh-CN")}
        </time>
      </footer>
        {queryDetails.length > 0 ? <Fold title="查询条件">
          <dl className="ask-query-conditions" aria-label="查询条件">
            {queryDetails.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
          </dl>
        </Fold> : analysis.source.scope_description && <p className="ask-notice">{analysis.source.scope_description}</p>}
      </div>
        {(refresh || statisticFromAnalysis(analysis, spec)) && <div className="ask-analysis-refresh">
          {statisticFromAnalysis(analysis, spec) && <button type="button" className="ask-resource-link" onClick={() =>
            window.dispatchEvent(new CustomEvent("zhiyi:pin-analysis", { detail: { analysis, spec } }))
          }>添加到仪表盘</button>}
          {refresh && <button type="button" className="ask-resource-link" disabled={refreshing} onClick={async () => {
            setRefreshing(true);
            try { await refresh(); } finally { setRefreshing(false); }
          }}>{refreshing ? "正在刷新…" : "按最新数据重新分析"}</button>}
        </div>}
      </div>
      </Fold>
      <dialog
        ref={dialog}
        aria-label={title}
        onClose={(event) => {
          event.stopPropagation();
          setExpanded(false);
        }}
        onCancel={(event) => event.stopPropagation()}
        className="ask-chart-dialog"
        onClick={(e) => {
          if (e.target === e.currentTarget) dialog.current?.close();
        }}
      >
        {expanded && (
          <>
            <header>
              <h2>{title}</h2>
              <button
                type="button"
                aria-label="关闭展开图表"
                onClick={() => dialog.current?.close()}
              >
                <X size={18} />
              </button>
            </header>
            <p className="ask-chart-description">{analysis.source.row_count} 条记录 · {analysis.source.document_count} 份来源
              {updated && <time dateTime={analysis.source.generated_at}> · 更新于 {new Date(analysis.source.generated_at).toLocaleString("zh-CN")}</time>}
            </p>
            {analysis.truncated && <p className="ask-quality-summary">{analysis.rows ? `共 ${analysis.source.row_count} 条记录，预览 ${analysis.rows.length} 条。` : `共 ${analysis.group_count ?? "多"} 组，展示前 ${data.length} 组；统计总计包含完整查询范围。`}</p>}
            {graphic(true)}
            {legend}
            {actualType !== "table" && actualType !== "metric" && <Fold title="查看明细">{table()}</Fold>}
          </>
        )}
      </dialog>
    </section>
  );
}

// Include callback changes: old refresh closures can refer to another thread.
export const AnalysisCard = memo(AnalysisCardView);
