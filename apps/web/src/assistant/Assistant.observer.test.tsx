// Actual controller and user-facing page; synthetic network fixtures follow Assistant.test.tsx.
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AssistantProvider } from "./AssistantProvider";
import { AssistantPage } from "./AssistantSurface";
import type { ThreadDetail } from "./types";

let events: string[];
let sources: Source[];
let queuedReads: ReturnType<typeof deferred<Response>>[];
let pendingStarted: number;
let saved: Record<"a" | "b", ThreadDetail>;
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
class Source {
  onmessage: ((event: { data: string; lastEventId?: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onopen: (() => void) | null = null;
  closed = false;
  constructor(public url: string) { sources.push(this); events.push(`source:${sources.length}:created`); }
  close() { this.closed = true; events.push(`source:${sources.indexOf(this) + 1}:closed`); }
  open() {
    if (this.closed) throw new Error("Test cannot emit open on a closed source");
    events.push(`source:${sources.indexOf(this) + 1}:open`); this.onopen?.();
  }
  message(data: Record<string, unknown>, lastEventId = "1") {
    if (this.closed) throw new Error("Test cannot emit a message on a closed source");
    this.onmessage?.({ data: JSON.stringify(data), lastEventId });
  }
  error() {
    if (this.closed) throw new Error("Test cannot emit error on a closed source");
    events.push(`source:${sources.indexOf(this) + 1}:error`); this.onerror?.();
  }
}
const local = { id: "local", name: "本地聊天", provider: "lm_studio", version: 1,
  base_url: "http://127.0.0.1:1234/v1", model_name: "synthetic", is_remote: false,
  is_archived: false, is_active: true };
function thread(id: "a" | "b"): ThreadDetail {
  return { id, title: `合成对话${id.toUpperCase()}`, profile_id: "local", archived: false,
    updated_at: "2026-10-31T00:00:00Z",
    messages: [{ id: `answer-${id}`, role: "assistant", parts: [{ type: "text", text: `合成${id.toUpperCase()}当前回答` }],
      context: {}, created_at: "2026-10-31T00:00:00Z" }],
    runs: [{ id: `run-${id}`, message_id: `answer-${id}`, status: "running", error: null,
      model: "synthetic", profile_id: "local", profile_version: 1 }], tools: [] };
}
function Shell() { return <AssistantProvider><AssistantPage /></AssistantProvider>; }
beforeEach(() => {
  localStorage.clear(); sessionStorage.clear();
  events = []; sources = []; queuedReads = []; pendingStarted = 0;
  saved = { a: thread("a"), b: thread("b") };
  vi.stubGlobal("EventSource", Source);
  HTMLElement.prototype.scrollTo = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    const path = url.pathname;
    const method = init?.method ?? "GET";
    if (path.endsWith("/models/profiles")) return Response.json([local]);
    if (path.endsWith("/assistant/settings")) return Response.json({ profile_id: "local" });
    if (path.endsWith("/assistant/threads") && method === "GET")
      return Response.json(url.searchParams.get("archived") === "true" ? [] : [thread("a"), thread("b")]);
    if (/\/assistant\/threads\/[ab]\/authorization$/.test(path) && method === "PUT")
      return Response.json({ authorization_revision: 1, invalidated_tools: 0, cancelled_runs: 0 });
    if (/\/assistant\/threads\/[ab]$/.test(path) && method === "GET") {
      const id = path.endsWith("/a") ? "a" : "b";
      events.push(`read:${id}:${url.searchParams.get("run_id") || "thread"}:start`);
      if (id === "a" && url.searchParams.get("run_id") === "run-a" && queuedReads.length) {
        const pending = queuedReads.shift()!; pendingStarted++;
        const response = await pending.promise;
        events.push(`read:a:run-a:delayed-response:${response.status}`);
        return response;
      }
      return Response.json(saved[id]);
    }
    throw new Error(`Unexpected synthetic fixture request: ${method} ${path}`);
  }));
});
afterEach(() => vi.restoreAllMocks());
async function openThread(id: "a" | "b", sourceCount: number) {
  fireEvent.click(await screen.findByRole("button", { name: new RegExp(`合成对话${id.toUpperCase()}.*2026`) }));
  await waitFor(() => expect(sources).toHaveLength(sourceCount));
  act(() => sources[sourceCount - 1].open());
  expect(screen.getByText("正在接收回答…")).toBeInTheDocument();
  expect(screen.getByText(`合成${id.toUpperCase()}当前回答`)).toBeInTheDocument();
}

it("does not replace the reopened same-thread stream status with an old pending reconnect read", async () => {
  render(<Shell />);
  await openThread("a", 1);
  const pending = deferred<Response>(); queuedReads.push(pending);
  act(() => sources[0].error());
  expect(pendingStarted).toBe(1);
  expect(screen.getByText("连接中断，正在核对已保存的回答…")).toBeInTheDocument();
  await openThread("a", 2); // This exact same history-row button is user reachable.
  expect(sources[0].closed).toBe(true); expect(sources[1].closed).toBe(false);
  const stale = thread("a"); stale.messages[0].parts = [{ type: "text", text: "旧连接快照不得覆盖当前回答" }];
  await act(async () => pending.resolve(Response.json(stale)));

  expect(screen.getByText("合成A当前回答")).toBeInTheDocument();
  expect(screen.queryByText("旧连接快照不得覆盖当前回答")).not.toBeInTheDocument();
  expect(screen.getByText("正在接收回答…")).toBeInTheDocument();
});

it("does not show the fifth old-stream reconnect failure in a different active conversation", async () => {
  render(<Shell />);
  await openThread("a", 1);
  for (let i = 0; i < 4; i++) {
    act(() => sources[0].error());
    await screen.findByText("连接短暂中断，正在自动重连…");
  }
  const pending = deferred<Response>(); queuedReads.push(pending);
  act(() => sources[0].error());
  expect(pendingStarted).toBe(1);
  await openThread("b", 2);
  expect(sources[0].closed).toBe(true); expect(sources[1].closed).toBe(false);
  await act(async () => pending.resolve(Response.json({ detail: "旧A第五次核对读取失败" }, { status: 503 })));

  expect(screen.getByText("合成B当前回答")).toBeInTheDocument();
  expect(screen.getByText("正在接收回答…")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});


it("keeps the current connection usable after a disconnect, including a reconnect before its read returns", async () => {
  render(<Shell />);
  await openThread("a", 1);
  const checked = thread("a");
  checked.messages[0].parts = [{ type: "text", text: "本连接核对后的回答" }];
  const currentRead = deferred<Response>(); queuedReads.push(currentRead);
  act(() => sources[0].error());
  await act(async () => currentRead.resolve(Response.json(checked)));
  expect(screen.getByText("本连接核对后的回答")).toBeInTheDocument();
  expect(screen.getByText("连接短暂中断，正在自动重连…")).toBeInTheDocument();
  expect(sources[0].closed).toBe(false);

  const beforeOpen = deferred<Response>(); queuedReads.push(beforeOpen);
  act(() => sources[0].error());
  act(() => sources[0].open());
  const stale = thread("a");
  stale.runs[0].error = "恢复之前的旧错误";
  await act(async () => beforeOpen.resolve(Response.json(stale)));
  expect(screen.getByText("正在接收回答…")).toBeInTheDocument();
  expect(screen.getByText("本连接核对后的回答")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();

  const beforeMessage = deferred<Response>(); queuedReads.push(beforeMessage);
  act(() => sources[0].error());
  act(() => sources[0].message({ type: "text.delta", part_index: 0,
    offset: Array.from("本连接核对后的回答").length, text: "，已恢复😀" }));
  await screen.findByText("本连接核对后的回答，已恢复😀");
  await act(async () => beforeMessage.resolve(Response.json(stale)));
  expect(screen.getByText("正在回答…")).toBeInTheDocument();
  expect(screen.getByText("本连接核对后的回答，已恢复😀")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it.each(["completed", "cancelled"])("flushes and reconciles %s without reviving a pending reconnect read", async status => {
  render(<Shell />);
  await openThread("a", 1);
  const oldRecovery = deferred<Response>(); queuedReads.push(oldRecovery);
  act(() => sources[0].error());
  const finalRead = deferred<Response>(); queuedReads.push(finalRead);
  const delta = { type: "text.delta", part_index: 0,
    offset: Array.from("合成A当前回答").length, text: "，完成前增量😀" };
  act(() => {
    sources[0].message(delta, "1");
    sources[0].message(delta, "1");
    sources[0].message({ type: "run.completed", status }, "2");
  });
  expect(sources[0].closed).toBe(true);
  expect(pendingStarted).toBe(2);
  expect(screen.getByText("合成A当前回答，完成前增量😀")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "停止回答" })).not.toBeInTheDocument();
  const final = thread("a"); final.runs[0].status = status; final.runs[0].stream_cursor = 2;
  final.messages[0].parts = [{ type: "text", text: "最终保存的合成回答" }];
  await act(async () => finalRead.resolve(Response.json(final)));
  await screen.findByText("最终保存的合成回答");
  await act(async () => oldRecovery.resolve(Response.json(thread("a"))));
  expect(screen.getByText("最终保存的合成回答")).toBeInTheDocument();
  expect(screen.queryByText("连接短暂中断，正在自动重连…")).not.toBeInTheDocument();
  expect(screen.queryByText("正在接收回答…")).not.toBeInTheDocument();
  if (status === "cancelled") expect(screen.getByText("已停止，生成内容已保存")).toBeInTheDocument();
  expect(sources).toHaveLength(1);
});

it("ignores a pending checkpoint failure after another thread opens", async () => {
  render(<Shell />);
  await openThread("a", 1);
  const pending = deferred<Response>(); queuedReads.push(pending);
  act(() => sources[0].message({ type: "tool.completed", message: "正在核对来源" }));
  await waitFor(() => expect(pendingStarted).toBe(1));
  await openThread("b", 2);
  await act(async () => pending.resolve(Response.json({ detail: "旧检查点读取失败" }, { status: 503 })));
  expect(screen.getByText("合成B当前回答")).toBeInTheDocument();
  expect(screen.getByText("正在接收回答…")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(sources[1].closed).toBe(false);
});

it("shows a current tool checkpoint while preserving text received during its read", async () => {
  render(<Shell />);
  await openThread("a", 1);
  const pending = deferred<Response>(); queuedReads.push(pending);
  act(() => sources[0].message({ type: "tool.completed" }));
  await waitFor(() => expect(pendingStarted).toBe(1));
  act(() => sources[0].message({ type: "text.delta", part_index: 0,
    offset: Array.from("合成A当前回答").length, text: "，检查点读取期间的新文字" }));
  await screen.findByText("合成A当前回答，检查点读取期间的新文字");
  const checkpoint = thread("a");
  const tool = { id: "checkpoint-tool", name: "read_resource", status: "completed", result: { note: "合成来源核对" } };
  checkpoint.tools.push(tool);
  checkpoint.messages[0].parts.push({ type: "tool", ...tool });
  await act(async () => pending.resolve(Response.json(checkpoint)));
  expect(screen.getByRole("button", { name: /读取资料.*已完成/ })).toBeInTheDocument();
  expect(screen.getByText("合成A当前回答，检查点读取期间的新文字")).toBeInTheDocument();
  expect(sources[0].closed).toBe(false);
  expect(screen.getByRole("button", { name: "停止回答" })).toBeEnabled();
});

it("releases the source, queued delta/checkpoint timers and pending reconnect ownership on unmount", async () => {
  const view = render(<Shell />);
  await openThread("a", 1);
  const pending = deferred<Response>(); queuedReads.push(pending);
  act(() => sources[0].error());
  vi.useFakeTimers();
  try {
    const before = vi.getTimerCount();
    act(() => {
      sources[0].message({ type: "text.delta", part_index: 0,
        offset: Array.from("合成A当前回答").length, text: "未刷新增量" });
      sources[0].message({ type: "tool.completed" });
    });
    expect(vi.getTimerCount()).toBeGreaterThanOrEqual(before + 2);
    view.unmount();
    expect(sources[0].closed).toBe(true);
    expect(sources[0].onopen).toBeNull();
    expect(sources[0].onmessage).toBeNull();
    expect(sources[0].onerror).toBeNull();
    const reads = vi.mocked(fetch).mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    expect(vi.mocked(fetch).mock.calls).toHaveLength(reads);
    await act(async () => pending.resolve(Response.json({ detail: "卸载后的旧响应" }, { status: 503 })));
    expect(sources).toHaveLength(1);
  } finally { vi.useRealTimers(); }
});

function interceptCancel(reply: (attempt: number) => Promise<Response> | Response) {
  const previous = fetch;
  let attempts = 0;
  vi.stubGlobal("fetch", vi.fn(async (input, init) => {
    if (String(input).endsWith("/assistant/runs/run-a/cancel") && init?.method === "POST")
      return reply(++attempts);
    return previous(input, init);
  }));
  return () => attempts;
}

it.each(["success", "failure"])("cancel request %s cannot replace a terminal saved status when its acknowledgement arrives late", async outcome => {
  const acknowledgement = deferred<Response>();
  const attempts = interceptCancel(() => acknowledgement.promise);
  render(<Shell />);
  await openThread("a", 1);
  fireEvent.click(screen.getByRole("button", { name: "停止回答" }));
  await waitFor(() => expect(attempts()).toBe(1));
  const final = thread("a"); final.runs[0].status = "cancelled"; final.runs[0].stream_cursor = 4;
  final.messages[0].parts = [{ type: "text", text: "停止前已保存的回答" }];
  saved.a = final;
  act(() => sources[0].message({ type: "run.completed", status: "cancelled" }, "4"));
  await screen.findByText("停止前已保存的回答");
  expect(screen.getByText("已停止，生成内容已保存")).toBeInTheDocument();
  await act(async () => acknowledgement.resolve(outcome === "success"
    ? Response.json({ ...final.runs[0], status: "cancelling" })
    : Response.json({ detail: "旧停止请求确认失败" }, { status: 503 })));
  expect(screen.getByText("停止前已保存的回答")).toBeInTheDocument();
  expect(screen.getByText("已停止，生成内容已保存")).toBeInTheDocument();
  expect(screen.queryByText("正在停止…")).not.toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it.each(["success", "failure"])("cancel request %s cannot change a new conversation after its owner is left", async outcome => {
  const acknowledgement = deferred<Response>();
  const attempts = interceptCancel(() => acknowledgement.promise);
  render(<Shell />);
  await openThread("a", 1);
  fireEvent.click(screen.getByRole("button", { name: "停止回答" }));
  await waitFor(() => expect(attempts()).toBe(1));
  fireEvent.click(screen.getByRole("button", { name: "新对话" }));
  expect(sources[0].closed).toBe(true);
  expect(screen.getByText("把问题交给知意")).toBeInTheDocument();
  await act(async () => acknowledgement.resolve(outcome === "success"
    ? Response.json({ ...thread("a").runs[0], status: "cancelling" })
    : Response.json({ detail: "旧对话停止确认失败" }, { status: 503 })));
  expect(screen.getByText("把问题交给知意")).toBeInTheDocument();
  expect(screen.queryByText("正在停止…")).not.toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("cancel request failure remains retryable and the normal acknowledgement-first path reaches saved completion", async () => {
  const acknowledgement = deferred<Response>();
  const attempts = interceptCancel(attempt => attempt === 1
    ? Response.json({ detail: "本次停止请求失败，可重试" }, { status: 503 }) : acknowledgement.promise);
  render(<Shell />);
  await openThread("a", 1);
  fireEvent.click(screen.getByRole("button", { name: "停止回答" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("本次停止请求失败，可重试");
  expect(screen.getByRole("button", { name: "停止回答" })).toBeEnabled();
  expect(screen.queryByText("正在停止…")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "停止回答" }));
  fireEvent.click(screen.getByRole("button", { name: "停止回答" }));
  expect(attempts()).toBe(2);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  saved.a.runs[0].status = "cancelling";
  await act(async () => acknowledgement.resolve(Response.json(saved.a.runs[0])));
  expect(screen.getByText("正在停止…")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "停止回答" })).toBeEnabled();
  saved.a.runs[0].status = "cancelled";
  act(() => sources[0].message({ type: "run.completed", status: "cancelled" }, "4"));
  await screen.findByText("已停止，生成内容已保存");
  expect(screen.queryByRole("button", { name: "停止回答" })).not.toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("cancel request late failure cannot overwrite a terminal run confirmed by reconnect recovery", async () => {
  const acknowledgement = deferred<Response>();
  const attempts = interceptCancel(() => acknowledgement.promise);
  render(<Shell />);
  await openThread("a", 1);
  fireEvent.click(screen.getByRole("button", { name: "停止回答" }));
  await waitFor(() => expect(attempts()).toBe(1));
  saved.a.runs[0].status = "cancelled";
  saved.a.runs[0].stream_cursor = 4;
  act(() => sources[0].error());
  await waitFor(() => expect(sources[0].closed).toBe(true));
  expect(screen.queryByRole("button", { name: "停止回答" })).not.toBeInTheDocument();
  await act(async () => acknowledgement.resolve(Response.json({ detail: "终态确认后的旧停止错误" }, { status: 503 })));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.queryByText("正在停止…")).not.toBeInTheDocument();
  expect(screen.getByText("已停止，生成内容已保存")).toBeInTheDocument();
});
