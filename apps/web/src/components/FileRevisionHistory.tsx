import { useEffect, useRef, useState } from "react";
import { ArrowRight, History, X } from "lucide-react";
import { getFileRevision, getFileRevisions, type FileRevisionDetail, type FileRevisionSummary } from "../api";
import { serverDate } from "../time";
import { Icon } from "./Icon";

function summary(version: FileRevisionSummary) {
  if (version.operation === "extraction") return "初次提取";
  if (version.operation === "migration") return "升级时的数据基线";
  return [version.modified_fields ? `修改 ${version.modified_fields} 项` : "",
    version.deleted_details ? `删除 ${version.deleted_details} 条` : "",
    version.added_details ? `新增 ${version.added_details} 条` : ""].filter(Boolean).join("，") || "清空当前数据";
}
const display = (value: unknown) => value == null || value === "" ? "—" : typeof value === "boolean" ? value ? "是" : "否" : String(value);

export function FileRevisionHistory({ taskId, filename, onClose }: {taskId: string; filename: string; onClose: () => void}) {
  return <FileRevisionHistoryContent key={taskId} taskId={taskId} filename={filename} onClose={onClose} />;
}

function FileRevisionHistoryContent({ taskId, filename, onClose }: {taskId: string; filename: string; onClose: () => void}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const changes = useRef<HTMLDivElement>(null);
  const [versions, setVersions] = useState<FileRevisionSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<string | null>(null);
  const [details, setDetails] = useState<FileRevisionDetail | null>(null);
  const [detailPage, setDetailPage] = useState(1);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    return () => previous?.focus();
  }, []);
  useEffect(() => {
    let active = true;
    setLoading(true); setError("");
    getFileRevisions(taskId, page).then(result => {
      if (!active) return;
      setVersions(current => page === 1 ? result.revisions : [...current, ...result.revisions]);
      setTotal(result.total);
      setSelected(current => current ?? result.revisions[0]?.id ?? null);
    }).catch(e => { if (active) setError(e.message); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [taskId, page]);
  useEffect(() => {
    if (!selected) return;
    let active = true;
    setDetails(null); setError("");
    if (changes.current) changes.current.scrollTop = 0;
    getFileRevision(taskId, selected, detailPage).then(result => { if (active) setDetails(result); })
      .catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [taskId, selected, detailPage]);
  const labels = new Map(details?.schema.map(field => [field.key, field.label]));
  function ordered(keys: string[]) {
    const present = new Set(keys);
    return [...labels.keys()].filter(key => present.delete(key)).concat([...present]);
  }
  function difference(key: string, before: unknown, after: unknown) {
    return <div className="revision-diff-row" key={key}>
      <span className="revision-field">{labels.get(key) ?? key}</span>
      <span className="revision-before">{display(before)}</span><Icon icon={ArrowRight} size={12} className="revision-arrow" />
      <span className="revision-after">{display(after)}</span>
    </div>;
  }
  return <dialog ref={dialog} className="template-history-dialog file-revision-dialog" aria-labelledby="file-revision-title"
    onCancel={event => { event.preventDefault(); onClose(); }}>
    <div className="template-history-head"><div><h3 id="file-revision-title"><Icon icon={History} size={18} /> 文件修订历史</h3>
      <p className="support" title={filename}>{filename}</p></div>
      <button className="btn ghost icon-only sm" aria-label="关闭文件修订历史" onClick={onClose}><Icon icon={X} size={18} /></button>
    </div>
    <div className="template-history-body">
      <nav className="template-version-list" aria-label="文件版本">{versions.map(version => <button key={version.id} aria-pressed={selected === version.id}
        onClick={() => { setDetailPage(1); setSelected(version.id); }}>
        <strong>V{version.version}{!version.sealed ? " · 本轮修改" : ""}</strong>
        <span>{serverDate(version.created_at).toLocaleString("zh-CN", {hour12: false})}</span><span>{summary(version)}</span>
      </button>)}{loading && <p className="support">读取中…</p>}
      {versions.length < total && <button disabled={loading} onClick={() => setPage(value => value + 1)}>更早的版本</button>}</nav>
      <div ref={changes} className="template-version-detail" aria-live="polite">
        {error && <p role="alert" className="callout danger">{error}</p>}
        {!loading && !versions.length && <p className="support">还没有修订记录。</p>}
        {selected && !details && !error && <p className="support">正在读取变化…</p>}
        {details && <><h4>V{details.version} · {summary(details)}</h4>
          {Object.keys(details.header).length > 0 && <section className="revision-item"><h5>文件级</h5><div className="revision-diff">
            {ordered(Object.keys(details.header)).map(key => difference(key, details.header[key].before, details.header[key].after))}
          </div></section>}
          {details.items.map(change => {
            const item = change.after ?? change.deleted_before ?? change.before!;
            const keys = ordered([...Object.keys(change.before?.values ?? {}), ...Object.keys(change.after?.values ?? {})]);
            return <section className="revision-item" key={item.id}><h5>明细 {item.item_index}{!change.after ? " · 已删除" : !change.before ? " · 新增" : ""}</h5>
              {change.deleted_before && <>
                <p className="support">{change.before ? "本轮修改后删除，以下保留删除前的完整内容。" : "本轮新增后删除，以下保留删除前的完整内容。"}</p>
                {change.before && <div className="revision-diff">{ordered(Object.keys(change.deleted_before.values))
                  .filter(key => change.before?.values[key] !== change.deleted_before!.values[key])
                  .map(key => difference(key, change.before?.values[key], change.deleted_before!.values[key]))}</div>}
              </>}
              {!change.after ? <dl className="file-revision-deleted">{ordered(Object.keys(item.values)).map(key => <div key={key}><dt>{labels.get(key) ?? key}</dt><dd>{display(item.values[key])}</dd></div>)}</dl> :
                <div className="revision-diff">{keys.filter(key => change.before?.values[key] !== change.after?.values[key])
                  .map(key => difference(key, change.before?.values[key], change.after?.values[key]))}</div>}
            </section>;
          })}
          {details.total_details > details.page_size && <div className="file-revision-pagination">
            <button className="btn secondary sm" disabled={detailPage === 1} onClick={() => setDetailPage(value => value - 1)}>上一页明细</button>
            <span className="support">{detailPage} / {Math.ceil(details.total_details / details.page_size)}</span>
            <button className="btn secondary sm" disabled={detailPage * details.page_size >= details.total_details} onClick={() => setDetailPage(value => value + 1)}>下一页明细</button>
          </div>}
        </>}
      </div>
    </div>
    <div className="template-history-foot"><p className="support">文件详情和数据仓库共用当前数据。这里保留每轮修改及删除前的内容，原文件仍是原始证据。</p></div>
  </dialog>;
}
