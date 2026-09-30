import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import type { TemplateField } from "../types";
import { DeterministicRulesEditor } from "./DeterministicRulesEditor";
import { previewTemplatePattern } from "../api";

vi.mock("../api", () => ({ previewTemplatePattern: vi.fn() }));

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
  {
    key: "total_amount",
    label: "价税合计",
    section: "header",
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

it("accepts valid JSON exponent syntax in numeric enum values", () => {
  const onChange = vi.fn();
  render(<DeterministicRulesEditor disabled={false} fields={fields} rules={[]} onChange={onChange} />);
  fireEvent.change(screen.getByLabelText("规则类型"), { target: { value: "enum" } });
  fireEvent.change(screen.getByLabelText("检查字段"), { target: { value: "items[].amount" } });
  fireEvent.change(screen.getByPlaceholderText("例如：有效，作废"), { target: { value: "1e3" } });
  fireEvent.click(screen.getByRole("button", { name: "添加校验规则" }));
  expect(onChange).toHaveBeenCalledWith([{ kind: "enum", field: "items[].amount", values: [1000], severity: "error" }]);
});

it.each(["0x10", "1.0000000000000001", "9007199254740991.1"])(
  "rejects numeric enum values that are not exactly representable: %s",
  value => {
    const onChange = vi.fn();
    render(<DeterministicRulesEditor disabled={false} fields={fields} rules={[]} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText("规则类型"), { target: { value: "enum" } });
    fireEvent.change(screen.getByLabelText("检查字段"), { target: { value: "items[].amount" } });
    fireEvent.change(screen.getByPlaceholderText("例如：有效，作废"), { target: { value } });
    expect(screen.getByRole("button", { name: "添加校验规则" })).toBeDisabled();
    expect(onChange).not.toHaveBeenCalled();
  },
);

it("uses strict numeric syntax for range bounds and calculation tolerance", () => {
  const onChange = vi.fn();
  render(<DeterministicRulesEditor disabled={false} fields={fields} rules={[]} onChange={onChange} />);
  fireEvent.change(screen.getByLabelText("规则类型"), { target: { value: "range" } });
  fireEvent.change(screen.getByLabelText("检查字段"), { target: { value: "items[].amount" } });
  fireEvent.change(screen.getByLabelText("最小值（可空）"), { target: { value: "0x10" } });
  expect(screen.getByRole("button", { name: "添加校验规则" })).toBeDisabled();

  fireEvent.change(screen.getByLabelText("规则类型"), { target: { value: "sum" } });
  fireEvent.change(screen.getByLabelText("明细求和字段（如 明细金额）"), { target: { value: "items[].amount" } });
  fireEvent.change(screen.getByLabelText("抬头合计字段（如 合计金额）"), { target: { value: "header.total_amount" } });
  fireEvent.change(screen.getByLabelText("允许误差"), { target: { value: "0x10" } });
  expect(screen.getByRole("button", { name: "添加校验规则" })).toBeDisabled();
  expect(onChange).not.toHaveBeenCalled();
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

function fillPattern(pattern = "PO-\\d{3}") {
  fireEvent.change(screen.getByLabelText("规则类型"), { target: { value: "pattern" } });
  fireEvent.change(screen.getByLabelText("检查字段"), { target: { value: "header.document_number" } });
  fireEvent.change(screen.getByLabelText("格式表达式（正则）"), { target: { value: pattern } });
}

it("rejects server-invalid syntax before adding and permits a corrected valid pattern even when the sample does not match", async () => {
  vi.mocked(previewTemplatePattern).mockReset()
    .mockResolvedValueOnce({ valid: false, matches: false, message: "不支持分组。" })
    .mockResolvedValueOnce({ valid: true, matches: false, message: "整段匹配不通过。" });
  const onChange = vi.fn();
  render(<DeterministicRulesEditor disabled={false} fields={fields} rules={[]} onChange={onChange} />);
  fillPattern("(");
  fireEvent.click(screen.getByRole("button", { name: "添加校验规则" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("不支持分组。规则尚未添加。");
  expect(onChange).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("格式表达式（正则）"), { target: { value: "PO-\\d{3}" } });
  fireEvent.click(screen.getByRole("button", { name: "添加校验规则" }));
  await waitFor(() => expect(onChange).toHaveBeenCalledWith([{ kind: "pattern", field: "header.document_number", pattern: "PO-\\d{3}", severity: "error" }]));
  expect(previewTemplatePattern).toHaveBeenLastCalledWith("PO-\\d{3}", "");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it.each(["expression", "field", "template", "rules", "disabled", "unmount"])("discards a late pattern check after changing %s", async change => {
  let resolve!: (result: Awaited<ReturnType<typeof previewTemplatePattern>>) => void;
  vi.mocked(previewTemplatePattern).mockReset().mockImplementation(() => new Promise(done => { resolve = done; }));
  const onChange = vi.fn();
  const props = { disabled: false, fields, rules: [], onChange, contextKey: "template-A" };
  const view = render(<DeterministicRulesEditor {...props} />);
  fillPattern();
  fireEvent.click(screen.getByRole("button", { name: "添加校验规则" }));
  expect(screen.getByRole("button", { name: "检查格式中…" })).toBeDisabled();
  if (change === "expression") fireEvent.change(screen.getByLabelText("格式表达式（正则）"), { target: { value: "PO-[0-9]+" } });
  if (change === "field") fireEvent.change(screen.getByLabelText("检查字段"), { target: { value: "items[].amount" } });
  if (change === "template") view.rerender(<DeterministicRulesEditor {...props} contextKey="template-B" />);
  if (change === "rules") view.rerender(<DeterministicRulesEditor {...props} rules={[{ kind: "required", field: "header.document_number" }]} />);
  if (change === "disabled") view.rerender(<DeterministicRulesEditor {...props} disabled />);
  if (change === "unmount") view.unmount();
  await act(async () => { resolve({ valid: true, matches: false, message: "整段匹配不通过。" }); });
  expect(onChange).not.toHaveBeenCalled();
});

it("keeps failed checks out of the draft and can retry without a duplicate request", async () => {
  vi.mocked(previewTemplatePattern).mockReset().mockRejectedValueOnce(new Error("连接失败，请重试。"))
    .mockResolvedValueOnce({ valid: true, matches: false, message: "整段匹配不通过。" });
  const onChange = vi.fn();
  render(<DeterministicRulesEditor disabled={false} fields={fields} rules={[]} onChange={onChange} />);
  fillPattern();
  fireEvent.click(screen.getByRole("button", { name: "添加校验规则" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("连接失败，请重试。规则尚未添加。");
  expect(onChange).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "添加校验规则" }));
  await waitFor(() => expect(onChange).toHaveBeenCalledOnce());
  expect(previewTemplatePattern).toHaveBeenCalledTimes(2);
});
