import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { AnalysisCard } from "./AnalysisCard";
import type { Analysis } from "./types";

const businessId = "a1234567-89ab-cdef-0123-456789abcdef";
const tableId = "internal-table-identity-77";
const taskId = "internal-task-identity-88";

function openDetails() {
  fireEvent.click(screen.getByRole("button", { name: "来源" }));
  fireEvent.click(screen.getByRole("button", { name: "查询条件" }));
  const details = document.querySelector<HTMLElement>(".ask-query-conditions");
  expect(details).not.toBeNull();
  return details!;
}

it("opens readable aggregate query details without internal identities and retains UUID business criteria", () => {
  const request = {
    table_id: tableId, row_ids: [714220, 714999], task_id: taskId,
    filters: [{ field: "customer", op: "eq", value: businessId }, { field: "amount", op: "gte", value: 0 }],
    dimensions: ["customer"], metrics: [{ op: "sum", field: "amount" }, { op: "count", field: null }],
    time_bucket: null, grain: "row", sort: "sum:amount", descending: false,
    limit: 10, records: false, search: businessId,
  };
  const original = structuredClone(request);
  const analysis: Analysis & { dimension_labels: Record<string, string> } = {
    analysis_id: "query-aggregate", source: { table_id: tableId, table_name: "采购记录", row_count: 2,
      document_count: 1, grain: "row", generated_at: "2026-11-01", request },
    data: [{ label: businessId, "sum:amount": 30, "count:*": 2 }],
    metric_keys: ["sum:amount", "count:*"], metric_labels: { "sum:amount": "金额合计", "count:*": "记录数" },
    dimension_labels: { customer: "客户编号" }, warnings: [], truncated: false,
  };
  const navigate = vi.fn();
  render(<AnalysisCard analysis={analysis} navigate={navigate} />);
  const details = openDetails();
  expect(details).not.toHaveTextContent(/table_id|row_ids|task_id|internal-table|internal-task|714220|714999/);
  expect(details).toHaveTextContent("客户编号");
  expect(details).toHaveTextContent("金额合计");
  expect(details).toHaveTextContent("记录数");
  expect(details).toHaveTextContent(businessId);
  expect(details).toHaveTextContent("不小于");
  expect(details).toHaveTextContent("升序");
  expect(details).toHaveTextContent("10");
  fireEvent.click(screen.getByRole("button", { name: "采购记录" }));
  expect(navigate).toHaveBeenCalledWith({ kind: "table", id: tableId });
  expect(analysis.source.request).toBe(request);
  expect(request).toEqual(original);
});

it("keeps a business field named task_id and record navigation while describing scoped identities without values", () => {
  const request = {
    table_id: tableId, task_id: taskId, row_ids: [714220],
    filters: [{ field: "task_id", op: "eq", value: businessId }], dimensions: [],
    metrics: [{ op: "count", field: null }], time_bucket: null, grain: "auto",
    sort: "row_id", descending: true, limit: 30, records: true, search: "",
  };
  const original = structuredClone(request);
  const analysis: Analysis = {
    analysis_id: "query-record", source: { table_id: tableId, table_name: "采购记录", row_count: 1,
      document_count: 1, grain: "auto", generated_at: "2026-11-01", request },
    columns: [{ key: "task_id", label: "业务任务编号" }],
    rows: [{ row_id: 714220, version: 1, task_id: taskId, values: { task_id: businessId } }],
    warnings: [], truncated: false,
  };
  const navigate = vi.fn();
  render(<AnalysisCard analysis={analysis} navigate={navigate} />);
  const details = openDetails();
  expect(details).not.toHaveTextContent(/table_id|row_ids|task_id|internal-table|internal-task|714220/);
  expect(details).toHaveTextContent("业务任务编号");
  expect(details).toHaveTextContent(businessId);
  expect(details).toHaveTextContent("记录顺序");
  expect(details).toHaveTextContent("明细记录");
  fireEvent.click(screen.getByRole("button", { name: "查看记录" }));
  expect(navigate).toHaveBeenCalledWith({ kind: "table", id: tableId, row_id: 714220 });
  expect(request).toEqual(original);
});

it("uses the business dimension label when row_id is an actual grouping and sort field", () => {
  const analysis: Analysis & { dimension_labels: Record<string, string> } = {
    analysis_id: "business-row-field", source: { table_id: tableId, table_name: "采购记录", row_count: 1,
      document_count: 1, grain: "row", generated_at: "2026-11-01", request: {
        table_id: tableId, row_ids: [714220], dimensions: ["row_id"],
        filters: [{ field: "row_id", op: "eq", value: businessId }],
        metrics: [{ op: "count", field: null }], sort: "row_id", descending: false, records: false,
      } },
    dimension_labels: { row_id: "业务行号" },
    data: [{ label: businessId, "count:*": 1 }], metric_keys: ["count:*"], warnings: [], truncated: false,
  };
  render(<AnalysisCard analysis={analysis} navigate={() => {}} />);
  const details = openDetails();
  const sort = [...details.querySelectorAll("dt")].find(item => item.textContent === "排序");
  expect(sort?.nextElementSibling).toHaveTextContent("业务行号 · 升序");
  expect(details).toHaveTextContent(businessId);
  expect(details).not.toHaveTextContent(/714220|internal-table|"row_id"/);
});
