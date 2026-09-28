import { useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, FileText, History, Minus, MoreHorizontal, Pencil, Plus, Trash2, X } from "lucide-react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { clearSourceCards, deleteDataRows, getOriginalFileUrl, getSourceCards, getSourceDetails, updateDataRow, type TableExportScope } from "../api";
import type { DataRowRead, SourceCard, SourceCardDetail, SourceCardPage, TableColumnDef } from "../types";
import { contentType, displayCardValue } from "./cardValues";
import { Disclosure } from "./Disclosure";
import { DocumentPreview } from "./DocumentPreview";
import { ConfirmDialog } from "./ConfirmDialog";
import { FileRevisionHistory } from "./FileRevisionHistory";
import { Icon } from "./Icon";
import { InputScopeDetails } from "./InputScopeDetails";
import "./cards.css";

const valueText = (value: unknown) => displayCardValue(value) || "—";
function previewValue(value: unknown, search: string) {
  const text = valueText(value);
  const query = search.trim();
  if (!query) return text;
  const start = text.toLocaleLowerCase().indexOf(query.toLocaleLowerCase());
  if (start < 0) return text;
  const match = text.slice(start, start + query.length);
  const after = Array.from(text.slice(start + match.length));
  return <><mark>{match}</mark>{after.slice(0, 12).join("")}{after.length > 12 ? "…" : ""}</>;
}
const emptyScope: TableExportScope = {};
// Only reading preferences are retained, never business values or unsaved drafts.
const collectionPositions = new Map<string, {page: number; capacity: number}>();
const readingPositions = new Map<string, {page: number; scroll: number}>();
const originalImageScales = new Map<string, number>();
function remember<K, V>(map: Map<K, V>, key: K, value: V) {
  map.delete(key); map.set(key, value);
  if (map.size > 40) map.delete(map.keys().next().value!);
}

export function SourceCardCollection({ tableId, scope = emptyScope, reload = 0, selected, onSelect, onChanged, hiddenFields = [], highlightTaskId, selectedRows = [], onSelectRow }: {
  tableId: string; scope?: TableExportScope; reload?: number; selected: SourceCard[];
  onSelect: (cards: SourceCard[]) => void; onChanged: () => void; hiddenFields?: string[]; highlightTaskId?: string | null;
  selectedRows?: number[]; onSelectRow?: (row: DataRowRead, checked: boolean) => void;
}) {
  const collectionKey = tableId + JSON.stringify(scope);
  const area = useRef<HTMLDivElement>(null);
  const [page, setPage] = useState(() => collectionPositions.get(collectionKey)?.page ?? 1);
  const [capacity, setCapacity] = useState(() => collectionPositions.get(collectionKey)?.capacity ?? 6);
  const capacityRef = useRef(capacity);
  const [result, setResult] = useState<SourceCardPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [opened, setOpened] = useState<SourceCard | null>(null);
  const scopeKey = JSON.stringify(scope);
  useEffect(() => { setPage(collectionPositions.get(collectionKey)?.page ?? 1); setOpened(null); }, [collectionKey]);
  useEffect(() => { remember(collectionPositions, collectionKey, {page, capacity}); }, [collectionKey, page, capacity]);
  useEffect(() => {
    const viewport = area.current?.closest(".data-table-scroll");
    if (!viewport) return;
    const observer = new ResizeObserver(([entry]) => {
      const width = Math.max(0, entry.contentRect.width - 32);
      const footerHeight = area.current?.querySelector(".table-footer")?.getBoundingClientRect().height ?? 68;
      const height = Math.max(0, entry.contentRect.height - footerHeight - 32);
      const columns = Math.max(1, Math.min(3, Math.floor((width + 16) / 316)));
      const rows = height >= 536 ? 2 : 1;
      area.current?.style.setProperty("--card-columns", String(columns));
      area.current?.style.setProperty("--source-card-height", `${Math.max(260, Math.min(320, (height - (rows - 1) * 16) / rows))}px`);
      const next = columns * rows, previous = capacityRef.current;
      if (next !== previous) {
        capacityRef.current = next; setCapacity(next);
        setPage(current => Math.floor((current - 1) * previous / next) + 1);
      }
    });
    observer.observe(viewport);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    let active = true;
    setLoading(true); setError("");
    getSourceCards(tableId, JSON.parse(scopeKey), page, capacity).then(data => {
      if (!active) return;
      if (page > 1 && !data.groups.length) { setPage(Math.max(1, Math.ceil(data.total / capacity))); return; }
      setResult(data);
    }).catch(reason => { if (active) setError(reason.message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [tableId, scopeKey, page, capacity, reload]);
  function choose(card: SourceCard, checked: boolean) {
    if (checked && selected.length >= 50) { setError("一次最多选择 50 个来源。导出整表时无需逐个勾选。"); return; }
    setError(""); onSelect(checked ? [...selected.filter(item => item.id !== card.id), card] : selected.filter(item => item.id !== card.id));
  }
  return <div ref={area} className="file-collection" aria-busy={loading}>
    {error && <p role="alert" className="callout danger">{error}</p>}
    {!result && loading && <p className="support">正在读取文件与来源…</p>}
    {result && <>
      <div className="file-card-grid" aria-label="文件与来源卡片">
        {!result.groups.length && <p className="table-empty-state">没有符合条件的数据</p>}
        {result.groups.map(card => {
          const preview = [...card.preview_header, ...(card.preview_items[0]?.fields ?? [])].filter(field => !hiddenFields.includes(field.key));
          if (scope.search?.trim()) preview.sort((a, b) => Number(!displayCardValue(a.value).toLocaleLowerCase().includes(scope.search!.trim().toLocaleLowerCase())) - Number(!displayCardValue(b.value).toLocaleLowerCase().includes(scope.search!.trim().toLocaleLowerCase())));
          const checked = selected.some(item => item.id === card.id);
          return <article className={`file-data-card${checked ? " selected" : ""}${highlightTaskId && highlightTaskId === card.task_id ? " highlighted" : ""}`} key={card.id}>
            <div className="file-card-heading"><Icon icon={FileText} size={16} />
              <button title={card.filename} onClick={() => setOpened(card)}>{card.filename}</button>
              <input type="checkbox" aria-label={`选择完整来源 ${card.filename}`} checked={checked} disabled={loading || !card.complete}
                title={card.complete ? "选择此来源的全部当前数据" : "当前只显示部分明细，不能按完整来源选择"} onChange={event => choose(card, event.target.checked)} />
            </div>
            <dl className="file-field-list">{preview.slice(0, 3).map(field => <div key={field.key}><dt title={field.label}>{field.label}</dt><dd title={displayCardValue(field.value)}>{previewValue(field.value, scope.search ?? "")}</dd></div>)}</dl>
            {!preview.length && <p className="support">尚未填写内容</p>}
            <div className="file-card-status">{card.review_pending && <span className="badge warning">来源待核对</span>}{card.partial_input && <span className="badge muted">局部读取</span>}</div>
            <div className="file-card-footer"><span>{!card.complete ? `匹配 ${card.matched_row_count} / ${card.row_count} 条` : card.item_count ? `${card.item_count} 条明细` : "文件级数据"}</span>
              <button className="btn secondary xs" onClick={() => setOpened(card)}>查看与编辑</button></div>
          </article>;
        })}
      </div>
      <div className="table-footer"><span className="footer-info">共 {result.total} 个来源 · {result.row_count} 条记录</span><div className="pagination">
        <button className="btn secondary sm" disabled={page <= 1 || loading} aria-label="上一页来源" onClick={() => setPage(p => p - 1)}><Icon icon={ChevronLeft} size={14} /></button>
        <span>{page} / {Math.max(1, Math.ceil(result.total / capacity))}</span>
        <button className="btn secondary sm" disabled={page * capacity >= result.total || loading} aria-label="下一页来源" onClick={() => setPage(p => p + 1)}><Icon icon={ChevronRight} size={14} /></button>
      </div></div>
    </>}
    {opened && <SourceDetail key={opened.id} tableId={tableId} initial={opened} scope={scope} onChanged={onChanged} onClose={() => setOpened(null)} selectedRows={selectedRows} onSelectRow={onSelectRow} />}
  </div>;
}

function SourceDetail({ tableId, initial, scope, onChanged, onClose, selectedRows, onSelectRow }: {
  tableId: string; initial: SourceCard; scope: TableExportScope; onChanged: () => void; onClose: () => void;
  selectedRows: number[]; onSelectRow?: (row: DataRowRead, checked: boolean) => void;
}) {
  const fileId = initial.task_id;
  const originalUrl = fileId ? getOriginalFileUrl(fileId) : "";
  const originalType = contentType(initial.filename);
  const zoomableImage = originalType.startsWith("image/");
  const dialog = useRef<HTMLDialogElement>(null);
  const layout = useRef<HTMLDivElement>(null);
  const originalPane = useRef<HTMLDivElement>(null);
  const [originalScale, setOriginalScale] = useState(() => originalImageScales.get(originalUrl) ?? 1);
  function changeOriginalScale(value: number) {
    const next = Math.min(3, Math.max(0.5, Number(value.toFixed(1))));
    remember(originalImageScales, originalUrl, next);
    setOriginalScale(next);
  }
  function fitOriginalImage() {
    if (originalPane.current) {
      originalPane.current.scrollTop = 0; originalPane.current.scrollLeft = 0;
      originalPane.current.dispatchEvent(new Event("scroll"));
    }
    changeOriginalScale(1);
  }
  const [splitRatio, setSplitRatio] = useState(() => {
    try { const saved = Number(localStorage.getItem("zhiyi-card-split")); return saved >= 30 && saved <= 70 ? saved : 50; }
    catch { return 50; }
  });
  function resize(ratio: number) {
    const next = Math.max(30, Math.min(70, ratio)); setSplitRatio(next);
    try { localStorage.setItem("zhiyi-card-split", String(next)); } catch { /* Optional reading preference. */ }
  }
  const [source, setSource] = useState<SourceCardDetail | null>(null);
  const readingKey = tableId + initial.id + JSON.stringify(scope);
  const content = useRef<HTMLDivElement>(null);
  const [page, setPage] = useState(() => readingPositions.get(readingKey)?.page ?? 1);
  const pendingLeave = useRef<() => void>(() => {});
  const [reload, setReload] = useState(0);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [showOriginal, setShowOriginal] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<"header" | DataRowRead | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [confirm, setConfirm] = useState<"clear" | DataRowRead | null>(null);
  const [discard, setDiscard] = useState(false);
  const scopeKey = JSON.stringify(scope);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    return () => previous?.focus();
  }, []);
  useEffect(() => {
    let active = true;
    setLoading(true); setError("");
    getSourceDetails(tableId, initial.id, JSON.parse(scopeKey), page).then(data => {
      if (!active) return;
      if (page > 1 && !data.rows.length) { setPage(Math.max(1, Math.ceil(data.total / 10))); return; }
      setSource(data);
    }).catch(reason => {
      if (!active) return;
      if (reason.status === 404) setSource(null);
      else setError(reason.message);
    })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [initial.id, tableId, scopeKey, page, reload]);
  useEffect(() => {
    if (!loading && content.current) content.current.scrollTop = readingPositions.get(readingKey)?.scroll ?? 0;
  }, [loading, page, readingKey]);
  function savePosition() {
    remember(readingPositions, readingKey, {page, scroll: content.current?.clientHeight ? content.current.scrollTop : readingPositions.get(readingKey)?.scroll ?? 0});
  }
  const busy = saving || loading;
  const headers = source?.schema.filter(column => column.section === "header") ?? [];
  const details = source?.schema.filter(column => column.section !== "header") ?? [];
  function close() {
    if (saving) return;
    savePosition(); leave(onClose);
  }
  function leave(action: () => void) {
    const columns = editing === "header" ? headers : details;
    const values = editing === "header" ? source?.header : editing?.values;
    const dirty = editing && columns.some(column => (draft[column.key] ?? "") !== displayCardValue(values?.[column.key]));
    if (dirty) { pendingLeave.current = action; setDiscard(true); } else action();
  }
  function changePage(next: number) {
    remember(readingPositions, readingKey, {page: next, scroll: 0}); setPage(next);
  }
  function begin(target: "header" | DataRowRead) {
    if (!source) return;
    const columns = target === "header" ? headers : details;
    const values = target === "header" ? source.header : target.values;
    setEditing(target); setConfirm(null); setError("");
    setDraft(Object.fromEntries(columns.map(column => [column.key, displayCardValue(values[column.key])])));
  }
  async function save() {
    if (!source || !editing) return;
    const row = editing === "header" ? source.rows[0] : editing;
    if (!row) return;
    setSaving(true); setError("");
    try {
      const columns = editing === "header" ? headers : details;
      const before = editing === "header" ? source.header : row.values;
      const changes: Record<string, unknown> = {};
      for (const column of columns) {
        const text = draft[column.key] ?? "";
        if (text === displayCardValue(before[column.key])) continue;
        let value: unknown = text === "" ? null : text;
        if (text !== "" && column.value_type === "number") {
          value = Number(text.replace(/,/g, "").trim());
          if (!text.trim() || !Number.isFinite(value)) throw new Error(`「${column.label}」需要有效数字。`);
        }
        if (text !== "" && column.value_type === "boolean") {
          if (["是", "true", "1"].includes(text.trim())) value = true;
          else if (["否", "false", "0"].includes(text.trim())) value = false;
          else throw new Error(`「${column.label}」需要填写是或否。`);
        }
        changes[column.key] = value;
      }
      if (Object.keys(changes).length) {
        await updateDataRow(tableId, row.id, row.version, changes, undefined, source.shared_header_keys);
        onChanged(); setReload(value => value + 1);
      }
      setEditing(null);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "修改未完成，请重试。"); }
    finally { setSaving(false); }
  }
  async function remove() {
    if (!confirm || !source) return;
    setSaving(true); setError("");
    try {
      if (confirm === "clear") await clearSourceCards(tableId, [{id: source.id, version: source.version}], scope);
      else await deleteDataRows(tableId, [confirm.id], undefined, {[confirm.id]: confirm.version});
      setConfirm(null); setEditing(null); onChanged();
      if (confirm === "clear" || source.total === 1) onClose();
      else setReload(value => value + 1);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "删除未完成，请重试。"); }
    finally { setSaving(false); }
  }
  function fields(columns: TableColumnDef[], values: Record<string, unknown>, editable: boolean) {
    return <dl className="file-field-list expanded">{columns.map(column => <div key={column.key}><dt>{column.label}</dt>
      <dd>{editable ? column.value_type === "text" && /[\r\n]|.{100}/s.test(displayCardValue(values[column.key])) ? <textarea className="form-textarea" rows={3} aria-label={column.label} disabled={saving} value={draft[column.key] ?? ""} onChange={event => setDraft({...draft, [column.key]: event.target.value})} />
        : <input className="form-input" aria-label={column.label} disabled={saving} value={draft[column.key] ?? ""} onChange={event => setDraft({...draft, [column.key]: event.target.value})} /> : valueText(values[column.key])}</dd>
    </div>)}</dl>;
  }
  return <>
    <dialog ref={dialog} className={`content-card-dialog file-card-dialog${showOriginal ? " with-original" : ""}`} aria-labelledby="source-card-title" onCancel={event => { event.preventDefault(); close(); }}>
      <div className="content-card-detail-head"><h3 id="source-card-title" title={initial.filename}>{initial.filename}</h3><div className="content-card-detail-actions">
        {fileId && <><button className="btn secondary xs" aria-pressed={showOriginal} onClick={() => { savePosition(); setShowOriginal(!showOriginal); }}><Icon icon={FileText} size={14} />{showOriginal ? <><span className="compare-wide-label">收起对照</span><span className="compare-narrow-label">返回数据</span></> : "原件对照"}</button>
          <button className="btn secondary xs" onClick={() => setShowHistory(true)}><Icon icon={History} size={14} />修订历史</button></>}
        <button className="btn ghost sm icon-only" aria-label="关闭来源详情" disabled={saving} onClick={close}><Icon icon={X} size={16} /></button>
      </div></div>
      <div ref={layout} className={`content-card-detail-layout${showOriginal ? " showing-original" : ""}`}><div ref={content} onScroll={savePosition} className="content-card-detail-content" style={showOriginal ? {flex: `0 0 ${splitRatio}%`} : undefined} aria-busy={busy}>
        {error && <div role="alert" className="callout danger">{error}<button className="btn secondary xs" onClick={() => leave(() => { setEditing(null); setReload(value => value + 1); })}>重新读取</button></div>}
        {loading && <p className="support">正在读取当前数据…</p>}
        {!loading && !source && !error && <p className="support" role="status">当前筛选范围内已没有此来源的数据。可以关闭详情查看其他结果。</p>}
        {source && <>
          {!source.complete && <p className="support">当前筛选匹配 {source.total} / {source.row_count} 条记录</p>}
          {!!headers.length && <section className="file-data-section"><div className="file-section-heading"><h4>{fileId ? "文件级" : "共享字段"}</h4><button className="btn ghost xs icon-only" aria-label="编辑共享字段" title="编辑共享字段" disabled={busy || editing !== null || !source.complete} onClick={() => begin("header")}><Icon icon={Pencil} size={14} /></button></div>{fields(headers, source.header, editing === "header")}</section>}
          {!!source.item_count && !!details.length && <Disclosure defaultOpen title={`明细 · ${source.complete ? source.item_count : source.total} 条`}>
            {source.rows.filter(row => !row.is_header_only).map(row => <section className="file-data-section" key={row.id}>
              <div className="file-section-heading"><h4>{onSelectRow && <input type="checkbox" aria-label={`选择明细 ${row.item_index}`} checked={selectedRows.includes(row.id)} disabled={busy || editing !== null} onChange={event => onSelectRow(row, event.target.checked)} />}明细 {row.item_index}</h4><button className="btn ghost xs icon-only" title={`编辑明细 ${row.item_index}`} aria-label={`编辑明细 ${row.item_index}`} disabled={busy || editing !== null} onClick={() => begin(row)}><Icon icon={Pencil} size={14} /></button></div>
              {fields(details, row.values, editing !== null && editing !== "header" && editing.id === row.id)}
            </section>)}
            {source.total > 10 && <div className="file-detail-pagination"><button className="btn secondary xs" disabled={busy || editing !== null || page <= 1} onClick={() => changePage(page - 1)}>上一页明细</button><span>{page} / {Math.max(1, Math.ceil(source.total / 10))}</span><button className="btn secondary xs" disabled={busy || editing !== null || page * 10 >= source.total} onClick={() => changePage(page + 1)}>下一页明细</button></div>}
          </Disclosure>}
          {source.review_pending && <p className="support">来源待核对{!fileId && " · 保留导入或合并时的核对状态"}</p>}
          {source.rows[0]?.input_scope && <InputScopeDetails scope={source.rows[0].input_scope} label="来源与处理详情" originalAvailable={Boolean(fileId)} />}
        </>}
      </div>{showOriginal && fileId && <>
        <div className="content-card-splitter" role="separator" aria-label="调整内容与原件宽度" aria-orientation="vertical" tabIndex={0} aria-valuenow={splitRatio} aria-valuemin={30} aria-valuemax={70}
          onKeyDown={event => { if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return; event.preventDefault(); resize(event.key === "Home" ? 30 : event.key === "End" ? 70 : splitRatio + (event.key === "ArrowLeft" ? -2 : 2)); }}
          onPointerDown={event => { event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId); }}
          onPointerMove={event => { if (!event.currentTarget.hasPointerCapture(event.pointerId)) return; const bounds = layout.current?.getBoundingClientRect(); if (bounds?.width) resize((event.clientX - bounds.left) / bounds.width * 100); }}
          onPointerUp={event => { if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }} />
        <div ref={originalPane} className="content-card-original" data-document-scroll>
          {zoomableImage && <div className="source-image-zoom" role="group" aria-label="图片原件缩放控件">
            <button type="button" className="btn secondary xs" aria-label="缩小图片原件" title="缩小图片原件" disabled={originalScale <= 0.5} onClick={() => changeOriginalScale(originalScale - 0.1)}><Icon icon={Minus} size={14} /></button>
            <output aria-label="图片原件缩放比例" aria-live="polite">{originalScale.toFixed(1)}×</output>
            <button type="button" className="btn secondary xs" aria-label="恢复图片原件适配宽度" title="恢复到适配宽度" disabled={originalScale === 1} onClick={fitOriginalImage}>适配</button>
            <button type="button" className="btn secondary xs" aria-label="放大图片原件" title="放大图片原件" disabled={originalScale >= 3} onClick={() => changeOriginalScale(originalScale + 0.1)}><Icon icon={Plus} size={14} /></button>
          </div>}
          <DocumentPreview showDownload={false} contentType={originalType} filename={initial.filename} url={originalUrl} pageNumber={1} scale={zoomableImage ? originalScale : 1} flowPages />
        </div>
      </>}</div>
      <div className="file-detail-footer">{editing ? <>
        <span className="support source-edit-status" role="status">正在编辑{editing === "header" ? "文件信息" : `明细 ${editing.item_index}`}，提交或取消后可切换</span>
        {editing !== "header" && <button className="btn danger-soft xs source-delete-action" disabled={busy} onClick={() => setConfirm(editing)}>删除此明细</button>}
        <button className="btn secondary xs" disabled={saving} onClick={() => leave(() => { setEditing(null); setConfirm(null); })}>取消修改</button><button className="btn primary xs" disabled={busy} onClick={() => void save()}>{saving ? "提交中…" : "提交修改"}</button>
      </> : <>
        <span className="support">{fileId ? "当前数据" : "独立数据"}</span>
        <DropdownMenu.Root><DropdownMenu.Trigger asChild><button className="btn ghost xs icon-only" aria-label="更多数据操作" disabled={busy}><Icon icon={MoreHorizontal} size={16} /></button></DropdownMenu.Trigger>
          <DropdownMenu.Portal container={dialog.current}><DropdownMenu.Content className="record-menu" align="end" sideOffset={5}>
            <DropdownMenu.Item className="danger" disabled={!source?.complete} onSelect={() => setConfirm("clear")}><Icon icon={Trash2} size={14} />清空此来源数据</DropdownMenu.Item>
            {!source?.complete && <p className="record-menu-note">当前仅匹配部分明细，不能清空整个来源。</p>}
          </DropdownMenu.Content></DropdownMenu.Portal>
        </DropdownMenu.Root>
      </>}</div>
      <ConfirmDialog open={discard} tone="normal" title="放弃未提交的修改？" description="这轮输入尚未提交，已保存的数据不受影响。" buttonLabel="放弃修改" onClose={() => setDiscard(false)} onConfirm={() => { setDiscard(false); setEditing(null); pendingLeave.current(); }} />
      <ConfirmDialog open={Boolean(confirm)} title={confirm === "clear" ? "清空此来源数据？" : "删除此明细？"}
        description={<>{confirm === "clear" ? `将清空此来源的 ${source?.row_count ?? 0} 条当前记录。` : `将删除明细 ${confirm?.item_index ?? ""}。${fileId && source?.item_count === 1 ? "这是最后一条明细，文件级数据也会清空。" : ""}`}{fileId ? "原件和修订历史仍会保留。" : "独立数据的删除记录仍会保留。"}</>}
        buttonLabel={confirm === "clear" ? "确认清空" : "确认删除"} busy={busy} onClose={() => setConfirm(null)} onConfirm={() => void remove()} />
    </dialog>
    {showHistory && fileId && <FileRevisionHistory taskId={fileId} filename={initial.filename} onClose={() => setShowHistory(false)} />}
  </>;
}
