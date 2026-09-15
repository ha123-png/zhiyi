import { forwardRef, useEffect, useImperativeHandle, useState } from "react";
import { getLocalExportBinding, saveLocalExportBinding, type LocalExportBinding } from "../api";
import { desktopApi } from "../desktop";

export interface TemplateExportHandle { save: () => Promise<void>; isDirty: () => boolean }

export const TemplateExportSettings = forwardRef<TemplateExportHandle, { templateId: string; disabled?: boolean }>(function TemplateExportSettings({ templateId, disabled = false }, ref) {
  const [binding, setBinding] = useState<LocalExportBinding | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [mode, setMode] = useState<"copy" | "move">("move");
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [manualPath, setManualPath] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    getLocalExportBinding(templateId).then((value) => {
      if (!active) return;
      setBinding(value); setEnabled(value.enabled); setMode(value.enabled ? value.mode ?? "copy" : "move"); setPath(value.parent_path ?? "");
    }).catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : "导出设置读取失败。"); });
    return () => { active = false; };
  }, [templateId]);

  async function save() {
    // Controls cannot be edited before the binding loads. Saving independent
    // template fields must not fail because an untouched local setting is slow.
    if (!binding) return;
    if (enabled && !path.trim()) throw new Error("请先选择文件夹，或关闭原文件处理。");
    if (enabled === binding.enabled && (!enabled || mode === (binding.mode ?? "copy")) && path === (binding.parent_path ?? "")) return;
    setBusy(true); setError("");
    try {
      const saved = await saveLocalExportBinding(templateId, { expected_revision: binding.revision, enabled, mode, parent_path: path || null });
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

  useImperativeHandle(ref, () => ({ save, isDirty: () => Boolean(binding && (enabled !== binding.enabled || (enabled && mode !== (binding.mode ?? "copy")) || path !== (binding.parent_path ?? ""))) }));
  return <div className="form-field template-export-settings template-feature">
    <label className="form-label" htmlFor={`original-action-${templateId}`}>原文件处理</label>
    <select className="form-input" id={`original-action-${templateId}`} value={enabled ? mode : "disabled"} disabled={!binding || busy || disabled}
      onChange={event => { setEnabled(event.target.value !== "disabled"); if (event.target.value !== "disabled") setMode(event.target.value as "copy" | "move"); }}>
      <option value="disabled">不额外处理</option><option value="move">原文件归档</option><option value="copy">复制原件副本</option>
    </select>
    {enabled && <p className="support">{mode === "move" ? "结果保存成功后，将导入的原文件移动到“所选位置 / 模板名”。知意保留内部原件用于预览；需在桌面版通过“选择文件”导入。" : "额外复制到“所选位置 / 模板名”，原位置的文件保留。内部原件仍由知意保存。"}</p>}
    {enabled && <div className="template-folder-choice">
      {desktopApi()?.choose_folder ? <button type="button" className="btn secondary sm" disabled={busy || disabled} onClick={() => void pick()}>{path ? "更换文件夹" : "选择文件夹"}</button>
        : <button type="button" className="btn secondary sm" disabled={busy || disabled} onClick={() => setManualPath(!manualPath)}>设置服务端目录</button>}
      {path && <><span className="source-value">{path}</span><button type="button" className="btn ghost sm" disabled={busy || disabled} onClick={() => setPath("")}>清除</button></>}
      {!desktopApi()?.choose_folder && manualPath && <label className="form-label">知意服务所在电脑的绝对路径
        <input className="form-input" value={path} disabled={busy || disabled} onChange={(event) => setPath(event.target.value)} />
      </label>}
      {!path && <p className="support">请先选择目录，再保存模板。</p>}
    </div>}
    {enabled && <p className="support">同名或权限问题进入待处理事项，不覆盖文件。归档与副本由你管理，清除知意数据不会删除它们。本机路径不随模板分享。</p>}
    {error && <p role="alert">{error}</p>}
  </div>;
});
