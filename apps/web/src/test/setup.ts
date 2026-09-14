import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}

// 普通全局赋值，避免被 afterEach 的 unstubAllGlobals 移除
globalThis.ResizeObserver = ResizeObserverMock as unknown as typeof ResizeObserver;
// Layout animation may restore scroll after measuring; jsdom has no viewport.
// Real scrolling and motion are exercised by the browser journey.
window.scrollTo = () => {};
window.matchMedia = (query: string) => ({
  matches: false, media: query, onchange: null,
  addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {},
  dispatchEvent: () => false,
});
// jsdom does not implement the native dialog lifecycle; real browser tests
// separately exercise focus trapping, Escape and modal/backdrop behavior.
HTMLDialogElement.prototype.showModal = function () { this.open = true; };
HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new Event("close")); };

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
