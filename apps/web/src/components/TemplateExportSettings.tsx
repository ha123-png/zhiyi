import { forwardRef, useEffect, useImperativeHandle, useState } from "react";
import { getLocalExportBinding, saveLocalExportBinding, type LocalExportBinding } from "../api";
import { desktopApi } from "../desktop";

export interface TemplateExportHandle { save: () => Promise<void>; isDirty: () => boolean }

export const TemplateExportSettings = forwardRef<TemplateExportHandle, { templateId: string; disabled?: boolean }>(function TemplateExportSettings({ templateId, disabled = false }, ref) {
  const [binding, setBinding] = useState<LocalExportBinding | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [manualPath, setManualPath] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    getLocalExportBinding(templateId).then((value) => {
      if (!active) return;
      setBinding(value); setEnabled(value.enabled); setPath(value.parent_path ?? "");
    }).catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : "导出设置读取失败。"); });
    return () => { active = false; };
  }, [templateId]);

  async function save() {
    // Controls cannot be edited before the binding loads. Saving independent
    // template fields must not fail because an untouched local setting is slow.
    if (!binding) return;
    if (enabled && !path.trim()) throw new Error("请先选择副本目录，或关闭自动导出。");
    if (enabled === binding.enabled && path === (binding.parent_path ?? "")) return;
    setBusy(true); setError("");
    try {
      const saved = await saveLocalExportBinding(templateId, { expected_revision: binding.revision, enabled, parent_path: path || null });
      setBinding(saved); setPath(saved.parent_path ?? "");

    } catch (reason) { setError(reason instanceof Error ? reason.message : "导出设置保存失败。"); throw reason; }
    finally { setBusy(false); }
  }

  async function pick() {
    try {
      const selected = await desktopApi()?.choose_folder?.();
      if (selected) { setPath(selected); }
    } catch { setError("文件夹选择未能打开，请重试。"); }
  }

  useImperativeHandle(ref, () => ({ save, isDirty: () => Boolean(binding && (enabled !== binding.enabled || path !== (binding.parent_path ?? ""))) }));
  return <div className="form-field template-export-settings template-feature">
    <label className="form-label"><input type="checkbox" checked={enabled} disabled={!binding || busy || disabled}
      onChange={(event) => setEnabled(event.target.checked)} /> 自动导出原件副本</label>
    <p className="support">额外复制到“所选位置 / 模板名”。内部原件仍由知意保存。</p>
    {enabled && <div className="template-folder-choice">
      {desktopApi()?.choose_folder ? <button type="button" className="btn secondary sm" disabled={busy || disabled} onClick={() => void pick()}>{path ? "更换文件夹" : "选择文件夹"}</button>
        : <button type="button" className="btn secondary sm" disabled={busy || disabled} onClick={() => setManualPath(!manualPath)}>设置服务端目录</button>}
      {path && <><span className="source-value">{path}</span><button type="button" className="btn ghost sm" disabled={busy || disabled} onClick={() => setPath("")}>清除</button></>}
      {!desktopApi()?.choose_folder && manualPath && <label className="form-label">知意服务所在电脑的绝对路径
        <input className="form-input" value={path} disabled={busy || disabled} onChange={(event) => setPath(event.target.value)} />
      </label>}
      {!path && <p className="support">请先选择目录，再保存模板。</p>}
    </div>}
    <p className="support">不覆盖同名文件。导出副本由你管理，清除知意数据不会删除它。本机路径不随模板分享。</p>
    {error && <p role="alert">{error}</p>}
  </div>;
});
