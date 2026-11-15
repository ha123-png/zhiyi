import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ServiceStatus } from "./ServiceStatus";
import { GlobalTaskCard } from "./TaskActivity";
import type { ModelStatus, SystemStatus } from "../types";

vi.mock("../assistant/ModelConnectionState", () => ({ ModelConnectionState: () => <span>问知意模型：聊天方案 · 尚未验证</span> }));

const system: SystemStatus = { api: { connected: true, message: "API 已连接" }, worker: { connected: true, message: "任务消费者在线" }, model: { connected: false, message: "提取模型未连接" }, model_provider: "lm_studio", configured_model: "提取方案" };
const model: ModelStatus = { connected: false, provider: "lm_studio", configured_model: "提取方案", available_models: [] };

describe("service status in the task card", () => {
  it("keeps API/worker readiness separate from extraction and chat status, and restores focus after closing", async () => {
    render(<GlobalTaskCard tasks={[]} serviceStatus={<ServiceStatus systemStatus={system} modelStatus={model} failed={false} />} />);
    const trigger = screen.getByRole("button", { name: "服务已就绪" });
    expect(trigger.closest(".gtb-card")).not.toBeNull();
    expect(screen.queryByText("文件处理：已启动")).not.toBeInTheDocument();
    fireEvent.click(trigger);
    const details = await screen.findByRole("dialog", { name: "连接详情" });
    expect(within(details).getByText("本机程序：可用")).toHaveAttribute("title", "API 已连接");
    expect(within(details).getByText("文件处理：已启动")).toHaveAttribute("title", "任务消费者在线");
    expect(within(details).getByText("提取模型：提取方案 · 未连接")).toBeInTheDocument();
    expect(within(details).getByText("问知意模型：聊天方案 · 尚未验证")).toBeInTheDocument();
    fireEvent.click(within(details).getByRole("button", { name: "关闭连接详情" }));
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it.each([
    [null, false, "正在连接服务…"], [null, true, "连接暂不可用"],
    [{ ...system, api: { connected: false, message: "API 未连接" } }, false, "服务未连接"],
    [{ ...system, worker: { connected: false, message: "消费者未启动" } }, false, "文件处理未启动"],
  ] as const)("keeps unknown and unavailable services explicit", async (status, failed, label) => {
    render(<ServiceStatus systemStatus={status} modelStatus={null} failed={failed} />);
    fireEvent.click(screen.getByRole("button", { name: label }));
    expect(await screen.findByText("提取模型：状态待确认")).toBeInTheDocument();
    if (!status) expect(screen.getByText(failed ? "暂时无法读取服务状态，将自动重试。" : "正在检查程序、文件处理和模型连接…")).toBeInTheDocument();
  });

  it("keeps configuration errors ahead of transport reachability", async () => {
    render(<ServiceStatus systemStatus={system} modelStatus={{ ...model, connected: true, configuration_error: "当前方案需要检查" }} failed={false} />);
    fireEvent.click(screen.getByRole("button", { name: "服务已就绪" }));
    expect(await screen.findByText("提取模型：提取方案 · 配置需检查")).toHaveAttribute("title", "当前方案需要检查");
  });
});
