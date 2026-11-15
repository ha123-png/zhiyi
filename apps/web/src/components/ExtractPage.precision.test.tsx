import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ExtractPage } from "./ExtractPage";
import type { Extraction, Task } from "../types";

function fixture(value: number) {
  localStorage.clear();
  HTMLElement.prototype.scrollIntoView = vi.fn();
  const task: Task = { id: "synthetic-precision", filename: "合成精度.txt", content_type: "text/plain",
    size_bytes: 0, sha256: "synthetic", template_mode: "manual", template_id: "synthetic",
    template_version: 1, candidate_templates: [], status: "needs_review", duplicate_of_task_id: null,
    created_at: "2026-11-01T00:00:00Z", updated_at: "2026-11-01T00:00:00Z" };
  const result = { header: { header_number: value }, items: [{ item_number: value }] };
  const extraction: Extraction = { task_id: task.id, document_kind: "custom", template_id: "synthetic",
    template_version: 1, template: null, model_name: "synthetic", prompt_version: "synthetic",
    elapsed_seconds: 1, review_version: 1, has_current_data: true, original_result: result, result,
    current_schema: [
      { key: "header_number", label: "抬头数", section: "header", value_type: "number" },
      { key: "item_number", label: "明细数", section: "item", value_type: "number" },
    ], validation_issues: [{ code: "synthetic-rule", field: "header.header_number", message: "合成核对事项", severity: "warning" }] };
  let complete!: (response: Response) => void;
  const pending = new Promise<Response>(resolve => { complete = resolve; });
  const writes: Array<{ result: typeof result; ignored_issue_indices: number[] }> = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/templates")) return Response.json([]);
    if (url.endsWith(`/tasks/${task.id}/result`)) return Response.json(extraction);
    if (url.endsWith(`/tasks/${task.id}/review`) && init?.method === "PUT") {
      writes.push(JSON.parse(init.body as string));
      return pending;
    }
    throw new Error(`Unexpected synthetic request: ${url}`);
  }));
  const view = render(<ExtractPage initialTask={task} />);
  return { view, writes, complete, extraction };
}

it.each([
  { value: 1e-8, expected: "1e-8" },
  { value: 1.2345678901234567, expected: "1.2345678901234567" },
])("shows the full supplied header and item number $expected", async ({ value, expected }) => {
  fixture(value);
  const header = await screen.findByRole("button", { name: "编辑字段 抬头数" });
  expect(header.querySelector(".extract-header-value")).toHaveTextContent(expected);
  expect(screen.getByRole("cell", { name: `编辑 明细数，当前值 ${expected}` })).toHaveTextContent(expected);
});

it.each(["header", "item", "rule"])("protects the submitted review draft from the %s entry while its save is pending", async entry => {
  const { view, writes, complete, extraction } = fixture(5);
  const header = await screen.findByRole("button", { name: "编辑字段 抬头数" });
  fireEvent.click(screen.getByRole("button", { name: "修订到表" }));
  await waitFor(() => expect(writes).toHaveLength(1));
  if (entry === "header") fireEvent.click(header);
  else if (entry === "item") fireEvent.keyDown(screen.getByRole("cell", { name: "编辑 明细数，当前值 5" }), { key: "Enter" });
  else fireEvent.click(screen.getByRole("button", { name: /^忽略$/ }));
  expect(view.container.querySelector('[contenteditable="true"]')).toBeNull();
  expect(screen.queryByText("已忽略", { selector: ".validation-ignored-tag" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^忽略$/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: "编辑相关字段" })).toBeDisabled();
  expect(writes[0].result.header.header_number).toBe(5);
  expect(writes[0].ignored_issue_indices).toEqual([]);
  await act(async () => { complete(Response.json({ ...extraction, review_version: 2 })); });
  await waitFor(() => expect(screen.getByRole("button", { name: "修订到表" })).toBeEnabled());
  fireEvent.click(header);
  expect(header.querySelector('[contenteditable="true"]')).not.toBeNull();
});

it("submits the value from the active editor before freezing the pending review", async () => {
  const { writes, complete, extraction } = fixture(5);
  const header = await screen.findByRole("button", { name: "编辑字段 抬头数" });
  fireEvent.click(header);
  const editor = header.querySelector<HTMLElement>('[contenteditable="true"]')!;
  editor.textContent = "7";
  fireEvent.input(editor);
  const save = screen.getByRole("button", { name: "修订到表" });
  await act(async () => { save.focus(); });
  fireEvent.click(save);
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0].result.header.header_number).toBe(7);
  await act(async () => { complete(Response.json({ ...extraction, result: writes[0].result, review_version: 2 })); });
  expect(header.querySelector(".extract-header-value")).toHaveTextContent("7");
});
