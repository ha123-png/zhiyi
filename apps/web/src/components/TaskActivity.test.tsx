import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GlobalTaskCard } from "./TaskActivity";
import type { Task } from "../types";

const processingTask: Task = {
  id: "active-task",
  filename: "发票_001.pdf",
  content_type: "application/pdf",
  size_bytes: 1,
  sha256: "digest",
  template_mode: "smart",
  template_id: "builtin-invoice",
  template_version: 1,
  candidate_templates: [],
  status: "processing",
  duplicate_of_task_id: null,
  created_at: "2026-07-30T00:00:00.000Z",
  updated_at: "2026-07-30T00:00:00.000Z",
};

describe("GlobalTaskCard", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("starts the timer from zero when the file appears and advances while processing", () => {
    // 上传后任务条第一时间可见即从 00:00 同步开始计时，不因创建/轮询延迟从 2 秒起算
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-30T00:00:05.000Z"));
    render(<GlobalTaskCard tasks={[processingTask]} />);

    expect(screen.getByText(/已用 00:00/)).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(1000);
    });

    expect(screen.getByText(/已用 00:01/)).toBeInTheDocument();
  });

  it("does not pretend waiting for a template is processing time", () => {
    render(
      <GlobalTaskCard
        tasks={[{ ...processingTask, status: "waiting_for_template" }]}
      />,
    );

    expect(screen.getByText(/此时不计处理耗时/)).toBeInTheDocument();
  });

  it("offers pause while processing, resume and delete while paused", () => {
    const onPause = vi.fn();
    const onResume = vi.fn();
    const onDelete = vi.fn();
    const { unmount } = render(
      <GlobalTaskCard
        onPause={onPause}
        onResume={onResume}
        onDelete={onDelete}
        tasks={[processingTask]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /暂停/ }));
    expect(onPause).toHaveBeenCalledWith("active-task");

    unmount();
    render(
      <GlobalTaskCard
        onPause={onPause}
        onResume={onResume}
        onDelete={onDelete}
        tasks={[{ ...processingTask, status: "paused" }]}
      />,
    );

    // 暂停任务提供"启动队列"（全部放回队列）与"删除"（含原文件）
    fireEvent.click(screen.getByRole("button", { name: /启动队列/ }));
    expect(onResume).toHaveBeenCalledWith("active-task");
    fireEvent.click(screen.getByRole("button", { name: /删除/ }));
    expect(onDelete).toHaveBeenCalledWith("active-task");
  });

  it("shows no queue controls while idle or after all tasks finish", () => {
    const { rerender } = render(
      <GlobalTaskCard onPause={vi.fn()} onResume={vi.fn()} tasks={[]} />,
    );
    expect(screen.getByText("当前空闲")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /暂停队列|启动队列/ })).not.toBeInTheDocument();

    rerender(
      <GlobalTaskCard
        onPause={vi.fn()}
        onResume={vi.fn()}
        tasks={[{ ...processingTask, status: "completed" }]}
      />,
    );
    expect(screen.getByText("当前空闲")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /暂停队列|启动队列/ })).not.toBeInTheDocument();
  });

  it("keeps showing the paused file even when others are queued", () => {
    // 暂停任务需要用户操作（恢复/删除），不能被排队中的其他任务挤掉显示，
    // 否则看起来像"暂停的任务自己又启动了"。
    render(
      <GlobalTaskCard
        onPause={vi.fn()}
        onResume={vi.fn()}
        onDelete={vi.fn()}
        tasks={[
          { ...processingTask, id: "queued-1", filename: "排队.png", status: "queued", created_at: "2026-07-30T00:00:01.000Z" },
          { ...processingTask, id: "paused-1", filename: "暂停.png", status: "paused" },
        ]}
      />,
    );

    expect(screen.getByText("暂停.png")).toBeInTheDocument();
    expect(screen.queryByText("排队.png")).not.toBeInTheDocument();
  });

  it("shows a queued task as enqueueing without running its own timer", () => {
    // 入队中的任务显示“入队中”且不计时；只有真正开始处理时才从 0 清零计时。
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-30T00:01:00.000Z"));
    render(
      <GlobalTaskCard
        tasks={[{ ...processingTask, status: "queued", created_at: "2026-07-30T00:00:00.000Z" }]}
      />,
    );

    expect(screen.getByText("入队中")).toBeInTheDocument();
    expect(screen.getByText("任务正在入队")).toBeInTheDocument();
    expect(screen.getByText("等待开始")).toBeInTheDocument();
    expect(screen.queryByText(/已用 01:00/)).not.toBeInTheDocument();
  });

  it("shows the oldest queued file as the next task", () => {
    render(
      <GlobalTaskCard
        tasks={[
          { ...processingTask, id: "newer", filename: "第三份.png", status: "queued", created_at: "2026-07-30T00:00:03.000Z" },
          { ...processingTask, id: "older", filename: "第一份.png", status: "queued", created_at: "2026-07-30T00:00:01.000Z" },
        ]}
      />,
    );

    expect(screen.getByText("第一份.png")).toBeInTheDocument();
    expect(screen.queryByText("第三份.png")).not.toBeInTheDocument();
  });

  it("shows the processing file instead of a newer queued one in a batch", () => {
    // 批量上传：最新上传的任务还在排队，任务条应显示正在处理的任务及其计时，
    // 从它出现在任务条的时刻从零起算，不叠加排队任务的等待时间。
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-30T00:00:05.000Z"));
    render(
      <GlobalTaskCard
        tasks={[
          { ...processingTask, id: "later-queued", filename: "发票_003.png", status: "queued", created_at: "2026-07-30T00:00:03.000Z" },
          processingTask,
        ]}
      />,
    );

    expect(screen.getByText("发票_001.pdf")).toBeInTheDocument();
    expect(screen.getByText(/已用 00:00/)).toBeInTheDocument();
    expect(screen.queryByText("发票_003.png")).not.toBeInTheDocument();
  });

  it("restarts the timer from zero after a paused task resumes", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-30T00:00:00.000Z"));
    const { rerender } = render(
      <GlobalTaskCard
        tasks={[
          { ...processingTask, status: "paused", updated_at: "2026-07-30T00:00:00.000Z" },
        ]}
      />,
    );
    // 暂停中：计时冻结在暂停时刻
    expect(screen.getByText(/已用 00:00/)).toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(screen.getByText(/已用 00:00/)).toBeInTheDocument();

    // 恢复后重新处理：从恢复可见时刻重新从零计时
    act(() => {
      vi.setSystemTime(new Date("2026-07-30T00:00:05.000Z"));
    });
    rerender(
      <GlobalTaskCard
        tasks={[{ ...processingTask, status: "processing" }]}
      />,
    );
    expect(screen.getByText(/已用 00:00/)).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(screen.getByText(/已用 00:01/)).toBeInTheDocument();
  });

  it("restarts the timer when a retry reuses the same task id", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-30T00:01:00.000Z"));
    const { rerender } = render(
      <GlobalTaskCard tasks={[{ ...processingTask, started_at: "2026-07-30T00:00:00.000Z" }]} />,
    );
    act(() => { vi.advanceTimersByTime(5000); });
    expect(screen.getByText(/已用 00:05/)).toBeInTheDocument();

    rerender(
      <GlobalTaskCard tasks={[{ ...processingTask, started_at: "2026-07-30T00:01:05.000Z" }]} />,
    );
    expect(screen.getByText(/已用 00:00/)).toBeInTheDocument();
  });

  it("treats naive server timestamps as UTC so a fresh task never shows 480 minutes", () => {
    // 后端返回的 created_at 无时区标记（SQLite 丢 Z）；计时锚点以前端可见时刻为准，
    // 不再解析 naive 时间戳，因此 UTC+8 环境绝不会多出 8 小时（480 分钟）的虚假耗时。
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-08T02:00:05.000Z"));
    render(
      <GlobalTaskCard
        tasks={[{ ...processingTask, id: "naive-ts", created_at: "2026-08-08T02:00:00" }]}
      />,
    );

    expect(screen.getByText(/已用 00:00/)).toBeInTheDocument();
    expect(screen.queryByText(/480/)).not.toBeInTheDocument();
  });

  it("does not let historical failures occupy the global status bar", () => {
    const failedTask: Task = {
      ...processingTask,
      id: "failed-task",
      status: "failed",
      failure_message: "无法连接本地模型服务",
    };
    render(<GlobalTaskCard tasks={[failedTask]} />);

    expect(screen.queryByText(/处理失败/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /重试/ })).not.toBeInTheDocument();
    expect(screen.getByText("当前空闲")).toBeInTheDocument();
  });

  it("offers pending recovery for a failed copy without calling extraction failed", () => {
    const open = vi.fn();
    const task = { ...processingTask, status: "completed", file_export: { status: "failed" } } as Task;
    const { rerender } = render(<GlobalTaskCard tasks={[task]} onOpenPending={open} />);
    expect(screen.getByText("有待处理事项")).toBeInTheDocument();
    expect(screen.queryByText("当前空闲")).not.toBeInTheDocument();
    expect(screen.queryByText("处理失败")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看事项" }));
    expect(open).toHaveBeenCalledOnce();
    rerender(<GlobalTaskCard tasks={[{ ...task, file_export: { ...task.file_export!, status: "skipped" } }]} onOpenPending={open} />);
    expect(screen.getByText("当前空闲")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "查看事项" })).not.toBeInTheDocument();
  });
});
