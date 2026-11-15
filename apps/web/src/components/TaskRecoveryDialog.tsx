import { useEffect, useRef, useState } from "react";
import { getTables, retryTask, selectTaskTemplate } from "../api";
import type { DataTableRead, ExtractionTemplate, Task } from "../types";
import { taskDisplayName } from "../taskNames";
import { TaskActionDialog } from "./TaskActionDialog";
import { PartialInputAction } from "./InputScopeDetails";
import { TaskExportDetails } from "./TaskExportDetails";
import { TaskFailureDetails } from "./TaskFailureDetails";

export type TaskRecoveryKind = "template" | "scope" | "export" | "retry" | "details";
export function TaskRecoveryDialog({ task, kind, templates, templatesLoading, templatesError, reloadTemplates, onUpdated, onClose }: {
  task: Task; kind: TaskRecoveryKind; templates: ExtractionTemplate[]; templatesLoading: boolean; templatesError: string | null;
  reloadTemplates: () => void; onUpdated: () => Promise<void>; onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [choice, setChoice] = useState("");
  const [useCurrentSettings, setUseCurrentSettings] = useState(false);
  const [tables, setTables] = useState<DataTableRead[]>([]);
  const [tablesLoading, setTablesLoading] = useState(false);
  const [tablesError, setTablesError] = useState<string | null>(null);
  const generation = useRef(0);
  const mutation = useRef(false);
  const choosingTable = task.pending_action === "confirm_target_table";
  async function loadTables() {
    const request = ++generation.current; setTablesLoading(true); setTablesError(null);
    try { const items = await getTables(); if (generation.current === request) setTables(items); }
    catch (reason) { if (generation.current === request) setTablesError(reason instanceof Error ? reason.message : "数据表列表读取失败"); }
    finally { if (generation.current === request) setTablesLoading(false); }
  }
  useEffect(() => { if (kind === "template" && choosingTable) void loadTables(); return () => { generation.current += 1; }; }, [kind, choosingTable]);
  async function submit(operation: () => Promise<unknown>) {
    if (mutation.current) return; mutation.current = true; setBusy(true); setError(null);
    try { await operation(); await onUpdated(); onClose(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "操作失败，请重试。"); }
    finally { mutation.current = false; setBusy(false); }
  }
  const candidates = task.candidate_templates;
  const options = choosingTable
    ? tables.filter(table => table.template_key === candidates[0]?.id).map(table => ({ id: table.id, name: table.name }))
    : [...candidates.map(item => ({ id: item.id, name: `${item.name}（推荐）` })), ...templates.filter(item => item.is_active !== false && !candidates.some(candidate => candidate.id === item.id)).map(item => ({ id: item.id, name: item.name }))];
  const catalogLoading = choosingTable ? tablesLoading : templatesLoading;
  const catalogError = choosingTable ? tablesError : templatesError;
  const catalogReady = !catalogLoading && !catalogError;
  const selectionAvailable = options.some(item => item.id === choice) && (catalogReady || (!choosingTable && candidates.some(item => item.id === choice)));
  const title = kind === "retry" ? "重试选项" : kind === "scope" ? "确认读取范围" : kind === "export" ? "处理外部文件" : kind === "details" ? "诊断详情" : choosingTable ? "选择数据表" : "选择模板";
  return <TaskActionDialog open title={title} filename={taskDisplayName(task)} busy={busy} onClose={onClose}>
    {kind === "retry" && <><p className="support">仅重新处理此文件。此选项只调整读取范围。</p>
      <label className="task-retry-choice"><input type="radio" name="task-retry-policy" checked={!useCurrentSettings} disabled={busy} onChange={() => setUseCurrentSettings(false)}/>沿用任务原读取范围</label>
      <label className="task-retry-choice"><input type="radio" name="task-retry-policy" checked={useCurrentSettings} disabled={busy} onChange={() => setUseCurrentSettings(true)}/>按当前范围重新读取</label>
      <p className="support">按设置中的页数、文本和图片范围读取。</p><button type="button" className="btn primary" disabled={busy} onClick={() => void submit(() => retryTask(task.id, useCurrentSettings))}>确认重试</button></>}
    {kind === "scope" && <PartialInputAction task={task} onBusyChange={setBusy} onUpdated={() => { void onUpdated(); onClose(); }}/>}
    {kind === "export" && <TaskExportDetails task={task} focused onBusyChange={setBusy} onUpdated={() => { void onUpdated(); onClose(); }}/>}
    {kind === "details" && <><p className="support">{task.failure_message || "处理失败，可查看诊断后重试。"}</p><TaskFailureDetails taskId={task.id}/></>}
    {kind === "template" && <>
      <p className="support">{choosingTable ? "已有提取结果与原指定表不兼容，请选择匹配的数据表。此操作不会再次调用模型。" : "选择模板后，此文件会继续处理。"}</p>
      {catalogLoading && <p role="status">{choosingTable ? "正在读取数据表列表…" : "正在读取模板列表…"}</p>}
      {catalogError && <div className="callout danger" role="alert">{catalogError}<button type="button" className="btn secondary sm" disabled={busy} onClick={() => choosingTable ? void loadTables() : reloadTemplates()}>{choosingTable ? "重新读取数据表" : "重新读取模板"}</button></div>}
      {catalogReady && options.length === 0 && <p>{choosingTable ? "没有兼容的数据表，请先创建数据表。" : "没有可用模板，请先创建或启用模板。"}</p>}
      <label className="form-label" htmlFor="task-template-choice">{choosingTable ? "选择指定表" : "选择模板"}</label><select id="task-template-choice" className="form-select" aria-label={choosingTable ? "选择指定表" : "选择模板"} disabled={busy || !options.length || (choosingTable && !catalogReady)} value={choice} onChange={event => setChoice(event.target.value)}><option value="" disabled>请选择…</option>{options.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select>
      <button type="button" className="btn primary task-dialog-submit" disabled={busy || !selectionAvailable || (choosingTable && task.pending_review_version == null)} onClick={() => void submit(() => selectTaskTemplate(task.id, choosingTable ? undefined : choice, choosingTable ? choice : undefined, choosingTable ? task.pending_review_version ?? undefined : undefined))}>确认</button>
      {choosingTable && task.pending_review_version == null && <p className="support">尚未确认结果版本，请关闭后重新读取任务。</p>}
    </>}
    {error && <div className="callout danger" role="alert">{error}</div>}
  </TaskActionDialog>;
}
