import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { Onboarding, ONBOARDING_DONE_KEY } from "./Onboarding";
import { setOnboardingState } from "../api";

vi.mock("../api", () => ({ setOnboardingState: vi.fn(() => Promise.resolve()) }));
beforeEach(() => { localStorage.clear(); vi.clearAllMocks(); });

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

it("returns from connection settings to the same step without prematurely completing the guide", () => {
  const onSetup = vi.fn();
  const props = { open: true, modelStatus: null, onNavigate: vi.fn(), onShowDemo: vi.fn(), onClose: vi.fn(), onSetup };
  const { rerender } = render(<Onboarding {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "下一步" }));
  fireEvent.click(screen.getByRole("button", { name: /云端 AI 服务/ }));
  fireEvent.click(screen.getByRole("button", { name: "下一步" }));
  expect(screen.queryByRole("button", { name: "下一步" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "前往连接设置" }));
  expect(onSetup).toHaveBeenCalledWith("ai");
  expect(setOnboardingState).not.toHaveBeenCalled();
  expect(localStorage.getItem(ONBOARDING_DONE_KEY)).toBeNull();
  rerender(<Onboarding {...props} open={false} />);
  rerender(<Onboarding {...props} />);
  expect(screen.getByRole("group", { name: "连接服务" })).toHaveFocus();
  expect(screen.getByRole("heading", { name: "使用云端大模型" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "先看示例" }));
  expect(screen.getByRole("button", { name: "使用自己的文件" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "使用自己的文件" }));
  expect(props.onNavigate).toHaveBeenCalledWith("extract");
  expect(setOnboardingState).toHaveBeenCalledOnce();
});

it("keeps examples read-only and routes each example without a settings detour", () => {
  const props = { open: true, modelStatus: null, onNavigate: vi.fn(), onShowDemo: vi.fn(), onClose: vi.fn() };
  render(<Onboarding {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "下一步" }));
  fireEvent.click(screen.getByRole("button", { name: "下一步" }));
  fireEvent.click(screen.getByRole("button", { name: "先看示例" }));
  expect(screen.getAllByRole("button", { name: /查看示例/ })).toHaveLength(6);
  fireEvent.click(screen.getByRole("button", { name: /规则发现错误/ }));
  expect(props.onShowDemo).toHaveBeenCalledWith("rule");
  expect(props.onNavigate).not.toHaveBeenCalled();
  expect(props.onClose).toHaveBeenCalledOnce();
});

it("honors a newly chosen setup path even when another model is already connected", () => {
  const props = { open: true, modelStatus: { connected: true, provider: "lm_studio", configured_model: "existing", available_models: [] }, onNavigate: vi.fn(), onShowDemo: vi.fn(), onClose: vi.fn(), onSetup: vi.fn() };
  const view = render(<Onboarding {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "下一步" }));
  fireEvent.click(screen.getByRole("button", { name: /云端 AI 服务/ }));
  fireEvent.click(screen.getByRole("button", { name: "下一步" }));
  expect(screen.getByRole("heading", { name: "使用云端大模型" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "前往连接设置" }));
  expect(props.onSetup).toHaveBeenCalledWith("ai");
  view.rerender(<Onboarding {...props} open={false} />);
  view.rerender(<Onboarding {...props} />);
  expect(screen.getByRole("heading", { name: "使用云端大模型" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "继续" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "前往连接设置" }));
  view.rerender(<Onboarding {...props} open={false} />);
  view.rerender(<Onboarding {...props} modelStatus={{ ...props.modelStatus, provider: "openai_compatible", configured_model: "new-cloud" }} />);
  expect(screen.getByRole("heading", { name: "连接已就绪" })).toBeInTheDocument();
  expect(screen.getByText(/new-cloud/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "继续" }));
  expect(screen.getByRole("group", { name: "开始使用" })).toHaveFocus();
});
