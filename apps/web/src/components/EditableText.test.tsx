import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { EditableText } from "./EditableText";

it("restores cancelled DOM text and allows a subsequent real edit", () => {
  const commit = vi.fn();
  const cancel = vi.fn();
  const view = render(<div tabIndex={0}><EditableText editing value="原值" onCommit={commit} onCancel={cancel} /></div>);
  const cell = screen.getByText("原值");
  cell.textContent = "不应保存的输入";
  fireEvent.keyDown(cell, { key: "Escape" });
  expect(cell).toHaveTextContent("原值");
  expect(commit).not.toHaveBeenCalled();
  expect(cancel).toHaveBeenCalledOnce();
  expect(cell.parentElement).toHaveFocus();
  view.rerender(<div tabIndex={0}><EditableText editing={false} value="原值" onCommit={commit} onCancel={cancel} /></div>);
  view.rerender(<div tabIndex={0}><EditableText editing value="原值" onCommit={commit} onCancel={cancel} /></div>);
  cell.textContent = "新值";
  fireEvent.keyDown(cell, { key: "Enter" });
  expect(commit).toHaveBeenCalledExactlyOnceWith("新值");
});

it("restores the supplied fact when an unchanged value leaves editing, then shows the saved value", () => {
  const commit = vi.fn();
  const view = render(<EditableText editing value="16" onCommit={commit} />);
  const cell = screen.getByText("16");
  cell.textContent = "17";
  fireEvent.keyDown(cell, { key: "Enter" });
  expect(commit).toHaveBeenCalledExactlyOnceWith("17");
  view.rerender(<EditableText editing={false} value="16" onCommit={commit} />);
  expect(cell).toHaveTextContent(/^16$/);
  view.rerender(<EditableText editing={false} value="18" onCommit={commit} />);
  expect(cell).toHaveTextContent(/^18$/);
});

it.each([
  { key: "Enter", isComposing: true },
  { key: "Escape", isComposing: true },
  { key: "Enter", keyCode: 229 },
])("keeps IME candidate keys inside the editor: %j", key => {
  const commit = vi.fn(), cancel = vi.fn();
  render(<EditableText editing value="原值" onCommit={commit} onCancel={cancel} />);
  const cell = screen.getByText("原值");
  cell.textContent = "候选中的中文";
  fireEvent.keyDown(cell, key);
  expect(commit).not.toHaveBeenCalled();
  expect(cancel).not.toHaveBeenCalled();
  expect(cell).toHaveTextContent("候选中的中文");
  expect(cell).toHaveFocus();
  fireEvent.keyDown(cell, { key: "Enter", isComposing: false });
  expect(commit).toHaveBeenCalledExactlyOnceWith("候选中的中文");
});

it("keeps a focused draft through a fact refresh and restores the newest fact on cancel", () => {
  const commit = vi.fn(), cancel = vi.fn();
  const view = render(<div tabIndex={0}><EditableText editing value="原值" onCommit={commit} onCancel={cancel} /></div>);
  const cell = screen.getByText("原值");
  cell.textContent = "尚未提交的草稿";
  fireEvent.input(cell);
  view.rerender(<div tabIndex={0}><EditableText editing value="后台新值" onCommit={commit} onCancel={cancel} /></div>);
  expect(cell).toHaveTextContent("尚未提交的草稿");
  expect(cell).toHaveFocus();
  fireEvent.keyDown(cell, { key: "Escape" });
  expect(cell).toHaveTextContent("后台新值");
  expect(cancel).toHaveBeenCalledOnce();
  expect(commit).not.toHaveBeenCalled();
});
