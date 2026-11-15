import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

  it("labels the horizontally scrollable table for narrow screens", async () => {
    vi.stubGlobal("fetch", vi.fn((input: string | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname.endsWith("/templates")) return Promise.resolve(json([]));
      if (url.pathname.endsWith("/tasks/summary")) return Promise.resolve(json(summary(1)));
      if (url.pathname.endsWith("/tasks")) return Promise.resolve(json([task("one", "sample.png")]));
      return Promise.resolve(json([]));
    }));

    render(<HistoryPage />);

    expect(await screen.findByText("sample.png")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "文件历史表格，可滚动" })).toContainElement(screen.getByText("sample.png"));
    expect(document.querySelectorAll(".history-blank-row")).toHaveLength(9);
    expect(screen.getAllByRole("row")).toHaveLength(2);
  });

  it("returns from a file to the same history page, position and focused record", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname.endsWith("/tasks/summary")) return json(summary(20));
      if (url.pathname.endsWith("/tasks")) {
        return json([url.searchParams.get("offset") === "10" ? task("two", "第二页.txt") : task("one", "第一页.txt")]);
      }
      if (url.pathname.endsWith("/result")) return json({
        task_id: "two", result: { header: {}, items: [] }, validation_issues: [],
      });
      return json([]);
    }));
    render(<HistoryPage />);
    await screen.findByRole("button", { name: "第一页.txt" });
    fireEvent.click(screen.getByTitle("下一页"));
    const file = await screen.findByRole("button", { name: "第二页.txt" });
    const viewport = () => screen.getByRole("region", { name: "文件历史表格，可滚动" });
    viewport().scrollTop = 105;
    viewport().scrollLeft = 165;
    fireEvent.scroll(viewport());
    file.focus();
    fireEvent.click(file);
    expect(await screen.findByRole("button", { name: "修订到表" })).toBeInTheDocument();
    const tab = screen.getByRole("button", { name: /^已完成/ });
    tab.focus();
    fireEvent.click(tab);
    expect(await screen.findByRole("button", { name: "第二页.txt" })).toHaveFocus();
    expect(viewport().scrollTop).toBe(105);
    expect(viewport().scrollLeft).toBe(165);
  });

  it("offers extraction in the normal empty history layout", async () => {
    vi.stubGlobal("fetch", vi.fn((input: string | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname.endsWith("/templates")) return Promise.resolve(json([]));
      if (url.pathname.endsWith("/tasks/summary")) return Promise.resolve(json(summary(0)));
      if (url.pathname.endsWith("/tasks")) return Promise.resolve(json([]));
      return Promise.resolve(json([]));
    }));
    const onNavigateExtract = vi.fn();

    render(<HistoryPage onNavigateExtract={onNavigateExtract} />);

    expect(await screen.findByText("还没有已完成的文件记录")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "按文件名搜索" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "去文件提取" }));
    expect(onNavigateExtract).toHaveBeenCalledTimes(1);
  });

  it("does not present an unreadable history as empty and lets the first read retry", async () => {
    let taskReads = 0;
    vi.stubGlobal("fetch", vi.fn((input: string | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname.endsWith("/templates")) return Promise.resolve(json([]));
      if (url.pathname.endsWith("/tasks/summary")) return Promise.resolve(json(summary(0)));
      if (url.pathname.endsWith("/tasks")) {
        taskReads += 1;
        return Promise.resolve(taskReads === 1
          ? new Response(JSON.stringify({ detail: "synthetic history read failure" }), { status: 503, headers: { "Content-Type": "application/json" } })
          : json([]));
      }
      return Promise.resolve(json([]));
    }));

    const { container } = render(<HistoryPage onNavigateExtract={vi.fn()} />);

    fireEvent.click(await screen.findByRole("button", { name: "文件历史事项" }));
    expect(screen.getByRole("dialog", { name: "文件历史事项" })).toHaveTextContent("尚未读取到历史数据");
    expect(screen.getByRole("dialog", { name: "文件历史事项" })).toHaveTextContent("知意服务暂时不可用，请稍后重试");
    expect(screen.getByRole("dialog", { name: "文件历史事项" })).not.toHaveTextContent("synthetic history read failure");
    expect(screen.getByRole("button", { name: "重新读取历史" })).toBeEnabled();
    expect(screen.queryByText("还没有已完成的文件记录")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "查看提取演示" })).not.toBeInTheDocument();
    expect(container.querySelector(".hist-stats-bar")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /已完成/ })).toHaveTextContent("—");

    fireEvent.click(screen.getByRole("button", { name: "重新读取历史" }));
    expect(await screen.findByText("还没有已完成的文件记录")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "去文件提取" })).toBeInTheDocument();
    expect(taskReads).toBe(2);
  });

  it("keeps previously loaded history visible and marks it stale when rereading fails", async () => {
    let fail = false;
    vi.stubGlobal("fetch", vi.fn((input: string | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname.endsWith("/templates")) return Promise.resolve(json([]));
      if (url.pathname.endsWith("/tasks/summary")) return Promise.resolve(json(summary(1)));
      if (url.pathname.endsWith("/tasks")) return Promise.resolve(fail
        ? new Response(JSON.stringify({ detail: "synthetic refresh failure" }), { status: 503, headers: { "Content-Type": "application/json" } })
        : json([task("known", "known.png")]));
      return Promise.resolve(json([]));
    }));

    render(<HistoryPage />);
    expect(await screen.findByText("known.png")).toBeInTheDocument();
    fail = true;
    fireEvent.change(screen.getByRole("textbox", { name: "按文件名搜索" }), { target: { value: "新的筛选条件" } });

    fireEvent.click(await screen.findByRole("button", { name: "文件历史事项" }));
    expect(screen.getByRole("dialog", { name: "文件历史事项" })).toHaveTextContent("当前筛选读取失败，仍显示上次成功读取的内容");
    expect(screen.getByRole("dialog", { name: "文件历史事项" })).toHaveTextContent("知意服务暂时不可用，请稍后重试");
    expect(screen.getByRole("dialog", { name: "文件历史事项" })).not.toHaveTextContent("synthetic refresh failure");
    expect(document.querySelector(".history-blank-row")).not.toBeInTheDocument();
    expect(screen.getByText("known.png")).toBeInTheDocument();
    expect(document.querySelector(".history-pagination .footer-info")).toHaveTextContent("当前筛选结果数尚未确认");
    expect(document.querySelector(".history-pagination .footer-info")).not.toHaveTextContent("共 0 条");

    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "重新读取历史" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(screen.getByText("known.png")).toBeInTheDocument();
  });

  it("shows a filter no-result state instead of the first-run history actions", async () => {
    vi.stubGlobal("fetch", vi.fn((input: string | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname.endsWith("/templates")) return Promise.resolve(json([]));
      if (url.pathname.endsWith("/tasks/summary")) {
        return Promise.resolve(json(url.searchParams.has("search")
          ? { total: 0, completed: 0, needs_review: 0, failed: 0 }
          : { total: 6, completed: 4, needs_review: 2, failed: 0 }));
      }
      if (url.pathname.endsWith("/tasks")) return Promise.resolve(json([]));
      return Promise.resolve(json([]));
    }));
    vi.useFakeTimers();
    render(<HistoryPage onNavigateExtract={vi.fn()} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(250); });

    fireEvent.change(screen.getByRole("textbox", { name: "按文件名搜索" }), { target: { value: "没有这份文件" } });
    await act(async () => { await vi.advanceTimersByTimeAsync(250); });

    expect(screen.getByText("没有符合条件的记录")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "清除筛选" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "按文件名搜索" })).toBeInTheDocument();
    expect(screen.queryByText("累计任务")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /已完成/ })).toHaveTextContent("0");
    expect(screen.getByLabelText("跳转页码")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "只读示例" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "查看提取演示" })).not.toBeInTheDocument();
  });

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

  it("retries a failed original extraction read in place without losing the selected file", async () => {
    let extractionReads = 0;
    const result = { header: { amount: 372 }, items: [] };
    const extraction = {
      task_id: "original",
      review_version: 1,
      result,
      original_result: result,
      detail_ids: [],
      validation_issues: [],
      current_schema: [{ key: "amount", label: "金额", section: "header", value_type: "number" }],
    };
    vi.stubGlobal("fetch", vi.fn((input: string | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname.endsWith("/templates")) return Promise.resolve(json([]));
      if (url.pathname.endsWith("/tasks/summary")) return Promise.resolve(json(summary(1)));
      if (url.pathname.endsWith("/tasks")) return Promise.resolve(json([task("original", "keep-selected.png")]));
      if (url.pathname.endsWith("/result")) {
        extractionReads += 1;
        return Promise.resolve(extractionReads === 1
          ? new Response(JSON.stringify({ detail: "synthetic extraction read failure" }), { status: 503, headers: { "Content-Type": "application/json" } })
          : json(extraction));
      }
      return Promise.resolve(json([]));
    }));
    render(<HistoryPage />);
    fireEvent.click(await screen.findByText("keep-selected.png"));

    const error = await screen.findByRole("alert");
    expect(error).toHaveTextContent("暂时无法读取这份文件的提取结果");
    expect(error).toHaveTextContent("知意服务暂时不可用，请稍后重试");
    expect(error).not.toHaveTextContent("synthetic extraction read failure");
    fireEvent.click(screen.getByRole("button", { name: "重新读取提取结果" }));
    expect(await screen.findByRole("button", { name: "修订到表" })).toBeInTheDocument();
    expect(screen.getByText("keep-selected.png")).toBeInTheDocument();
    expect(extractionReads).toBe(2);
  });

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
  it("moves keyboard focus to the located header field", async () => {
    HTMLElement.prototype.scrollIntoView = vi.fn();
    await openReview();
    const field = screen.getByText("372", { selector: ".extract-header-value" }).closest(".extract-header-row");
    fireEvent.click(screen.getByRole("button", { name: "定位相关字段" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(20); });
    expect(field).toHaveFocus();
  });

  it("saves a textual exception together with another corrected field", async () => {
    const writes = await openReview();
    edit("372", "1,23");
    edit("12", "13");
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "修订到表" })); });
    expect(writes).toHaveLength(1);
    expect(writes[0]).toMatchObject({result: {header: {amount: "1,23", tax: 13}}});
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("1,23", { selector: ".extract-header-value" })).toBeInTheDocument();
  });
  it("keeps a conflicted review draft until the user confirms rereading", async () => {
    await openReview(async () => new Response(JSON.stringify({ detail: "数据已有更新" }), { status: 409 }));
    edit("372", "373");
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "修订到表" })); });
    expect(screen.getByRole("alert")).toHaveTextContent("修改未保存");
    fireEvent.click(screen.getByRole("button", { name: "处理冲突" }));
    fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
    let dialog = screen.getByRole("dialog", { name: "修改尚未保存" });
    fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(screen.getByText("373", { selector: ".extract-header-value" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "处理冲突" }));
    fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
    dialog = screen.getByRole("dialog", { name: "修改尚未保存" });
    await act(async () => { fireEvent.click(within(dialog).getByRole("button", { name: "放弃修改并继续" })); });
    expect(screen.getByText("372", { selector: ".extract-header-value" })).toBeInTheDocument();
  });

  it("clears the old conflict action only after confirming a tab change", async () => {
    await openReview(async () => new Response(JSON.stringify({ detail: "数据已有更新" }), { status: 409 }));
    const originalFetch = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn((input: string | URL, init?: RequestInit) => init?.method === "DELETE"
      ? Promise.resolve(new Response(JSON.stringify({ detail: "删除失败" }), { status: 500 }))
      : originalFetch(input, init)));
    edit("372", "373");
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "修订到表" })); });
    fireEvent.click(screen.getByRole("button", { name: /已完成/ }));
    fireEvent.click(within(screen.getByRole("dialog", { name: "修改尚未保存" })).getByRole("button", { name: "取消" }));
    expect(screen.getByRole("button", { name: "处理冲突" })).toBeInTheDocument();
    expect(screen.getByText("373", { selector: ".extract-header-value" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /已完成/ }));
    fireEvent.click(within(screen.getByRole("dialog", { name: "修改尚未保存" })).getByRole("button", { name: "放弃修改并继续" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(250); });
    fireEvent.keyDown(screen.getByRole("button", { name: "文件操作：另一文件.txt" }), { key: "Enter" });
    fireEvent.click(screen.getByRole("menuitem", { name: "删除整个文件" }));
    fireEvent.change(screen.getByRole("textbox", { name: "输入 另一文件.txt 确认删除" }), { target: { value: "另一文件.txt" } });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "确认删除" })); });
    expect(screen.getByRole("alert")).toHaveTextContent("删除失败");
    expect(screen.queryByRole("button", { name: "重新读取" })).not.toBeInTheDocument();
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
