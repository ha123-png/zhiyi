import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useFieldSort } from "./useFieldSort";

function Sortable({ identity = "a|b|c", onMove }: { identity?: string; onMove: (from: number, to: number) => void }) {
  const sort = useFieldSort(identity, onMove);
  return <div className="app-page-wrap" data-testid="viewport"><div ref={sort.list}>
    {[0, 1, 2].map(index => <div data-field-index={index} key={index}>
      <button onPointerDown={event => sort.onPointerDown(event, index)} onPointerMove={sort.onPointerMove}
        onPointerUp={sort.onPointerUp} onPointerCancel={sort.onPointerCancel} onLostPointerCapture={sort.onPointerCancel}>字段 {index + 1}</button>
    </div>)}
    {sort.placeholder && <div data-testid="placeholder" />}
  </div></div>;
}

function pointer(target: HTMLElement, type: string, y: number, pointerId = 4) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperties(event, { button: { value: 0 }, clientY: { value: y }, pointerId: { value: pointerId } });
  fireEvent(target, event);
}

let frames: Map<number, FrameRequestCallback>;
beforeEach(() => {
  frames = new Map();
  let nextFrame = 0;
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { frames.set(++nextFrame, callback); return nextFrame; });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
});
afterEach(() => vi.restoreAllMocks());

function begin() {
  const viewport = screen.getByTestId("viewport");
  vi.spyOn(viewport, "getBoundingClientRect").mockReturnValue({ top: 0, bottom: 120 } as DOMRect);
  const rows = [...viewport.querySelectorAll<HTMLElement>("[data-field-index]")];
  rows.forEach((row, index) => Object.defineProperties(row, { offsetTop: { value: index * 48 }, offsetHeight: { value: 40 } }));
  const handle = screen.getByRole("button", { name: "字段 1" });
  const captures = new Set<number>();
  Object.assign(handle, {
    setPointerCapture: (id: number) => captures.add(id), hasPointerCapture: (id: number) => captures.has(id),
    releasePointerCapture: (id: number) => captures.delete(id),
  });
  pointer(handle, "pointerdown", 10);
  pointer(handle, "pointermove", 160);
  expect(rows[0]).toHaveClass("field-lifted");
  expect(frames.size).toBe(1);
  return { handle, rows, viewport, captures };
}

it("cancels a lifted field and its autoscroll when the window loses focus", () => {
  const onMove = vi.fn();
  render(<Sortable onMove={onMove} />);
  const { handle, rows, viewport, captures } = begin();
  fireEvent.blur(window);
  expect(frames.size).toBe(0);
  expect(captures.size).toBe(0);
  expect(rows[0]).not.toHaveClass("field-lifted");
  expect(rows.every(row => row.style.transform === "")).toBe(true);
  expect(screen.queryByTestId("placeholder")).not.toBeInTheDocument();
  expect(viewport.scrollTop).toBe(0);
  pointer(handle, "pointerup", 160);
  expect(onMove).not.toHaveBeenCalled();
});

it("releases capture and visual transforms when an active owner unmounts", () => {
  const onMove = vi.fn();
  const view = render(<Sortable onMove={onMove} />);
  const { captures, rows } = begin();
  view.unmount();
  expect(frames.size).toBe(0);
  expect(captures.size).toBe(0);
  expect(rows[0]).not.toHaveClass("field-lifted");
  expect(rows.every(row => row.style.transform === "")).toBe(true);
  expect(onMove).not.toHaveBeenCalled();
});

it("cancels a position snapshot when the field identity changes during a drag", () => {
  const onMove = vi.fn();
  const view = render(<Sortable onMove={onMove} />);
  const { handle, captures } = begin();
  view.rerender(<Sortable identity="another-template" onMove={onMove} />);
  pointer(handle, "pointerup", 160);
  expect(onMove).not.toHaveBeenCalled();
  expect(captures.size).toBe(0);
  expect(frames.size).toBe(0);
  expect(screen.queryByTestId("placeholder")).not.toBeInTheDocument();
});

it("still commits one normal drop and stops its animation frame", () => {
  const onMove = vi.fn();
  render(<Sortable onMove={onMove} />);
  const { handle, captures } = begin();
  act(() => {
    const [id, tick] = [...frames][0]; frames.delete(id); tick(0);
  });
  pointer(handle, "pointerup", 160);
  expect(onMove).toHaveBeenCalledExactlyOnceWith(0, 2);
  expect(captures.size).toBe(0);
  expect(frames.size).toBe(0);
});
