import { fireEvent, render, screen } from "@testing-library/react";
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
  expect(screen.getByRole("button", { name: "数据与来源" })).toHaveAttribute("aria-expanded", "false");
});

it("exposes chart values and provenance after one disclosure", () => {
  const analysis: Analysis = {
    analysis_id: "evidence", source: { table_id: "t", table_name: "采购账本", row_count: 3,
      document_count: 2, grain: "auto", generated_at: "2026-09-13", request: { table_id: "t", dimensions: [], metrics: [{ op: "sum", field: "total" }] } },
    data: [{ label: "全部", "sum:total": 9600 }], metric_keys: ["sum:total"], warnings: [], truncated: false,
  };
  const { rerender } = render(<AnalysisCard analysis={analysis} navigate={() => {}} />);
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "数据与来源" }));
  expect(screen.getByRole("table")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "采购账本" })).toBeInTheDocument();
  expect(screen.getByText(/这是生成时保存的快照/)).toBeInTheDocument();
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
  expect(screen.getByText("影响 2 条记录 · 2 处字段变更")).toBeInTheDocument();
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
  expect(screen.getByText("影响 2 条记录 · 2 处字段变更")).toBeInTheDocument();
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

it("uses the server-provided record name and retains a secondary row identifier", () => {
  render(<OperationPreview result={{ title: "调整数值 · 采购", items: [{
    label: "调整数值", name: "采购", columns: { amount: "金额" }, operation: { kind: "increment_rows", table_id: "t", changes: { amount: 1 } },
    affected: [{ row_id: 14, name: "采购编号：SYN-014", before: { amount: 10 }, after: { amount: 11 } }], before: null, after: null,
  }] }} />);
  expect(screen.getByRole("cell", { name: "采购编号：SYN-014 #14" })).toBeInTheDocument();
});
