import type { ToolResult } from "./types";

type Item = {
  label: string;
  name?: string;
  note?: string;
  columns?: Record<string, string>;
  operation: { kind: string; table_id?: string; changes?: Record<string, unknown> };
  affected: { row_id?: number; task_id?: string; name?: string; before: unknown; after: unknown; model?: string; provider?: string }[];
  before: unknown;
  after: unknown;
};
const value = (v: unknown): string =>
  v == null ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v);
const record = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : {};
const labels: Record<string, string> = {
  pause: "暂停", resume: "恢复", retry: "重试", cancel: "取消",
  queued: "排队中", processing: "处理中", failed: "失败", paused: "已暂停",
  completed: "已完成", needs_review: "待核验", cancelled: "已取消",
};
export function containsTemplateWrite(result: ToolResult) {
  return !!result.draft || (Array.isArray(result.items) && result.items.some(item =>
    String((item as Item).operation?.kind || "").includes("template")));
}

type Change = { id: string; row: number | undefined; name?: string; field: string; before: unknown; after: unknown };
type ChangeGroup = { name: string; fields: string[]; columns: Record<string, string>; changes: Map<string, Change>; notes: Set<string>; increments: Set<string> };

export function OperationPreview({ result }: { result: ToolResult }) {
  const items = (result.items || []) as Item[];
  const groups = new Map<string, ChangeGroup>();
  const other: Item[] = [];
  const affectedRecords = new Set<string>();
  for (const item of items) {
    if (!["update_rows", "increment_rows"].includes(item.operation.kind)) {
      other.push(item);
      continue;
    }
    const fields = [...new Set(item.affected.flatMap(row => Object.keys(record(row.after))))].filter(k => !k.startsWith("__")).sort();
    const table = item.operation.table_id || item.name || "数据表";
    const key = JSON.stringify([table, fields]);
    const group: ChangeGroup = groups.get(key) || { name: item.name || "数据表", fields, columns: {}, changes: new Map(), notes: new Set(), increments: new Set() };
    Object.assign(group.columns, item.columns);
    if (item.note) group.notes.add(item.note);
    if (item.operation.kind === "increment_rows") {
      const deltas = Object.entries(item.operation.changes || {}).map(([field, delta]) =>
        `${item.columns?.[field] || field}${typeof delta === "number" ? `${delta < 0 ? "减少" : "增加"} ${Math.abs(delta).toLocaleString("zh-CN")}` : "调整数值"}`);
      group.increments.add(deltas.join("、"));
    } else group.increments.add("");
    for (const row of item.affected) {
      affectedRecords.add(`${table}:${row.row_id}`);
      for (const field of Object.keys(record(row.after)).filter(k => !k.startsWith("__"))) {
        const id = `${row.row_id}:${field}`;
        // Shared fields may occur in several operations; the server rejects conflicts.
        group.changes.set(id, { id, row: row.row_id, name: row.name, field, before: record(row.before)[field], after: record(row.after)[field] });
      }
    }
    groups.set(key, group);
  }
  return <div className="ask-plan">
    <h4>{String(result.title || "变更预览")}</h4>
    {!!affectedRecords.size && <p className="ask-plan-summary">影响 {affectedRecords.size} 条记录 · {Array.from(groups.values()).reduce((sum, group) => sum + group.changes.size, 0)} 处字段变更</p>}
    {Array.from(groups.entries()).map(([key, group]) => <section className="ask-plan-item" key={key}>
      <h5>{group.name} · {group.fields.map(field => group.columns[field] || field).join("、")}</h5>
      {group.increments.size === 1 && Array.from(group.increments)[0] && <p className="ask-plan-note">{Array.from(group.increments)[0]}</p>}
      <div className="ask-data-scroll" role="region" aria-label={`${group.name}变更记录`} tabIndex={0}>
        <table className="ask-change-table"><thead><tr><th>记录</th>{group.fields.length > 1 && <th>字段</th>}<th>修改前</th><th>修改后</th></tr></thead>
          <tbody>{Array.from(group.changes.values()).map(change => <tr key={change.id}>
            <td>{change.name ? <span className="ask-record-label">{change.name}<small>#{change.row}</small></span> : `#${change.row}`}</td>{group.fields.length > 1 && <td>{group.columns[change.field] || change.field}</td>}
            <td>{value(change.before)}</td><td>{value(change.after)}</td>
          </tr>)}</tbody></table>
      </div>
      {Array.from(group.notes).map(note => <p className="ask-plan-note" key={note}>{note}</p>)}
    </section>)}
    {other.map((item, index) => <section className="ask-plan-item" key={index}>
      <h5>{item.label}{item.name ? ` · ${item.name}` : ""}</h5>
      {item.operation.kind.includes("template") ? <p>此历史模板提案已停止支持执行。请在模板页查看或编辑模板。</p> : <>
        {!!item.affected.length && <div className="ask-data-scroll" role="region" aria-label={`${item.name || item.label}变更记录`} tabIndex={0}>
          <table><thead><tr><th>对象</th><th>字段 / 模型</th><th>修改前</th><th>修改后</th></tr></thead>
            <tbody>{item.affected.flatMap((row, i) => row.task_id ? <tr key={row.task_id}>
              <td>{row.name}</td><td>{row.model} · {row.provider}</td><td>{labels[value(row.before)] || value(row.before)}</td><td>{labels[value(row.after)] || value(row.after)}</td>
            </tr> : Object.keys(record(row.after === null ? row.before : row.after)).filter(k => !k.startsWith("__")).map(k => <tr key={`${i}-${k}`}>
              <td>#{row.row_id}</td><td>{item.columns?.[k] || k}</td><td>{value(record(row.before)[k])}</td><td>{row.after === null ? "删除记录" : value(record(row.after)[k])}</td>
            </tr>))}</tbody></table>
        </div>}
        {!item.affected.length && (item.operation.kind === "add_rows" && Array.isArray(item.after) ? <>
          <p>新增 {item.after.length} 条记录</p>
          <div className="ask-data-scroll" role="region" aria-label="新增记录预览" tabIndex={0}><table>
            <thead><tr>{Object.keys(item.columns || {}).map(key => <th key={key}>{item.columns![key]}</th>)}</tr></thead>
            <tbody>{item.after.map((row, i) => <tr key={i}>{Object.keys(item.columns || {}).map(key => <td key={key}>{value(record(row)[key])}</td>)}</tr>)}</tbody>
          </table></div>
        </> : <p>{item.operation.kind === "create_table" ? `新建数据表「${value(item.after)}」，使用模板「${item.name || "当前模板"}」。` : `${item.before != null ? `${value(item.before)} → ` : ""}${value(item.after)}`}</p>)}
        {item.note && <p className="ask-plan-note">{item.note}</p>}
      </>}
    </section>)}
  </div>;
}
