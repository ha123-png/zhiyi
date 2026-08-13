import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Extraction, ExtractionTemplate, Task } from "../types";
import { ReviewPreview } from "./ReviewPreview";


const task: Task = {
  id: "task-1",
  filename: "invoice.png",
  content_type: "image/png",
  size_bytes: 1,
  sha256: "digest",
  template_mode: "invoice",
  template_id: "builtin-invoice",
  template_version: 1,
  candidate_templates: [],
  status: "needs_review",
  duplicate_of_task_id: null,
  created_at: "2026-07-30T00:00:00Z",
  updated_at: "2026-07-30T00:00:00Z",
};

const result = {
  document_type: "发票",
  seller_name: "模型销售方",
  buyer_name: "购买方",
  document_number: "NO-1",
  document_date: "2026-07-30",
  amount_before_tax: 100,
  tax_amount: 6,
  total_amount: 106,
  items: [
    {
      name: "原名称",
      specification: "A4",
      unit: "件",
      quantity: 1,
      unit_price: 100,
      amount: 100,
      tax_rate: "6%",
      tax_amount: 6,
    },
  ],
};

const invoiceTemplate: ExtractionTemplate = {
  id: "builtin-invoice",
  version: 1,
  is_system: true,
  builtin_key: "invoice",
  source_template_id: null,
  name: "发票",
  description: "整理购销双方、票号、日期、金额、税额和商品明细。",
  extra_instructions: "",
  fields: [
    { key: "seller_name", label: "销售方", section: "header", example: "", instructions: "", value_type: "text" },
    { key: "buyer_name", label: "购买方", section: "header", example: "", instructions: "", value_type: "text" },
    { key: "document_number", label: "发票号码", section: "header", example: "", instructions: "", value_type: "text" },
    { key: "document_date", label: "开票日期", section: "header", example: "", instructions: "", value_type: "date" },
    { key: "amount_before_tax", label: "不含税金额", section: "header", example: "", instructions: "", value_type: "number" },
    { key: "tax_amount", label: "税额", section: "header", example: "", instructions: "", value_type: "number" },
    { key: "total_amount", label: "价税合计", section: "header", example: "", instructions: "", value_type: "number" },
    { key: "name", label: "商品名称", section: "item", example: "", instructions: "", value_type: "text" },
    { key: "specification", label: "规格型号", section: "item", example: "", instructions: "", value_type: "text" },
    { key: "unit", label: "单位", section: "item", example: "", instructions: "", value_type: "text" },
    { key: "quantity", label: "数量", section: "item", example: "", instructions: "", value_type: "number" },
    { key: "unit_price", label: "单价", section: "item", example: "", instructions: "", value_type: "number" },
    { key: "amount", label: "金额", section: "item", example: "", instructions: "", value_type: "number" },
    { key: "tax_rate", label: "税率", section: "item", example: "", instructions: "", value_type: "text" },
    { key: "tax_amount", label: "税额", section: "item", example: "", instructions: "", value_type: "number" },
  ],
  validation_rules: [],
  deterministic_rules: [],
  output_mapping: {},
  created_at: "2026-07-30T00:00:00Z",
  updated_at: "2026-07-30T00:00:00Z",
};

const extraction: Extraction = {
  task_id: "task-1",
  document_kind: "invoice",
  template_id: "builtin-invoice",
  template_version: 1,
  template: invoiceTemplate,
  model_name: "test-model",
  prompt_version: "test-prompt",
  elapsed_seconds: 1,
  review_version: 2,
  original_result: result,
  result,
  validation_issues: [],
};

describe("ReviewPreview", () => {
  it("submits edited data against the visible review version", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <ReviewPreview
        confirmation={null}
        extraction={extraction}
        onConfirm={vi.fn()}
        onSave={onSave}
        onSelectTemplate={vi.fn()}
        task={task}
      />,
    );

    fireEvent.change(screen.getByRole("textbox", { name: "销售方" }), {
      target: { value: "人工修正销售方" },
    });
    fireEvent.change(screen.getByRole("spinbutton", { name: "第 1 行单价" }), {
      target: { value: "88.5" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
    expect(onSave.mock.calls[0][0].seller_name).toBe("人工修正销售方");
    expect(onSave.mock.calls[0][0].items[0].unit_price).toBe(88.5);
    expect(onSave.mock.calls[0][1]).toBe(2);
  });

  it("confirms the visible review version", async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    render(
      <ReviewPreview
        confirmation={null}
        extraction={extraction}
        onConfirm={onConfirm}
        onSave={vi.fn()}
        onSelectTemplate={vi.fn()}
        task={task}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "忽略提示并完成" }));

    await waitFor(() => expect(onConfirm).toHaveBeenCalledWith(2));
  });

  it("shows the confirmed table and export entry", () => {
    render(
      <ReviewPreview
        confirmation={{
          task_id: "task-1",
          table_id: "table-1",
          table_name: "发票",
          review_version: 2,
          row_count: 1,
          confirmed_at: "2026-07-30T00:00:00Z",
        }}
        extraction={extraction}
        onConfirm={vi.fn()}
        onSave={vi.fn()}
        onSelectTemplate={vi.fn()}
        task={{ ...task, status: "completed" }}
      />,
    );

    expect(screen.getByRole("link", { name: "下载 Excel" })).toHaveAttribute(
      "href",
      "/api/v1/tables/table-1/export.xlsx",
    );
    expect(screen.getByText("已进入发票表 · 1 行")).toBeInTheDocument();
  });

  it("renders and saves fields from a custom template version", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <ReviewPreview
        confirmation={null}
        extraction={{
          ...extraction,
          document_kind: "custom",
          template_id: "custom-score",
          template_version: 3,
          template: {
            id: "custom-score",
            version: 3,
            is_system: false,
            builtin_key: null,
            source_template_id: null,
            name: "门店评分",
            description: "整理门店得分",
            extra_instructions: "",
            fields: [
              {
                key: "store",
                label: "门店",
                section: "header",
                example: "",
                instructions: "",
                value_type: "text",
              },
              {
                key: "score",
                label: "得分",
                section: "item",
                example: "",
                instructions: "",
                value_type: "number",
              },
            ],
            validation_rules: [],
            deterministic_rules: [],
            output_mapping: {},
            created_at: "2026-07-30T00:00:00Z",
            updated_at: "2026-07-30T00:00:00Z",
          },
          original_result: {
            header: { store: "一号门店" },
            items: [{ score: 98 }],
          },
          result: {
            header: { store: "一号门店" },
            items: [{ score: 98 }],
          },
        }}
        onConfirm={vi.fn()}
        onSave={onSave}
        onSelectTemplate={vi.fn()}
        task={{ ...task, template_id: "custom-score", template_version: 3 }}
      />,
    );

    fireEvent.change(screen.getByRole("spinbutton", { name: "第 1 行得分" }), {
      target: { value: "99.5" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
    expect(onSave.mock.calls[0][0].items[0].score).toBe(99.5);
    expect(screen.getByText("门店评分")).toBeInTheDocument();
  });

  it("lets the user resolve an ambiguous template without reuploading", async () => {
    const onSelectTemplate = vi.fn().mockResolvedValue(undefined);
    render(
      <ReviewPreview
        confirmation={null}
        extraction={null}
        onConfirm={vi.fn()}
        onSave={vi.fn()}
        onSelectTemplate={onSelectTemplate}
        task={{
          ...task,
          status: "waiting_for_template",
          template_id: null,
          template_version: null,
          candidate_templates: [
            {
              id: "builtin-delivery",
              version: 1,
              name: "送货单",
              description: "整理供货和收货明细",
            },
            {
              id: "custom-store",
              version: 2,
              name: "门店入库单",
              description: "整理门店入库记录",
            },
          ],
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /门店入库单/ }));

    await waitFor(() =>
      expect(onSelectTemplate).toHaveBeenCalledWith(
        "task-1",
        "custom-store",
      ),
    );
    expect(screen.getByText(/模型没有替你猜/)).toBeInTheDocument();
  });

  it("renders editor fields from the template instead of a fixed invoice list", () => {
    render(
      <ReviewPreview
        confirmation={null}
        extraction={{
          ...extraction,
          document_kind: "delivery",
          template_id: "builtin-delivery",
          template: {
            id: "builtin-delivery",
            version: 1,
            is_system: true,
            builtin_key: "delivery",
            source_template_id: null,
            name: "送货单",
            description: "整理供货方、收货方、单号、日期、合计和货品明细。",
            extra_instructions: "",
            fields: [
              { key: "seller_name", label: "供货方", section: "header", example: "", instructions: "", value_type: "text" },
              { key: "buyer_name", label: "收货方", section: "header", example: "", instructions: "", value_type: "text" },
              { key: "document_number", label: "送货单号", section: "header", example: "", instructions: "", value_type: "text" },
              { key: "document_date", label: "送货日期", section: "header", example: "", instructions: "", value_type: "date" },
              { key: "total_amount", label: "合计金额", section: "header", example: "", instructions: "", value_type: "number" },
              { key: "name", label: "货品名称", section: "item", example: "", instructions: "", value_type: "text" },
              { key: "specification", label: "规格", section: "item", example: "", instructions: "", value_type: "text" },
              { key: "unit", label: "单位", section: "item", example: "", instructions: "", value_type: "text" },
              { key: "quantity", label: "数量", section: "item", example: "", instructions: "", value_type: "number" },
              { key: "unit_price", label: "单价", section: "item", example: "", instructions: "", value_type: "number" },
              { key: "amount", label: "金额", section: "item", example: "", instructions: "", value_type: "number" },
            ],
            validation_rules: [],
            deterministic_rules: [],
            output_mapping: {},
            created_at: "2026-07-30T00:00:00Z",
            updated_at: "2026-07-30T00:00:00Z",
          },
          result: {
            document_type: "送货单",
            seller_name: "模型供货方",
            buyer_name: "收货方",
            document_number: "SH-001",
            document_date: "2026-07-30",
            amount_before_tax: null,
            tax_amount: null,
            total_amount: 106,
            items: [
              {
                name: "原名称",
                specification: "A4",
                unit: "件",
                quantity: 1,
                unit_price: 100,
                amount: 100,
                tax_rate: null,
                tax_amount: null,
              },
            ],
          },
          original_result: {
            document_type: "送货单",
            seller_name: "模型供货方",
            buyer_name: "收货方",
            document_number: "SH-001",
            document_date: "2026-07-30",
            amount_before_tax: null,
            tax_amount: null,
            total_amount: 106,
            items: [
              {
                name: "原名称",
                specification: "A4",
                unit: "件",
                quantity: 1,
                unit_price: 100,
                amount: 100,
                tax_rate: null,
                tax_amount: null,
              },
            ],
          },
        }}
        onConfirm={vi.fn()}
        onSave={vi.fn()}
        onSelectTemplate={vi.fn()}
        task={{ ...task, template_id: "builtin-delivery" }}
      />,
    );

    // 送货单模板字段的中文名（模板为准，非固定发票清单）
    expect(screen.getByLabelText("供货方")).toBeInTheDocument();
    expect(screen.getByLabelText("收货方")).toBeInTheDocument();
    expect(screen.getByLabelText("送货单号")).toBeInTheDocument();
    expect(screen.getByLabelText("合计金额")).toBeInTheDocument();
    // 发票才有、送货单模板没有的字段不渲染
    expect(screen.queryByLabelText("不含税金额")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("税额")).not.toBeInTheDocument();
    // 明细列以模板为准：有税率/税额则模板没有就不显示
    expect(screen.queryByLabelText("第 1 行税率")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("第 1 行税额")).not.toBeInTheDocument();
    expect(screen.getByLabelText("第 1 行金额")).toBeInTheDocument();
  });

  it("offers all current templates when the model finds no reliable match", async () => {
    const onSelectTemplate = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => [
          {
            id: "custom-other",
            version: 1,
            is_system: false,
            builtin_key: null,
            source_template_id: null,
            name: "其他业务单",
            description: "人工确认后使用",
            extra_instructions: "",
            fields: [],
            validation_rules: [],
            deterministic_rules: [],
            output_mapping: {},
            created_at: "2026-07-30T00:00:00Z",
            updated_at: "2026-07-30T00:00:00Z",
          },
        ],
      }),
    );
    render(
      <ReviewPreview
        confirmation={null}
        extraction={null}
        onConfirm={vi.fn()}
        onSave={vi.fn()}
        onSelectTemplate={onSelectTemplate}
        task={{
          ...task,
          status: "waiting_for_template",
          template_id: null,
          template_version: null,
          candidate_templates: [],
        }}
      />,
    );

    fireEvent.click(
      await screen.findByRole("button", { name: /其他业务单/ }),
    );

    await waitFor(() =>
      expect(onSelectTemplate).toHaveBeenCalledWith(
        "task-1",
        "custom-other",
      ),
    );
    expect(screen.getByText(/没有强行套用/)).toBeInTheDocument();
  });
});
