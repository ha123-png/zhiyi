import { describe, expect, test, vi } from "vitest";
import { ApiError, createBackup, deleteBackup, generateTemplateDraft, getTasks } from "./api";
import { assistantApi } from "./assistant/api";
import { dashboardApi } from "./dashboard/api";

const incompleteResult = "未能读取完整结果。请刷新当前页面，确认最新状态后再操作。";
const entries = [
  { label: "任务读取", status: 200, request: () => getTasks(), apiError: true },
  { label: "创建备份", status: 201, request: () => createBackup(), apiError: true },
  { label: "问知意", status: 200, request: () => assistantApi("/threads"), apiError: false },
  { label: "仪表盘", status: 200, request: () => dashboardApi.overview(7), apiError: false },
];

describe("成功响应体读取", () => {
  const cancellableEntries = [
    { label: "任务读取", request: (signal: AbortSignal) => getTasks({ signal }) },
    { label: "生成模板", request: (signal: AbortSignal) => generateTemplateDraft([], "合成需求", undefined, true, false, signal) },
  ];

  test.each(cancellableEntries.flatMap(entry => [200, 409].flatMap(status => [TypeError, SyntaxError].map(ErrorType => ({
    ...entry, status, ErrorType,
  })))))("$label 状态 $status 读取体时保留自定义 $ErrorType.name 取消原因", async ({request: call, status, ErrorType}) => {
    const abort = new AbortController();
    const reason = new ErrorType("explicit cancellation");
    const response = new Response(new ReadableStream({ start(body) {
      abort.signal.addEventListener("abort", () => body.error(abort.signal.reason), { once: true });
    } }), { status });
    let onRead!: () => void;
    const startedReading = new Promise<void>(resolve => { onRead = resolve; });
    const read = response.json.bind(response);
    vi.spyOn(response, "json").mockImplementation(() => {
      const pending = read();
      onRead();
      return pending;
    });
    const fetchRequest = vi.fn().mockResolvedValue(response);
    vi.stubGlobal("fetch", fetchRequest);
    const assertion = expect(call(abort.signal)).rejects.toBe(reason);
    await startedReading;
    abort.abort(reason);
    await assertion;
    expect(fetchRequest).toHaveBeenCalledTimes(1);
    expect(fetchRequest).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ signal: abort.signal }));
  });

  test.each(cancellableEntries)("$label 信号未取消时的响应断流仍显示完整结果提示", async ({request: call}) => {
    const abort = new AbortController();
    const response = new Response(new ReadableStream({ start(body) {
      body.error(new TypeError("terminated"));
    } }), { status: 200 });
    const fetchRequest = vi.fn().mockResolvedValue(response);
    vi.stubGlobal("fetch", fetchRequest);
    await expect(call(abort.signal)).rejects.toThrow(incompleteResult);
    expect(fetchRequest).toHaveBeenCalledTimes(1);
  });

  test.each(cancellableEntries)("$label 信号未取消时错误体断流仍保留 409 状态", async ({request: call}) => {
    const abort = new AbortController();
    const response = new Response(new ReadableStream({ start(body) {
      body.error(new TypeError("terminated"));
    } }), { status: 409 });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
    const error = await call(abort.signal).catch(value => value);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toHaveProperty("status", 409);
    expect(error).toHaveProperty("message", "请求失败：数据已经发生变化，请刷新后重试。");
  });

  test.each(entries)("$label 的断流先要求确认最新结果", async entry => {
    const response = new Response(new ReadableStream({ start(controller) {
      controller.error(new TypeError("terminated"));
    } }), { status: entry.status });
    const request = vi.fn().mockResolvedValue(response);
    vi.stubGlobal("fetch", request);
    await expect(entry.request()).rejects.toThrow(incompleteResult);
    expect(request).toHaveBeenCalledTimes(1);
  });

  test.each(entries)("$label 的不完整 JSON 不透出解析诊断", async entry => {
    const request = vi.fn().mockResolvedValue(new Response('{"incomplete":', { status: entry.status }));
    vi.stubGlobal("fetch", request);
    await expect(entry.request()).rejects.toThrow(incompleteResult);
    expect(request).toHaveBeenCalledTimes(1);
  });

  test.each(entries)("$label 读取期间取消仍抛原取消对象", async entry => {
    const cancelled = new DOMException("The operation was aborted", "AbortError");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(new ReadableStream({ start(controller) {
      controller.error(cancelled);
    } }), { status: entry.status })));
    await expect(entry.request()).rejects.toBe(cancelled);
  });

  test.each(entries.filter(entry => entry.label !== "创建备份"))("$label 保留中文业务错误及原错误类型", async entry => {
    const detail = "数据已变化，请重新读取后再修改。";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ detail }, { status: 409 })));
    const error = await entry.request().catch(value => value);
    expect(error).toBeInstanceOf(Error);
    expect(error).toHaveProperty("message", detail);
    if (entry.apiError) {
      expect(error).toBeInstanceOf(ApiError);
      expect(error).toHaveProperty("status", 409);
    }
  });

  test.each([
    { label: "删除备份", request: () => deleteBackup("synthetic") },
    { label: "删除统计卡", request: () => dashboardApi.remove("synthetic") },
  ])("$label 的204不读取JSON", async entry => {
    const response = new Response(null, { status: 204 });
    const read = vi.spyOn(response, "json");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
    await expect(entry.request()).resolves.toBeUndefined();
    expect(read).not.toHaveBeenCalled();
  });

  test.each([
    { label: "问知意", request: () => assistantApi("/threads") },
    { label: "统计", request: () => dashboardApi.overview(7) },
  ])("$label 的框架错误仍使用原操作说明", async entry => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ detail: "Not Found" }, { status: 404 })));
    await expect(entry.request()).rejects.toThrow(`${entry.label}失败：相关内容不存在，可能已被删除。`);
  });

  test("统计写入连接中断不提示盲重试", async () => {
    const request = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    vi.stubGlobal("fetch", request);
    await expect(dashboardApi.order([])).rejects.toThrow("连接中断，尚未确认操作结果。请先查看最新状态，再决定是否重试。");
    expect(request).toHaveBeenCalledTimes(1);
  });

  test("统计请求阶段的取消仍抛原取消对象", async () => {
    const cancelled = new DOMException("The operation was aborted", "AbortError");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(cancelled));
    await expect(dashboardApi.overview(7)).rejects.toBe(cancelled);
  });
});
