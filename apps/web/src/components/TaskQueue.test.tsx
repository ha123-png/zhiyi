import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Task } from "../types";
import { TaskQueue } from "./TaskQueue";


const failedTask: Task = {
  id: "failed-task",
  filename: "kept.png",
  content_type: "image/png",
  size_bytes: 1,
  sha256: "digest",
  template_mode: "invoice",
  template_id: "builtin-invoice",
  template_version: 1,
  candidate_templates: [],
  status: "failed",
  duplicate_of_task_id: null,
  created_at: "2026-07-30T00:00:00Z",
  updated_at: "2026-07-30T00:00:00Z",
};


describe("TaskQueue", () => {
  it("offers retry for a failed task without asking for another upload", () => {
    const onRetry = vi.fn();
    render(
      <TaskQueue
        loading={false}
        onCancel={vi.fn()}
        onRetry={onRetry}
        onSelect={vi.fn()}
        selectedId=""
        tasks={[failedTask]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "重试 kept.png" }));

    expect(onRetry).toHaveBeenCalledWith("failed-task");
  });

  it("keeps each status card collapsible", () => {
    render(
      <TaskQueue
        loading={false}
        onCancel={vi.fn()}
        onRetry={vi.fn()}
        onSelect={vi.fn()}
        selectedId=""
        tasks={[]}
      />,
    );

    const processingHeader = screen.getByRole("button", { name: /活动任务/ });
    expect(processingHeader).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(processingHeader);

    expect(processingHeader).toHaveAttribute("aria-expanded", "false");
  });
});
