import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { TaskQueuePage } from "./TaskQueuePage";

it("does not carry a former task diagnostic into the same log index after the bounded log trims its head", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/tasks/old/diagnostics")) return Response.json({ detail: "旧文件专属诊断", code: null, attempt: 1 });
    if (url.endsWith("/tasks/new/diagnostics")) return Response.json({ detail: "新文件专属诊断", code: null, attempt: 1 });
    if (url.endsWith("/tasks/summary")) return Response.json({ total: 0, completed: 0, needs_review: 0, failed: 0 });
    if (url.endsWith("/queue/status")) return Response.json({ paused: false });
    return Response.json([]);
  }));
  const view = render(<TaskQueuePage logs={[{ time: "10:00:00", message: "旧文件失败", taskId: "old" }]} />);
  const initialDetails = screen.getByText("诊断详情").closest("details")!;
  fireEvent.click(screen.getByText("诊断详情"));
  await screen.findByText("旧文件专属诊断");
  view.rerender(<TaskQueuePage logs={[{ time: "10:00:00", message: "旧文件再次提示", taskId: "old" }]} />);
  expect(screen.getByText("诊断详情").closest("details")).toBe(initialDetails);
  expect(initialDetails.open).toBe(true);
  expect(screen.getByText("旧文件专属诊断")).toBeInTheDocument();
  view.rerender(<TaskQueuePage logs={[{ time: "10:00:01", message: "新文件失败", taskId: "new" }]} />);
  expect(screen.getByText("新文件失败")).toBeInTheDocument();
  expect(screen.queryByText("旧文件专属诊断")).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("诊断详情"));
  await screen.findByText("新文件专属诊断");
});
