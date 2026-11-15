import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { FileText, History, Minus, MoreHorizontal, Plus, Trash2, X } from "lucide-react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { clearSourceCards, deleteDataRows, getOriginalFileUrl, getSourceCards, getSourceDetails, updateDataRow, type TableExportScope } from "../api";
import type { DataRowRead, SourceCard, SourceCardDetail, SourceCardPage, TableColumnDef } from "../types";
import { contentType, displayCardValue } from "./cardValues";
import { manualCellValue } from "../numericInput";
import { EditableText } from "./EditableText";
import { Disclosure } from "./Disclosure";
import { DocumentPreview } from "./DocumentPreview";
import { OriginalDownload } from "./OriginalDownload";
import { ConfirmDialog } from "./ConfirmDialog";
import { FileRevisionHistory } from "./FileRevisionHistory";
import { Icon } from "./Icon";
import { ValidationResults } from "./ValidationResults";
import { InputScopeDetails } from "./InputScopeDetails";
import { SourceCardSummary, SourceFieldList } from "./SourceCardSummary";
import { WarehousePagination } from "./WarehousePagination";
import { NoticeDialog } from "./NoticeDialog";
import { Toast, useToast } from "./Toast";
import "./cards.css";

const emptyScope: TableExportScope = {};
// Only reading preferences are retained, never business values or unsaved drafts.
const collectionPositions = new Map<string, {page: number; capacity: number}>();
const readingPositions = new Map<string, {page: number; scroll: number}>();
const originalImageScales = new Map<string, number>();
function remember<K, V>(map: Map<K, V>, key: K, value: V) {
  map.delete(key); map.set(key, value);
  if (map.size > 40) map.delete(map.keys().next().value!);
}

interface ExampleSourceCollection {
  cards: readonly SourceCard[];
  detail: (card: SourceCard, page: number) => SourceCardDetail;
  edit: (id: number, key: string, value: unknown) => void;
  remove: (card: SourceCard, rows?: DataRowRead[]) => void;
}

export function SourceCardCollection({ tableId, scope = emptyScope, reload = 0, selected, onSelect, onChanged, hiddenFields = [], highlightTaskId, example }: {
  tableId: string; scope?: TableExportScope; reload?: number; selected: SourceCard[];
  onSelect: (cards: SourceCard[]) => void; onChanged: () => void; hiddenFields?: string[]; highlightTaskId?: string | null;
  example?: ExampleSourceCollection;
}) {
  const exampleCards = example?.cards;
  const scopeKey = JSON.stringify(scope);
  const collectionKey = tableId + scopeKey;
  const grid = useRef<HTMLDivElement>(null);
  const [{ page, capacity }, setPosition] = useState(() => collectionPositions.get(collectionKey) ?? { page: 1, capacity: 3 });
  function setPage(page: number) { setPosition(position => position.page === page ? position : { ...position, page }); }
  const [loaded, setLoaded] = useState<{ collectionKey: string; page: number; capacity: number; data: SourceCardPage } | null>(null);
  const result = loaded?.collectionKey === collectionKey ? loaded.data : undefined;
  const pageCount = result ? Math.max(1, Math.ceil(result.total / capacity)) : page;
  const resultMatches = loaded?.collectionKey === collectionKey && loaded.page === page &&
    (loaded.capacity === capacity || (page === 1 && loaded.data.total <= Math.min(loaded.capacity, capacity)));
  const [readAttempt, setReadAttempt] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [opened, setOpened] = useState<SourceCard | null>(null);
  const [readNoticeOpen, setReadNoticeOpen] = useState(false);
  const { toast, notify, clear } = useToast();
  useEffect(() => {
    const saved = collectionPositions.get(collectionKey);
    setPosition(position => ({
      capacity: position.capacity,
      page: saved ? Math.floor((saved.page - 1) * saved.capacity / position.capacity) + 1 : 1,
    }));
    setOpened(null); setReadNoticeOpen(false); clear();
  }, [collectionKey, clear]);
  useEffect(() => { remember(collectionPositions, collectionKey, {page, capacity}); }, [collectionKey, page, capacity]);
  useLayoutEffect(() => {
    const viewport = grid.current;
    if (!viewport) return;
    const style = getComputedStyle(viewport);
    const gap = parseFloat(style.columnGap) || 16;
    function measure(width: number) {
      if (width <= 0) return;
      const capacity = Math.max(1, Math.min(3, Math.floor((width + gap) / (280 + gap))));
      setPosition(previous => capacity === previous.capacity ? previous : {
        capacity,
        page: Math.floor((previous.page - 1) * previous.capacity / capacity) + 1,
      });
    }
    measure(viewport.clientWidth - (parseFloat(style.paddingLeft) || 0) - (parseFloat(style.paddingRight) || 0));
    const observer = new ResizeObserver(([entry]) => measure(entry.contentRect.width));
    observer.observe(viewport);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    let active = true;
    setLoading(true); setError("");
    if (exampleCards) {
      const totalPages = Math.max(1, Math.ceil(exampleCards.length / capacity));
      if (page > totalPages) setPage(totalPages);
      else setLoaded({ collectionKey, page, capacity, data: {
        table_id: tableId, page, page_size: capacity, total: exampleCards.length,
        row_count: exampleCards.reduce((count, card) => count + card.row_count, 0),
        groups: exampleCards.slice((page - 1) * capacity, page * capacity),
      } });
      setLoading(false);
      return;
    }
    getSourceCards(tableId, JSON.parse(scopeKey), page, capacity).then(data => {
      if (!active) return;
      if (page > 1 && !data.groups.length) { setPage(Math.max(1, Math.ceil(data.total / capacity))); return; }
      setLoaded({ collectionKey, page, capacity, data });
    }).catch(reason => { if (active) setError(reason.message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [tableId, scopeKey, collectionKey, page, capacity, reload, readAttempt, exampleCards]);
  function choose(card: SourceCard, checked: boolean) {
    if (checked && selected.length >= 50) { notify("一次最多选择 50 个来源。", "error"); return; }
    clear(); onSelect(checked ? [...selected.filter(item => item.id !== card.id), card] : selected.filter(item => item.id !== card.id));
  }
  return <div className="file-collection" aria-busy={loading}>
    <Toast toast={toast} onDismiss={clear} />
    {readNoticeOpen && error && <NoticeDialog title="来源读取失败" onClose={() => setReadNoticeOpen(false)}>
      <p>{error}</p>
      {resultMatches && <p>当前保留上次读取的来源。</p>}
      <button className="btn secondary sm" onClick={() => { setReadNoticeOpen(false); setReadAttempt(value => value + 1); }}>重新读取来源</button>
    </NoticeDialog>}
      <div ref={grid} className="warehouse-card-grid" aria-label="文件与来源卡片"
        style={{ gridTemplateColumns: `repeat(${capacity}, minmax(0, 1fr))` }}>
        {!resultMatches && loading && <p className="table-empty-state" role="status">正在读取文件与来源…</p>}
        {resultMatches && result && !result.groups.length && <p className="table-empty-state">没有符合条件的数据</p>}
        {resultMatches && result?.groups.map(card => {
          const preview = [...card.preview_header, ...(card.preview_items[0]?.fields ?? [])].filter(field => !hiddenFields.includes(field.key));
          if (scope.search?.trim()) preview.sort((a, b) => Number(!displayCardValue(a.value).toLocaleLowerCase().includes(scope.search!.trim().toLocaleLowerCase())) - Number(!displayCardValue(b.value).toLocaleLowerCase().includes(scope.search!.trim().toLocaleLowerCase())));
          const checked = selected.some(item => item.id === card.id);
          return <SourceCardSummary key={card.id} filename={card.filename} fields={preview} search={scope.search}
            className={`${checked ? "selected" : ""}${highlightTaskId && highlightTaskId === card.task_id ? " highlighted" : ""}`.trim()}
            onOpen={() => setOpened(card)}
            headingAction={<input type="checkbox" aria-label={`选择完整来源 ${card.filename}`} checked={checked} disabled={loading || !!error || !card.complete}
              title={card.complete ? "选择此来源的全部当前数据" : "当前只显示部分明细，不能按完整来源选择"} onChange={event => choose(card, event.target.checked)} />}
            status={<>{card.review_pending && <span className="badge warning">来源待核对</span>}{card.partial_input && <span className="badge muted">局部读取</span>}</>}
            summary={!card.complete ? `匹配 ${card.matched_row_count} / ${card.row_count} 条` : card.item_count ? `${card.item_count} 条明细` : "文件级数据"} />;
        })}
      </div>
      <WarehousePagination key={collectionKey} kind="sources" page={page} pageCount={pageCount}
        disabled={!resultMatches || loading || !!error} onChange={setPage}
        summary={error ? <button type="button" className="link-btn" aria-haspopup="dialog" onClick={() => setReadNoticeOpen(true)}>来源读取失败</button>
          : result ? `共 ${result.total} 个来源 · ${result.row_count} 条记录` : ""} />
    {opened && <SourceDetail key={opened.id} tableId={tableId} initial={opened} scope={scope} example={example} onChanged={onChanged} onClose={() => setOpened(null)} />}
  </div>;
}

function SourceDetail({ tableId, initial, scope, onChanged, onClose, example }: {
  tableId: string; initial: SourceCard; scope: TableExportScope; onChanged: () => void; onClose: () => void; example?: ExampleSourceCollection;
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
  const [sourceResult, setSourceResult] = useState<{ key: string; data: SourceCardDetail } | null>(null);
  const readingKey = tableId + initial.id + JSON.stringify(scope);
  const content = useRef<HTMLDivElement>(null);
  const [page, setPage] = useState(() => readingPositions.get(readingKey)?.page ?? 1);
  const [reload, setReload] = useState(0);
  const [loading, setLoading] = useState(!example);
  const [saving, setSaving] = useState(false);
  const [showOriginal, setShowOriginal] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [failedEdits, setFailedEdits] = useState<Record<string, { input: string; message: string }>>({});
  const [selectedDetails, setSelectedDetails] = useState<DataRowRead[]>([]);
  const [confirm, setConfirm] = useState<"clear" | DataRowRead[] | null>(null);
  const [removeError, setRemoveError] = useState("");
  const writeInFlight = useRef(false);
  const scopeKey = JSON.stringify(scope);
  const sourceQueryKey = JSON.stringify([tableId, initial.id, scopeKey, page]);
  // A prior page or pre-save snapshot must not become editable after a failed read.
  const isExample = Boolean(example);
  const exampleCard = example?.cards.find(card => card.id === initial.id);
  const source = example ? example.detail(exampleCard ?? initial, page)
    : sourceResult?.key === sourceQueryKey ? sourceResult.data : null;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    return () => previous?.focus();
  }, []);
  useEffect(() => {
    if (isExample) return;
    let active = true;
    setLoading(true); setError("");
    getSourceDetails(tableId, initial.id, JSON.parse(scopeKey), page).then(data => {
      if (!active) return;
      if (page > 1 && !data.rows.length) { setPage(Math.max(1, Math.ceil(data.total / 10))); return; }
      setSourceResult({ key: sourceQueryKey, data });
    }).catch(reason => {
      if (!active) return;
      setSourceResult(null);
      if (reason.status !== 404) setError(reason.message);
    })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [initial.id, tableId, scopeKey, page, reload, sourceQueryKey, isExample]);
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
    if (writeInFlight.current) return;
    savePosition();
    onClose();
  }

  function changePage(next: number) {
    remember(readingPositions, readingKey, {page: next, scroll: 0});
    setPage(next);
  }

  async function saveCell(row: DataRowRead, column: TableColumnDef, field: string, input: string, before: string) {
    setEditing(null);
    if (!source || writeInFlight.current || input === before) return;
    const value = manualCellValue(input, column.value_type);
    if (example) {
      example.edit(row.id, column.key, value);
      return;
    }
    writeInFlight.current = true;
    setSaving(true);
    setFailedEdits(previous => {
      if (!(field in previous)) return previous;
      const next = { ...previous };
      delete next[field];
      return next;
    });
    savePosition();
    try {
      const updated = await updateDataRow(tableId, row.id, row.version,
        { [column.key]: value }, undefined, source.shared_header_keys);
      setSelectedDetails(previous => previous.map(item => {
        if (item.id === updated.id) return updated;
        return fileId && item.task_id === fileId ? { ...item, version: updated.version } : item;
      }));
      onChanged();
      setLoading(true);
      setReload(value => value + 1);
    } catch (reason) {
      const conflict = reason instanceof Error && "status" in reason && reason.status === 409;
      const message = conflict ? "未保存。该记录已更新，请核对后重试。"
        : reason instanceof Error ? reason.message : "修改未保存，请重试。";
      setFailedEdits(previous => ({ ...previous, [field]: { input, message } }));
      if (conflict) setReload(value => value + 1);
    } finally {
      writeInFlight.current = false;
      setSaving(false);
    }
  }

  async function remove() {
    if (!confirm || !source || writeInFlight.current) return;
    writeInFlight.current = true;
    setSaving(true);
    setRemoveError("");
    try {
      if (example) {
        example.remove(source, confirm === "clear" ? undefined : confirm);
      } else if (confirm === "clear") {
        await clearSourceCards(tableId, [{id: source.id, version: source.version}], scope);
      } else {
        await deleteDataRows(tableId, confirm.map(row => row.id), undefined,
          Object.fromEntries(confirm.map(row => [row.id, row.version])));
      }
      setConfirm(null);
      setSelectedDetails([]);
      onChanged();
      if (confirm === "clear" || source.total === confirm.length) onClose();
      else if (example) setPage(current => Math.min(current, Math.ceil((source.total - confirm.length) / source.page_size)));
      else setReload(value => value + 1);
    } catch (reason) {
      setRemoveError(reason instanceof Error ? reason.message : "删除未完成，请重试。");
    } finally {
      writeInFlight.current = false;
      setSaving(false);
    }
  }

  function fields(columns: TableColumnDef[], values: Record<string, unknown>, row?: DataRowRead) {
    return <SourceFieldList columns={columns} renderValue={column => {
      const field = JSON.stringify([row?.id, column.key]);
      const value = displayCardValue(values[column.key]);
      const failure = failedEdits[field];
      const active = editing === field;
      return <>
        {row ? <div className="source-editable-value" tabIndex={0}
          role="group" aria-label={`编辑 ${column.label}`}
          onClick={() => { if (!busy) setEditing(field); }}
          onKeyDown={event => {
            if (event.target === event.currentTarget && !busy && (event.key === "Enter" || event.key === "F2")) {
              event.preventDefault();
              setEditing(field);
            }
          }}>
          <EditableText ariaLabel={column.label} editing={active} value={active ? failure?.input ?? value : value || "—"}
            onCancel={() => setEditing(null)}
            onCommit={input => { if (active) void saveCell(row, column, field, input, value); }} />
        </div> : value || "—"}
        {failure && <p className="field-error" role="alert">{failure.message}
          <button type="button" className="btn ghost xs" disabled={busy} onClick={() => setEditing(field)}>重试</button>
        </p>}
      </>;
    }} />;
  }

  return <>
    <dialog ref={dialog} className={`content-card-dialog file-card-dialog${showOriginal ? " with-original" : ""}`} aria-labelledby="source-card-title" onCancel={event => { event.preventDefault(); close(); }}>
      <div className="content-card-detail-head"><h3 id="source-card-title" title={initial.filename}>{initial.filename}</h3><div className="content-card-detail-actions">
        {fileId && <><button className="btn secondary xs" aria-pressed={showOriginal} onClick={() => { savePosition(); setShowOriginal(!showOriginal); }}><Icon icon={FileText} size={14} />{showOriginal ? <><span className="compare-wide-label">收起对照</span><span className="compare-narrow-label">返回数据</span></> : "原件对照"}</button>
          <OriginalDownload url={originalUrl} filename={initial.filename} size="xs" />
          <button className="btn secondary xs" onClick={() => setShowHistory(true)}><Icon icon={History} size={14} />修订历史</button></>}
        {initial.item_count > 0 && <button className="btn danger-soft xs" type="button" disabled={busy || selectedDetails.length === 0}
          onClick={() => { setRemoveError(""); setConfirm(selectedDetails); }}>删除选中{selectedDetails.length > 0 && `（${selectedDetails.length}）`}</button>}
        <button className="btn ghost sm icon-only" aria-label="关闭来源详情" disabled={saving} onClick={close}><Icon icon={X} size={16} /></button>
      </div></div>
      <div ref={layout} className={`content-card-detail-layout${showOriginal ? " showing-original" : ""}`}><div ref={content} onScroll={savePosition} className="content-card-detail-content" style={showOriginal ? {flex: `0 0 ${splitRatio}%`} : undefined} aria-busy={busy}>
        {error && <div role="alert" className="callout danger">{error}<button className="btn secondary xs" onClick={() => setReload(value => value + 1)}>重新读取</button></div>}
        {loading && <p className="support">正在读取当前数据…</p>}
        {!loading && !source && !error && <p className="support" role="status">当前筛选范围内已没有此来源的数据。可以关闭详情查看其他结果。</p>}
        {source && <>
          {!source.complete && <p className="support">当前筛选匹配 {source.total} / {source.row_count} 条记录</p>}
          {Boolean(source.validation_issues?.length) && <Disclosure title="校验详情">
            <ValidationResults issues={(source.validation_issues ?? []).map((issue, index) => {
              const detail = /^items\[(\d+)\]\.(.+)$/.exec(issue.field);
              const field = detail?.[2] ?? issue.field.replace(/^header\./, "");
              const column = source.schema.find(column => column.key === field && column.section === (detail ? "item" : "header"));
              return {
                index, row: detail ? `明细第 ${Number(detail[1]) + 1} 行` : "文件级信息",
                field: column?.label ?? field, valueType: column?.value_type,
                message: issue.message, severity: issue.severity, rawValue: issue.raw_value,
                notChecked: issue.not_checked, ignored: !!issue.ignored, advisory: !source.review_pending,
              };
            })} />
          </Disclosure>}
          {!!headers.length && <section className="file-data-section">
            <div className="file-section-heading"><h4>文件级</h4></div>
            {fields(headers, source.header, source.complete ? source.rows[0] : undefined)}
          </section>}
          {!!source.item_count && !!details.length && <Disclosure defaultOpen title={`明细 · ${source.complete ? source.item_count : source.total} 条`}>
            {source.rows.filter(row => !row.is_header_only).map(row => <section className="file-data-section" key={row.id}>
              <div className="file-section-heading"><h4><input type="checkbox" aria-label={`选择明细 ${row.item_index}`}
                checked={selectedDetails.some(item => item.id === row.id)} disabled={busy}
                onChange={event => {
                  const checked = event.target.checked;
                  setSelectedDetails(previous => checked ? [...previous, row] : previous.filter(item => item.id !== row.id));
                }} />明细 {row.item_index}</h4></div>
              {fields(details, row.values, row)}
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
          <DocumentPreview contentType={originalType} filename={initial.filename} url={originalUrl} pageNumber={1} scale={zoomableImage ? originalScale : 1} flowPages />
        </div>
      </>}</div>
      <div className="file-detail-footer">
        <span className="support" role="status">{saving ? "正在保存…" : fileId ? "当前数据" : "独立数据"}</span>
        <DropdownMenu.Root><DropdownMenu.Trigger asChild><button className="btn ghost xs icon-only" aria-label="更多数据操作" disabled={busy || !source}><Icon icon={MoreHorizontal} size={16} /></button></DropdownMenu.Trigger>
          <DropdownMenu.Portal container={dialog.current}><DropdownMenu.Content className="record-menu" align="end" sideOffset={5}>
            <DropdownMenu.Item className="danger" disabled={!source?.complete} onSelect={() => { setRemoveError(""); setConfirm("clear"); }}><Icon icon={Trash2} size={14} />清空来源</DropdownMenu.Item>
            {!source?.complete && <p className="record-menu-note">当前仅匹配部分明细，不能清空整个来源。</p>}
          </DropdownMenu.Content></DropdownMenu.Portal>
        </DropdownMenu.Root>
      </div>
      <ConfirmDialog open={Boolean(confirm)} title={confirm === "clear" ? "清空此来源数据？" : "删除选中明细？"}
        description={confirm === "clear"
          ? `将清空此来源的 ${source?.row_count ?? 0} 条当前记录。原件和修订历史保留。`
          : `将删除选中的 ${confirm?.length ?? 0} 条明细。${fileId && source?.item_count === confirm?.length ? "最后一条明细删除后，文件级当前数据也会清空。" : ""}修订历史保留。`}
        buttonLabel={confirm === "clear" ? "确认清空" : "确认删除"} busy={busy} error={removeError}
        onClose={() => setConfirm(null)} onConfirm={() => void remove()} />
    </dialog>
    {showHistory && fileId && <FileRevisionHistory taskId={fileId} filename={initial.filename} onClose={() => setShowHistory(false)} />}
  </>;
}
