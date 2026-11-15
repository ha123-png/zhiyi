import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { BackupPage } from "./BackupPage";

const backup = { name: "20261030T010000Z.dpbak", size_bytes: 2048, created_at: "2026-10-30T01:00:00" };
const status = { backup_dir: "synthetic/backups", count: 1, retention: null, free_bytes: 1024,
  last_success_at: backup.created_at, database_size_bytes: 512, uploads_size_bytes: 1024,
  restore_state: null, restore_message: null };
const json = (body: unknown, code = 200) => new Response(JSON.stringify(body), {
  status: code, headers: { "Content-Type": "application/json" },
});

describe("backup read state", () => {
  it("shows a restored backup once and clears the success on refresh, including after remount", async () => {
    localStorage.clear();
    let completed = 42;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => json(String(input).endsWith("/status")
      ? { ...status, restore_state: "succeeded", restore_message: "备份恢复成功。", restore_completed_at: completed }
      : [backup])));
    const first = render(<BackupPage />);
    await screen.findByText("备份恢复成功。");
    fireEvent.click(screen.getByRole("button", { name: "重新读取备份" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "重新读取备份" })).toBeEnabled());
    expect(screen.queryByText("备份恢复成功。")).not.toBeInTheDocument();
    first.unmount();
    render(<BackupPage />);
    await screen.findByRole("button", { name: "恢复" });
    expect(screen.queryByText("备份恢复成功。")).not.toBeInTheDocument();
    completed = 43;
    fireEvent.click(screen.getByRole("button", { name: "重新读取备份" }));
    await screen.findByText("备份恢复成功。");
    fireEvent.click(screen.getByRole("button", { name: "关闭通知" }));
    expect(screen.queryByText("备份恢复成功。")).not.toBeInTheDocument();
    localStorage.clear();
  });

  it.each(["list", "status"])("does not call a failed initial %s read an empty backup set and retries in place", async (failedRead) => {
    let failed = true;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      const url = String(input);
      if (url.endsWith("/backups/status")) return failed && failedRead === "status"
        ? json({ detail: "合成备份读取失败" }, 503) : json(status);
      return failed && failedRead === "list" ? json({ detail: "合成备份读取失败" }, 503) : json([backup]);
    }));
    render(<BackupPage />);
    fireEvent.click(await screen.findByRole("button", { name: "备份事项" }));
    expect(screen.getByRole("dialog", { name: "备份事项" })).toHaveTextContent("合成备份读取失败");
    expect(screen.queryByText("尚未备份过")).not.toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
    expect(screen.queryByText(/还没有备份/)).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "备份事项" })).toHaveTextContent("暂时无法读取备份信息");
    fireEvent.click(screen.getByRole("button", { name: "关闭备份事项" }));
    expect(screen.getByRole("button", { name: "立即备份" })).toBeDisabled();
    failed = false;
    fireEvent.click(screen.getByRole("button", { name: "重新读取备份" }));
    expect(await screen.findByRole("button", { name: /^恢复$/ })).toBeEnabled();
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps pending initial reads unknown and prevents premature writes", async () => {
    let release!: (value: Response) => void;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => String(input).endsWith("/status")
      ? json(status) : new Promise<Response>(resolve => { release = resolve; })));
    render(<BackupPage />);
    expect(screen.queryByText("尚未备份过")).not.toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "立即备份" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "重新读取备份" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("正在读取备份");
    release(json([backup]));
    expect(await screen.findByRole("button", { name: /^恢复$/ })).toBeEnabled();
  });

  it("retains known backups after a refresh failure while disabling stale actions", async () => {
    let failed = false;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      if (failed) return json({ detail: "合成刷新失败" }, 503);
      return json(String(input).endsWith("/status") ? status : [backup]);
    }));
    render(<BackupPage />);
    await screen.findByRole("button", { name: /^恢复$/ });
    failed = true;
    fireEvent.click(screen.getByRole("button", { name: "重新读取备份" }));
    fireEvent.click(await screen.findByRole("button", { name: "备份事项" }));
    expect(screen.getByRole("dialog", { name: "备份事项" })).toHaveTextContent("保留上次读取的内容");
    fireEvent.click(screen.getByRole("button", { name: "关闭备份事项" }));
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^恢复$/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /^删除$/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "立即备份" })).toBeDisabled();
    failed = false;
    fireEvent.click(screen.getByRole("button", { name: "重新读取备份" }));
    await waitFor(() => expect(screen.getByRole("button", { name: /^恢复$/ })).toBeEnabled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps successful creation distinct from its failed readback and never retries the write", async () => {
    let readFailed = false;
    const fetchMock = vi.fn(async (input: string | URL, init?: RequestInit) => {
      if (init?.method === "POST") { readFailed = true; return json(backup, 201); }
      if (readFailed) return json({ detail: "创建后刷新失败" }, 503);
      return json(String(input).endsWith("/status") ? status : [backup]);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<BackupPage />);
    await screen.findByRole("button", { name: /^恢复$/ });
    fireEvent.click(screen.getByRole("button", { name: "立即备份" }));
    expect(await screen.findByText("备份已创建。")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "备份事项" }));
    expect(screen.getByRole("dialog", { name: "备份事项" })).toHaveTextContent("创建后刷新失败");
    fireEvent.click(screen.getByRole("button", { name: "关闭备份事项" }));
    expect(screen.queryByText("操作未完成")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "立即备份" })).toBeDisabled();
    readFailed = false;
    fireEvent.click(screen.getByRole("button", { name: "重新读取备份" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "立即备份" })).toBeEnabled());
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  });

  it("labels a failed write as an operation failure while preserving readable backups", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => init?.method === "POST"
      ? json({ detail: "合成创建失败" }, 503)
      : json(String(input).endsWith("/status") ? status : [backup])));
    render(<BackupPage />);
    await screen.findByRole("button", { name: /^恢复$/ });
    fireEvent.click(screen.getByRole("button", { name: "立即备份" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("合成创建失败");
    expect(screen.queryByText(/暂时无法读取备份信息/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^恢复$/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: "立即备份" })).toBeEnabled();
  });

  it("shows a confirmed empty set only after both reads succeed", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => json(String(input).endsWith("/status")
      ? { ...status, count: 0, last_success_at: null } : [])));
    render(<BackupPage />);
    expect(await screen.findByText(/还没有备份/)).toBeInTheDocument();
    expect(screen.getByText("尚未备份过")).toBeInTheDocument();
    expect(screen.getByText("0")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "立即备份" })).toBeEnabled();
  });
});
