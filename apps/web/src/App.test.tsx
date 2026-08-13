import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

describe("App", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (input: string | URL) => {
        const url = String(input);
        const body = url.endsWith("/models/status")
          ? {
              connected: true,
              provider: "lm_studio",
              configured_model: "qwen3.5-4b",
              available_models: ["qwen3.5-4b"],
            }
          : url.endsWith("/system/status")
            ? {
                api: { connected: true, message: "API 已连接" },
                worker: { connected: true, message: "任务消费者在线" },
                model: { connected: true, message: "模型 qwen3.5-4b 可用" },
                model_provider: "lm_studio",
                configured_model: "qwen3.5-4b",
              }
            : [];

        return {
          ok: true,
          json: async () => body,
        };
      }),
    );
  });

  it("shows the primary file-to-data workflow", async () => {
    render(<App />);

    expect(
      screen.getByRole("heading", { name: "任务队列" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "活动任务" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "待选模板/表" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "最近完成" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "失败任务" })).toBeInTheDocument();
    expect(await screen.findByText(/任务消费者在线/)).toHaveTextContent(
      "API 已连接 · 任务消费者在线",
    );

    fireEvent.click(screen.getByRole("button", { name: "提取工作台" }));

    expect(screen.getByRole("heading", { name: "上传文档" })).toBeInTheDocument();
    expect(
      screen.getByText("拖入文件并匹配模板后，提取结果表与校验问题将显示在此"),
    ).toBeInTheDocument();
  });

  it("renders dashboard, settings and connections pages", async () => {
    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: "仪表盘" }));

    // 仪表盘已接入真实数据：KPI 标签可见，平均耗时等无后端统计的字段诚实留空
    expect(
      screen.getByRole("heading", { name: "数据仪表盘" }),
    ).toBeInTheDocument();
    expect(screen.getByText("累计处理文件")).toBeInTheDocument();
    expect(screen.getByText("平均处理耗时")).toBeInTheDocument();

    // 设置页已接入：配置卡可见
    fireEvent.click(screen.getByRole("button", { name: "设置" }));
    expect(
      screen.getByRole("heading", { name: "系统设置" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /AI 服务配置/ }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "深色" }));
    expect(document.documentElement).toHaveClass("dark");
    expect(localStorage.getItem("theme")).toBe("dark");
    fireEvent.click(screen.getByRole("button", { name: "跟随系统" }));

    // 接口页已接入：集成 API 与 MCP 文档
    fireEvent.click(screen.getByRole("button", { name: "接口" }));
    expect(
      screen.getByRole("heading", { name: "接口与 MCP" }),
    ).toBeInTheDocument();
    expect(screen.getByText("读写密钥")).toBeInTheDocument();
  });

  it("starts the global timer the moment an upload begins", async () => {
    // 上传请求可控挂起：验证选择文件后、后端创建任务响应返回前，
    // 全局任务条已立即显示该文件并从零开始计时（前端状态不依赖后端响应）。
    let resolveUpload!: (response: unknown) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(
        async (input: string | URL, init?: RequestInit) => {
          const url = String(input);
          if (url.endsWith("/models/status")) {
            return {
              ok: true,
              json: async () => ({
                connected: true,
                provider: "lm_studio",
                configured_model: "qwen3.5-4b",
                available_models: ["qwen3.5-4b"],
              }),
            };
          }
          if (url.endsWith("/system/status")) {
            return {
              ok: true,
              json: async () => ({
                api: { connected: true, message: "API 已连接" },
                worker: { connected: true, message: "任务消费者在线" },
                model: { connected: true, message: "模型可用" },
                model_provider: "lm_studio",
                configured_model: "qwen3.5-4b",
              }),
            };
          }
          if (url.endsWith("/tasks") && init?.method === "POST") {
            return new Promise((resolve) => {
              resolveUpload = (response) =>
                resolve({ ok: true, json: async () => response });
            });
          }
          return { ok: true, json: async () => [] };
        },
      ),
    );

    const { container } = render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "提取工作台" }));
    const fileInput = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    const file = new File(["x"], "发票_001.pdf", { type: "application/pdf" });

    fireEvent.change(fileInput, { target: { files: [file] } });

    // 上传请求发出前：全局任务条立即显示该文件、状态"处理中"并开始计时
    expect(await screen.findByText("发票_001.pdf")).toBeInTheDocument();
    expect(screen.getAllByText("处理中").length).toBeGreaterThan(0);
    expect(screen.getByText(/已用 00:00/)).toBeInTheDocument();

    // 后端响应返回：乐观任务被真实任务替换；列表 mock 无真实任务，
    // 全局任务条回到空闲（不再显示乐观的"处理中"）。
    act(() => {
      resolveUpload({
        id: "real-task-1",
        filename: "发票_001.pdf",
        content_type: "application/pdf",
        size_bytes: 1,
        sha256: "digest",
        template_mode: "smart",
        template_id: null,
        template_version: null,
        candidate_templates: [],
        status: "queued",
        duplicate_of_task_id: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      });
    });
    await waitFor(() =>
      expect(screen.getByText("当前空闲")).toBeInTheDocument(),
    );
  });

  it("reports queue pause failures from the global task card instead of claiming success", async () => {
    const activeTask = {
      id: "active-queue-task",
      filename: "处理中.pdf",
      content_type: "application/pdf",
      size_bytes: 1,
      sha256: "queue-digest",
      template_mode: "smart",
      template_id: null,
      template_version: null,
      candidate_templates: [],
      status: "processing",
      duplicate_of_task_id: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (input: string | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/queue/pause") && init?.method === "POST") {
          return { ok: false, status: 503, json: async () => ({ detail: "队列控制暂不可用" }) };
        }
        if (url.endsWith("/models/status")) return { ok: true, json: async () => ({ connected: true }) };
        if (url.endsWith("/system/status")) {
          return { ok: true, json: async () => ({
            api: { connected: true, message: "API 已连接" },
            worker: { connected: true, message: "任务消费者在线" },
            model: { connected: true, message: "模型可用" },
          }) };
        }
        if (url.includes("/tasks")) return { ok: true, json: async () => [activeTask] };
        return { ok: true, json: async () => [] };
      }),
    );

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "暂停队列" }));

    expect(await screen.findByText("队列控制暂不可用")).toBeInTheDocument();
    expect(screen.queryByText("队列已暂停。")).not.toBeInTheDocument();
  });

  it("refreshes the global task bar immediately when a task event arrives", async () => {
    // 模拟 EventSource：手动触发 task 事件，验证前端"马上"刷新全局任务条
    const listeners: Record<string, (event?: MessageEvent) => void> = {};
    class MockEventSource {
      addEventListener(type: string, fn: (event?: MessageEvent) => void) {
        listeners[type] = fn;
      }
      close() {
        vi.fn();
      }
    }
    vi.stubGlobal("EventSource", MockEventSource);
    const fetchMock = vi.fn().mockImplementation(
      async (input: string | URL) => {
        const url = String(input);
        if (url.endsWith("/models/status")) {
          return {
            ok: true,
            json: async () => ({
              connected: true,
              provider: "lm_studio",
              configured_model: "qwen3.5-4b",
              available_models: ["qwen3.5-4b"],
            }),
          };
        }
        if (url.endsWith("/system/status")) {
          return {
            ok: true,
            json: async () => ({
              api: { connected: true, message: "API 已连接" },
              worker: { connected: true, message: "任务消费者在线" },
              model: { connected: true, message: "模型可用" },
              model_provider: "lm_studio",
              configured_model: "qwen3.5-4b",
            }),
          };
        }
        return { ok: true, json: async () => [] };
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]) => String(url).includes("/tasks")),
      ).toBe(true),
    );

    const before = fetchMock.mock.calls.filter(([url]) =>
      String(url).includes("/tasks"),
    ).length;
    act(() => {
      listeners["task"]?.({
        data: JSON.stringify([
          { id: "evt-1", status: "processing", filename: "a.txt", failure_message: null },
        ]),
      } as MessageEvent);
    });
    await waitFor(() => {
      const after = fetchMock.mock.calls.filter(([url]) =>
        String(url).includes("/tasks"),
      ).length;
      expect(after).toBeGreaterThan(before);
    });
  });

  it("restores each page scroll position when navigating back", async () => {
    const { container } = render(<App />);
    const pageWrap = container.querySelector(".app-page-wrap") as HTMLDivElement;
    Object.defineProperty(pageWrap, "scrollTop", { value: 420, writable: true });

    fireEvent.click(screen.getByRole("button", { name: "仪表盘" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "数据仪表盘" })).toBeInTheDocument());

    const dashboardWrap = container.querySelector(".app-page-wrap") as HTMLDivElement;
    Object.defineProperty(dashboardWrap, "scrollTop", { value: 160, writable: true });
    fireEvent.click(screen.getByRole("button", { name: "状态监控" }));

    await waitFor(() => {
      const restored = container.querySelector(".app-page-wrap") as HTMLDivElement;
      expect(restored.scrollTop).toBe(420);
    });
  });
});
