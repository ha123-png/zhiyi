import { render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ExtractionTemplate, Task } from "../types";
import { HistoryPage } from "./HistoryPage";
import { TaskListRow } from "./TaskListRow";

vi.mock("./DocumentPreview", () => ({ DocumentPreview: () => <div>原件预览</div> }));
afterEach(() => vi.unstubAllGlobals());

const currentName = "业务命名 builtin-invoice_v1（2026·采购）";
const snapshotName = "历史业务名称／用户自定义";
const catalog = [{ id: "tpl-current-internal", name: currentName }];
const base: Task = {
  id: "task", filename: "file.pdf", content_type: "application/pdf", size_bytes: 1,
  sha256: "synthetic", template_mode: "manual", template_id: null, template_version: 1,
  candidate_templates: [], status: "completed", duplicate_of_task_id: null,
  created_at: "2026-11-02T08:00:00Z", updated_at: "2026-11-02T08:00:00Z",
};
const tasks: Task[] = [
  { ...base, id: "current", filename: "当前名称.pdf", template_id: "tpl-current-internal",
    candidate_templates: [{ id: "tpl-current-internal", name: "过去的名称", version: 1, description: "" }] },
  { ...base, id: "snapshot", filename: "历史名称.pdf", template_id: "tpl-deleted-internal",
    candidate_templates: [
      { id: "different-template", name: "不相关的候选名称", version: 1, description: "" },
      { id: "tpl-deleted-internal", name: snapshotName, version: 1, description: "" },
    ] },
  { ...base, id: "missing", filename: "缺失名称.pdf", template_id: "tpl-missing-internal" },
  { ...base, id: "smart-missing", filename: "智能匹配名称缺失.pdf", template_mode: "smart", template_id: "tpl-smart-missing-internal" },
  { ...base, id: "unassigned", filename: "未指定模板.pdf", template_mode: "invoice" },
  { ...base, id: "smart", filename: "尚未选择模板.pdf", template_mode: "smart" },
];
const expected = [currentName, snapshotName, "模板名称待确认", "模板名称待确认", "未指定", "智能匹配"];
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});

function historyServer(items: Task[], templates: Response = json(catalog)) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname.endsWith("/templates")) return templates;
    if (url.pathname.endsWith("/tasks/summary")) return json({ total: items.length, completed: items.length, needs_review: 0, failed: 0 });
    if (url.pathname.endsWith("/tasks")) return json(items);
    return json([]);
  }));
}

it("uses current business names, matching snapshots and readable missing labels in history", async () => {
  historyServer(tasks);
  render(<HistoryPage />);
  await screen.findByText("当前名称.pdf");

  tasks.forEach((task, index) => {
    const row = screen.getByText(task.filename).closest("tr")!;
    expect(within(row).getByRole("cell", { name: expected[index] })).toBeInTheDocument();
    if (task.template_id) expect(within(row).queryByText(task.template_id)).not.toBeInTheDocument();
  });
  expect(screen.queryByText("过去的名称")).not.toBeInTheDocument();
  expect(screen.queryByText("不相关的候选名称")).not.toBeInTheDocument();
});

it("keeps a task snapshot name when the template catalog cannot be read", async () => {
  historyServer([tasks[1]], json({ detail: "模板列表不可用" }, 503));
  render(<HistoryPage />);
  await screen.findByText("历史名称.pdf");
  const row = screen.getByText("历史名称.pdf").closest("tr")!;
  expect(within(row).getByRole("cell", { name: snapshotName })).toBeInTheDocument();
  expect(within(row).queryByText("tpl-deleted-internal")).not.toBeInTheDocument();
});

it("keeps task rows on the same template-name contract as file history", () => {
  const action = vi.fn();
  render(<table><tbody>{tasks.map(task => <TaskListRow key={task.id} task={task}
    templates={catalog as ExtractionTemplate[]} selected={false} disabled={false}
    primary={{ label: "对照", action }} onSelect={action}
    actions={{ cancel: action, delete: action, retry: action, details: action }} />)}</tbody></table>);

  tasks.forEach((task, index) => {
    const row = screen.getByText(task.filename).closest("tr")!;
    expect(within(row).getByRole("cell", { name: expected[index] })).toBeInTheDocument();
    if (task.template_id) expect(within(row).queryByText(task.template_id)).not.toBeInTheDocument();
  });
});
