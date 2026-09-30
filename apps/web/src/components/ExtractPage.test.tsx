import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Extraction, ExtractionTemplate, Task } from "../types";
import { ExtractPage } from "./ExtractPage";

const task: Task = {
  id: "task-review",
  filename: "invoice.png",
  content_type: "image/png",
  size_bytes: 1024,
  page_count: 1,
  sha256: "abc",
  template_mode: "invoice",
  template_id: "builtin-invoice",
  template_version: 1,
  candidate_templates: [],
  status: "completed",
  duplicate_of_task_id: null,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
};

const invoiceTemplate: ExtractionTemplate = {
  id: "builtin-invoice",
  version: 1,
  is_system: true,
  builtin_key: "invoice",
  source_template_id: null,
  name: "发票",
  description: "整理购销双方、票号、日期、金额、税额和商品明细。",
  extra_instructions: "",
  fields: [
    { key: "seller_name", label: "销售方", section: "header", example: "", instructions: "", value_type: "text" },
    { key: "buyer_name", label: "购买方", section: "header", example: "", instructions: "", value_type: "text" },
    { key: "document_number", label: "发票号码", section: "header", example: "", instructions: "", value_type: "text" },
    { key: "document_date", label: "开票日期", section: "header", example: "", instructions: "", value_type: "date" },
    { key: "amount_before_tax", label: "不含税金额", section: "header", example: "", instructions: "", value_type: "number" },
    { key: "tax_amount", label: "税额", section: "header", example: "", instructions: "", value_type: "number" },
    { key: "total_amount", label: "价税合计", section: "header", example: "", instructions: "", value_type: "number" },
    { key: "name", label: "商品名称", section: "item", example: "", instructions: "", value_type: "text" },
    { key: "specification", label: "规格型号", section: "item", example: "", instructions: "", value_type: "text" },
    { key: "unit", label: "单位", section: "item", example: "", instructions: "", value_type: "text" },
    { key: "quantity", label: "数量", section: "item", example: "", instructions: "", value_type: "number" },
    { key: "unit_price", label: "单价", section: "item", example: "", instructions: "", value_type: "number" },
    { key: "amount", label: "金额", section: "item", example: "", instructions: "", value_type: "number" },
    { key: "tax_rate", label: "税率", section: "item", example: "", instructions: "", value_type: "text" },
    { key: "tax_amount", label: "税额", section: "item", example: "", instructions: "", value_type: "number" },
  ],
  validation_rules: [],
  deterministic_rules: [],
  output_mapping: {},
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
};

const extraction: Extraction = {
  task_id: task.id,
  document_kind: "invoice",
  template_id: task.template_id,
  template_version: 1,
  template: invoiceTemplate,
  model_name: "test-model",
  prompt_version: "test-v1",
  elapsed_seconds: 1,
  review_version: 2,
  original_result: result(372),
  result: result(372),
  validation_issues: [],
  evidence: [
    {
      field_path: "total_amount",
      page_number: 1,
      region: null,
      quote: null,
      status: "page_only",
      source: "system",
      location_verified: true,
    },
  ],
};

let taskListeners: Array<(event?: MessageEvent) => void> = [];
let taskSubscriptionCount = 0;

class MockEventSource {
  private taskListener?: (event?: MessageEvent) => void;

  addEventListener(_type: string, fn: (event?: MessageEvent) => void) {
    this.taskListener = fn;
    taskSubscriptionCount += 1;
    taskListeners.push(fn);
  }
  close() {
    if (this.taskListener) taskListeners = taskListeners.filter(listener => listener !== this.taskListener);
  }
}

function triggerTaskEvent() {
  // 与 api.ts 的 subscribeTaskEvents 一致：SSE 推送事件数组，前端解析后刷新
  for (const fn of taskListeners) {
    fn({
      data: JSON.stringify([
        { id: "evt", status: "queued", filename: "x.png", failure_message: null },
      ]),
    } as MessageEvent);
  }
}

async function waitForTaskListener() {
  await waitFor(() => expect(taskListeners.length).toBeGreaterThan(0));
}

async function waitForResubscribedTaskListener(previousCount: number) {
  await waitFor(() => {
    expect(taskSubscriptionCount).toBeGreaterThan(previousCount);
    expect(taskListeners.length).toBeGreaterThan(0);
  });
}

describe("ExtractPage", () => {
  it("focuses the related value from a validation issue instead of clearing editing on the parent", async () => {
    HTMLElement.prototype.scrollIntoView = vi.fn();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      const url = String(input);
      if (url.endsWith("/templates")) return response([]);
      if (url.endsWith(`/tasks/${task.id}/result`)) return response({ ...extraction,
        validation_issues: [{ code: "amount_mismatch", field: "total_amount", message: "合计不一致", severity: "warning" }],
      });
      throw new Error(url);
    }));
    render(<ExtractPage initialTask={task} />);
    fireEvent.click(await screen.findByRole("button", { name: "编辑相关字段" }));
    const value = screen.getByRole("button", { name: "编辑字段 价税合计" }).querySelector('[contenteditable="true"]');
    expect(value).toHaveFocus();
  });

  it("independent audit: old save response cannot attach old values to a newly uploaded source", async () => {
    HTMLElement.prototype.scrollIntoView = vi.fn();
    let release: ((value: Response) => void) | undefined;
    const savedResponse = new Promise<Response>(resolve => { release = resolve; });
    const nextTask = {...task, id:"new-source",filename:"new-source.png",status:"paused"};
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/templates")) return response([]);
      if (url.endsWith(`/tasks/${task.id}/result`)) return response(extraction);
      if (url.endsWith(`/tasks/${task.id}/review`)) return savedResponse;
      if (url.endsWith("/tasks") && init?.method === "POST") return response(nextTask);
      if (url.includes("/tasks?")) return response([nextTask]);
      throw new Error(url);
    }));
    const {container} = render(<ExtractPage initialTask={task} />);
    await screen.findByText("372", {selector:".extract-header-value"});
    fireEvent.click(screen.getByRole("button",{name:"修订到表"}));
    fireEvent.change(container.querySelector('input[type="file"]')!, {target:{files:[new File(["new"],"new-source.png")]}});
    await waitFor(() => expect(screen.queryByText("372",{selector:".extract-header-value"})).not.toBeInTheDocument());
    await act(async()=> {release!(response({...extraction,review_version:3}));});
    expect(screen.queryByText("372",{selector:".extract-header-value"})).not.toBeInTheDocument();
    expect(screen.queryByRole("button",{name:"修订到表"})).not.toBeInTheDocument();
  });

  it("independent audit: switching history task cannot retain old file values while new result is pending", async () => {
    HTMLElement.prototype.scrollIntoView = vi.fn();
    const pendingResponse = new Promise<Response>(() => {});
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      const url = String(input);
      if (url.endsWith("/templates")) return response([]);
      if (url.endsWith(`/tasks/${task.id}/result`)) return response(extraction);
      if (url.endsWith("/tasks/new-history/result")) return pendingResponse;
      throw new Error(url);
    }));
    const {rerender} = render(<ExtractPage initialTask={task} />);
    await screen.findByText("372",{selector:".extract-header-value"});
    rerender(<ExtractPage initialTask={{...task,id:"new-history",filename:"new-history.png"}} />);
    expect(screen.queryByText("372",{selector:".extract-header-value"})).not.toBeInTheDocument();
  });


  beforeEach(() => {
    window.localStorage.clear();
    taskListeners = [];
    taskSubscriptionCount = 0;
    HTMLElement.prototype.scrollIntoView = vi.fn();
    vi.stubGlobal("EventSource", MockEventSource);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (input: string | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/templates")) return response([]);
        if (url.endsWith(`/tasks/${task.id}/result`)) return response(extraction);
        if (url.endsWith(`/tasks/${task.id}/review`) && init?.method === "PUT") {
          const body = JSON.parse(String(init.body));
          return response({ ...extraction, review_version: 3, result: body.result });
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
  });

  it("reloads remembered current data instead of trusting stale browser values", async () => {
    localStorage.setItem("last-extraction-record-v1", JSON.stringify({ task, extraction }));
    let release: ((response: Response) => void) | undefined;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      const url = String(input);
      if (url.endsWith("/templates")) return response([]);
      if (url.endsWith(`/tasks/${task.id}/result`)) return new Promise<Response>(resolve => { release = resolve; });
      throw new Error(url);
    }));
    render(<ExtractPage />);
    expect(screen.queryByText("372", { selector: ".extract-header-value" })).not.toBeInTheDocument();
    await act(async () => { release!(response({ ...extraction, result: result(401), review_version: 3 })); });
    expect(await screen.findByText("401", { selector: ".extract-header-value" })).toBeInTheDocument();
    expect(screen.queryByText("372", { selector: ".extract-header-value" })).not.toBeInTheDocument();
  });

  it("does not resurrect a remembered file deleted from the service", async () => {
    localStorage.setItem("last-extraction-record-v1", JSON.stringify({ task, extraction }));
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      const url = String(input);
      if (url.endsWith("/templates")) return response([]);
      if (url.endsWith(`/tasks/${task.id}/result`)) return new Response(JSON.stringify({ detail: "没有找到任务" }), { status: 404 });
      throw new Error(url);
    }));
    render(<ExtractPage />);
    await screen.findByText("没有找到任务");
    expect(localStorage.getItem("last-extraction-record-v1")).toBeNull();
    expect(screen.queryByText("372", { selector: ".extract-header-value" })).not.toBeInTheDocument();
  });

  it("keeps the onboarding example read-only and opens its original without a fictitious task request", async () => {
    HTMLElement.prototype.scrollIntoView = vi.fn();
    const { container } = render(<ExtractPage demo="rule" />);
    const field = await screen.findByRole("button", { name: "查看字段 价税合计" });
    fireEvent.click(field);
    expect(container.querySelector('[contenteditable="true"]')).toBeNull();
    expect(screen.queryByRole("button", { name: "忽略" })).not.toBeInTheDocument();
    const opener = screen.getByRole("button", { name: "打开原文件全屏预览" });
    opener.focus();
    fireEvent.click(opener);
    const dialog = screen.getByRole("dialog", { name: "原文件全屏预览" });
    expect(dialog.textContent).toContain("服务费：1000.00 元");
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("onboarding-demo"))).toBe(false);
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it("keeps validation content mounted through collapse while removing hidden controls from navigation", async () => {
    HTMLElement.prototype.scrollIntoView = vi.fn();
    const { container } = render(<ExtractPage initialTask={task} />);
    const toggle = await screen.findByRole("button", { name: /校验结果/ });
    const body = container.querySelector(".validation-panel-body");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(container.querySelector("#extract-validation-content")).toHaveAttribute("inert");
    expect(container.querySelector(".validation-panel-body")).toBe(body);
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(container.querySelector("#extract-validation-content")).not.toHaveAttribute("inert");
  });

  it("paginates the real extraction editor and submits edits from multiple pages as one draft", async () => {
    const many = {...extraction, detail_ids: Array.from({length: 1000}, (_, index) => index + 500),
      result: {...result(372), items: Array.from({length: 1000}, (_, index) => ({name: `项目-${index + 1}`, quantity: 1, amount: 1}))}};
    let saved: {result: {items: {name: string}[]}; detail_ids: number[]} | undefined;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/templates")) return response([]);
      if (url.endsWith(`/tasks/${task.id}/result`)) return response(many);
      if (url.endsWith(`/tasks/${task.id}/review`) && init?.method === "PUT") {
        saved = JSON.parse(String(init.body));
        return response({...many, ...saved, review_version: 3});
      }
      throw new Error(`Unexpected request: ${url}`);
    }));
    render(<ExtractPage initialTask={task} />);
    const first = await screen.findByText("项目-1");
    expect(screen.getAllByRole("button", {name: /^明细 \d+ 操作$/})).toHaveLength(20);
    expect(screen.queryByText("项目-21")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", {name: /删除明细/})).not.toBeInTheDocument();
    fireEvent.click(first); first.textContent = "第一页修改"; fireEvent.blur(first);
    fireEvent.click(screen.getByRole("button", {name: "下一页明细"}));
    const next = await screen.findByText("项目-21");
    fireEvent.click(next); next.textContent = "第二页修改"; fireEvent.blur(next);
    const menu = screen.getByRole("button", {name: "明细 22 操作"});
    fireEvent.keyDown(menu, {key: "Enter"});
    fireEvent.click(await screen.findByRole("menuitem", {name: "删除此明细"}));
    expect(screen.queryByText("项目-22")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {name: "上一页明细"}));
    expect(await screen.findByText("第一页修改")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {name: "修订到表"}));
    await waitFor(() => expect(saved).toBeDefined());
    expect(saved?.result.items).toHaveLength(999);
    expect(saved?.result.items[0].name).toBe("第一页修改");
    expect(saved?.result.items[20].name).toBe("第二页修改");
    expect(saved?.detail_ids).not.toContain(521);
    expect(saved?.detail_ids).toHaveLength(999);
  });

  it("uses the native picker ticket without another confirmation or raw source path", async () => {
    const picked = { token: "a".repeat(64), name: "invoice.png", size: 1024, type: "image/png" };
    const pick = vi.fn().mockResolvedValue([picked]);
    window.pywebview = { api: { choose_import_files: pick } as unknown as NonNullable<NonNullable<Window["pywebview"]>["api"]> };
    const fetchMock = vi.fn().mockImplementation(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/templates")) return response([]);
      if (url.endsWith("/tasks/native-import") && init?.method === "POST") return response({ ...task, status: "queued" });
      if (url.includes("/tasks?")) return response([{ ...task, status: "queued" }]);
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    try {
      render(<ExtractPage />);
      fireEvent.click(screen.getByRole("button", { name: "选择文件" }));
      await waitFor(() => expect(pick).toHaveBeenCalledOnce());
      await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/tasks/native-import"))).toBe(true));
      const request = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/tasks/native-import"));
      expect(JSON.parse(String(request?.[1]?.body))).toEqual({ token: picked.token, template_mode: "smart", template_id: null, target_table_id: null, duplicate_policy: "reject" });
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    } finally { delete window.pywebview; }
  });

  it("skips an accidental duplicate without retrying and keeps the other batch file", async () => {
    const policies: string[] = [];
    const uploaded = { ...task, id: "new-batch-file", filename: "new.png", status: "queued" };
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/templates")) return response([]);
      if (url.includes("/tasks?")) return response([uploaded]);
      if (url.endsWith("/tasks") && init?.method === "POST") {
        const data = init.body as FormData;
        policies.push(String(data.get("duplicate_policy")));
        return (data.get("file") as File).name === "existing.png"
          ? new Response(JSON.stringify({ detail: { code: "duplicate_upload", message: "再次提取会重复生成数据", duplicate_task_id: task.id, duplicate_status: "completed" } }), { status: 409 })
          : response(uploaded);
      }
      throw new Error(`Unexpected request: ${url}`);
    }));
    render(<ExtractPage />);
    fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [new File(["old"], "existing.png"), new File(["new"], "new.png")] } });
    expect(await screen.findByRole("dialog", { name: "这份文件已经导入" })).toHaveTextContent("existing.png");
    await waitFor(() => expect(screen.getByRole("button", { name: "跳过此文件" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "跳过此文件" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(policies).toEqual(["reject", "reject"]);
    expect(await screen.findByText("队列中 1 个任务")).toBeInTheDocument();
  });

  it("confirms a native duplicate only with its replacement single-use ticket", async () => {
    const bodies: Array<Record<string, unknown>> = [];
    const original = "a".repeat(64), replacement = "b".repeat(64);
    window.pywebview = { api: { choose_import_files: vi.fn().mockResolvedValue([{ token: original, name: "invoice.png", size: 1024, type: "image/png" }]) } as unknown as NonNullable<NonNullable<Window["pywebview"]>["api"]> };
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/templates")) return response([]);
      if (url.includes("/tasks?")) return response([{ ...task, status: "queued" }]);
      if (url.endsWith("/tasks/native-import")) {
        bodies.push(JSON.parse(String(init?.body)));
        if (bodies.length === 1) return new Response(JSON.stringify({ detail: { code: "duplicate_upload", message: "再次提取会重复生成数据", duplicate_task_id: task.id, duplicate_status: "completed", retry_token: replacement } }), { status: 409 });
        return response({ ...task, status: "queued" });
      }
      throw new Error(`Unexpected request: ${url}`);
    }));
    try {
      render(<ExtractPage />);
      fireEvent.click(screen.getByRole("button", { name: "选择文件" }));
      await screen.findByRole("dialog", { name: "这份文件已经导入" });
      await waitFor(() => expect(screen.getByRole("button", { name: "再次提取" })).toBeEnabled());
      expect(bodies).toHaveLength(1);
      fireEvent.click(screen.getByRole("button", { name: "再次提取" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(bodies.map(body => [body.token, body.duplicate_policy])).toEqual([[original, "reject"], [replacement, "allow"]]);
    } finally { delete window.pywebview; }
  });

  it("shows honest page-only evidence and saves the edited draft", async () => {
    const scroll = vi.fn();
    HTMLElement.prototype.scrollIntoView = scroll;
    render(<ExtractPage initialTask={task} />);

    const amount = await screen.findByText("372", { selector: ".extract-header-value" });
    expect(screen.getByText("手动 · 发票")).toBeInTheDocument();
    await waitFor(() => expect(scroll).toHaveBeenCalledOnce());
    fireEvent.change(screen.getByRole("combobox", { name: "提取模板" }), { target: { value: "smart" } });
    expect(screen.getByText("手动 · 发票")).toBeInTheDocument();
    fireEvent.click(amount);
    expect(screen.getByText("仅确认来自第 1 页，暂无可靠区域")).toBeInTheDocument();

    amount.textContent = "373";
    fireEvent.blur(amount);
    fireEvent.click(screen.getByRole("button", { name: "修订到表" }));

    await waitFor(() => {
      const fetchMock = vi.mocked(fetch);
      const saveCall = fetchMock.mock.calls.find(
        ([url, init]) => String(url).endsWith(`/tasks/${task.id}/review`) && init?.method === "PUT",
      );
      expect(saveCall).toBeDefined();
      const body = JSON.parse(String(saveCall?.[1]?.body));
      expect(body.expected_version).toBe(2);
      expect(body.result.total_amount).toBe(373);
    });
  });

  it.each(["1,23", "0x10", "   ", "1.0000000000000001", "9007199254740991.1"])(
    "keeps invalid numeric review text %j and prevents saving it",
    async (input) => {
      const fetchMock = vi.fn(async (url: string | URL, _init?: RequestInit) => {
        const path = String(url);
        if (path.includes("/templates")) return response([]);
        if (path.endsWith(`/tasks/${task.id}/result`)) return response(extraction);
        throw new Error(path);
      });
      vi.stubGlobal("fetch", fetchMock);
      render(<ExtractPage initialTask={task} />);

      const amount = await screen.findByText("372", { selector: ".extract-header-value" });
      amount.textContent = input;
      fireEvent.blur(amount);
      fireEvent.click(screen.getByRole("button", { name: "修订到表" }));

      expect(await screen.findByRole("alert")).toHaveTextContent(/标准数字|精度/);
      expect(amount.textContent).toBe(input);
      expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith(`/tasks/${task.id}/review`) && init?.method === "PUT")).toBe(false);
    },
  );

  it("sends unsafe integer review values as decimal strings without rounding", async () => {
    let sentValue: unknown;
    vi.stubGlobal("fetch", vi.fn(async (url: string | URL, init?: RequestInit) => {
      const path = String(url);
      if (path.endsWith("/templates")) return response([]);
      if (path.endsWith(`/tasks/${task.id}/result`)) return response({
        ...extraction,
        result: resultWire("9007199254740991"),
        original_result: resultWire("9007199254740991"),
      });
      if (path.endsWith(`/tasks/${task.id}/review`) && init?.method === "PUT") {
        sentValue = JSON.parse(String(init.body)).result.total_amount;
        return response({ ...extraction, review_version: 3, result: resultWire("9007199254740993") });
      }
      throw new Error(path);
    }));
    render(<ExtractPage initialTask={task} />);

    const amount = await screen.findByText("9007199254740991", { selector: ".extract-header-value" });
    amount.textContent = "9007199254740993";
    fireEvent.blur(amount);
    fireEvent.click(screen.getByRole("button", { name: "修订到表" }));

    await waitFor(() => expect(sentValue).toBe("9007199254740993"));
  });

  it("retains an invalid field when another numeric field is corrected and blocks save", async () => {
    const fetchMock = vi.fn(async (url: string | URL, _init?: RequestInit) => {
      const path = String(url);
      if (path.includes("/templates")) return response([]);
      if (path.endsWith(`/tasks/${task.id}/result`)) return response(extraction);
      throw new Error(path);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ExtractPage initialTask={task} />);

    const total = await screen.findByText("372", { selector: ".extract-header-value" });
    total.textContent = "1,23";
    fireEvent.blur(total);
    const tax = await screen.findByText("21.06", { selector: ".extract-header-value" });
    tax.textContent = "22.06";
    fireEvent.blur(tax);

    expect(screen.getByText("1,23", { selector: ".extract-header-value" })).toBeInTheDocument();
    expect(screen.getByText("22.06", { selector: ".extract-header-value" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "修订到表" }));
    expect(fetchMock.mock.calls.filter(([url, init]) => String(url).endsWith(`/tasks/${task.id}/review`) && init?.method === "PUT")).toEqual([]);
  });

  it("warns before leaving a dirty review, keeps the draft on cancel, and proceeds only after discard", async () => {
    const proceeded = vi.fn();
    const onNavigateHistory = () => {
      const proceed = () => proceeded();
      if (window.dispatchEvent(new CustomEvent("zhiyi:before-navigate", { cancelable: true, detail: { proceed } }))) proceed();
    };
    render(<ExtractPage initialTask={task} onNavigateHistory={onNavigateHistory} />);
    const amount = await screen.findByText("372", { selector: ".extract-header-value" });
    amount.textContent = "373";
    fireEvent.blur(amount);

    fireEvent.click(screen.getByRole("button", { name: "文件历史" }));
    let dialog = await screen.findByRole("dialog", { name: "修改尚未保存" });
    expect(proceeded).not.toHaveBeenCalled();
    fireEvent.click(dialog.querySelector("button.btn.secondary")!);
    expect(screen.getByText("373", { selector: ".extract-header-value" })).toBeInTheDocument();
    expect(proceeded).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "文件历史" }));
    dialog = await screen.findByRole("dialog", { name: "修改尚未保存" });
    fireEvent.click(screen.getByRole("button", { name: "放弃修改并继续" }));
    expect(proceeded).toHaveBeenCalledOnce();
  });

  it("guards upload-based file replacement and discards the old draft only after confirmation", async () => {
    let uploadCalls = 0;
    const uploadedTask = { ...task, id: "replacement-task", filename: "replacement.png", status: "queued" };
    const fetchMock = vi.fn(async (url: string | URL, init?: RequestInit) => {
      const path = String(url);
      if (path.includes("/templates")) return response([]);
      if (path.endsWith(`/tasks/${task.id}/result`)) return response(extraction);
      if (path.endsWith("/tasks") && init?.method === "POST") { uploadCalls += 1; return response(uploadedTask); }
      if (path.includes("/tasks?")) return response([]);
      throw new Error(path);
    });
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(<ExtractPage initialTask={task} />);
    const amount = await screen.findByText("372", { selector: ".extract-header-value" });
    amount.textContent = "373";
    fireEvent.blur(amount);
    const input = container.querySelector('input[type="file"]')!;

    fireEvent.change(input, { target: { files: [new File(["new"], "first-upload.png", { type: "image/png" })] } });
    let dialog = await screen.findByRole("dialog", { name: "修改尚未保存" });
    expect(uploadCalls).toBe(0);
    fireEvent.click(dialog.querySelector("button.btn.secondary")!);
    expect(screen.getByText("373", { selector: ".extract-header-value" })).toBeInTheDocument();
    expect(uploadCalls).toBe(0);

    fireEvent.change(input, { target: { files: [new File(["new"], "second-upload.png", { type: "image/png" })] } });
    dialog = await screen.findByRole("dialog", { name: "修改尚未保存" });
    fireEvent.click(screen.getByRole("button", { name: "放弃修改并继续" }));
    await waitFor(() => expect(uploadCalls).toBe(1));
    expect(screen.queryByText("373", { selector: ".extract-header-value" })).not.toBeInTheDocument();
  });

  it("refreshes on task events and shows the extraction result automatically", async () => {
    const fetchMock = vi.fn().mockImplementation(
      async (input: string | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/templates")) return response([]);
        if (url.endsWith("/tasks") && init?.method === "POST") {
          return response({ ...task, status: "queued" });
        }
        if (url.includes("/tasks?") && !init?.method) {
          return response([{ ...task, status: "completed", updated_at: "2026-08-01T00:00:01Z" }]);
        }
        if (url.endsWith(`/tasks/${task.id}/result`)) return response(extraction);
        throw new Error(`Unexpected request: ${url} ${init?.method ?? ""}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<ExtractPage />);

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["fake"], "invoice.png", { type: "image/png" });
    fireEvent.change(input, { target: { files: [file] } });

    // 等批次渲染完成（SSE 监听已注册）后再触发任务完成事件
    expect(await screen.findByText("队列中 1 个任务", {}, { timeout: 3000 })).toBeInTheDocument();
    await waitFor(() => expect(taskListeners.length).toBeGreaterThan(0));
    act(() => {
      triggerTaskEvent();
    });
    expect(
      await screen.findByText("372", { selector: ".extract-header-value" }, { timeout: 4000 }),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/tasks"),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("keeps listening while paused and refreshes after resume completes", async () => {
    let pollCount = 0;
    const fetchMock = vi.fn().mockImplementation(
      async (input: string | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/templates")) return response([]);
        if (url.endsWith("/tasks") && init?.method === "POST") {
          return response({ ...task, status: "paused", updated_at: "2026-08-01T00:00:01Z" });
        }
        if (url.includes("/tasks?") && !init?.method) {
          pollCount += 1;
          return response([
            pollCount === 1
              ? { ...task, status: "paused", updated_at: "2026-08-01T00:00:01Z" }
              : { ...task, status: "completed", updated_at: "2026-08-01T00:00:02Z" },
          ]);
        }
        if (url.endsWith(`/tasks/${task.id}/result`)) return response(extraction);
        throw new Error(`Unexpected request: ${url} ${init?.method ?? ""}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<ExtractPage />);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [new File(["fake"], "invoice.png", { type: "image/png" })] },
    });

    expect(await screen.findByText("已暂停", {}, { timeout: 3000 })).toBeInTheDocument();
    // 暂停事件先刷新并保持监听；恢复后的完成事件再次刷新并展示结果。
    await waitForTaskListener();
    act(() => triggerTaskEvent());
    act(() => triggerTaskEvent());
    expect(
      await screen.findByText("372", { selector: ".extract-header-value" }, { timeout: 4000 }),
    ).toBeInTheDocument();
  });

  it("notifies the global task bar immediately after upload, before the result arrives", async () => {
    const fetchMock = vi.fn().mockImplementation(
      async (input: string | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/templates")) return response([]);
        if (url.endsWith("/tasks") && init?.method === "POST") {
          return response({ ...task, status: "queued" });
        }
        if (url.includes("/tasks?") && !init?.method) {
          return response([{ ...task, status: "completed", updated_at: "2026-08-01T00:00:01Z" }]);
        }
        if (url.endsWith(`/tasks/${task.id}/result`)) return response(extraction);
        throw new Error(`Unexpected request: ${url} ${init?.method ?? ""}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const onTasksChange = vi.fn();

    render(<ExtractPage onTasksChange={onTasksChange} />);

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["fake"], "invoice.png", { type: "image/png" });
    fireEvent.change(input, { target: { files: [file] } });

    // 上传成功即触发一次全局任务条刷新（不用等 2.5s 轮询或 5s 轮询）
    await waitFor(() => {
      expect(onTasksChange).toHaveBeenCalled();
    });
  });

  it("never shows the previous file values under a newly uploaded source", async () => {
    const next = { ...task, id: "next-upload", filename: "next.png", status: "queued" };
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/templates")) return response([]);
      if (url.endsWith(`/tasks/${task.id}/result`)) return response(extraction);
      if (url.includes("/tasks?")) return response([next]);
      if (url.endsWith("/tasks") && init?.method === "POST") return response(next);
      throw new Error(`Unexpected request: ${url}`);
    }));
    render(<ExtractPage initialTask={task} />);
    expect(await screen.findByText("372", { selector: ".extract-header-value" })).toBeInTheDocument();
    fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [new File(["new"], "next.png")] } });
    await waitFor(() => expect(screen.queryByText("372", { selector: ".extract-header-value" })).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "修订到表" })).not.toBeInTheDocument();
    expect(screen.getByText("队列中 1 个任务")).toBeInTheDocument();
  });

  it.each(["failed", "completed"] as const)("keeps a consecutive upload's %s outcome from being replaced by the previous success", async (outcome) => {
    const first = { ...task, id: "previous-success", filename: "previous.txt", status: "queued" as const };
    const next = { ...task, id: "latest-upload", filename: "latest.png", status: "queued" as const };
    let uploadIndex = 0;
    let pollIndex = 0;
    const fetchMock = vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/templates")) return response([]);
      if (url.endsWith("/tasks") && init?.method === "POST") return response(uploadIndex++ === 0 ? first : next);
      if (url.includes("/tasks?")) {
        pollIndex += 1;
        const completedFirst = { ...first, status: "completed", updated_at: "2026-08-01T00:00:01Z" };
        return response(pollIndex === 1 ? [completedFirst] : [completedFirst, {
          ...next, status: outcome, updated_at: "2026-08-01T00:00:02Z",
          failure_message: outcome === "failed" ? "模型返回内容无效，请重试。" : null,
        }]);
      }
      if (url.endsWith("/tasks/previous-success/result")) return response({ ...extraction, task_id: first.id, result: result(371) });
      if (url.endsWith("/tasks/latest-upload/result")) return response({ ...extraction, task_id: next.id, result: result(372) });
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const history = vi.fn();
    render(<ExtractPage onNavigateHistory={history} />);
    const input = document.querySelector('input[type="file"]')!;
    fireEvent.change(input, { target: { files: [new File(["first"], "previous.txt")] } });
    await screen.findByText("队列中 1 个任务");
    await waitForTaskListener();
    act(() => triggerTaskEvent());
    await screen.findByText("371", { selector: ".extract-header-value" });
    fireEvent.change(input, { target: { files: [new File(["next"], "latest.png")] } });
    await waitFor(() => expect(screen.queryByText("371", { selector: ".extract-header-value" })).not.toBeInTheDocument());
    await screen.findByText("队列中 1 个任务");
    await waitForTaskListener();
    act(() => triggerTaskEvent());
    if (outcome === "failed") {
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent("latest.png · 提取失败");
      expect(alert).toHaveTextContent("模型返回内容无效，请重试。");
      expect(screen.queryByRole("button", { name: "修订到表" })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "查看文件历史并重试" }));
      expect(history).toHaveBeenCalledOnce();
    } else {
      expect(await screen.findByText("372", { selector: ".extract-header-value" })).toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    }
    expect(screen.queryByText("371", { selector: ".extract-header-value" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/tasks/previous-success/result"))).toHaveLength(1);
  });

  it("keeps a successful result and a sibling failure visible in the same upload batch", async () => {
    const good = { ...task, id: "mixed-good", filename: "good.txt", status: "queued" as const };
    const bad = { ...task, id: "mixed-bad", filename: "bad.png", status: "queued" as const };
    let index = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/templates")) return response([]);
      if (url.endsWith("/tasks") && init?.method === "POST") return response(index++ === 0 ? good : bad);
      if (url.includes("/tasks?")) return response([
        { ...good, status: "completed", updated_at: "2026-08-01T00:00:01Z" },
        { ...bad, status: "failed", updated_at: "2026-08-01T00:00:02Z", failure_message: "图片理解失败。" },
      ]);
      if (url.endsWith("/tasks/mixed-good/result")) return response({ ...extraction, task_id: good.id });
      throw new Error(`Unexpected request: ${url}`);
    }));
    render(<ExtractPage />);
    fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [new File(["good"], "good.txt"), new File(["bad"], "bad.png")] } });
    await screen.findByText("队列中 2 个任务");
    await waitForTaskListener();
    act(() => triggerTaskEvent());
    expect(await screen.findByText("372", { selector: ".extract-header-value" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("bad.png · 提取失败");
    expect(screen.getByRole("alert")).toHaveTextContent("图片理解失败。");
    expect(screen.getByText("good.txt")).toBeInTheDocument();
  });

  it("keeps the latest completed batch result visible and replaces it when the next finishes", async () => {
    let uploadIndex = 0;
    let pollIndex = 0;
    const first = { ...task, id: "batch-first", filename: "第一份.png", status: "queued" as const };
    const second = { ...task, id: "batch-second", filename: "第二份.png", status: "queued" as const };
    const fetchMock = vi.fn().mockImplementation(
      async (input: string | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/templates")) return response([]);
        if (url.endsWith("/tasks") && init?.method === "POST") {
          return response(uploadIndex++ === 0 ? first : second);
        }
        if (url.includes("/tasks?") && !init?.method) {
          pollIndex += 1;
          return response(
            pollIndex <= 2
              ? [{ ...second, status: "queued" }, { ...first, status: "completed", updated_at: "2026-08-01T00:00:01Z" }]
              : [{ ...second, status: "completed", updated_at: "2026-08-01T00:00:02Z" }, { ...first, status: "completed" }],
          );
        }
        if (url.endsWith("/tasks/batch-first/result")) {
          return response({ ...extraction, task_id: "batch-first", result: result(371) });
        }
        if (url.endsWith("/tasks/batch-second/result")) {
          return response({ ...extraction, task_id: "batch-second", result: result(372) });
        }
        throw new Error(`Unexpected request: ${url} ${init?.method ?? ""}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<ExtractPage />);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, {
      target: {
        files: [
          new File(["one"], "第一份.png", { type: "image/png" }),
          new File(["two"], "第二份.png", { type: "image/png" }),
        ],
      },
    });

    expect(await screen.findByText("队列中 2 个任务", {}, { timeout: 3000 })).toBeInTheDocument();
    // 事件 1：第一份完成 → 展示第一份结果
    await waitForTaskListener();
    const firstSubscription = taskSubscriptionCount;
    act(() => {
      triggerTaskEvent();
    });
    expect(await screen.findByText("371", { selector: ".extract-header-value" }, { timeout: 4000 })).toBeInTheDocument();
    await waitForResubscribedTaskListener(firstSubscription);
    expect(screen.getByText("第一份.png")).toBeInTheDocument();
    // 事件 2：批次无变化（第二份仍在排队），不重复展示
    act(() => {
      triggerTaskEvent();
    });
    // 事件 3：第二份完成 → 展示第二份结果（替换第一份）
    act(() => {
      triggerTaskEvent();
    });
    expect(await screen.findByText("372", { selector: ".extract-header-value" }, { timeout: 4000 })).toBeInTheDocument();
    const resultCalls = fetchMock.mock.calls.filter(([url]) => String(url).includes("/result"));
    expect(resultCalls).toHaveLength(2);
    expect(String(resultCalls[0]?.[0])).toContain("batch-first");
    expect(String(resultCalls[1]?.[0])).toContain("batch-second");
  }, 18_000);
});

function result(totalAmount: number) {
  return {
    document_type: "invoice",
    seller_name: "销售方",
    buyer_name: "购买方",
    document_number: "INV-001",
    document_date: "2026-08-01",
    amount_before_tax: 350.94,
    tax_amount: 21.06,
    total_amount: totalAmount,
    items: [],
  };
}

function resultWire(totalAmount: string | number) {
  return { ...result(372), total_amount: totalAmount };
}

function response(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}
