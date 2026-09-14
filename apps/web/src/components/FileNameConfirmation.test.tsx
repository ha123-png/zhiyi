import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { FileNameConfirmation } from "./FileNameConfirmation";
import { TemplateNameSettings } from "./PresentationSettings";

it("automatically uses the content name while keeping the original traceable and editable", () => {
  const changed = vi.fn();
  render(<FileNameConfirmation original="IMG_1234.png" state={{ status: "pending", suggested_filename: "数学错题.png", confirmed_filename: null, source_fields: ["title"], explanation: "来自已提取标题" }} onChange={changed} />);
  fireEvent.click(screen.getByText("文件名称 · 内容名称"));
  expect(screen.getByLabelText("文件名称（保留扩展名）")).toHaveValue("数学错题.png");
  expect(screen.queryByRole("button", { name: "采用建议" })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("文件名称（保留扩展名）"), { target: { value: "我的错题.png" } });
  expect(changed).toHaveBeenLastCalledWith("我的错题.png");
  expect(screen.getByText(/上传原名：IMG_1234.png/)).toBeInTheDocument();
  expect(screen.getByText(/已有外部副本不变/)).toBeInTheDocument();
});

it("defaults legacy templates to disabled without changing presentation", () => {
  const changed = vi.fn();
  render(<TemplateNameSettings onChange={changed} />);
  const checkbox = screen.getByRole("checkbox");
  expect(checkbox).not.toBeChecked();
  fireEvent.click(checkbox);
  expect(changed).toHaveBeenCalledWith(expect.objectContaining({ suggest_filename: true, requires_complete_input: true, presentation: expect.objectContaining({ mode: "table" }) }));
});
