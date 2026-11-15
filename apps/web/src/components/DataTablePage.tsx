import { FileRevisionHistory } from "./FileRevisionHistory";
import { IndependentHistory } from "./IndependentHistory";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { enterWarehouseEditing } from "../api";
import { useAssistantPageContext } from "../assistant/AssistantProvider";
import { assistantApi } from "../assistant/api";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import {
  MoreHorizontal,
  Braces,
  CheckCheck,
  ChevronDown,
  Columns3,
  Download,
  FileSpreadsheet,
  FileText,
  GitMerge,
  History,
  Info,
  Loader2,
  Pencil,
  Plus,
  Search,
  Table2,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import {
  addDataRow,
  ApiError,
  addCustomColumn,
  createSplitViews,
  clearTableViews,
  createTable,
  clearSourceCards,
  deleteDataRows,
  deleteTable,
  deleteCustomColumn,
  getTable,
  getTableExportUrl,
  getTableNotesExportUrl,
  getTableViewsExportUrl,
  getTables,
  getTaskSummary,
  subscribeTaskEvents,
  getTableView,
  getTableViews,
  getTemplates,
  mergeTables,
  renameTable,
  responseErrorMessage,
  updateDataRow,
} from "../api";
import { desktopApi } from "../desktop";
import type {
  DataRowRead,
  DataTableDetail,
  DataTableRead,
  DataViewDetail,
  DataViewRead,
  ExtractionTemplate,
  TableColumnDef,
  SourceCard,
} from "../types";
import { Icon } from "./Icon";
import { CollapseChevron } from "./CollapseChevron";
import { EditableText } from "./EditableText";
import { SourceCardCollection } from "./SourceCardCollection";
import { WarehousePagination } from "./WarehousePagination";
import { EXAMPLE_WAREHOUSE_ID, EXAMPLE_WAREHOUSE_NAME, exampleColumns, exampleRows, editExampleRow, exampleSourceId, warehouseExampleCards, warehouseExampleDetail } from "./firstRunExampleData";
import { ImportSheetDialog } from "./ImportSheetDialog";
import { InputScopeDetails } from "./InputScopeDetails";
import { ConfirmDialog } from "./ConfirmDialog";
import { Toast, useToast } from "./Toast";
import { NoticeDialog } from "./NoticeDialog";
import { CheckboxOption } from "./CheckboxOption";
import { manualCellValue } from "../numericInput";
import { useModalFocus } from "../useModalFocus";
import { usePointerDrag } from "../usePointerDrag";
import "./warehouse-states.css";

// --- Types ---

interface ColumnDef {
  key: string;
  label: string;
  numeric: boolean;
  valueType: string;
  section?: string | null;
  width: number;
  userDefined: boolean;
}

interface UnsavedCellDraft {
  key: string;
  tableName: string;
  tableId: string;
  rowId: number;
  columnLabel: string;
  input: string;
}

const TABLE_PAGE_SIZE = 10;


// --- Helpers ---

function getCellValue(row: DataRowRead, colKey: string): string {
  const v = row.values[colKey];
  if (v == null) return "";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

interface DataTablePageProps {
  onNavigateExtract?: () => void;
  initialRowId?: number | null;
  initialTemplateId?: string | null;
  initialTableId?: string | null;
  jumpNotice?: string | null;
  /** 跳转来源任务：对应 task_id 的记录行高亮标记 */
  highlightTaskId?: string | null;
}

export function DataTablePage({
  onNavigateExtract,
  initialRowId = null,
  initialTemplateId = null,
  initialTableId = null,
  jumpNotice = null,
  highlightTaskId = null,
}: DataTablePageProps = {}) {
  const [importMenuOpen, setImportMenuOpen] = useState(false);
  const [pendingImport, setPendingImport] = useState<{file: File; tableId: string | null; sheets: string[]} | null>(null);
  const [exportMenuOpen, setExportMenuOpen] = useState(false);
  const [exportNotes, setExportNotes] = useState<{ tableId: string; name: string } | null>(null);
  const [exportRevisions, setExportRevisions] = useState(true);
  const [exportInputScope, setExportInputScope] = useState(true);
  const [exportNotesBusy, setExportNotesBusy] = useState(false);
  const [exportNotesError, setExportNotesError] = useState("");
  const [fieldFilterOpen, setFieldFilterOpen] = useState(false);
  const [addColumnOpen, setAddColumnOpen] = useState(false);
  const [newColumnLabel, setNewColumnLabel] = useState("");
  const [newColumnSection, setNewColumnSection] = useState<"header" | "item">("header");
  const [newColumnType, setNewColumnType] = useState<"text" | "number" | "date" | "boolean">("text");
  const [mergeModalOpen, setMergeModalOpen] = useState(false);
  const [splitModalOpen, setSplitModalOpen] = useState(false);
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
  const [deleteRowsConfirm, setDeleteRowsConfirm] = useState(false);
  const [deleteColumnTarget, setDeleteColumnTarget] = useState<ColumnDef | null>(null);
  const { toast, notify, clear } = useToast();
  useEffect(() => enterWarehouseEditing(), []);
  const [revisionsOpen, setRevisionsOpen] = useState(false);
  const [revisionSource, setRevisionSource] = useState<DataRowRead | null>(null);
  const [independentHistoryOpen, setIndependentHistoryOpen] = useState(false);

  const [tables, setTables] = useState<DataTableRead[]>([]);
  const [tablesLoading, setTablesLoading] = useState(true);
  const [tablesError, setTablesError] = useState<string | null>(null);
  const [tablesReload, setTablesReload] = useState(0);
  const [emptyWorkspaceConfirmed, setEmptyWorkspaceConfirmed] = useState<boolean | null>(null);

  const [currentTableId, setCurrentTableId] = useState<string | null>(null);
  const [localExampleRows, setLocalExampleRows] = useState(() => exampleRows);
  const [presentationOverrides, setPresentationOverrides] = useState<Record<string, "table" | "card">>({});
  const [tableDetail, setTableDetail] = useState<DataTableDetail | DataViewDetail | null>(null);
  const [tableLoading, setTableLoading] = useState(false);
  const [tableLoadError, setTableLoadError] = useState<string | null>(null);
  const [loadedDetailKey, setLoadedDetailKey] = useState<string | null>(null);
  const [tableError, setTableError] = useState<string | null>(null);
  const [noticesOpen, setNoticesOpen] = useState(false);
  const [unsavedCellDrafts, setUnsavedCellDrafts] = useState<UnsavedCellDraft[]>([]);
  const cellSaveAttempts = useRef(new Map<string, number>());
  useEffect(() => { if (tableError) notify(tableError, "error"); }, [tableError, notify]);
  useEffect(() => { if (jumpNotice) notify(jumpNotice, "info"); }, [jumpNotice, notify]);

  const [page, setPage] = useState(1);
  const [searchText, setSearchText] = useState("");
  const [reloadTrigger, setReloadTrigger] = useState(0);
  useEffect(() => {
    const changed = (event: Event) => {
      if ((event as CustomEvent<{ table_id?: string }>).detail?.table_id === currentTableId) {
        setReloadTrigger(n => n + 1);
        notify("数据已更新。", "success");
      }
    };
    window.addEventListener("zhiyi:assistant-changed", changed);
    return () => window.removeEventListener("zhiyi:assistant-changed", changed);
  }, [currentTableId]);
  const [views, setViews] = useState<DataViewRead[]>([]);
  const [moreViews, setMoreViews] = useState(false);
  const [viewsRefreshing, setViewsRefreshing] = useState(false);
  const [loadingMoreViews, setLoadingMoreViews] = useState(false);
  const viewsGeneration = useRef(0);
  const [currentViewId, setCurrentViewId] = useState<string | null>(null);
  const currentViewIdRef = useRef<string | null>(null);
  currentViewIdRef.current = currentViewId;
  const [splitFieldKey, setSplitFieldKey] = useState("");
  const [groupBy, setGroupBy] = useState<"none" | "file" | "field">("field");
  const [splitting, setSplitting] = useState(false);

  const [hiddenCols, setHiddenCols] = useState<string[]>([]);
  const [selectedRows, setSelectedRows] = useState<number[]>([]);
  const selectedRowSnapshots = useRef<Record<number, DataRowRead>>({});
  const [selectedSources, setSelectedSources] = useState<SourceCard[]>([]);
  const [clearSourcesConfirm, setClearSourcesConfirm] = useState(false);
  useEffect(() => {
    const selected = new Set(selectedRows);
    for (const id of Object.keys(selectedRowSnapshots.current)) {
      if (!selected.has(Number(id))) delete selectedRowSnapshots.current[Number(id)];
    }
  }, [selectedRows]);
  useEffect(() => { setSelectedSources([]); setClearSourcesConfirm(false); setDeleteRowsConfirm(false); setDeleteColumnTarget(null); }, [currentTableId, currentViewId, searchText]);
  useAssistantPageContext("tables", { table_id: currentTableId, table_name: tableDetail?.name,
    row_ids: currentTableId && selectedRows.length ? selectedRows : null, source_keys: currentTableId && selectedSources.length ? selectedSources.map(source => source.id) : null,
    view_id: currentViewId, search: searchText });
  const [editingCell, setEditingCell] = useState<string | null>(null);
  const editingRowSnapshot = useRef<{ row: DataRowRead; column: string } | null>(null);
  const [mergeSelection, setMergeSelection] = useState<string[]>([]);
  const [mergeName, setMergeName] = useState("");
  const [renamingSheet, setRenamingSheet] = useState(false);
  const [sheetNameInput, setSheetNameInput] = useState("");
  const [columnWidths, setColumnWidths] = useState<Record<string, number>>({});
  const [resizingColKey, setResizingColKey] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState(false);
  const [treeGroupsOpen, setTreeGroupsOpen] = useState<{ records: boolean; views: boolean }>({
    records: true,
    views: true,
  });

  // 新建表表单
  const [newTableName, setNewTableName] = useState("");
  const [newTableTemplate, setNewTableTemplate] = useState("");
  const [templates, setTemplates] = useState<ExtractionTemplate[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(true);
  const [templatesError, setTemplatesError] = useState<string | null>(null);
  const [templatesReload, setTemplatesReload] = useState(0);
  const [tablesRefreshError, setTablesRefreshError] = useState<string | null>(null);
  const [tablesRefreshing, setTablesRefreshing] = useState(false);
  const catalogGeneration = useRef(0);

  const cancelEditRef = useRef(false);
  const importInputRef = useRef<HTMLInputElement>(null);
  const importNewInputRef = useRef<HTMLInputElement>(null);
  const importGate = useRef(false);
  const [unconfirmedImport, setUnconfirmedImport] = useState<{filename: string; tableId: string | null} | null>(null);
  const importButtonRef = useRef<HTMLButtonElement>(null);
  const wasUnconfirmedImport = useRef(false);
  useEffect(() => {
    if (!unconfirmedImport && wasUnconfirmedImport.current) importButtonRef.current?.focus({preventScroll: true});
    wasUnconfirmedImport.current = Boolean(unconfirmedImport);
  }, [unconfirmedImport]);
  const newColumnInputRef = useRef<HTMLInputElement>(null);
  const tableMenuButtonRef = useRef<HTMLButtonElement>(null);
  const startPointerDrag = usePointerDrag();
  const menuFocusHandoff = useRef<(() => void) | null>(null);
  const mergeDialog = useModalFocus(mergeModalOpen, () => setMergeModalOpen(false), busyAction);
  const splitDialog = useModalFocus(splitModalOpen, () => setSplitModalOpen(false), splitting);
  const createDialog = useModalFocus(createModalOpen, () => setCreateModalOpen(false), busyAction);
  const deleteTableDialog = useModalFocus(deleteConfirmOpen, () => setDeleteConfirmOpen(false), busyAction, undefined, tableMenuButtonRef);
  const fieldDialog = useModalFocus(fieldFilterOpen, () => setFieldFilterOpen(false));
  const addColumnDialog = useModalFocus(addColumnOpen, () => setAddColumnOpen(false), busyAction, newColumnInputRef);

  const [sidebarWidth, setSidebarWidth] = useState(240);
  const resizeSidebar = (width: number) => setSidebarWidth(Math.max(180, Math.min(360, width)));

  // 加载表列表
  useEffect(() => {
    let active = true;
    const generation = ++catalogGeneration.current;
    setTablesLoading(true);
    setTablesError(null);
    setTablesRefreshError(null);
    getTables()
      .then((res) => {
        if (!active || generation !== catalogGeneration.current) return;
        setTables(res);
        if (res.length > 0) {
          // 优先用确认记录精确定位目标表；无则按模板匹配
          const normalizedTemplateId = initialTemplateId?.replace(/^builtin-/, "");
          const preferred =
            res.find((table) => table.id === initialTableId)
            ?? res.find(
              (table) =>
                table.template_key === initialTemplateId
                || table.template_key === normalizedTemplateId,
            );
          setCurrentTableId((prev) => res.some(table => table.id === prev) ? prev : preferred?.id ?? res[0].id);
        } else {
          setCurrentTableId(null);
        }
      })
      .catch((err) => { if (active && generation === catalogGeneration.current) setTablesError(err instanceof Error ? err.message : "加载表列表失败"); })
      .finally(() => { if (active && generation === catalogGeneration.current) setTablesLoading(false); });
    return () => { active = false; };
  }, [initialTemplateId, initialTableId, tablesReload]);

  useEffect(() => {
    let active = true;
    setEmptyWorkspaceConfirmed(null);
    if (!tablesLoading && !tablesError && tables.length === 0) {
      // A deleted/cleared warehouse can still have real file history.
      getTaskSummary().then(summary => { if (active) setEmptyWorkspaceConfirmed(summary.completed === 0 && summary.needs_review === 0); })
        .catch(() => { if (active) setEmptyWorkspaceConfirmed(false); });
    }
    return () => { active = false; };
  }, [tablesLoading, tablesError, tables.length, tablesReload]);

  useEffect(() => {
    if (tables.length || typeof EventSource === "undefined") return;
    return subscribeTaskEvents(events => {
      if (events.some(event => event.status === "completed" || event.status === "needs_review")) {
        setTablesReload(value => value + 1);
      }
    });
  }, [tables.length]);

  async function refreshTableCatalogAfterWrite(committed?: DataTableRead) {
    const generation = ++catalogGeneration.current;
    setTablesRefreshing(true);
    if (committed) setTables(current => [committed, ...current.filter(table => table.id !== committed.id)]);
    try {
      const catalog = await getTables();
      if (generation !== catalogGeneration.current) return;
      setTables(catalog);
      setTablesError(null);
      setTablesRefreshError(null);
    } catch (error) {
      if (generation === catalogGeneration.current) {
        setTablesRefreshError(`更改已保存，但数据表目录暂未刷新：${(error instanceof Error ? error.message : "读取失败").replace(/[。.!！]+$/u, "")}。请重新读取目录，无需重复提交。`);
      }
    } finally {
      if (generation === catalogGeneration.current) { setTablesLoading(false); setTablesRefreshing(false); }
    }
  }

  // 加载模板列表（新建表用）
  useEffect(() => {
    let active = true;
    setTemplatesLoading(true);
    setTemplatesError(null);
    getTemplates(true)
      .then(result => { if (active) setTemplates(result); })
      .catch(error => { if (active) setTemplatesError(error instanceof Error ? error.message : "字段模板读取失败"); })
      .finally(() => { if (active) setTemplatesLoading(false); });
    return () => { active = false; };
  }, [templatesReload]);

  const detailKey = JSON.stringify([currentTableId, currentViewId, page, searchText]);
  const detailMatches = Boolean(tableDetail && loadedDetailKey === detailKey);
  const showingExample = emptyWorkspaceConfirmed && !tablesLoading && !tablesError && tables.length === 0;
  const displayTableId = currentTableId ?? (showingExample ? EXAMPLE_WAREHOUSE_ID : null);
  const dataUnavailable = !showingExample && (!detailMatches || tablesLoading || Boolean(tablesError));
  const activeTable = tables.find(table => table.id === currentTableId);
  const isFileCollection = activeTable?.collection_kind === "files";
  const isMerge = Boolean(activeTable?.merge_sources);
  const appendUnavailable = isMerge ? "合并表只读，请到原表追加"
    : isFileCollection ? "文件卡片集合不支持表格追加"
    : showingExample || !currentTableId ? "请先选择普通数据表"
    : dataUnavailable ? "当前表数据暂不可用" : "";
  const mergeCandidates = tables.filter(table => table.collection_kind !== "files" && !table.merge_sources);
  const presentationMode = isFileCollection ? "card" : isMerge ? "table" : (displayTableId && presentationOverrides[displayTableId]) || (detailMatches ? tableDetail?.presentation?.mode : undefined) || "table";
  const cardArea = useRef<HTMLDivElement>(null);
  const pageSize = TABLE_PAGE_SIZE;
  useEffect(() => {
    if (!initialRowId || !currentTableId || currentTableId !== initialTableId) return;
    let cancelled = false;
    void assistantApi<{ page: number }>(`/references/tables/${encodeURIComponent(currentTableId)}/rows/${initialRowId}?page_size=${pageSize}`).then(result => {
      if (!cancelled) { setCurrentViewId(null); setSearchText(""); setPage(result.page); setSelectedRows([initialRowId]); setPresentationOverrides(previous => ({...previous, [currentTableId]: "table"})); }
    }).catch(error => { if (!cancelled) notify(error.message, "error"); });
    return () => { cancelled = true; };
  }, [initialRowId, currentTableId, initialTableId, pageSize]);
  useEffect(() => {
    const area = cardArea.current;
    if (!area?.animate || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const motion = area.animate([{ opacity: 0.45 }, { opacity: 1 }], { duration: 170, easing: "ease-out" });
    return () => motion.cancel();
  }, [presentationMode, currentTableId, currentViewId]);

  // 加载表详情（选中表 / 翻页 / 编辑刷新时触发）
  useEffect(() => {
    if (currentTableId === null) {
      setTableDetail(null);
      setLoadedDetailKey(null);
      setTableLoadError(null);
      setTableLoading(false);
      return;
    }
    let cancelled = false;
    setTableLoading(true);
    setTableLoadError(null);
    setTableError(null);
    const request = currentViewId
      ? getTableView(currentTableId, currentViewId, page, pageSize, searchText)
      : getTable(currentTableId, page, pageSize, searchText);
    request
      .then((detail) => {
        if (!cancelled) {
          setTableDetail(detail);
          setLoadedDetailKey(detailKey);
          setTableError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) setTableLoadError(err instanceof Error ? err.message : "加载表数据失败");
      })
      .finally(() => {
        if (!cancelled) setTableLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [currentTableId, currentViewId, page, pageSize, searchText, reloadTrigger, detailKey]);

  useEffect(() => {
    const generation = ++viewsGeneration.current;
    setMoreViews(false); setLoadingMoreViews(false);
    if (!currentTableId) {
      setViews([]);
      setViewsRefreshing(false);
      return;
    }
    let cancelled = false;
    setViewsRefreshing(true);
    void (async () => {
      try {
        const nextViews: DataViewRead[] = [];
        let offset = 0;
        let lastPageSize = 0;
        do {
          const pageViews = await getTableViews(currentTableId, offset);
          if (cancelled || generation !== viewsGeneration.current) return;
          offset += pageViews.length;
          nextViews.push(...pageViews.filter(view => !nextViews.some(existing => existing.id === view.id)));
          lastPageSize = pageViews.length;
          const selectedViewId = currentViewIdRef.current;
          if (!selectedViewId || nextViews.some(view => view.id === selectedViewId) || lastPageSize < 200) break;
        }
        while (lastPageSize === 200);

        if (cancelled || generation !== viewsGeneration.current) return;
        setViews(nextViews);
        setMoreViews(lastPageSize === 200);
        const selectedViewId = currentViewIdRef.current;
        if (selectedViewId && !nextViews.some(view => view.id === selectedViewId)) {
          // The selected dynamic group has lost its final member. Return the
          // whole surface to the table so its label, rows, page and export agree.
          setCurrentViewId(null);
          setPage(1);
          setSelectedRows([]);
          setSelectedSources([]);
          setTableError(null);
        }
      } catch (err) {
        if (!cancelled && generation === viewsGeneration.current) {
          setTableError(err instanceof Error ? err.message : "加载分 Sheet 失败");
        }
      } finally {
        if (!cancelled && generation === viewsGeneration.current) setViewsRefreshing(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [currentTableId, reloadTrigger]);

  async function loadMoreViews() {
    if (!currentTableId || loadingMoreViews) return;
    const generation = viewsGeneration.current;
    setLoadingMoreViews(true);
    try {
      const next = await getTableViews(currentTableId, views.length);
      if (generation !== viewsGeneration.current) return;
      setViews(previous => [...previous, ...next.filter(view => !previous.some(old => old.id === view.id))]);
      setMoreViews(next.length === 200);
    } catch (error) {
      if (generation === viewsGeneration.current) setTableError(error instanceof Error ? error.message : "读取分组失败");
    } finally { if (generation === viewsGeneration.current) setLoadingMoreViews(false); }
  }

  // 跳转定位：找到包含该任务记录的第一页并高亮（最多扫描 50 页）
  useEffect(() => {
    if (!highlightTaskId || !currentTableId || currentViewId) return;
    let cancelled = false;
    void (async () => {
      try {
        const first = await getTable(currentTableId, 1, pageSize);
        if (cancelled) return;
        const scanPages = Math.min(
          Math.max(1, Math.ceil(first.row_count / pageSize)),
          50,
        );
        for (let p = 1; p <= scanPages; p += 1) {
          const detail = p === 1 ? first : await getTable(currentTableId, p, pageSize);
          if (cancelled) return;
          if (detail.rows.some((r) => r.task_id === highlightTaskId)) {
            setPage(p);
            return;
          }
        }
      } catch {
        // 扫描失败静默，保留当前页
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [highlightTaskId, currentTableId, currentViewId, pageSize]);

  // Excel 手感：点击表格外部任意处退出编辑态
  useEffect(() => {
    function handleDocClick(event: MouseEvent) {
      const target = event.target as HTMLElement | null;
      if (!target) return;
      if (!target.closest(".data-table-wrap")) {
        setEditingCell(null);
      }
    }
    document.addEventListener("click", handleDocClick);
    return () => document.removeEventListener("click", handleDocClick);
  }, []);

  const columns: ColumnDef[] = useMemo(() => {
    return (showingExample ? exampleColumns : tableDetail?.columns ?? []).map((c: TableColumnDef) => ({
      key: c.key,
      label: c.label,
      numeric: c.value_type === "number",
      valueType: c.value_type,
      section: c.section,
      width: columnWidths[c.key] ?? (c.value_type === "number" ? 112 : 132),
      userDefined: Boolean(c.user_defined),
    }));
  }, [tableDetail, columnWidths, showingExample]);

  const visibleColumns = columns.filter((col) => !hiddenCols.includes(col.key));
  const widthFor = (col: ColumnDef) => columnWidths[col.key] ?? col.width;
  const leadingWidth = isMerge ? 160 : 44;
  const tableMinWidth = leadingWidth + visibleColumns.reduce((sum, col) => sum + widthFor(col), 0);
  const matchingExampleRows = useMemo(() => {
    const search = searchText.trim().toLowerCase();
    return search ? localExampleRows.filter(row => Object.values(row.values).some(value => String(value).toLowerCase().includes(search))) : localExampleRows;
  }, [searchText, localExampleRows]);
  const exampleCards = useMemo(() => showingExample ? warehouseExampleCards(localExampleRows, matchingExampleRows) : undefined,
    [showingExample, localExampleRows, matchingExampleRows]);
  const rows = showingExample ? matchingExampleRows.slice((page - 1) * pageSize, page * pageSize) : (tableDetail?.rows ?? []).slice(0, pageSize);

  const contentUnit = "行";
  const totalRows = showingExample ? matchingExampleRows.length : tableDetail?.row_count ?? 0;
  const totalPages = Math.max(1, Math.ceil(totalRows / pageSize));
  useEffect(() => {
    if (showingExample && page > totalPages) setPage(totalPages);
  }, [showingExample, page, totalPages]);

  const selectedCount = selectedRows.length;
  const allSelected = rows.length > 0 && rows.every((row) => selectedRows.includes(row.id));

  const currentSheetName =
    (showingExample ? EXAMPLE_WAREHOUSE_NAME : detailMatches ? tableDetail?.name : null) ?? views.find(view => view.id === currentViewId)?.name ?? tables.find((t) => t.id === currentTableId)?.name ?? "数据仓库";
  const currentSheetSource =
    tableDetail && "source_table_name" in tableDetail
      ? `分组 · ${tableDetail.source_table_name}${searchText.trim() ? " · 搜索结果" : ""}`
      : searchText.trim() ? "搜索结果" : "全部记录";

  const mergePreviewRows = mergeSelection.reduce(
    (sum, id) => sum + (tables.find((t) => t.id === id)?.row_count ?? 0),
    0,
  );

  // 多明细合并：按 task_id 连续分组合并抬头列
  const rowGroups = useMemo(() => {
    const groups: Array<{ key: string; rows: DataRowRead[] }> = [];
    for (const row of rows) {
      const independentGroup = row.values.__row_group;
      const source = row.task_id ? ["file", row.task_id]
        : typeof independentGroup === "string" && independentGroup ? ["group", independentGroup] : ["row", row.id];
      const key = JSON.stringify([row.source_table_id, ...source]);
      const last = groups[groups.length - 1];
      if (last && last.key === key) {
        last.rows.push(row);
      } else {
        groups.push({ key, rows: [row] });
      }
    }
    return groups;
  }, [rows]);

  const headerKeys = useMemo(
    () => new Set(columns.filter((c) => c.section === "header").map((c) => c.key)),
    [columns],
  );

  // 网格铺满：一页固定 10 行网格，有 x 行数据就补 10-x 个空网格行
  const placeholderCount = Math.max(0, pageSize - rows.length);
  const searchQuery = searchText.trim().toLowerCase();

  function selectTable(id: string) {
    if (id === currentTableId && !currentViewId) return;
    setCurrentTableId(id);
    setCurrentViewId(null);
    setViews([]);
    setMoreViews(false);
    setPage(1);
    setSearchText("");
    setSelectedRows([]);
    setEditingCell(null);
    setHiddenCols([]);
    setColumnWidths({});
    setTableError(null);
  }

  async function openMergedSource(row: DataRowRead) {
    if (!row.source_table_id) return;
    const tableId = row.source_table_id;
    try {
      const result = await assistantApi<{ page: number }>(`/references/tables/${encodeURIComponent(tableId)}/rows/${row.id}?page_size=${pageSize}`);
      selectTable(tableId);
      setPage(result.page);
      setSelectedRows([row.id]);
      setPresentationOverrides(previous => ({...previous, [tableId]: "table"}));
    } catch (error) {
      notify(error instanceof Error ? error.message : "无法打开原表", "error");
    }
  }

  function selectView(viewId: string) {
    setCurrentViewId(viewId);
    setPage(1);
    setSearchText("");
    setSelectedRows([]);
    setEditingCell(null);
  }

  function goToPage(p: number) {
    if (p < 1 || p > totalPages || p === page) return;
    if (selectedRows.length) notify("已切换页面，并清除上一页的明细选择。", "info");
    setPage(p);
    setSelectedRows([]);
    setEditingCell(null);
  }

  function toggleSelectAll() {
    setSelectedSources([]);
    if (allSelected) {
      setSelectedRows([]);
    } else {
      setSelectedRows(rows.map((row) => row.id));
    }
  }

  function toggleSelectRow(rowId: number) {
    setSelectedSources([]);
    setSelectedRows((current) =>
      current.includes(rowId)
        ? current.filter((id) => id !== rowId)
        : [...current, rowId],
    );
  }

  function toggleMergeSheet(id: string) {
    setMergeSelection((current) =>
      current.includes(id) ? current.filter((i) => i !== id) : [...current, id],
    );
  }

  function toggleCol(key: string) {
    setHiddenCols((current) =>
      current.includes(key) ? current.filter((k) => k !== key) : [...current, key],
    );
  }

  function startColumnResize(
    event: ReactPointerEvent<HTMLSpanElement>,
    key: string,
    initialWidth: number,
  ) {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const startX = event.clientX;
    setResizingColKey(key);
    const move = (moveEvent: PointerEvent) => {
      const nextWidth = Math.min(520, Math.max(72, initialWidth + moveEvent.clientX - startX));
      setColumnWidths((current) => ({ ...current, [key]: nextWidth }));
    };
    startPointerDrag(move, () => setResizingColKey(null), event.pointerId, event.currentTarget);
  }

  function resizeColumnWithKeyboard(
    event: ReactKeyboardEvent<HTMLSpanElement>,
    key: string,
    currentWidth: number,
  ) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const step = event.shiftKey ? 32 : 12;
    const direction = event.key === "ArrowRight" ? 1 : -1;
    setColumnWidths((current) => ({
      ...current,
      [key]: Math.min(520, Math.max(72, currentWidth + direction * step)),
    }));
  }

  function startRenameSheet() {
    setSheetNameInput(currentSheetName);
    setRenamingSheet(true);
  }

  async function commitRenameSheet() {
    setRenamingSheet(false);
    const nextName = sheetNameInput.trim();
    if (!nextName || !currentTableId || currentViewId || nextName === currentSheetName) {
      return;
    }
    try {
      const renamed = await renameTable(currentTableId, nextName);
      setReloadTrigger((n) => n + 1);
      await refreshTableCatalogAfterWrite(renamed);
    } catch (err) {
      setTableError(err instanceof Error ? err.message : "重命名失败");
    }
  }

  async function handleExport(format: "xlsx" | "csv" | "json" | "views" | "zip") {
    const activeDetailMatches = Boolean(tableDetail && detailMatches && (
      currentViewId
        ? "table_id" in tableDetail && tableDetail.table_id === currentTableId && tableDetail.id === currentViewId
        : !("table_id" in tableDetail) && tableDetail.id === currentTableId
    ));
    if (!currentTableId || tableLoading || viewsRefreshing || !activeDetailMatches) {
      setExportMenuOpen(false);
      return;
    }
    setExportMenuOpen(false);
    if (!isFileCollection && !isMerge && (format === "xlsx" || format === "views") && !visibleColumns.length) {
      notify("请至少显示一个字段再导出。");
      return;
    }
    if (format === "views" && views.length === 0) {
      setTableError(null);
      notify("当前数据表还没有分 Sheet，请先创建分 Sheet 后再导出。");
      return;
    }
    const scope = { viewId: currentViewId, search: searchText, rowIds: !isFileCollection && selectedRows.length ? selectedRows : null,
      sourceKeys: selectedSources.length ? selectedSources.map(source => source.id) : null,
      columns: !isFileCollection && (format === "xlsx" || format === "views")
        ? [...(isMerge ? ["__zhiyi_source_table"] : []), ...visibleColumns.map(column => column.key)] : undefined };
    const url = format === "views" ? getTableViewsExportUrl(currentTableId, scope) : getTableExportUrl(currentTableId, format, scope);
    const filename = `${currentSheetName || "数据表"}${searchText.trim() ? "-搜索结果" : ""}${selectedSources.length ? "-选中来源" : selectedRows.length && !isFileCollection ? "-选中明细" : ""}${format === "views" ? "-分Sheet.xlsx" : `.${format}`}`;
    await downloadExport(url, filename);
  }

  async function downloadExport(url: string, filename: string, onError = (message: string) => notify(message, "error")): Promise<boolean> {
    const bridge = desktopApi();
    try {
      const absoluteUrl = new URL(url, window.location.origin).toString();
      if (!bridge) {
        const response = await fetch(url);
        if (!response.ok) {
          throw new Error(await responseErrorMessage(response, "导出"));
        }
        const match = /filename\*=UTF-8''([^;]+)/i.exec(response.headers.get("Content-Disposition") || "");
        const objectUrl = URL.createObjectURL(await response.blob());
        const link = document.createElement("a");
        link.href = objectUrl; link.download = match ? decodeURIComponent(match[1]) : filename;
        document.body.appendChild(link); link.click(); link.remove();
        window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
        setTableError(null); notify("导出成功。", "success");
        return true;
      }
      const path = await bridge.export_table(absoluteUrl, filename);
      setTableError(null);
      if (path) notify(`已导出到 ${path}`, "success");
      return Boolean(path);
    } catch (err) {
      onError(err instanceof Error ? err.message : "导出失败");
      return false;
    }
  }

  async function importFile(file: File, tableId: string | null, sheetName?: string) {
    if (importGate.current) return;
    importGate.current = true;
    setImportMenuOpen(false);
    setBusyAction(true);
    setTableError(null);
    let rejected = false;
    let uncertain = false;
    try {
      const body = new FormData();
      body.append("file", file);
      if (sheetName) body.append("sheet_name", sheetName);
      const response = await fetch(tableId ? `/api/v1/tables/${tableId}/import` : "/api/v1/tables/import-new", {
        method: "POST",
        body,
      });
      const data = await response.clone().json().catch(() => null) as (DataTableRead & {detail?: unknown}) | null;
      const detail = data?.detail as {code?: string; sheets?: unknown} | undefined;
      if (response.status === 409 && detail?.code === "select_sheet" && Array.isArray(detail.sheets) && detail.sheets.every(sheet => typeof sheet === "string")) {
        setPendingImport({file, tableId, sheets: detail.sheets});
        return;
      }
      rejected = response.status >= 400 && response.status < 500 && ![408, 499].includes(response.status);
      if (!response.ok) throw new Error(await responseErrorMessage(response, "导入"));
      if (!data?.id) throw new Error("导入结果未能确认，请刷新数据表查看。");
      setPendingImport(null);
      if (!tableId) { setCurrentTableId(data.id); setCurrentViewId(null); }
      notify(`已导入「${data.name || "数据表"}」。`, "success");
      setReloadTrigger((n) => n + 1);
      await refreshTableCatalogAfterWrite(data);
    } catch (err) {
      if (rejected) {
        setTableError(err instanceof Error ? err.message : "导入失败");
      } else {
        // Losing a response does not prove the append was rolled back. In
        // particular, don't leave a worksheet dialog able to resubmit it.
        uncertain = true;
        setPendingImport(null);
        setUnconfirmedImport({filename: file.name, tableId});
        notify("导入结果待确认。请打开「仓库事项」核对。", "error");
      }
    } finally {
      if (!uncertain) importGate.current = false;
      setBusyAction(false);
      if (importInputRef.current) importInputRef.current.value = "";
      if (importNewInputRef.current) importNewInputRef.current.value = "";
    }
  }

  async function handleImportFile(file: File) {
    if (currentTableId && !isMerge && !isFileCollection) await importFile(file, currentTableId);
  }

  async function handleImportNewTable(file: File) {
    await importFile(file, null);
  }

  function beginCellEdit(row: DataRowRead, column: string, cell: string) {
    if (editingCell === cell) return;
    editingRowSnapshot.current = { row, column };
    cancelEditRef.current = false;
    setEditingCell(cell);
  }

  async function commitCellEdit(row: DataRowRead, colKey: string, newValue: string) {
    const snapshot = editingRowSnapshot.current;
    editingRowSnapshot.current = null;
    if (snapshot?.row.id === row.id && snapshot.column === colKey) row = snapshot.row;
    const oldValue = getCellValue(row, colKey);
    if (newValue === oldValue) return;
    const column = columns.find((item) => item.key === colKey);
    if (showingExample) {
      setLocalExampleRows(current => editExampleRow(current, row.id, colKey, manualCellValue(newValue, column?.valueType)));
      return;
    }
    if (!currentTableId || !tableDetail) return;
    const key = JSON.stringify([currentTableId, row.id, colKey]);
    const attempt = (cellSaveAttempts.current.get(key) ?? 0) + 1;
    cellSaveAttempts.current.set(key, attempt);
    const draft: UnsavedCellDraft = {key, tableId: currentTableId,
      tableName: tables.find(table => table.id === currentTableId)?.name ?? currentTableId,
      rowId: row.id, columnLabel: column?.label ?? colKey, input: newValue};
    try {
      const value = manualCellValue(newValue, column?.valueType);
      await updateDataRow(currentTableId, row.id, row.version, {
        [colKey]: value,
      }, undefined, row.shared_header_keys ?? undefined);
      if (cellSaveAttempts.current.get(key) !== attempt) return;
      setUnsavedCellDrafts(drafts => drafts.filter(item => item.key !== key));
      setReloadTrigger((n) => n + 1);
    } catch (err) {
      if (cellSaveAttempts.current.get(key) !== attempt) return;
      setUnsavedCellDrafts(drafts => [...drafts.filter(item => item.key !== key), draft]);
      if (err instanceof ApiError && err.status === 409) {
        notify("这行数据已被更新，本次修改未保存；请核对刷新后的值再修改。", "error");
        setReloadTrigger((n) => n + 1);
      } else {
        setTableError(err instanceof Error ? err.message : "保存失败");
      }
    }
  }

  async function handleAddColumn() {
    if (!currentTableId || !newColumnLabel.trim()) return;
    setBusyAction(true);
    setTableError(null);
    try {
      await addCustomColumn(currentTableId, newColumnLabel.trim(), newColumnSection, newColumnType);
      setAddColumnOpen(false);
      setNewColumnLabel("");
      setNewColumnSection("header");
      setNewColumnType("text");
      setReloadTrigger((n) => n + 1);
      notify("附加列已创建。", "success");
    } catch (err) {
      setTableError(err instanceof Error ? err.message : "新增列失败");
    } finally {
      setBusyAction(false);
    }
  }

  async function handleDeleteColumn(column: ColumnDef) {
    if (!currentTableId || !column.userDefined) return;
    setBusyAction(true);
    setTableError(null);
    try {
      await deleteCustomColumn(currentTableId, column.key);
      setDeleteColumnTarget(null);
      setReloadTrigger((n) => n + 1);
      notify(`已删除附加列“${column.label}”。`, "success");
    } catch (err) {
      setTableError(err instanceof Error ? err.message : "删除列失败");
    } finally {
      setBusyAction(false);
    }
  }

  async function handleCreateSplitViews() {
    if (!currentTableId || (groupBy === "field" && !splitFieldKey)) return;
    const generation = ++viewsGeneration.current;
    setLoadingMoreViews(false);
    setSplitting(true);
    setTableError(null);
    try {
      const created = groupBy === "none" ? (await clearTableViews(currentTableId), []) : await createSplitViews(currentTableId, groupBy === "file" ? "source_filename" : splitFieldKey, groupBy);
      if (generation !== viewsGeneration.current) return;
      setViews(created);
      setMoreViews(created.length === 200);
      notify(groupBy === "none" ? "已取消分组。" : "分组已保存。", "success");
      await refreshTableCatalogAfterWrite();
      if (generation !== viewsGeneration.current) return;
      setCurrentViewId(created[0]?.id ?? null);
      setPage(1);
      setSelectedRows([]);
      setSplitModalOpen(false);
      setSplitFieldKey("");
    } catch (err) {
      setTableError(err instanceof Error ? err.message : "创建分 Sheet 失败");
    } finally {
      setSplitting(false);
    }
  }

  async function handleMerge() {
    if (mergeSelection.length < 2 || !mergeName.trim()) return;
    setBusyAction(true);
    setTableError(null);
    try {
      const merged = await mergeTables(mergeSelection, mergeName.trim());
      setMergeModalOpen(false);
      setMergeSelection([]);
      setMergeName("");
      selectTable(merged.id);
      setTableError(null);
      await refreshTableCatalogAfterWrite(merged);
    } catch (err) {
      setTableError(err instanceof Error ? err.message : "合并失败");
    } finally {
      setBusyAction(false);
    }
  }

  async function handleCreateTable() {
    if (!newTableName.trim() || !newTableTemplate || templatesLoading || templatesError) return;
    setBusyAction(true);
    setTableError(null);
    try {
      const created = await createTable(newTableName.trim(), newTableTemplate);
      setCreateModalOpen(false);
      setNewTableName("");
      setNewTableTemplate("");
      selectTable(created.id);
      await refreshTableCatalogAfterWrite(created);
    } catch (err) {
      setTableError(err instanceof Error ? err.message : "新建表失败");
    } finally {
      setBusyAction(false);
    }
  }

  async function handleAddRow() {
    if (showingExample) {
      setLocalExampleRows(current => [...current, {
        id: Math.max(0, ...current.map(row => row.id)) + 1, version: 1, task_id: null, item_index: 1,
        created_at: "", updated_at: "", values: {},
      }]);
      setSearchText("");
      setPage(Math.ceil((localExampleRows.length + 1) / pageSize));
      return;
    }
    if (!currentTableId) return;
    setBusyAction(true);
    setTableError(null);
    try {
      await addDataRow(currentTableId, {});
      // 跳转到新行所在页（空行追加在末尾），让用户立即看到并直接编辑
      setPage(Math.max(1, Math.ceil((totalRows + 1) / pageSize)));
      setReloadTrigger((n) => n + 1);
      await refreshTableCatalogAfterWrite();
    } catch (err) {
      setTableError(err instanceof Error ? err.message : "新增行失败");
    } finally {
      setBusyAction(false);
    }
  }

  async function handleDeleteRows() {
    if (!selectedRows.length) return;
    if (showingExample) {
      setLocalExampleRows(current => current.filter(row => !selectedRows.includes(row.id)));
      setSelectedRows([]);
      setDeleteRowsConfirm(false);
      return;
    }
    if (!currentTableId) return;
    setBusyAction(true);
    setTableError(null);
    try {
      const rowIds = selectedRows.filter(
        (id): id is number => typeof id === "number",
      );
      const currentRows = new Map([...Object.values(selectedRowSnapshots.current), ...rows].map(row => [row.id, row]));
      await deleteDataRows(currentTableId, rowIds, undefined, Object.fromEntries(rowIds.flatMap(id => currentRows.has(id) ? [[id, currentRows.get(id)!.version]] : [])));
      setDeleteRowsConfirm(false);
      setSelectedRows([]);
      setReloadTrigger((n) => n + 1);
      await refreshTableCatalogAfterWrite();
    } catch (err) {
      setTableError(err instanceof Error ? err.message : "删除行失败");
    } finally {
      setBusyAction(false);
    }
  }

  async function handleClearSources() {
    if (!selectedSources.length) return;
    if (showingExample) {
      const selected = new Set(selectedSources.map(source => source.id));
      setLocalExampleRows(current => current.filter(row => !selected.has(exampleSourceId(row))));
      setSelectedSources([]);
      setClearSourcesConfirm(false);
      return;
    }
    if (!currentTableId) return;
    setBusyAction(true); setTableError(null);
    try {
      await clearSourceCards(currentTableId, selectedSources.map(({id, version}) => ({id, version})), {viewId: currentViewId, search: searchText});
      setSelectedSources([]); setClearSourcesConfirm(false); setReloadTrigger(n => n + 1);
      await refreshTableCatalogAfterWrite();
    } catch (err) { setTableError(err instanceof Error ? err.message : "清空未完成，请重试。"); }
    finally { setBusyAction(false); }
  }

  async function handleDeleteTable() {
    if (!currentTableId) return;
    setBusyAction(true);
    setTableError(null);
    try {
      await deleteTable(currentTableId);
      setDeleteConfirmOpen(false);
      setCurrentTableId(null);
      setCurrentViewId(null);
      setTableDetail(null);
      setTables(current => current.filter(table => table.id !== currentTableId));
      await refreshTableCatalogAfterWrite();
    } catch (err) {
      setTableError(err instanceof Error ? err.message : "删除表失败");
    } finally {
      setBusyAction(false);
    }
  }

  async function openRevisions(rowId: number) {
    if (!currentTableId) return;
    const source = rows.find((row) => row.id === rowId) ?? selectedRowSnapshots.current[rowId] ?? null;
    setRevisionSource(source);
    setRevisionsOpen(true);

  }

  // 全局行索引（rowGroups 展开时递增）
  let rowIndexCounter = 0;
  const staleTableError = detailMatches && !tablesError ? tableLoadError : null;
  const showTableSourceStatus = !dataUnavailable && presentationMode === "table";
  const pendingReviewCount = showTableSourceStatus ? rows.filter(row => row.review_pending).length : 0;
  const hasPartialInput = showTableSourceStatus && rows.some(row => row.input_scope?.coverage === "partial");
  const hasWarehouseNotices = Boolean(unconfirmedImport || tablesRefreshError || staleTableError || unsavedCellDrafts.length || pendingReviewCount || hasPartialInput);
  useEffect(() => { if (!hasWarehouseNotices) setNoticesOpen(false); }, [hasWarehouseNotices]);

  return (
    <div className={`view${presentationMode === "card" ? " presentation-card" : ""}`}>
      {noticesOpen && hasWarehouseNotices && <NoticeDialog title="仓库事项" onClose={() => setNoticesOpen(false)}>
      {unconfirmedImport && <section>
        <p>「{unconfirmedImport.filename}」的导入结果尚未确认，可能已经写入。请先重新读取数据核对，重复导入会新增记录。</p>
        <button className="btn secondary sm" aria-disabled={tablesLoading || tableLoading} onClick={() => {
          if (tablesLoading || tableLoading) return;
          if (unconfirmedImport.tableId) {
            setCurrentTableId(unconfirmedImport.tableId); setCurrentViewId(null); setPage(1); setSearchText("");
            setSelectedRows([]); setSelectedSources([]);
          }
          setTablesReload(value => value + 1); setReloadTrigger(value => value + 1);
          setNoticesOpen(false);
        }}>{unconfirmedImport.tableId ? "重新读取目标表核对" : "重新读取目录核对"}</button>
        <button className="btn ghost sm" disabled={tablesLoading || tableLoading} onClick={() => {
          importGate.current = false; setUnconfirmedImport(null);
        }}>已核对，继续操作</button>
      </section>}
      {tablesRefreshError && <section>
        <p>{tablesRefreshError}</p>
        <button className="btn secondary sm" disabled={tablesRefreshing} onClick={() => void refreshTableCatalogAfterWrite()}>重新读取数据表目录</button>
      </section>}
      {staleTableError && <section>
        <p>未能刷新，当前保留上次读取的内容。</p>
        <p>{staleTableError}</p>
        <button className="btn secondary sm" type="button" onClick={() => {
          setNoticesOpen(false); setReloadTrigger(value => value + 1);
        }}>重新读取表数据</button>
      </section>}
      {unsavedCellDrafts.length > 0 && <section>
        <h4>未保存输入（{unsavedCellDrafts.length}）</h4>
        <p>可复制输入后重新编辑。离开数据仓库会清除本页暂存。</p>
        {unsavedCellDrafts.map(draft => {
          const label = `${draft.tableName} · 行 ${draft.rowId} · ${draft.columnLabel}`;
          return <div key={draft.key} style={{marginTop: 12}}>
            <p title={`数据表：${draft.tableId}`}>{label}</p>
            <textarea className="form-input" aria-label={`未保存输入：${label}`} readOnly
              value={draft.input} rows={3} />
            <button type="button" className="btn secondary sm" aria-label={`丢弃未保存输入：${label}`}
              onClick={() => setUnsavedCellDrafts(drafts => drafts.filter(item => item.key !== draft.key))}>
              关闭此输入
            </button>
          </div>;
        })}
      </section>}
      {pendingReviewCount > 0 && (
        <section>
          <h4>本页有 {pendingReviewCount} 条来源待核对的数据</h4>
          <p>到文件历史可查看校验问题。</p>
          {rows.filter((row, index) => row.review_pending && !rows.slice(0, index).some((previous) => previous.review_pending &&
            (previous.task_id ?? previous.values.__row_group ?? previous.id) === (row.task_id ?? row.values.__row_group ?? row.id)
          )).map((row) => <p key={row.id}>{String(row.values.source_filename || `内容 ${row.id}`)}{!row.task_id && " · 来源已移除或来自导入/合并，保留原待核对状态"}</p>)}
        </section>
      )}
      {hasPartialInput && (
        <section>
          <h4>本页有局部读取的数据</h4>
          <p>这些结果只基于文件的部分内容，不能代表全文或全部明细。修改表中数据不会改变当时的读取范围。</p>
          {rows.filter((row, index) => row.input_scope?.coverage === "partial" && !rows.slice(0, index).some((previous) =>
            (previous.task_id ?? previous.values.__row_group ?? previous.id) === (row.task_id ?? row.values.__row_group ?? row.id)
            && JSON.stringify(previous.input_scope) === JSON.stringify(row.input_scope)
          )).map((row) => <InputScopeDetails key={row.id} scope={row.input_scope}
            label={String(row.values.source_filename || `内容 ${row.id}`)} originalAvailable={Boolean(row.task_id)} />)}
        </section>
      )}
      </NoticeDialog>}
      <div className="datastore-layout">
        {/* Toolbar */}
        <div className="datastore-toolbar">
          <div className="datastore-toolbar-left" style={{ gap: "calc(var(--space-4) * 2)" }}>
            {hasWarehouseNotices && <button className="btn ghost sm" type="button" aria-haspopup="dialog"
              onClick={() => { clear(); setNoticesOpen(true); }}>仓库事项</button>}
            {!isFileCollection && !showingExample && <>
            <button className="btn secondary sm" disabled={dataUnavailable} onClick={() => setFieldFilterOpen(true)}>
              <Icon icon={Columns3} size={15} /> 字段设置
              {hiddenCols.length > 0 && (
                <span className="filter-count-badge">{hiddenCols.length}</span>
              )}
            </button>
            <button
              className="btn secondary sm"
              disabled={isMerge || dataUnavailable || Boolean(currentViewId)}
              onClick={() => { setTableError(null); setAddColumnOpen(true); }}
              title={currentViewId ? "请回到原始数据表后新增列" : "新增不参与 AI 提取的附加列"}
            >
              <Icon icon={Plus} size={15} /> 新增列
            </button>
            <button
              className="btn secondary sm"
              disabled={isMerge || dataUnavailable}
              onClick={() => { setTableError(null); setSplitModalOpen(true); }}
            >
              <Icon icon={Table2} size={15} /> {presentationMode === "card" ? "分组" : "分 Sheet"}
            </button>
            <div className="toolbar-divider" />
            </>}
            {!isFileCollection && <button
              className="btn secondary sm"
              disabled={tablesLoading || Boolean(tablesError) || mergeCandidates.length < 2}
              onClick={() => { setTableError(null); setMergeModalOpen(true); }}
              title={mergeCandidates.length < 2 ? "至少需要两张原表才能合并" : undefined}
            >
              <Icon icon={GitMerge} size={15} /> 合并表
            </button>}
            <div className="toolbar-menu-wrap">
              <button
                ref={importButtonRef}
                className="btn secondary sm"
                disabled={tablesLoading || Boolean(tablesError) || busyAction || Boolean(unconfirmedImport)}
                onClick={() => setImportMenuOpen(!importMenuOpen)}
              >
                <Icon icon={Upload} size={15} /> 导入
                <Icon icon={ChevronDown} size={13} style={{ opacity: 0.6 }} />
              </button>
              <input
                ref={importInputRef}
                hidden
                type="file"
                accept=".xlsx,.csv"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void handleImportFile(file);
                }}
              />
              <input ref={importNewInputRef} hidden type="file" accept=".xlsx,.csv" onChange={(event) => { const file = event.target.files?.[0]; if (file) void handleImportNewTable(file); }} />
              {importMenuOpen && (
                <div className="toolbar-menu">
                  <button className="toolbar-menu-item" disabled={Boolean(appendUnavailable)} onClick={() => importInputRef.current?.click()}>
                    <Icon icon={FileSpreadsheet} size={15} /> 追加到当前表<span className="menu-hint">{appendUnavailable || "按列名匹配"}</span>
                  </button>
                  <button className="toolbar-menu-item" onClick={() => importNewInputRef.current?.click()}>
                    <Icon icon={FileText} size={15} /> 从文件新建表<span className="menu-hint">按文件表头建表</span>
                  </button>
                </div>
              )}
            </div>
            <div className="toolbar-menu-wrap">
              <button className="btn secondary sm" disabled={showingExample || tableLoading || viewsRefreshing || dataUnavailable} onClick={() => setExportMenuOpen(!exportMenuOpen)}>
                <Icon icon={Download} size={15} /> 导出
                <Icon icon={ChevronDown} size={13} style={{ opacity: 0.6 }} />
              </button>
              {exportMenuOpen && (
                <div className="toolbar-menu">
                  <div className="menu-hint" role="note">{selectedSources.length ? `选中 ${selectedSources.length} 个完整来源` : selectedRows.length && !isFileCollection ? `选中 ${selectedRows.length} 条明细` : searchText.trim() ? (isFileCollection ? "搜索匹配的文件" : "当前搜索结果") : currentViewId ? "当前分组" : "当前表全部数据"}</div>
                  <button className="toolbar-menu-item" onClick={() => handleExport(isFileCollection && (tables.find(table => table.id === currentTableId)?.file_count ?? 0) > 100 ? "zip" : "xlsx")}>
                    <Icon icon={FileSpreadsheet} size={15} /> 导出 Excel<span className="menu-hint">{isFileCollection ? ((tables.find(table => table.id === currentTableId)?.file_count ?? 0) > 100 ? "ZIP · 每包最多 100 份" : "XLSX · 每文件一页") : "XLSX"}</span>
                  </button>
                  {!isFileCollection && <><button
                    className="toolbar-menu-item"
                    disabled={views.length === 0}
                    onClick={() => handleExport("views")}
                    title={views.length === 0 ? "当前数据表还没有分 Sheet，请先创建分 Sheet" : undefined}
                  >
                    <Icon icon={FileSpreadsheet} size={15} /> 按分 Sheet 导出<span className="menu-hint">当前范围</span>
                  </button>
                  <button className="toolbar-menu-item" onClick={() => handleExport("csv")}>
                    <Icon icon={FileText} size={15} /> 导出 CSV (.csv)<span className="menu-hint">CSV</span>
                  </button></>}
                  <div className="toolbar-menu-sep" />
                  <button className="toolbar-menu-item" onClick={() => handleExport("json")}>
                    <Icon icon={Braces} size={15} /> 导出 JSON<span className="menu-hint">JSON</span>
                  </button>
                  <button className="toolbar-menu-item" onClick={() => {
                    const table = tables.find(item => item.id === currentTableId);
                    if (table) {
                      setExportMenuOpen(false);
                      setExportRevisions(true);
                      setExportInputScope(true);
                      setExportNotesError("");
                      setExportNotes({ tableId: table.id, name: table.name });
                    }
                  }}>
                    <Icon icon={FileText} size={15} /> 附加资料<span className="menu-hint">整表</span>
                  </button>
                </div>
              )}
            </div>
          </div>
          <div className="datastore-toolbar-right">
            <div className="toolbar-search">
              <Icon icon={Search} size={14} />
              <input
                aria-label="搜索当前表"
                type="text"
                className="form-input"
                placeholder="搜索当前表…"
                value={searchText}
                onChange={(event) => {
                  if (selectedRows.length || selectedSources.length) notify("搜索范围已改变，已清除之前的选择。", "info");
                  setSelectedRows([]);
                  setSelectedSources([]);
                  setSearchText(event.target.value);
                  setPage(1);
                }}
              />
            </div>
          </div>
        </div>

        <div className="datastore-compact-navigation">
          <label className="visually-hidden" htmlFor="compact-table">选择数据表或分组</label>
          <select id="compact-table" className="form-select" value={currentViewId && views.some(view => view.id === currentViewId) ? `view:${currentViewId}` : `table:${displayTableId ?? ""}`}
            onChange={(event) => { const value = event.target.value; if (value.startsWith("view:")) selectView(value.slice(5)); else if (value !== `table:${EXAMPLE_WAREHOUSE_ID}`) selectTable(value.slice(6)); }}>
            <optgroup label="数据表">{showingExample && <option value={`table:${EXAMPLE_WAREHOUSE_ID}`}>{EXAMPLE_WAREHOUSE_NAME}</option>}{tables.map((table) => <option key={table.id} value={`table:${table.id}`}>{table.name} · {table.collection_kind === "files" ? `${table.file_count ?? 0} 份文件` : `${table.row_count} 行`}</option>)}</optgroup>
            {views.length > 0 && <optgroup label="当前表的分组">{views.map((view) => <option key={view.id} value={`view:${view.id}`}>{view.name}</option>)}</optgroup>}
          </select>
          <button className="btn ghost sm icon-only" disabled={tablesLoading || Boolean(tablesError)} title="新建表" onClick={() => { setTableError(null); setNewTableName(""); setNewTableTemplate(""); setCreateModalOpen(true); }}><Icon icon={Plus} size={14} /></button>
        </div>
        {/* Sheet tree + detail + 可拖拽分隔条 */}
        <div className={`datastore-split${resizingColKey ? " dragging-col" : ""}`}>
          <div className="sheet-tree" style={{ width: `${sidebarWidth}px`, flexShrink: 0 }}>
            <div className="sheet-tree-head">
              <span className="sheet-tree-title">数据仓库</span>
              <div className="sheet-tree-add">
                <button
                  className="btn ghost sm icon-only"
                  disabled={tablesLoading || Boolean(tablesError)}
                  onClick={() => {
                    setNewTableName("");
                    setNewTableTemplate("");
                    setTableError(null);
                    setCreateModalOpen(true);
                  }}
                  title="新建表（基于模板创建空表）"
                >
                  <Icon icon={Plus} size={14} />
                </button>
              </div>
            </div>
            <div className="sheet-tree-body-flat">
              {tablesLoading ? (
                <div style={{ padding: 24, textAlign: "center" }}>
                  <Icon icon={Loader2} size={20} className="spin" />
                </div>
              ) : tablesError ? (
                <p className="support" style={{ margin: 12 }}>数据表列表暂不可用，请重新读取。</p>
              ) : (
                <>
                  <button
                    className="sheet-tree-group-title"
                    aria-expanded={treeGroupsOpen.records}
                    onClick={() =>
                      setTreeGroupsOpen((s) => ({ ...s, records: !s.records }))
                    }
                    title={treeGroupsOpen.records ? "收起原始记录" : "展开原始记录"}
                  >
                    <CollapseChevron expanded={treeGroupsOpen.records} size={16} />
                    <span className="sheet-tree-group-label">原始记录</span>
                  </button>
                  <div className={`sheet-tree-group-body${treeGroupsOpen.records ? " open" : ""}`} inert={!treeGroupsOpen.records} aria-hidden={!treeGroupsOpen.records}>
                    <div className="sheet-tree-group-inner">
                  {showingExample && <button type="button" className="sheet-tree-item active" aria-current="page">
                    <Icon icon={Table2} size={14} />
                    <span className="warehouse-example-name">{EXAMPLE_WAREHOUSE_NAME}</span>
                    <span className="row-count">{localExampleRows.length}</span>
                  </button>}
                  {tables.length === 0 && !showingExample && (
                    <div className="sheet-tree-empty">暂无数据表，先处理文件或新建表</div>
                  )}
                  {tables.map((t) => (
                    <button
                      key={t.id}
                      className={`sheet-tree-item${currentTableId === t.id && !currentViewId ? " active" : ""}`}
                      onClick={() => selectTable(t.id)}
                    >
                      <Icon icon={Table2} size={14} />
                      <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.name}</span>
                      <span className="row-count">{t.collection_kind === "files" ? `${t.file_count ?? 0} 份` : t.row_count}</span>
                    </button>
                  ))}
                    </div>
                  </div>
                  {!isFileCollection && !isMerge && <><button
                    className="sheet-tree-group-title"
                    aria-expanded={treeGroupsOpen.views}
                    onClick={() =>
                      setTreeGroupsOpen((s) => ({ ...s, views: !s.views }))
                    }
                    title={`${treeGroupsOpen.views ? "收起" : "展开"}${presentationMode === "card" ? "内容分组" : "分 Sheet 视图"}`}
                  >
                    <CollapseChevron expanded={treeGroupsOpen.views} size={16} />
                    <span className="sheet-tree-group-label">{presentationMode === "card" ? "内容分组" : "分 Sheet 视图"}</span>
                  </button>
                  <div className={`sheet-tree-group-body${treeGroupsOpen.views ? " open" : ""}`} inert={!treeGroupsOpen.views} aria-hidden={!treeGroupsOpen.views}>
                    <div className="sheet-tree-group-inner">
                  {currentTableId && views.length > 0 ? (
                    views.map((view) => (
                      <button
                        key={view.id}
                        className={`sheet-tree-item sheet-tree-view${currentViewId === view.id ? " active" : ""}`}
                        onClick={() => selectView(view.id)}
                      >
                        <Icon icon={FileSpreadsheet} size={13} />
                        <span className="sheet-view-name">{view.name}</span>
                        <span className="row-count">{view.row_count}</span>
                      </button>
                    ))
                  ) : (
                    <div className="sheet-tree-empty">
                      {currentTableId ? (tables.find(table => table.id === currentTableId)?.grouping_fields?.length ? "分组规则已保存，当前暂无分类数据" : "尚未分组，可从上方“分组”选择文件或字段") : "选择一张表查看其视图"}
                    </div>
                  )}
                  {moreViews && <button className="sheet-tree-item" disabled={loadingMoreViews} onClick={() => void loadMoreViews()}>{loadingMoreViews ? "正在读取…" : "更多分组"}</button>}
                    </div>
                  </div></>}
                </>
              )}
            </div>
          </div>

          <div className="splitter-v" role="separator" aria-label="调整数据表列表宽度" aria-orientation="vertical"
            aria-valuemin={180} aria-valuemax={360} aria-valuenow={sidebarWidth} tabIndex={0}
            onPointerDown={event => {
              if (event.button !== 0) return;
              event.preventDefault();
              const startX = event.clientX;
              const initialWidth = sidebarWidth;
              setResizingColKey(null);
              startPointerDrag(move => resizeSidebar(initialWidth + move.clientX - startX), undefined, event.pointerId, event.currentTarget);
            }}
            onKeyDown={event => {
              if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
              event.preventDefault(); resizeSidebar(event.key === "Home" ? 180 : event.key === "End" ? 360 : sidebarWidth + (event.key === "ArrowLeft" ? -12 : 12));
            }} />

          <div className="sheet-detail">
            <div className="panel-title" style={{ marginBottom: 0 }}>
              <div>
                <div className="warehouse-sheet-name-line">
                  {!renamingSheet ? (
                    <h3
                      onClick={() => {
                        if (!currentViewId && currentTableId && !dataUnavailable) startRenameSheet();
                      }}
                      style={{ cursor: !currentTableId || currentViewId || dataUnavailable ? "default" : "text" }}
                      title={!currentTableId || currentViewId || dataUnavailable ? undefined : "点击重命名"}
                    >
                      {currentSheetName}
                    </h3>
                  ) : (
                    <input
                      aria-label="表名称"
                      className="sheet-name-input"
                      type="text"
                      autoFocus
                      value={sheetNameInput}
                      onChange={(e) => setSheetNameInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          void commitRenameSheet();
                        } else if (e.key === "Escape") {
                          setRenamingSheet(false);
                        }
                      }}
                      onBlur={() => void commitRenameSheet()}
                    />
                  )}
                  {showingExample && <span className="warehouse-example-note">改动仅本页，提取首条真实数据后隐藏</span>}
                </div>
                <div className="support">{dataUnavailable
                  ? tablesError || tableLoadError ? "数据暂时无法读取" : tablesLoading || currentTableId ? "正在读取数据…" : "处理文件或导入表格后，在这里核对和整理。"
                  : <>{tableLoadError && "上次读取的内容 · "}{isFileCollection ? `字段按文件保存 · 共 ${tables.find(table => table.id === currentTableId)?.file_count ?? 0} 份文件` : <>共 <span>{totalRows}</span> {contentUnit} · <span>{currentSheetSource}</span></>}</>}</div>
              </div>
              <div className="sheet-detail-actions">
                {!currentViewId && currentTableId && !dataUnavailable ? (
                  <DropdownMenu.Root>
                    <DropdownMenu.Trigger asChild><button ref={tableMenuButtonRef} className="btn ghost sm icon-only" aria-label="数据表操作"><Icon icon={MoreHorizontal} size={18} /></button></DropdownMenu.Trigger>
                    <DropdownMenu.Portal><DropdownMenu.Content className="record-menu" align="end" sideOffset={6}
                      onCloseAutoFocus={event => {
                        const handoff = menuFocusHandoff.current;
                        // Open the next focus owner only after Radix releases its menu scope.
                        if (handoff) { event.preventDefault(); menuFocusHandoff.current = null; queueMicrotask(handoff); }
                      }}>
                      <DropdownMenu.Item onSelect={() => { menuFocusHandoff.current = startRenameSheet; }}><Icon icon={Pencil} size={14} />重命名</DropdownMenu.Item>
                      {!isFileCollection && !isMerge && <DropdownMenu.Item onSelect={() => {
                        menuFocusHandoff.current = () => setIndependentHistoryOpen(true);
                      }}><Icon icon={History} size={14} />独立数据历史</DropdownMenu.Item>}
                      <DropdownMenu.Separator />
                      <DropdownMenu.Item className="danger" onSelect={() => {
                        menuFocusHandoff.current = () => { setTableError(null); setDeleteConfirmOpen(true); };
                      }}><Icon icon={Trash2} size={14} />{isMerge ? "删除合并表" : "删除数据表"}</DropdownMenu.Item>
                    </DropdownMenu.Content></DropdownMenu.Portal>
                  </DropdownMenu.Root>
                ) : null}
              </div>
            </div>

            <div className="data-table-wrap">
              {/* 行级操作栏 */}
              {!dataUnavailable && <div className="row-actions-bar">
                <div className="row-actions-left">
                  {presentationMode === "card" ? <button className="btn xs danger-soft"
                    disabled={busyAction || selectedSources.length === 0} onClick={() => setClearSourcesConfirm(true)}>
                    <Icon icon={Trash2} size={14} /> 清空来源
                  </button> : <button className="btn xs danger-soft"
                    disabled={isMerge || busyAction || selectedRows.length === 0} onClick={() => setDeleteRowsConfirm(true)}>
                    <Icon icon={Trash2} size={14} /> 删除选中
                  </button>}
                  {!isFileCollection && <>
                  {!showingExample && <button
                    className="btn xs secondary"
                    disabled={isMerge || selectedRows.length !== 1}
                    onClick={() => {
                      const row = rows.find((r) => r.id === selectedRows[0]) ?? selectedRowSnapshots.current[selectedRows[0]];
                      if (row) void openRevisions(row.id);
                    }}
                    title={selectedRows.length !== 1 ? "先勾选一行查看其修订历史" : "查看选中行的修订历史"}
                  >
                    <Icon icon={History} size={14} /> 修订历史
                  </button>}
                  <button
                    className="btn xs primary"
                    disabled={isMerge || busyAction}
                    onClick={() => void handleAddRow()}
                    title={presentationMode === "card" ? "新增空白内容，展开卡片后填写" : "在表尾新增一行空记录，直接在表格里填写"}
                  >
                    <Icon icon={Plus} size={14} /> {presentationMode === "card" ? "新增记录" : "新增一行"}
                  </button>
                  </>}
                </div>
                <div className="row-actions-right">
                  {!isFileCollection && !isMerge && <div className="presentation-switch" aria-label="内容展示方式">
                    {(["table", "card"] as const).map((mode) => <button key={mode} className={`btn xs ${presentationMode === mode ? "primary" : "ghost"}`}
                      disabled={!displayTableId} aria-pressed={presentationMode === mode}
                      onClick={() => {
                        if (!displayTableId || mode === presentationMode) return;
                        setSelectedRows([]);
                        setSelectedSources([]);
                        setPresentationOverrides(previous => ({ ...previous, [displayTableId]: mode }));
                      }}>
                      {mode === "table" ? "表格" : "卡片"}
                    </button>)}
                  </div>}
                  {selectedSources.length > 0 && <span className="row-selected-hint">已选 <strong>{selectedSources.length}</strong> 个完整来源 · <button className="link-btn" onClick={() => setSelectedSources([])}>取消</button></span>}
                  {selectedCount > 0 && (
                    <span className="row-selected-hint">已选 <strong>{selectedCount}</strong> 行 · <button className="link-btn" onClick={() => setSelectedRows([])}>取消</button></span>
                  )}
                  {presentationMode === "table" && <span className="row-total-hint">共 <span>{totalRows}</span> {contentUnit}</span>}
                </div>
              </div>}
              <div className="data-table-scroll" ref={cardArea}>
                {tablesError || (tableLoadError && !detailMatches) ? (
                  <div className="table-empty-state warehouse-read-state" role="alert">
                    <h3>暂时无法读取{tablesError ? "数据表列表" : "这张表"}</h3>
                    <p className="support">{tablesError || tableLoadError}</p>
                    <p className="support">尚未确认当前数据，请重新读取后继续。</p>
                    <button className="btn secondary" type="button" onClick={() => {
                      if (tablesError) setTablesReload(value => value + 1);
                      else setReloadTrigger(value => value + 1);
                    }}>{tablesError ? "重新读取数据表" : "重新读取表数据"}</button>
                  </div>
                ) : tablesLoading || (currentTableId && !detailMatches) || (!currentTableId && tables.length === 0 && emptyWorkspaceConfirmed === null) ? (
                  <div className="table-empty-state warehouse-read-state" role="status">
                    <Icon icon={Loader2} size={24} className="spin" /><span>正在读取数据…</span>
                  </div>
                ) : !displayTableId ? (
                  <div className="table-empty-state warehouse-read-state">
                    <Icon icon={Table2} size={28} /><h3>还没有数据表</h3>
                    <p className="support">可以先提取一份文件，或从上方导入已有表格。</p>
                    {onNavigateExtract && <button className="btn primary" type="button" onClick={onNavigateExtract}>去文件提取</button>}
                  </div>
                ) : presentationMode === "card" ? (
                  <SourceCardCollection key={`${displayTableId}:${currentViewId}`} tableId={displayTableId} scope={{viewId: currentViewId, search: searchText}}
                    example={exampleCards && {
                      cards: exampleCards,
                      detail: (card, page) => warehouseExampleDetail(card, matchingExampleRows, page),
                      edit: (id, key, value) => setLocalExampleRows(current => editExampleRow(current, id, key, value)),
                      remove: (card, details) => setLocalExampleRows(current => current.filter(row =>
                        details ? !details.some(detail => detail.id === row.id) : exampleSourceId(row) !== card.id)),
                    }}
                    reload={reloadTrigger} selected={selectedSources} onSelect={sources => { setSelectedSources(sources); setSelectedRows([]); }}
                    onChanged={() => { setSelectedSources([]); setSelectedRows([]); setReloadTrigger(n => n + 1); }} hiddenFields={hiddenCols} highlightTaskId={highlightTaskId} />
                ) : rows.length === 0 && columns.length === 0 ? (
                  <div className="table-empty-state">
                    <Icon icon={Table2} size={28} />
                    <div>这张表还没有数据</div>
                    {!isMerge && !showingExample && <div className="small muted">使用“新增一行”或“导入”开始填写</div>}
                  </div>
                ) : (
                  <table className="data-table" style={{ minWidth: `${tableMinWidth}px` }}>
                    <colgroup>
                      <col style={{ width: leadingWidth }} />
                      {visibleColumns.map((col) => (
                        <col key={col.key} style={{ width: widthFor(col) }} />
                      ))}
                    </colgroup>
                    <thead>
                      <tr>
                        <th className={isMerge ? "" : "th-check"} style={{ width: leadingWidth }}>
                          {isMerge ? "原表" : <input aria-label="全选或取消全选" type="checkbox" checked={allSelected} onChange={() => toggleSelectAll()} title="全选 / 取消全选" />}
                        </th>
                        {visibleColumns.map((col) => (
                          <th
                            key={col.key}
                            className={resizingColKey === col.key ? "resizing" : ""}
                            style={{ width: `${widthFor(col)}px`, textAlign: col.numeric ? "right" : "left" }}
                          >
                            <span>{col.label}</span>
                            <span
                              aria-label={`调整“${col.label}”列宽`}
                              aria-orientation="vertical"
                              className="column-resizer"
                              onKeyDown={(event) =>
                                resizeColumnWithKeyboard(event, col.key, widthFor(col))
                              }
                              onPointerDown={(event) =>
                                startColumnResize(event, col.key, widthFor(col))
                              }
                              role="separator"
                              tabIndex={0}
                            />
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {rowGroups.map((group) =>
                        group.rows.map((row) => {
                          const idx = rowIndexCounter++;
                          const isGroupFirst = row.id === group.rows[0].id;
                          const isHighlight = highlightTaskId != null && row.task_id === highlightTaskId;
                          return (
                            <tr
                              key={row.id}
                              className={`${selectedRows.includes(row.id) ? "checked" : ""}${isHighlight ? " is-highlight" : ""}`}
                              title={isMerge ? undefined : "单击单元格直接编辑 · 勾选框用于批量选择"}
                            >
                              {isMerge ? <td>
                                <button type="button" className="btn ghost xs merge-source-link"
                                  onClick={() => void openMergedSource(row)}>{tables.find(table => table.id === row.source_table_id)?.name ?? "原表"}</button>
                              </td> : <td className="td-check">
                                <input aria-label="勾选此行加入批量" type="checkbox" checked={selectedRows.includes(row.id)} onChange={() => toggleSelectRow(row.id)} title="勾选加入批量" />
                              </td>}
                              {visibleColumns.map((col) => {
                                const cellText = getCellValue(row, col.key);
                                const isMatch =
                                  searchQuery !== "" &&
                                  cellText.toLowerCase().includes(searchQuery);
                                const shared = headerKeys.has(col.key) && (row.shared_header_keys?.includes(col.key) ?? true);
                                if (isMerge) {
                                  if (shared && !isGroupFirst) return null;
                                  return <td key={col.key} rowSpan={shared ? group.rows.length : undefined}
                                    className={`${col.numeric ? "numeric " : ""}${isMatch ? "cell-match" : ""}`}
                                    title={cellText}>{cellText}</td>;
                                }
                                if (shared) {
                                  if (!isGroupFirst) {
                                    return null;
                                  }
                                  return (
                                    <td
                                      aria-label={`编辑 ${col.label}，当前值 ${getCellValue(row, col.key) || "空"}`}
                                      key={col.key}
                                      rowSpan={group.rows.length}
                                      className={`${editingCell === `${idx}-${col.key}` ? "focused" : ""}${col.numeric ? " numeric" : ""} header-cell${isMatch ? " cell-match" : ""}`}
                                      onClick={() => {
                                        beginCellEdit(row, col.key, `${idx}-${col.key}`);
                                      }}
                                      onKeyDown={(event) => {
                                        if (event.target !== event.currentTarget) return;
                                        if (event.key === "Enter" || event.key === "F2") {
                                          event.preventDefault();
                                          beginCellEdit(row, col.key, `${idx}-${col.key}`);
                                        }
                                      }}
                                      tabIndex={0}
                                      title={getCellValue(row, col.key)}
                                    >
                                      <EditableText
                                        editing={editingCell === `${idx}-${col.key}`}
                                        value={getCellValue(row, col.key)}
                                        onCancel={() => {
                                          cancelEditRef.current = true;
                                          setEditingCell(null);
                                        }}
                                        onCommit={(raw) => {
                                          if (editingCell === `${idx}-${col.key}`) {
                                            const cancelled = cancelEditRef.current;
                                            cancelEditRef.current = false;
                                            setEditingCell(null);
                                            if (!cancelled) {
                                              void commitCellEdit(row, col.key, raw);
                                            }
                                          }
                                        }}
                                      />
                                    </td>
                                  );
                                }
                                return (
                                  <td
                                    aria-label={`编辑 ${col.label}，当前值 ${getCellValue(row, col.key) || "空"}`}
                                    key={col.key}
                                    className={`${editingCell === `${idx}-${col.key}` ? "focused" : ""}${col.numeric ? " numeric" : ""}${isMatch ? " cell-match" : ""}`}
                                    onClick={() => {
                                      beginCellEdit(row, col.key, `${idx}-${col.key}`);
                                    }}
                                    onKeyDown={(event) => {
                                      if (event.target !== event.currentTarget) return;
                                      if (event.key === "Enter" || event.key === "F2") {
                                        event.preventDefault();
                                        beginCellEdit(row, col.key, `${idx}-${col.key}`);
                                      }
                                    }}
                                    tabIndex={0}
                                    title={getCellValue(row, col.key)}
                                  >
                                    <EditableText
                                      editing={editingCell === `${idx}-${col.key}`}
                                      value={getCellValue(row, col.key)}
                                      onCancel={() => {
                                        cancelEditRef.current = true;
                                        setEditingCell(null);
                                      }}
                                      onCommit={(raw) => {
                                        if (editingCell === `${idx}-${col.key}`) {
                                          const cancelled = cancelEditRef.current;
                                          cancelEditRef.current = false;
                                          setEditingCell(null);
                                          if (!cancelled) {
                                            void commitCellEdit(row, col.key, raw);
                                          }
                                        }
                                      }}
                                    />
                                  </td>
                                );
                              })}
                            </tr>
                          );
                        }),
                      )}
                      {Array.from({ length: placeholderCount }).map((_, index) => (
                        <tr
                          key={`placeholder-${index}`}
                          className="grid-placeholder"
                          onClick={() => setEditingCell(null)}
                        >
                          <td className="td-check" />
                          {visibleColumns.map((col) => (
                            <td key={col.key} />
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
              {presentationMode === "table" && !dataUnavailable &&
                <WarehousePagination key={`${displayTableId}:${currentViewId}`} page={page} pageCount={totalPages} onChange={goToPage}
                  summary={<>显示 {rows.length === 0 ? 0 : (page - 1) * pageSize + 1}-{(page - 1) * pageSize + rows.length} {contentUnit}，共 {totalRows} {contentUnit}</>} />}
            </div>
          </div>
        </div>
      </div>

      {/* 合并表模态 */}
      {mergeModalOpen && (
        <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget && !busyAction) setMergeModalOpen(false); }}>
          <div className="modal-card" role="dialog" aria-modal="true" aria-labelledby="warehouse-merge-title" {...mergeDialog} onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <div>
                <div className="modal-eyebrow"><Icon icon={GitMerge} size={15} /> 合并表</div>
                <h3 id="warehouse-merge-title">选择需要合并的数据表</h3>
                <div className="support">按勾选顺序排列记录。合并表随原表更新。</div>
              </div>
              <button aria-label="关闭合并表" disabled={busyAction} className="btn ghost sm icon-only" onClick={() => setMergeModalOpen(false)}>
                <Icon icon={X} size={16} />
              </button>
            </div>
            <div className="modal-body">
              <div className="merge-sheet-list">
                {mergeCandidates.map((s) => (
                  <label className={`merge-sheet-row${mergeSelection.includes(s.id) ? " checked" : ""}`} key={s.id}>
                    <input type="checkbox" disabled={busyAction} checked={mergeSelection.includes(s.id)} onChange={() => toggleMergeSheet(s.id)} />
                    <Icon icon={Table2} size={15} style={{ color: "var(--color-text-muted)" }} />
                    <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.name}</span>
                    <span className="row-count">{mergeSelection.includes(s.id) && `第 ${mergeSelection.indexOf(s.id) + 1} 张 · `}{s.row_count} 行</span>
                  </label>
                ))}
              </div>
              <div className="merge-name-row">
                <label className="form-label" htmlFor="merge-table-name">合并表名称</label>
                <input aria-label="合并表名称" disabled={busyAction} className="form-input" type="text" id="merge-table-name" value={mergeName} onChange={(e) => setMergeName(e.target.value)} placeholder="为合并后的表命名" />
              </div>
              <div className="merge-summary">
                <div className="merge-summary-stat">
                  <span className="num">{mergeSelection.length}</span>
                  <span className="label">已选表</span>
                </div>
                <div className="merge-summary-stat">
                  <span className="num">{mergePreviewRows}</span>
                  <span className="label">合并后行数</span>
                </div>
                {mergeSelection.length < 2 && (
                  <div className="merge-summary-hint">
                    <Icon icon={Info} size={14} /> 至少选择 2 张表才能合并
                  </div>
                )}
              </div>
              {tableError && <p role="alert" className="callout danger">{tableError}</p>}
            </div>
            <div className="modal-foot">
              <button className="btn ghost" disabled={busyAction} onClick={() => setMergeModalOpen(false)}>取消</button>
              <button
                className="btn primary"
                disabled={mergeSelection.length < 2 || !mergeName.trim() || busyAction}
                onClick={() => void handleMerge()}
              >
                {busyAction ? <Icon icon={Loader2} size={16} className="spin" /> : <Icon icon={GitMerge} size={16} />}
                确认合并
              </button>
            </div>
          </div>
        </div>
      )}

      {splitModalOpen && (
        <div
          className="modal-overlay"
          onClick={(event) => {
            if (event.target === event.currentTarget && !splitting) setSplitModalOpen(false);
          }}
        >
          <div className="modal-card" role="dialog" aria-modal="true" aria-labelledby="warehouse-split-title" {...splitDialog} onClick={(event) => event.stopPropagation()} style={{ maxWidth: 480 }}>
            <div className="modal-head">
              <div>
                <div className="modal-eyebrow"><Icon icon={Table2} size={15} /> 内容分组</div>
                <h3 id="warehouse-split-title">选择分组方式</h3>
                <div className="support">按来源文件或字段浏览，数据仍只保存一份。</div>
              </div>
              <button aria-label="关闭分组设置" disabled={splitting} className="btn ghost sm icon-only" onClick={() => setSplitModalOpen(false)}>
                <Icon icon={X} size={16} />
              </button>
            </div>
            <div className="modal-body">
              <label className="form-field"><span className="form-label">分组方式</span>
                <select className="form-select" disabled={splitting} value={groupBy} onChange={(event) => setGroupBy(event.target.value as "none" | "file" | "field")}>
                  <option value="none">取消全部分组</option><option value="file">按文件或独立来源组</option><option value="field">按字段</option>
                </select>
              </label>
              {groupBy === "field" && <label className="form-field">
                <span className="form-label">用于分类的字段</span>
                <select
                  className="form-select"
                  disabled={splitting}
                  value={splitFieldKey}
                  onChange={(event) => setSplitFieldKey(event.target.value)}
                >
                  <option value="">请选择字段</option>
                  {columns.map((column) => (
                    <option key={column.key} value={column.key}>{column.label}</option>
                  ))}
                </select>
              </label>}
              {tableError && <p role="alert" className="callout danger">{tableError}</p>}
            </div>
            <div className="modal-foot">
              <button className="btn ghost" disabled={splitting} onClick={() => setSplitModalOpen(false)}>取消</button>
              <button
                className="btn primary"
                disabled={(groupBy === "field" && !splitFieldKey) || splitting}
                onClick={() => void handleCreateSplitViews()}
              >
                {splitting ? <Icon icon={Loader2} size={15} className="spin" /> : <Icon icon={Table2} size={15} />}
                应用分组
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 新建表模态 */}
      {createModalOpen && (
        <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget && !busyAction) setCreateModalOpen(false); }}>
          <div className="modal-card" role="dialog" aria-modal="true" aria-labelledby="warehouse-create-title" {...createDialog} onClick={(e) => e.stopPropagation()} style={{ maxWidth: 440 }}>
            <div className="modal-head">
              <div>
                <div className="modal-eyebrow"><Icon icon={Plus} size={15} /> 新建表</div>
                <h3 id="warehouse-create-title">基于模板创建空表</h3>
                <div className="support">空白表没有字段定义，无法填写，因此必须选择一个模板作为字段来源。</div>
              </div>
              <button aria-label="关闭新建表" disabled={busyAction} className="btn ghost sm icon-only" onClick={() => setCreateModalOpen(false)}>
                <Icon icon={X} size={16} />
              </button>
            </div>
            <div className="modal-body">
              <label className="form-field">
                <span className="form-label">表名称</span>
                <input
                  className="form-input"
                  type="text"
                  disabled={busyAction}
                  value={newTableName}
                  onChange={(e) => setNewTableName(e.target.value)}
                  placeholder="例如：2026 年 8 月发票"
                />
              </label>
              <label className="form-field">
                <span className="form-label">字段模板</span>
                <select
                  className="form-select"
                  disabled={busyAction || templatesLoading || Boolean(templatesError)}
                  value={newTableTemplate}
                  onChange={(e) => setNewTableTemplate(e.target.value)}
                >
                  <option value="">请选择模板</option>
                  {templates.filter(template => template.engine_kind !== "autonomous").map((t) => (
                    <option key={t.id} value={t.id}>{t.name}</option>
                  ))}
                </select>
              </label>
              {templatesLoading && <p className="support" role="status">正在读取字段模板…</p>}
              {templatesError && <div className="callout danger" role="alert">字段模板读取失败：{templatesError}
                <button className="btn secondary sm" onClick={() => setTemplatesReload(value => value + 1)}>重新读取字段模板</button>
              </div>}
              {!templatesLoading && !templatesError && !templates.some(template => template.engine_kind !== "autonomous") &&
                <p className="support">还没有可用的字段模板，请先到模板页新建模板。</p>}
              {tableError && <p role="alert" className="callout danger">{tableError}</p>}
            </div>
            <div className="modal-foot">
              <button className="btn ghost" disabled={busyAction} onClick={() => setCreateModalOpen(false)}>取消</button>
              <button
                className="btn primary"
                disabled={!newTableName.trim() || !newTableTemplate || busyAction || templatesLoading || Boolean(templatesError)}
                onClick={() => void handleCreateTable()}
              >
                {busyAction ? <Icon icon={Loader2} size={15} className="spin" /> : <Icon icon={Plus} size={15} />}
                创建表
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 删除表确认 */}
      {deleteConfirmOpen && (
        <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget && !busyAction) setDeleteConfirmOpen(false); }}>
          <div className="modal-card" role="dialog" aria-modal="true" aria-labelledby="warehouse-delete-title" {...deleteTableDialog} onClick={(e) => e.stopPropagation()} style={{ maxWidth: 420 }}>
            <div className="modal-head">
              <div>
                <div className="modal-eyebrow"><Icon icon={Trash2} size={15} /> 删除表</div>
                <h3 id="warehouse-delete-title">删除“{currentSheetName}”？</h3>
                <div className="support">{isMerge ? "删除此合并表。原表和记录保留。" : "将删除该表、全部当前数据、分 Sheet 视图及独立数据历史。关联文件的原件与文件修订历史保留，模板仍可用于后续提取。此操作不可恢复。"}</div>
              </div>
              <button aria-label="关闭删除表确认" disabled={busyAction} className="btn ghost sm icon-only" onClick={() => setDeleteConfirmOpen(false)}>
                <Icon icon={X} size={16} />
              </button>
            </div>
            {tableError && <div className="modal-body"><p role="alert" className="callout danger">{tableError}</p></div>}
            <div className="modal-foot">
              <button className="btn ghost" disabled={busyAction} onClick={() => setDeleteConfirmOpen(false)}>取消</button>
              <button
                className="btn danger-soft"
                disabled={busyAction}
                onClick={() => void handleDeleteTable()}
              >
                {busyAction ? <Icon icon={Loader2} size={15} className="spin" /> : <Icon icon={Trash2} size={15} />}
                确认删除
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 字段筛选模态 */}
      {fieldFilterOpen && (
        <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget) setFieldFilterOpen(false); }}>
          <div className="modal-card" role="dialog" aria-modal="true" aria-labelledby="warehouse-fields-title" {...fieldDialog} onClick={(e) => e.stopPropagation()} style={{ maxWidth: 440 }}>
            <div className="modal-head">
              <div>
                <div className="modal-eyebrow"><Icon icon={Columns3} size={15} /> 数据表</div>
                <h3 id="warehouse-fields-title">显示字段</h3>
                <div className="support">勾选要在表格中显示的列，取消则隐藏该列。</div>
              </div>
              <button aria-label="关闭字段显示设置" className="btn ghost sm icon-only" onClick={() => setFieldFilterOpen(false)}>
                <Icon icon={X} size={16} />
              </button>
            </div>
            <div className="modal-body">
              <div className="field-filter-list">
                {columns.map((col) => (
                  <label className={`field-filter-row${hiddenCols.includes(col.key) ? " off" : ""}`} key={col.key}>
                    <input type="checkbox" checked={!hiddenCols.includes(col.key)} onChange={() => toggleCol(col.key)} />
                    <span className="ff-label">{col.label}</span>
                    <span className="ff-tag">{col.numeric ? "数值" : "文本"}</span>
                    {col.userDefined && !currentViewId && (
                      <button
                        aria-label={`删除附加列 ${col.label}`}
                        className="btn ghost sm icon-only danger-btn"
                        type="button"
                        onClick={(event) => {
                          event.preventDefault();
                          event.stopPropagation();
                          setTableError(null);
                          setDeleteColumnTarget(col);
                        }}
                        title="删除附加列"
                      ><Icon icon={Trash2} size={14} /></button>
                    )}
                  </label>
                ))}
              </div>
            </div>
            <div className="modal-foot">
              <button className="btn ghost" onClick={() => setHiddenCols([])}>
                <Icon icon={CheckCheck} size={16} /> 全部显示
              </button>
              <button className="btn primary" onClick={() => setFieldFilterOpen(false)}>完成</button>
            </div>
          </div>
        </div>
      )}

      {addColumnOpen && (
        <div className="modal-overlay" onClick={(event) => { if (event.target === event.currentTarget && !busyAction) setAddColumnOpen(false); }}>
          <div className="modal-card" role="dialog" aria-modal="true" aria-labelledby="warehouse-add-column-title" {...addColumnDialog} style={{ maxWidth: 480 }}>
            <div className="modal-head">
              <div><h3 id="warehouse-add-column-title">新增附加列</h3><div className="support">用于整理当前数据，不改变模板的提取规则；填写后也会记录在文件修订中。</div></div>
              <button aria-label="关闭新增附加列" disabled={busyAction} className="btn ghost sm icon-only" onClick={() => setAddColumnOpen(false)}><Icon icon={X} size={16} /></button>
            </div>
            <div className="modal-body">
              <label className="form-field"><span>列名</span><input ref={newColumnInputRef} disabled={busyAction} className="form-input" maxLength={64} value={newColumnLabel} onChange={(event) => setNewColumnLabel(event.target.value)} placeholder="例如：内部备注" /></label>
              <label className="form-field"><span>字段类型</span><select disabled={busyAction} className="form-input" value={newColumnType} onChange={event => setNewColumnType(event.target.value as typeof newColumnType)}>
                <option value="text">文字</option><option value="number">数字</option><option value="date">日期</option><option value="boolean">是 / 否</option>
              </select></label>
              <div className="form-field"><span>填写方式</span><div className="column-mode-options" role="group" aria-label="附加列填写方式">
                <button type="button" disabled={busyAction} className={`column-mode-button${newColumnSection === "header" ? " active" : ""}`} onClick={() => setNewColumnSection("header")}><strong>按表头填写</strong><span>同一文件共用一个值，导出时合并单元格</span></button>
                <button type="button" disabled={busyAction} className={`column-mode-button${newColumnSection === "item" ? " active" : ""}`} onClick={() => setNewColumnSection("item")}><strong>按明细填写</strong><span>每条明细可以分别填写不同内容</span></button>
              </div></div>
              {tableError && <p role="alert" className="callout danger">{tableError}</p>}
            </div>
            <div className="modal-foot"><button className="btn ghost" disabled={busyAction} onClick={() => setAddColumnOpen(false)}>取消</button><button className="btn primary" disabled={busyAction || !newColumnLabel.trim()} onClick={() => void handleAddColumn()}>新增列</button></div>
          </div>
        </div>
      )}

      {pendingImport && <ImportSheetDialog filename={pendingImport.file.name} sheets={pendingImport.sheets} busy={busyAction} error={tableError}
        onClose={() => { setPendingImport(null); setTableError(null); }}
        onChoose={sheet => { void importFile(pendingImport.file, pendingImport.tableId, sheet); }} />}

      {/* 行修订历史模态 */}
      {revisionsOpen && revisionSource?.task_id && <FileRevisionHistory taskId={revisionSource.task_id}
        filename={String(revisionSource.values.source_filename ?? "文件") } onClose={() => setRevisionsOpen(false)} />}
      {currentTableId && (independentHistoryOpen || revisionsOpen && !revisionSource?.task_id) && <IndependentHistory
        key={`${currentTableId}-${independentHistoryOpen ? "all" : revisionSource?.id}`}
        tableId={currentTableId} tableName={tables.find(table => table.id === currentTableId)?.name ?? "数据表"}
        rowId={independentHistoryOpen ? undefined : revisionSource?.id} columns={tableDetail?.columns ?? []}
        onClose={() => {
          const restoreMenu = independentHistoryOpen;
          setIndependentHistoryOpen(false); setRevisionsOpen(false);
          if (restoreMenu) queueMicrotask(() => tableMenuButtonRef.current?.focus({ preventScroll: true }));
        }} />}

      <ConfirmDialog open={clearSourcesConfirm} title="清空来源"
        description={<>{`将清空 ${selectedSources.length} 个完整来源的 ${selectedSources.reduce((count, source) => count + source.row_count, 0)} 条当前记录。文件原件和修订历史保留；这些数据将不再出现在统计、问知意或后续导出中。`}<span className="confirm-source-list" role="list" aria-label="将清空的完整来源">{selectedSources.map(source => <span role="listitem" key={source.id}>{source.filename || "独立记录"} · {source.row_count} 条</span>)}</span></>}
        buttonLabel="确认清空" busy={busyAction} onConfirm={() => void handleClearSources()} onClose={() => setClearSourcesConfirm(false)} />
      <ConfirmDialog open={deleteRowsConfirm} title={`删除 ${selectedRows.length} 条明细？`}
        description="这些明细将从当前数据中删除，并保留删除前内容的修订记录。文件原件保留；若删去文件的最后一条明细，该文件的共享字段也会从当前数据中清空。"
        buttonLabel="确认删除" busy={busyAction} onConfirm={() => void handleDeleteRows()} onClose={() => setDeleteRowsConfirm(false)} />
      <ConfirmDialog open={deleteColumnTarget !== null} title={`删除附加列“${deleteColumnTarget?.label ?? ""}”？`}
        error={tableError}
        description="仅能删除整张表中没有当前值、且未被分组或仪表盘使用的附加列。已有数据或依赖时会拒绝删除。历史内容保留。"
        buttonLabel="确认删除" busy={busyAction} onConfirm={() => { if (deleteColumnTarget) void handleDeleteColumn(deleteColumnTarget); }} onClose={() => setDeleteColumnTarget(null)} />
      {exportNotes && <NoticeDialog title="导出附加资料" busy={exportNotesBusy} onClose={() => setExportNotes(null)}
        actions={<button className="btn primary" type="button" disabled={exportNotesBusy || (!exportRevisions && !exportInputScope)}
          onClick={async () => {
            setExportNotesBusy(true);
            setExportNotesError("");
            const saved = await downloadExport(getTableNotesExportUrl(exportNotes.tableId, exportRevisions, exportInputScope),
              `${exportNotes.name}-附加资料.xlsx`, setExportNotesError);
            setExportNotesBusy(false);
            if (saved) setExportNotes(null);
          }}>导出</button>}>
        <p>导出「{exportNotes.name}」整表的资料，包含筛选和勾选范围以外的记录。</p>
        <div className="checkbox-options">
          <CheckboxOption title="修订历史" description="每个任务一张工作表，按版本列出修改前后的值。"
            checked={exportRevisions} disabled={exportNotesBusy} onChange={setExportRevisions} />
          <CheckboxOption title="提取范围" description="列出哪些内容交给了模型，以及哪些部分未读取。"
            checked={exportInputScope} disabled={exportNotesBusy} onChange={setExportInputScope} />
        </div>
        {exportNotesError && <p role="alert">{exportNotesError}</p>}
      </NoticeDialog>}
      <Toast toast={toast} onDismiss={clear} />
    </div>
  );
}
