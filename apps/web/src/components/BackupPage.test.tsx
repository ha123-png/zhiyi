import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as axe from "axe-core";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { BackupPage } from "./BackupPage";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const backups = [
  {
    name: "20260810T010000000000Z.dpbak",
    size_bytes: 2048,
    created_at: "2026-08-10T01:00:00",
  },
];

const backupStatus = {
  backup_dir: "C:\\data\\backups",
  count: 1,
  retention: 10,
  free_bytes: 1024 * 1024 * 1024,
  last_success_at: "2026-08-10T01:00:00",
  database_size_bytes: 512,
  uploads_size_bytes: 1024,
};

describe("BackupPage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (input: string | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/backups") && !init?.method) return response(backups);
        if (url.endsWith("/backups/status")) return response(backupStatus);
        if (url.endsWith("/backups") && init?.method === "POST") {
          return response(backups[0], 201);
        }
        if (url.endsWith("/backups/20260810T010000000000Z.dpbak/restore") && init?.method === "POST") {
          return response({ rollback_dir: "C:\\data\\backups\\before-restore-x", restart_required: true });
        }
        if (url.endsWith("/backups/20260810T010000000000Z.dpbak") && init?.method === "DELETE") {
          return new Response(null, { status: 204 });
        }
        throw new Error(`Unexpected request: ${url} ${init?.method ?? ""}`);
      }),
    );
  });

  it("shows backup status and list", async () => {
    render(<BackupPage />);
    expect(await screen.findByText("1 / 10")).toBeInTheDocument();
    expect(screen.getByText("最近成功备份")).toBeInTheDocument();
    expect(screen.getByText("C:\\data\\backups")).toBeInTheDocument();
  });

  it("has no serious or critical automated accessibility violations", async () => {
    const { container } = render(<BackupPage />);
    await screen.findByText("1 / 10");
    const result = await axe.run(container, {
      // jsdom 没有 Canvas 颜色计算；真实颜色对比留给最终浏览器扫描，其他规则在 CI 执行。
      rules: { "color-contrast": { enabled: false } },
    });
    expect(
      result.violations.filter(
        (violation) => violation.impact === "serious" || violation.impact === "critical",
      ),
    ).toEqual([]);
  });

  it("creates a backup and notifies", async () => {
    render(<BackupPage />);
    const createButton = await screen.findByRole("button", { name: /立即备份/ });
    fireEvent.click(createButton);
    await waitFor(() => {
      const fetchMock = vi.mocked(fetch);
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) => String(url).endsWith("/backups") && init?.method === "POST",
        ),
      ).toBe(true);
    });
    expect(await screen.findByText("备份已创建。")).toBeInTheDocument();
  });

  it("restores from a backup with confirmation", async () => {
    render(<BackupPage />);
    const restoreButton = await screen.findByRole("button", { name: /^恢复$/ });
    fireEvent.click(restoreButton);
    expect(await screen.findByText("恢复备份")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认恢复" }));
    await waitFor(() => {
      const fetchMock = vi.mocked(fetch);
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) =>
            String(url).endsWith("/restore") && init?.method === "POST",
        ),
      ).toBe(true);
    });
    expect(
      await screen.findByText(/已恢复到备份时的状态/),
    ).toBeInTheDocument();
  });

  it("deletes a backup with confirmation", async () => {
    render(<BackupPage />);
    const deleteButton = await screen.findByRole("button", { name: /删除/ });
    fireEvent.click(deleteButton);
    expect(await screen.findByText("删除备份")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => {
      const fetchMock = vi.mocked(fetch);
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) => String(url).endsWith(".dpbak") && init?.method === "DELETE",
        ),
      ).toBe(true);
    });
    expect(await screen.findByText("备份已删除。")).toBeInTheDocument();
  });
});
