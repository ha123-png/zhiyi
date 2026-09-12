import { useEffect, useRef, useState } from "react";
import { FileText, X } from "lucide-react";
import { getOriginalFileUrl, updateDataRow } from "../api";
import type { DataRowRead, TableColumnDef, TemplateBehavior } from "../types";
import { DocumentPreview } from "./DocumentPreview";
import { Icon } from "./Icon";
import { InfoHint } from "./InfoHint";
import { InputScopeDetails } from "./InputScopeDetails";
import "./cards.css";

interface Props {
  tableId: string;
  rows: DataRowRead[];
  columns: TableColumnDef[];
  hiddenFields?: string[];
  presentation?: TemplateBehavior["presentation"];
  selectedRows: number[];
  onSelect: (ids: number[]) => void;
  onChanged: () => void;
  highlightTaskId?: string | null;
  searchText?: string;
}

export function displayCardValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "boolean") return value ? "是" : "否";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}


function contentType(filename: string): string {
  const extension = filename.split(".").pop()?.toLowerCase() ?? "";
  return ({ pdf: "application/pdf", txt: "text/plain", md: "text/markdown",
    docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", webp: "image/webp",
    bmp: "image/bmp", gif: "image/gif", tif: "image/tiff", tiff: "image/tiff",
  } as Record<string, string>)[extension] ?? "application/octet-stream";
}

export function CardCollection({ tableId, rows, columns, hiddenFields = [], selectedRows, onSelect, onChanged, highlightTaskId, searchText = "" }: Props) {
  const [opened, setOpened] = useState<DataRowRead | null>(null);
  const [showOriginal, setShowOriginal] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const layout = useRef<HTMLDivElement>(null);
  const [splitRatio, setSplitRatio] = useState(() => {
    try { const saved = Number(localStorage.getItem("zhiyi-card-split")); return saved >= 30 && saved <= 70 ? saved : 50; }
    catch { return 50; }
  });
  function resize(ratio: number) {
    const next = Math.max(30, Math.min(70, ratio));
    setSplitRatio(next);
    try { localStorage.setItem("zhiyi-card-split", String(next)); } catch { /* Reading remains available without preference storage. */ }
  }

  useEffect(() => {
    if (opened && !dialog.current?.open) dialog.current?.showModal();
  }, [opened]);

  function close() {
    if (saving) return;
    if (editing && opened && columns.some((c) => (draft[c.key] ?? "") !== displayCardValue(opened.values[c.key]))
      && !window.confirm("有尚未保存的修改，确定放弃并关闭？")) return;
    dialog.current?.close();
    setOpened(null); setEditing(false); setShowOriginal(false); setError(null);
    returnFocus.current?.focus();
  }

  function open(row: DataRowRead) {
    returnFocus.current = document.activeElement as HTMLElement;
    setOpened(row); setShowOriginal(false); setEditing(false); setError(null);
  }

  const titleColumn = columns[0];
  function title(row: DataRowRead) {
    return titleColumn
      ? displayCardValue(row.values[titleColumn.key]) || `未填写${titleColumn.label}`
      : `内容 ${row.id}`;
  }

  async function save() {
    if (!opened) return;
    setSaving(true); setError(null);
    try {
      const changes: Record<string, unknown> = {};
      for (const column of columns) {
        const text = draft[column.key] ?? "";
        if (text === displayCardValue(opened.values[column.key])) continue;
        let value: unknown = text === "" ? null : text;
        if (text !== "" && column.value_type === "number") {
          value = Number(text.replace(/,/g, "").trim());
          if (!Number.isFinite(value)) throw new Error(`「${column.label}」需要有效数字。`);
        }
        if (text !== "" && column.value_type === "boolean") {
          if (["是", "true", "1"].includes(text.trim())) value = true;
          else if (["否", "false", "0"].includes(text.trim())) value = false;
          else throw new Error(`「${column.label}」需要填写是或否。`);
        }
        changes[column.key] = value;
      }
      if (Object.keys(changes).length) {
        const updated = await updateDataRow(tableId, opened.id, opened.version, changes);
        setOpened(updated);
        onChanged();
      }
      setEditing(false);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "保存失败，请重试。"); }
    finally { setSaving(false); }
  }

  const originalName = displayCardValue(opened?.values.source_filename);
  const query = searchText.trim();
  function highlighted(text: string, excerpt = false) {
    if (excerpt && query) text = text.replace(/\s+/g, " ");
    const index = query ? text.toLocaleLowerCase().indexOf(query.toLocaleLowerCase()) : -1;
    if (index < 0) return text;
    const start = excerpt ? Math.max(0, index - 10) : 0;
    const end = excerpt ? Math.min(text.length, index + query.length + 120) : text.length;
    return <>{start > 0 ? "…" : ""}{text.slice(start, index)}<mark>{text.slice(index, index + query.length)}</mark>{text.slice(index + query.length, end)}{end < text.length ? "…" : ""}</>;
  }
  return <>
    <div className="content-card-grid" aria-label="卡片内容">
      {rows.length === 0 && <div className="table-empty-state">没有符合条件的内容</div>}
      {rows.map((row) => {
        const nonEmpty = columns.filter((c) => displayCardValue(row.values[c.key]) !== "");
        const preview = nonEmpty.filter((c) => !hiddenFields.includes(c.key) && c.key !== titleColumn?.key).slice(0, 3);
        const match = query ? nonEmpty.find((c) => displayCardValue(row.values[c.key]).toLocaleLowerCase().includes(query.toLocaleLowerCase())) : undefined;
        return <article className={`content-card${highlightTaskId && row.task_id === highlightTaskId ? " highlighted" : ""}`} key={row.id}>
          <div className="content-card-head">
            <button className="content-card-title" onClick={() => open(row)}>{highlighted(title(row))}</button>
            <input type="checkbox" aria-label={`选择内容 ${row.id}`} checked={selectedRows.includes(row.id)}
              onChange={(event) => onSelect(event.target.checked ? [...selectedRows, row.id] : selectedRows.filter((id) => id !== row.id))} />
          </div>
          <div className="content-card-body">
            {match && <div className="content-card-field content-card-match"><span className="small muted">匹配 · {match.label}</span><p>{highlighted(displayCardValue(row.values[match.key]), true)}</p></div>}
            {preview.filter((column) => column.key !== match?.key).slice(0, match ? 2 : 3).map((column) => <div className="content-card-field" key={column.key}>
              <span className="small muted">{column.label}</span>
              <p>{highlighted(displayCardValue(row.values[column.key]), true)}</p>
            </div>)}
            {nonEmpty.length === 0 && <p className="muted">尚未填写内容，展开后可编辑。</p>}
          </div>
          <div className="content-card-footer">
            {row.review_pending && <span className="badge warning">来源待核对</span>}
            {row.input_scope?.coverage === "partial" && <span className="badge muted">局部读取</span>}
            <span title={displayCardValue(row.values.source_filename)}>{row.task_id ? displayCardValue(row.values.source_filename) || "有来源文件" : "无来源文件"}</span>
            <button className="btn ghost xs" onClick={() => open(row)}>查看全文</button>
          </div>
        </article>;
      })}
    </div>
    {opened && <dialog ref={dialog} className={`content-card-dialog${showOriginal ? " with-original" : ""}`}
      aria-labelledby="content-card-detail-title" onCancel={(event) => { event.preventDefault(); close(); }}>
      <div className="content-card-detail-head">
        <h3 id="content-card-detail-title">内容详情</h3>
        <div className="content-card-detail-actions">
          {opened.task_id && <button className="btn secondary sm" onClick={() => setShowOriginal(!showOriginal)}>
            <Icon icon={FileText} size={14} />{showOriginal ? "收起原件" : "查看原件"}</button>}
          {!editing && <button className="btn primary sm" onClick={() => {
            setDraft(Object.fromEntries(columns.map((c) => [c.key, displayCardValue(opened.values[c.key])]))); setEditing(true);
          }}>编辑</button>}
          <button className="btn icon-only sm" aria-label="关闭内容详情" disabled={saving} onClick={close}><Icon icon={X} size={16} /></button>
        </div>
      </div>
      <div className={`content-card-detail-layout${showOriginal ? " showing-original" : ""}`} ref={layout}>
        <div className="content-card-detail-content" style={showOriginal ? { flexBasis: `${splitRatio}%`, flexGrow: 0 } : undefined}>
          <p className="support">{originalName || "手工内容或来源关联已移除"} · 第 {opened.item_index} 条</p>
          {error && <div role="alert" className="callout danger">{error}</div>}
          {editing && Boolean(opened.task_id || opened.values.__row_group) && columns.some((c) => c.section === "header") && <p className="callout">整份文件的信息会同步到该文件的其他条目。保存后表格与卡片同时更新。</p>}
          {columns.filter((c) => editing || displayCardValue(opened.values[c.key]) !== "").map((column) => {
            const value = displayCardValue(opened.values[column.key]);
            return <section className="content-card-detail-field" key={column.key}>
              {editing ? <label><span>{column.label}{column.section === "header" && (opened.task_id || opened.values.__row_group) ? " · 整份文件" : ""}</span>
                <textarea className="form-textarea" disabled={saving} rows={column.value_type === "text" ? 4 : 2}
                  value={draft[column.key] ?? ""} onChange={(e) => setDraft({ ...draft, [column.key]: e.target.value })} /></label>
                : <><h4>{column.label}</h4><p>{highlighted(value)}</p></>}
            </section>;
          })}
          {!editing && columns.every((c) => displayCardValue(opened.values[c.key]) === "") && <p>暂无内容，可点击编辑填写。</p>}
          {opened.review_pending && <p className="support" style={{ color: "var(--warning-text)" }}>来源待核对 · 修改这里的内容不会重新执行来源任务的校验。{!opened.task_id && "来源已移除或来自导入/合并，此处保留原状态。"}</p>}
          <InputScopeDetails scope={opened.input_scope} label="来源与处理详情" originalAvailable={Boolean(opened.task_id)} />
        </div>
        {showOriginal && opened.task_id && <>
          <div className="content-card-splitter" role="separator" aria-label="调整内容与原件宽度" aria-orientation="vertical"
            tabIndex={0} aria-valuenow={Math.round(splitRatio)} aria-valuemin={30} aria-valuemax={70}
            onKeyDown={(event) => {
              if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
              event.preventDefault();
              resize(event.key === "Home" ? 30 : event.key === "End" ? 70 : splitRatio + (event.key === "ArrowLeft" ? -2 : 2));
            }}
            onPointerDown={(event) => { event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId); }}
            onPointerMove={(event) => {
              if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
              const bounds = layout.current?.getBoundingClientRect();
              if (bounds?.width) resize((event.clientX - bounds.left) / bounds.width * 100);
            }}
            onPointerUp={(event) => { if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }} />
          <div className="content-card-original">
          <p className="support">原件 <InfoHint label="原件展示说明" text="未提供字段位置时，从文件开头展示。可在这里阅读完整原件。" /></p>
          <DocumentPreview contentType={contentType(originalName)} filename={originalName} pageNumber={1} scale={1}
            url={getOriginalFileUrl(opened.task_id)} flowPages showDownload={false} />
        </div></>}
      </div>
      {editing && <div className="content-card-detail-actions content-card-edit-footer">
        <button className="btn secondary sm" disabled={saving} onClick={() => { setEditing(false); setError(null); }}>取消修改</button>
        <button className="btn primary sm" disabled={saving} onClick={() => void save()}>{saving ? "保存中…" : "保存修改"}</button>
      </div>}
    </dialog>}
  </>;
}
