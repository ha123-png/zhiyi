import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ImportSheetDialog } from "./ImportSheetDialog";

it("does not choose an active worksheet silently and imports only the chosen one", () => {
  const choose = vi.fn(); const close = vi.fn();
  render(<ImportSheetDialog filename="批次.xlsx" sheets={["第一批", "第二批"]} busy={false} error={null} onChoose={choose} onClose={close} />);
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  expect(screen.getByRole("button", {name: "导入此工作表"})).toBeDisabled();
  fireEvent.change(screen.getByRole("combobox", {name: "工作表"}), {target: {value: "第二批"}});
  fireEvent.click(screen.getByRole("button", {name: "导入此工作表"}));
  expect(choose).toHaveBeenCalledWith("第二批");
  fireEvent.click(screen.getByRole("button", {name: "取消"}));
  expect(close).toHaveBeenCalledOnce();
});
