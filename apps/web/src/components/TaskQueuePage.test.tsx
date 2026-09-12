import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TaskQueuePage } from "./TaskQueuePage";

const BASE_TASK = {
  content_type: "image/png",
  size_bytes: 100,
  sha256: "digest",
  template_mode: "smart",
  template_id: null,
  template_version: null,
  candidate_templates: [],
  duplicate_of_task_id: null,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

describe("TaskQueuePage", () => {
  it("keeps the two status columns inside the same grid", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => [],
      }),
    );

    const { container } = render(<TaskQueuePage />);

    expect(await screen.findByText("暂无活动任务")).toBeInTheDocument();
    const grid = container.querySelector(".task-queue-grid");
    expect(grid).not.toBeNull();
    expect(Array.from(grid?.children ?? [])).toHaveLength(2);
    expect(Array.from(grid?.children ?? []).every((child) => child.classList.contains("task-col")))
      .toBe(true);
    expect(grid?.querySelectorAll(".task-queue-card")).toHaveLength(4);
  });

  it("opens a completed task in its review and fact table", async () => {
    const today = new Date().toISOString();
    const task = {
      id: "task-1",
      filename: "invoice.png",
      content_type: "image/png",
      size_bytes: 100,
      sha256: "digest",
      template_mode: "smart",
      template_id: "builtin-invoice",
      template_version: 1,
      candidate_templates: [],
      status: "completed",
      duplicate_of_task_id: null,
      created_at: today,
      updated_at: today,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => [task],
      }),
    );
    const onOpenTask = vi.fn();
    const onOpenData = vi.fn();

    render(<TaskQueuePage onOpenData={onOpenData} onOpenTask={onOpenTask} />);

    expect(await screen.findByText("invoice.png")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "对照" }));
    fireEvent.click(screen.getByRole("button", { name: "数据" }));

    expect(onOpenTask).toHaveBeenCalledWith(task);
    expect(onOpenData).toHaveBeenCalledWith(task);
  });

  it("shows paused tasks and logs but keeps queue controls out of status monitoring", async () => {
    const pausedTask = { ...BASE_TASK, id: "task-paused", filename: "paused.png", status: "paused" };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/templates")) {
          return Promise.resolve({ ok: true, json: async () => [] });
        }
        return Promise.resolve({ ok: true, json: async () => [pausedTask] });
      }),
    );

    render(<TaskQueuePage />);

    expect(await screen.findByText("paused.png")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "暂停队列" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "启动队列" })).not.toBeInTheDocument();
    expect(screen.getByTitle("删除此任务（防止上传错文件，直接删除不确认）")).toBeInTheDocument();
  });

  it("deletes a failed task directly without confirmation", async () => {
    const failedTask = { ...BASE_TASK, id: "task-failed", filename: "failed.png", status: "failed" };
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/templates")) {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      if (url.endsWith("/tasks/task-failed") && init?.method === "DELETE") {
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ kept_rows: 3 }) });
      }
      return Promise.resolve({ ok: true, json: async () => [failedTask] });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<TaskQueuePage />);

    expect(await screen.findByText("failed.png")).toBeInTheDocument();
    // 失败任务没有产生数据：直接点删除，不弹确认
    fireEvent.click(screen.getByTitle("删除任务（失败任务没有产生数据，直接删除不确认）"));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url, init]) =>
          String(url).endsWith("/tasks/task-failed") && init?.method === "DELETE"),
      ).toBe(true);
    });
    // 失败任务没有数据：直接删除，提示已删除
    expect(await screen.findByText("任务已删除。")).toBeInTheDocument();
  });

  it("batch retry submits only failed tasks", async () => {
    const failedTask = { ...BASE_TASK, id: "task-f1", filename: "f1.png", status: "failed" };
    const cancelledTask = { ...BASE_TASK, id: "task-c1", filename: "c1.png", status: "cancelled" };
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/templates")) {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      if (url.endsWith("/tasks/batch-retry") && init?.method === "POST") {
        return Promise.resolve({ ok: true, json: async () => ({ retried: ["task-f1"], skipped: 0 }) });
      }
      return Promise.resolve({ ok: true, json: async () => [failedTask, cancelledTask] });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<TaskQueuePage />);

    expect(await screen.findByText("f1.png")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "全部重试" }));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url, init]) =>
          String(url).endsWith("/tasks/batch-retry") && init?.method === "POST"),
      ).toBe(true);
    });
    expect(await screen.findByText(/已重新提交 1 个任务/)).toBeInTheDocument();
  });

  it("batch deletes waiting-for-template tasks with a single confirmation", async () => {
    const matchTask = {
      ...BASE_TASK,
      id: "task-match",
      filename: "m.png",
      status: "waiting_for_template",
      candidate_templates: [{ id: "tpl-1", name: "发票" }],
    };
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/templates")) {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      if (url.endsWith("/tasks/batch-delete") && init?.method === "POST") {
        return Promise.resolve({ ok: true, json: async () => ({ deleted: 1, kept_rows: 0 }) });
      }
      return Promise.resolve({ ok: true, json: async () => [matchTask] });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<TaskQueuePage />);

    expect(await screen.findByText("m.png")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "删除本页待选任务" }));

    // 只弹一次确认，无需输入文件名，确认即可删除
    const confirmButton = await screen.findByRole("button", { name: "确认删除" });
    expect(confirmButton).toBeEnabled();
    fireEvent.click(confirmButton);

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url, init]) =>
          String(url).endsWith("/tasks/batch-delete") && init?.method === "POST"),
      ).toBe(true);
    });
    expect(await screen.findByText(/已删除 1 个任务/)).toBeInTheDocument();
  });

  it("pages pending actions using backend totals instead of the loaded row count", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/tasks/summary")) return Promise.resolve({ ok: true, json: async () => ({ waiting_for_action: 51, pending_exports: 0 }) });
      if (url.includes("status=waiting_for_template")) return Promise.resolve({ ok: true, json: async () => [{ ...BASE_TASK, id: url.includes("offset=50") ? "last" : "first", filename: url.includes("offset=50") ? "最后一页.png" : "第一页.png", status: "waiting_for_template", candidate_templates: [] }] });
      return Promise.resolve({ ok: true, json: async () => [] });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<TaskQueuePage />);
    await screen.findByText("第一页.png");
    expect(screen.getByText(/共 51 份/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "下一页待选" }));
    await screen.findByText("最后一页.png");
    expect(screen.queryByText("第一页.png")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下一页待选" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "上一页待选" })).toBeEnabled();
  });

  it("keeps old waiting tasks and offers every active template", async () => {
    const waitingTask = {
      ...BASE_TASK,
      id: "task-old-waiting",
      filename: "old-waiting.png",
      status: "waiting_for_template",
      candidate_templates: [{ id: "tpl-recommended", name: "推荐模板" }],
    };
    const templateBase = {
      version: 1,
      is_system: false,
      is_active: true,
      builtin_key: null,
      source_template_id: null,
      description: "",
      extra_instructions: "",
      fields: [],
      validation_rules: [],
      deterministic_rules: [],
      output_mapping: {},
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/templates")) {
        return Promise.resolve({
          ok: true,
          json: async () => [
            { ...templateBase, id: "tpl-recommended", name: "推荐模板" },
            { ...templateBase, id: "tpl-other", name: "其他有效模板" },
            { ...templateBase, id: "tpl-archived", name: "已归档模板", is_active: false },
          ],
        });
      }
      if (url.includes("active_only=true") || url.includes("status=waiting_for_template")) {
        return Promise.resolve({ ok: true, json: async () => [waitingTask] });
      }
      return Promise.resolve({ ok: true, json: async () => [] });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<TaskQueuePage />);

    expect(await screen.findByText("old-waiting.png")).toBeInTheDocument();
    const select = screen.getByRole("combobox", { name: "选择模板" });
    expect(select).toHaveTextContent("推荐模板（推荐）");
    expect(select).toHaveTextContent("其他有效模板");
    expect(select).not.toHaveTextContent("已归档模板");
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).includes("active_only=true")),
    ).toBe(true);
  });

  it("deletes a paused task directly without confirmation", async () => {
    const pausedTask = { ...BASE_TASK, id: "task-paused", filename: "p.png", status: "paused" };
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/templates")) {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      if (url.endsWith("/tasks/task-paused") && init?.method === "DELETE") {
        return Promise.resolve({ ok: true, json: async () => ({ kept_rows: 0 }) });
      }
      return Promise.resolve({ ok: true, json: async () => [pausedTask] });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<TaskQueuePage />);

    expect(await screen.findByText("p.png")).toBeInTheDocument();
    fireEvent.click(screen.getByTitle("删除此任务（防止上传错文件，直接删除不确认）"));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url, init]) =>
          String(url).endsWith("/tasks/task-paused") && init?.method === "DELETE"),
      ).toBe(true);
    });
  });
});
