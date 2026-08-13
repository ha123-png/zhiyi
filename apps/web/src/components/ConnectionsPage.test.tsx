import { render, screen } from "@testing-library/react";
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
