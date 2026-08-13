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

class MockEventSource {
  addEventListener(_type: string, fn: (event?: MessageEvent) => void) {
    taskListeners.push(fn);
  }
  close() {}
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

describe("ExtractPage", () => {
  beforeEach(() => {
    window.localStorage.clear();
    taskListeners = [];
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

  it("shows honest page-only evidence and saves the edited draft", async () => {
    render(<ExtractPage initialTask={task} />);

    const amount = await screen.findByText("372", { selector: ".extract-header-value" });
    fireEvent.click(amount);
    expect(screen.getByText("仅确认来自第 1 页，暂无可靠区域")).toBeInTheDocument();

    amount.textContent = "373";
    fireEvent.blur(amount);
    fireEvent.click(screen.getByRole("button", { name: "保存到表" }));

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
    act(() => {
      triggerTaskEvent();
    });
    expect(await screen.findByText("371", { selector: ".extract-header-value" }, { timeout: 4000 })).toBeInTheDocument();
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

function response(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}
