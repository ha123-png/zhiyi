import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Toast } from "./Toast";

describe("Toast modal feedback", () => {
  it("keeps an error within the open native dialog and allows keyboard dismissal", async () => {
    const dismiss = vi.fn();
    const { container } = render(<><dialog open aria-label="编辑文件"><input aria-label="金额" /></dialog>
      <Toast toast={{message: "修改未保存，请重试。", tone: "error"}} onDismiss={dismiss} /></>);
    const dialog = screen.getByRole("dialog", {name: "编辑文件"});
    await waitFor(() => expect(within(dialog).getByRole("alert")).toHaveTextContent("修改未保存"));
    expect(container.querySelectorAll(".app-toast")).toHaveLength(1);
    fireEvent.click(within(dialog).getByRole("button", {name: "关闭通知"}));
    expect(dismiss).toHaveBeenCalledOnce();
  });

  it("moves a persistent error back to the page when its dialog closes", async () => {
    const { rerender } = render(<><dialog open aria-label="编辑文件" /><Toast toast={{message: "保存失败", tone: "error"}} /></>);
    await waitFor(() => expect(screen.getByRole("alert").closest("dialog")).not.toBeNull());
    rerender(<><dialog aria-label="编辑文件" /><Toast toast={{message: "保存失败", tone: "error"}} /></>);
    await waitFor(() => expect(screen.getByRole("alert").closest("dialog")).toBeNull());
    expect(screen.getByRole("alert")).toHaveTextContent("保存失败");
  });
});
