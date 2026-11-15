import { History, ScanSearch, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ExtractionTemplate } from "../types";
import { trapModalTab } from "../useModalFocus";
import { Icon } from "./Icon";
import { SmartPoolSettings } from "./SmartPoolSettings";
import "./extract-configuration.css";

interface Props {
  templates: ExtractionTemplate[];
  automatic: boolean;
  selectedTemplateId: string;
  onSelect: (value: string) => void;
  onUpdated: (templates: ExtractionTemplate[]) => void;
  onNavigateHistory?: () => void;
  templateReadState?: "ready" | "loading" | "error";
}

export function ExtractConfiguration({ templates, automatic, selectedTemplateId, onSelect, onUpdated, onNavigateHistory, templateReadState = "ready" }: Props) {
  const [scopeOpen, setScopeOpen] = useState(false);
  const enabled = templates.filter(template => template.is_active !== false && template.in_smart_pool !== false);
  const ordinary = enabled.filter(template => template.engine_kind !== "autonomous");
  const fallback = enabled.some(template => template.engine_kind === "autonomous");
  const selected = templates.find(template => template.id === selectedTemplateId);
  return <section className="extract-template-config" aria-label="提取方式">
    <div className="extract-config-heading">
      <div className="extract-template-select">
        <label className="form-label" htmlFor="extract-template">提取模板</label>
        <div className="extract-matching-controls"><div className="extract-matching-row">
        <select id="extract-template" className="form-select" value={automatic ? "smart" : selectedTemplateId}
          onChange={event => onSelect(event.target.value)}>
          <option value="smart">自动选择合适的模板</option>
          {!automatic && !selectedTemplateId && <option value="" disabled>请选择模板…</option>}
          {templates.map(template => <option key={template.id} value={template.id}>{template.name}</option>)}
        </select>
        {automatic && <button className="btn secondary extract-scope-button" type="button" aria-haspopup="dialog"
          aria-expanded={scopeOpen} onClick={() => setScopeOpen(true)}>
          <Icon icon={ScanSearch} size={16} />匹配范围
        </button>}
        {automatic && <span className="extract-scope-description" role={templateReadState === "loading" ? "status" : undefined}>
          {templateReadState === "loading" ? "正在读取模板…" : templateReadState === "error" ? "匹配范围待确认" : ordinary.length ? `在 ${ordinary.length} 个模板中匹配` : fallback ? "使用自主提取" : "尚未选择普通模板"}
        </span>}
        </div></div>
      </div>
      {onNavigateHistory && <button className="btn ghost sm" type="button" onClick={onNavigateHistory}>
        <Icon icon={History} size={16} />文件历史
      </button>}
    </div>
    {!automatic && <p className="extract-template-description">
      {selected?.description || "新上传的文件将使用所选模板提取。"}
    </p>}
    {scopeOpen && automatic && <MatchingScopeDialog onUpdated={onUpdated} onClose={() => setScopeOpen(false)} />}
  </section>;
}

function MatchingScopeDialog({ onUpdated, onClose }: Pick<Props, "onUpdated"> & { onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const element = dialog.current;
    element?.showModal();
    return () => {
      element?.close();
      if (previous?.isConnected) previous.focus();
    };
  }, []);

  return <dialog ref={dialog} className="template-history-dialog extract-scope-dialog" aria-labelledby="matching-scope-title"
    tabIndex={-1} onKeyDown={trapModalTab}
    aria-describedby="matching-scope-description" onCancel={event => { event.preventDefault(); if (!saving) onClose(); }}>
    <div className="template-history-head">
      <div><h3 id="matching-scope-title"><Icon icon={ScanSearch} size={18} />匹配范围</h3>
        <p id="matching-scope-description" className="support">选择自动匹配时可以使用的模板。</p></div>
      <button className="btn ghost icon-only sm" type="button" aria-label="关闭匹配范围" disabled={saving} onClick={onClose}>
        <Icon icon={X} size={18} />
      </button>
    </div>
    <div className="extract-scope-body"><SmartPoolSettings onUpdated={onUpdated} onBusyChange={setSaving} /></div>
    <div className="extract-scope-footer">
      <span className="support" role="status">{saving ? "正在保存，请稍候…" : "更改即时保存，已有文件不受影响。"}</span>
      <button className="btn primary" type="button" disabled={saving} onClick={onClose}>完成</button>
    </div>
  </dialog>;
}
