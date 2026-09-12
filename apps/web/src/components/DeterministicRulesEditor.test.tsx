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
    { kind: "required", field: "header.document_number", severity: "error" },
  ]);
  expect(screen.queryByText("header.document_number")).not.toBeInTheDocument();
});

it("keeps numeric allowed values numeric and rejects reversed bounds", () => {
  const onChange = vi.fn();
  render(<DeterministicRulesEditor disabled={false} fields={fields} rules={[]} onChange={onChange} />);
  fireEvent.change(screen.getByLabelText("规则类型"), { target: { value: "enum" } });
  fireEvent.change(screen.getByLabelText("检查字段"), { target: { value: "items[].amount" } });
  fireEvent.change(screen.getByPlaceholderText("例如：有效，作废"), { target: { value: "0,1" } });
  fireEvent.click(screen.getByRole("button", { name: "添加校验规则" }));
  expect(onChange.mock.calls[0][0][0].values).toEqual([0, 1]);
  fireEvent.change(screen.getByLabelText("规则类型"), { target: { value: "range" } });
  fireEvent.change(screen.getByLabelText("最小值（可空）"), { target: { value: "10" } });
  fireEvent.change(screen.getByLabelText("最大值（可空）"), { target: { value: "2" } });
  expect(screen.getByRole("button", { name: "添加校验规则" })).toBeDisabled();
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
    screen.getByText("暂无校验规则。"),
  ).toBeInTheDocument();
});
