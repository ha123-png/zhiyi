import { useEffect, useState } from "react";
import { Loader2, ListChecks } from "lucide-react";
import { getTemplates, updateTemplateSmartPool } from "../api";
import type { ExtractionTemplate } from "../types";
import { Icon } from "./Icon";

/** 智能匹配预选池：只有勾选的模板才会被智能匹配选中（设置 → 左侧）。 */
export function SmartPoolSettings() {
  const [templates, setTemplates] = useState<ExtractionTemplate[]>([]);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getTemplates(true)
      .then((items) => setTemplates(items.filter((t) => t.is_active !== false)))
      .catch((reason) => setError(reason instanceof Error ? reason.message : "模板读取失败。"));
  }, []);

  async function toggle(template: ExtractionTemplate, checked: boolean) {
    setBusyId(template.id);
    setError(null);
    try {
      const updated = await updateTemplateSmartPool(template.id, checked);
      setTemplates((current) =>
        current.map((item) => (item.id === updated.id ? updated : item)),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存预选池失败。");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="smart-pool-settings">
      <div className="callout" style={{ marginBottom: 12 }}>
        <Icon icon={ListChecks} size={14} />
        <span>
          智能匹配只会从勾选的模板里选择。建议每类单据只对应一个模板，模板之间的用途描述不要重叠混淆，否则可能匹配到错误的模板。
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
                disabled={busyId === template.id}
                onChange={(event) => void toggle(template, event.target.checked)}
              />
              <span className="smart-pool-name">
                {template.name}
                {template.is_system ? <span className="tpl-badge">内置</span> : null}
              </span>
              <span className="small muted smart-pool-desc">
                {template.description || "尚未填写用途"}
              </span>
              {busyId === template.id && (
                <Icon icon={Loader2} size={13} className="spin" />
              )}
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
