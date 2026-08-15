import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}
