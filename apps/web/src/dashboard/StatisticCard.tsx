import { useEffect, useRef, useState } from "react";
import { ArrowDown, ArrowUp, ArrowUpRight, Loader2, MoreHorizontal, Pencil, RotateCw, Trash2 } from "lucide-react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { dashboardApi } from "./api";
import { DashboardChart } from "./DashboardChart";
import type { Statistic, StatisticResult } from "./types";
import type { DashboardRange } from "../types";

export function StatisticCard({ card, days, refresh, first, last, onEdit, onDelete, onMove, onOpenTable }: {
  card: Statistic; days: DashboardRange; refresh: number; first: boolean; last: boolean;
  onEdit: () => void; onDelete: () => void; onMove: (direction: number) => void; onOpenTable?: (id: string) => void;
}) {
  const element = useRef<HTMLElement>(null);
  const [visible, setVisible] = useState(typeof IntersectionObserver === "undefined");
  const [result, setResult] = useState<StatisticResult | null>(null);
  const [resultDays, setResultDays] = useState(days);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(entries => setVisible(entries.some(entry => entry.isIntersecting)), { rootMargin: "120px" });
    if (element.current) observer.observe(element.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!visible) return;
    let current = true;
    setLoading(true); setError("");
    dashboardApi.result(card.id, days).then(value => { if (current) { setResult(value); setResultDays(days); } })
      .catch(cause => { if (current) setError(cause.message); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [card.id, card.updated_at, days, refresh, reload, visible]);
  const metric = result?.analysis?.metric_keys?.[0] ?? "";
  const data = (result?.analysis?.data ?? []).map(row => ({ label: String(row.label ?? "全部"), value: typeof row[metric] === "number" ? row[metric] as number : null }));
  return <article ref={element} className="dashboard-panel statistic-card" aria-label={card.name} aria-busy={loading}>
    <div className="dashboard-panel-heading"><div className="dashboard-panel-title"><h2 title={card.name}>{card.name}</h2><p>{result?.analysis?.source.table_name ?? "我的统计"} · {card.time_range === "dashboard" && card.date_field ? (resultDays === "all" ? "全部数据" : resultDays === 1 ? "今日" : `近 ${resultDays} 天`) : "全部数据"}</p></div>
      <div className="dashboard-card-actions">{loading && result && <Loader2 size={14} className="spin" aria-label="正在刷新统计" />}<DropdownMenu.Root><DropdownMenu.Trigger asChild><button className="icon-button" aria-label={`管理统计：${card.name}`}><MoreHorizontal size={18} /></button></DropdownMenu.Trigger><DropdownMenu.Portal><DropdownMenu.Content className="statistic-menu" align="end" sideOffset={6}><DropdownMenu.Item onSelect={onEdit}><Pencil size={14} />修改统计</DropdownMenu.Item><DropdownMenu.Item disabled={first} onSelect={() => onMove(-1)}><ArrowUp size={14} />前移</DropdownMenu.Item><DropdownMenu.Item disabled={last} onSelect={() => onMove(1)}><ArrowDown size={14} />后移</DropdownMenu.Item><DropdownMenu.Separator /><DropdownMenu.Item onSelect={onDelete}><Trash2 size={14} />删除统计</DropdownMenu.Item></DropdownMenu.Content></DropdownMenu.Portal></DropdownMenu.Root></div>
    </div>
    {error ? <div className="dashboard-card-error" role="alert"><span>{error}</span><small>当前结果无法确认，重试后更新。</small><button className="btn secondary xs" onClick={() => setReload(value => value + 1)}><RotateCw size={13} />重新加载</button></div> : result?.status === "invalid" ? <div className="dashboard-card-error" role="status"><span>{result.message || "原表或字段已变化，请修改统计。"}</span><button className="btn secondary xs" onClick={onEdit}>修改统计</button></div> : result?.status === "empty" ? <div className="dashboard-chart-empty"><span>暂无符合条件的记录</span><small>数据更新后会自动显示。</small></div> : result ? <DashboardChart data={data} type={result.display} label={result.analysis?.metric_labels?.[metric] ?? "数值"} /> : <div className="dashboard-chart-empty"><Loader2 size={20} className="spin" /><span>正在读取统计</span></div>}
    {!!result?.analysis?.warnings?.length && !error && result.status === "ready" && <p className="statistic-note">{result.analysis.warnings.join("；")}</p>}
    <div className="statistic-card-footer"><span>{error ? "等待重新加载" : loading && result ? "正在更新…" : result?.status === "ready" ? "随当前数据更新" : ""}</span>{onOpenTable && <button className="dashboard-text-action" onClick={() => onOpenTable(card.table_id)}>查看原表<ArrowUpRight size={13} /></button>}</div>
  </article>;
}
