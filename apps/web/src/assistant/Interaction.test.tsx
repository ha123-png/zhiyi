import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { expect, it } from "vitest";
import { useConversationScroll } from "./Interaction";

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
