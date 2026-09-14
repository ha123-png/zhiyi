import { memo, useEffect, useId, useRef, useState } from "react";
import { useReducedMotion } from "motion/react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Fold } from "../assistant/Interaction";

export const formatNumber = (value: number | null | undefined) => value == null ? "—" : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value);
export function AnimatedNumber({ value }: { value: number | null | undefined }) {
  const reduced = useReducedMotion();
  const [display, setDisplay] = useState(value ?? 0);
  const previous = useRef(value ?? 0);
  useEffect(() => {
    if (value == null) return;
    if (reduced) { previous.current = value; setDisplay(value); return; }
    const from = previous.current;
    const start = performance.now();
    let frame = 0;
    const tick = (now: number) => {
      const progress = Math.min(1, (now - start) / 420);
      const next = from + (value - from) * (1 - Math.pow(1 - progress, 3));
      previous.current = next;
      setDisplay(next);
      if (progress < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [value, reduced]);
  return <><span aria-hidden="true">{formatNumber(value == null ? null : Number.isInteger(value) ? Math.round(display) : display)}</span><span className="visually-hidden">{formatNumber(value)}</span></>;
}

export interface ChartDatum { label: string; value: number | null }
const palette = ["var(--dashboard-chart-1, var(--foreground))", "var(--dashboard-chart-2, var(--muted-foreground))", "var(--dashboard-chart-3, var(--brand-400))", "var(--dashboard-chart-4, var(--brand-500))", "var(--dashboard-chart-5, var(--brand-300))"];
const shortLabel = (value: unknown) => String(value).length > 12 ? `${String(value).slice(0, 11)}…` : String(value);

export const DashboardChart = memo(function DashboardChart({ data, type = "line", label, unit = "", height = 226, showTable = true }: {
  data: ChartDatum[];
  type?: "line" | "bar" | "donut" | "number";
  label: string;
  unit?: string;
  height?: number;
  showTable?: boolean;
}) {
  const reduced = useReducedMotion();
  const uid = useId().replaceAll(":", "");
  const [focused, setFocused] = useState<ChartDatum | null>(null);
  useEffect(() => setFocused(null), [data]);
  const animation = { isAnimationActive: !reduced, animationDuration: 460, animationEasing: "ease-out" as const, animationBegin: 0 };
  const plotData = type === "bar" || (type === "donut" && data.length > 8) ? data.slice(0, 8) : data;
  const onHover = (state: { activeTooltipIndex?: number | string | null }) => {
    const index = Number(state.activeTooltipIndex);
    setFocused(state.activeTooltipIndex == null ? null : plotData[index] ?? null);
  };
  const total = data.reduce((sum, row) => sum + (row.value ?? 0), 0);
  const donut = type === "donut" && data.length > 1 && data.length <= 8 && data.every(row => row.value != null && row.value >= 0) && total > 0;
  const resolved = type === "donut" && !donut ? "bar" : type;
  const table = (showTable || plotData.length < data.length) && <Fold className="dashboard-data-details" title="查看数据"><div className="dashboard-data-scroll"><table><caption className="visually-hidden">{label}</caption><thead><tr><th>项目</th><th>{unit || "数值"}</th></tr></thead><tbody>{data.map((row, index) => <tr key={`${row.label}-${index}`}><td>{row.label}</td><td>{formatNumber(row.value)}</td></tr>)}</tbody></table></div></Fold>;
  if (!data.length) return <div className="dashboard-chart-empty" style={{ minHeight: height }}><span>暂无数据</span><small>符合条件的数据出现后会显示在这里。</small></div>;
  if (type === "number" || (type === "donut" && data.length === 1)) return <div className="dashboard-single-number" style={{ minHeight: height }}><strong><AnimatedNumber value={data[0]?.value} />{unit && <small>{unit}</small>}</strong><span>{data[0]?.label === "全部" ? unit || label : data[0]?.label}</span></div>;
  return <div className="dashboard-chart-component">
    <div className="dashboard-chart-readout" aria-hidden="true"><span title={focused?.label}>{focused?.label ?? label}</span><strong>{focused ? `${formatNumber(focused.value)}${unit ? ` ${unit}` : ""}` : ""}</strong></div>
    {donut ? <div className="dashboard-donut-layout">
      <div className="dashboard-donut" style={{ height }}>
        <ResponsiveContainer width="100%" height="100%"><PieChart aria-label={`${label}占比图`}><Pie data={data} nameKey="label" dataKey="value" innerRadius="66%" outerRadius="90%" paddingAngle={data.length > 1 ? 3 : 0} stroke="var(--card)" strokeWidth={2} onMouseEnter={(_, i) => setFocused(data[i])} onMouseLeave={() => setFocused(null)} {...animation}>{data.map((row, i) => <Cell key={row.label} fill={palette[i % palette.length]} opacity={focused && focused !== row ? .45 : 1} />)}</Pie></PieChart></ResponsiveContainer>
        <div className="dashboard-donut-center"><strong>{formatNumber(total)}</strong><span>{unit || "合计"}</span></div>
      </div>
      <div className="dashboard-chart-legend">{data.map((row, i) => <button type="button" key={row.label} onMouseEnter={() => setFocused(row)} onMouseLeave={() => setFocused(null)} onFocus={() => setFocused(row)} onBlur={() => setFocused(null)} aria-label={`${row.label}，${formatNumber(row.value)}${unit}`}><i style={{ background: palette[i % palette.length] }} /><span title={row.label}>{row.label}</span><strong>{formatNumber(row.value)}</strong></button>)}</div>
    </div> : <div className="dashboard-plot" style={{ height: resolved === "bar" ? Math.max(height, plotData.length * 33) : height }}>
      <ResponsiveContainer width="100%" height="100%">
        {resolved === "bar" ? <BarChart aria-label={`${label}条形图`} data={plotData} layout="vertical" margin={{ top: 8, right: 16, left: 0, bottom: 4 }} onMouseMove={onHover} onMouseLeave={() => setFocused(null)} accessibilityLayer>
          <CartesianGrid horizontal={false} stroke="var(--border)" strokeDasharray="3 5" /><XAxis type="number" axisLine={false} tickLine={false} tick={{ fill: "var(--muted-foreground)", fontSize: 11 }} tickFormatter={formatNumber} /><YAxis type="category" dataKey="label" width={100} axisLine={false} tickLine={false} tickFormatter={shortLabel} tick={{ fill: "var(--muted-foreground)", fontSize: 12 }} /><Tooltip content={() => null} cursor={false} /><Bar dataKey="value" fill="var(--foreground)" radius={[0, 4, 4, 0]} barSize={14} {...animation} />
        </BarChart> : <AreaChart aria-label={`${label}趋势图`} data={data} margin={{ top: 12, right: 14, bottom: 4, left: -12 }} onMouseMove={onHover} onMouseLeave={() => setFocused(null)} accessibilityLayer>
          <defs><linearGradient id={uid} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="var(--foreground)" stopOpacity={.14} /><stop offset="100%" stopColor="var(--foreground)" stopOpacity={0} /></linearGradient></defs>
          <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="3 5" /><XAxis dataKey="label" axisLine={false} tickLine={false} minTickGap={30} tickFormatter={value => /^\d{4}-\d{2}-\d{2}$/.test(String(value)) ? String(value).slice(5) : shortLabel(value)} tick={{ fill: "var(--muted-foreground)", fontSize: 11 }} /><YAxis axisLine={false} tickLine={false} allowDecimals={!data.every(row => row.value == null || Number.isInteger(row.value))} tickFormatter={formatNumber} tick={{ fill: "var(--muted-foreground)", fontSize: 11 }} /><Tooltip content={() => null} cursor={false} /><Area type="monotone" dataKey="value" stroke="var(--foreground)" fill={`url(#${uid})`} strokeWidth={2} dot={data.length === 1 ? { r: 4 } : false} activeDot={{ r: 4, fill: "var(--foreground)", stroke: "var(--card)", strokeWidth: 2 }} {...animation} />
        </AreaChart>}
      </ResponsiveContainer>
    </div>}
    {plotData.length < data.length && <p className="statistic-note">图中展示前 {plotData.length} 项，完整 {data.length} 项可在数据明细中查看。</p>}
    {table}
  </div>;
}, (previous, next) => previous.type === next.type && previous.label === next.label && previous.unit === next.unit && previous.height === next.height && previous.showTable === next.showTable && previous.data.length === next.data.length && previous.data.every((row, index) => row.label === next.data[index].label && row.value === next.data[index].value));
