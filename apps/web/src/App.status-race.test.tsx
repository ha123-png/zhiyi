import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { ModelStatus, SystemStatus } from "./types";

// Keep the real App/header and API read paths. Only the Settings child is a
// user-operated activation callback, so these cases cannot pass on its guard.
vi.mock("./components/SettingsPage", () => ({
  SettingsPage: ({ onModelStatusChanged }: { onModelStatusChanged?: () => void }) => (
    <section><h1>设置回调夹具</h1>
      <button type="button" onClick={onModelStatusChanged}>激活模型B</button>
    </section>
  ),
}));

function pendingRead() {
  let finish!: (response: Response) => void;
  const promise = new Promise<Response>(resolve => { finish = resolve; });
  return {
    promise,
    success: (body: unknown) => finish(new Response(JSON.stringify(body), { status: 200 })),
    fail: () => finish(new Response(JSON.stringify({ detail: "合成状态读取失败" }), { status: 503 })),
  };
}

function model(name: string): ModelStatus {
  return { connected: true, provider: "lm_studio", configured_model: name, available_models: [name] };
}
function system(name: string, worker = true): SystemStatus {
  return {
    api: { connected: true, message: name + " API已连接" },
    worker: { connected: worker, message: name + (worker ? " 消费者在线" : " 消费者未启动") },
    model: { connected: true, message: name + " 服务可用" },
    model_provider: "lm_studio", configured_model: name,
  };
}

function controlledStatusReads() {
  const models: ReturnType<typeof pendingRead>[] = [];
  const systems: ReturnType<typeof pendingRead>[] = [];
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const path = new URL(String(input), "http://localhost").pathname;
    if (path.endsWith("/models/status")) {
      const read = pendingRead(); models.push(read); return read.promise;
    }
    if (path.endsWith("/system/status")) {
      const read = pendingRead(); systems.push(read); return read.promise;
    }
    const body = path.endsWith("/user-state/onboarding") ? { completed_version: 99 } : [];
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
  }));
  return { models, systems };
}

async function activateB() {
  fireEvent.click(screen.getByRole("button", { name: "设置" }));
  fireEvent.click(await screen.findByRole("button", { name: "激活模型B" }));
}
function openDetails(container: HTMLElement) {
  const trigger = container.querySelector<HTMLButtonElement>(".system-status-trigger")!;
  if (trigger.dataset.state !== "open") fireEvent.click(trigger);
}

describe("App global status generations after model activation", () => {
  beforeEach(() => {
    HTMLElement.prototype.scrollIntoView = vi.fn();
    vi.stubGlobal("EventSource", class { addEventListener() {} close() {} });
  });

  it("keeps fresh B header and model when initial A reads finish late", async () => {
    const { models, systems } = controlledStatusReads();
    const { container } = render(<App />);
    await waitFor(() => { expect(models).toHaveLength(1); expect(systems).toHaveLength(1); });
    await activateB();
    await waitFor(() => { expect(models).toHaveLength(2); expect(systems).toHaveLength(2); });
    await act(async () => { models[1].success(model("模型B")); systems[1].success(system("模型B")); });
    expect(screen.getByRole("button", { name: /服务已就绪/ })).toBeInTheDocument();
    openDetails(container);
    expect(screen.getByText("提取模型：模型B · 服务可达")).toBeInTheDocument();

    await act(async () => { models[0].success(model("模型A")); systems[0].success(system("模型A", false)); });
    expect.soft(screen.queryByRole("button", { name: /服务已就绪/ })).toBeInTheDocument();
    expect.soft(screen.queryByText("提取模型：模型B · 服务可达")).toBeInTheDocument();
    expect.soft(screen.queryByText("提取模型：模型A · 服务可达")).not.toBeInTheDocument();
  });

  it("keeps a failed B read unknown despite late A success, then recovers by a current visible check", async () => {
    const { models, systems } = controlledStatusReads();
    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(false);
    try {
      const { container } = render(<App />);
      await waitFor(() => { expect(models).toHaveLength(1); expect(systems).toHaveLength(1); });
      await activateB();
      await waitFor(() => { expect(models).toHaveLength(2); expect(systems).toHaveLength(2); });
      await act(async () => { models[1].fail(); systems[1].fail(); });
      expect.soft(screen.queryByRole("button", { name: /连接暂不可用/ })).toBeInTheDocument();
      await act(async () => { models[0].success(model("模型A")); systems[0].success(system("模型A")); });
      expect.soft(screen.queryByRole("button", { name: /连接暂不可用/ })).toBeInTheDocument();
      openDetails(container);
      expect.soft(screen.queryByText("提取模型：模型A · 服务可达")).not.toBeInTheDocument();
      expect.soft(screen.queryByText("提取模型：状态待确认")).toBeInTheDocument();
      expect.soft(screen.queryByText("提取模型：未配置 · 检查中")).not.toBeInTheDocument();

      act(() => document.dispatchEvent(new Event("visibilitychange")));
      await waitFor(() => expect(systems).toHaveLength(3));
      await act(async () => { systems[2].success(system("模型B")); });
      expect(screen.getByRole("button", { name: /服务已就绪/ })).toBeInTheDocument();
    } finally { hidden.mockRestore(); }
  });

  it.each(["success", "failure"] as const)("clears old snapshots on activation and ignores a prior poll %s after B succeeds", async (oldResult) => {
    const { models, systems } = controlledStatusReads();
    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(false);
    try {
      const { container } = render(<App />);
      await waitFor(() => { expect(models).toHaveLength(1); expect(systems).toHaveLength(1); });
      await act(async () => { models[0].success(model("模型A")); systems[0].success(system("模型A")); });
      expect(screen.getByRole("button", { name: /服务已就绪/ })).toBeInTheDocument();
      act(() => document.dispatchEvent(new Event("visibilitychange")));
      await waitFor(() => expect(systems).toHaveLength(2));

      await activateB();
      await waitFor(() => { expect(models).toHaveLength(2); expect(systems).toHaveLength(3); });
      expect.soft(screen.queryByRole("button", { name: /正在连接服务/ })).toBeInTheDocument();
      openDetails(container);
      expect.soft(screen.queryByText("提取模型：模型A · 服务可达")).not.toBeInTheDocument();
      await act(async () => { models[1].success(model("模型B")); systems[2].success(system("模型B")); });
      await act(async () => {
        if (oldResult === "success") systems[1].success(system("模型A", false));
        else systems[1].fail();
      });
      expect.soft(screen.queryByRole("button", { name: /服务已就绪/ })).toBeInTheDocument();
      expect.soft(screen.queryByText("提取模型：模型B · 服务可达")).toBeInTheDocument();
      expect(models).toHaveLength(2); // Polling remains system-only.
    } finally { hidden.mockRestore(); }
  });
});
