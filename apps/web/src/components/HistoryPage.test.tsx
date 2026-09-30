import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Task } from "../types";
import { HistoryPage } from "./HistoryPage";

const summary = (completed: number) => ({ total: completed, completed, needs_review: 0, failed: 0 });
const task = (id: string, filename: string) => ({
  id, filename, content_type: "text/plain", size_bytes: 1, page_count: 1, sha256: id,
  template_mode: "smart", template_id: null, template_version: null, candidate_templates: [],
  status: "completed", duplicate_of_task_id: null, created_at: "2026-08-01T00:00:00Z",
  completed_at: "2026-08-01T00:00:01Z", updated_at: "2026-08-01T00:00:01Z", elapsed_seconds: 1,
}) as Task;
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
vi.mock("./DocumentPreview", () => ({ DocumentPreview: () => <div>原件预览</div> }));

describe("HistoryPage request races", () => {
  afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

  it("does not let an older search response replace the newer list or counts", async () => {
    let releaseA!: (response: Response) => void;
    const fetchMock = vi.fn((input: string | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname.endsWith("/templates")) return Promise.resolve(json([]));
      if (url.pathname.endsWith("/tasks/summary")) {
        const query = url.searchParams.get("search");
        return Promise.resolve(json(query === "A" ? summary(8) : query === "B" ? summary(2) : summary(9)));
      }
      if (url.pathname.endsWith("/tasks")) {
        const query = url.searchParams.get("search");
        if (query === "A") return new Promise<Response>(resolve => { releaseA = resolve; });
        if (query === "B") return Promise.resolve(json([task("b", "B.png")]));
        return Promise.resolve(json([]));
      }
      return Promise.resolve(json([]));
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.useFakeTimers();

    render(<HistoryPage />);
    await act(async () => { await vi.advanceTimersByTimeAsync(250); });
    const search = screen.getByRole("textbox", { name: "按文件名搜索" });
    fireEvent.change(search, { target: { value: "A" } });
    await act(async () => { await vi.advanceTimersByTimeAsync(250); });
    expect(releaseA).toBeDefined();

    fireEvent.change(search, { target: { value: "B" } });
    await act(async () => { await vi.advanceTimersByTimeAsync(250); });
    expect(screen.getByText("B.png")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /已完成/ })).toHaveTextContent("2");

    await act(async () => { releaseA(json([task("a", "A.png")])); });
    expect(screen.getByText("B.png")).toBeInTheDocument();
    expect(screen.queryByText("A.png")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /已完成/ })).toHaveTextContent("2");
  });

  it.each(["template", "time", "tab"])("ignores an old error during the new %s filter debounce", async (change) => {
    let rejectOld!: (reason: unknown) => void;
    let calls = 0;
    vi.stubGlobal("fetch", vi.fn((input: string | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname.endsWith("/templates")) return Promise.resolve(json([{ id: "t", name: "新模板" }]));
      if (url.pathname.endsWith("/tasks/summary")) return Promise.resolve(json({ ...summary(2), needs_review: 1 }));
      if (url.pathname.endsWith("/tasks")) {
        calls += 1;
        if (calls === 1) return new Promise<Response>((_, reject) => { rejectOld = reject; });
        return Promise.resolve(json([task("new", "新条件.png")]));
      }
      return Promise.resolve(json([]));
    }));
    vi.useFakeTimers();
    render(<HistoryPage />);
    await act(async () => { await vi.advanceTimersByTimeAsync(250); });
    if (change === "template") fireEvent.change(screen.getByLabelText("按模板筛选"), { target: { value: "t" } });
    else if (change === "time") fireEvent.change(screen.getByLabelText("按时间筛选"), { target: { value: "7d" } });
    else fireEvent.click(screen.getByRole("button", { name: /校验问题/ }));
    await act(async () => { rejectOld(new Error("旧条件响应失败")); });
    expect(screen.queryByText("旧条件响应失败")).not.toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(250); });
    expect(screen.getByText("新条件.png")).toBeInTheDocument();
    expect(calls).toBe(2);
  });
});

describe("HistoryPage original review integrity", () => {
  afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

  async function openReview(holdSave?: (body: Record<string, unknown>, extraction: Record<string, unknown>) => Promise<Response>) {
    const writes: Array<Record<string, unknown>> = [];
    const result = { header: { amount: 372, tax: 12 }, items: [] };
    const extraction = {
      task_id: "review", review_version: 1, result, original_result: result, detail_ids: [],
      validation_issues: [{ code: "mismatch", field: "amount", severity: "warning", message: "金额待核对" }],
      current_schema: [
        { key: "amount", label: "金额", section: "header", value_type: "number" },
        { key: "tax", label: "税额", section: "header", value_type: "number" },
      ],
    };
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname.endsWith("/templates")) return json([]);
      if (url.pathname.endsWith("/tasks/summary")) return json(summary(1));
      if (url.pathname.endsWith("/tasks")) return json([task("review", "待核对.txt"), task("second", "另一文件.txt")]);
      if (url.pathname.endsWith("/result")) return json(url.pathname.includes("/second/")
        ? { ...extraction, task_id: "second", result: { header: { amount: 999, tax: 12 }, items: [] } }
        : extraction);
      if (url.pathname.endsWith("/review")) {
        const body = JSON.parse(String(init?.body));
        writes.push(body);
        if (holdSave) return holdSave(body, extraction);
        return json({ ...extraction, result: body.result, review_version: 2 });
      }
      return json([]);
    }));
    vi.useFakeTimers();
    render(<HistoryPage />);
    await act(async () => { await vi.advanceTimersByTimeAsync(250); });
    await act(async () => { fireEvent.click(screen.getByText("待核对.txt")); });
    return writes;
  }

  function edit(text: string, value: string) {
    const cell = screen.getByText(text, { selector: ".extract-header-value" });
    fireEvent.click(cell);
    cell.textContent = value;
    fireEvent.blur(cell);
  }

  it("retains an invalid numeric draft when another field is edited and prevents any write", async () => {
    const writes = await openReview();
    edit("372", "1,23");
    edit("12", "13");
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "修订到表" })); });
    expect(writes).toEqual([]);
    expect(screen.getByRole("alert")).toHaveTextContent("标准数字");
    expect(screen.getByText("1,23", { selector: ".extract-header-value" })).toBeInTheDocument();
  });

  it("preserves unsafe integer digits in the review request and clears dirty state on success", async () => {
    const writes = await openReview();
    edit("372", "9007199254740993");
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "修订到表" })); });
    expect(writes).toHaveLength(1);
    expect(writes[0]).toMatchObject({ result: { header: { amount: "9007199254740993" } } });
    fireEvent.click(screen.getByRole("button", { name: /已完成/ }));
    expect(screen.queryByRole("dialog", { name: "修改尚未保存" })).not.toBeInTheDocument();
  });

  it("guards local tab changes and app navigation, retaining edits when canceled", async () => {
    await openReview();
    edit("372", "373");
    fireEvent.click(screen.getByRole("button", { name: /已完成/ }));
    let dialog = screen.getByRole("dialog", { name: "修改尚未保存" });
    fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(screen.getByText("373", { selector: ".extract-header-value" })).toBeInTheDocument();
    const proceed = vi.fn();
    const event = new CustomEvent("zhiyi:before-navigate", { cancelable: true, detail: { proceed } });
    act(() => { window.dispatchEvent(event); });
    expect(event.defaultPrevented).toBe(true);
    dialog = screen.getByRole("dialog", { name: "修改尚未保存" });
    fireEvent.click(within(dialog).getByRole("button", { name: "放弃修改并继续" }));
    expect(proceed).toHaveBeenCalledTimes(1);
  });

  it("treats ignored-rule-only edits as unsaved changes", async () => {
    await openReview();
    fireEvent.click(screen.getByRole("button", { name: "忽略" }));
    fireEvent.click(screen.getByRole("button", { name: /已完成/ }));
    expect(screen.getByRole("dialog", { name: "修改尚未保存" })).toBeInTheDocument();
  });

  it("does not apply a late save from the previous file to a newly opened file", async () => {
    let release!: () => void;
    await openReview((body, extraction) => new Promise<Response>(resolve => {
      release = () => resolve(json({ ...extraction, result: body.result, review_version: 2 }));
    }));
    edit("372", "373");
    fireEvent.click(screen.getByRole("button", { name: "修订到表" }));
    fireEvent.click(screen.getByRole("button", { name: /已完成/ }));
    fireEvent.click(within(screen.getByRole("dialog", { name: "修改尚未保存" })).getByRole("button", { name: "放弃修改并继续" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(250); });
    await act(async () => { fireEvent.click(screen.getByText("另一文件.txt")); });
    expect(screen.getByText("999", { selector: ".extract-header-value" })).toBeInTheDocument();
    await act(async () => { release(); });
    expect(screen.getByText("999", { selector: ".extract-header-value" })).toBeInTheDocument();
    expect(screen.queryByText("373", { selector: ".extract-header-value" })).not.toBeInTheDocument();
  });
});
