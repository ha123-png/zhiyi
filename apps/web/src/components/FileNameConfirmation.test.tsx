import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { FileNameConfirmation } from "./FileNameConfirmation";
import { TemplateNameSettings } from "./PresentationSettings";

it("keeps the original selected until the user adopts or edits the suggestion", () => {
  const changed = vi.fn();
  render(<FileNameConfirmation original="IMG_1234.png" state={{ status: "pending", suggested_filename: "数学错题.png", confirmed_filename: null, source_fields: ["title"], explanation: "来自已提取标题" }} onChange={changed} />);
  expect(screen.getByLabelText("确认后的名称（保留扩展名）")).toHaveValue("IMG_1234.png");
  fireEvent.click(screen.getByRole("button", { name: "采用建议" }));
  expect(changed).toHaveBeenLastCalledWith("数学错题.png");
  fireEvent.click(screen.getByRole("button", { name: "保留原名" }));
  expect(changed).toHaveBeenLastCalledWith("IMG_1234.png");
  expect(screen.getByText(/已有外部副本不会被改名/)).toBeInTheDocument();
});

it("defaults legacy templates to disabled without changing presentation", () => {
  const changed = vi.fn();
  render(<TemplateNameSettings onChange={changed} />);
  const checkbox = screen.getByRole("checkbox");
  expect(checkbox).not.toBeChecked();
  fireEvent.click(checkbox);
  expect(changed).toHaveBeenCalledWith(expect.objectContaining({ suggest_filename: true, requires_complete_input: true, presentation: expect.objectContaining({ mode: "table" }) }));
});
