import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { OperationPreview } from "./OperationPreview";
import { AnalysisCard } from "./AnalysisCard";
import type { Analysis } from "./types";

it("separates record counts from amounts instead of sharing one misleading axis", () => {
  const analysis: Analysis = {
    analysis_id: "mixed", source: {table_id:"t", table_name:"采购", row_count:3,
      document_count:3, grain:"row", generated_at:"2026-09-13", request:{}},
    data:[{label:"A", "sum:total":6000, "count:*":2}, {label:"B", "sum:total":3000, "count:*":1}],
    metric_keys:["sum:total", "count:*"], warnings:[], truncated:false,
  };
  render(<AnalysisCard analysis={analysis} navigate={() => {}} spec={{analysis_id:"mixed", type:"bar", title:"采购", x:"label", series:analysis.metric_keys!}} />);
  expect(screen.getByRole("combobox", {name:"图表展示方式"})).toHaveValue("composed");
  expect(screen.getByText(/左轴：数值；右轴：记录数/)).toBeInTheDocument();
  expect(screen.queryByRole("option", {name:"堆叠柱状图"})).not.toBeInTheDocument();
});

it("keeps legacy template proposals readable without offering template writes", () => {
  render(
    <OperationPreview
      result={{
        title: "创建收支模板",
        kind: "operation_plan",
        items: [
          {
            label: "创建模板",
            operation: { kind: "create_template" },
            affected: [],
            before: null,
            after: {
              name: "收支",
              description: "整理每笔收支",
              fields: [
                {
                  key: "amount",
                  label: "金额",
                  value_type: "number",
                  section: "header",
                  instructions: "按实际金额填写",
                  example: "",
                },
              ],
              deterministic_rules: [
                {
                  kind: "range",
                  field: "header.amount",
                  minimum: 0,
                  maximum: 10000,
                  severity: "error",
                },
              ],
              behavior: {
                presentation: { mode: "table" },
                requires_complete_input: true,
                suggest_filename: false,
              },
            },
          },
        ],
      }}
    />,
  );
  expect(screen.getByText(/此历史模板提案已停止支持执行/)).toBeInTheDocument();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
  expect(screen.queryByText(/requires_complete_input/)).not.toBeInTheDocument();
});

it("shows a single category as a readable value rather than an empty 100-percent ring", () => {
  const analysis: Analysis = {
    analysis_id: "one",
    source: {
      table_id: "t",
      table_name: "采购",
      row_count: 1,
      document_count: 1,
      grain: "row",
      generated_at: "2026-09-13",
      request: { dimensions: ["vendor"] },
    },
    data: [{ label: "供应商 A", "sum:total": 9600 }],
    metric_keys: ["sum:total"],
    metric_labels: { "sum:total": "总额" },
    truncated: false,
    warnings: [],
  };
  render(
    <AnalysisCard
      analysis={analysis}
      spec={{
        analysis_id: "one",
        type: "donut",
        title: "采购金额",
        x: "label",
        series: ["sum:total"],
      }}
      navigate={() => {}}
    />,
  );
  expect(screen.getByRole("combobox", { name: "图表展示方式" })).toHaveValue(
    "metric",
  );
  expect(screen.getByText("9,600")).toBeInTheDocument();
  expect(screen.getByText("供应商 A")).toBeInTheDocument();
  expect(
    screen.queryByRole("option", { name: "环形图" }),
  ).not.toBeInTheDocument();
});

it("keeps negative amounts and incomplete groups out of proportion charts", () => {
  const analysis: Analysis = {
    analysis_id: "signed", source: { table_id: "t", table_name: "收支", row_count: 2,
      document_count: 0, grain: "row", generated_at: "2026-09-13", request: {} },
    data: [{ label: "收入", "sum:total": 500 }, { label: "支出", "sum:total": -200 }],
    metric_keys: ["sum:total"], warnings: [], truncated: false,
  };
  const { rerender } = render(<AnalysisCard analysis={analysis} navigate={() => {}}
    spec={{ analysis_id: "signed", type: "donut", title: "收支", x: "label", series: ["sum:total"] }} />);
  expect(screen.getByRole("combobox", { name: "图表展示方式" })).toHaveValue("table");
  expect(screen.queryByRole("option", { name: "环形图" })).not.toBeInTheDocument();
  expect(screen.getByText("-200")).toBeInTheDocument();
  rerender(<AnalysisCard analysis={{ ...analysis, truncated: true, data: [{ label: "收入", "sum:total": 500 }, { label: "支出", "sum:total": 200 }] }} navigate={() => {}} />);
  expect(screen.queryByRole("option", { name: "环形图" })).not.toBeInTheDocument();
  expect(screen.getByText(/展示前 2 组；统计总计包含完整查询范围/)).toBeInTheDocument();
});

it("keeps currency and partial-source limitations visible before opening provenance", () => {
  const warnings = ["「金额」未记录币种，仅显示数值汇总；不能作为统一币种总额。", "范围内有 1 条记录来自部分读取的文件，结果不代表完整原件。"];
  render(<AnalysisCard analysis={{ analysis_id: "limits", source: { table_id: "t", table_name: "采购", row_count: 1, document_count: 1,
    grain: "auto", generated_at: "2026-09-13", request: {} }, data: [{ label: "全部", "sum:total": 120 }], metric_keys: ["sum:total"], warnings, truncated: false }} navigate={() => {}} />);
  for (const warning of warnings) expect(screen.getByText(warning)).toBeVisible();
  expect(screen.getByRole("button", { name: "来源" })).toHaveAttribute("aria-expanded", "false");
});

it("exposes provenance without repeating a scalar as a data table", () => {
  const analysis: Analysis = {
    analysis_id: "evidence", source: { table_id: "t", table_name: "采购账本", row_count: 3,
      document_count: 2, grain: "auto", generated_at: "2026-09-13", request: { table_id: "t", dimensions: [], metrics: [{ op: "sum", field: "total" }] } },
    data: [{ label: "全部", "sum:total": 9600 }], metric_keys: ["sum:total"], warnings: [], truncated: false,
  };
  const { rerender } = render(<AnalysisCard analysis={analysis} navigate={() => {}} />);
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "来源" }));
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "采购账本" })).toBeInTheDocument();
  expect(screen.getByText(/生成于/)).toBeInTheDocument();
  expect(screen.queryByText(/"metrics"/)).not.toBeInTheDocument();
  const pin = vi.fn();
  window.addEventListener("zhiyi:pin-analysis", pin);
  fireEvent.click(screen.getByRole("button", { name: "添加到仪表盘" }));
  expect(pin).toHaveBeenCalledOnce();
  expect((pin.mock.calls[0][0] as CustomEvent).detail.analysis).toEqual(analysis);
  window.removeEventListener("zhiyi:pin-analysis", pin);
  rerender(<AnalysisCard analysis={{ ...analysis, source: { ...analysis.source, request: { ...analysis.source.request, row_ids: [1] } } }} navigate={() => {}} />);
  expect(screen.queryByRole("button", { name: "添加到仪表盘" })).not.toBeInTheDocument();
});


it("merges one table and field into a single preview with actual before/after values and unique affected counts", () => {
  const item = (row: number, before: number, after: number) => ({
    label: "调整数值", name: "收支明细", columns: { amount: "金额" },
    operation: { kind: "increment_rows", table_id: "ledger", changes: { amount: 1 } },
    affected: [{ row_id: row, before: { amount: before }, after: { amount: after } }], before: null, after: null,
  });
  render(<OperationPreview result={{ title: "调整数值 · 收支明细", items: [item(1, 10, 11), item(2, 20, 21), item(1, 10, 11)] }} />);
  expect(screen.getAllByRole("table")).toHaveLength(1);
  expect(screen.getAllByRole("row")).toHaveLength(3);
  expect(screen.getByText("修改 2 条记录 · 2 处字段变更")).toBeInTheDocument();
  expect(screen.getByText("金额增加 1")).toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "21" })).toBeInTheDocument();
  expect(screen.queryByText(/原始配置/)).not.toBeInTheDocument();
});

it("keeps independent tables and different modified fields distinct", () => {
  const items = ["one", "two"].map(table => ({ label: "修改数据", name: table, columns: { amount: "金额" },
    operation: { kind: "update_rows", table_id: table }, before: null, after: null,
    affected: [{ row_id: 1, before: { amount: 10 }, after: { amount: 11 } }] }));
  render(<OperationPreview result={{ title: "修改数据", items }} />);
  expect(screen.getAllByRole("table")).toHaveLength(2);
  expect(screen.getByText("修改 2 条记录 · 2 处字段变更")).toBeInTheDocument();
});

it("uses matching line markers and dashed line segments in combination legends", () => {
  const analysis: Analysis = { analysis_id: "legend", source: { table_id: "t", table_name: "采购", row_count: 3,
    document_count: 3, grain: "row", generated_at: "2026-09-13", request: {} },
    data: [{ label: "A", "sum:total": 100, "count:*": 2 }, { label: "B", "sum:total": 60, "count:*": 1 }],
    metric_keys: ["sum:total", "count:*"], warnings: [], truncated: false };
  const { container } = render(<AnalysisCard analysis={analysis} navigate={() => {}}
    spec={{ analysis_id: "legend", type: "composed", title: "采购", x: "label", series: analysis.metric_keys! }} />);
  expect(container.querySelector('[data-series-kind="bar"] rect')).toBeInTheDocument();
  expect(container.querySelector('[data-series-kind="line"] line')).toHaveAttribute("stroke-dasharray", "5 4");
  expect(container.querySelector('[data-series-kind="line"] circle')).toBeInTheDocument();
});

it("uses the server-provided business record name without exposing a row identifier", () => {
  render(<OperationPreview result={{ title: "调整数值 · 采购", items: [{
    label: "调整数值", name: "采购", columns: { amount: "金额" }, operation: { kind: "increment_rows", table_id: "t", changes: { amount: 1 } },
    affected: [{ row_id: 14, name: "采购编号：SYN-014", before: { amount: 10 }, after: { amount: 11 } }], before: null, after: null,
  }] }} />);
  expect(screen.getByRole("cell", { name: "采购编号：SYN-014" })).toBeInTheDocument();
  expect(screen.queryByText("#14")).not.toBeInTheDocument();
});

it("uses preview positions for unnamed records while keeping business IDs and multiple fields", () => {
  render(<OperationPreview result={{ title: "修改采购", items: [{
    label: "修改数据", name: "采购", columns: { invoice_id: "订单编号", amount: "金额" },
    operation: { kind: "update_rows", table_id: "private-table-xyz" },
    affected: [
      { row_id: 714220, name: "采购单甲", before: { invoice_id: "ORD-001", amount: 10 }, after: { invoice_id: "ORD-002", amount: 11 } },
      { row_id: 714999, before: { invoice_id: "ORD-003", amount: 20 }, after: { invoice_id: "a1234567-89ab-cdef-0123-456789abcdef", amount: 21 } },
    ], before: null, after: null,
  }] }} />);
  expect(screen.getByText("修改 2 条记录 · 4 处字段变更")).toBeInTheDocument();
  expect(screen.getAllByRole("cell", { name: "采购单甲" })).toHaveLength(2);
  expect(screen.getAllByRole("cell", { name: "预览记录 2" })).toHaveLength(2);
  for (const value of ["ORD-001", "ORD-002", "ORD-003", "a1234567-89ab-cdef-0123-456789abcdef", "21"]) expect(screen.getByRole("cell", { name: value })).toBeInTheDocument();
  expect(screen.queryByText(/714220|714999|private-table-xyz/)).not.toBeInTheDocument();
});

it("labels historical deletion rows by business name or preview position without removing values", () => {
  render(<OperationPreview result={{ title: "删除记录", items: [{
    label: "删除记录", name: "采购", columns: { invoice_id: "订单编号" }, operation: { kind: "delete_rows", table_id: "private-table-xyz" },
    affected: [{ row_id: 714220, name: "采购单甲", before: { invoice_id: "ORD-001" }, after: null },
      { row_id: 714999, before: { invoice_id: "ORD-002" }, after: null }], before: null, after: null,
  }] }} />);
  expect(screen.getByRole("cell", { name: "采购单甲" })).toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "预览记录 2" })).toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "ORD-001" })).toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "ORD-002" })).toBeInTheDocument();
  expect(screen.getAllByRole("cell", { name: "删除记录" })).toHaveLength(2);
  expect(screen.queryByText(/714220|714999|private-table-xyz/)).not.toBeInTheDocument();
});

it("keeps one preview position per record across field groups presented in different orders", () => {
  const change = (field: string, rows: { row_id: number; before: number; after: number }[]) => ({
    label: "修改数据", name: "采购", columns: { amount: "金额", quantity: "数量" },
    operation: { kind: "update_rows", table_id: "same-table" },
    affected: rows.map(row => ({ row_id: row.row_id, before: { [field]: row.before }, after: { [field]: row.after } })),
    before: null, after: null,
  });
  render(<OperationPreview result={{ title: "修改采购", items: [
    change("amount", [{ row_id: 714220, before: 10, after: 11 }, { row_id: 714999, before: 20, after: 21 }]),
    change("quantity", [{ row_id: 714999, before: 30, after: 31 }, { row_id: 714220, before: 40, after: 41 }]),
  ] }} />);
  const [amount, quantity] = screen.getAllByRole("table");
  for (const [table, value, label] of [[amount, "11", "预览记录 1"], [amount, "21", "预览记录 2"],
    [quantity, "31", "预览记录 2"], [quantity, "41", "预览记录 1"]] as const) {
    const row = within(table).getByRole("cell", { name: value }).closest("tr");
    expect(row).not.toBeNull();
    expect(within(row!).getByRole("cell", { name: label })).toBeInTheDocument();
  }
  expect(screen.getByText("修改 2 条记录 · 4 处字段变更")).toBeInTheDocument();
});

it("keeps distinct preview positions across updates and separate deletions in one table", () => {
  const remove = (row_id: number, invoice_id: string) => ({
    label: "删除记录", name: "采购", columns: { invoice_id: "订单编号" }, operation: { kind: "delete_rows", table_id: "same-table" },
    affected: [{ row_id, before: { invoice_id }, after: null }], before: null, after: null,
  });
  render(<OperationPreview result={{ title: "修改和删除采购", items: [
    remove(714999, "ORD-B"),
    { label: "修改数据", name: "采购", columns: { amount: "金额" }, operation: { kind: "update_rows", table_id: "same-table" },
      affected: [{ row_id: 714220, before: { amount: 10 }, after: { amount: 11 } }], before: null, after: null },
    remove(715111, "ORD-C"),
  ] }} />);
  expect(screen.getByText("修改 1 条记录 · 1 处字段变更")).toBeInTheDocument();
  const [updated, removedB, removedC] = screen.getAllByRole("table");
  for (const [table, value, label] of [[updated, "11", "预览记录 1"], [removedB, "ORD-B", "预览记录 2"],
    [removedC, "ORD-C", "预览记录 3"]] as const) {
    const row = within(table).getByRole("cell", { name: value }).closest("tr");
    expect(row).not.toBeNull();
    expect(within(row!).getByRole("cell", { name: label })).toBeInTheDocument();
  }
  expect(screen.getAllByRole("cell", { name: "删除记录" })).toHaveLength(2);
  expect(screen.queryByText(/714220|714999|715111/)).not.toBeInTheDocument();
});

it("labels an analysis source as a record while navigating to the original internal identity", () => {
  const navigate = vi.fn();
  const analysis: Analysis = { analysis_id: "analysis", source: { table_id: "private-table-xyz", table_name: "采购",
    row_count: 1, document_count: 1, grain: "row", generated_at: "2026-11-01", request: {} },
    columns: [{ key: "invoice_id", label: "订单编号" }],
    rows: [{ row_id: 714220, version: 0, task_id: null, values: { invoice_id: "ORD-001" } }], warnings: [], truncated: false };
  render(<AnalysisCard analysis={analysis} navigate={navigate} />);
  fireEvent.click(screen.getByRole("button", { name: "来源" }));
  expect(screen.getByRole("cell", { name: "ORD-001" })).toBeInTheDocument();
  const source = screen.getByRole("button", { name: "查看记录" });
  expect(screen.queryByText(/714220|private-table-xyz/)).not.toBeInTheDocument();
  fireEvent.click(source);
  expect(navigate).toHaveBeenCalledWith({ kind: "table", id: "private-table-xyz", row_id: 714220 });
});

it.each([[0.00000001, "1e-8"], [0.12345678912345678, "0.12345678912345678"]])(
  "keeps the precise analysis value %s in the metric and underlying data", (value, expected) => {
    const analysis: Analysis = { analysis_id: `precise-${value}`, source: { table_id: "t", table_name: "精度检查", row_count: 1,
      document_count: 1, grain: "row", generated_at: "2026-11-01", request: {} },
      data: [{ label: "全部", "sum:amount": value as number }], metric_keys: ["sum:amount"], warnings: [], truncated: false };
    render(<AnalysisCard analysis={analysis} navigate={() => {}} />);
    expect(screen.getByText(String(expected))).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "来源" }));
    fireEvent.change(screen.getByRole("combobox", { name: "图表展示方式" }), { target: { value: "table" } });
    const cell = screen.getByRole("cell", { name: String(expected) });
    expect(cell.querySelector("[title]")).toHaveAttribute("title", String(expected));
  },
);

it("uses the canonical server total in a complete analysis donut", () => {
  const analysis: Analysis = { analysis_id: "decimal-total", source: { table_id: "t", table_name: "金额", row_count: 2,
    document_count: 2, grain: "row", generated_at: "2026-11-01", request: {} },
    data: [{ label: "甲", "sum:amount": 0.1 }, { label: "乙", "sum:amount": 0.2 }], metric_keys: ["sum:amount"],
    totals: { "sum:amount": 0.3 }, warnings: [], truncated: false };
  const { container } = render(<AnalysisCard analysis={analysis} navigate={() => {}}
    spec={{ analysis_id: analysis.analysis_id, type: "donut", title: "金额合计", x: "label", series: ["sum:amount"] }} />);
  expect(container.querySelector(".ask-donut-center strong")).toHaveTextContent("0.3");
  expect(container.querySelector(".ask-donut-center strong")?.textContent).toBe("0.3");
});
