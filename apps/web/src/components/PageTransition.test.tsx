import { act, fireEvent, render, screen } from "@testing-library/react";
import { lazy, Suspense, useRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PageEntrance, PageOpening } from "./PageTransition";

describe("page entrance", () => {
  const originalAnimate = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "animate");
  let cancellations: ReturnType<typeof vi.fn>[];
  let reduced: boolean;
  let listeners: Set<() => void>;
  beforeEach(() => {
    cancellations = [];
    reduced = false;
    listeners = new Set();
    Object.defineProperty(HTMLElement.prototype, "animate", { configurable: true, value: vi.fn(() => {
      const cancel = vi.fn();
      cancellations.push(cancel);
      return { cancel };
    }) });
    vi.stubGlobal("matchMedia", () => ({
      get matches() { return reduced; },
      addEventListener: (_type: string, callback: () => void) => listeners.add(callback),
      removeEventListener: (_type: string, callback: () => void) => listeners.delete(callback),
    }));
  });
  afterEach(() => {
    if (originalAnimate) Object.defineProperty(HTMLElement.prototype, "animate", originalAnimate);
    else Reflect.deleteProperty(HTMLElement.prototype, "animate");
    vi.unstubAllGlobals();
  });

  it("waits for the actual lazy destination instead of animating its loading placeholder", async () => {
    let ready!: (value: { default: () => React.JSX.Element }) => void;
    const Destination = lazy(() => new Promise<{ default: () => React.JSX.Element }>(resolve => { ready = resolve; }));
    function Example() {
      const container = useRef<HTMLDivElement>(null);
      return <div ref={container}><Suspense fallback={<PageOpening />}>
        <PageEntrance page="guide" container={container} /><Destination />
      </Suspense></div>;
    }
    render(<Example />);
    expect(screen.getByRole("status", { name: "页面加载中" })).toBeInTheDocument();
    expect(cancellations).toHaveLength(0);
    await act(async () => { ready({ default: () => <section aria-label="使用说明"><h2>知意使用手册</h2></section> }); });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "知意使用手册" })).toBeInTheDocument();
    expect(cancellations).toHaveLength(1);
  });

  function StableContent({ page }: { page: string }) {
    const container = useRef<HTMLDivElement>(null);
    return <div ref={container}><PageEntrance page={page} container={container} />
      <h2>业务内容</h2><input aria-label="未提交草稿" />
    </div>;
  }

  it("cancels interrupted motion without remounting content or taking keyboard focus", () => {
    const view = render(<StableContent page="guide" />);
    view.rerender(<StableContent page="tables" />);
    expect(cancellations).toHaveLength(1);
    const field = screen.getByRole("textbox");
    fireEvent.change(field, { target: { value: "保留草稿" } });
    field.focus();
    const earlier = [...cancellations];
    view.rerender(<StableContent page="settings" />);
    expect(earlier.every(cancel => cancel.mock.calls.length === 1)).toBe(true);
    expect(screen.getByRole("textbox")).toBe(field);
    expect(field).toHaveValue("保留草稿");
    expect(field).toHaveFocus();
    const current = cancellations.slice(earlier.length);
    act(() => { reduced = true; listeners.forEach(notify => notify()); });
    expect(current.every(cancel => cancel.mock.calls.length === 1)).toBe(true);
    view.unmount();
    expect(listeners.size).toBe(0);
  });

  it("opens immediately with no entrance motion when reduced motion is requested", () => {
    reduced = true;
    const view = render(<StableContent page="settings" />);
    view.rerender(<StableContent page="guide" />);
    expect(screen.getByRole("heading", { name: "业务内容" })).toBeInTheDocument();
    expect(cancellations).toHaveLength(0);
    expect(listeners.size).toBe(0);
  });
});
