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
afterEach(() => { vi.restoreAllMocks(); vi.clearAllMocks(); vi.useRealTimers(); });

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

it.each([
  { storageName: "localStorage", scheduled: false, incomplete: false },
  { storageName: "sessionStorage", scheduled: false, incomplete: false },
  { storageName: "localStorage", scheduled: true, incomplete: false },
  { storageName: "sessionStorage", scheduled: true, incomplete: false },
  { storageName: "localStorage", scheduled: false, incomplete: true },
  { storageName: "sessionStorage", scheduled: true, incomplete: true },
] as const)("separates service success from denied $storageName (scheduled=$scheduled, previously incomplete=$incomplete) and retries browser cleanup without deleting again", async ({ storageName, scheduled, incomplete }) => {
  const done = vi.fn();
  const before = { ...status, incomplete };
  vi.mocked(getClearDataStatus).mockResolvedValue(before);
  vi.mocked(clearAllLocalData).mockResolvedValue(scheduled ? { scheduled: true } : { scheduled: false, state: "succeeded" });
  render(<ClearLocalData onCleared={done} />);
  const submit = await openConfirmation();
  if (scheduled) {
    vi.mocked(getClearDataStatus).mockResolvedValueOnce(before).mockResolvedValue({
      ...status, result: { state: "succeeded", completed_at: "fresh" },
    });
    vi.useFakeTimers();
  }
  const denied = vi.spyOn(window, storageName, "get").mockImplementation(() => {
    throw new DOMException("Synthetic storage refusal", "SecurityError");
  });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "清除全部本地数据" } });
  await act(async () => { fireEvent.click(submit); });
  if (scheduled) {
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    vi.useRealTimers();
  }
  const retry = await screen.findByRole("button", { name: "重试清除浏览器偏好" });
  expect(screen.getByRole("alert")).toHaveTextContent("知意中的数据已清除");
  expect(screen.getByRole("alert")).toHaveTextContent("浏览器偏好");
  expect(screen.queryByText(/上次清除未完成/)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "重试清除" })).not.toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(clearAllLocalData).toHaveBeenCalledOnce();
  expect(done).not.toHaveBeenCalled();
  denied.mockRestore();
  fireEvent.click(retry);
  await waitFor(() => expect(done).toHaveBeenCalledOnce());
  expect(clearAllLocalData).toHaveBeenCalledOnce();
});
