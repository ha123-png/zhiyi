import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsPage } from "./SettingsPage";

vi.mock("./ExistingLocalModelSettings", () => ({ ExistingLocalModelSettings: () => null }));
vi.mock("./ModelProfilesSettings", () => ({ ModelProfilesSettings: ({ onActiveProfileChanged }: { onActiveProfileChanged?: () => void }) =>
  <button type="button" onClick={onActiveProfileChanged}>模拟激活新方案</button> }));
vi.mock("./SmartPoolSettings", () => ({ SmartPoolSettings: () => null }));
vi.mock("./ClearLocalData", () => ({ ClearLocalData: () => null }));

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const settingsFixture = {
  image_convert: true, office_convert: true, word_include_images: false, allow_limited_input: false,
  upload_limit_mb: 125, input_text_limit: 20000, input_page_limit: 10, input_row_limit: 500, input_docx_image_limit: 10,
};

beforeEach(() => {
  HTMLElement.prototype.scrollIntoView = vi.fn();
  localStorage.clear();
  sessionStorage.clear();
  delete document.documentElement.dataset.zhiyiThemePreference;
  document.documentElement.classList.remove("dark");
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
    const path = String(input);
    if (path.endsWith("/system/settings")) return jsonResponse(settingsFixture);
    if (path.endsWith("/models/status")) return jsonResponse({ connected: true, provider: "openai_compatible", configured_model: "synthetic", available_models: [] });
    if (path.endsWith("/system/status")) return jsonResponse({
      api: { connected: true, message: "程序就绪" }, worker: { connected: true, message: "处理器就绪" }, model: { connected: true, message: "模型就绪" },
      model_provider: "openai_compatible", configured_model: "synthetic",
    });
    return jsonResponse({});
  }));
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

async function confirmThemeReset() {
  fireEvent.click(screen.getByRole("button", { name: "重置" }));
  const dialog = screen.getByRole("dialog", { name: "重置界面主题" });
  fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "重置" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "确认重置" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "重置界面主题" })).not.toBeInTheDocument());
}

describe("SettingsPage audit regression", () => {
  it("audit: refreshes its own status after activation and ignores the older initial result", async () => {
    let activeProfileChanged = false;
    let finishOldModel!: (response: Response) => void;
    let finishOldSystem!: (response: Response) => void;
    const parentChanged = vi.fn();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      const path = String(input);
      if (path.endsWith("/system/settings")) return jsonResponse(settingsFixture);
      if (path.endsWith("/models/status")) return activeProfileChanged
        ? jsonResponse({ connected: true, provider: "openai_compatible", configured_model: "new", available_models: ["new"] })
        : new Promise<Response>(resolve => { finishOldModel = resolve; });
      if (path.endsWith("/system/status")) return activeProfileChanged
        ? jsonResponse({ api: { connected: true, message: "程序就绪" }, worker: { connected: true, message: "处理器就绪" },
          model: { connected: true, message: "新方案已经就绪" }, model_provider: "openai_compatible", configured_model: "new" })
        : new Promise<Response>(resolve => { finishOldSystem = resolve; });
      return jsonResponse({});
    }));
    render(<SettingsPage onModelStatusChanged={parentChanged} />);
    await waitFor(() => expect(finishOldSystem).toBeTypeOf("function"));
    activeProfileChanged = true;
    fireEvent.click(screen.getByRole("button", { name: "模拟激活新方案" }));
    expect(await screen.findByText("新方案已经就绪")).toBeInTheDocument();
    expect(parentChanged).toHaveBeenCalledTimes(1);
    await act(async () => {
      finishOldModel(jsonResponse({ connected: false, provider: "lm_studio", configured_model: "old", available_models: [] }));
      finishOldSystem(jsonResponse({ api: { connected: true, message: "程序就绪" }, worker: { connected: true, message: "处理器就绪" },
        model: { connected: false, message: "旧方案离线" }, model_provider: "lm_studio", configured_model: "old" }));
    });
    expect(screen.getByText("新方案已经就绪")).toBeInTheDocument();
    expect(screen.queryByText("旧方案离线")).not.toBeInTheDocument();
  });

  it("audit: reports a failed history cleanup inside the confirmation without changing unrelated settings", async () => {
    const previousFetch = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) => init?.method === "POST"
      ? jsonResponse({ detail: "合成清理失败，当前历史未改变。" }, 409)
      : previousFetch(input, init));
    render(<SettingsPage />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Office 文档自动转换" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "清理" }));
    const dialog = screen.getByRole("dialog", { name: "清理历史数据" });
    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "清除历史" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "确认清理" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("合成清理失败，当前历史未改变。");
    expect(within(dialog).getByRole("button", { name: "确认清理" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Office 文档自动转换" })).toHaveAttribute("aria-pressed", "true");
  });

  it("audit: clears the successful old profile snapshot when the newly activated profile cannot be read", async () => {
    const previousFetch = vi.mocked(fetch).getMockImplementation()!;
    let activeB = false;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const path = String(input);
      if (path.endsWith("/models/status") || path.endsWith("/system/status")) {
        if (activeB) return jsonResponse({ detail: "新方案 B 状态读取失败" }, 503);
        if (path.endsWith("/system/status")) return jsonResponse({
          api: { connected: true, message: "方案 A 程序已就绪" },
          worker: { connected: true, message: "方案 A 处理器已就绪" },
          model: { connected: true, message: "方案 A 模型已就绪" },
          model_provider: "openai_compatible", configured_model: "profile-a",
        });
      }
      return previousFetch(input, init);
    });
    render(<SettingsPage />);
    await screen.findByText("方案 A 模型已就绪");
    expect(screen.getAllByText("可用")).toHaveLength(3);
    activeB = true;
    fireEvent.click(screen.getByRole("button", { name: "模拟激活新方案" }));
    fireEvent.click(await screen.findByRole("button", { name: "运行状态读取失败" }));
    expect(screen.getByRole("dialog", { name: "运行状态读取失败" })).toHaveTextContent("新方案 B 状态读取失败");
    expect(screen.queryByText("方案 A 模型已就绪")).not.toBeInTheDocument();
    expect(screen.queryByText("方案 A 程序已就绪")).not.toBeInTheDocument();
    expect(screen.queryByText("方案 A 处理器已就绪")).not.toBeInTheDocument();
    expect(screen.queryByText("可用")).not.toBeInTheDocument();
    expect(screen.getAllByText("暂时无法读取")).toHaveLength(3);
  });

  it("opens settings when either browser storage area denies reading", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new DOMException("blocked", "SecurityError"); });
    render(<SettingsPage />);
    expect(screen.getByRole("heading", { name: "当前运行状态" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "跟随系统" })).toHaveAttribute("aria-pressed", "true");
    await waitFor(() => expect(screen.getByRole("button", { name: "Office 文档自动转换" })).toBeEnabled());
  });

  it("returns to an unfinished setup guide without performing a settings action", () => {
    const resume = vi.fn();
    render(<SettingsPage onResumeOnboarding={resume} />);
    fireEvent.click(screen.getByRole("button", { name: "返回引导" }));
    expect(resume).toHaveBeenCalledOnce();
    expect(screen.getByText("使用引导 · 连接服务")).toBeInTheDocument();
  });

  it("reopens the requested setup section on the same page without losing a settings draft", async () => {
    const view = render(<SettingsPage />);
    const limit = await screen.findByRole("spinbutton", { name: "单文件上传上限" });
    await waitFor(() => expect(limit).toBeEnabled());
    fireEvent.change(limit, { target: { value: "250" } });
    const ai = screen.getByRole("button", { name: "AI 服务配置" });
    fireEvent.click(ai);
    expect(ai).toHaveAttribute("aria-expanded", "false");
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new DOMException("blocked", "SecurityError"); });
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: true })));
    const requestCount = vi.mocked(fetch).mock.calls.length;
    view.rerender(<SettingsPage settingsFocusRequest={{ section: "ai", revision: 1 }} />);
    await waitFor(() => expect(ai).toHaveAttribute("aria-expanded", "true"));
    await waitFor(() => expect(ai).toHaveFocus());
    expect(HTMLElement.prototype.scrollIntoView).toHaveBeenLastCalledWith({ behavior: "instant", block: "start" });
    expect(limit).toHaveValue(250);
    expect(vi.mocked(fetch).mock.calls).toHaveLength(requestCount);
    fireEvent.click(ai);
    view.rerender(<SettingsPage settingsFocusRequest={{ section: "ai", revision: 2 }} />);
    await waitFor(() => expect(ai).toHaveAttribute("aria-expanded", "true"));
    expect(limit).toHaveValue(250);
  });

  it("applies a theme before a failed write and keeps that choice across settings navigation", async () => {
    localStorage.setItem("theme", "light");
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("blocked", "SecurityError"); });
    const first = render(<SettingsPage />);
    fireEvent.click(screen.getByRole("button", { name: "深色" }));
    expect(document.documentElement).toHaveClass("dark");
    expect(screen.getByRole("button", { name: "深色" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/主题已在本次打开中生效；浏览器未能记住/)).toHaveAttribute("role", "status");
    expect(localStorage.getItem("theme")).toBe("light");
    first.unmount();
    render(<SettingsPage />);
    expect(screen.getByRole("button", { name: "深色" })).toHaveAttribute("aria-pressed", "true");
    expect(document.documentElement).toHaveClass("dark");
  });

  it("clears the temporary-theme warning after the browser accepts the next choice", async () => {
    const blocked = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("full", "QuotaExceededError"); });
    render(<SettingsPage />);
    fireEvent.click(screen.getByRole("button", { name: "深色" }));
    expect(screen.getByText(/浏览器未能记住/)).toBeInTheDocument();
    blocked.mockRestore();
    fireEvent.click(screen.getByRole("button", { name: "浅色" }));
    expect(document.documentElement).not.toHaveClass("dark");
    expect(localStorage.getItem("theme")).toBe("light");
    expect(screen.queryByText(/浏览器未能记住/)).not.toBeInTheDocument();
  });

  it("resets the current theme without a reload when browser writes and removals both fail", async () => {
    localStorage.setItem("theme", "dark");
    document.documentElement.classList.add("dark");
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("blocked", "SecurityError"); });
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => { throw new DOMException("blocked", "SecurityError"); });
    const timer = vi.spyOn(window, "setTimeout");
    render(<SettingsPage />);
    await confirmThemeReset();
    expect(document.documentElement).not.toHaveClass("dark");
    expect(screen.getByRole("button", { name: "跟随系统" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("本次主题已恢复为跟随系统，但浏览器未能保存重置。").closest(".app-toast")).toHaveAttribute("role", "status");
    expect(localStorage.getItem("theme")).toBe("dark");
    expect(timer.mock.calls.some(([callback]) => String(callback).includes("location.reload"))).toBe(false);
  });

  it("stores the default theme when removing the old preference is denied but replacement works", async () => {
    localStorage.setItem("theme", "dark");
    document.documentElement.classList.add("dark");
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => { throw new DOMException("blocked", "SecurityError"); });
    render(<SettingsPage />);
    await confirmThemeReset();
    expect(localStorage.getItem("theme")).toBe("auto");
    expect(document.documentElement).not.toHaveClass("dark");
    expect(screen.getByText("主题已恢复为跟随系统。").closest(".app-toast")).toHaveAttribute("role", "status");
    expect(screen.queryByText(/浏览器未能保存重置/)).not.toBeInTheDocument();
  });

  it("ignores an unreadable or undeletable settings anchor without blocking the page", async () => {
    sessionStorage.setItem("zhiyi-settings-anchor", "appearance");
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => { throw new DOMException("blocked", "SecurityError"); });
    render(<SettingsPage />);
    expect(screen.getByRole("button", { name: "外观" })).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(screen.getByRole("button", { name: "深色" }));
    expect(document.documentElement).toHaveClass("dark");
  });

  it("shows failed settings as unknown and retries instead of presenting a fake upload limit", async () => {
    const fetchBefore = globalThis.fetch;
    let fail = true;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      if (fail && String(input).endsWith("/system/settings")) return jsonResponse({ detail: "合成的文件设置读取失败" }, 503);
      return fetchBefore(input, init);
    }));
    render(<SettingsPage />);
    const retry = await screen.findByRole("button", { name: "重试读取文件设置" });
    const uploadLimit = screen.getByRole("spinbutton", { name: /单文件上传上限/ });
    expect(uploadLimit).toBeDisabled();
    expect(uploadLimit).toHaveValue(null);
    expect(screen.getByRole("button", { name: "Office 文档自动转换" })).toBeDisabled();
    expect(screen.queryByText("正在读取文件设置…")).not.toBeInTheDocument();
    fail = false;
    fireEvent.click(retry);
    await waitFor(() => expect(screen.getByRole("spinbutton", { name: /单文件上传上限/ })).toHaveValue(125));
    expect(screen.getByRole("button", { name: "Office 文档自动转换" })).toBeEnabled();
    expect(screen.queryByText("合成的文件设置读取失败")).not.toBeInTheDocument();
  });

  it("preserves available status results and lets a failed status read be retried", async () => {
    const fetchBefore = globalThis.fetch;
    let fail = true;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      if (fail && String(input).endsWith("/system/status")) return jsonResponse({ detail: "合成的运行状态读取失败" }, 503);
      return fetchBefore(input, init);
    }));
    render(<SettingsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "运行状态读取失败" }));
    const retry = screen.getByRole("button", { name: "重新检查状态" });
    expect(screen.queryByText("正在检查…")).not.toBeInTheDocument();
    expect(screen.getAllByText("暂时无法读取").length).toBeGreaterThan(0);
    expect(screen.getAllByText("可用")).toHaveLength(1);
    fail = false;
    fireEvent.click(retry);
    expect(await screen.findByText("运行正常")).toBeInTheDocument();
    expect(screen.getByText("已启动")).toBeInTheDocument();
    expect(screen.queryByText("合成的运行状态读取失败")).not.toBeInTheDocument();
    expect(screen.getAllByText("可用")).toHaveLength(3);
  });

  it("clears a prior setting-save error after a successful retry", async () => {
    let serverSettings = {
      image_convert: true,
      office_convert: true,
      word_include_images: true,
      allow_limited_input: false,
      upload_limit_mb: 50,
      input_text_limit: 20000,
      input_page_limit: 10,
      input_row_limit: 500,
      input_docx_image_limit: 10,
    };
    let saveCount = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith("/system/settings") && init?.method === "PUT") {
        saveCount += 1;
        if (saveCount === 1) return jsonResponse({ detail: "合成 503 保存失败" }, 503);
        serverSettings = { ...serverSettings, ...JSON.parse(String(init.body)) };
        return jsonResponse(serverSettings);
      }
      if (path.endsWith("/system/settings")) return jsonResponse(serverSettings);
      if (path.endsWith("/models/status")) return jsonResponse({
        connected: true, provider: "openai_compatible", configured_model: "test-model", available_models: [],
      });
      if (path.endsWith("/system/status")) return jsonResponse({
        api: { connected: true, message: "就绪" },
        worker: { connected: true, message: "就绪" },
        model: { connected: true, message: "就绪" },
        model_provider: "openai_compatible", configured_model: "test-model",
      });
      return jsonResponse({});
    }));

    const firstRender = render(<SettingsPage />);
    const officeToggle = await screen.findByRole("button", { name: "Office 文档自动转换" });
    expect(officeToggle).toHaveClass("toggle", "on");

    fireEvent.click(officeToggle);
    expect(await screen.findByText("合成 503 保存失败")).toBeInTheDocument();
    await waitFor(() => expect(officeToggle).toHaveClass("toggle", "on"));

    fireEvent.click(officeToggle);
    await waitFor(() => expect(serverSettings.office_convert).toBe(false));
    expect(await screen.findByText(/已关闭Office 文档自动转换/)).toBeInTheDocument();
    expect(screen.queryByText("合成 503 保存失败")).not.toBeInTheDocument();
    expect(officeToggle).toHaveClass("toggle", "off");

    firstRender.unmount();
    render(<SettingsPage />);
    expect(await screen.findByRole("button", { name: "Office 文档自动转换" }))
      .toHaveClass("toggle", "off");
    expect(screen.queryByText("合成 503 保存失败")).not.toBeInTheDocument();
  });
});
