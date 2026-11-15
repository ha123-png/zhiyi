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

  it("uses the current server attempt across mounting, reload, delayed processing, retry and pause", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-30T00:00:05Z"));
    const existing = { ...processingTask, started_at: "2026-07-30T00:00:00Z" };
    const first = render(<GlobalTaskCard tasks={[existing]} />);
    expect(screen.getByText("00:05")).toBeInTheDocument();
    first.unmount();
    const view = render(<GlobalTaskCard tasks={[existing]} />);
    expect(screen.getByText("00:05")).toBeInTheDocument();
    view.rerender(<GlobalTaskCard tasks={[{ ...existing, status: "queued", started_at: null }]} />);
    expect(screen.getByText("等待开始")).toBeInTheDocument();
    act(() => vi.setSystemTime(new Date("2026-07-30T00:00:10Z")));
    view.rerender(<GlobalTaskCard tasks={[{ ...existing, started_at: "2026-07-30T00:00:08Z" }]} />);
    expect(screen.getByText("00:02")).toBeInTheDocument();
    view.rerender(<GlobalTaskCard tasks={[{ ...existing, started_at: "2026-07-30T00:00:09Z" }]} />);
    expect(screen.getByText("00:01")).toBeInTheDocument();
    view.rerender(<GlobalTaskCard tasks={[{ ...existing, status: "paused", started_at: "2026-07-30T00:00:09Z", updated_at: "2026-07-30T00:00:10Z" }]} />);
    act(() => vi.advanceTimersByTime(5000));
    expect(screen.getByText("00:01")).toBeInTheDocument();
  });

  it("uses first observation only when the server attempt start is absent or invalid", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-30T00:00:05Z"));
    render(<GlobalTaskCard tasks={[{ ...processingTask, started_at: "invalid" }]} />);
    expect(screen.getByText("00:00")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(3000));
    expect(screen.getByText("00:03")).toBeInTheDocument();
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument();
  });

  it("uses first observation when the file has no server start, and advances while processing", () => {
    // This task has no started_at; created_at includes queue time, so only the
    // first observed processing time is available as a fallback.
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-30T00:00:05.000Z"));
    render(<GlobalTaskCard tasks={[processingTask]} />);

    expect(screen.getByText("00:00")).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(1000);
    });

    expect(screen.getByText("00:01")).toBeInTheDocument();
  });

  it("does not pretend waiting for a template is processing time", () => {
    render(
      <GlobalTaskCard
        tasks={[{ ...processingTask, status: "waiting_for_template" }]}
      />,
    );

    expect(screen.getByText("1 个待处理事项")).toBeInTheDocument();
    expect(screen.queryByText("处理用时")).not.toBeInTheDocument();
  });

  it("separates whole-queue controls from single-file actions", () => {
    const pause = vi.fn();
    const resume = vi.fn();
    const open = vi.fn();
    const { rerender } = render(<GlobalTaskCard tasks={[processingTask]} onPauseQueue={pause} onResumeQueue={resume} onOpenTasks={open} queuePaused={false}/>);
    fireEvent.click(screen.getByRole("button", { name: "暂停队列" }));
    expect(pause).toHaveBeenCalledTimes(1);
    expect(pause).toHaveBeenCalledWith();
    rerender(<GlobalTaskCard tasks={[{...processingTask, status: "paused"}]} onPauseQueue={pause} onResumeQueue={resume} onOpenTasks={open} queuePaused={false}/>);
    expect(screen.queryByRole("button", { name: "启动队列" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "删除" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看任务" }));
    expect(open).toHaveBeenCalledTimes(1);
    rerender(<GlobalTaskCard tasks={[]} onResumeQueue={resume} queuePaused/>);
    fireEvent.click(screen.getByRole("button", { name: "启动队列" }));
    expect(resume).toHaveBeenCalledWith();
  });

  it("shows no queue controls while idle or after all tasks finish", () => {
    const { rerender } = render(
      <GlobalTaskCard onPauseQueue={vi.fn()} onResumeQueue={vi.fn()} tasks={[]} />,
    );
    expect(screen.getByText("当前空闲")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /暂停队列|启动队列/ })).not.toBeInTheDocument();

    rerender(
      <GlobalTaskCard
        onPauseQueue={vi.fn()}
        onResumeQueue={vi.fn()}
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
        onPauseQueue={vi.fn()}
        onResumeQueue={vi.fn()}
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
    expect(screen.queryByText("01:00")).not.toBeInTheDocument();
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
    expect(screen.getByText("00:00")).toBeInTheDocument();
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
    expect(screen.getByText("00:00")).toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(screen.getByText("00:00")).toBeInTheDocument();

    // 恢复后重新处理：从恢复可见时刻重新从零计时
    act(() => {
      vi.setSystemTime(new Date("2026-07-30T00:00:05.000Z"));
    });
    rerender(
      <GlobalTaskCard
        tasks={[{ ...processingTask, status: "processing" }]}
      />,
    );
    expect(screen.getByText("00:00")).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(screen.getByText("00:01")).toBeInTheDocument();
  });

  it("restarts the timer when a retry reuses the same task id", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-30T00:01:00.000Z"));
    const { rerender } = render(
      <GlobalTaskCard tasks={[{ ...processingTask, started_at: "2026-07-30T00:00:00.000Z" }]} />,
    );
    act(() => { vi.advanceTimersByTime(5000); });
    expect(screen.getByText("01:05")).toBeInTheDocument();

    rerender(
      <GlobalTaskCard tasks={[{ ...processingTask, started_at: "2026-07-30T00:01:05.000Z" }]} />,
    );
    expect(screen.getByText("00:00")).toBeInTheDocument();
  });

  it("treats naive server timestamps as UTC so a fresh task never shows 480 minutes", () => {
    // Without started_at the observation fallback excludes queue time;
    // a timezone-less created_at must never add eight hours to processing.
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-08T02:00:05.000Z"));
    render(
      <GlobalTaskCard
        tasks={[{ ...processingTask, id: "naive-ts", created_at: "2026-08-08T02:00:00" }]}
      />,
    );

    expect(screen.getByText("00:00")).toBeInTheDocument();
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

  it("keeps processing healthy and exposes failed files through a separate shortcut", () => {
    const open = vi.fn();
    render(<GlobalTaskCard tasks={[processingTask, { ...processingTask, id: "failed", status: "failed" }]} onOpenFailed={open} />);
    expect(screen.getByText("处理中")).toBeInTheDocument();
    expect(screen.getByText("识别中…")).toBeInTheDocument();
    expect(screen.getByText("处理用时")).toBeInTheDocument();
    expect(screen.getByText("00:00").closest("time")).toHaveAttribute("datetime", "PT0S");
    fireEvent.click(screen.getByRole("button", { name: "1 个文件失败" }));
    expect(open).toHaveBeenCalledOnce();
  });

  it("offers pending recovery for a failed copy without calling extraction failed", () => {
    const open = vi.fn();
    const task = { ...processingTask, status: "completed", file_export: { status: "failed" } } as Task;
    const { rerender } = render(<GlobalTaskCard tasks={[task]} onOpenPending={open} />);
    expect(screen.getByText("1 个待处理事项")).toBeInTheDocument();
    expect(screen.queryByText("当前空闲")).not.toBeInTheDocument();
    expect(screen.queryByText("处理失败")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "1 个待处理事项" }));
    expect(open).toHaveBeenCalledOnce();
    rerender(<GlobalTaskCard tasks={[{ ...task, file_export: { ...task.file_export!, status: "skipped" } }]} onOpenPending={open} />);
    expect(screen.getByText("当前空闲")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /待处理事项/ })).not.toBeInTheDocument();
  });
});
