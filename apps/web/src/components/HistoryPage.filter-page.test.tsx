import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { HistoryPage } from "./HistoryPage";
import type { Task } from "../types";

const task = (filename: string): Task => ({
  id: filename, filename, content_type: "text/plain", size_bytes: 10, sha256: "synthetic",
  template_mode: "smart", template_id: null, template_version: null, candidate_templates: [],
  status: "completed", duplicate_of_task_id: null, created_at: "2026-11-01T00:00:00Z",
  updated_at: "2026-11-01T00:00:00Z", completed_at: "2026-11-01T00:00:00Z", record_count: 1,
});

it("starts a new history filter at page one instead of keeping the previous result offset", async () => {
  const searchedOffsets: number[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    const searched = url.searchParams.get("search") === "命中";
    if (url.pathname.endsWith("/tasks/summary")) return Response.json({
      total: searched ? 1 : 20, completed: searched ? 1 : 20, needs_review: 0, failed: 0,
    });
    if (url.pathname.endsWith("/templates")) return Response.json([]);
    if (url.pathname.endsWith("/tasks")) {
      const offset = Number(url.searchParams.get("offset") ?? 0);
      if (searched) {
        searchedOffsets.push(offset);
        return Response.json(offset === 0 ? [task("命中文件.txt")] : []);
      }
      return Response.json([task(offset === 0 ? "第一页.txt" : "第二页.txt")]);
    }
    throw new Error(`Unexpected synthetic request: ${url.pathname}`);
  }));
  render(<HistoryPage />);
  await screen.findByText("第一页.txt");
  fireEvent.click(screen.getByTitle("下一页"));
  await screen.findByText("第二页.txt");
  fireEvent.change(screen.getByRole("textbox", { name: "按文件名搜索" }), { target: { value: "命中" } });
  await screen.findByText("命中文件.txt");
  expect(searchedOffsets).toEqual([0]);
  expect(screen.getByText(/显示 1-1 条，共 1 条/)).toBeInTheDocument();
});

it("rereads the clamped page after deleting the last file on the last page", async () => {
  let removed = false;
  const offsetsAfterDelete: number[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    if (init?.method === "DELETE") {
      removed = true;
      return Response.json({ kept_rows: 0 });
    }
    if (url.pathname.endsWith("/tasks/summary")) return Response.json({
      total: removed ? 10 : 11, completed: removed ? 10 : 11, needs_review: 0, failed: 0,
    });
    if (url.pathname.endsWith("/templates")) return Response.json([]);
    if (url.pathname.endsWith("/tasks")) {
      const offset = Number(url.searchParams.get("offset") ?? 0);
      if (removed) offsetsAfterDelete.push(offset);
      return Response.json(offset === 0 ? [task("仍保留的文件.txt")]
        : removed ? [] : [task("末页唯一文件.txt")]);
    }
    throw new Error(`Unexpected synthetic request: ${url.pathname}`);
  }));
  render(<HistoryPage />);
  await screen.findByText("仍保留的文件.txt");
  fireEvent.click(screen.getByTitle("下一页"));
  await screen.findByText("末页唯一文件.txt");
  fireEvent.keyDown(screen.getByRole("button", { name: "文件操作：末页唯一文件.txt" }), { key: "ArrowDown" });
  fireEvent.click(await screen.findByRole("menuitem", { name: "删除整个文件" }));
  fireEvent.change(screen.getByRole("textbox", { name: "输入 末页唯一文件.txt 确认删除" }), { target: { value: "末页唯一文件.txt" } });
  fireEvent.click(screen.getByRole("button", { name: /^确认删除$/ }));
  await screen.findByText("仍保留的文件.txt");
  expect(offsetsAfterDelete).toEqual([10, 0]);
  expect(screen.queryByText("当前页没有记录")).not.toBeInTheDocument();
});
