import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { TaskFailureDetails } from "./TaskFailureDetails";
import { getTaskDiagnostics } from "../api";

vi.mock("../api", () => ({ getTaskDiagnostics: vi.fn() }));

it("loads diagnostic on demand and copies the complete returned text", async () => {
  const detail = "服务错误：" + "说明".repeat(500) + "关键原因在末尾";
  vi.mocked(getTaskDiagnostics).mockResolvedValue({ detail, attempt: 1, code: "model_error" });
  const copy = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: copy } });
  render(<TaskFailureDetails taskId="task" />);
  expect(getTaskDiagnostics).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("诊断详情"));
  await screen.findByText(detail);
  fireEvent.click(screen.getByRole("button", { name: "复制诊断" }));
  await waitFor(() => expect(copy).toHaveBeenCalledWith(detail));
});
