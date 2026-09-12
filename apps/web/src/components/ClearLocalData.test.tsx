import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { clearAllLocalData, getClearDataStatus } from "../api";
import { ClearLocalData } from "./ClearLocalData";

vi.mock("../api", () => ({ clearAllLocalData: vi.fn(), getClearDataStatus: vi.fn() }));
const status = { data_directory: "C:\\知意", original_directory: "C:\\知意\\uploads", incomplete: false, result: null };
beforeEach(() => {
  localStorage.clear();
  vi.mocked(getClearDataStatus).mockResolvedValue(status);
});
afterEach(() => { vi.clearAllMocks(); vi.useRealTimers(); });

async function openConfirmation() {
  const button = screen.getByRole("button", { name: /清除全部|重试清除/ });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  return screen.getByRole("button", { name: "永久清除本地数据" });
}

it("requires exact confirmation, explains ownership and resets only known browser keys after success", async () => {
  const done = vi.fn();
  localStorage.setItem("theme", "dark");
  localStorage.setItem("last-extraction-record-v1", "private result");
  localStorage.setItem("unrelated-app", "keep");
  vi.mocked(clearAllLocalData).mockResolvedValue({ scheduled: false, state: "succeeded" });
  render(<ClearLocalData onCleared={done} />);
  const submit = await openConfirmation();
  expect(submit).toBeDisabled();
  expect(screen.getByRole("dialog")).toHaveTextContent("C:\\知意\\uploads");
  expect(screen.getByRole("dialog")).toHaveTextContent("保留：外部导出的副本");
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "清除历史" } });
  expect(submit).toBeDisabled();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "清除全部本地数据" } });
  fireEvent.click(submit);
  await waitFor(() => expect(done).toHaveBeenCalledOnce());
  expect(clearAllLocalData).toHaveBeenCalledOnce();
  expect(localStorage.getItem("theme")).toBeNull();
  expect(localStorage.getItem("last-extraction-record-v1")).toBeNull();
  expect(localStorage.getItem("unrelated-app")).toBe("keep");
});

it("keeps browser state and offers retry when clearing is incomplete", async () => {
  const done = vi.fn();
  localStorage.setItem("theme", "dark");
  vi.mocked(clearAllLocalData).mockRejectedValue(new Error("文件被占用，清除未完成"));
  render(<ClearLocalData onCleared={done} />);
  const submit = await openConfirmation();
  vi.mocked(getClearDataStatus).mockResolvedValue({ ...status, incomplete: true });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "清除全部本地数据" } });
  fireEvent.click(submit);
  expect(await screen.findByRole("button", { name: "重试清除" })).toBeEnabled();
  expect(screen.getByText("文件被占用，清除未完成")).toBeInTheDocument();
  expect(done).not.toHaveBeenCalled();
  expect(localStorage.getItem("theme")).toBe("dark");
});

it("waits through restart and never treats an older success or incomplete state as completion", async () => {
  const done = vi.fn();
  const old = { state: "succeeded", completed_at: "old" };
  vi.mocked(getClearDataStatus).mockResolvedValue({ ...status, result: old });
  vi.mocked(clearAllLocalData).mockResolvedValue({ scheduled: true });
  render(<ClearLocalData onCleared={done} />);
  const submit = await openConfirmation();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "清除全部本地数据" } });
  fireEvent.click(submit);
  await screen.findByRole("status");
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 2100)); });
  expect(done).not.toHaveBeenCalled();
  vi.mocked(getClearDataStatus).mockResolvedValue({ ...status, incomplete: true, result: { state: "succeeded", completed_at: "new" } });
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 2100)); });
  expect(done).not.toHaveBeenCalled();
  vi.mocked(getClearDataStatus).mockResolvedValue({ ...status, result: { state: "succeeded", completed_at: "new" } });
  await waitFor(() => expect(done).toHaveBeenCalledOnce(), { timeout: 3000 });
}, 10000);
