import type { DataRowRead, SourceCard, SourceCardDetail, TableColumnDef } from "../types";

export const EXAMPLE_WAREHOUSE_ID = "warehouse-example";
export const EXAMPLE_WAREHOUSE_NAME = "示例送货单";

export const exampleColumns: TableColumnDef[] = [
  { key: "customer", label: "客户", section: "header", value_type: "text" },
  { key: "number", label: "单号", section: "header", value_type: "text" },
  { key: "total", label: "合计金额", section: "header", value_type: "number" },
  { key: "item", label: "商品", section: "item", value_type: "text" },
  { key: "qty", label: "数量", section: "item", value_type: "number" },
  { key: "price", label: "单价", section: "item", value_type: "number" },
  { key: "amount", label: "金额", section: "item", value_type: "number" },
];

const files = [
  { filename: "示例送货单-001.pdf", customer: "云栖设计工作室", number: "DEMO-001", total: 376,
    items: [{ item: "打印纸", qty: 12, price: 28, amount: 336 }, { item: "水笔", qty: 5, price: 8, amount: 40 }] },
  { filename: "示例送货单-002.pdf", customer: "青禾工作室", number: "DEMO-002", total: 180,
    items: [{ item: "文件夹", qty: 15, price: 12, amount: 180 }] },
];

const headerKeys = exampleColumns.filter(column => column.section === "header").map(column => column.key);

export const exampleRows: DataRowRead[] = files.flatMap(({ items, filename, ...header }, fileIndex) =>
  items.map((item, index) => ({
    id: fileIndex * 10 + index + 1, task_id: null, item_index: index + 1,
    version: 1, created_at: "", updated_at: "", shared_header_keys: headerKeys,
    values: { ...header, ...item, source_filename: filename, __row_group: filename },
  })),
);

export function exampleSourceId(row: DataRowRead): string {
  return String(row.values.__row_group ?? row.id);
}

export function editExampleRow(rows: DataRowRead[], id: number, key: string, value: unknown): DataRowRead[] {
  const row = rows.find(candidate => candidate.id === id);
  if (!row) return rows;
  const shared = row.shared_header_keys?.includes(key);
  return rows.map(candidate => candidate.id === id || (shared && exampleSourceId(candidate) === exampleSourceId(row))
    ? { ...candidate, version: candidate.version + 1, values: { ...candidate.values, [key]: value } }
    : candidate);
}

function fields(section: "header" | "item", row: DataRowRead) {
  return exampleColumns.filter(column => column.section === section)
    .map(({ key, label }) => ({ key, label, value: row.values[key], truncated: false }));
}

export function warehouseExampleCards(rows: readonly DataRowRead[], matches: readonly DataRowRead[]): SourceCard[] {
  const matchedIds = new Set(matches.map(row => row.id));
  const groups = new Map<string, DataRowRead[]>();
  for (const row of rows) {
    const id = exampleSourceId(row);
    const group = groups.get(id);
    if (group) group.push(row);
    else groups.set(id, [row]);
  }
  return Array.from(groups).flatMap(([id, group]) => {
    const matched = group.filter(row => matchedIds.has(row.id));
    if (!matched.length) return [];
    const first = group[0];
    return [{
      id, kind: "group", task_id: null, filename: String(first.values.source_filename ?? `独立数据组 · ${first.id}`),
      version: group.map(row => row.version).join("-"),
      first_row_id: first.id, row_count: group.length, item_count: group.length,
      matched_row_count: matched.length, complete: matched.length === group.length, review_pending: false, partial_input: false,
      shared_header_keys: first.shared_header_keys ?? [], preview_header: fields("header", first),
      preview_items: matched.slice(0, 1).map(row => ({ id: row.id, item_index: row.item_index, fields: fields("item", row) })),
    }];
  });
}

export function warehouseExampleDetail(card: SourceCard, rows: readonly DataRowRead[], page: number): SourceCardDetail {
  const group = rows.filter(row => exampleSourceId(row) === card.id);
  const pageSize = 10;
  return { ...card, page, page_size: pageSize, total: group.length, schema: exampleColumns,
    header: group[0]?.values ?? {}, rows: group.slice((page - 1) * pageSize, page * pageSize) };
}
