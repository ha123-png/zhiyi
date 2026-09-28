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

  it("shows single-task resume and cancellation controls for paused tasks", async () => {
    const pausedTask = { ...BASE_TASK, id: "task-paused", filename: "paused.png", status: "paused" };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/templates")) {
          return Promise.resolve({ ok: true, json: async () => [] });
        }
        if (url.includes("/queue/status")) {
          return Promise.resolve({ ok: true, json: async () => ({ paused: false }) });
        }
        return Promise.resolve({ ok: true, json: async () => [pausedTask] });
      }),
    );

    render(<TaskQueuePage />);

    expect(await screen.findByText("paused.png")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "暂停队列" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "继续 paused.png" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "取消 paused.png" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "删除任务 paused.png" })).toBeInTheDocument();
  });

  it("confirms failed-task deletion and states the real data scope", async () => {
    const failedTask = { ...BASE_TASK, id: "task-failed", filename: "failed.png", status: "failed" };
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/templates")) {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      if (url.includes("/queue/status")) {
        return Promise.resolve({ ok: true, json: async () => ({ paused: false }) });
      }
      if (url.endsWith("/tasks/task-failed") && init?.method === "DELETE") {
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ kept_rows: 3 }) });
      }
      return Promise.resolve({ ok: true, json: async () => [failedTask] });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<TaskQueuePage />);

    expect(await screen.findByText("failed.png")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "删除任务 failed.png" }));
    const dialog = await screen.findByRole("dialog", { name: "删除任务" });
    expect(dialog).toHaveTextContent("平台内部原件、当前数据和该文件的修订历史");
    expect(dialog).toHaveTextContent("独立导入/合并表不受影响");
    expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith("/tasks/task-failed") && init?.method === "DELETE")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "删除任务 failed.png" }));
    fireEvent.click(await screen.findByRole("button", { name: "确认删除" }));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url, init]) =>
          String(url).endsWith("/tasks/task-failed") && init?.method === "DELETE"),
      ).toBe(true);
    });
    expect(await screen.findByText("文件及关联数据已删除。")).toBeInTheDocument();
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
    fireEvent.click(screen.getByRole("button", { name: "重试本页" }));

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
    expect(await screen.findByText(/已删除 1 个文件及关联数据/)).toBeInTheDocument();
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

  it("confirms paused-task deletion before removing the task", async () => {
    const pausedTask = { ...BASE_TASK, id: "task-paused", filename: "p.png", status: "paused" };
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/templates")) {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      if (url.includes("/queue/status")) {
        return Promise.resolve({ ok: true, json: async () => ({ paused: false }) });
      }
      if (url.endsWith("/tasks/task-paused") && init?.method === "DELETE") {
        return Promise.resolve({ ok: true, json: async () => ({ kept_rows: 0 }) });
      }
      return Promise.resolve({ ok: true, json: async () => [pausedTask] });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<TaskQueuePage />);

    expect(await screen.findByText("p.png")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "删除任务 p.png" }));
    expect(await screen.findByRole("dialog", { name: "删除任务" })).toHaveTextContent("任务记录、平台内部原件、当前数据和该文件的修订历史");
    expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith("/tasks/task-paused") && init?.method === "DELETE")).toBe(false);
    fireEvent.click(await screen.findByRole("button", { name: "确认删除" }));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url, init]) =>
          String(url).endsWith("/tasks/task-paused") && init?.method === "DELETE"),
      ).toBe(true);
    });
  });

  it("pauses and resumes only the selected task when the global queue is running", async () => {
    let currentTask = { ...BASE_TASK, id: "task-control", filename: "control.png", status: "queued" };
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/templates") || url.includes("/tables")) return Promise.resolve({ ok: true, json: async () => [] });
      if (url.includes("/queue/status")) return Promise.resolve({ ok: true, json: async () => ({ paused: false }) });
      if (url.includes("/tasks/summary")) return Promise.resolve({ ok: true, json: async () => ({ failed: 0, cancelled: 0, needs_review: 0, waiting_for_action: 0, pending_exports: 0 }) });
      if (url.endsWith("/tasks/task-control/pause") && init?.method === "POST") {
        currentTask = { ...currentTask, status: "paused" };
        return Promise.resolve({ ok: true, json: async () => currentTask });
      }
      if (url.endsWith("/tasks/task-control/resume") && init?.method === "POST") {
        currentTask = { ...currentTask, status: "queued" };
        return Promise.resolve({ ok: true, json: async () => currentTask });
      }
      return Promise.resolve({ ok: true, json: async () => [currentTask] });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<TaskQueuePage />);

    expect(await screen.findByText("control.png")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "暂停 control.png" }));
    expect(await screen.findByRole("button", { name: "继续 control.png" })).toBeEnabled();
    expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith("/tasks/task-control/pause") && init?.method === "POST")).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "继续 control.png" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith("/tasks/task-control/resume") && init?.method === "POST")).toBe(true));
    expect(await screen.findByText(/已继续处理/)).toBeInTheDocument();
  });

  it("does not resume one task while the global queue is paused, but cancels without deleting its data", async () => {
    let currentTask = { ...BASE_TASK, id: "task-paused", filename: "paused.png", status: "paused" };
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/templates") || url.includes("/tables")) return Promise.resolve({ ok: true, json: async () => [] });
      if (url.includes("/queue/status")) return Promise.resolve({ ok: true, json: async () => ({ paused: true }) });
      if (url.includes("/tasks/summary")) return Promise.resolve({ ok: true, json: async () => ({ failed: 0, cancelled: 0, needs_review: 0, waiting_for_action: 0, pending_exports: 0 }) });
      if (url.endsWith("/tasks/task-paused/resume") && init?.method === "POST") return Promise.resolve({ ok: true, json: async () => currentTask });
      if (url.endsWith("/tasks/task-paused/cancel") && init?.method === "POST") {
        currentTask = { ...currentTask, status: "cancelled" };
        return Promise.resolve({ ok: true, json: async () => currentTask });
      }
      if (url.includes("status=failed%2Ccancelled")) return Promise.resolve({ ok: true, json: async () => currentTask.status === "cancelled" ? [currentTask] : [] });
      return Promise.resolve({ ok: true, json: async () => currentTask.status === "cancelled" ? [] : [currentTask] });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<TaskQueuePage />);

    expect(await screen.findByText("paused.png")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "继续 paused.png" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "继续 paused.png" }));
    expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith("/tasks/task-paused/resume") && init?.method === "POST")).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "取消 paused.png" }));
    expect(await screen.findByText(/任务记录、原件、已有数据和修订历史均保留/)).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith("/tasks/task-paused/cancel") && init?.method === "POST")).toBe(true);
    expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith("/tasks/task-paused") && init?.method === "DELETE")).toBe(false);
  });

  it("puts failure diagnostics and recovery actions on separate full-width rows", async () => {
    const failedTask = { ...BASE_TASK, id: "task-layout", filename: "layout.png", status: "failed", failure_message: "A long diagnostic explanation that should wrap within the card." };
    vi.stubGlobal("fetch", vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/templates") || url.includes("/tables")) return Promise.resolve({ ok: true, json: async () => [] });
      if (url.includes("/queue/status")) return Promise.resolve({ ok: true, json: async () => ({ paused: false }) });
      return Promise.resolve({ ok: true, json: async () => [failedTask] });
    }));
    const { container } = render(<TaskQueuePage />);

    await screen.findByText("layout.png");
    const failureRow = container.querySelector(".task-failure-row");
    const content = container.querySelector(".task-failure-content");
    const actions = container.querySelector(".task-failure-actions");
    expect(failureRow).not.toBeNull();
    expect(content?.parentElement).toBe(failureRow);
    expect(actions?.parentElement).toBe(failureRow);
    expect(failureRow?.children[0]).toBe(content);
    expect(failureRow?.children[1]).toBe(actions);
    expect(content).toHaveTextContent("A long diagnostic explanation");
  });

  it("disables retry immediately and submits only once while a retry is pending", async () => {
    const failedTask = { ...BASE_TASK, id: "task-retry", filename: "retry.png", status: "failed" };
    let releaseRetry: ((value: { ok: boolean; json: () => Promise<typeof failedTask> }) => void) | undefined;
    const retryPromise = new Promise<{ ok: boolean; json: () => Promise<typeof failedTask> }>((resolve) => { releaseRetry = resolve; });
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/templates") || url.includes("/tables")) return Promise.resolve({ ok: true, json: async () => [] });
      if (url.includes("/queue/status")) return Promise.resolve({ ok: true, json: async () => ({ paused: true }) });
      if (url.endsWith("/tasks/task-retry/retry")) return retryPromise;
      return Promise.resolve({ ok: true, json: async () => [failedTask] });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<TaskQueuePage />);

    await screen.findByText("retry.png");
    const retryButton = screen.getByRole("button", { name: "重试" });
    fireEvent.click(retryButton);
    expect(retryButton).toBeDisabled();
    fireEvent.click(retryButton);
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/tasks/task-retry/retry"))).toHaveLength(1);

    releaseRetry?.({ ok: true, json: async () => ({ ...failedTask, status: "queued" }) });
    await waitFor(() => expect(retryButton).toBeEnabled());
  });

  it("batch deletion confirms only the visible failed/cancelled page and scopes its API request", async () => {
    const failedTask = { ...BASE_TASK, id: "task-failed", filename: "failed.png", status: "failed" };
    const cancelledTask = { ...BASE_TASK, id: "task-cancelled", filename: "cancelled.png", status: "cancelled" };
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/templates") || url.includes("/tables")) return Promise.resolve({ ok: true, json: async () => [] });
      if (url.includes("/queue/status")) return Promise.resolve({ ok: true, json: async () => ({ paused: false }) });
      if (url.includes("/tasks/summary")) return Promise.resolve({ ok: true, json: async () => ({ failed: 1, cancelled: 1, needs_review: 0, waiting_for_action: 0, pending_exports: 0 }) });
      if (url.endsWith("/tasks/batch-delete") && init?.method === "POST") return Promise.resolve({ ok: true, json: async () => ({ deleted: 2, kept_rows: 1 }) });
      if (url.includes("status=failed%2Ccancelled")) return Promise.resolve({ ok: true, json: async () => [failedTask, cancelledTask] });
      return Promise.resolve({ ok: true, json: async () => [failedTask, cancelledTask] });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<TaskQueuePage />);

    await screen.findByText("failed.png");
    fireEvent.click(screen.getByRole("button", { name: "删除本页失败/已取消任务" }));
    const dialog = await screen.findByRole("dialog", { name: "批量删除失败或已取消任务" });
    expect(dialog).toHaveTextContent("当前页 2 个失败/已取消任务");
    expect(dialog).toHaveTextContent("当前数据和文件修订历史（如有）");
    expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith("/tasks/batch-delete") && init?.method === "POST")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith("/tasks/batch-delete") && init?.method === "POST")).toBe(true));
    const request = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith("/tasks/batch-delete") && init?.method === "POST");
    expect(JSON.parse(String(request?.[1]?.body)).task_ids).toEqual(["task-failed", "task-cancelled"]);
  });
});
