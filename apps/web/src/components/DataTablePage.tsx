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
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Columns3,
  Copy,
  Download,
  FileSpreadsheet,
  FileText,
  Filter,
  FolderOpen,
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
  getTableViewsExportUrl,
  getTables,
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
import { EditableText } from "./EditableText";
import { SourceCardCollection } from "./SourceCardCollection";
import { ImportSheetDialog } from "./ImportSheetDialog";
import { InputScopeDetails } from "./InputScopeDetails";
import { ConfirmDialog } from "./ConfirmDialog";
import { Toast, useToast } from "./Toast";

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

const IDENTITY_KEYS = new Set(["source_filename", "item_index"]);

// --- Helpers ---

function getCellValue(row: DataRowRead, colKey: string): string {
  const v = row.values[colKey];
  if (v == null) return "";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

interface DataTablePageProps {
  initialRowId?: number | null;
  initialTemplateId?: string | null;
  initialTableId?: string | null;
  jumpNotice?: string | null;
  /** 跳转来源任务：对应 task_id 的记录行高亮标记 */
  highlightTaskId?: string | null;
}

export function DataTablePage({
  initialRowId = null,
  initialTemplateId = null,
  initialTableId = null,
  jumpNotice = null,
  highlightTaskId = null,
}: DataTablePageProps = {}) {
  const [dsFilterOpen, setDsFilterOpen] = useState(false);
  const [importMenuOpen, setImportMenuOpen] = useState(false);
  const [pendingImport, setPendingImport] = useState<{file: File; tableId: string | null; sheets: string[]} | null>(null);
  const [exportMenuOpen, setExportMenuOpen] = useState(false);
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

  const [currentTableId, setCurrentTableId] = useState<string | null>(null);
  const [presentationOverrides, setPresentationOverrides] = useState<Record<string, "table" | "card">>({});
  const [tableDetail, setTableDetail] = useState<DataTableDetail | DataViewDetail | null>(null);
  const [tableLoading, setTableLoading] = useState(false);
  const [tableError, setTableError] = useState<string | null>(null);
  const [tableNotice, setTableNotice] = useState<string | null>(null);
  const [unsavedCellDrafts, setUnsavedCellDrafts] = useState<UnsavedCellDraft[]>([]);
  const cellSaveAttempts = useRef(new Map<string, number>());
  useEffect(() => { if (tableError) notify(tableError, "error"); }, [tableError, notify]);
  useEffect(() => { if (tableNotice) notify(tableNotice, "success"); }, [tableNotice, notify]);

  const [page, setPage] = useState(1);
  const [jumpPageInput, setJumpPageInput] = useState("");
  const [searchText, setSearchText] = useState("");
  const [reloadTrigger, setReloadTrigger] = useState(0);
  useEffect(() => {
    const changed = (event: Event) => {
      if ((event as CustomEvent<{ table_id?: string }>).detail?.table_id === currentTableId) {
        setReloadTrigger(n => n + 1);
        setTableNotice("问知意已执行确认的修改，当前数据已刷新；历史分析仍保留原快照。");
      }
    };
    window.addEventListener("zhiyi:assistant-changed", changed);
    return () => window.removeEventListener("zhiyi:assistant-changed", changed);
  }, [currentTableId]);
  const [views, setViews] = useState<DataViewRead[]>([]);
  const [moreViews, setMoreViews] = useState(false);
  const [loadingMoreViews, setLoadingMoreViews] = useState(false);
  const viewsGeneration = useRef(0);
  const [currentViewId, setCurrentViewId] = useState<string | null>(null);
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
    row_ids: selectedRows.length ? selectedRows : null, source_keys: selectedSources.length ? selectedSources.map(source => source.id) : null,
    view_id: currentViewId, search: searchText });
  const [editingCell, setEditingCell] = useState<string | null>(null);
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

  const cancelEditRef = useRef(false);
  const importInputRef = useRef<HTMLInputElement>(null);
  const importNewInputRef = useRef<HTMLInputElement>(null);

  const [sidebarWidth, setSidebarWidth] = useState(240);
  const sidebarDrag = useRef<{x: number; width: number} | null>(null);
  const resizeSidebar = (width: number) => setSidebarWidth(Math.max(180, Math.min(360, width)));

  // 加载表列表
  useEffect(() => {
    getTables()
      .then((res) => {
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
          setCurrentTableId((prev) => prev ?? preferred?.id ?? res[0].id);
        }
      })
      .catch((err) => setTablesError(err instanceof Error ? err.message : "加载表列表失败"))
      .finally(() => setTablesLoading(false));
  }, [initialTemplateId, initialTableId]);

  // 加载模板列表（新建表用）
  useEffect(() => {
    getTemplates(true)
      .then(setTemplates)
      .catch(() => setTemplates([]));
  }, []);

  const detailMatches = tableDetail && ("table_id" in tableDetail ? tableDetail.table_id : tableDetail.id) === currentTableId;
  const isFileCollection = tables.find(table => table.id === currentTableId)?.collection_kind === "files";
  const presentationMode = isFileCollection ? "card" : (currentTableId && presentationOverrides[currentTableId]) || (detailMatches ? tableDetail.presentation?.mode : undefined) || "table";
  const cardArea = useRef<HTMLDivElement>(null);
  const pageSize = TABLE_PAGE_SIZE;
  useEffect(() => {
    if (!initialRowId || !currentTableId || currentTableId !== initialTableId) return;
    let cancelled = false;
    void assistantApi<{ page: number }>(`/references/tables/${encodeURIComponent(currentTableId)}/rows/${initialRowId}?page_size=${pageSize}`).then(result => {
      if (!cancelled) { setCurrentViewId(null); setSearchText(""); setPage(result.page); setSelectedRows([initialRowId]); setPresentationOverrides(previous => ({...previous, [currentTableId]: "table"})); }
    }).catch(error => { if (!cancelled) setTableNotice(error.message); });
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
      return;
    }
    let cancelled = false;
    setTableLoading(true);
    setTableError(null);
    const request = currentViewId
      ? getTableView(currentTableId, currentViewId, page, pageSize, searchText)
      : getTable(currentTableId, page, pageSize, searchText);
    request
      .then((detail) => {
        if (!cancelled) {
          setTableDetail(detail);
          setTableError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) setTableError(err instanceof Error ? err.message : "加载表数据失败");
      })
      .finally(() => {
        if (!cancelled) setTableLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [currentTableId, currentViewId, page, pageSize, searchText, reloadTrigger]);

  useEffect(() => {
    const generation = ++viewsGeneration.current;
    setMoreViews(false); setLoadingMoreViews(false);
    if (!currentTableId) {
      setViews([]);
      return;
    }
    let cancelled = false;
    getTableViews(currentTableId)
      .then((nextViews) => {
        if (!cancelled && generation === viewsGeneration.current) { setViews(nextViews); setMoreViews(nextViews.length === 200); }
      })
      .catch((err) => {
        if (!cancelled && generation === viewsGeneration.current) {
          setTableError(err instanceof Error ? err.message : "加载分 Sheet 失败");
        }
      });
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
    if (!tableDetail) return [];
    return tableDetail.columns.map((c: TableColumnDef) => ({
      key: c.key,
      label: c.label,
      numeric: c.value_type === "number",
      valueType: c.value_type,
      section: c.section,
      width: columnWidths[c.key] ?? (c.value_type === "number" ? 112 : 132),
      userDefined: Boolean(c.user_defined),
    }));
  }, [tableDetail, columnWidths]);

  const visibleColumns = columns.filter((col) => !hiddenCols.includes(col.key));
  const widthFor = (col: ColumnDef) => columnWidths[col.key] ?? col.width;
  const tableMinWidth = 44 + visibleColumns.reduce((sum, col) => sum + widthFor(col), 0);
  const rows = (tableDetail?.rows ?? []).slice(0, pageSize);

  const contentUnit = "行";
  const totalRows = tableDetail?.row_count ?? 0;
  const totalPages = Math.max(1, Math.ceil(totalRows / pageSize));

  const selectedCount = selectedRows.length;
  const allSelected = rows.length > 0 && rows.every((row) => selectedRows.includes(row.id));

  const currentSheetName =
    tableDetail?.name ?? tables.find((t) => t.id === currentTableId)?.name ?? "";
  const currentSheetSource =
    tableDetail && "source_table_name" in tableDetail
      ? `分组 · ${tableDetail.source_table_name}`
      : "全部记录";

  const mergePreviewRows = mergeSelection.reduce(
    (sum, id) => sum + (tables.find((t) => t.id === id)?.row_count ?? 0),
    0,
  );

  // 分页页码（最多展示 3 个）
  const pageNumbers: number[] = [];
  const pageStart = Math.max(1, Math.min(page - 1, totalPages - 2));
  const pageEnd = Math.min(totalPages, pageStart + 2);
  for (let p = pageStart; p <= pageEnd; p++) pageNumbers.push(p);

  // 多明细合并：按 task_id 连续分组合并抬头列
  const rowGroups = useMemo(() => {
    const groups: Array<{ taskId: string | null; rows: DataRowRead[] }> = [];
    for (const row of rows) {
      const key = row.task_id ?? String(row.values.__row_group ?? `row-${row.id}`);
      const last = groups[groups.length - 1];
      if (last && last.taskId === key) {
        last.rows.push(row);
      } else {
        groups.push({ taskId: key, rows: [row] });
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
    setPage(1);
    setSearchText("");
    setSelectedRows([]);
    setEditingCell(null);
    setHiddenCols([]);
    setColumnWidths({});
    setTableError(null);
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

  function jumpToPage() {
    const target = Number(jumpPageInput);
    if (!Number.isNaN(target)) {
      goToPage(target);
      setJumpPageInput("");
    }
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
    event.preventDefault();
    event.stopPropagation();
    const startX = event.clientX;
    setResizingColKey(key);
    const move = (moveEvent: PointerEvent) => {
      const nextWidth = Math.min(520, Math.max(72, initialWidth + moveEvent.clientX - startX));
      setColumnWidths((current) => ({ ...current, [key]: nextWidth }));
    };
    const finish = () => {
      setResizingColKey(null);
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerup", finish);
    };
    document.addEventListener("pointermove", move);
    document.addEventListener("pointerup", finish);
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
      await renameTable(currentTableId, nextName);
      setReloadTrigger((n) => n + 1);
      await getTables().then(setTables);
    } catch (err) {
      setTableError(err instanceof Error ? err.message : "重命名失败");
    }
  }

  async function handleExport(format: "xlsx" | "csv" | "json" | "views" | "zip") {
    if (!currentTableId) return;
    setExportMenuOpen(false);
    if (format === "views" && views.length === 0) {
      setTableError(null);
      setTableNotice("当前数据表还没有分 Sheet，请先创建分 Sheet 后再导出。");
      return;
    }
    const scope = { viewId: currentViewId, search: searchText, rowIds: !isFileCollection && selectedRows.length ? selectedRows : null,
      sourceKeys: selectedSources.length ? selectedSources.map(source => source.id) : null };
    const url = format === "views" ? getTableViewsExportUrl(currentTableId, scope) : getTableExportUrl(currentTableId, format, scope);
    const bridge = desktopApi();
    try {
      const absoluteUrl = new URL(url, window.location.origin).toString();
      const filename = `${currentSheetName || "数据表"}${searchText.trim() ? "-搜索结果" : ""}${selectedSources.length ? "-选中来源" : selectedRows.length && !isFileCollection ? "-选中明细" : ""}${format === "views" ? "-分Sheet.xlsx" : `.${format}`}`;
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
        setTableError(null); setTableNotice("已导出当前范围。");
        return;
      }
      const path = await bridge.export_table(absoluteUrl, filename);
      setTableError(null);
      setTableNotice(`已导出到 ${path}`);
    } catch (err) {
      setTableError(err instanceof Error ? err.message : "导出失败");
    }
  }

  async function importFile(file: File, tableId: string | null, sheetName?: string) {
    setImportMenuOpen(false);
    setBusyAction(true);
    setTableError(null);
    try {
      const body = new FormData();
      body.append("file", file);
      if (sheetName) body.append("sheet_name", sheetName);
      const response = await fetch(tableId ? `/api/v1/tables/${tableId}/import` : "/api/v1/tables/import-new", {
        method: "POST",
        body,
      });
      const data = await response.clone().json().catch(() => null) as {id?: string; name?: string; detail?: unknown} | null;
      const detail = data?.detail as {code?: string; sheets?: unknown} | undefined;
      if (!response.ok && detail?.code === "select_sheet" && Array.isArray(detail.sheets) && detail.sheets.every(sheet => typeof sheet === "string")) {
        setPendingImport({file, tableId, sheets: detail.sheets});
        return;
      }
      if (!response.ok) throw new Error(await responseErrorMessage(response, "导入"));
      if (!data?.id) throw new Error("导入结果未能确认，请刷新数据表查看。");
      setPendingImport(null);
      if (!tableId) { setCurrentTableId(data.id); setCurrentViewId(null); }
      setTableNotice(`已将「${file.name}${sheetName ? ` · ${sheetName}` : ""}」导入「${data.name || "数据表"}」。`);
      setReloadTrigger((n) => n + 1);
      await getTables().then(setTables);
    } catch (err) {
      setTableError(err instanceof Error ? err.message : "导入失败");
    } finally {
      setBusyAction(false);
      if (importInputRef.current) importInputRef.current.value = "";
      if (importNewInputRef.current) importNewInputRef.current.value = "";
    }
  }

  async function handleImportFile(file: File) {
    if (currentTableId) await importFile(file, currentTableId);
  }

  async function handleImportNewTable(file: File) {
    await importFile(file, null);
  }

  async function commitCellEdit(row: DataRowRead, colKey: string, newValue: string) {
    if (!currentTableId || !tableDetail) return;
    const oldValue = getCellValue(row, colKey);
    if (newValue === oldValue) return;
    const key = JSON.stringify([currentTableId, row.id, colKey]);
    const attempt = (cellSaveAttempts.current.get(key) ?? 0) + 1;
    cellSaveAttempts.current.set(key, attempt);
    const column = columns.find((item) => item.key === colKey);
    const draft: UnsavedCellDraft = {key, tableId: currentTableId,
      tableName: tables.find(table => table.id === currentTableId)?.name ?? currentTableId,
      rowId: row.id, columnLabel: column?.label ?? colKey, input: newValue};
    try {
      let value: unknown = newValue === "" ? null : newValue;
      if (value !== null && column?.valueType === "number") {
        const normalized = Number(newValue.replace(/,/g, "").trim());
        if (!Number.isFinite(normalized)) {
          throw new Error(`「${column.label}」需要填写有效数字。`);
        }
        value = normalized;
      } else if (value !== null && column?.valueType === "boolean") {
        const normalized = newValue.trim().toLowerCase();
        if (["true", "是", "1"].includes(normalized)) value = true;
        else if (["false", "否", "0"].includes(normalized)) value = false;
        else throw new Error(`「${column.label}」需要填写是/否。`);
      }
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
    try {
      await addCustomColumn(currentTableId, newColumnLabel.trim(), newColumnSection, newColumnType);
      setAddColumnOpen(false);
      setNewColumnLabel("");
      setNewColumnSection("header");
      setNewColumnType("text");
      setReloadTrigger((n) => n + 1);
      setTableNotice("附加列已创建；它只属于数据表，不会发送给模型。 ");
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
      setTableNotice(`已删除附加列“${column.label}”。`);
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
      setTableNotice(groupBy === "none" ? "已取消分组，数据未改变。" : "分组会随当前数据更新，新类别会自动出现。");
      await getTables().then(setTables);
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
      await getTables().then(setTables);
      selectTable(merged.id);
      setTableError(null);
    } catch (err) {
      setTableError(err instanceof Error ? err.message : "合并失败");
    } finally {
      setBusyAction(false);
    }
  }

  async function handleCreateTable() {
    if (!newTableName.trim() || !newTableTemplate) return;
    setBusyAction(true);
    setTableError(null);
    try {
      const created = await createTable(newTableName.trim(), newTableTemplate);
      setCreateModalOpen(false);
      setNewTableName("");
      setNewTableTemplate("");
      await getTables().then(setTables);
      selectTable(created.id);
    } catch (err) {
      setTableError(err instanceof Error ? err.message : "新建表失败");
    } finally {
      setBusyAction(false);
    }
  }

  async function handleAddRow() {
    if (!currentTableId) return;
    setBusyAction(true);
    setTableError(null);
    try {
      await addDataRow(currentTableId, {});
      // 跳转到新行所在页（空行追加在末尾），让用户立即看到并直接编辑
      setPage(Math.max(1, Math.ceil((totalRows + 1) / pageSize)));
      setReloadTrigger((n) => n + 1);
      await getTables().then(setTables);
    } catch (err) {
      setTableError(err instanceof Error ? err.message : "新增行失败");
    } finally {
      setBusyAction(false);
    }
  }

  async function handleDeleteRows() {
    if (!currentTableId || selectedRows.length === 0) return;
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
      await getTables().then(setTables);
    } catch (err) {
      setTableError(err instanceof Error ? err.message : "删除行失败");
    } finally {
      setBusyAction(false);
    }
  }

  async function handleClearSources() {
    if (!currentTableId || !selectedSources.length) return;
    setBusyAction(true); setTableError(null);
    try {
      await clearSourceCards(currentTableId, selectedSources.map(({id, version}) => ({id, version})), {viewId: currentViewId, search: searchText});
      setSelectedSources([]); setClearSourcesConfirm(false); setReloadTrigger(n => n + 1);
      await getTables().then(setTables);
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
      await getTables().then(setTables);
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

  return (
    <div className={`view${presentationMode === "card" ? " presentation-card" : ""}`}>
      {unsavedCellDrafts.length > 0 && <details className="callout warning" style={{marginBottom: 16}}>
        <summary>未保存输入（{unsavedCellDrafts.length}）· 展开查看或复制</summary>
        <p>以下输入未写入数据表，仅在本页暂存。可复制后重新修改；关闭条目或离开本页会丢弃输入。</p>
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
      </details>}
      {jumpNotice && (
        <div className="callout warning" style={{ marginBottom: 16 }}>
          {jumpNotice}
        </div>
      )}
      <div className="datastore-layout">
        {/* Toolbar */}
        <div className="datastore-toolbar">
          <div className="datastore-toolbar-left" style={{ gap: "calc(var(--space-4) * 2)" }}>
            {!isFileCollection && <>
            <button className="btn secondary sm" onClick={() => setFieldFilterOpen(true)}>
              <Icon icon={Columns3} size={15} /> 字段
              {hiddenCols.length > 0 && (
                <span className="filter-count-badge">{hiddenCols.length}</span>
              )}
            </button>
            <button
              className="btn secondary sm"
              disabled={!currentTableId || Boolean(currentViewId)}
              onClick={() => setAddColumnOpen(true)}
              title={currentViewId ? "请回到原始数据表后新增列" : "新增不参与 AI 提取的附加列"}
            >
              <Icon icon={Plus} size={15} /> 新增列
            </button>
            <button
              className="btn secondary sm"
              disabled={!currentTableId}
              onClick={() => setSplitModalOpen(true)}
            >
              <Icon icon={Table2} size={15} /> {presentationMode === "card" ? "分组" : "分 Sheet"}
            </button>
            <div className="toolbar-divider" />
            </>}
            {!isFileCollection && <button
              className="btn secondary sm"
              disabled={isFileCollection || tables.filter(table => table.collection_kind !== "files").length < 2}
              onClick={() => setMergeModalOpen(true)}
              title={tables.length < 2 ? "至少需要两张表才能合并" : undefined}
            >
              <Icon icon={GitMerge} size={15} /> 合并表
            </button>}
            <div className="toolbar-menu-wrap">
              <button
                className="btn secondary sm"
                onClick={() => setImportMenuOpen(!importMenuOpen)}
              >
                <Icon icon={Upload} size={15} /> 导入
                <Icon icon={ChevronDown} size={13} style={{ opacity: 0.6 }} />
              </button>
              <input
                ref={importInputRef}
                className="visually-hidden"
                type="file"
                accept=".xlsx,.csv"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void handleImportFile(file);
                }}
              />
              <input ref={importNewInputRef} className="visually-hidden" type="file" accept=".xlsx,.csv" onChange={(event) => { const file = event.target.files?.[0]; if (file) void handleImportNewTable(file); }} />
              {importMenuOpen && (
                <div className="toolbar-menu">
                  <button className="toolbar-menu-item" disabled={isFileCollection} onClick={() => importInputRef.current?.click()}>
                    <Icon icon={FileSpreadsheet} size={15} /> 追加到当前表<span className="menu-hint">XLSX / CSV</span>
                  </button>
                  <button className="toolbar-menu-item" onClick={() => importNewInputRef.current?.click()}>
                    <Icon icon={FileText} size={15} /> 从文件新建表<span className="menu-hint">保留重复表头</span>
                  </button>
                </div>
              )}
            </div>
            <div className="toolbar-menu-wrap">
              <button className="btn secondary sm" onClick={() => setExportMenuOpen(!exportMenuOpen)}>
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
          <select id="compact-table" className="form-select" value={currentViewId ? `view:${currentViewId}` : `table:${currentTableId ?? ""}`}
            onChange={(event) => { const value = event.target.value; if (value.startsWith("view:")) selectView(value.slice(5)); else selectTable(value.slice(6)); }}>
            <optgroup label="数据表">{tables.map((table) => <option key={table.id} value={`table:${table.id}`}>{table.name} · {table.collection_kind === "files" ? `${table.file_count ?? 0} 份文件` : `${table.row_count} 行`}</option>)}</optgroup>
            {views.length > 0 && <optgroup label="当前表的分组">{views.map((view) => <option key={view.id} value={`view:${view.id}`}>{view.name}</option>)}</optgroup>}
          </select>
          <button className="btn ghost sm icon-only" title="新建表" onClick={() => { setNewTableName(""); setNewTableTemplate(""); setCreateModalOpen(true); }}><Icon icon={Plus} size={14} /></button>
        </div>
        {/* Sheet tree + detail + 可拖拽分隔条 */}
        <div className={`datastore-split${resizingColKey ? " dragging-col" : ""}`}>
          <div className="sheet-tree" style={{ width: `${sidebarWidth}px`, flexShrink: 0 }}>
            <div className="sheet-tree-head">
              <span className="sheet-tree-title">数据仓库</span>
              <div className="sheet-tree-add">
                <button
                  className="btn ghost sm icon-only"
                  onClick={() => {
                    setNewTableName("");
                    setNewTableTemplate("");
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
                <div className="callout danger" style={{ margin: 12 }}>{tablesError}</div>
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
                    <Icon icon={ChevronDown} size={16} />
                    <span className="sheet-tree-group-label">原始记录</span>
                  </button>
                  <div className={`sheet-tree-group-body${treeGroupsOpen.records ? " open" : ""}`} inert={!treeGroupsOpen.records} aria-hidden={!treeGroupsOpen.records}>
                    <div className="sheet-tree-group-inner">
                  {tables.length === 0 && (
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
                  {!isFileCollection && <><button
                    className="sheet-tree-group-title"
                    aria-expanded={treeGroupsOpen.views}
                    onClick={() =>
                      setTreeGroupsOpen((s) => ({ ...s, views: !s.views }))
                    }
                    title={`${treeGroupsOpen.views ? "收起" : "展开"}${presentationMode === "card" ? "内容分组" : "分 Sheet 视图"}`}
                  >
                    <Icon icon={ChevronDown} size={16} />
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
              event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId);
              sidebarDrag.current = {x: event.clientX, width: sidebarWidth};
            }}
            onPointerMove={event => { if (sidebarDrag.current) resizeSidebar(sidebarDrag.current.width + event.clientX - sidebarDrag.current.x); }}
            onPointerUp={event => { sidebarDrag.current = null; if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }}
            onPointerCancel={() => { sidebarDrag.current = null; }} onLostPointerCapture={() => { sidebarDrag.current = null; }}
            onKeyDown={event => {
              if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
              event.preventDefault(); resizeSidebar(event.key === "Home" ? 180 : event.key === "End" ? 360 : sidebarWidth + (event.key === "ArrowLeft" ? -12 : 12));
            }} />

          <div className="sheet-detail">
            <div className="panel-title" style={{ marginBottom: 0 }}>
              <div>
                {!renamingSheet ? (
                  <h3
                    onClick={() => {
                      if (!currentViewId && currentTableId) startRenameSheet();
                    }}
                    style={{ cursor: currentViewId ? "default" : "text" }}
                    title={currentViewId ? undefined : "点击重命名"}
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
                <div className="support">{isFileCollection ? `字段按文件保存 · 共 ${tables.find(table => table.id === currentTableId)?.file_count ?? 0} 份文件` : <>共 <span>{totalRows}</span> {contentUnit} · <span>{currentSheetSource}</span></>}</div>
              </div>
              <div className="sheet-detail-actions">
                {!currentViewId && currentTableId ? (
                  <DropdownMenu.Root>
                    <DropdownMenu.Trigger asChild><button className="btn ghost sm icon-only" aria-label="数据表操作"><Icon icon={MoreHorizontal} size={18} /></button></DropdownMenu.Trigger>
                    <DropdownMenu.Portal><DropdownMenu.Content className="record-menu" align="end" sideOffset={6}>
                      <DropdownMenu.Item onSelect={startRenameSheet}><Icon icon={Pencil} size={14} />重命名</DropdownMenu.Item>
                      {!isFileCollection && <DropdownMenu.Item onSelect={() => setIndependentHistoryOpen(true)}><Icon icon={History} size={14} />独立数据历史</DropdownMenu.Item>}
                      <DropdownMenu.Separator />
                      <DropdownMenu.Item className="danger" onSelect={() => setDeleteConfirmOpen(true)}><Icon icon={Trash2} size={14} />删除数据表</DropdownMenu.Item>
                    </DropdownMenu.Content></DropdownMenu.Portal>
                  </DropdownMenu.Root>
                ) : null}
              </div>
            </div>

            <div className="data-table-wrap">
              {/* 行级操作栏 */}
              <div className="row-actions-bar">
                <div className="row-actions-left">
                  {(presentationMode === "card" && selectedRows.length === 0 || selectedSources.length > 0) ? <button className="btn xs danger-soft" disabled={busyAction || !selectedSources.length} onClick={() => setClearSourcesConfirm(true)}><Icon icon={Trash2} size={14} />清空选中来源数据</button> : <button
                    className="btn xs danger-soft"
                    disabled={busyAction || selectedRows.length === 0}
                    onClick={() => setDeleteRowsConfirm(true)}
                  >
                    <Icon icon={Trash2} size={14} /> 删除选中
                  </button>}
                  {!isFileCollection && <>
                  <button
                    className="btn xs secondary"
                    disabled={selectedRows.length !== 1}
                    onClick={() => {
                      const row = rows.find((r) => r.id === selectedRows[0]) ?? selectedRowSnapshots.current[selectedRows[0]];
                      if (row) void openRevisions(row.id);
                    }}
                    title={selectedRows.length !== 1 ? "先勾选一行查看其修订历史" : "查看选中行的修订历史"}
                  >
                    <Icon icon={History} size={14} /> 修订历史
                  </button>
                  <button
                    className="btn xs primary"
                    disabled={busyAction}
                    onClick={() => void handleAddRow()}
                    title={presentationMode === "card" ? "新增空白内容，展开卡片后填写" : "在表尾新增一行空记录，直接在表格里填写"}
                  >
                    <Icon icon={Plus} size={14} /> {presentationMode === "card" ? "新增独立记录" : "新增一行"}
                  </button>
                  <button className="btn xs secondary" onClick={() => setFieldFilterOpen(true)}>
                    <Icon icon={Columns3} size={14} /> 字段筛选
                    {hiddenCols.length > 0 && (
                      <span className="filter-count-badge">{hiddenCols.length}</span>
                    )}
                  </button>
                  </>}
                </div>
                <div className="row-actions-right">
                  {!isFileCollection && <div className="presentation-switch" aria-label="内容展示方式">
                    {(["table", "card"] as const).map((mode) => <button key={mode} className={`btn xs ${presentationMode === mode ? "primary" : "ghost"}`}
                      disabled={!currentTableId} aria-pressed={presentationMode === mode}
                      onClick={() => {
                        if (!currentTableId || mode === presentationMode) return;
                        setPresentationOverrides({ ...presentationOverrides, [currentTableId]: mode });
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
              </div>
              {presentationMode === "table" && rows.some((row) => row.review_pending) && (
                <details className="support data-source-details" style={{ padding: "8px 16px", color: "var(--warning-text)", overflowWrap: "anywhere" }}>
                  <summary>本页有 {rows.filter((row) => row.review_pending).length} 条来源待核对的数据</summary>
                  <p>关联文件的数据修改后，会按提取时的规则重新校验。可在文件历史查看问题；导出保留待核对标记。独立导入和合并数据只保留原来源的核对状态。</p>
                  {rows.filter((row, index) => row.review_pending && !rows.slice(0, index).some((previous) => previous.review_pending &&
                    (previous.task_id ?? previous.values.__row_group ?? previous.id) === (row.task_id ?? row.values.__row_group ?? row.id)
                  )).map((row) => <p key={row.id}>{String(row.values.source_filename || `内容 ${row.id}`)}{!row.task_id && " · 来源已移除或来自导入/合并，保留原待核对状态"}</p>)}
                </details>
              )}
              {presentationMode === "table" && rows.some((row) => row.input_scope?.coverage === "partial") && (
                <details className="support data-source-details" style={{ padding: "8px 16px", overflowWrap: "anywhere" }}>
                  <summary>本页有局部读取的数据 · 查看来源范围</summary>
                  <p>这些结果只基于文件的部分内容，不能代表全文或全部明细。修改表中数据不会改变当时的读取范围。</p>
                  {rows.filter((row, index) => row.input_scope?.coverage === "partial" && !rows.slice(0, index).some((previous) =>
                    (previous.task_id ?? previous.values.__row_group ?? previous.id) === (row.task_id ?? row.values.__row_group ?? row.id)
                    && JSON.stringify(previous.input_scope) === JSON.stringify(row.input_scope)
                  )).map((row) => <InputScopeDetails key={row.id} scope={row.input_scope}
                    label={String(row.values.source_filename || `内容 ${row.id}`)} originalAvailable={Boolean(row.task_id)} />)}
                </details>
              )}
              <div className="data-table-scroll" ref={cardArea}>
                {tableLoading && !detailMatches ? (
                  <div style={{ padding: 48, textAlign: "center" }}>
                    <Icon icon={Loader2} size={24} className="spin" />
                  </div>
                ) : presentationMode === "card" && currentTableId ? (
                  <SourceCardCollection key={`${currentTableId}:${currentViewId}`} tableId={currentTableId} scope={{viewId: currentViewId, search: searchText}}
                    reload={reloadTrigger} selected={selectedSources} onSelect={sources => { setSelectedSources(sources); setSelectedRows([]); }}
                    selectedRows={selectedRows} onSelectRow={isFileCollection ? undefined : (row, checked) => { selectedRowSnapshots.current[row.id] = row; setSelectedSources([]); setSelectedRows(previous => checked ? [...previous.filter(id => id !== row.id), row.id] : previous.filter(id => id !== row.id)); }}
                    onChanged={() => { setSelectedSources([]); setSelectedRows([]); setReloadTrigger(n => n + 1); }} hiddenFields={hiddenCols} highlightTaskId={highlightTaskId} />
                ) : rows.length === 0 && columns.length === 0 ? (
                  <div className="table-empty-state">
                    <Icon icon={Table2} size={28} />
                    <div>这张表还没有数据</div>
                    <div className="small muted">使用“新增一行”或“导入”开始填写</div>
                  </div>
                ) : (
                  <table className="data-table" style={{ minWidth: `${tableMinWidth}px` }}>
                    <colgroup>
                      <col style={{ width: 44 }} />
                      {visibleColumns.map((col) => (
                        <col key={col.key} style={{ width: widthFor(col) }} />
                      ))}
                    </colgroup>
                    <thead>
                      <tr>
                        <th className="th-check" style={{ width: 44 }}>
                          <input aria-label="全选或取消全选" type="checkbox" checked={allSelected} onChange={() => toggleSelectAll()} title="全选 / 取消全选" />
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
                              title="单击单元格直接编辑 · 勾选框用于批量选择"
                            >
                              <td className="td-check" onClick={(e) => e.stopPropagation()}>
                                <input aria-label="勾选此行加入批量" type="checkbox" checked={selectedRows.includes(row.id)} onChange={() => toggleSelectRow(row.id)} title="勾选加入批量" />
                              </td>
                              {visibleColumns.map((col) => {
                                const cellText = getCellValue(row, col.key);
                                const isMatch =
                                  searchQuery !== "" &&
                                  cellText.toLowerCase().includes(searchQuery);
                                if (headerKeys.has(col.key) && (row.shared_header_keys?.includes(col.key) ?? true)) {
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
                                        if (editingCell !== `${idx}-${col.key}`) {
                                          cancelEditRef.current = false;
                                          setEditingCell(`${idx}-${col.key}`);
                                        }
                                      }}
                                      onKeyDown={(event) => {
                                        if (event.target !== event.currentTarget) return;
                                        if (event.key === "Enter" || event.key === "F2") {
                                          event.preventDefault();
                                          cancelEditRef.current = false;
                                          setEditingCell(`${idx}-${col.key}`);
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
                                      if (editingCell !== `${idx}-${col.key}`) {
                                        cancelEditRef.current = false;
                                        setEditingCell(`${idx}-${col.key}`);
                                      }
                                    }}
                                    onKeyDown={(event) => {
                                      if (event.target !== event.currentTarget) return;
                                      if (event.key === "Enter" || event.key === "F2") {
                                        event.preventDefault();
                                        cancelEditRef.current = false;
                                        setEditingCell(`${idx}-${col.key}`);
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
              {presentationMode === "table" && <div className="table-footer">
                <span className="footer-info">显示 <span>{rows.length === 0 ? 0 : (page - 1) * pageSize + 1}</span>-<span>{(page - 1) * pageSize + rows.length}</span> {contentUnit}，共 <span>{totalRows}</span> {contentUnit}</span>
                <div className="pagination">
                  <button className="btn icon-only sm" disabled={page <= 1} onClick={() => goToPage(1)} title="第一页">
                    <Icon icon={ChevronsLeft} size={14} />
                  </button>
                  <button className="btn icon-only sm" disabled={page <= 1} onClick={() => goToPage(Math.max(1, page - 1))} title="上一页">
                    <Icon icon={ChevronLeft} size={14} />
                  </button>
                  {pageNumbers.map((p) => (
                    <button key={p} className={`btn sm${p === page ? " active" : ""}`} onClick={() => goToPage(p)}>{p}</button>
                  ))}
                  <button className="btn icon-only sm" disabled={page >= totalPages} onClick={() => goToPage(Math.min(totalPages, page + 1))} title="下一页">
                    <Icon icon={ChevronRight} size={14} />
                  </button>
                  <button className="btn icon-only sm" disabled={page >= totalPages} onClick={() => goToPage(totalPages)} title="最后一页">
                    <Icon icon={ChevronsRight} size={14} />
                  </button>
                  <span className="pagination-jump">
                    第
                    <input
                      type="number"
                      min={1}
                      max={totalPages}
                      value={jumpPageInput}
                      onChange={(e) => setJumpPageInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") jumpToPage();
                      }}
                      placeholder={String(page)}
                      aria-label="跳转页码"
                    />
                    页 / 共 {totalPages} 页
                    <button className="btn sm secondary" disabled={!jumpPageInput.trim()} onClick={jumpToPage}>
                      跳转
                    </button>
                  </span>
                </div>
              </div>}
            </div>
          </div>
        </div>
      </div>

      {/* 合并表模态 */}
      {mergeModalOpen && (
        <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget) setMergeModalOpen(false); }}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <div>
                <div className="modal-eyebrow"><Icon icon={GitMerge} size={15} /> 合并表</div>
                <h3>选择需要合并的数据表</h3>
                <div className="support">将多张表纵向合并为一张新表，字段取并集，保留全部行。</div>
              </div>
              <button aria-label="关闭" className="btn ghost sm icon-only" onClick={() => setMergeModalOpen(false)}>
                <Icon icon={X} size={16} />
              </button>
            </div>
            <div className="modal-body">
              <div className="merge-sheet-list">
                {tables.filter(table => table.collection_kind !== "files").map((s) => (
                  <label className={`merge-sheet-row${mergeSelection.includes(s.id) ? " checked" : ""}`} key={s.id}>
                    <input type="checkbox" checked={mergeSelection.includes(s.id)} onChange={() => toggleMergeSheet(s.id)} />
                    <Icon icon={Table2} size={15} style={{ color: "var(--color-text-muted)" }} />
                    <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.name}</span>
                    <span className="row-count">{s.row_count + " 行"}</span>
                  </label>
                ))}
              </div>
              <div className="merge-name-row">
                <label className="form-label" htmlFor="merge-table-name">新表名称</label>
                <input aria-label="新表名称" className="form-input" type="text" id="merge-table-name" value={mergeName} onChange={(e) => setMergeName(e.target.value)} placeholder="为合并后的表命名" />
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
            </div>
            <div className="modal-foot">
              <button className="btn ghost" onClick={() => setMergeModalOpen(false)}>取消</button>
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
            if (event.target === event.currentTarget) setSplitModalOpen(false);
          }}
        >
          <div className="modal-card" onClick={(event) => event.stopPropagation()} style={{ maxWidth: 480 }}>
            <div className="modal-head">
              <div>
                <div className="modal-eyebrow"><Icon icon={Table2} size={15} /> 内容分组</div>
                <h3>选择分组方式</h3>
                <div className="support">按来源文件或字段浏览，数据仍只保存一份。</div>
              </div>
              <button className="btn ghost sm icon-only" onClick={() => setSplitModalOpen(false)}>
                <Icon icon={X} size={16} />
              </button>
            </div>
            <div className="modal-body">
              <label className="form-field"><span className="form-label">分组方式</span>
                <select className="form-select" value={groupBy} onChange={(event) => setGroupBy(event.target.value as "none" | "file" | "field")}>
                  <option value="none">取消全部分组</option><option value="file">按文件或独立来源组</option><option value="field">按字段</option>
                </select>
              </label>
              {groupBy === "field" && <label className="form-field">
                <span className="form-label">用于分类的字段</span>
                <select
                  className="form-select"
                  value={splitFieldKey}
                  onChange={(event) => setSplitFieldKey(event.target.value)}
                >
                  <option value="">请选择字段</option>
                  {columns.map((column) => (
                    <option key={column.key} value={column.key}>{column.label}</option>
                  ))}
                </select>
              </label>}
            </div>
            <div className="modal-foot">
              <button className="btn ghost" onClick={() => setSplitModalOpen(false)}>取消</button>
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
        <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget) setCreateModalOpen(false); }}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 440 }}>
            <div className="modal-head">
              <div>
                <div className="modal-eyebrow"><Icon icon={Plus} size={15} /> 新建表</div>
                <h3>基于模板创建空表</h3>
                <div className="support">空白表没有字段定义，无法填写，因此必须选择一个模板作为字段来源。</div>
              </div>
              <button className="btn ghost sm icon-only" onClick={() => setCreateModalOpen(false)}>
                <Icon icon={X} size={16} />
              </button>
            </div>
            <div className="modal-body">
              <label className="form-field">
                <span className="form-label">表名称</span>
                <input
                  className="form-input"
                  type="text"
                  value={newTableName}
                  onChange={(e) => setNewTableName(e.target.value)}
                  placeholder="例如：2026 年 8 月发票"
                />
              </label>
              <label className="form-field">
                <span className="form-label">字段模板</span>
                <select
                  className="form-select"
                  value={newTableTemplate}
                  onChange={(e) => setNewTableTemplate(e.target.value)}
                >
                  <option value="">请选择模板</option>
                  {templates.filter(template => template.engine_kind !== "autonomous").map((t) => (
                    <option key={t.id} value={t.id}>{t.name}</option>
                  ))}
                </select>
              </label>
            </div>
            <div className="modal-foot">
              <button className="btn ghost" onClick={() => setCreateModalOpen(false)}>取消</button>
              <button
                className="btn primary"
                disabled={!newTableName.trim() || !newTableTemplate || busyAction}
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
        <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget) setDeleteConfirmOpen(false); }}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 420 }}>
            <div className="modal-head">
              <div>
                <div className="modal-eyebrow"><Icon icon={Trash2} size={15} /> 删除表</div>
                <h3>删除“{currentSheetName}”？</h3>
                <div className="support">将删除该表、全部当前数据、分 Sheet 视图及独立数据历史。关联文件的原件与文件修订历史保留，模板仍可用于后续提取。此操作不可恢复。</div>
              </div>
              <button aria-label="关闭" className="btn ghost sm icon-only" onClick={() => setDeleteConfirmOpen(false)}>
                <Icon icon={X} size={16} />
              </button>
            </div>
            <div className="modal-foot">
              <button className="btn ghost" onClick={() => setDeleteConfirmOpen(false)}>取消</button>
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
          <div className="modal-card" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 440 }}>
            <div className="modal-head">
              <div>
                <div className="modal-eyebrow"><Icon icon={Columns3} size={15} /> 字段筛选</div>
                <h3>显示字段</h3>
                <div className="support">勾选要显示的列，取消则隐藏该列。</div>
              </div>
              <button className="btn ghost sm icon-only" onClick={() => setFieldFilterOpen(false)}>
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
        <div className="modal-overlay" onClick={(event) => { if (event.target === event.currentTarget) setAddColumnOpen(false); }}>
          <div className="modal-card" style={{ maxWidth: 480 }}>
            <div className="modal-head">
              <div><h3>新增附加列</h3><div className="support">用于整理当前数据，不改变模板的提取规则；填写后也会记录在文件修订中。</div></div>
              <button aria-label="关闭" className="btn ghost sm icon-only" onClick={() => setAddColumnOpen(false)}><Icon icon={X} size={16} /></button>
            </div>
            <div className="modal-body">
              <label className="form-field"><span>列名</span><input autoFocus className="form-input" maxLength={64} value={newColumnLabel} onChange={(event) => setNewColumnLabel(event.target.value)} placeholder="例如：内部备注" /></label>
              <label className="form-field"><span>字段类型</span><select className="form-input" value={newColumnType} onChange={event => setNewColumnType(event.target.value as typeof newColumnType)}>
                <option value="text">文字</option><option value="number">数字</option><option value="date">日期</option><option value="boolean">是 / 否</option>
              </select></label>
              <div className="form-field"><span>填写方式</span><div className="column-mode-options" role="group" aria-label="附加列填写方式">
                <button type="button" className={`column-mode-button${newColumnSection === "header" ? " active" : ""}`} onClick={() => setNewColumnSection("header")}><strong>按表头填写</strong><span>同一文件共用一个值，导出时合并单元格</span></button>
                <button type="button" className={`column-mode-button${newColumnSection === "item" ? " active" : ""}`} onClick={() => setNewColumnSection("item")}><strong>按明细填写</strong><span>每条明细可以分别填写不同内容</span></button>
              </div></div>
            </div>
            <div className="modal-foot"><button className="btn ghost" onClick={() => setAddColumnOpen(false)}>取消</button><button className="btn primary" disabled={busyAction || !newColumnLabel.trim()} onClick={() => void handleAddColumn()}>新增列</button></div>
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
        onClose={() => {setIndependentHistoryOpen(false); setRevisionsOpen(false);}} />}

      <ConfirmDialog open={clearSourcesConfirm} title="清空选中来源数据"
        description={<>{`将清空 ${selectedSources.length} 个完整来源的 ${selectedSources.reduce((count, source) => count + source.row_count, 0)} 条当前记录。文件原件和修订历史保留；这些数据将不再出现在统计、问知意或后续导出中。`}<span className="confirm-source-list" role="list" aria-label="将清空的完整来源">{selectedSources.map(source => <span role="listitem" key={source.id}>{source.filename || "独立记录"} · {source.row_count} 条</span>)}</span></>}
        buttonLabel="确认清空" busy={busyAction} onConfirm={() => void handleClearSources()} onClose={() => setClearSourcesConfirm(false)} />
      <ConfirmDialog open={deleteRowsConfirm} title={`删除 ${selectedRows.length} 条明细？`}
        description="这些明细将从当前数据中删除，并保留删除前内容的修订记录。文件原件保留；若删去文件的最后一条明细，该文件的共享字段也会从当前数据中清空。"
        buttonLabel="确认删除" busy={busyAction} onConfirm={() => void handleDeleteRows()} onClose={() => setDeleteRowsConfirm(false)} />
      <ConfirmDialog open={deleteColumnTarget !== null} title={`删除附加列“${deleteColumnTarget?.label ?? ""}”？`}
        description="仅能删除整张表中没有当前值、且未被分组或仪表盘使用的附加列。已有数据或依赖时会拒绝删除。历史内容保留。"
        buttonLabel="确认删除" busy={busyAction} onConfirm={() => { if (deleteColumnTarget) void handleDeleteColumn(deleteColumnTarget); }} onClose={() => setDeleteColumnTarget(null)} />
      <Toast toast={toast} onDismiss={clear} />
    </div>
  );
}
