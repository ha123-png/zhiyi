import { useEffect, useRef, useState } from "react";
import { X, History, RotateCcw } from "lucide-react";
import { getTemplateVersions, getTemplateVersion, restoreTemplateVersion, type TemplateVersionSummary, getTemplateRestorations, type TemplateRestoration } from "../api";
import type { ExtractionTemplate } from "../types";
import { templateDifferences } from "../templateHistory";
import { Icon } from "./Icon";

export function TemplateHistory({ current, dirty, onClose, onRestored }: {
  current: ExtractionTemplate; dirty: boolean; onClose: () => void; onRestored: (template: ExtractionTemplate) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [versions, setVersions] = useState<TemplateVersionSummary[]>([]);
  const [restorations, setRestorations] = useState<TemplateRestoration[]>([]);
  const [restorationError, setRestorationError] = useState("");
  const [selected, setSelected] = useState(current.version);
  const [target, setTarget] = useState<ExtractionTemplate | null>(current);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [more, setMore] = useState(false);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    let active = true;
    getTemplateVersions(current.id).then(items => { if (active) { setVersions(items); setMore(items.length === 50); } })
      .catch(e => { if (active) setError(e.message); }).finally(() => { if (active) setLoading(false); });
    getTemplateRestorations(current.id).then(items => { if (active) setRestorations(items); })
      .catch(() => { if (active) setRestorationError("恢复记录读取失败，请关闭后重试。"); });
    return () => { active = false; previous?.focus(); };
  }, [current.id]);
  useEffect(() => {
    let active = true;
    setTarget(null); setError("");
    getTemplateVersion(current.id, selected).then(value => { if (active) setTarget(value); })
      .catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [current.id, selected]);
  async function loadMore() {
    setLoading(true); setError("");
    try { const items = await getTemplateVersions(current.id, versions.at(-1)?.version); setVersions(v => [...v, ...items]); setMore(items.length === 50); }
    catch (e) { setError(e instanceof Error ? e.message : "历史读取失败。"); }
    finally { setLoading(false); }
  }
  async function restore() {
    setBusy(true); setError("");
    try { const restored = await restoreTemplateVersion(current.id, selected, current.version, current.updated_at); onRestored(restored); }
    catch (e) { setError(e instanceof Error ? e.message : "恢复失败，请重试。"); }
    finally { setBusy(false); }
  }
  const formatDate = (value: string) => new Date(/(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : `${value}Z`).toLocaleString("zh-CN");
  const differences = target ? templateDifferences(current, target) : [];
  return <dialog ref={dialog} className="template-history-dialog" aria-labelledby="template-history-title" onCancel={e => { e.preventDefault(); if (!busy) onClose(); }}>
    <div className="template-history-head"><div><h3 id="template-history-title"><Icon icon={History} size={18} /> 模板历史</h3><p className="support">{current.name} · 当前第 {current.version} 版</p></div><button className="btn ghost icon-only sm" aria-label="关闭模板历史" disabled={busy} onClick={onClose}><Icon icon={X} size={18} /></button></div>
    <div className="template-history-body">
      <nav className="template-version-list" aria-label="模板版本">{versions.map(v => <button key={v.version} disabled={busy} aria-pressed={selected === v.version} onClick={() => setSelected(v.version)}><strong>第 {v.version} 版{v.version === current.version ? " · 当前" : ""}</strong><span>{formatDate(v.created_at)}</span><span>{v.field_count} 个字段</span></button>)}{loading && <p className="support">读取中…</p>}{more && <button disabled={loading || busy} onClick={() => void loadMore()}>更早的版本</button>}</nav>
      <div className="template-version-detail" aria-live="polite">
        <details className="template-restoration-log"><summary>最近恢复记录</summary>{restorationError ? <p role="alert">{restorationError}</p> : restorations.length ? <><p className="support">最近 20 次版本切换，不创建重复版本。</p><ul>{restorations.map(item => <li key={item.id}>从第 {item.from_version} 版恢复到第 {item.to_version} 版<time>{formatDate(item.created_at)}</time></li>)}</ul></> : <p className="support">还没有恢复记录。</p>}</details>
        {error && <p role="alert" className="callout danger">{error}</p>}
        {!target && !error && <p className="support">正在读取版本…</p>}
        {target && <><h4>{selected === current.version ? "当前版本内容" : `恢复第 ${selected} 版会带来这些变化`}</h4>
          {selected === current.version || !differences.length ? <><p className="support">{selected === current.version ? "选择其他版本，查看它与当前内容的差异。" : "可见设置与当前版本一致。"}</p><ol className="version-field-order">{target.fields.map(f => <li key={`${f.section}:${f.key}`}>{f.label}<span>{f.section === "header" ? "整份文件" : "每条明细"} · {f.value_type}</span></li>)}</ol></> : differences.map(d => <section className="template-difference" key={d.label}><h5>{d.label}{d.structural && <span className="badge muted">影响今后提取结构</span>}</h5><div className="template-difference-values"><div><span>当前</span><pre>{d.before}</pre></div><div><span>恢复后</span><pre>{d.after}</pre></div></div></section>)}
        </>}
      </div>
    </div>
    <div className="template-history-foot"><p className="support">恢复会将选定版本设为当前，不新增版本。旧任务、已确认数据和外部副本保留；匹配字段的阅读顺序会更新，本机副本目录不变。</p>
      {dirty && <p className="support">模板有未保存编辑，请先保存后再恢复历史。</p>}
      {current.is_system && <p className="support">内置模板只读，修改请使用顶部复制入口。</p>}
      <button className="btn primary sm" disabled={dirty || busy || !target || selected === current.version || current.is_system || !current.is_active} onClick={() => void restore()}><Icon icon={RotateCcw} size={14} />{busy ? "正在恢复…" : "设为当前版本"}</button>
    </div>
  </dialog>;
}
