import { act, fireEvent, renderHook } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { usePointerDrag } from "./usePointerDrag";

function pointer(type: string, pointerId: number) {
  const event = new Event(type, { bubbles: true });
  Object.defineProperty(event, "pointerId", { value: pointerId });
  fireEvent(window, event);
}

it("ignores other fingers and ends only the active pointer session", () => {
  const { result } = renderHook(usePointerDrag);
  const move = vi.fn(), end = vi.fn();
  act(() => result.current(move, end, 4));
  pointer("pointermove", 5);
  pointer("pointercancel", 5);
  expect(move).not.toHaveBeenCalled();
  expect(end).not.toHaveBeenCalled();
  pointer("pointermove", 4);
  pointer("pointerup", 4);
  pointer("pointermove", 4);
  expect(move).toHaveBeenCalledTimes(1);
  expect(end).toHaveBeenCalledTimes(1);
});

it("releases an interrupted owner on unmount without a final state update", () => {
  const { result, unmount } = renderHook(usePointerDrag);
  const move = vi.fn(), end = vi.fn();
  act(() => result.current(move, end, 4));
  unmount();
  pointer("pointermove", 4);
  pointer("pointerup", 4);
  fireEvent.blur(window);
  expect(move).not.toHaveBeenCalled();
  expect(end).not.toHaveBeenCalled();
});

it("replaces an unfinished session instead of accumulating movement listeners", () => {
  const { result } = renderHook(usePointerDrag);
  const oldMove = vi.fn(), nextMove = vi.fn();
  act(() => { result.current(oldMove, undefined, 1); result.current(nextMove, undefined, 2); });
  pointer("pointermove", 1);
  pointer("pointermove", 2);
  expect(oldMove).not.toHaveBeenCalled();
  expect(nextMove).toHaveBeenCalledTimes(1);
});

it("releases native pointer capture on blur and unmount without duplicate completion", () => {
  const { result, unmount } = renderHook(usePointerDrag);
  const target = document.createElement("div");
  const capture = vi.fn(), release = vi.fn(), end = vi.fn();
  Object.assign(target, { setPointerCapture: capture, releasePointerCapture: release, hasPointerCapture: () => true });
  act(() => result.current(vi.fn(), end, 4, target));
  expect(capture).toHaveBeenCalledWith(4);
  fireEvent.blur(window);
  pointer("lostpointercapture", 4);
  expect(release).toHaveBeenCalledTimes(1);
  expect(end).toHaveBeenCalledTimes(1);
  act(() => result.current(vi.fn(), end, 5, target));
  unmount();
  expect(release).toHaveBeenLastCalledWith(5);
  expect(release).toHaveBeenCalledTimes(2);
  expect(end).toHaveBeenCalledTimes(1);
});
