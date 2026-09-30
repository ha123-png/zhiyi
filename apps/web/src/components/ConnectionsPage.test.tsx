import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ConnectionsPage } from "./ConnectionsPage";

afterEach(() => {
  vi.unstubAllGlobals();
});

test("curl 示例使用当前 API 地址而不是过期硬编码端口", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "未配置" }), {
        status: 503,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<ConnectionsPage />);

  const example = screen.getByText(/查询任务列表/, { selector: "pre" });
  expect(example).toHaveTextContent(`${window.location.origin}/api/integration/v1/tasks`);
  expect(example).not.toHaveTextContent("127.0.0.1:8000");
});

test("MCP 写权限不依赖 HTTP 写密钥且提示显示在 MCP 权限卡片", async () => {
  const fetchMock = vi.fn().mockImplementation(async (input: string | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/integration/settings") && !init?.method) {
      return jsonResponse({
        read_token_set: false,
        write_token_set: false,
        task_read: false,
        template_read: false,
        result_read: false,
        data_read: false,
        task_control: false,
        write_enabled: false,
        file_access: false,
        file_roots: [],
      });
    }
    if (url.endsWith("/integration/settings/mcp-config")) {
      return jsonResponse({ runtime: "development", server_name: "zhiyi", server: {}, mcp_servers: { mcpServers: {} } });
    }
    if (url.endsWith("/integration/settings/permissions") && init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      expect(body.write_enabled).toBe(true);
      return jsonResponse({ saved: true });
    }
    return new Response(JSON.stringify({ detail: "未配置" }), {
      status: 503,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<ConnectionsPage />);
  await screen.findByText(/与 HTTP 写密钥无关/);
  fireEvent.click(await screen.findByRole("button", { name: "写事实" }));
  fireEvent.click(screen.getByRole("button", { name: "保存权限" }));

  const notice = await screen.findByText(/MCP 权限已保存/);
  const mcpCard = screen.getByRole("button", { name: /MCP 权限/ }).closest(".card")!;
  const keyCard = screen.getByRole("button", { name: /^读写密钥/ }).closest(".card")!;
  expect(within(mcpCard as HTMLElement).getByText(/MCP 权限已保存/)).toBe(notice);
  expect(within(keyCard as HTMLElement).queryByText(/MCP 权限已保存/)).not.toBeInTheDocument();
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    expect.stringContaining("/integration/settings/permissions"),
    expect.objectContaining({ method: "POST" }),
  ));
});

test("跨页导航先确认，取消保留权限草稿，放弃才继续导航", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
    const url = String(input);
    if (url.endsWith("/integration/settings")) return jsonResponse(integrationSettings());
    if (url.endsWith("/integration/settings/mcp-config")) return jsonResponse({ runtime: "development", mcp_servers: { mcpServers: {} } });
    return jsonResponse({ enabled: false, message: "未启用" });
  }));

  render(<ConnectionsPage />);
  const toggle = await screen.findByRole("button", { name: "读取模板" });
  fireEvent.click(toggle);
  expect(toggle).toHaveClass("on");

  const cancelledProceed = vi.fn();
  const cancelledNavigation = new CustomEvent("zhiyi:before-navigate", {
    cancelable: true,
    detail: { proceed: cancelledProceed },
  });
  act(() => window.dispatchEvent(cancelledNavigation));
  expect(cancelledNavigation.defaultPrevented).toBe(true);
  const dialog = screen.getByRole("dialog", { name: "修改尚未保存" });
  fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
  expect(cancelledProceed).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "读取模板" })).toHaveClass("on");

  const discardedProceed = vi.fn();
  const discardedNavigation = new CustomEvent("zhiyi:before-navigate", {
    cancelable: true,
    detail: { proceed: discardedProceed },
  });
  act(() => window.dispatchEvent(discardedNavigation));
  expect(discardedNavigation.defaultPrevented).toBe(true);
  fireEvent.click(within(screen.getByRole("dialog", { name: "修改尚未保存" })).getByRole("button", { name: "放弃修改并继续" }));
  expect(discardedProceed).toHaveBeenCalledOnce();
  expect(screen.queryByRole("dialog", { name: "修改尚未保存" })).not.toBeInTheDocument();
});

test("权限保存失败保留草稿，重试持久化后可以干净离开", async () => {
  let persisted = integrationSettings();
  let permissionPosts = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/integration/settings/permissions") && init?.method === "POST") {
      permissionPosts += 1;
      if (permissionPosts === 1) return new Response(JSON.stringify({ detail: "synthetic save failure" }), { status: 503, headers: { "Content-Type": "application/json" } });
      persisted = { ...persisted, ...JSON.parse(String(init.body)) };
      return jsonResponse({ saved: true });
    }
    if (url.endsWith("/integration/settings")) return jsonResponse(persisted);
    if (url.endsWith("/integration/settings/mcp-config")) return jsonResponse({ runtime: "development", mcp_servers: { mcpServers: {} } });
    return jsonResponse({ enabled: false, message: "未启用" });
  }));

  render(<ConnectionsPage />);
  const toggle = await screen.findByRole("button", { name: "读取模板" });
  fireEvent.click(toggle);
  fireEvent.click(screen.getByRole("button", { name: "保存权限" }));
  expect(await screen.findByText(/synthetic save failure/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "读取模板" })).toHaveClass("on");

  fireEvent.click(screen.getByRole("button", { name: "保存权限" }));
  expect(await screen.findByText(/MCP 权限已保存/)).toBeInTheDocument();
  await waitFor(() => expect(permissionPosts).toBe(2));
  await waitFor(() => expect(screen.getByRole("button", { name: "保存权限" })).toBeEnabled());
  expect(persisted.template_read).toBe(true);

  const navigation = new CustomEvent("zhiyi:before-navigate", { cancelable: true, detail: { proceed: vi.fn() } });
  act(() => window.dispatchEvent(navigation));
  expect(navigation.defaultPrevented).toBe(false);
});

test("生成HTTP密钥刷新状态时保留尚未保存的权限草稿", async () => {
  const fetchMock = vi.fn(async (input: string | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/integration/settings/keys") && init?.method === "POST") return jsonResponse({ kind: "read", token: "synthetic-only-secret" });
    if (url.endsWith("/integration/settings")) return jsonResponse(integrationSettings());
    if (url.endsWith("/integration/settings/mcp-config")) return jsonResponse({ runtime: "development", mcp_servers: { mcpServers: {} } });
    return jsonResponse({ enabled: false, message: "未启用" });
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<ConnectionsPage />);
  fireEvent.click(await screen.findByRole("button", { name: "读取模板" }));
  fireEvent.click(screen.getByRole("button", { name: "生成读密钥" }));
  await screen.findByRole("dialog", { name: "新密钥" });
  expect(screen.getByRole("button", { name: "读取模板" })).toHaveClass("on");
  fireEvent.click(screen.getByRole("button", { name: "关闭" }));
  expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/integration/settings"), undefined);
});

test("初始权限配置未知或读取失败时禁用密钥和权限操作，重读后以服务端基线解锁", async () => {
  const baseline = {
    ...integrationSettings(),
    read_token_set: true,
    write_token_set: true,
    task_read: true,
    data_read: true,
    data_scope: "selected" as const,
    table_ids: [],
  };
  let finishInitialSettings!: (response: Response) => void;
  let finishRetrySettings!: (response: Response) => void;
  let settingsReads = 0;
  let permissionPosts = 0;
  let keyPosts = 0;
  const fetchMock = vi.fn(async (input: string | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/integration/settings") && !init?.method) {
      settingsReads += 1;
      if (settingsReads === 1) return new Promise<Response>((resolve) => { finishInitialSettings = resolve; });
      if (settingsReads === 2) return new Promise<Response>((resolve) => { finishRetrySettings = resolve; });
      return jsonResponse(baseline);
    }
    if (url.endsWith("/integration/settings/mcp-config")) {
      return jsonResponse({ runtime: "development", server_name: "zhiyi", server: {}, mcp_servers: { mcpServers: {} } });
    }
    if (url.endsWith("/tables")) return jsonResponse([]);
    if (url.endsWith("/integration/settings/permissions") && init?.method === "POST") {
      permissionPosts += 1;
      return jsonResponse({ saved: true });
    }
    if (url.endsWith("/integration/settings/keys") && init?.method === "POST") {
      keyPosts += 1;
      return jsonResponse({ kind: "read", token: "synthetic-only-secret" });
    }
    return jsonResponse({ enabled: false, message: "未启用" });
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<ConnectionsPage />);
  await screen.findByText("正在读取 MCP 权限配置…");
  await waitFor(() => expect(finishInitialSettings).toBeTypeOf("function"));
  const keyLabels = ["生成读密钥", "生成写密钥", "撤销读", "撤销写"];
  for (const label of keyLabels) expect(screen.getByRole("button", { name: label })).toBeDisabled();
  expect(screen.getByRole("button", { name: "保存权限" })).toBeDisabled();

  await act(async () => {
    finishInitialSettings(new Response(JSON.stringify({ detail: "synthetic initial settings failure" }), {
      status: 503,
      headers: { "Content-Type": "application/json" },
    }));
  });
  const retry = await screen.findByRole("button", { name: "重新读取" });
  expect(screen.getAllByRole("button", { name: "重新读取" })).toHaveLength(1);
  for (const label of keyLabels) expect(screen.getByRole("button", { name: label })).toBeDisabled();
  expect(screen.getByRole("button", { name: "保存权限" })).toBeDisabled();

  fireEvent.click(retry);
  await waitFor(() => expect(settingsReads).toBe(2));
  const retrying = screen.getByRole("button", { name: "正在读取…" });
  expect(retrying).toBeDisabled();
  fireEvent.click(retrying);
  expect(settingsReads).toBe(2);
  await act(async () => { finishRetrySettings(jsonResponse(baseline)); });
  await waitFor(() => expect(screen.getByRole("button", { name: "读取任务" })).toHaveClass("on"));
  expect(screen.getByRole("button", { name: "读取模板" })).toHaveClass("off");
  expect(screen.getByRole("button", { name: "读取数据仓库" })).toHaveClass("on");
  expect(screen.getByLabelText("允许访问的数据表")).toHaveValue("selected");
  for (const label of keyLabels) expect(screen.getByRole("button", { name: label })).toBeEnabled();
  expect(screen.getByRole("button", { name: "保存权限" })).toBeEnabled();

  fireEvent.click(screen.getByRole("button", { name: "撤销读" }));
  const confirm = await screen.findByRole("dialog", { name: "撤销密钥" });
  fireEvent.click(within(confirm).getByRole("button", { name: "取消" }));
  expect(screen.queryByRole("dialog", { name: "撤销密钥" })).not.toBeInTheDocument();
  expect(settingsReads).toBe(2);
  expect(permissionPosts).toBe(0);
  expect(keyPosts).toBe(0);
});

function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function integrationSettings() {
  return {
    read_token_set: false,
    write_token_set: false,
    task_read: false,
    template_read: false,
    result_read: false,
    data_read: false,
    task_control: false,
    write_enabled: false,
    file_access: false,
    file_roots: [],
    data_scope: "all" as const,
    table_ids: [],
  };
}

test("a clipboard rejection shows a readable MCP error instead of claiming copied", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
    if (String(input).endsWith("/integration/settings/mcp-config")) return jsonResponse({runtime: "development", mcp_servers: {mcpServers: {}}});
    if (String(input).endsWith("/integration/settings")) return jsonResponse({file_roots: [], data_read: true, result_read: true});
    if (String(input).endsWith("/tables")) return jsonResponse([]);
    return jsonResponse({enabled: false, message: "未启用"});
  }));
  const clipboard = Object.getOwnPropertyDescriptor(navigator, "clipboard");
  Object.defineProperty(navigator, "clipboard", {configurable: true, value: {writeText: vi.fn().mockRejectedValue(new Error("denied"))}});
  try {
    render(<ConnectionsPage />);
    fireEvent.click(await screen.findByRole("button", {name: "复制配置"}));
    expect(await screen.findByRole("alert")).toHaveTextContent("复制失败，请手动选中上方 MCP 配置复制");
    expect(screen.queryByRole("button", {name: "已复制"})).not.toBeInTheDocument();
    expect(screen.getByText(/独立授权，不受下方所选数据表限制/)).toBeInTheDocument();
  } finally {
    if (clipboard) Object.defineProperty(navigator, "clipboard", clipboard);
    else Reflect.deleteProperty(navigator, "clipboard");
  }
});

test("HTTP revocation confirmation and success both promise immediate effect", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
    if (String(input).endsWith("/integration/settings")) return jsonResponse({file_roots: [], read_token_set: true});
    return jsonResponse({enabled: false, message: "未启用"});
  }));
  render(<ConnectionsPage />);
  await waitFor(() => expect(screen.getByRole("button", {name: "撤销读"})).toBeEnabled());
  fireEvent.click(screen.getByRole("button", {name: "撤销读"}));
  const confirm = screen.getByRole("dialog", {name: "撤销密钥"});
  expect(confirm).toHaveTextContent("HTTP API 请求立即失效，无需重启");
  fireEvent.click(within(confirm).getByRole("button", {name: "确认撤销"}));
  expect(await screen.findByText("读密钥已撤销，HTTP API 立即生效。")).toBeInTheDocument();
});

test("MCP config read failure ends loading and can be retried", async () => {
  let configReads = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
    const url = String(input);
    if (url.endsWith("/integration/settings/mcp-config")) {
      configReads += 1;
      if (configReads === 1) throw new TypeError("connection reset");
      return jsonResponse({ runtime: "development", mcp_servers: { mcpServers: { zhiyi: { command: "zhiyi" } } } });
    }
    if (url.endsWith("/integration/settings")) return jsonResponse({ file_roots: [], read_token_set: false, write_token_set: false });
    return jsonResponse({ enabled: false, message: "未启用" });
  }));

  render(<ConnectionsPage />);
  const configCard = screen.getByRole("button", { name: /可直接使用的 MCP 配置/ }).closest(".card") as HTMLElement;
  expect(await within(configCard).findByRole("alert")).toHaveTextContent(/读取.*配置|连接/);
  expect(within(configCard).queryByText(/正在生成当前环境的 MCP 配置/)).not.toBeInTheDocument();
  fireEvent.click(within(configCard).getByRole("button", { name: /重新读取配置/ }));
  expect(await within(configCard).findByText(/"command": "zhiyi"/)).toBeInTheDocument();
  expect(configReads).toBe(2);
});
