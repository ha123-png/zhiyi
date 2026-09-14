import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { Onboarding } from "./Onboarding";

vi.mock("../api", () => ({ setOnboardingState: vi.fn(() => Promise.resolve()) }));
beforeEach(() => { localStorage.clear(); });

it("keeps keyboard navigation in the guide, announces each step and restores focus", () => {
  const opener = document.createElement("button");
  document.body.appendChild(opener);
  opener.focus();
  const onClose = vi.fn();
  const props = { open: true, modelStatus: null, onNavigate: vi.fn(), onShowDemo: vi.fn(), onClose };
  const { rerender } = render(<Onboarding {...props} />);
  expect(screen.getByRole("group", { name: "认识知意" })).toHaveFocus();
  const next = screen.getByRole("button", { name: "下一步" });
  next.focus();
  fireEvent.keyDown(next, { key: "Tab" });
  expect(screen.getByRole("button", { name: "跳过引导" })).toHaveFocus();
  fireEvent.click(next);
  expect(screen.getByRole("group", { name: "选择模型" })).toHaveFocus();
  fireEvent.keyDown(screen.getByRole("group", { name: "选择模型" }), { key: "Escape" });
  expect(onClose).toHaveBeenCalledOnce();
  rerender(<Onboarding {...props} open={false} />);
  expect(opener).toHaveFocus();
  opener.remove();
});
