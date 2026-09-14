import { FileLocation } from "./FileLocation";
import { Disclosure } from "./Disclosure";
import { useEffect, useRef, useState } from "react";
import { actOnTaskExport } from "../api";
import { desktopApi } from "../desktop";
import type { Task } from "../types";

const labels = {
  disabled: "未开启", awaiting_confirmation: "等待结果确认", pending: "等待导出", exporting: "正在导出",
  completed: "副本已导出", failed: "副本导出受阻", skipped: "已跳过副本导出", needs_rebind: "需重新确认位置",
};

export function TaskExportDetails({ task, expanded = false, focused = false, onUpdated }: { task: Task; expanded?: boolean; focused?: boolean; onUpdated?: (task: Task) => void }) {
  const [result, setResult] = useState<Task | null>(null);
  const current = result ?? task;
  const state = current.file_export;
  const [filename, setFilename] = useState(task.file_export?.confirmed_name ?? task.filename);
  const [parent, setParent] = useState(task.file_export?.parent_path ?? "");
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!state || state.status === "disabled") return null;
  const actionable = state.status === "failed" || state.status === "needs_rebind";
  const uncertain = state.error_code === "publication_uncertain";
  const label = state.status === "awaiting_confirmation" && task.status === "completed" ? "等待导出" : labels[state.status];

  async function act(action: "retry" | "skip") {
    setBusy(true); setError("");
    try {
      const updated = await actOnTaskExport(task.id, action === "skip" ? { action } : {
        action, filename, parent_path: parent, acknowledge_uncertain: acknowledged,
      });
      setResult(updated); onUpdated?.(updated);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "副本操作失败，请重试。"); }
    finally { setBusy(false); }
  }

  async function chooseFolder() {
    try {
      const selected = await desktopApi()?.choose_folder?.();
      if (selected) setParent(selected);
    } catch { setError("无法打开文件夹选择器，请重试。"); }
  }

  async function openFolder() {
    setError("");
    try { await desktopApi()?.open_export_folder?.(task.id); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "原副本位置无法访问。内部原件预览不受影响，请检查目录是否移动或不可用。"); }
  }

  const details = <dl className="source-values" style={{ overflowWrap: "anywhere" }}>
      <dt>上传原名</dt><dd>{task.filename}</dd>
      {state.confirmed_name && <><dt>确认后的名称</dt><dd>{state.confirmed_name}</dd></>}
      {state.destination && <><dt>本次目标</dt><dd><FileLocation path={state.destination} /></dd></>}
      {state.actual_path && <><dt>实际导出位置</dt><dd><FileLocation path={state.actual_path} /></dd></>}
    </dl>;
  const content = <>
    {!focused && <><p className="support">知意保留完整内部原件用于预览和追溯；外部仅为额外副本。副本导出与提取结果的成功状态分开。</p>{details}</>}
    {state.error_message && <p className="source-value">{state.error_message}</p>}
    {actionable && <>
      <div className="form-field"><label className="form-label" htmlFor={`export-name-${task.id}`}>副本名称（保留扩展名）</label>
        <input className="form-input" id={`export-name-${task.id}`} value={filename} disabled={busy} onChange={(event) => setFilename(event.target.value)} /></div>
      <div className="form-field">
        {desktopApi()?.choose_folder ? <div className="template-folder-choice">
          <button type="button" className="btn secondary sm" disabled={busy} onClick={() => void chooseFolder()}>选择新路径</button>
          <span className="support" style={{ overflowWrap: "anywhere" }}>{parent || "尚未选择目录"}</span>
        </div> : <label className="form-label" htmlFor={`export-path-${task.id}`}>知意服务所在电脑的目标目录
          <input className="form-input" id={`export-path-${task.id}`} value={parent} disabled={busy} onChange={(event) => setParent(event.target.value)} />
        </label>}
      </div>
      <p className="support">仅调整此次副本；保留内部原件，不重新提取。</p>
      {uncertain && <label><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} /> 我已检查上次目标，确认重新导出可能额外产生一份副本。</label>}
      <div className="button-row">
        <button type="button" className="btn sm" disabled={busy || !filename.trim() || !parent.trim() || (uncertain && !acknowledged)} onClick={() => void act("retry")}>重试副本导出</button>
        <button type="button" className="btn ghost sm" disabled={busy} onClick={() => void act("skip")}>跳过此次副本导出</button>
      </div>
    </>}
    {state.status === "completed" && <p className="support">这是当时写出的副本位置。之后由你管理，外部移动、改名或删除不会影响内部预览，知意也不会重新生成副本。</p>}
    {state.status === "completed" && state.actual_path && desktopApi()?.open_export_folder && <button type="button" className="btn secondary sm" onClick={() => void openFolder()}>打开副本所在文件夹</button>}
    {error && <p role="alert">{error}</p>}
    {focused && <Disclosure title="原文件与位置"><p className="support">目标目录下会使用模板名文件夹，不修改模板的默认导出设置。</p>{details}</Disclosure>}
  </>;
  return focused ? <div className="task-export-form">{content}</div> : <Disclosure className="task-export-details" defaultOpen={expanded} title={`原件副本 · ${label}`}>{content}</Disclosure>;
}

export function TaskExportAction({ task, onUpdated }: { task: Task; onUpdated?: (task: Task) => void }) {
  const [open, setOpen] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { if (open) dialog.current?.showModal(); else dialog.current?.close(); }, [open]);
  const reason = ["destination_exists", "name_conflict"].includes(task.file_export?.error_code || "") ? "目标位置已有同名文件" : task.file_export?.error_message || "副本导出需要处理";
  return <>
    <div className="task-row-bottom task-export-action">
      <span className="task-reason" title={reason}>{reason}</span>
      <button type="button" className="btn secondary sm" onClick={() => setOpen(true)}>处理</button>
    </div>
    <dialog className="task-recovery-dialog" ref={dialog} aria-label="处理原件副本" onCancel={() => setOpen(false)} onClose={() => setOpen(false)} onClick={event => { if (event.target === event.currentTarget) setOpen(false); }}>
      {open && <div><header><h3>处理原件副本</h3><button type="button" className="btn ghost sm" aria-label="关闭副本处理" onClick={() => setOpen(false)}>关闭</button></header>
        <TaskExportDetails key={task.id} task={task} focused onUpdated={updated => { onUpdated?.(updated); setOpen(false); }} />
      </div>}
    </dialog>
  </>;
}
