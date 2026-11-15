import { PendingDuplicateDialog } from "./PendingDuplicateDialog";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
  it("shows an unread template pool as unknown and retries without reloading the page", async () => {
    let failed = true;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      if (String(input).includes("/templates")) return failed
        ? new Response(JSON.stringify({ detail: "合成模板读取失败" }), { status: 503 })
        : response([invoiceTemplate]);
      return response([]);
    }));
    render(<ExtractPage />);
    fireEvent.click(await screen.findByRole("button", { name: "查看提取事项" }));
    expect(screen.getByRole("dialog", { name: "提取事项" })).toHaveTextContent("合成模板读取失败");
    expect(screen.queryByText("尚未选择普通模板")).not.toBeInTheDocument();
    expect(screen.getByText("匹配范围待确认")).toBeInTheDocument();
    failed = false;
    fireEvent.click(screen.getByRole("button", { name: "重新读取模板" }));
    expect(await screen.findByRole("option", { name: "发票" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText(/1 个模板/)).toBeInTheDocument();
  });

  it("keeps the pool read in the modal when the earlier page read returns late", async () => {
    let release!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      if (String(input).endsWith("/templates")) return new Promise<Response>(resolve => { release = resolve; });
      if (String(input).includes("/templates?")) return response([invoiceTemplate]);
      return response([]);
    }));
    render(<ExtractPage />);
    fireEvent.click(screen.getByRole("button", { name: "匹配范围" }));
    await screen.findByRole("checkbox", { name: /发票/ });
    fireEvent.click(screen.getByRole("button", { name: "完成" }));
    await act(async () => release(response([])));
    expect(screen.getByRole("option", { name: "发票" })).toBeInTheDocument();
    expect(screen.getByText(/1 个模板/)).toBeInTheDocument();
  });

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
  it("reveals the correct detail page before locating a validation field", async () => {
    const many = { ...extraction, result: { ...extraction.result, items: Array.from({ length: 25 }, (_, index) => ({
      name: `项目-${index + 1}`, quantity: 1, amount: index + 100,
    })) }, validation_issues: [{ code: "mismatch", field: "items[24].amount", message: "请核对金额", severity: "warning" }] };
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => String(input).endsWith("/templates") ? response([]) : response(many)));
    render(<ExtractPage initialTask={task} />);
    fireEvent.click(await screen.findByRole("button", { name: "编辑相关字段" }));
    expect(await screen.findByText("项目-25")).toBeInTheDocument();
    await waitFor(() => expect(document.activeElement).toHaveTextContent("124"));
  });
  it("preserves unsaved values after a review conflict and canceled reread", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      if (String(input).endsWith("/templates")) return response([]);
      if (init?.method === "PUT") return new Response(JSON.stringify({ detail: "数据已有更新" }), { status: 409 });
      return response(extraction);
    }));
    render(<ExtractPage initialTask={task} />);
    const amount = await screen.findByText("372", { selector: ".extract-header-value" });
    fireEvent.click(amount); amount.textContent = "373"; fireEvent.blur(amount);
    fireEvent.click(screen.getByRole("button", { name: "修订到表" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("修改未保存");
    fireEvent.click(screen.getByRole("button", { name: "处理冲突" }));
    expect(screen.getByRole("dialog", { name: "修订冲突" })).toHaveTextContent("数据已有更新，本次修改尚未保存");
    fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
    fireEvent.click(within(screen.getByRole("dialog", { name: "修改尚未保存" })).getByRole("button", { name: "取消" }));
    expect(screen.getByText("373", { selector: ".extract-header-value" })).toBeInTheDocument();
  });
  it("does not attach an old conflict reread action to a new file read failure", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/templates")) return response([]);
      if (url.includes("new-file/result")) throw new Error("新文件暂时无法读取");
      if (init?.method === "PUT") return new Response(JSON.stringify({ detail: "数据已有更新" }), { status: 409 });
      return response(extraction);
    }));
    const { rerender } = render(<ExtractPage initialTask={task} />);
    const amount = await screen.findByText("372", { selector: ".extract-header-value" });
    fireEvent.click(amount); amount.textContent = "373"; fireEvent.blur(amount);
    fireEvent.click(screen.getByRole("button", { name: "修订到表" }));
    fireEvent.click(await screen.findByRole("button", { name: "处理冲突" }));
    rerender(<ExtractPage initialTask={{ ...task, id: "new-file", filename: "new-file.png" }} />);
    fireEvent.click(await screen.findByRole("button", { name: "查看提取事项" }));
    expect(screen.getByRole("dialog", { name: "提取事项" })).toHaveTextContent("新文件暂时无法读取");
    expect(screen.queryByRole("button", { name: "重新读取" })).not.toBeInTheDocument();
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
    expect(screen.queryByText("已保存")).not.toBeInTheDocument();
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

  it("restores older batch tasks by ID without treating unrelated recent tasks as the batch", async () => {
    const old = { ...task, id: "older-than-latest-200", filename: "旧批次.pdf", status: "paused" };
    localStorage.setItem("extract-batch-queue-v1", JSON.stringify([old.id, "deleted-task"]));
    const requests: URL[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname.endsWith("/templates")) return response([]);
      if (url.pathname.endsWith("/tasks")) {
        requests.push(url);
        return response(url.searchParams.getAll("task_ids").includes(old.id) ? [old] : []);
      }
      throw new Error(String(url));
    }));
    render(<ExtractPage />);
    fireEvent.click(await screen.findByRole("button", { name: "队列中 1 个任务" }));
    expect(screen.getByRole("dialog", { name: "提取事项" })).toHaveTextContent("旧批次.pdf");
    await waitFor(() => expect(JSON.parse(localStorage.getItem("extract-batch-queue-v1")!)).toEqual([old.id]));
    expect(requests.length).toBeGreaterThan(0);
    expect(requests.every(url => url.searchParams.getAll("task_ids").includes(old.id))).toBe(true);
  });

  it("preserves remembered IDs after a failed read and offers a queue retry", async () => {
    localStorage.setItem("extract-batch-queue-v1", JSON.stringify([task.id]));
    let failed = true;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      const url = String(input);
      if (url.includes("/templates")) return response([]);
      if (url.includes("/tasks?")) return failed
        ? new Response(JSON.stringify({ detail: "合成队列暂不可达" }), { status: 503 })
        : response([task]);
      throw new Error(url);
    }));
    render(<ExtractPage />);
    fireEvent.click(await screen.findByRole("button", { name: "查看提取事项" }));
    expect(screen.getByRole("dialog", { name: "提取事项" })).toHaveTextContent("合成队列暂不可达");
    expect(screen.getByText("队列状态待确认")).toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem("extract-batch-queue-v1")!)).toEqual([task.id]);
    failed = false;
    fireEvent.click(screen.getByRole("button", { name: "重新读取队列" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(screen.queryByText("队列空闲")).not.toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem("extract-batch-queue-v1")!)).toEqual([task.id]);
  });

  it("does not let a late queue restoration replace a newly uploaded batch", async () => {
    localStorage.setItem("extract-batch-queue-v1", JSON.stringify(["old-task"]));
    let release!: (value: Response) => void;
    const uploaded = { ...task, id: "fresh-upload", filename: "新文件.pdf", status: "paused" };
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname.endsWith("/templates")) return response([]);
      if (url.pathname.endsWith("/tasks") && init?.method === "POST") return response(uploaded);
      if (url.pathname.endsWith("/tasks")) {
        if (url.searchParams.getAll("task_ids").includes("old-task")) return new Promise<Response>(resolve => { release = resolve; });
        return response([uploaded]);
      }
      throw new Error(String(url));
    }));
    const { container } = render(<ExtractPage />);
    fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [new File(["new"], "新文件.pdf", { type: "application/pdf" })] } });
    fireEvent.click(await screen.findByRole("button", { name: "队列中 1 个任务" }));
    expect(screen.getByRole("dialog", { name: "提取事项" })).toHaveTextContent("新文件.pdf");
    await act(async () => release(response([{ ...task, id: "old-task", filename: "旧文件.pdf", status: "paused" }])));
    expect(screen.getByRole("dialog", { name: "提取事项" })).not.toHaveTextContent("旧文件.pdf");
    expect(JSON.parse(localStorage.getItem("extract-batch-queue-v1")!)).toEqual([uploaded.id]);
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
    await waitFor(() => expect(localStorage.getItem("last-extraction-record-v1")).toBeNull());
    expect(screen.queryByText("没有找到任务")).not.toBeInTheDocument();
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

  it("shows each problem with its own correction, including non-date fields", async () => {
    const fields = [
      { key: "date", label: "复盘日期", value_type: "date" },
      { key: "amount", label: "金额", value_type: "number" },
      { key: "approved", label: "已批准", value_type: "boolean" },
      { key: "note", label: "备注", value_type: "text" },
    ] as const;
    const headerFields = fields.map(field => ({ ...field, section: "header" as const, example: "", instructions: "" }));
    const values = { header: { date: null, amount: null, approved: null, note: null }, items: [] };
    const data: Extraction = {
      ...extraction, document_kind: "custom", current_schema: headerFields, result: values, original_result: values,
      template: {
        ...invoiceTemplate,
        fields: headerFields,
        deterministic_rules: [{ kind: "pattern", field: "header.note", pattern: "^[A-Z][0-9]{3}$" }],
      },
      validation_issues: [
        ...["下周五", "10–20", "也许", true].map((raw_value, index) => ({
          code: "autonomous_field_type", field: `header.${fields[index].key}`,
          message: "请核对字段。", severity: "warning" as const, raw_value,
        })),
        { code: "range", field: "header.amount", message: "金额不能小于 0。", severity: "warning" },
        { code: "template_rule_1", field: "header.note", message: "备注格式不符合要求。", severity: "warning" },
      ],
    };
    let saved: { result: typeof values } | undefined;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      if (String(input).endsWith("/templates")) return response([]);
      if (init?.method === "PUT") {
        saved = JSON.parse(String(init.body));
        return response({ ...data, ...saved, review_version: 3 });
      }
      return response(data);
    }));
    render(<ExtractPage initialTask={{ ...task, status: "needs_review" }} />);
    const panel = await screen.findByRole("region", { name: "校验结果" });
    const issues = within(panel).getAllByRole("listitem");
    expect(issues).toHaveLength(6);
    expect(issues[0]).toHaveTextContent("复盘日期 · 提醒 · 模型原值「下周五」 · 请按原件填写完整日期（如 2026-11-20）。");
    expect(issues[1]).toHaveTextContent("填写数字");
    expect(issues[2]).toHaveTextContent("填写「是」或「否」");
    expect(issues[3]).toHaveTextContent("保留编号前导零等原有格式");
    expect(issues[4]).toHaveTextContent("金额不能小于 0。");
    expect(issues[5]).toHaveTextContent("需符合模板格式「^[A-Z][0-9]{3}$」");
    const value = within(screen.getByRole("button", { name: "编辑字段 已批准" })).getByText("", { selector: ".extract-header-value" });
    fireEvent.click(value); value.textContent = "否"; fireEvent.blur(value);
    fireEvent.click(screen.getByRole("button", { name: /^(修订到表|保存)$/ }));
    await waitFor(() => expect(saved?.result.header.approved).toBe(false));
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
    const onDuplicate = vi.fn();
    render(<ExtractPage onDuplicate={onDuplicate} />);
    fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [new File(["old"], "existing.png"), new File(["new"], "new.png")] } });
    await waitFor(() => expect(onDuplicate).toHaveBeenCalledWith(expect.objectContaining({
      name: "existing.png", existingTaskId: task.id,
    })));
    expect(policies).toEqual(["reject", "reject"]);
    expect(await screen.findByRole("button", { name: "队列中 1 个任务" })).toBeInTheDocument();
  });

  it("confirms a native duplicate by reusing the stored original", async () => {
    const bodies: Array<Record<string, unknown>> = [];
    const original = "a".repeat(64);
    window.pywebview = { api: { choose_import_files: vi.fn().mockResolvedValue([{ token: original, name: "invoice.png", size: 1024, type: "image/png" }]) } as unknown as NonNullable<NonNullable<Window["pywebview"]>["api"]> };
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/templates")) return response([]);
      if (url.includes("/tasks?")) return response([{ ...task, status: "queued" }]);
      if (url.endsWith("/tasks/native-import")) {
        bodies.push(JSON.parse(String(init?.body)));
        return new Response(JSON.stringify({ detail: { code: "duplicate_upload", message: "再次提取会重复生成数据", duplicate_task_id: task.id, duplicate_status: "completed" } }), { status: 409 });
      }
      if (url.includes("/tasks/pending-duplicates/") && url.endsWith("/extract")) return response({ ...task, status: "queued" });
      throw new Error(`Unexpected request: ${url}`);
    }));
    try {
      const onDuplicate = vi.fn();
      render(<ExtractPage onDuplicate={onDuplicate} />);
      fireEvent.click(screen.getByRole("button", { name: "选择文件" }));
      await waitFor(() => expect(onDuplicate).toHaveBeenCalledWith(expect.objectContaining({
        name: "invoice.png", existingTaskId: task.id,
      })));
      const item = onDuplicate.mock.calls[0][0];
      const queued = vi.fn();
      render(<PendingDuplicateDialog item={item} onClose={vi.fn()} onSkip={vi.fn()} onQueued={queued}
        onBeforeRetry={async () => "d".repeat(64)} onView={vi.fn()} />);
      await waitFor(() => expect(screen.getByRole("button", { name: "再次提取" })).toBeEnabled());
      expect(bodies).toHaveLength(1);
      fireEvent.click(screen.getByRole("button", { name: "再次提取" }));
      await waitFor(() => expect(queued).toHaveBeenCalledOnce());
      expect(bodies.map(body => [body.token, body.duplicate_policy])).toEqual([[original, "reject"]]);
    } finally { delete window.pywebview; }
  });

  it("shows saved feedback only after the response and clears it on editing", async () => {
    let finishSave!: (value: Response) => void;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/templates")) return response([]);
      if (url.endsWith(`/tasks/${task.id}/result`)) return response(extraction);
      if (url.endsWith(`/tasks/${task.id}/review`) && init?.method === "PUT") {
        return new Promise<Response>(resolve => { finishSave = resolve; });
      }
      throw new Error(url);
    }));
    render(<ExtractPage initialTask={task} />);
    await screen.findByText("372", { selector: ".extract-header-value" });
    fireEvent.click(screen.getByRole("button", { name: "修订到表" }));
    expect(screen.queryByText("已保存")).not.toBeInTheDocument();
    await act(async () => finishSave(response({ ...extraction, review_version: 3 })));
    expect(await screen.findByText("已保存")).toHaveAttribute("role", "status");
    fireEvent.click(screen.getByRole("button", { name: "编辑字段 价税合计" }));
    expect(screen.queryByText("已保存")).not.toBeInTheDocument();
  });

  it("clears saved feedback when switching files", async () => {
    const nextTask = { ...task, id: "next-saved-file", filename: "next.png" };
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/templates")) return response([]);
      if (url.endsWith(`/tasks/${nextTask.id}/result`)) return response({ ...extraction, task_id: nextTask.id });
      if (url.endsWith(`/tasks/${task.id}/result`)) return response(extraction);
      if (url.endsWith(`/tasks/${task.id}/review`) && init?.method === "PUT") return response({ ...extraction, review_version: 3 });
      throw new Error(url);
    }));
    const view = render(<ExtractPage initialTask={task} />);
    await screen.findByText("372", { selector: ".extract-header-value" });
    fireEvent.click(screen.getByRole("button", { name: "修订到表" }));
    await screen.findByText("已保存");
    view.rerender(<ExtractPage initialTask={nextTask} />);
    expect(screen.queryByText("已保存")).not.toBeInTheDocument();
    await screen.findByText("next.png");
    expect(screen.queryByText("已保存")).not.toBeInTheDocument();
  });

  it("removes a previous success when the next save fails", async () => {
    let saves = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/templates")) return response([]);
      if (url.endsWith(`/tasks/${task.id}/result`)) return response(extraction);
      if (url.endsWith(`/tasks/${task.id}/review`) && init?.method === "PUT") {
        saves += 1;
        return saves === 1 ? response({ ...extraction, review_version: 3 })
          : new Response(JSON.stringify({ detail: "保存未完成，请重试" }), { status: 503 });
      }
      throw new Error(url);
    }));
    render(<ExtractPage initialTask={task} />);
    await screen.findByText("372", { selector: ".extract-header-value" });
    fireEvent.click(screen.getByRole("button", { name: "修订到表" }));
    await screen.findByText("已保存");
    fireEvent.click(screen.getByRole("button", { name: "修订到表" }));
    expect(screen.queryByText("已保存")).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "查看原因" }));
    expect(screen.getByRole("dialog", { name: "修改未保存" })).toHaveTextContent("保存未完成，请重试");
    expect(screen.queryByText("已保存")).not.toBeInTheDocument();
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
    "saves manual numeric exception %j as entered",
    async (input) => {
      let saved: unknown;
      const fetchMock = vi.fn(async (url: string | URL, init?: RequestInit) => {
        const path = String(url);
        if (path.includes("/templates")) return response([]);
        if (path.endsWith(`/tasks/${task.id}/result`)) return response(extraction);
        if (path.endsWith('/review') && init?.method === "PUT") {
          const body = JSON.parse(String(init.body));
          saved = body.result.total_amount;
          return response({...extraction, result: body.result});
        }
        throw new Error(path);
      });
      vi.stubGlobal("fetch", fetchMock);
      render(<ExtractPage initialTask={task} />);

      const amount = await screen.findByText("372", { selector: ".extract-header-value" });
      fireEvent.click(amount);
      expect(amount).toHaveAttribute("contenteditable", "true");
      amount.textContent = input;
      fireEvent.blur(amount);
      fireEvent.click(screen.getByRole("button", { name: "修订到表" }));

      await waitFor(() => expect(saved).toBe(input));
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
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
    fireEvent.click(amount);
    expect(amount).toHaveAttribute("contenteditable", "true");
    amount.textContent = "9007199254740993";
    fireEvent.blur(amount);
    fireEvent.click(screen.getByRole("button", { name: "修订到表" }));

    await waitFor(() => expect(sentValue).toBe("9007199254740993"));
  });

  it("saves text and numeric revisions together", async () => {
    let saved: Record<string, unknown> | undefined;
    const fetchMock = vi.fn(async (url: string | URL, init?: RequestInit) => {
      const path = String(url);
      if (path.includes("/templates")) return response([]);
      if (path.endsWith(`/tasks/${task.id}/result`)) return response(extraction);
      if (path.endsWith('/review') && init?.method === "PUT") {
        saved = JSON.parse(String(init.body)).result;
        return response({...extraction, result: saved});
      }
      throw new Error(path);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ExtractPage initialTask={task} />);

    const total = await screen.findByText("372", { selector: ".extract-header-value" });
    fireEvent.click(total);
    expect(total).toHaveAttribute("contenteditable", "true");
    total.textContent = "1,23";
    act(() => total.blur());
    const tax = await screen.findByText("21.06", { selector: ".extract-header-value" });
    fireEvent.click(tax);
    expect(tax).toHaveAttribute("contenteditable", "true");
    tax.textContent = "22.06";
    act(() => tax.blur());

    expect(screen.getByText("1,23", { selector: ".extract-header-value" })).toBeInTheDocument();
    expect(screen.getByText("22.06", { selector: ".extract-header-value" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "修订到表" }));
    await waitFor(() => expect(saved).toMatchObject({total_amount:"1,23", tax_amount:22.06}));
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
    let completed = false;
    const fetchMock = vi.fn().mockImplementation(
      async (input: string | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/templates")) return response([]);
        if (url.endsWith("/tasks") && init?.method === "POST") {
          return response({ ...task, status: "queued" });
        }
        if (url.includes("/tasks?") && !init?.method) {
          return response([{ ...task, status: completed ? "completed" : "queued", updated_at: completed ? "2026-08-01T00:00:01Z" : task.updated_at }]);
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
      completed = true;
      triggerTaskEvent();
    });
    await waitFor(() => {
      expect(screen.getByText("372", { selector: ".extract-header-value" })).toBeInTheDocument();
    }, { timeout: 4000 });
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/tasks"),
      expect.objectContaining({ method: "POST" }),
    );
  });
  it("rereads an automatically displayed task after repeated review conflicts", async () => {
    let resultReads = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/templates")) return response([]);
      if (url.endsWith("/tasks") && init?.method === "POST") return response({ ...task, status: "queued" });
      if (url.includes("/tasks?")) return response([{ ...task, status: "completed" }]);
      if (url.endsWith(`/tasks/${task.id}/result`)) {
        resultReads += 1;
        return response({ ...extraction, review_version: resultReads + 1, result: result([372, 400, 500][resultReads - 1] ?? 500) });
      }
      if (url.endsWith("/review")) return new Response(JSON.stringify({ detail: "数据已有更新" }), { status: 409 });
      throw new Error(url);
    }));
    render(<ExtractPage />);
    fireEvent.change(document.querySelector('input[type="file"]')!, {
      target: { files: [new File(["invoice"], "invoice.png", { type: "image/png" })] },
    });
    await screen.findByText("队列中 1 个任务");
    await waitForTaskListener();
    act(() => triggerTaskEvent());
    await screen.findByText("372", { selector: ".extract-header-value" });
    for (const [current, changed, latest, reads] of [[372, 373, 400, 2], [400, 401, 500, 3]]) {
      const amount = screen.getByText(String(current), { selector: ".extract-header-value" });
      fireEvent.click(amount); amount.textContent = String(changed); fireEvent.blur(amount);
      fireEvent.click(screen.getByRole("button", { name: "修订到表" }));
      fireEvent.click(await screen.findByRole("button", { name: "处理冲突" }));
      fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
      fireEvent.click(within(screen.getByRole("dialog", { name: "修改尚未保存" })).getByRole("button", { name: "放弃修改并继续" }));
      await waitFor(() => expect(resultReads).toBe(reads));
      expect(await screen.findByText(String(latest), { selector: ".extract-header-value" })).toBeInTheDocument();
    }
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

    fireEvent.click(await screen.findByRole("button", { name: "队列中 1 个任务" }));
    expect(screen.getByRole("dialog", { name: "提取事项" })).toHaveTextContent("已暂停");
    fireEvent.click(screen.getByRole("button", { name: "关闭提取事项" }));
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
    let firstFinished = false;
    let nextFinished = false;
    const fetchMock = vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/templates")) return response([]);
      if (url.endsWith("/tasks") && init?.method === "POST") return response(uploadIndex++ === 0 ? first : next);
      if (url.includes("/tasks?")) {
        const completedFirst = { ...first, status: "completed", updated_at: "2026-08-01T00:00:01Z" };
        const requested = new URL(url, "http://localhost").searchParams.getAll("task_ids");
        return response([firstFinished ? completedFirst : first, {
          ...next, status: nextFinished ? outcome : "queued", updated_at: "2026-08-01T00:00:02Z",
          failure_message: outcome === "failed" ? "模型返回内容无效，请重试。" : null,
        }].filter(item => requested.includes(item.id)));
      }
      if (url.endsWith("/tasks/previous-success/result")) return response({ ...extraction, task_id: first.id, result: result(371) });
      if (url.endsWith("/tasks/latest-upload/result")) return response({ ...extraction, task_id: next.id, result: result(372) });
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const openHistory = vi.fn();
    render(<ExtractPage onNavigateHistory={openHistory} />);
    const input = document.querySelector('input[type="file"]')!;
    fireEvent.change(input, { target: { files: [new File(["first"], "previous.txt")] } });
    await screen.findByText("队列中 1 个任务");
    await waitForTaskListener();
    firstFinished = true;
    act(() => triggerTaskEvent());
    await screen.findByText("371", { selector: ".extract-header-value" });
    fireEvent.change(input, { target: { files: [new File(["next"], "latest.png")] } });
    await waitFor(() => expect(screen.queryByText("371", { selector: ".extract-header-value" })).not.toBeInTheDocument());
    await screen.findByText("队列中 1 个任务");
    await waitForTaskListener();
    nextFinished = true;
    act(() => triggerTaskEvent());
    if (outcome === "failed") {
      await waitFor(() => expect(screen.queryByText("队列中 1 个任务")).not.toBeInTheDocument());
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "修订到表" })).not.toBeInTheDocument();
      expect(openHistory).not.toHaveBeenCalled();
    } else {
      expect(await screen.findByText("372", { selector: ".extract-header-value" })).toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    }
    expect(screen.queryByText("371", { selector: ".extract-header-value" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/tasks/previous-success/result"))).toHaveLength(1);
  });

  it("keeps a successful result visible when a sibling task fails", async () => {
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
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("good.txt")).toBeInTheDocument();
  });

  it("keeps the latest completed batch result visible and replaces it when the next finishes", async () => {
    let uploadIndex = 0;
    let completionStage = 0;
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
          const requested = new URL(url, "http://localhost").searchParams.getAll("task_ids");
          return response((
            completionStage === 0 ? [first, second] : completionStage === 1
              ? [{ ...second, status: "queued" }, { ...first, status: "completed", updated_at: "2026-08-01T00:00:01Z" }]
              : [{ ...second, status: "completed", updated_at: "2026-08-01T00:00:02Z" }, { ...first, status: "completed", updated_at: "2026-08-01T00:00:01Z" }]).filter(item => requested.includes(item.id)),
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
    completionStage = 1;
    act(() => {
      triggerTaskEvent();
    });
    expect(await screen.findByText("371", { selector: ".extract-header-value" }, { timeout: 4000 })).toBeInTheDocument();
    await waitForResubscribedTaskListener(firstSubscription);
    expect(screen.getByText("第一份.png", { selector: ".support span" })).toBeInTheDocument();
    // 事件 2：批次无变化（第二份仍在排队），不重复展示
    act(() => {
      triggerTaskEvent();
    });
    // 事件 3：第二份完成 → 展示第二份结果（替换第一份）
    await act(async () => {});
    completionStage = 2;
    act(() => {
      triggerTaskEvent();
    });
    expect(await screen.findByText("372", { selector: ".extract-header-value" }, { timeout: 4000 })).toBeInTheDocument();
    const resultCalls = fetchMock.mock.calls.filter(([url]) => String(url).includes("/result"));
    expect(resultCalls).toHaveLength(2);
    expect(String(resultCalls[0]?.[0])).toContain("batch-first");
    expect(String(resultCalls[1]?.[0])).toContain("batch-second");
  }, 18_000);

  it("keeps a dirty review when the next batch result completes and asks before switching", async () => {
    let uploadIndex = 0;
    let completionStage = 0;
    const first = { ...task, id: "dirty-first", filename: "先完成.png", status: "queued" as const };
    const second = { ...task, id: "dirty-second", filename: "后完成.png", status: "queued" as const };
    const fetchMock = vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/templates")) return response([]);
      if (url.endsWith("/tasks") && init?.method === "POST") return response(uploadIndex++ === 0 ? first : second);
      if (url.includes("/tasks?") && !init?.method) {
        return response(completionStage === 0 ? [first, second]
          : completionStage === 1 ? [{ ...first, status: "completed", updated_at: "2026-08-01T00:00:01Z" }, second]
            : [{ ...first, status: "completed", updated_at: "2026-08-01T00:00:01Z" }, { ...second, status: "completed", updated_at: "2026-08-01T00:00:02Z" }]);
      }
      if (url.endsWith(`/tasks/${first.id}/result`)) return response({ ...extraction, task_id: first.id, result: result(371) });
      if (url.endsWith(`/tasks/${second.id}/result`)) return response({ ...extraction, task_id: second.id, result: result(372) });
      throw new Error(`Unexpected request: ${url} ${init?.method ?? ""}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ExtractPage />);
    fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [new File(["one"], "first.png"), new File(["two"], "second.png")] } });
    await screen.findByText("队列中 2 个任务");
    await waitForTaskListener();
    const firstSubscription = taskSubscriptionCount;
    completionStage = 1;
    act(() => triggerTaskEvent());
    const firstAmount = await screen.findByText("371", { selector: ".extract-header-value" });
    await waitForResubscribedTaskListener(firstSubscription);

    fireEvent.click(firstAmount);
    firstAmount.textContent = "373";
    fireEvent.blur(firstAmount);
    expect(await screen.findByText("373", { selector: ".extract-header-value" })).toBeInTheDocument();

    const secondSubscription = taskSubscriptionCount;
    completionStage = 2;
    act(() => triggerTaskEvent());
    await screen.findByRole("button", { name: "新结果可查看" });
    expect(screen.getByText("373", { selector: ".extract-header-value" })).toBeInTheDocument();
    expect(screen.queryByText("372", { selector: ".extract-header-value" })).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "修改尚未保存" })).not.toBeInTheDocument();
    const secondResultReads = () => fetchMock.mock.calls.filter(([url]) => String(url).endsWith(`/tasks/${second.id}/result`)).length;
    expect(secondResultReads()).toBe(0);

    fireEvent.click(screen.getByRole("button", { name: "新结果可查看" }));
    fireEvent.click(screen.getByRole("button", { name: "查看结果" }));
    const confirmation = screen.getByRole("dialog", { name: "修改尚未保存" });
    expect(secondResultReads()).toBe(0);
    expect(screen.getByText("373", { selector: ".extract-header-value" })).toBeInTheDocument();
    fireEvent.click(confirmation.querySelector("button.btn.secondary")!);
    expect(screen.getByText("373", { selector: ".extract-header-value" })).toBeInTheDocument();
    expect(taskSubscriptionCount).toBeGreaterThanOrEqual(secondSubscription);
    fireEvent.click(screen.getByRole("button", { name: "新结果可查看" }));
    fireEvent.click(screen.getByRole("button", { name: "查看结果" }));
    const discardConfirmation = screen.getByRole("dialog", { name: "修改尚未保存" });
    fireEvent.click(discardConfirmation.querySelector("button.btn.primary")!);
    expect(await screen.findByText("372", { selector: ".extract-header-value" })).toBeInTheDocument();
    expect(secondResultReads()).toBe(1);
  }, 18_000);

  it("does not apply a late automatic result over an active contentEditable edit", async () => {
    let uploadIndex = 0;
    let completionStage = 0;
    let releaseSecond!: (value: Response) => void;
    let markSecondReadStarted!: () => void;
    const secondReadStarted = new Promise<void>(resolve => { markSecondReadStarted = resolve; });
    const first = { ...task, id: "late-first", filename: "先完成.png", status: "queued" as const };
    const second = { ...task, id: "late-second", filename: "后完成.png", status: "queued" as const };
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/templates")) return response([]);
      if (url.endsWith("/tasks") && init?.method === "POST") return response(uploadIndex++ === 0 ? first : second);
      if (url.includes("/tasks?") && !init?.method) {
        return response(completionStage === 0 ? [first, second]
          : completionStage === 1 ? [{ ...first, status: "completed", updated_at: "2026-08-01T00:00:01Z" }, second]
            : [{ ...first, status: "completed", updated_at: "2026-08-01T00:00:01Z" }, { ...second, status: "completed", updated_at: "2026-08-01T00:00:02Z" }]);
      }
      if (url.endsWith(`/tasks/${first.id}/result`)) return response({ ...extraction, task_id: first.id, result: result(371) });
      if (url.endsWith(`/tasks/${second.id}/result`)) {
        markSecondReadStarted();
        return new Promise<Response>(resolve => { releaseSecond = resolve; });
      }
      throw new Error(`Unexpected request: ${url} ${init?.method ?? ""}`);
    }));

    render(<ExtractPage />);
    fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [new File(["one"], "first.png"), new File(["two"], "second.png")] } });
    await screen.findByText("队列中 2 个任务");
    await waitForTaskListener();
    const firstSubscription = taskSubscriptionCount;
    completionStage = 1;
    act(() => triggerTaskEvent());
    const amount = await screen.findByText("371", { selector: ".extract-header-value" });
    await waitForResubscribedTaskListener(firstSubscription);

    completionStage = 2;
    act(() => triggerTaskEvent());
    await secondReadStarted;
    fireEvent.click(amount);
    expect(amount).toHaveAttribute("contenteditable", "true");
    amount.textContent = "373";
    expect(amount).toHaveFocus();

    await act(async () => {
      releaseSecond(response({ ...extraction, task_id: second.id, result: result(372) }));
    });
    await screen.findByRole("button", { name: "新结果可查看" });
    expect(amount).toHaveTextContent("373");
    expect(amount).toHaveAttribute("contenteditable", "true");
    expect(amount).toHaveFocus();
    expect(screen.queryByText("372", { selector: ".extract-header-value" })).not.toBeInTheDocument();
  }, 18_000);

  it("offers an in-place retry when an automatic result read fails", async () => {
    let status = "queued";
    let resultReads = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/templates")) return response([]);
      if (url.endsWith("/tasks") && init?.method === "POST") return response({ ...task, id: "retry-result", status: "queued" });
      if (url.includes("/tasks?") && !init?.method) return response([{ ...task, id: "retry-result", status, updated_at: status === "queued" ? "2026-08-01T00:00:00Z" : "2026-08-01T00:00:01Z" }]);
      if (url.endsWith("/tasks/retry-result/result")) {
        resultReads += 1;
        if (resultReads === 1) return new Response(JSON.stringify({ detail: "合成结果暂不可达" }), { status: 503, headers: { "Content-Type": "application/json" } });
        return response({ ...extraction, task_id: "retry-result", result: result(374) });
      }
      throw new Error(`Unexpected request: ${url} ${init?.method ?? ""}`);
    }));

    render(<ExtractPage />);
    fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [new File(["retry"], "retry.png")] } });
    await screen.findByText("队列中 1 个任务");
    await waitForTaskListener();
    status = "completed";
    act(() => triggerTaskEvent());

    fireEvent.click(await screen.findByRole("button", { name: "结果读取失败" }));
    const retry = screen.getByRole("button", { name: "重新读取结果" });
    expect(screen.getByText(/合成结果暂不可达/)).toBeInTheDocument();
    expect(screen.queryByText("374", { selector: ".extract-header-value" })).not.toBeInTheDocument();
    fireEvent.click(retry);
    expect(await screen.findByText("374", { selector: ".extract-header-value" })).toBeInTheDocument();
    expect(resultReads).toBe(2);
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
