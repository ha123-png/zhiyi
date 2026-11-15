import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { ONBOARDING_VERSION } from "./components/Onboarding";

describe("App", () => {
  beforeEach(() => {
    // jsdom has no layout; example scrolling is verified in Chromium.
    HTMLElement.prototype.scrollIntoView = vi.fn();
    vi.stubGlobal("EventSource", class {
      addEventListener() {}
      close() {}
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (input: string | URL) => {
        const url = String(input);
        if (url.endsWith("/user-state/onboarding")) return { ok: true, json: async () => ({ completed_version: ONBOARDING_VERSION }) };
        if (url.includes("/stats/overview")) return { ok: true, json: async () => ({
          rows_trend: [], templates: [], model_usage: {
            calls: 0, completed: 0, failed: 0, interrupted: 0, elapsed_ms: 0,
            elapsed_sample_count: 0, average_elapsed_ms: null,
            prompt_tokens: null, completion_tokens: null, total_tokens: null,
            calls_with_usage: 0, calls_with_cache_usage: 0, cached_tokens: null,
            cache_hit_ratio: null, cache_measured_prompt_tokens: 0,
            by_purpose: [], coverage_start: null, history_note: "历史调用未记录用量。",
          },
        }) };
        if (url.endsWith("/stats/cards")) return { ok: true, json: async () => ({ items: [], limit: 4 }) };
        if (url.includes("/stats/summary")) return { ok: true, json: async () => ({
          row_count: 0, table_count: 0, template_count: 0, new_rows: 0,
        }) };
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
      screen.getByRole("group", { name: "任务状态筛选" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "任务状态筛选" })).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(await screen.findByRole("button", {name: /服务已就绪/})).toBeInTheDocument();
    expect(screen.queryByText(/任务消费者在线/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "文件提取" }));

    expect(await screen.findByRole("heading", { name: "提取出的数据" })).toBeInTheDocument();
    expect(
      await screen.findByText("拖入文件并匹配模板后，提取结果表与校验问题将显示在此"),
    ).toBeInTheDocument();
  });

  it.each(["continue", "cancel"])("hands expansion focus over only after guarded navigation can %s", async decision => {
    render(<App />);
    const trigger = screen.getAllByRole("button", { name: "问知意" }).find(button => button.classList.contains("ask-trigger"))!;
    trigger.focus();
    fireEvent.click(trigger);
    const drawer = screen.getByRole("dialog", { name: "快捷问知意" });
    fireEvent.change(within(drawer).getByRole("textbox", { name: "向问知意提问" }), { target: { value: "导航确认后继续的合成草稿" } });
    let proceed: (() => void) | undefined;
    const guard = (event: Event) => {
      event.preventDefault();
      proceed = (event as CustomEvent<{ proceed: () => void }>).detail.proceed;
    };
    window.addEventListener("zhiyi:before-navigate", guard);
    try {
      fireEvent.click(within(drawer).getByRole("button", { name: "展开到问知意页面" }));
      expect(proceed).toBeTypeOf("function");
      expect(screen.getByRole("group", { name: "任务状态筛选" })).toBeVisible();
      expect(screen.queryByRole("textbox", { name: "向问知意提问" })).not.toBeInTheDocument();
    } finally {
      window.removeEventListener("zhiyi:before-navigate", guard);
    }
    const nav = screen.getByRole("navigation", { name: "工作区" });
    const sidebarAsk = within(nav).getByRole("button", { name: "问知意" });
    if (decision === "continue") act(() => proceed!());
    else {
      // Cancelling drops the captured action; later sidebar navigation is a new intent.
      sidebarAsk.focus();
      fireEvent.click(sidebarAsk);
    }
    const composer = await screen.findByRole("textbox", { name: "向问知意提问" });
    expect(composer).toHaveValue("导航确认后继续的合成草稿");
    if (decision === "continue") expect(composer).toHaveFocus();
    else expect(sidebarAsk).toHaveFocus();
    // The expansion request must not replay when revisiting by the sidebar.
    const monitor = within(nav).getByRole("button", { name: "状态监控" });
    fireEvent.click(monitor);
    await screen.findByRole("group", { name: "任务状态筛选" });
    sidebarAsk.focus();
    fireEvent.click(sidebarAsk);
    await screen.findByRole("textbox", { name: "向问知意提问" });
    expect(sidebarAsk).toHaveFocus();
  });

  it("opens the workspace when preference storage and onboarding reads are unavailable", async () => {
    const original = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/user-state/onboarding")) return new Response(JSON.stringify({ detail: "服务暂不可用" }), { status: 503 });
      return original(input, init);
    }));
    const read = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new DOMException("Storage disabled", "SecurityError"); });
    try {
      render(<App />);
      expect(await screen.findByRole("button", { name: /服务已就绪/ })).toBeInTheDocument();
      expect(screen.queryByRole("dialog", { name: "首次使用引导" })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "文件提取" }));
      expect(await screen.findByRole("heading", { name: "提取出的数据" })).toBeInTheDocument();
    } finally { read.mockRestore(); }
  });

  it("keeps the task query when visiting settings and returning", async () => {
    render(<App />);
    const filters = screen.getByRole("group", { name: "任务状态筛选" });
    fireEvent.click(within(filters).getByRole("button", { name: /^失败/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "搜索任务文件名" }), { target: { value: "发票" } });
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("group=failed") && String(url).includes("search="))).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "设置" }));
    await waitFor(() => expect(screen.queryByRole("group", { name: "任务状态筛选" })).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "状态监控" }));
    expect(await screen.findByRole("textbox", { name: "搜索任务文件名" })).toHaveValue("发票");
    expect(within(screen.getByRole("group", { name: "任务状态筛选" })).getByRole("button", { name: /^失败/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("returns from same-page model setup to the chosen guide step and explicitly restarts a new guide", async () => {
    render(<App />);
    await screen.findByRole("button", { name: "服务已就绪" });
    fireEvent.click(screen.getByRole("button", { name: "设置" }));
    fireEvent.click(await screen.findByRole("button", { name: "打开引导" }));
    let guide = screen.getByRole("dialog", { name: "首次使用引导" });
    fireEvent.click(within(guide).getByRole("button", { name: "下一步" }));
    fireEvent.click(within(guide).getByRole("button", { name: /云端 AI 服务/ }));
    fireEvent.click(within(guide).getByRole("button", { name: "下一步" }));
    expect(within(guide).getByRole("heading", { name: "使用云端大模型" })).toBeInTheDocument();
    let proceed: (() => void) | undefined;
    const guard = (event: Event) => { event.preventDefault(); proceed = (event as CustomEvent<{ proceed: () => void }>).detail.proceed; };
    window.addEventListener("zhiyi:before-navigate", guard);
    try {
      fireEvent.click(within(guide).getByRole("button", { name: "前往连接设置" }));
      expect(proceed).toBeTypeOf("function");
      expect(guide).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "返回引导" })).not.toBeInTheDocument();
    } finally { window.removeEventListener("zhiyi:before-navigate", guard); }
    act(() => proceed!());
    expect(screen.queryByRole("dialog", { name: "首次使用引导" })).not.toBeInTheDocument();
    const back = await screen.findByRole("button", { name: "返回引导" });
    await waitFor(() => expect(document.getElementById("settings-ai")?.querySelector(".settings-card-head")).toHaveAttribute("aria-expanded", "true"));
    expect(vi.mocked(fetch).mock.calls.filter(([url, init]) => String(url).endsWith("/user-state/onboarding") && init?.method === "PUT")).toHaveLength(0);
    fireEvent.click(back);
    guide = screen.getByRole("dialog", { name: "首次使用引导" });
    expect(within(guide).getByRole("heading", { name: "使用云端大模型" })).toBeInTheDocument();
    fireEvent.click(within(guide).getByRole("button", { name: "跳过引导" }));
    fireEvent.click(screen.getByRole("button", { name: "打开引导" }));
    expect(within(screen.getByRole("dialog", { name: "首次使用引导" })).getByRole("group", { name: "认识知意" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "返回引导" })).not.toBeInTheDocument();
  });

  it("does not claim the global queue is idle before its first read completes", async () => {
    const original = globalThis.fetch;
    let finish!: () => void;
    const waiting = new Promise<void>((resolve) => { finish = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (new URL(String(input), "http://localhost").pathname.endsWith("/tasks")) await waiting;
      return original(input, init);
    }));
    render(<App />);
    expect(within(screen.getByRole("region", { name: "当前任务状态" })).getByText("正在读取任务…")).toBeInTheDocument();
    expect(screen.queryByText("当前空闲")).not.toBeInTheDocument();
    await act(async () => { finish(); });
    expect(await screen.findByText("当前空闲")).toBeInTheDocument();
  });

  it("replaces stale global idle status after a failed read and recovers with an explicit retry", async () => {
    const original = globalThis.fetch;
    let failed = false;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), "http://localhost").pathname;
      if (failed && (path.endsWith("/tasks") || path.endsWith("/system/status"))) {
        return new Response(JSON.stringify({ detail: "状态读取暂不可用" }), { status: 503 });
      }
      return original(input, init);
    }));
    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(false);
    try {
      render(<App />);
      expect(await screen.findByText("当前空闲")).toBeInTheDocument();
      failed = true;
      act(() => {
        document.dispatchEvent(new Event("visibilitychange"));
      });
      expect(await screen.findByText("任务状态未确认")).toBeInTheDocument();
      expect(screen.queryByText("当前空闲")).not.toBeInTheDocument();
      expect(await screen.findByRole("button", { name: /连接暂不可用/ })).toBeInTheDocument();
      failed = false;
      fireEvent.click(screen.getByRole("button", { name: "重试任务状态" }));
      expect(await screen.findByText("当前空闲")).toBeInTheDocument();
      act(() => { document.dispatchEvent(new Event("visibilitychange")); });
      expect(await screen.findByRole("button", { name: /服务已就绪/ })).toBeInTheDocument();
    } finally { hidden.mockRestore(); }
  });

  it("links an empty warehouse to a read-only example without creating data", async () => {
    const writes: string[] = [];
    const original = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method && init.method !== "GET") writes.push(String(input));
      if (new URL(String(input), "http://localhost").pathname.endsWith("/tasks/summary")) {
        return new Response(JSON.stringify({ total: 0, completed: 0, needs_review: 0, failed: 0 }), {
          headers: { "Content-Type": "application/json" },
        });
      }
      return original(input, init);
    }));
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "数据仓库" }));
    expect(await screen.findByRole("heading", { name: "示例送货单" })).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "卡片" })).toBeEnabled();
    expect(writes).toHaveLength(0);
  });

  it("refreshes worker recovery without a reload and pauses checks while hidden", async () => {
    const originalFetch = globalThis.fetch;
    let workerOnline = false;
    let checks = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/system/status")) {
        checks += 1;
        return { ok: true, json: async () => ({
          api: { connected: true, message: "API 已连接" },
          worker: { connected: workerOnline, message: workerOnline ? "任务消费者在线" : "任务消费者未启动" },
          model: { connected: true, message: "模型可用" },
          model_provider: "openai_compatible", configured_model: "test-model",
        }) };
      }
      return originalFetch(input, init);
    }));
    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(false);
    vi.useFakeTimers();
    let view: ReturnType<typeof render> | undefined;
    try {
      await act(async () => { view = render(<App />); });
      expect(screen.getByRole("button", { name: /文件处理未启动/ })).toBeInTheDocument();
      workerOnline = true;
      await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
      expect(screen.getByRole("button", { name: /服务已就绪/ })).toBeInTheDocument();
      const beforeHidden = checks;
      hidden.mockReturnValue(true);
      workerOnline = false;
      await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
      expect(checks).toBe(beforeHidden);
      hidden.mockReturnValue(false);
      await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
      expect(screen.getByRole("button", { name: /文件处理未启动/ })).toBeInTheDocument();
    } finally {
      view?.unmount();
      hidden.mockRestore();
      vi.useRealTimers();
    }
  });

  it("renders dashboard, settings and connections pages", async () => {
    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: "仪表盘" }));

    // Unknown model usage remains unknown; it must not become zero seconds or accuracy.
    expect(
      await screen.findByRole("button", { name: "仪表盘统计说明" }, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(await screen.findByText("文件任务")).toBeInTheDocument();
    expect(await screen.findByText("平均调用耗时")).toBeInTheDocument();
    expect(screen.getByText("平均调用耗时").parentElement).toHaveTextContent("—秒");
    expect(screen.queryByText("平均提取耗时")).not.toBeInTheDocument();

    // 设置页已接入：配置卡可见
    fireEvent.click(screen.getByRole("button", { name: "设置" }));
    expect(
      await screen.findByRole("heading", { name: /AI 服务配置/ }),
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
      await screen.findByRole("heading", { name: "API 端点" }),
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
    fireEvent.click(screen.getByRole("button", { name: "文件提取" }));
    await screen.findByRole("heading", { name: "提取出的数据" });
    const fileInput = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    const file = new File(["x"], "发票_001.pdf", { type: "application/pdf" });

    fireEvent.change(fileInput, { target: { files: [file] } });

    // 上传请求发出前：全局任务条立即显示该文件、状态"处理中"并开始计时
    expect(await screen.findByText("发票_001.pdf")).toBeInTheDocument();
    expect(screen.getAllByText("处理中").length).toBeGreaterThan(0);
    expect(screen.getByText("00:00")).toBeInTheDocument();

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
    await waitFor(() => expect(screen.getByRole("button", { name: "仪表盘统计说明" })).toBeInTheDocument());

    const dashboardWrap = container.querySelector(".app-page-wrap") as HTMLDivElement;
    expect(dashboardWrap).toBe(pageWrap);
    Object.defineProperty(dashboardWrap, "scrollTop", { value: 160, writable: true });
    fireEvent.click(screen.getByRole("button", { name: "状态监控" }));

    await waitFor(() => {
      const restored = container.querySelector(".app-page-wrap") as HTMLDivElement;
      expect(restored.scrollTop).toBe(420);
    });
  });

  it("restores task and history list viewports after navigation and fresh reads", async () => {
    const task = (filename: string) => ({
      id: filename, filename, status: "completed",
      candidate_templates: [], updated_at: "2026-11-02T00:00:00Z",
    });
    const original = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const { pathname, searchParams } = new URL(String(input), "http://localhost");
      if (pathname.endsWith("/tasks/summary")) return Response.json({ total: 20, completed: 20 });
      if (pathname.endsWith("/tasks")) {
        const name = searchParams.get("offset") === "10" && searchParams.get("search") === "滚动"
          ? "滚动验收第二页.txt" : "滚动验收.txt";
        return Response.json([task(name)]);
      }
      return original(input, init);
    }));
    render(<App />);
    const taskList = () => screen.getByRole("region", { name: "任务列表" });
    const historyList = () => screen.getByRole("region", { name: "文件历史表格，可滚动" });
    const navigate = (name: string) =>
      fireEvent.click(within(screen.getByRole("navigation", { name: "工作区" })).getByRole("button", { name }));

    await within(taskList()).findByText("滚动验收.txt");
    taskList().scrollTop = 180;
    fireEvent.scroll(taskList());
    navigate("文件历史");
    await screen.findByRole("region", { name: "文件历史表格，可滚动" });
    fireEvent.change(screen.getByRole("textbox", { name: "按文件名搜索" }), { target: { value: "滚动" } });
    await waitFor(() => expect(screen.getByTitle("下一页")).toBeEnabled());
    fireEvent.click(screen.getByTitle("下一页"));
    await within(historyList()).findByText("滚动验收第二页.txt");
    historyList().scrollTop = 120;
    historyList().scrollLeft = 85;
    fireEvent.scroll(historyList());
    navigate("状态监控");
    await within(taskList()).findByText("滚动验收.txt");
    expect(taskList().scrollTop).toBe(180);
    navigate("文件历史");
    expect(await screen.findByRole("textbox", { name: "按文件名搜索" })).toHaveValue("滚动");
    await screen.findByText("滚动验收第二页.txt");
    expect(historyList().scrollTop).toBe(120);
    expect(historyList().scrollLeft).toBe(85);
  });

  it("opens pending history from the dashboard with a fresh list scope", async () => {
    const original = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const { pathname, searchParams } = new URL(String(input), "http://localhost");
      const filtered = searchParams.get("search") === "没有匹配";
      if (pathname.endsWith("/tasks/summary")) {
        return Response.json({ total: filtered ? 0 : 1, completed: 0, needs_review: filtered ? 0 : 1 });
      }
      if (pathname.endsWith("/tasks")) {
        return Response.json(!filtered && searchParams.get("status") === "needs_review" ? [{
          id: "pending-review", filename: "待复核.txt", status: "needs_review", candidate_templates: [],
          updated_at: "2026-11-02T00:00:00Z",
        }] : []);
      }
      return original(input, init);
    }));
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "文件历史" }));
    const search = await screen.findByRole("textbox", { name: "按文件名搜索" });
    fireEvent.change(search, { target: { value: "没有匹配" } });
    fireEvent.click(screen.getByRole("button", { name: "仪表盘" }));
    fireEvent.click(await screen.findByRole("button", { name: /1 份文件待复核/ }));
    expect(await screen.findByRole("textbox", { name: "按文件名搜索" })).toHaveValue("");
    expect(await screen.findByRole("button", { name: "待复核.txt" })).toBeInTheDocument();
  });

  it("cancels interrupted page animation and keeps the last requested page", async () => {
    const motions: { element: HTMLElement; cancel: ReturnType<typeof vi.fn> }[] = [];
    const animate = vi.fn(function (this: HTMLElement) {
      const cancel = vi.fn();
      motions.push({ element: this, cancel });
      return { cancel };
    });
    const originalAnimate = HTMLElement.prototype.animate;
    HTMLElement.prototype.animate = animate as unknown as typeof HTMLElement.prototype.animate;
    try {
      const { container, unmount } = render(<App />);
      const wrapper = container.querySelector(".app-page-wrap");
      fireEvent.click(screen.getByRole("button", {name: "文件提取"}));
      fireEvent.click(screen.getByRole("button", {name: "设置"}));
      fireEvent.click(screen.getByRole("button", {name: "文件提取"}));
      expect(screen.getByRole("heading", {name: "提取出的数据"})).toBeInTheDocument();
      expect(container.querySelector(".app-page-wrap")).toBe(wrapper);
      const pageMotions = motions.filter(motion => motion.element === wrapper);
      expect(pageMotions).toHaveLength(3);
      for (const motion of pageMotions.slice(0, -1)) expect(motion.cancel).toHaveBeenCalledTimes(1);
      expect(pageMotions.at(-1)!.cancel).not.toHaveBeenCalled();
      unmount();
      for (const motion of motions) expect(motion.cancel).toHaveBeenCalledTimes(1);
    } finally { HTMLElement.prototype.animate = originalAnimate; }
  });
});
