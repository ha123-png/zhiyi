import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { getTemplates, updateTemplateSmartPool } from "../api";
import type { ExtractionTemplate } from "../types";
import { Icon } from "./Icon";
import "./smart-pool.css";
import { CheckboxOption } from "./CheckboxOption";

/** Only selected templates can match future uploads; existing tasks keep their snapshot. */
export function SmartPoolSettings({ onUpdated, onBusyChange }: {
  onUpdated?: (templates: ExtractionTemplate[]) => void;
  onBusyChange?: (busy: boolean) => void;
} = {}) {
  const [templates, setTemplates] = useState<ExtractionTemplate[]>([]);
  const [busyIds, setBusyIds] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const templatesRef = useRef<ExtractionTemplate[]>([]);
  const savingIdsRef = useRef(new Set<string>());
  const requestSeqRef = useRef(new Map<string, number>());
  const mountedRef = useRef(false);
  const onUpdatedRef = useRef(onUpdated);
  onUpdatedRef.current = onUpdated;

  useEffect(() => { onBusyChange?.(busyIds.size > 0); }, [busyIds, onBusyChange]);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  useEffect(() => {
    let current = true;
    setLoading(true);
    setLoadError(null);
    getTemplates(true)
      .then((items) => {
        if (!current) return;
        const active = items.filter((t) => t.is_active !== false);
        templatesRef.current = active;
        setTemplates(active);
        onUpdatedRef.current?.(active);
      })
      .catch((reason) => {
        if (current) setLoadError(reason instanceof Error ? reason.message : "模板读取失败。");
      })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [reload]);

  async function toggle(template: ExtractionTemplate, checked: boolean) {
    if (savingIdsRef.current.has(template.id)) return;
    savingIdsRef.current.add(template.id);
    const seq = (requestSeqRef.current.get(template.id) ?? 0) + 1;
    requestSeqRef.current.set(template.id, seq);
    setBusyIds((current) => new Set(current).add(template.id));
    setError(null);
    try {
      const updated = await updateTemplateSmartPool(template.id, checked);
      if (!mountedRef.current || requestSeqRef.current.get(template.id) !== seq) return;
      const next = templatesRef.current.map((item) => (item.id === updated.id ? updated : item));
      templatesRef.current = next;
      setTemplates(next);
      onUpdatedRef.current?.(next);
    } catch (reason) {
      if (mountedRef.current && requestSeqRef.current.get(template.id) === seq) {
        setError(reason instanceof Error ? reason.message : "保存匹配范围失败，请重试。");
      }
    } finally {
      savingIdsRef.current.delete(template.id);
      if (mountedRef.current && requestSeqRef.current.get(template.id) === seq) {
        setBusyIds((current) => {
          const next = new Set(current);
          next.delete(template.id);
          return next;
        });
      }
    }
  }

  const ordinary = templates.filter(template => template.engine_kind !== "autonomous");
  const fallback = templates.filter(template => template.engine_kind === "autonomous");
  function choice(template: ExtractionTemplate) {
    const isFallback = template.engine_kind === "autonomous";
    const busy = busyIds.has(template.id);
    return <CheckboxOption key={template.id} checked={template.in_smart_pool !== false} busy={busy}
      onChange={checked => void toggle(template, checked)}
      title={<>{template.name}{template.is_system && <span className="tpl-badge">内置</span>}</>}
      description={isFallback ? "普通模板都不适用时，根据文件内容提取字段。" : template.description || "按此模板的字段和规则提取"}
      accessory={busy && <span role="status" aria-label={`正在保存${template.name}`}><Icon icon={Loader2} size={14} className="spin" /></span>} />;
  }

  return (
    <div className="smart-pool-settings">
      <p className="smart-pool-help">只在勾选的模板中自动选择。更改即时保存，仅用于之后上传的文件。</p>
      {error && <div className="callout danger" role="alert">{error}</div>}
      {loading ? <div className="support" role="status">正在读取模板…</div>
        : loadError ? <div className="callout danger" role="alert">{loadError}<button className="btn secondary sm" type="button" onClick={() => setReload(value => value + 1)}>重新读取模板</button></div>
        : templates.length === 0 ? <p className="support">暂无可用模板，请在模板页新建或启用模板。</p>
        : <div className="smart-pool-list" role="group" aria-label="智能匹配预选池">
          {ordinary.length > 0 && <div className="smart-pool-grid">{ordinary.map(choice)}</div>}
          {ordinary.length > 0 && !ordinary.some(template => template.in_smart_pool !== false) && <p className="smart-pool-help">尚未勾选普通模板。{fallback.some(template => template.in_smart_pool !== false) ? "新文件将使用自主提取。" : "请勾选模板，或开启下方自主提取兜底。"}</p>}
          {fallback.length > 0 && <div className="smart-pool-fallback"><span className="smart-pool-group-label">没有合适模板时</span>{fallback.map(choice)}</div>}
        </div>}
    </div>
  );
}
