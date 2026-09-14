import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { actOnTaskExport } from "../api";
import type { Task } from "../types";
import { TaskExportDetails, TaskExportAction } from "./TaskExportDetails";

vi.mock("../api", () => ({ actOnTaskExport: vi.fn() }));
const task = { id: "copy-task", filename: "原名.png", status: "completed", file_export: {
  status: "failed", parent_path: "D:\\资料", destination: "D:\\资料\\数学", actual_path: null,
  confirmed_name: null, error_code: "name_conflict", error_message: "目标已有同名文件，不会覆盖。",
} } as Task;
beforeEach(() => vi.clearAllMocks());
afterEach(() => { delete window.pywebview; });

it("keeps recovery controls out of the overview and completes them in a focused dialog", async () => {
  vi.mocked(actOnTaskExport).mockResolvedValue({ ...task, file_export: { ...task.file_export!, status: "skipped" } });
  render(<TaskExportAction task={task} />);
  expect(screen.queryByLabelText("副本名称（保留扩展名）")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "处理" }));
  expect(screen.getByRole("dialog", { name: "处理原件副本" })).toBeVisible();
  expect(screen.getByLabelText("副本名称（保留扩展名）")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "跳过此次副本导出" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "处理原件副本" })).not.toBeInTheDocument());
});

it("reports a moved external copy without retrying export or changing extraction status", async () => {
  const open = vi.fn().mockRejectedValue(new Error("原副本位置已不可用，内部原件预览不受影响。"));
  window.pywebview = { api: { open_export_folder: open } as unknown as NonNullable<NonNullable<Window["pywebview"]>["api"]> };
  render(<TaskExportDetails task={{ ...task, file_export: { ...task.file_export!, status: "completed", actual_path: "D:\\资料\\copy.png", error_message: null } }} expanded />);
  fireEvent.click(screen.getByRole("button", { name: "打开副本所在文件夹" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("内部原件预览不受影响");
  expect(open).toHaveBeenCalledExactlyOnceWith(task.id);
  expect(actOnTaskExport).not.toHaveBeenCalled();
  expect(screen.getByText("原件副本 · 副本已导出")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "重试副本导出" })).not.toBeInTheDocument();
});

it("changes name and path for the copy while preserving the uploaded name", async () => {
  const updated = { ...task, file_export: { ...task.file_export!, status: "completed" as const, actual_path: "D:\\新位置\\数学\\新名.png", confirmed_name: "新名.png" } };
  vi.mocked(actOnTaskExport).mockResolvedValue(updated);
  render(<TaskExportDetails task={task} expanded />);
  expect(screen.getByText(/目标已有同名文件/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("副本名称（保留扩展名）"), { target: { value: "新名.png" } });
  fireEvent.change(screen.getByLabelText("知意服务所在电脑的目标目录"), { target: { value: "D:\\新位置" } });
  fireEvent.click(screen.getByRole("button", { name: "重试副本导出" }));
  await screen.findByText("原件副本 · 副本已导出");
  expect(actOnTaskExport).toHaveBeenCalledExactlyOnceWith("copy-task", { action: "retry", filename: "新名.png", parent_path: "D:\\新位置", acknowledge_uncertain: false });
  expect(screen.getByText("原名.png")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "重试副本导出" })).not.toBeInTheDocument();
});

it("requires explicit acknowledgement for uncertain publication but allows skipping", async () => {
  vi.mocked(actOnTaskExport).mockResolvedValue({ ...task, file_export: { ...task.file_export!, status: "skipped" } });
  render(<TaskExportDetails task={{ ...task, file_export: { ...task.file_export!, error_code: "publication_uncertain" } }} expanded />);
  expect(screen.getByRole("button", { name: "重试副本导出" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "跳过此次副本导出" }));
  await waitFor(() => expect(actOnTaskExport).toHaveBeenCalledExactlyOnceWith(task.id, { action: "skip" }));
  expect(await screen.findByText("原件副本 · 已跳过副本导出")).toBeInTheDocument();
});

it("preserves actionable controls after an API failure", async () => {
  vi.mocked(actOnTaskExport).mockRejectedValue(new Error("正在导出文件，请稍后重试"));
  render(<TaskExportDetails task={task} expanded />);
  fireEvent.click(screen.getByRole("button", { name: "重试副本导出" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("请稍后重试");
  expect(screen.getByRole("button", { name: "重试副本导出" })).toBeEnabled();
});
