import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Task } from "../types";
import { ExtractPage } from "./ExtractPage";

const json = (body: unknown) => new Response(JSON.stringify(body), {
  headers: { "Content-Type": "application/json" },
});

function source(status: Task["status"], extra: Partial<Task> = {}): Task {
  return {
    id: "independent-file", filename: "合成文件.png", content_type: "image/png", size_bytes: 1,
    page_count: 1, sha256: "synthetic", template_mode: "invoice", template_id: null,
    template_version: null, candidate_templates: [], duplicate_of_task_id: null, status,
    created_at: "2026-11-02T08:00:00Z", updated_at: "2026-11-02T08:00:00Z", ...extra,
  };
}

const documentResult = {
  document_type: "invoice", seller_name: "合成销方", buyer_name: "合成购方", document_number: "S-001",
  document_date: "2026-11-02", amount_before_tax: 100, tax_amount: 6, total_amount: 106, items: [],
};

function result() {
  return {
    task_id: "independent-file", document_kind: "invoice", template_id: null, template_version: null,
    template: null, model_name: "synthetic", prompt_version: "synthetic", elapsed_seconds: 1,
    review_version: 2, has_current_data: true, original_result: documentResult, result: documentResult,
    validation_issues: [], evidence: [],
  };
}

function installApi() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/templates")) return json([]);
    if (url.endsWith("/tasks/independent-file/result")) return json(result());
    if (url.endsWith("/tasks/independent-file/review") && init?.method === "PUT") {
      const body = JSON.parse(String(init.body));
      return json({ ...result(), review_version: 3, result: body.result });
    }
    return json([]);
  });
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("EventSource", undefined);
  HTMLElement.prototype.scrollIntoView = vi.fn();
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

it.each([
  source("waiting_for_template", { pending_reason: "target_table", pending_action: "confirm_target_table", pending_review_version: 2 }),
  source("cancelled"),
])("shows the existing result without offering a write for $status", async item => {
  const fetchMock = installApi();
  render(<ExtractPage initialTask={item} />);
  expect(await screen.findByText("合成销方", { selector: ".extract-header-value" })).toBeInTheDocument();
  expect(screen.getByText(/此任务当前只读/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "修订到表" })).toBeDisabled();
  const field = screen.getByRole("button", { name: "查看字段 销售方" });
  fireEvent.click(field);
  expect(field.querySelector('[contenteditable="true"]')).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "修订到表" }));
  expect(fetchMock.mock.calls.filter(([url, init]) => String(url).endsWith("/review") && init?.method === "PUT")).toHaveLength(0);
});

it.each(["completed", "needs_review"] as const)("preserves editing and a real review request for %s", async status => {
  const fetchMock = installApi();
  render(<ExtractPage initialTask={source(status)} />);
  await screen.findByText("合成销方", { selector: ".extract-header-value" });
  const field = screen.getByRole("button", { name: "编辑字段 销售方" });
  fireEvent.click(field);
  const editor = field.querySelector<HTMLElement>('[contenteditable="true"]');
  expect(editor).not.toBeNull();
  editor!.textContent = "修订销方";
  fireEvent.blur(editor!);
  fireEvent.click(screen.getByRole("button", { name: "修订到表" }));
  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith("/review") && init?.method === "PUT");
    expect(call).toBeDefined();
    const body = JSON.parse(String(call?.[1]?.body));
    expect(body.expected_version).toBe(2);
    expect(body.result.seller_name).toBe("修订销方");
  });
});

it("does not invent an extraction result for a waiting template choice", async () => {
  const fetchMock = installApi();
  render(<ExtractPage initialTask={source("waiting_for_template", {
    pending_reason: "template", pending_action: "select_template",
  })} />);
  await screen.findByText("等待提取");
  expect(screen.queryByText("合成销方")).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/result"))).toBe(false);
});
