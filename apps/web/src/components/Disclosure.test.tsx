import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { getTaskDiagnostics } from "../api";
import { Disclosure } from "./Disclosure";
import { TaskFailureDetails } from "./TaskFailureDetails";

vi.mock("../api", () => ({ getTaskDiagnostics: vi.fn() }));
beforeEach(() => vi.clearAllMocks());

it("keeps the disclosure trigger, controlled region and entered draft through collapse/reopen", () => {
  const opened = vi.fn();
  render(<Disclosure title="读取说明" onOpen={opened}><label>保留的草稿<input /></label></Disclosure>);
  const trigger = screen.getByRole("button", { name: "读取说明" });
  const region = document.getElementById(trigger.getAttribute("aria-controls")!)!;
  expect(trigger).toHaveAttribute("aria-expanded", "false");
  expect(region).toHaveAttribute("inert");
  trigger.focus();
  fireEvent.click(trigger);
  expect(trigger).toHaveAttribute("aria-expanded", "true");
  expect(region).not.toHaveAttribute("inert");
  fireEvent.change(screen.getByRole("textbox", { name: "保留的草稿" }), { target: { value: "尚未提交的输入" } });
  fireEvent.click(trigger);
  expect(region).toHaveAttribute("inert");
  fireEvent.click(trigger);
  expect(screen.getByRole("textbox", { name: "保留的草稿" })).toHaveValue("尚未提交的输入");
  expect(screen.getByRole("button", { name: "读取说明" })).toBe(trigger);
  expect(trigger).toHaveFocus();
  expect(opened).toHaveBeenCalledTimes(2);
});

it("retains native summary activation and the lazy diagnostic read without adding accessible icon text", async () => {
  vi.mocked(getTaskDiagnostics).mockResolvedValue({ code: null, detail: "合成诊断内容", attempt: 1 });
  render(<TaskFailureDetails taskId="fixture-task" />);
  const summary = screen.getByText("诊断详情").closest("summary")!;
  const details = summary.parentElement as HTMLDetailsElement;
  expect(details.tagName).toBe("DETAILS");
  expect(details.open).toBe(false);
  expect(getTaskDiagnostics).not.toHaveBeenCalled();
  fireEvent.click(summary);
  expect(details.open).toBe(true);
  expect(await screen.findByText("合成诊断内容")).toBeInTheDocument();
  expect(getTaskDiagnostics).toHaveBeenCalledExactlyOnceWith("fixture-task");
  expect(summary).toHaveTextContent(/^诊断详情$/);
  fireEvent.click(summary);
  await waitFor(() => expect(details.open).toBe(false));
});
