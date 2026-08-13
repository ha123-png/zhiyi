import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import {
  ArrowRight,
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
  addCustomColumn,
  createSplitViews,
  createTable,
  deleteDataRows,
  deleteTable,
  deleteCustomColumn,
  getTable,
  getTableExportUrl,
  getTables,
  getTableView,
  getTableViews,
  getRowRevisions,
  getTemplates,
  mergeTables,
  renameTable,
  updateDataRow,
} from "../api";
import { desktopApi } from "../desktop";
import type {
  DataRowRead,
  DataRowRevision,
  DataTableDetail,
  DataTableRead,
  DataViewDetail,
  DataViewRead,
  ExtractionTemplate,
  TableColumnDef,
} from "../types";
import { serverDate } from "../time";
import { Icon } from "./Icon";
import { EditableText } from "./EditableText";

// --- Types ---

interface ColumnDef {
  key: string;
  label: string;
  numeric: boolean;
  section?: string | null;
  width: number;
  userDefined: boolean;
}

const PAGE_SIZE = 10;
const IDENTITY_KEYS = new Set(["source_filename", "item_index"]);

// --- Helpers ---

function getCellValue(row: DataRowRead, colKey: string): string {
  const v = row.values[colKey];
  if (v == null) return "";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

interface DataTablePageProps {
  initialTemplateId?: string | null;
  initialTableId?: string | null;
  jumpNotice?: string | null;
  /** 跳转来源任务：对应 task_id 的记录行高亮标记 */
  highlightTaskId?: string | null;
}

export function DataTablePage({
  initialTemplateId = null,
  initialTableId = null,
  jumpNotice = null,
  highlightTaskId = null,
}: DataTablePageProps = {}) {
  const [dsFilterOpen, setDsFilterOpen] = useState(false);
  const [importMenuOpen, setImportMenuOpen] = useState(false);
  const [exportMenuOpen, setExportMenuOpen] = useState(false);
  const [fieldFilterOpen, setFieldFilterOpen] = useState(false);
  const [addColumnOpen, setAddColumnOpen] = useState(false);
  const [newColumnLabel, setNewColumnLabel] = useState("");
  const [newColumnSection, setNewColumnSection] = useState<"header" | "item">("header");
  const [mergeModalOpen, setMergeModalOpen] = useState(false);
  const [splitModalOpen, setSplitModalOpen] = useState(false);
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
  const [revisionsOpen, setRevisionsOpen] = useState(false);
  const [revisions, setRevisions] = useState<DataRowRevision[]>([]);
  const [revisionsLoading, setRevisionsLoading] = useState(false);
  const [revisionsError, setRevisionsError] = useState<string | null>(null);

  const [tables, setTables] = useState<DataTableRead[]>([]);
  const [tablesLoading, setTablesLoading] = useState(true);
  const [tablesError, setTablesError] = useState<string | null>(null);

  const [currentTableId, setCurrentTableId] = useState<string | null>(null);
  const [tableDetail, setTableDetail] = useState<DataTableDetail | DataViewDetail | null>(null);
  const [tableLoading, setTableLoading] = useState(false);
  const [tableError, setTableError] = useState<string | null>(null);
  const [tableNotice, setTableNotice] = useState<string | null>(null);

  const [page, setPage] = useState(1);
  const [jumpPageInput, setJumpPageInput] = useState("");
  const [searchText, setSearchText] = useState("");
  const [reloadTrigger, setReloadTrigger] = useState(0);
  const [views, setViews] = useState<DataViewRead[]>([]);
  const [currentViewId, setCurrentViewId] = useState<string | null>(null);
  const [splitFieldKey, setSplitFieldKey] = useState("");
  const [splitting, setSplitting] = useState(false);

  const [hiddenCols, setHiddenCols] = useState<string[]>([]);
  const [selectedRows, setSelectedRows] = useState<number[]>([]);
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

  const sidebarWidth = 240;

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
      ? getTableView(currentTableId, currentViewId, page, PAGE_SIZE)
      : getTable(currentTableId, page, PAGE_SIZE, searchText);
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
  }, [currentTableId, currentViewId, page, searchText, reloadTrigger]);

  useEffect(() => {
    if (!currentTableId) {
      setViews([]);
      return;
    }
    let cancelled = false;
    getTableViews(currentTableId)
      .then((nextViews) => {
        if (!cancelled) setViews(nextViews);
      })
      .catch((err) => {
        if (!cancelled) {
          setTableError(err instanceof Error ? err.message : "加载分 Sheet 失败");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [currentTableId, reloadTrigger]);

  // 跳转定位：找到包含该任务记录的第一页并高亮（最多扫描 50 页）
  useEffect(() => {
    if (!highlightTaskId || !currentTableId || currentViewId) return;
    let cancelled = false;
    void (async () => {
      try {
        const first = await getTable(currentTableId, 1, PAGE_SIZE);
        if (cancelled) return;
        const scanPages = Math.min(
          Math.max(1, Math.ceil(first.row_count / PAGE_SIZE)),
          50,
        );
        for (let p = 1; p <= scanPages; p += 1) {
          const detail = p === 1 ? first : await getTable(currentTableId, p, PAGE_SIZE);
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
  }, [highlightTaskId, currentTableId, currentViewId]);

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
      section: c.section,
      width: columnWidths[c.key] ?? (c.value_type === "number" ? 112 : 132),
      userDefined: Boolean(c.user_defined),
    }));
  }, [tableDetail, columnWidths]);

  const visibleColumns = columns.filter((col) => !hiddenCols.includes(col.key));
  const widthFor = (col: ColumnDef) => columnWidths[col.key] ?? col.width;
  const tableMinWidth = 44 + visibleColumns.reduce((sum, col) => sum + widthFor(col), 0);
  const rows = tableDetail?.rows ?? [];
  const totalRows = tableDetail?.row_count ?? 0;
  const totalPages = Math.max(1, Math.ceil(totalRows / PAGE_SIZE));

  const selectedCount = selectedRows.length;
  const allSelected = rows.length > 0 && selectedRows.length === rows.length;

  const currentSheetName =
    tableDetail?.name ?? tables.find((t) => t.id === currentTableId)?.name ?? "";
  const currentSheetSource =
    tableDetail && "source_table_name" in tableDetail
      ? `视图 · 来源 ${tableDetail.source_table_name}`
      : "文档提取";

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
  const placeholderCount = Math.max(0, PAGE_SIZE - rows.length);
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
    if (p < 1 || p > totalPages) return;
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
    if (allSelected) {
      setSelectedRows([]);
    } else {
      setSelectedRows(rows.map((row) => row.id));
    }
  }

  function toggleSelectRow(rowId: number) {
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

  async function handleExport(format: "xlsx" | "csv" | "json") {
    if (!currentTableId) return;
    setExportMenuOpen(false);
    const url = getTableExportUrl(currentTableId, format);
    const bridge = desktopApi();
    if (!bridge) {
      window.open(url, "_blank");
      return;
    }
    try {
      const absoluteUrl = new URL(url, window.location.origin).toString();
      const filename = `${currentSheetName || "数据表"}.${format}`;
      const path = await bridge.export_table(absoluteUrl, filename);
      setTableError(null);
      setTableNotice(`已导出到 ${path}`);
    } catch (err) {
      setTableError(err instanceof Error ? err.message : "导出失败");
    }
  }

  async function handleImportFile(file: File) {
    setImportMenuOpen(false);
    if (!currentTableId) return;
    setBusyAction(true);
    setTableError(null);
    try {
      const body = new FormData();
      body.append("file", file);
      const response = await fetch(`/api/v1/tables/${currentTableId}/import`, {
        method: "POST",
        body,
      });
      if (!response.ok) {
        const data = (await response.json().catch(() => null)) as { detail?: string } | null;
        throw new Error(data?.detail ?? "导入失败");
      }
      await response.json();
      setTableNotice(`已把「${file.name}」导入当前数据表。`);
      setReloadTrigger((n) => n + 1);
      await getTables().then(setTables);
    } catch (err) {
      setTableError(err instanceof Error ? err.message : "导入失败");
    } finally {
      setBusyAction(false);
      if (importInputRef.current) {
        importInputRef.current.value = "";
      }
    }
  }

  async function handleImportNewTable(file: File) {
    setImportMenuOpen(false);
    setBusyAction(true);
    setTableError(null);
    try {
      const body = new FormData(); body.append("file", file);
      const response = await fetch("/api/v1/tables/import-new", { method: "POST", body });
      const data = await response.json().catch(() => null) as { id?: string; name?: string; detail?: string } | null;
      if (!response.ok || !data?.id) throw new Error(data?.detail ?? "新建表导入失败");
      setCurrentTableId(data.id); setCurrentViewId(null);
      setTableNotice(`已从「${file.name}」新建数据表「${data.name ?? file.name}」。`);
      setReloadTrigger((n) => n + 1); await getTables().then(setTables);
    } catch (err) { setTableError(err instanceof Error ? err.message : "新建表导入失败"); }
    finally { setBusyAction(false); if (importNewInputRef.current) importNewInputRef.current.value = ""; }
  }

  async function commitCellEdit(row: DataRowRead, colKey: string, newValue: string) {
    if (!currentTableId || !tableDetail) return;
    const oldValue = getCellValue(row, colKey);
    if (newValue === oldValue) return;
    try {
      // 空串 = 清空该单元格（提交 null）；否则按输入值提交
      await updateDataRow(currentTableId, row.id, row.version, {
        [colKey]: newValue === "" ? null : newValue,
      });
      setReloadTrigger((n) => n + 1);
    } catch (err) {
      setTableError(err instanceof Error ? err.message : "保存失败");
    }
  }

  async function handleAddColumn() {
    if (!currentTableId || !newColumnLabel.trim()) return;
    setBusyAction(true);
    try {
      await addCustomColumn(currentTableId, newColumnLabel.trim(), newColumnSection);
      setAddColumnOpen(false);
      setNewColumnLabel("");
      setNewColumnSection("header");
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
    if (!window.confirm(`删除附加列“${column.label}”？这一列现有内容也会删除。`)) return;
    try {
      await deleteCustomColumn(currentTableId, column.key);
      setReloadTrigger((n) => n + 1);
      setTableNotice(`已删除附加列“${column.label}”。`);
    } catch (err) {
      setTableError(err instanceof Error ? err.message : "删除列失败");
    }
  }

  async function handleCreateSplitViews() {
    if (!currentTableId || !splitFieldKey) return;
    setSplitting(true);
    setTableError(null);
    try {
      const created = await createSplitViews(currentTableId, splitFieldKey);
      setViews(created);
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
      setPage(Math.max(1, Math.ceil((totalRows + 1) / PAGE_SIZE)));
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
      await deleteDataRows(currentTableId, rowIds);
      setSelectedRows([]);
      setReloadTrigger((n) => n + 1);
      await getTables().then(setTables);
    } catch (err) {
      setTableError(err instanceof Error ? err.message : "删除行失败");
    } finally {
      setBusyAction(false);
    }
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
    setRevisionsOpen(true);
    setRevisionsLoading(true);
    setRevisionsError(null);
    setRevisions([]);
    try {
      const items = await getRowRevisions(currentTableId, rowId);
      setRevisions(items);
    } catch (err) {
      setRevisionsError(err instanceof Error ? err.message : "读取修订历史失败");
    } finally {
      setRevisionsLoading(false);
    }
  }

  // 全局行索引（rowGroups 展开时递增）
  let rowIndexCounter = 0;

  return (
    <div className="view">
      {jumpNotice && (
        <div className="callout warning" style={{ marginBottom: 16 }}>
          {jumpNotice}
        </div>
      )}
      <div className="datastore-layout">
        {/* Toolbar */}
        <div className="datastore-toolbar">
          <div className="datastore-toolbar-left" style={{ gap: "calc(var(--space-4) * 2)" }}>
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
              disabled={!currentTableId || columns.length <= 1}
              onClick={() => setSplitModalOpen(true)}
              title={columns.length <= 1 ? "当前表没有可用于分 Sheet 的字段" : undefined}
            >
              <Icon icon={Table2} size={15} /> 分 Sheet
            </button>
            <div className="toolbar-divider" />
            <button
              className="btn secondary sm"
              disabled={tables.length < 2}
              onClick={() => setMergeModalOpen(true)}
              title={tables.length < 2 ? "至少需要两张表才能合并" : undefined}
            >
              <Icon icon={GitMerge} size={15} /> 合并表
            </button>
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
                  <button className="toolbar-menu-item" onClick={() => importInputRef.current?.click()}>
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
                  <button className="toolbar-menu-item" onClick={() => handleExport("xlsx")}>
                    <Icon icon={FileSpreadsheet} size={15} /> 导出 Excel (.xlsx)<span className="menu-hint">XLSX</span>
                  </button>
                  <button className="toolbar-menu-item" onClick={() => handleExport("csv")}>
                    <Icon icon={FileText} size={15} /> 导出 CSV (.csv)<span className="menu-hint">CSV</span>
                  </button>
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
                  setSearchText(event.target.value);
                  setPage(1);
                }}
              />
            </div>
          </div>
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
                  <div className={`sheet-tree-group-body${treeGroupsOpen.records ? " open" : ""}`}>
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
                      <span className="row-count">{t.row_count}</span>
                    </button>
                  ))}
                    </div>
                  </div>
                  <button
                    className="sheet-tree-group-title"
                    aria-expanded={treeGroupsOpen.views}
                    onClick={() =>
                      setTreeGroupsOpen((s) => ({ ...s, views: !s.views }))
                    }
                    title={treeGroupsOpen.views ? "收起分 Sheet 视图" : "展开分 Sheet 视图"}
                  >
                    <Icon icon={ChevronDown} size={16} />
                    <span className="sheet-tree-group-label">分 Sheet 视图</span>
                  </button>
                  <div className={`sheet-tree-group-body${treeGroupsOpen.views ? " open" : ""}`}>
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
                      {currentTableId ? "当前表还没有分 Sheet 视图" : "选择一张表查看其视图"}
                    </div>
                  )}
                    </div>
                  </div>
                </>
              )}
            </div>
          </div>

          <div className="splitter-v" onMouseDown={() => {}} />

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
                <div className="support">共 <span>{totalRows}</span> 行 · 来源 <span>{currentSheetSource}</span></div>
              </div>
              <div className="sheet-detail-actions">
                {!currentViewId && currentTableId ? (
                  <>
                    <button className="btn ghost sm" onClick={startRenameSheet}>
                      <Icon icon={Pencil} size={14} /> 重命名
                    </button>
                    <button className="btn danger-soft sm" onClick={() => setDeleteConfirmOpen(true)}>
                      <Icon icon={Trash2} size={14} /> 删除
                    </button>
                  </>
                ) : null}
              </div>
            </div>

            {tableError && (
              <div className="callout danger" style={{ marginBottom: 16 }}>{tableError}</div>
            )}
            {tableNotice && (
              <div className="callout success" style={{ marginBottom: 16 }}>{tableNotice}</div>
            )}

            <div className="data-table-wrap">
              {/* 行级操作栏 */}
              <div className="row-actions-bar">
                <div className="row-actions-left">
                  <button
                    className="btn xs danger-soft"
                    disabled={selectedRows.length === 0}
                    onClick={() => void handleDeleteRows()}
                  >
                    <Icon icon={Trash2} size={14} /> 删除选中
                  </button>
                  <button
                    className="btn xs secondary"
                    disabled={selectedRows.length !== 1}
                    onClick={() => {
                      const row = rows.find((r) => r.id === selectedRows[0]);
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
                    title="在表尾新增一行空记录，直接在表格里填写"
                  >
                    <Icon icon={Plus} size={14} /> 新增一行
                  </button>
                  <button className="btn xs secondary" onClick={() => setFieldFilterOpen(true)}>
                    <Icon icon={Columns3} size={14} /> 字段筛选
                    {hiddenCols.length > 0 && (
                      <span className="filter-count-badge">{hiddenCols.length}</span>
                    )}
                  </button>
                </div>
                <div className="row-actions-right">
                  {selectedCount > 0 && (
                    <span className="row-selected-hint">已选 <strong>{selectedCount}</strong> 行 · <button className="link-btn" onClick={() => setSelectedRows([])}>取消</button></span>
                  )}
                  <span className="row-total-hint">共 <span>{totalRows}</span> 行</span>
                </div>
              </div>
              <div className="data-table-scroll">
                {tableLoading ? (
                  <div style={{ padding: 48, textAlign: "center" }}>
                    <Icon icon={Loader2} size={24} className="spin" />
                  </div>
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
                                if (headerKeys.has(col.key)) {
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
              <div className="table-footer">
                <span className="footer-info">显示 <span>{rows.length === 0 ? 0 : (page - 1) * PAGE_SIZE + 1}</span>-<span>{(page - 1) * PAGE_SIZE + rows.length}</span> 行，共 <span>{totalRows}</span> 行</span>
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
              </div>
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
                {tables.map((s) => (
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
                <div className="modal-eyebrow"><Icon icon={Table2} size={15} /> 分 Sheet</div>
                <h3>按一个字段建立分组视图</h3>
                <div className="support">数据仍只保存一份；分 Sheet 只是同一批数据的分类视图。</div>
              </div>
              <button className="btn ghost sm icon-only" onClick={() => setSplitModalOpen(false)}>
                <Icon icon={X} size={16} />
              </button>
            </div>
            <div className="modal-body">
              <label className="form-field">
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
              </label>
            </div>
            <div className="modal-foot">
              <button className="btn ghost" onClick={() => setSplitModalOpen(false)}>取消</button>
              <button
                className="btn primary"
                disabled={!splitFieldKey || splitting}
                onClick={() => void handleCreateSplitViews()}
              >
                {splitting ? <Icon icon={Loader2} size={15} className="spin" /> : <Icon icon={Table2} size={15} />}
                创建分 Sheet
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
                  {templates.map((t) => (
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
                <div className="support">将同时删除该表的全部行、分 Sheet 视图和确认记录，此操作不可恢复。</div>
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
                        className="btn ghost sm icon-only"
                        type="button"
                        onClick={(event) => {
                          event.preventDefault();
                          event.stopPropagation();
                          void handleDeleteColumn(col);
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
              <div><h3>新增附加列</h3><div className="support">仅用于整理和导出，不参与模型提取，也不改变文件历史。</div></div>
              <button aria-label="关闭" className="btn ghost sm icon-only" onClick={() => setAddColumnOpen(false)}><Icon icon={X} size={16} /></button>
            </div>
            <div className="modal-body">
              <label className="form-field"><span>列名</span><input autoFocus className="form-input" maxLength={64} value={newColumnLabel} onChange={(event) => setNewColumnLabel(event.target.value)} placeholder="例如：内部备注" /></label>
              <div className="form-field"><span>填写方式</span><div className="column-mode-options" role="group" aria-label="附加列填写方式">
                <button type="button" className={`column-mode-button${newColumnSection === "header" ? " active" : ""}`} onClick={() => setNewColumnSection("header")}><strong>按表头填写</strong><span>同一文件共用一个值，导出时合并单元格</span></button>
                <button type="button" className={`column-mode-button${newColumnSection === "item" ? " active" : ""}`} onClick={() => setNewColumnSection("item")}><strong>按明细填写</strong><span>每条明细可以分别填写不同内容</span></button>
              </div></div>
            </div>
            <div className="modal-foot"><button className="btn ghost" onClick={() => setAddColumnOpen(false)}>取消</button><button className="btn primary" disabled={busyAction || !newColumnLabel.trim()} onClick={() => void handleAddColumn()}>新增列</button></div>
          </div>
        </div>
      )}

      {/* 行修订历史模态 */}
      {revisionsOpen && (
        <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget) setRevisionsOpen(false); }}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 560 }}>
            <div className="modal-head">
              <div>
                <div className="modal-eyebrow"><Icon icon={History} size={15} /> 修订历史</div>
                <h3>选中行的变更记录</h3>
                <div className="support">记录这行数据从哪里来，以及之后由谁在何时修改了哪些字段。</div>
              </div>
              <button className="btn ghost sm icon-only" onClick={() => setRevisionsOpen(false)}>
                <Icon icon={X} size={16} />
              </button>
            </div>
            <div className="modal-body">
              {revisionsError && <div className="callout danger">{revisionsError}</div>}
              {revisionsLoading ? (
                <div style={{ padding: 24, textAlign: "center" }}>
                  <Icon icon={Loader2} size={18} className="spin" />
                </div>
              ) : revisions.length === 0 ? (
                <div className="small muted" style={{ padding: 16, textAlign: "center" }}>
                  这行还没有任何变更记录。
                </div>
              ) : (
                <div className="revision-list">
                  {revisions.map((revision) => (
                    <div className="revision-item" key={revision.id}>
                      <div className="revision-head">
                        <span className="revision-badge">v{revision.version}</span>
                        <span className="revision-op">{REVISION_OPERATION[revision.operation] ?? revision.operation}</span>
                        <span className="revision-meta">{revision.editor} · {formatRevisionTime(revision.created_at)}</span>
                      </div>
                      <div className="revision-diff">
                        {diffFields(revision.before, revision.after).length === 0 ? (
                          <span className="small muted">无字段变化</span>
                        ) : (
                          diffFields(revision.before, revision.after).map((key) => (
                            <div className="revision-diff-row" key={key}>
                              <span className="revision-field">
                                {tableDetail?.columns.find((column) => column.key === key)?.label ?? key}
                              </span>
                              <span className="revision-before">{String(revision.before?.[key] ?? "—")}</span>
                              <Icon icon={ArrowRight} size={12} className="revision-arrow" />
                              <span className="revision-after">{String(revision.after?.[key] ?? "—")}</span>
                            </div>
                          ))
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div className="modal-foot">
              <button className="btn primary" onClick={() => setRevisionsOpen(false)}>关闭</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const REVISION_OPERATION: Record<string, string> = {
  manual_create: "手动新增",
  import_create: "从文件导入",
  merge_create: "由表合并生成",
  materialize_create: "由文件提取生成",
  materialize_move: "移动到指定表",
  materialize_update: "同步提取结果",
  materialize_delete: "提取结果中已删除",
  table_edit: "手动编辑",
  merge: "由表合并生成",
  update: "手动编辑",
  delete: "删除",
  review_sync: "保存到表",
};

function formatRevisionTime(iso: string): string {
  const d = serverDate(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function diffFields(
  before: Record<string, unknown> | null,
  after: Record<string, unknown> | null,
): string[] {
  const keys = new Set([
    ...Object.keys(before ?? {}),
    ...Object.keys(after ?? {}),
  ]);
  return [...keys].filter((key) => (before?.[key] ?? null) !== (after?.[key] ?? null));
}
