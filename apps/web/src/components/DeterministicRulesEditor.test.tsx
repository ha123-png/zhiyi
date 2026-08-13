import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import type { TemplateField } from "../types";
import { DeterministicRulesEditor } from "./DeterministicRulesEditor";

const fields: TemplateField[] = [
  {
    key: "document_number",
    label: "单据号码",
    section: "header",
    example: "",
    instructions: "",
    value_type: "text",
  },
  {
    key: "amount",
    label: "明细金额",
    section: "item",
    example: "",
    instructions: "",
    value_type: "number",
  },
];

it("builds a required rule without exposing internal field paths", () => {
  const onChange = vi.fn();
  render(
    <DeterministicRulesEditor
      disabled={false}
      fields={fields}
      onChange={onChange}
      rules={[]}
    />,
  );

  fireEvent.change(screen.getByLabelText("检查字段"), {
    target: { value: "header.document_number" },
  });
  fireEvent.click(screen.getByRole("button", { name: "添加校验规则" }));

  expect(onChange).toHaveBeenCalledWith([
    { kind: "required", field: "header.document_number" },
  ]);
  expect(screen.queryByText("header.document_number")).not.toBeInTheDocument();
});

it("explains that natural-language requirements are not deterministic checks", () => {
  render(
    <DeterministicRulesEditor
      disabled={false}
      fields={fields}
      onChange={vi.fn()}
      rules={[]}
    />,
  );

  expect(
    screen.getByText("暂无自定义校验规则。上面的“AI 理解要求”只会提示 AI，不会自动报错。"),
  ).toBeInTheDocument();
});
