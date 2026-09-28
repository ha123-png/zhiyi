import { useState } from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, it } from "vitest";
import { ConfirmDialog } from "./ConfirmDialog";

it("traps keyboard focus in a confirmation and restores it inside the open parent dialog", async () => {
  function Harness() {
    const [open, setOpen] = useState(false);
    return <dialog open aria-label="外层文件详情">
      <button type="button" onClick={() => setOpen(true)}>删除此明细</button>
      <ConfirmDialog open={open} title="删除此明细？" description="仅删除当前明细。" buttonLabel="确认删除"
        onClose={() => setOpen(false)} onConfirm={() => setOpen(false)} />
    </dialog>;
  }
  render(<Harness />);
  const opener = screen.getByRole("button", {name: "删除此明细"});
  opener.focus();
  fireEvent.click(opener);
  const confirmation = screen.getByRole("dialog", {name: "删除此明细？"});
  const cancel = within(confirmation).getByRole("button", {name: "取消"});
  const confirm = within(confirmation).getByRole("button", {name: "确认删除"});
  await waitFor(() => expect(cancel).toHaveFocus());

  fireEvent.keyDown(cancel, {key: "Tab", shiftKey: true});
  expect(confirm).toHaveFocus();
  fireEvent.keyDown(confirm, {key: "Tab"});
  expect(cancel).toHaveFocus();
  fireEvent.keyDown(cancel, {key: "Escape"});
  await waitFor(() => expect(opener).toHaveFocus());
  expect(screen.getByRole("dialog", {name: "外层文件详情"})).toHaveAttribute("open");
});
