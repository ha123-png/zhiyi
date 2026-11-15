import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { assistantApi } from "./api";
import { ModelConnectionState } from "./ModelConnectionState";

const ask = vi.hoisted(() => ({ profileId: "local-one", profile: { name: "本地方案", version: 1 }, busy: false }));
vi.mock("./AssistantProvider", () => ({ useAssistant: () => ask }));
vi.mock("./api", () => ({ assistantApi: vi.fn() }));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.mocked(assistantApi).mockReset();
  ask.profileId = "local-one"; ask.profile.version = 1; ask.busy = false;
});
afterEach(() => vi.useRealTimers());

it.each(["late success", "late failure"])("keeps the latest poll when an earlier request has a %s", async ending => {
  const old = deferred<{ state: string }>();
  vi.mocked(assistantApi).mockReturnValueOnce(old.promise).mockResolvedValue({ state: "cancelled" });
  render(<ModelConnectionState />);
  await act(async () => { vi.advanceTimersByTime(8000); });
  expect(screen.getByText(/最近请求已停止/)).toBeInTheDocument();
  await act(async () => {
    if (ending === "late success") old.resolve({ state: "running" });
    else old.reject(new Error("旧读取断开"));
  });
  expect(screen.getByText(/最近请求已停止/)).toBeInTheDocument();
});

it("ignores a previous profile response and stops polling after unmount", async () => {
  const old = deferred<{ state: string }>();
  vi.mocked(assistantApi).mockReturnValueOnce(old.promise).mockResolvedValue({ state: "success" });
  const view = render(<ModelConnectionState />);
  ask.profileId = "local-two";
  await act(async () => { view.rerender(<ModelConnectionState />); });
  expect(screen.getByText(/最近调用成功/)).toBeInTheDocument();
  await act(async () => old.resolve({ state: "failed" }));
  expect(screen.getByText(/最近调用成功/)).toBeInTheDocument();
  view.unmount();
  const reads = vi.mocked(assistantApi).mock.calls.length;
  await act(async () => { vi.advanceTimersByTime(16000); });
  expect(assistantApi).toHaveBeenCalledTimes(reads);
});
