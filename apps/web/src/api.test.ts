import { describe, expect, test, vi } from "vitest";

import { apiFetch, createBackup, getTableExportUrl, getTableViewsExportUrl, getTasks, responseErrorMessage } from "./api";
import { assistantApi } from "./assistant/api";

test("export preserves empty selections and composes the group/search boundary", () => {
  const query = new URL(getTableExportUrl("one", "json", {viewId: "group", search: "日期 + 编号", rowIds: []}), "http://localhost").searchParams;
  expect(query.get("row_ids")).toBe("");
  expect(query.get("view_id")).toBe("group");
  expect(query.get("search")).toBe("日期 + 编号");
  expect(new URL(getTableViewsExportUrl("one", {taskIds: ["file-a", "file-b"]}), "http://localhost").searchParams.get("task_ids")).toBe("file-a,file-b");
});

describe("responseErrorMessage", () => {
  test("读取错误响应期间的取消保留取消语义", async () => {
    const cancelled = new DOMException("The operation was aborted", "AbortError");
    const response = new Response(new ReadableStream({ start(controller) { controller.error(cancelled); } }), { status: 503 });
    await expect(responseErrorMessage(response, "保存")).rejects.toBe(cancelled);
  });

  test("自定义取消原因不会变成网络故障", async () => {
    const controller = new AbortController();
    const cancelled = new TypeError("Cancelled by caller");
    controller.abort(cancelled);
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(cancelled));
    try { await expect(apiFetch("/api/v1/tasks", { signal: controller.signal })).rejects.toBe(cancelled); }
    finally { vi.unstubAllGlobals(); }
  });

  test.each([
    [400, "There was an error parsing the body", "上传失败：提交的内容不完整或格式不正确，请检查后重试。"],
    [404, "Not Found", "上传失败：相关内容不存在，可能已被删除。"],
    [500, "数据库读取失败：sqlalchemy.exc.OperationalError: SELECT * FROM data_rows", "上传失败：知意服务暂时不可用，请稍后重试。"],
  ])("将 %s 的框架诊断转成可理解的操作提示", async (status, detail, expected) => {
    const response = new Response(JSON.stringify({ detail }), { status });
    await expect(responseErrorMessage(response, "上传")).resolves.toBe(expected);
  });

  test.each([getTasks, () => assistantApi("/threads")])("连接中断不要求普通用户启动API", async request => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    try {
      await expect(request()).rejects.toThrow("无法连接知意，请稍后重试。如果仍无法连接，请关闭并重新打开知意。");
    } finally { vi.unstubAllGlobals(); }
  });

  test("优先显示后端提供的中文业务提示", async () => {
    const response = new Response(
      JSON.stringify({ detail: "当前数据表还没有分 Sheet 视图。" }),
      { status: 422, headers: { "Content-Type": "application/json" } },
    );
    await expect(responseErrorMessage(response, "导出")).resolves.toBe(
      "当前数据表还没有分 Sheet 视图。",
    );
  });

  test("写入响应中断时先确认结果，不提示盲目重复操作", async () => {
    const fetchRequest = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    vi.stubGlobal("fetch", fetchRequest);
    try {
      await expect(createBackup()).rejects.toThrow("连接中断，尚未确认操作结果。请先查看最新状态，再决定是否重试。");
      expect(fetchRequest).toHaveBeenCalledTimes(1);
      expect(fetchRequest).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ method: "POST" }));
    } finally { vi.unstubAllGlobals(); }
  });

  test("不把状态码或结构化校验对象直接展示给用户", async () => {
    const response = new Response(JSON.stringify({ detail: [{ loc: ["body", "name"] }] }), {
      status: 422,
      headers: { "Content-Type": "application/json" },
    });
    const message = await responseErrorMessage(response, "保存");
    expect(message).toBe("保存失败：提交的内容不完整或格式不正确，请检查后重试。");
    expect(message).not.toMatch(/422|Unprocessable|loc|body/);
  });

  test("服务异常使用可行动提示而不是 HTTP 状态", async () => {
    const response = new Response("server error", { status: 500 });
    await expect(responseErrorMessage(response, "导出")).resolves.toBe(
      "导出失败：知意服务暂时不可用，请稍后重试。",
    );
  });
});
