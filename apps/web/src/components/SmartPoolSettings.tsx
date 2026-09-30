import { useEffect, useRef, useState } from "react";
import { Loader2, ListChecks } from "lucide-react";
import { getTemplates, updateTemplateSmartPool } from "../api";
import type { ExtractionTemplate } from "../types";
import { Icon } from "./Icon";

/** 智能匹配预选池：只有勾选的模板才会被智能匹配选中（设置 → 左侧）。 */
export function SmartPoolSettings({ onUpdated }: { onUpdated?: (templates: ExtractionTemplate[]) => void } = {}) {
  const [templates, setTemplates] = useState<ExtractionTemplate[]>([]);
  const [busyIds, setBusyIds] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const templatesRef = useRef<ExtractionTemplate[]>([]);
  const requestSeqRef = useRef(new Map<string, number>());

  useEffect(() => {
    let current = true;
    getTemplates(true)
      .then((items) => {
        if (!current) return;
        const active = items.filter((t) => t.is_active !== false);
        templatesRef.current = active;
        setTemplates(active);
      })
      .catch((reason) => {
        if (current) setError(reason instanceof Error ? reason.message : "模板读取失败。");
      });
    return () => { current = false; };
  }, []);

  async function toggle(template: ExtractionTemplate, checked: boolean) {
    const seq = (requestSeqRef.current.get(template.id) ?? 0) + 1;
    requestSeqRef.current.set(template.id, seq);
    setBusyIds((current) => new Set(current).add(template.id));
    setError(null);
    try {
      const updated = await updateTemplateSmartPool(template.id, checked);
      if (requestSeqRef.current.get(template.id) !== seq) return;
      const next = templatesRef.current.map((item) => (item.id === updated.id ? updated : item));
      templatesRef.current = next;
      setTemplates(next);
      onUpdated?.(next);
    } catch (reason) {
      if (requestSeqRef.current.get(template.id) === seq) {
        setError(reason instanceof Error ? reason.message : "保存预选池失败。");
      }
    } finally {
      if (requestSeqRef.current.get(template.id) === seq) {
        setBusyIds((current) => {
          const next = new Set(current);
          next.delete(template.id);
          return next;
        });
      }
    }
  }

  return (
    <div className="smart-pool-settings">
      <div className="callout" style={{ marginBottom: 12 }}>
        <Icon icon={ListChecks} size={14} />
        <span>
          只在勾选的普通模板中匹配。自主提取默认不勾选；加入后，仅在普通模板都不适用时兜底。更改只用于之后上传的文件。
        </span>
      </div>
      {error && <div className="callout danger">{error}</div>}
      {templates.length === 0 ? (
        <div className="support">正在读取模板…</div>
      ) : (
        <div className="smart-pool-list" aria-label="智能匹配预选池">
          {templates.map((template) => (
            <label className="smart-pool-item" key={template.id}>
              <input
                type="checkbox"
                checked={template.in_smart_pool !== false}
                disabled={busyIds.has(template.id)}
                onChange={(event) => void toggle(template, event.target.checked)}
              />
              <span className="smart-pool-name">
                {template.name}
                {template.is_system ? <span className="tpl-badge">内置</span> : null}
              </span>
              <span className="small muted smart-pool-desc">
                {template.engine_kind === "autonomous" ? "可选兜底 · 普通模板均不适用时才使用" : template.description || "尚未填写用途"}
              </span>
              {busyIds.has(template.id) && (
                <Icon icon={Loader2} size={13} className="spin" />
              )}
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
