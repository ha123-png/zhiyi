import { useEffect, useRef, useState } from "react";
import { ArrowRight, History, X } from "lucide-react";
import { getIndependentHistory } from "../api";
import type { TableColumnDef } from "../types";
import { serverDate } from "../time";
import { Icon } from "./Icon";
import { Disclosure } from "./Disclosure";

const operations: Record<string, string> = {
  manual_create: "手动新增", import_create: "从文件导入", merge_create: "由表合并生成",
  table_edit: "手动编辑", merge: "由表合并生成", update: "手动编辑", delete: "已删除",
};
const display = (value: unknown) => value == null || value === "" ? "—" : typeof value === "boolean" ? value ? "是" : "否" : typeof value === "object" ? JSON.stringify(value) : String(value);

export function IndependentHistory({tableId, tableName, rowId, columns, onClose}: {
  tableId: string; tableName: string; rowId?: number; columns: TableColumnDef[]; onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [page, setPage] = useState(1);
  const [data, setData] = useState<Awaited<ReturnType<typeof getIndependentHistory>> | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    return () => previous?.focus();
  }, []);
  useEffect(() => {
    let active = true;
    setData(null); setError("");
    getIndependentHistory(tableId, page, rowId).then(result => { if (active) setData(result); })
      .catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [tableId, rowId, page]);
  return <dialog ref={dialog} className="template-history-dialog independent-history-dialog" aria-labelledby="independent-history-title"
    onCancel={event => {event.preventDefault(); onClose();}}>
    <div className="template-history-head"><div><h3 id="independent-history-title"><Icon icon={History} size={18} /> 独立数据历史</h3>
      <p className="support">{tableName}{rowId ? ` · 记录 #${rowId}` : " · 手工、导入与合并数据，包含已删除记录"}</p></div>
      <button className="btn ghost sm icon-only" aria-label="关闭独立数据历史" onClick={onClose}><Icon icon={X} size={18} /></button>
    </div>
    <div className="independent-history-content" aria-live="polite">
      {error && <p role="alert" className="callout danger">{error}</p>}
      {!data && !error && <p className="support">读取中…</p>}
      {data?.total === 0 && <p className="support">没有独立数据的变更记录。关联文件的修改请在文件修订历史中查看。</p>}
      {data?.revisions.map((revision, index) => {
        const keys = [...new Set([...Object.keys(revision.before ?? {}), ...Object.keys(revision.after ?? {})])]
          .filter(key => !key.startsWith("__") && (revision.before === null || revision.after === null || revision.before[key] !== revision.after[key]));
        return <Disclosure key={revision.id} defaultOpen={Boolean(rowId && index === 0)}
          title={`记录 #${revision.row_id} · ${operations[revision.operation] ?? "变更"} · ${serverDate(revision.created_at).toLocaleString("zh-CN", {hour12: false})}`}>
          {revision.after === null && <p className="support">以下保留删除前的完整内容。</p>}
          <div className="revision-diff">{keys.map(key => <div className="revision-diff-row" key={key}>
            <span className="revision-field">{columns.find(column => column.key === key)?.label ?? key}</span>
            <span className="revision-before">{display(revision.before?.[key])}</span><Icon icon={ArrowRight} size={12} className="revision-arrow" />
            <span className="revision-after">{display(revision.after?.[key])}</span>
          </div>)}</div>
        </Disclosure>;
      })}
    </div>
    {data && data.total > data.page_size && <div className="template-history-foot file-revision-pagination">
      <button className="btn secondary sm" disabled={page === 1} onClick={() => setPage(value => value - 1)}>较新记录</button>
      <span className="support">{page} / {Math.ceil(data.total / data.page_size)}</span>
      <button className="btn secondary sm" disabled={page * data.page_size >= data.total} onClick={() => setPage(value => value + 1)}>更早记录</button>
    </div>}
  </dialog>;
}
