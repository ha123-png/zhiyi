import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { defaultBehavior, TemplateNameSettings } from "./PresentationSettings";

it("preserves existing AI naming and offers one fixed rule without a format editor", () => {
  const value = { ...defaultBehavior(), suggest_filename: true };
  const onChange = vi.fn();
  const { rerender } = render(<TemplateNameSettings value={value} onChange={onChange} />);
  expect(screen.getByLabelText("文件命名方式")).toHaveValue("ai");
  fireEvent.change(screen.getByLabelText("文件命名方式"), { target: { value: "fixed" } });
  expect(onChange).toHaveBeenLastCalledWith({ ...value, filename_mode: "fixed" });
  rerender(<TemplateNameSettings value={{ ...value, filename_mode: "fixed" }} onChange={onChange} />);
  expect(screen.getByText(/导入日期，同日同模板递增/)).toBeVisible();
  expect(screen.getByRole("option", { name: "AI 命名 · 根据内容建议" })).toBeInTheDocument();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
});
