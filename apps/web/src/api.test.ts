import { describe, expect, test } from "vitest";

import { responseErrorMessage } from "./api";

describe("responseErrorMessage", () => {
  test("优先显示后端提供的中文业务提示", async () => {
    const response = new Response(
      JSON.stringify({ detail: "当前数据表还没有分 Sheet 视图。" }),
      { status: 422, headers: { "Content-Type": "application/json" } },
    );
    await expect(responseErrorMessage(response, "导出")).resolves.toBe(
      "当前数据表还没有分 Sheet 视图。",
    );
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
