import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Loader2, X } from "lucide-react";
import { dashboardApi } from "./api";
import { DashboardChart } from "./DashboardChart";
import type { Statistic, StatisticInput, StatisticResult, StatisticTable } from "./types";
import type { DashboardRange } from "../types";

const empty: StatisticInput = { name: "", table_id: "", metric: "count", metric_field: null, group_field: null, time_bucket: null, date_field: null, time_range: "all", display: "auto" };
export function StatisticDialog({ initial, existing, days = 7, onClose, onSaved }: {
  initial?: Partial<StatisticInput>;
  existing?: Statistic;
  days?: DashboardRange;
  onClose: () => void;
  onSaved: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const uid = useId();
  const [draft, setDraft] = useState<StatisticInput>({ ...empty, ...existing, ...initial });
  const [named, setNamed] = useState(Boolean(existing || initial?.name));
  const [tables, setTables] = useState<StatisticTable[]>([]);
  const [optionsLoaded, setOptionsLoaded] = useState(false);
  const [optionsError, setOptionsError] = useState("");
  const [optionsReload, setOptionsReload] = useState(0);
  const [preview, setPreview] = useState<StatisticResult | null>(null);
  const [previewKey, setPreviewKey] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [previewReload, setPreviewReload] = useState(0);
  const table = tables.find(item => item.id === draft.table_id);
  const columns = table?.columns ?? [];
  const group = columns.find(item => item.key === draft.group_field);
  const suggestedName = `${table?.name ?? "数据"}${draft.group_field ? ` · 按${group?.label ?? "字段"}` : ""}${draft.metric === "count" ? "记录数" : ` · ${columns.find(item => item.key === draft.metric_field)?.label ?? "数值"}${draft.metric === "sum" ? "合计" : "平均值"}`}`;
  const input = useMemo<StatisticInput>(() => ({
    name: (named ? draft.name : suggestedName).trim(), table_id: draft.table_id,
    metric: draft.metric, metric_field: draft.metric === "count" ? null : draft.metric_field,
    group_field: draft.group_field, time_bucket: group?.value_type === "date" ? draft.time_bucket ?? "month" : null,
    date_field: draft.date_field, time_range: draft.date_field ? draft.time_range : "all", display: draft.display,
  }), [draft, named, suggestedName, group?.value_type]);
  const key = JSON.stringify(input);
  const ready = Boolean(input.table_id && input.name && (input.metric === "count" || input.metric_field));
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    return () => { previous?.focus(); };
  }, []);
  useEffect(() => {
    if (optionsLoaded && tables.length) dialog.current?.querySelector<HTMLSelectElement>("select")?.focus();
  }, [optionsLoaded, tables.length]);
  useEffect(() => {
    let active = true;
    setOptionsError("");
    dashboardApi.options().then(response => {
      if (!active) return;
      setTables(response.tables);
      setOptionsLoaded(true);
      setDraft(value => value.table_id || !response.tables.length ? value : { ...value, table_id: response.tables[0].id });
    }).catch(cause => { if (active) { setOptionsError(String(cause.message)); setOptionsLoaded(true); } });
    return () => { active = false; };
  }, [optionsReload]);
  useEffect(() => {
    let active = true;
    setError("");
    setPreview(null);
    if (!ready || !optionsLoaded || optionsError) { setLoading(false); return; }
    setLoading(true);
    const timer = window.setTimeout(() => {
      dashboardApi.preview(JSON.parse(key) as StatisticInput, days)
        .then(result => { if (active) { setPreview(result); setPreviewKey(key); } })
        .catch(cause => { if (active) setError(cause.message); })
        .finally(() => { if (active) setLoading(false); });
    }, 240);
    return () => { active = false; window.clearTimeout(timer); };
  }, [key, days, ready, optionsLoaded, optionsError, previewReload]);
  async function save() {
    if (busy || previewKey !== key || !preview || preview.status === "invalid") return;
    setBusy(true); setError("");
    try { await dashboardApi.save(input, existing, days); window.dispatchEvent(new Event("zhiyi:statistics-changed")); onSaved(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "统计保存失败，请重试。"); }
    finally { setBusy(false); }
  }
  const metricKey = preview?.analysis?.metric_keys?.[0];
  const chartData = (preview?.analysis?.data ?? []).map(row => ({ label: String(row.label ?? "全部"), value: metricKey && typeof row[metricKey] === "number" ? row[metricKey] as number : null }));
  const missing = (value: string | null, available: {key: string}[]) => value && !available.some(item => item.key === value) ? <option value={value}>原字段已删除 · 请选择</option> : null;
  return <dialog ref={dialog} className="statistic-dialog" aria-labelledby={`${uid}-title`} onCancel={event => { event.preventDefault(); if (!busy) onClose(); }} onClick={event => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <form onSubmit={event => { event.preventDefault(); void save(); }}>
      <div className="statistic-dialog-heading"><div><span className="statistic-kicker">我的统计</span><h2 id={`${uid}-title`}>{existing ? "修改统计" : "添加统计"}</h2><p>选择一张表，把常看的数据留在仪表盘。</p></div><button type="button" className="icon-button" aria-label="关闭统计设置" disabled={busy} onClick={onClose}><X size={18} /></button></div>
      {optionsError && <div className="callout danger" role="alert">{optionsError}<button type="button" className="btn secondary xs" onClick={() => setOptionsReload(value => value + 1)}>重新读取数据表</button></div>}
      {optionsLoaded && !tables.length && !optionsError ? <div className="statistic-no-tables"><strong>先准备一张数据表</strong><p>提取文件或在数据仓库创建、导入数据表后，就可以添加统计。</p></div> : <div className="statistic-editor-grid">
        <fieldset disabled={busy || !optionsLoaded} className="statistic-fields">
          <label htmlFor={`${uid}-table`}>数据表<select id={`${uid}-table`} className="form-select" value={draft.table_id} onChange={event => setDraft(value => ({ ...value, table_id: event.target.value, metric: "count", metric_field: null, group_field: null, time_bucket: null, date_field: null, time_range: "all", display: "auto" }))}>{!draft.table_id && <option value="">选择数据表</option>}{draft.table_id && !table && <option value={draft.table_id}>原数据表已删除 · 请选择</option>}{tables.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          <div className="statistic-field-pair"><label htmlFor={`${uid}-metric`}>统计什么<select id={`${uid}-metric`} className="form-select" value={draft.metric} onChange={event => setDraft(value => ({ ...value, metric: event.target.value as StatisticInput["metric"], display: "auto", metric_field: columns.find(column => column.value_type === "number")?.key ?? null }))}><option value="count">记录数</option><option value="sum" disabled={!columns.some(column => column.value_type === "number")}>数值合计</option><option value="avg" disabled={!columns.some(column => column.value_type === "number")}>数值平均</option></select></label>{draft.metric !== "count" && <label htmlFor={`${uid}-field`}>数值字段<select id={`${uid}-field`} className="form-select" value={draft.metric_field ?? ""} onChange={event => setDraft(value => ({ ...value, metric_field: event.target.value }))}><option value="" disabled>选择字段</option>{missing(draft.metric_field, columns.filter(item => item.value_type === "number"))}{columns.filter(item => item.value_type === "number").map(column => <option key={column.key} value={column.key}>{column.label}</option>)}</select></label>}</div>
          <div className="statistic-field-pair"><label htmlFor={`${uid}-group`}>按什么分组<select id={`${uid}-group`} className="form-select" value={draft.group_field ?? ""} onChange={event => { const selected = columns.find(column => column.key === event.target.value); setDraft(value => ({ ...value, group_field: event.target.value || null, time_bucket: selected?.value_type === "date" ? "month" : null, date_field: selected?.value_type === "date" ? selected.key : value.date_field, display: "auto" })); }}><option value="">不分组</option>{missing(draft.group_field, columns)}{columns.map(column => <option key={column.key} value={column.key}>{column.label}</option>)}</select></label>{group?.value_type === "date" && <label htmlFor={`${uid}-bucket`}>日期粒度<select id={`${uid}-bucket`} className="form-select" value={draft.time_bucket ?? "month"} onChange={event => setDraft(value => ({ ...value, time_bucket: event.target.value as "day" | "month" }))}><option value="month">按月</option><option value="day">按日</option></select></label>}</div>
          <label htmlFor={`${uid}-date`}>日期范围 <span className="statistic-optional">可选</span><select id={`${uid}-date`} className="form-select" value={draft.date_field ?? ""} onChange={event => setDraft(value => ({ ...value, date_field: event.target.value || null, time_range: event.target.value ? "dashboard" : "all" }))}><option value="">全部数据</option>{missing(draft.date_field, columns.filter(item => item.value_type === "date"))}{columns.filter(item => item.value_type === "date").map(column => <option key={column.key} value={column.key}>{column.label}</option>)}</select></label>
          {draft.date_field && <div className="statistic-radio-row"><label><input type="radio" name={`${uid}-range`} checked={draft.time_range === "dashboard"} onChange={() => setDraft(value => ({ ...value, time_range: "dashboard" }))} />跟随仪表盘</label><label><input type="radio" name={`${uid}-range`} checked={draft.time_range === "all"} onChange={() => setDraft(value => ({ ...value, time_range: "all" }))} />全部日期</label></div>}
          <div className="statistic-field-pair"><label htmlFor={`${uid}-display`}>展示方式<select id={`${uid}-display`} className="form-select" value={draft.display} onChange={event => setDraft(value => ({ ...value, display: event.target.value as StatisticInput["display"] }))}><option value="auto">自动推荐</option>{!draft.group_field && <option value="number">数字</option>}{draft.group_field && <><option value="bar">条形图</option>{draft.metric !== "avg" && <option value="donut">环形图</option>}{group?.value_type === "date" && <option value="line">趋势图</option>}</>}</select></label></div>
          <label htmlFor={`${uid}-name`}>名称<input id={`${uid}-name`} className="form-input" maxLength={80} value={named ? draft.name : suggestedName} onChange={event => { setNamed(true); setDraft(value => ({ ...value, name: event.target.value })); }} /></label>
        </fieldset>
        <section className="statistic-preview" aria-label="统计预览" aria-busy={loading}><div className="statistic-preview-heading"><span>预览</span>{loading && <Loader2 size={15} className="spin" />}</div><h3>{input.name || "统计名称"}</h3><p>{table?.name ?? "选择数据表"}{input.time_range === "dashboard" ? ` · ${days === 1 ? "今日" : `近 ${days} 天`}` : " · 全部数据"}</p>
          {preview?.status === "invalid" ? <div className="dashboard-card-error" role="status">{preview.message || "请调整统计条件。"}</div> : preview?.status === "empty" ? <div className="dashboard-chart-empty"><span>暂无符合条件的记录</span><small>可以先保存，之后会随数据更新。</small></div> : preview ? <DashboardChart data={chartData} type={preview.display} label={metricKey ? preview.analysis?.metric_labels?.[metricKey] ?? "数值" : "数值"} showTable={false} /> : <div className="dashboard-chart-empty">{loading ? "正在生成预览…" : "选择统计条件后查看结果"}</div>}
          {!!preview?.analysis?.warnings?.length && <p className="statistic-note">{preview.analysis.warnings.join("；")}</p>}
          <p className="statistic-preview-note">保存统计方式，随当前数据更新。不会修改原表。</p>
        </section>
      </div>}
      {error && <div className="callout danger" role="alert">{error}{!preview && <button type="button" className="btn secondary xs" onClick={() => setPreviewReload(value => value + 1)}>重新预览</button>}</div>}
      <div className="statistic-dialog-footer"><button type="button" className="btn secondary" disabled={busy} onClick={onClose}>取消</button><button type="submit" className="btn primary" disabled={busy || loading || !ready || previewKey !== key || !preview || preview.status === "invalid" || Boolean(optionsError)}>{busy && <Loader2 size={14} className="spin" />}保存统计</button></div>
    </form>
  </dialog>;
}
