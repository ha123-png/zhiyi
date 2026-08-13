import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  CircleX,
  Database,
  FileText,
  Info,
  Loader2,
  Timer,
} from "lucide-react";
import { getDashboardSummary, getDashboardTrend, getTables, getTaskSummary } from "../api";
import { parseServerTime, serverDate } from "../time";
import type { DashboardSummary, DataTableRead, TaskSummary, TrendPoint } from "../types";
import { Icon } from "./Icon";

function formatDateLocal(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function formatRelativeTime(iso: string): string {
  const then = parseServerTime(iso);
  if (Number.isNaN(then)) return "—";
  const diffMs = Date.now() - then;
  if (diffMs < 0) return "刚刚";
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return "刚刚";
  if (diffMin < 60) return `${diffMin} 分钟前`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr} 小时前`;
  const diffDay = Math.floor(diffHr / 24);
  return `${diffDay} 天前`;
}

const WEEKDAY_LABELS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

/** 数字平滑过渡：目标值变化时从当前值 easeOut 缓动到新值（rAF 驱动）。 */
function useAnimatedNumber(target: number, duration = 450): number {
  const [display, setDisplay] = useState(target);
  const valueRef = useRef(target);
  const rafRef = useRef(0);

  useEffect(() => {
    const from = valueRef.current;
    if (from === target) return;
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3); // easeOutCubic
      const value = from + (target - from) * eased;
      valueRef.current = value;
      setDisplay(value);
      if (t < 1) rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, [target, duration]);

  return display;
}

function AnimatedNumber({
  value,
  format,
}: {
  value: number;
  format?: (n: number) => string;
}) {
  const display = useAnimatedNumber(value);
  return <>{format ? format(display) : Math.round(display)}</>;
}

export function DashboardPage({ onNavigate }: { onNavigate?: (page: "tables") => void } = {}) {
  const [dashRange, setDashRange] = useState("week");
  const [kpiTipOpen, setKpiTipOpen] = useState(false);
  // 任务统计走聚合接口（ISSUE-067）：不拉全量任务表到前端内存
  const [taskSummary, setTaskSummary] = useState<TaskSummary | null>(null);
  const [trend, setTrend] = useState<TrendPoint[]>([]);
  const [tables, setTables] = useState<DataTableRead[]>([]);
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const rangeDays =
    dashRange === "today" ? 1 :
    dashRange === "month" ? 30 :
    dashRange === "quarter" ? 90 : 7;
  const rangeLabel =
    dashRange === "today" ? "今日" :
    dashRange === "month" ? "近 30 天" :
    dashRange === "quarter" ? "近 90 天" : "近 7 天";

  useEffect(() => {
    setLoading(true);
    Promise.all([
      getTaskSummary(),
      getTables(),
      getDashboardSummary(),
      getDashboardTrend(rangeDays),
    ])
      .then(([summaryAll, tableList, stats, trendData]) => {
        setTaskSummary(summaryAll);
        setTables(tableList);
        setSummary(stats);
        setTrend(trendData);
      })
      .catch((err) =>
        setError(err instanceof Error ? err.message : "加载失败"),
      )
      .finally(() => setLoading(false));
  }, [rangeDays]);

  const totalFiles = taskSummary?.total ?? 0;
  const extractedRows = tables.reduce((sum, t) => sum + t.row_count, 0);
  // 切换时间范围时保留旧内容直接做动画，不闪整页加载圈（仅首次加载显示）
  const hasData = summary !== null || taskSummary !== null;

  const rangeStart = new Date();
  rangeStart.setDate(rangeStart.getDate() - (rangeDays - 1));
  const inRange = (iso: string) => serverDate(iso) >= rangeStart;
  // 范围统计直接来自按天聚合，不再内存过滤全量任务
  const rangeProcessed = trend.reduce((sum, p) => sum + p.total, 0);
  const rangeSuccess = trend.reduce((sum, p) => sum + p.completed, 0);
  const rangeFailed = trend.reduce((sum, p) => sum + p.failed, 0);
  const rangeNewRecords = tables
    .filter((t) => inRange(t.created_at))
    .reduce((sum, t) => sum + t.row_count, 0);

  // 趋势图数据：后端已按服务器本地时区切天（与前端展示一致）
  const trendDays: Date[] = trend.map((p) => serverDate(p.date));
  const trendCounts = trend.map((p) => p.total);
  const trendTotal = trendCounts.reduce((sum, c) => sum + c, 0);
  const maxCount = Math.max(...trendCounts, 1);
  const trendPoints = trendCounts
    .map((count, i) => {
      const x =
        trendDays.length === 1
          ? 0
          : Math.round((i * 700) / (trendDays.length - 1));
      const y = Math.round(160 - (count / maxCount) * 120);
      return `${x},${y}`;
    })
    .join(" ");
  const trendPolygonPoints = `${trendPoints} 700,200 0,200`;

  return (
    <div className="view">
      <div className="page-header">
        <div className="eyebrow">概览</div>
        <h1>数据仪表盘</h1>
        <div className="support">文件处理、提取质量与数据仓库的全局视图。</div>
        <div className="dash-filter-bar">
          <span className="dash-filter-label">时间范围</span>
          <select
            aria-label="时间范围"
            className="dash-filter-select"
            value={dashRange}
            onChange={(e) => setDashRange(e.target.value)}
          >
            <option value="today">今日</option>
            <option value="week">本周</option>
            <option value="month">本月</option>
            <option value="quarter">本季</option>
          </select>
        </div>
      </div>

      {error && (
        <div className="callout danger" style={{ marginBottom: 16 }}>
          {error}
        </div>
      )}

      {loading && !hasData && (
        <div className="card flush" style={{ padding: 48, textAlign: "center" }}>
          <Icon icon={Loader2} size={24} className="spin" />
        </div>
      )}
      <>
          {/* KPI 卡片 */}
          <div className="dash-kpi-row">
            <div className="dash-kpi-card" data-kpi="files">
              <div className="dash-kpi-icon">
                <Icon icon={FileText} size={24} />
              </div>
              <div className="dash-kpi-body">
                <div className="dash-kpi-value">
                  <AnimatedNumber value={totalFiles} />
                  <span className="dash-kpi-unit">个</span>
                </div>
                <div className="dash-kpi-label">累计处理文件</div>
                <div className="dash-kpi-sub">按当前任务实时统计</div>
              </div>
            </div>
            <div className="dash-kpi-card" data-kpi="time">
              <div className="dash-kpi-icon">
                <Icon icon={Timer} size={24} />
              </div>
              <div className="dash-kpi-body">
                <div className="dash-kpi-value">
                  {summary?.average_elapsed_seconds != null ? (
                    <AnimatedNumber
                      value={summary.average_elapsed_seconds}
                      format={(n) => String(Math.round(n * 10) / 10)}
                    />
                  ) : (
                    "—"
                  )}
                  <span className="dash-kpi-unit">
                    {summary?.average_elapsed_seconds != null ? "秒" : ""}
                  </span>
                </div>
                <div className="dash-kpi-label">平均处理耗时</div>
                <div className="dash-kpi-sub">
                  {summary?.processed_count
                    ? `按 ${summary.processed_count} 个已处理文件统计`
                    : "暂无处理记录"}
                </div>
              </div>
            </div>
            <div className="dash-kpi-card" data-kpi="replace">
              <div
                className="dash-kpi-icon"
                style={{ position: "relative" }}
                onMouseEnter={() => setKpiTipOpen(true)}
                onMouseLeave={() => setKpiTipOpen(false)}
              >
                <Icon icon={Database} size={24} />
                <Icon
                  icon={Info}
                  className="kpi-tip-trigger"
                  size={11}
                  style={{
                    color: "var(--color-text-muted)",
                    cursor: "help",
                    position: "absolute",
                    bottom: "-2px",
                    right: "-2px",
                    background: "var(--card)",
                    borderRadius: "50%",
                    padding: "1px",
                  }}
                />
                {kpiTipOpen && (
                  <div
                    className="kpi-tip-popover"
                    style={{
                      position: "absolute",
                      top: "calc(100% + 6px)",
                      left: 0,
                      right: 0,
                      zIndex: 50,
                    }}
                  >
                    <div className="kpi-tip-title">统计规则</div>
                    <div className="kpi-tip-text">
                      无明细字段的文档按表头计 1 条；含明细的按明细行数累计
                    </div>
                  </div>
                )}
              </div>
              <div className="dash-kpi-body">
                <div className="dash-kpi-value">
                  <AnimatedNumber value={extractedRows} />
                  <span className="dash-kpi-unit">条</span>
                </div>
                <div className="dash-kpi-label">提取数据条数</div>
                <div className="dash-kpi-sub">按事实表实时统计</div>
              </div>
            </div>
            <div className="dash-kpi-card" data-kpi="error">
              <div className="dash-kpi-icon">
                <Icon icon={CircleX} size={24} />
              </div>
              <div className="dash-kpi-body">
                <div className="dash-kpi-value">
                  {summary?.success_rate != null ? (
                    <AnimatedNumber value={Math.round((1 - summary.success_rate) * 100)} />
                  ) : (
                    "—"
                  )}
                  <span className="dash-kpi-unit">
                    {summary?.success_rate != null ? "%" : ""}
                  </span>
                </div>
                <div className="dash-kpi-label">报错率</div>
                <div className="dash-kpi-sub">
                  {summary?.processed_count
                    ? `${summary.failed_count} 失败 / ${summary.processed_count} 处理`
                    : "暂无处理记录"}
                </div>
              </div>
            </div>
          </div>

          {/* 图表 + 今日概览 */}
          <div className="dash-charts-row">
            <div className="dash-card dash-card--chart">
              <div className="dash-card-header">
                <div className="dash-card-title">每日处理趋势</div>
                <div className="dash-card-hint">
                  {rangeLabel} · <strong><AnimatedNumber value={trendTotal} /></strong> 个文件
                </div>
              </div>
              <div className="dash-card-body">
                <div className="dash-chart-wrap">
                  <svg
                    className="dash-svg"
                    viewBox="0 0 700 200"
                    preserveAspectRatio="none"
                  >
                    <defs>
                      <linearGradient id="dashGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop
                          offset="0%"
                          stopColor="var(--color-primary)"
                          stopOpacity="0.25"
                        />
                        <stop
                          offset="100%"
                          stopColor="var(--color-primary)"
                          stopOpacity="0"
                        />
                      </linearGradient>
                    </defs>
                    <g className="dash-grid">
                      <line
                        x1="0"
                        y1="40"
                        x2="700"
                        y2="40"
                        stroke="var(--color-border)"
                        strokeDasharray="3,4"
                      />
                      <line
                        x1="0"
                        y1="100"
                        x2="700"
                        y2="100"
                        stroke="var(--color-border)"
                        strokeDasharray="3,4"
                      />
                      <line
                        x1="0"
                        y1="160"
                        x2="700"
                        y2="160"
                        stroke="var(--color-border)"
                        strokeDasharray="3,4"
                      />
                    </g>
                    {/* key 随范围变化强制重挂载，让折线绘制/淡入动画在切换范围时重播 */}
                    <g key={rangeDays}>
                      <polygon
                        className="dash-polygon"
                        fill="url(#dashGrad)"
                        points={trendPolygonPoints}
                      />
                      <polyline
                        className="dash-line"
                        pathLength={1}
                        fill="none"
                        stroke="var(--color-primary)"
                        strokeWidth="2.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        points={trendPoints}
                      />
                      <g className="dash-dots">
                        {trendCounts.map((count, i) => {
                          const x =
                            trendDays.length === 1
                              ? 0
                              : Math.round((i * 700) / (trendDays.length - 1));
                          const y = Math.round(160 - (count / maxCount) * 120);
                          return (
                            <circle
                              key={i}
                              cx={x}
                              cy={y}
                              r="3.5"
                              fill="var(--color-primary)"
                            />
                          );
                        })}
                      </g>
                    </g>
                  </svg>
                  <div className="dash-chart-xaxis">
                    {rangeDays <= 7
                      ? trendDays.map((d, i) => (
                          <span key={i}>{WEEKDAY_LABELS[d.getDay()]}</span>
                        ))
                      : (
                        <>
                          <span>{formatDateLocal(trendDays[0])}</span>
                          <span>{formatDateLocal(trendDays[trendDays.length - 1])}</span>
                        </>
                      )}
                  </div>
                </div>
              </div>
            </div>
            <div className="dash-card dash-card--today">
              <div className="dash-card-header">
                <div className="dash-card-title">{rangeLabel}概览</div>
                <div className="dash-card-hint">实时</div>
              </div>
              <div className="dash-card-body">
                <div className="dash-today-list">
                  <div className="dash-today-item">
                    <span className="dash-today-lbl">已处理</span>
                    <span className="dash-today-val">
                      <AnimatedNumber value={rangeProcessed} />
                      <small>个</small>
                    </span>
                  </div>
                  <div className="dash-today-item">
                    <span className="dash-today-lbl">成功</span>
                    <span
                      className="dash-today-val"
                      style={{ color: "var(--color-success)" }}
                    >
                      <AnimatedNumber value={rangeSuccess} />
                      <small>个</small>
                    </span>
                  </div>
                  <div className="dash-today-item">
                    <span className="dash-today-lbl">失败</span>
                    <span
                      className="dash-today-val"
                      style={{ color: "var(--color-danger)" }}
                    >
                      <AnimatedNumber value={rangeFailed} />
                      <small>个</small>
                    </span>
                  </div>
                  <div className="dash-today-item">
                    <span className="dash-today-lbl">新增记录</span>
                    <span className="dash-today-val">
                      <AnimatedNumber value={rangeNewRecords} />
                      <small>条</small>
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* 数据仓库概览 */}
          <div className="dash-card" style={{ marginTop: 20 }}>
            <div className="dash-card-header">
              <div className="dash-card-title">数据仓库概览</div>
              <button
                className="btn secondary xs"
                onClick={() => onNavigate?.("tables")}
              >
                查看全部 <Icon icon={ArrowRight} size={13} />
              </button>
            </div>
            <div className="dash-card-body" style={{ padding: 0 }}>
              <div
                className="data-table-wrap"
                style={{ border: 0, borderRadius: 0 }}
              >
                <div className="data-table-scroll">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>表名</th>
                        <th>行数</th>
                        <th>最近更新</th>
                        <th>状态</th>
                      </tr>
                    </thead>
                    <tbody>
                      {tables.slice(0, 5).map((t) => (
                        <tr key={t.id}>
                          <td>{t.name}</td>
                          <td className="numeric">{t.row_count + " 行"}</td>
                          <td>{formatRelativeTime(t.created_at)}</td>
                          <td>
                            <span className="badge live">活跃</span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </div>
      </>
    </div>
  );
}
