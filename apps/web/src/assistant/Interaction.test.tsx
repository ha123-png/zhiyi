import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useConversationScroll, usePanelWidth } from "./Interaction";

afterEach(() => vi.restoreAllMocks());

function ResizablePanel() {
  const panel = usePanelWidth("test-width", 200, 150, 360);
  return <div style={{ width: panel.width }} data-testid="panel"><div {...panel.separator} aria-label="调整面板宽度" /></div>;
}

it("opens and resizes panels when the browser denies preference storage", () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new DOMException("blocked", "SecurityError"); });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("blocked", "SecurityError"); });
  render(<ResizablePanel />);
  expect(screen.getByTestId("panel")).toHaveStyle({ width: "200px" });
  fireEvent.keyDown(screen.getByRole("separator", { name: "调整面板宽度" }), { key: "ArrowRight" });
  expect(screen.getByTestId("panel")).toHaveStyle({ width: "216px" });
});

function Conversation({
  text,
  busy = false,
}: {
  text: string;
  busy?: boolean;
}) {
  const scroll = useConversationScroll("thread", busy);
  return (
    <>
      <div data-testid="viewport" ref={scroll.viewport} {...scroll.events}>
        <div ref={scroll.content}>
          <p className="ask-message">{text}</p>
        </div>
      </div>
      {scroll.paused && <button onClick={scroll.resume}>回到最新</button>}
    </>
  );
}

it("keeps the reader's position during streaming and resumes only on request or a new send", async () => {
  const { rerender } = render(<Conversation text="first" />);
  const viewport = screen.getByTestId("viewport");
  let height = 1000;
  let top = 0;
  Object.defineProperties(viewport, {
    scrollHeight: { get: () => height },
    clientHeight: { get: () => 300 },
    scrollTop: {
      get: () => top,
      set: (value: number) => {
        top = Math.max(0, Math.min(height - 300, value));
      },
    },
  });
  rerender(<Conversation text="answer starts" busy />);
  await waitFor(() => expect(top).toBe(700));
  fireEvent.wheel(viewport, { deltaY: -150 });
  viewport.scrollTop = 400;
  fireEvent.scroll(viewport);
  height = 1400;
  rerender(
    <Conversation text="answer keeps growing while reading history" busy />,
  );
  // Let the real MutationObserver and animation frame handle the growing answer.
  await act(async () => {
    await new Promise(requestAnimationFrame);
    await new Promise(requestAnimationFrame);
  });
  expect(top).toBe(400);
  fireEvent.click(screen.getByRole("button", { name: "回到最新" }));
  expect(top).toBe(1100);
  expect(
    screen.queryByRole("button", { name: "回到最新" }),
  ).not.toBeInTheDocument();
  height = 1700;
  rerender(<Conversation text="answer finishes" />);
  await waitFor(() => expect(top).toBe(1400));
  fireEvent.wheel(viewport, { deltaY: -300 });
  viewport.scrollTop = 500;
  fireEvent.scroll(viewport);
  rerender(<Conversation text="new question starts" busy />);
  await waitFor(() => expect(top).toBe(1400));
});
