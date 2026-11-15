import { useState } from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
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

it("consumes a typed confirmation Enter before returning focus to the opener", () => {
  function Harness() {
    const [open, setOpen] = useState(false);
    return <>
      <button type="button" onClick={() => setOpen(true)}>重置</button>
      <ConfirmDialog open={open} title="重置主题" description="恢复当前主题。" confirmText="重置" buttonLabel="确认重置"
        onClose={() => setOpen(false)} onConfirm={() => setOpen(false)} />
    </>;
  }
  render(<Harness />);
  const opener = screen.getByRole("button", { name: "重置" });
  opener.focus(); fireEvent.click(opener);
  const input = screen.getByRole("textbox");
  fireEvent.change(input, { target: { value: "重置" } });
  // The browser must not apply this Enter's default activation to the newly focused opener.
  expect(fireEvent.keyDown(input, { key: "Enter" })).toBe(false);
  expect(screen.queryByRole("dialog", { name: "重置主题" })).not.toBeInTheDocument();
  expect(opener).toHaveFocus();
});

it("does not submit while Enter is committing an input-method composition", () => {
  const confirm = vi.fn();
  render(<ConfirmDialog open title="重置主题" description="恢复当前主题。" confirmText="重置" buttonLabel="确认重置"
    onClose={() => {}} onConfirm={confirm} />);
  const input = screen.getByRole("textbox");
  fireEvent.change(input, { target: { value: "重置" } });
  fireEvent.keyDown(input, { key: "Enter", isComposing: true });
  expect(confirm).not.toHaveBeenCalled();
});

it("returns escaped focus inside after an async failure so Escape can dismiss the confirmation", async () => {
  function Harness({ busy }: { busy: boolean }) {
    const [open, setOpen] = useState(true);
    return <>
      <button type="button">弹窗外</button>
      <ConfirmDialog open={open} busy={busy} title="恢复备份" description="恢复当前备份。" buttonLabel="确认恢复"
        error={busy ? null : "保存失败"} onClose={() => setOpen(false)} onConfirm={() => {}} />
    </>;
  }
  const rendered = render(<Harness busy={false} />);
  const confirm = screen.getByRole("button", { name: "确认恢复" });
  confirm.focus(); rendered.rerender(<Harness busy />);
  // Real browsers move focus out when the active submit button becomes disabled.
  screen.getByRole("button", { name: "弹窗外" }).focus();
  rendered.rerender(<Harness busy={false} />);
  const cancel = screen.getByRole("button", { name: "取消" });
  expect(cancel).toHaveFocus();
  fireEvent.keyDown(cancel, { key: "Escape" });
  expect(screen.queryByRole("dialog", { name: "恢复备份" })).not.toBeInTheDocument();
});
