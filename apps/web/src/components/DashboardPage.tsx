import { useEffect, useMemo, useState } from "react";
import { ArrowRight, Check, Database, FileText, Layers, Loader2, Plus, RotateCw, TrendingUp } from "lucide-react";
import { getDashboardSummary, getDashboardTrend, getTables, getTaskSummary, subscribeTaskEvents } from "../api";
import type { DashboardRange, DashboardSummary, DataTableRead, TaskSummary, TrendPoint } from "../types";
import { Fold } from "../assistant/Interaction";
import { dashboardApi } from "../dashboard/api";
import { AnimatedNumber, DashboardChart, formatNumber } from "../dashboard/DashboardChart";
import { StatisticCard } from "../dashboard/StatisticCard";
import { StatisticDialog } from "../dashboard/StatisticDialog";
import type { DashboardOverview, Statistic } from "../dashboard/types";
import { Icon } from "./Icon";
import "./dashboard.css";

type Destination = "tables" | "workspace" | "history" | "templates" | "backups" | "extract";
const ranges: Record<DashboardRange, string> = { 1: "今日", 7: "近 7 天", 30: "近 30 天", 90: "近 90 天", all: "全部" };
const rangeDescription = (range: DashboardRange) => range === "all" ? "全部保留历史" : ranges[range];
const bucketDescription = (bucket = "day", interval = 1) => bucket === "year" && interval > 1 ? `每 ${interval} 年` : ({ day: "按日", month: "按月", year: "按年" }[bucket] ?? "按日");
const purposeNames: Record<string, string> = { extraction: "文件提取", matching: "智能匹配", template_generation: "模板生成", assistant: "问知意" };

export function DashboardPage({ onNavigate, onOpenTable, onReview }: { onNavigate?: (page: Destination) => void; onOpenTable?: (id: string) => void; onReview?: () => void } = {}) {
  const [days, setDays] = useState<DashboardRange>(7);
  const [reload, setReload] = useState(0);
  const [data, setData] = useState<{ tasks: TaskSummary; tables: DataTableRead[]; stats: DashboardSummary; trend: TrendPoint[]; days: DashboardRange } | null>(null);
  const [overview, setOverview] = useState<{ data: DashboardOverview; days: DashboardRange } | null>(null);
  const [overviewError, setOverviewError] = useState("");
  const [cards, setCards] = useState<Statistic[]>([]);
  const [limit, setLimit] = useState(4);
  const [cardsError, setCardsError] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<Statistic | "new" | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [mutating, setMutating] = useState(false);
  const [chartView, setChartView] = useState<"files" | "rows">("files");
  const [notice, setNotice] = useState("");
  useEffect(() => {
    let current = true;
    setLoading(true); setError("");
    Promise.all([getTaskSummary(), getTables(), getDashboardSummary(days), getDashboardTrend(days)])
      .then(([tasks, tables, stats, trend]) => { if (current) setData({ tasks, tables, stats, trend, days }); })
      .catch(cause => { if (current) setError(cause instanceof Error ? cause.message : "统计加载失败，请重试。"); })
      .finally(() => { if (current) setLoading(false); });
    setOverviewError("");
    dashboardApi.overview(days).then(value => { if (current) setOverview({ data: value, days }); }).catch(cause => { if (current) setOverviewError(cause.message); });
    return () => { current = false; };
  }, [days, reload]);
  useEffect(() => {
    let current = true;
    dashboardApi.cards().then(value => { if (current) { setCards(value.items); setLimit(value.limit); setCardsError(""); } }).catch(cause => { if (current) setCardsError(cause.message); });
    return () => { current = false; };
  }, [reload]);
  useEffect(() => {
    let timer = 0;
    const refresh = () => {
      window.clearTimeout(timer);
      if (!document.hidden) timer = window.setTimeout(() => setReload(value => value + 1), 350);
    };
    const unsubscribe = subscribeTaskEvents(refresh);
    window.addEventListener("zhiyi:assistant-changed", refresh);
    window.addEventListener("zhiyi:statistics-changed", refresh);
    document.addEventListener("visibilitychange", refresh);
    const interval = window.setInterval(refresh, 30000);
    return () => { unsubscribe(); window.clearTimeout(timer); window.clearInterval(interval); window.removeEventListener("zhiyi:assistant-changed", refresh); window.removeEventListener("zhiyi:statistics-changed", refresh); document.removeEventListener("visibilitychange", refresh); };
  }, []);
  const stats = data?.stats;
  const tasks = data?.tasks;
  const fileData = useMemo(() => (data?.trend ?? []).map(point => ({ label: point.label || point.date, value: point.total })), [data?.trend]);
  const rowData = useMemo(() => (overview?.data.rows_trend ?? []).map(point => ({ label: point.label || point.date, value: point.count })), [overview?.data.rows_trend]);
  const composition = useMemo(() => {
    const sorted = [...(data?.tables ?? [])].filter(table => table.row_count > 0).sort((a, b) => b.row_count - a.row_count);
    return [...sorted.slice(0, 4).map(table => ({ label: table.name, value: table.row_count })), ...(sorted.length > 4 ? [{ label: "其他数据表", value: sorted.slice(4).reduce((sum, table) => sum + table.row_count, 0) }] : [])];
  }, [data?.tables]);
  const received = fileData.reduce((sum, item) => sum + item.value, 0);
  const usage = overview?.data.model_usage;
  async function removeCard(id: string) {
    setMutating(true); setCardsError("");
    try { await dashboardApi.remove(id); setDeleting(null); setNotice("已删除统计，原表数据保留。"); setReload(value => value + 1); }
    catch (cause) { setCardsError(cause instanceof Error ? cause.message : "删除失败，请重试。"); }
    finally { setMutating(false); }
  }
  async function moveCard(index: number, direction: number) {
    if (mutating || index + direction < 0 || index + direction >= cards.length) return;
    const reordered = cards.map(card => card.id);
    [reordered[index], reordered[index + direction]] = [reordered[index + direction], reordered[index]];
    setMutating(true); setCardsError("");
    try { await dashboardApi.order(reordered); setReload(value => value + 1); }
    catch (cause) { setCardsError(cause instanceof Error ? cause.message : "排序失败，请重试。"); }
    finally { setMutating(false); }
  }
  return <div className="view dashboard-page">
    <div className="page-header"><div className="eyebrow">概览</div><h1>数据仪表盘</h1><div className="support">从文件到数据，看见每一次积累。</div></div>
    <div className="dashboard-toolbar"><div className="dashboard-range-control"><label htmlFor="dashboard-range">时间范围</label><select id="dashboard-range" className="form-select" value={days} onChange={event => setDays(event.target.value === "all" ? "all" : Number(event.target.value))}>{Object.entries(ranges).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></div><button className="btn secondary xs" disabled={loading} onClick={() => setReload(value => value + 1)}><RotateCw size={13} className={loading ? "spin" : ""} />刷新</button></div>
    {error && <div className="callout danger" role="alert">{error}<button className="btn secondary xs" onClick={() => setReload(value => value + 1)}>重新加载</button></div>}
    {!data && loading ? <div className="dashboard-panel dashboard-loading"><Loader2 size={20} className="spin" />正在读取统计</div> : data && <>
      <section className="dashboard-metrics" aria-label="数据概览" aria-busy={loading}>
        {[
          { label: "文件任务", value: tasks?.total, hint: "当前保留", icon: FileText, page: "history" as const },
          { label: "数据记录", value: stats?.row_count, hint: `当前 · ${formatNumber(stats?.table_count)} 张表`, icon: Database, page: "tables" as const },
          { label: "可用模板", value: stats?.template_count, hint: "当前启用", icon: Layers, page: "templates" as const },
          { label: "新增记录", value: stats?.new_rows, hint: rangeDescription(data.days), icon: TrendingUp, page: "tables" as const },
        ].map((item, index) => <button key={item.label} className="dashboard-metric" style={{ animationDelay: `${index * 40}ms` }} onClick={() => onNavigate?.(item.page)}><span className="dashboard-metric-top"><span className="dashboard-metric-icon"><Icon icon={item.icon} size={17} /></span><span>{item.label}</span><ArrowRight size={13} /></span><strong><AnimatedNumber value={item.value} /></strong><small>{item.hint}</small></button>)}
      </section>
      {Boolean(tasks?.needs_review || tasks?.failed || tasks?.waiting_for_action || tasks?.pending_exports) && <div className="dashboard-attention-strip"><span>需要关注</span>{!!tasks?.needs_review && <button onClick={() => onReview ? onReview() : onNavigate?.("history")}>{tasks.needs_review} 份文件待复核<ArrowRight size={12} /></button>}{!!tasks?.failed && <button onClick={() => onNavigate?.("workspace")}>{tasks.failed} 个任务失败<ArrowRight size={12} /></button>}{!!tasks?.waiting_for_action && <button onClick={() => onNavigate?.("workspace")}>{tasks.waiting_for_action} 个任务待处理<ArrowRight size={12} /></button>}{!!tasks?.pending_exports && <button onClick={() => onNavigate?.("workspace")}>{tasks.pending_exports} 份文件待整理<ArrowRight size={12} /></button>}</div>}
      <div className="dashboard-charts-grid">
        <section className="dashboard-panel dashboard-main-trend" aria-label="期间趋势"><div className="dashboard-panel-heading"><div><h2>{chartView === "files" ? "文件接收趋势" : "数据新增趋势"}</h2><p>{rangeDescription(chartView === "files" ? data.days : overview?.days ?? data.days)} · {chartView === "files" ? bucketDescription(data.trend[0]?.bucket, data.trend[0]?.interval) : bucketDescription(overview?.data.trend_bucket, overview?.data.trend_interval)}</p></div><div className="dashboard-chart-switch" role="group" aria-label="趋势内容"><button aria-pressed={chartView === "files"} onClick={() => setChartView("files")}>文件</button><button aria-pressed={chartView === "rows"} onClick={() => setChartView("rows")}>数据</button></div></div><div className="dashboard-trend-summary"><strong><AnimatedNumber value={chartView === "files" ? received : rowData.reduce((sum, row) => sum + row.value, 0)} /></strong><span>{chartView === "files" ? "份文件接收" : "条记录新增"}</span></div>{chartView === "rows" && overviewError ? <div className="dashboard-card-error" role="alert">{overviewError}<button className="btn secondary xs" onClick={() => setReload(value => value + 1)}>重新加载趋势</button></div> : <DashboardChart data={chartView === "files" ? fileData : rowData} label={chartView === "files" ? "接收文件" : "新增记录"} unit={chartView === "files" ? "份" : "条"} />}</section>
        <section className="dashboard-panel" aria-label="数据分布"><div className="dashboard-panel-heading"><div><h2>数据分布</h2><p>当前各表记录数</p></div><button className="dashboard-text-action" onClick={() => onNavigate?.("tables")}>数据仓库<ArrowRight size={13} /></button></div><DashboardChart data={composition} type="donut" label="数据记录" unit="条" height={174} /></section>
      </div>
    </>}
    <section className="dashboard-statistics" aria-label="我的统计"><div className="dashboard-section-heading"><div><h2>我的统计</h2><span>{cards.length ? `${cards.length} / ${limit}` : "把常看的数据留在这里"}</span></div><button className="btn secondary" disabled={cards.length >= limit || mutating || Boolean(cardsError && !cards.length)} onClick={() => { setNotice(""); setEditing("new"); }}><Plus size={14} />添加统计</button></div>
      {cardsError && <div className="callout danger" role="alert">{cardsError}<button className="btn secondary xs" onClick={() => setReload(value => value + 1)}>重新读取统计</button></div>}
      {notice && <div className="dashboard-notice" role="status"><Check size={13} />{notice}</div>}
      {!!cards.length && <div className="dashboard-statistic-grid">{cards.map((card, index) => <div key={card.id} className="dashboard-statistic-slot"><StatisticCard card={card} days={days} refresh={reload} first={index === 0} last={index === cards.length - 1} onEdit={() => setEditing(card)} onDelete={() => setDeleting(card.id)} onMove={direction => { void moveCard(index, direction); }} onOpenTable={onOpenTable} />{deleting === card.id && <div className="statistic-delete-prompt" role="alert"><span>删除这张统计？原表数据会保留。</span><div><button className="btn secondary xs" disabled={mutating} onClick={() => setDeleting(null)}>取消</button><button className="btn danger xs" disabled={mutating} onClick={() => { void removeCard(card.id); }}>删除统计</button></div></div>}</div>)}</div>}
      {cards.length >= limit && <p className="dashboard-limit-note">已添加 {limit} 张统计，可修改或替换已有统计。</p>}
    </section>
    <section className="dashboard-panel dashboard-model-panel" aria-label="模型使用"><div className="dashboard-panel-heading"><div><h2>模型使用</h2><p>{rangeDescription(overview?.days ?? days)} · 按实际调用记录</p></div></div>{overviewError ? <div className="dashboard-card-error" role="alert">{overviewError}<button className="btn secondary xs" onClick={() => setReload(value => value + 1)}>重新加载模型统计</button></div> : usage ? <><div className="dashboard-usage-metrics"><div><span>模型调用</span><strong><AnimatedNumber value={usage.calls} /><small>次</small></strong></div><div><span>平均调用耗时</span><strong>{usage.average_elapsed_ms == null ? "—" : formatNumber(usage.average_elapsed_ms / 1000)}<small>秒</small></strong></div><div><span>已上报 Token</span><strong>{formatNumber(usage.total_tokens)}</strong></div><div><span>失败调用</span><strong>{formatNumber(usage.failed)}<small>次</small></strong></div></div><Fold className="dashboard-data-details" title="使用明细与统计口径">{usage.by_purpose.length ? <div className="dashboard-data-scroll"><table><thead><tr><th>用途 / 模型</th><th>调用</th><th>失败</th><th>平均耗时</th><th>Token</th></tr></thead><tbody>{usage.by_purpose.map((item, index) => <tr key={`${item.purpose}-${item.model}-${index}`}><td>{purposeNames[item.purpose] ?? item.purpose}<small>{item.model}</small></td><td>{item.calls}</td><td>{item.failed}</td><td>{item.average_elapsed_ms == null ? "—" : formatNumber(item.average_elapsed_ms / 1000)} 秒</td><td>{formatNumber(item.total_tokens)}</td></tr>)}</tbody></table></div> : <p>这个时间范围内还没有模型调用记录。</p>}<p>耗时统计覆盖 {usage.elapsed_sample_count} / {usage.calls} 次调用。Token 上报覆盖 {usage.calls_with_usage} / {usage.calls} 次调用；未上报显示“—”。缓存读入占比 {usage.cache_hit_ratio == null ? "—" : `${formatNumber(usage.cache_hit_ratio * 100)}%`}，覆盖 {usage.calls_with_cache_usage} 次调用。失败次数不代表内容准确率。</p><p>{usage.history_note}</p></Fold></> : <div className="dashboard-loading"><Loader2 size={16} className="spin" />正在读取模型使用</div>}</section>
    {data && <Fold className="dashboard-definitions" title="仪表盘统计说明"><p>“当前”统计只包含现在保留的任务、数据与启用模板。数据记录包含提取、导入、手工新增及合并副本；期间新增按记录创建时间统计。</p><p>文件趋势按任务接收日期统计，不等于当天完成量。日期范围按本机日期从当天零点起计算，近 7 天包含今天和之前 6 天。“全部”覆盖当前保留的完整历史，跨度较长时自动按月或年汇总；已删除的数据不计入。</p><p>我的统计读取原表当前数据；涉及文件公共字段时沿用文件级去重规则。删除统计不影响原表。模型调用耗时包括请求等待，不包含任务排队，也不是内容准确率。</p></Fold>}
    {editing && <StatisticDialog existing={editing === "new" ? undefined : editing} days={days} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); setNotice("统计已保存，会随当前数据更新。"); setReload(value => value + 1); }} />}
  </div>;
}
