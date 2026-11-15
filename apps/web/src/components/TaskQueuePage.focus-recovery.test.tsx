import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TaskQueuePage } from "./TaskQueuePage";
import type { Task } from "../types";
import type { TaskOverview } from "../useTaskOverview";

function pausedTask(id: string): Task {
  return {
    id, filename: `${id}.pdf`, content_type: "application/pdf", size_bytes: 1,
    sha256: id, template_mode: "smart", template_id: null, template_version: null,
    candidate_templates: [], duplicate_of_task_id: null, status: "paused",
    created_at: "2026-11-02T08:00:00Z", updated_at: "2026-11-02T08:00:00Z",
  };
}

function overview(rows: Task[], queuePaused: boolean, revision = 1): TaskOverview {
  return {
    tasks: rows, queuePaused, revision, state: "ready", error: null,
    summary: { total: rows.length, completed: 0, needs_review: 0, failed: 0,
      group_counts: { all: rows.length, active: rows.length, pending: 0, completed: 0, failed: 0, cancelled: 0 } },
    refresh: vi.fn(async () => {}),
  };
}

function server(withNextRow: boolean) {
  const removed = pausedTask("cancel-this");
  const next = pausedTask("still-paused");
  const rows = withNextRow ? [removed, next] : [removed];
  const json = (data: unknown) => new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } });
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname.endsWith("/cancel-this/cancel") && init?.method === "POST") {
      rows.splice(0, 1);
      return json({ ...removed, status: "cancelled" });
    }
    if (url.pathname.endsWith("/templates")) return json([]);
    if (url.pathname.endsWith("/tasks/summary")) return json(overview(rows, true).summary);
    if (url.pathname.endsWith("/tasks")) return json(rows);
    throw new Error(`Unexpected synthetic request: ${url.pathname}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { rows, fetchMock };
}

async function cancelFocusedRow() {
  const opener = await screen.findByRole("button", { name: "更多操作 cancel-this.pdf" });
  await waitFor(() => expect(opener).toBeEnabled());
  opener.focus();
  expect(opener).toHaveFocus();
  fireEvent.keyDown(opener, { key: "Enter" });
  fireEvent.click(await screen.findByRole("menuitem", { name: "取消任务" }));
  await waitFor(() => expect(screen.queryByText("cancel-this.pdf")).not.toBeInTheDocument());
  await waitFor(() => expect(screen.getByRole("checkbox", { name: "选择本页任务" }).closest(".task-workspace-list")).toHaveAttribute("aria-busy", "false"));
}

describe("task-list focus after row removal", () => {
  it("falls back to the selected filter when the next row cannot resume under a paused queue", async () => {
    const data = server(true);
    const view = render(<TaskQueuePage overview={overview([...data.rows], true)}
      viewState={{ group: "active", searchInput: "", page: 0 }} />);
    expect(await screen.findByRole("button", { name: "继续 still-paused.pdf" })).toBeDisabled();
    await cancelFocusedRow();
    const selectedFilter = screen.getByRole("button", { name: /^进行中/ });
    await waitFor(() => expect(selectedFilter).toHaveFocus());
    expect(screen.getByRole("button", { name: "继续 still-paused.pdf" })).toBeDisabled();
    expect(data.fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);

    // A settled fallback consumes the removal marker, so a later queue update
    // cannot steal focus from a different control the user chose afterward.
    const search = screen.getByRole("textbox", { name: "搜索任务文件名" });
    search.focus();
    view.rerender(<TaskQueuePage overview={overview([...data.rows], false, 2)}
      viewState={{ group: "active", searchInput: "", page: 0 }} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "继续 still-paused.pdf" })).toBeEnabled());
    expect(search).toHaveFocus();
  });

  it("keeps the adjacent primary action as the target when it is enabled", async () => {
    server(true);
    render(<TaskQueuePage overview={overview([pausedTask("cancel-this"), pausedTask("still-paused")], false)}
      viewState={{ group: "active", searchInput: "", page: 0 }} />);
    await cancelFocusedRow();
    await waitFor(() => expect(screen.getByRole("button", { name: "继续 still-paused.pdf" })).toHaveFocus());
  });

  it("returns to the selected filter when the last visible row is removed", async () => {
    server(false);
    render(<TaskQueuePage overview={overview([pausedTask("cancel-this")], true)}
      viewState={{ group: "active", searchInput: "", page: 0 }} />);
    await cancelFocusedRow();
    await waitFor(() => expect(screen.getByRole("button", { name: /^进行中/ })).toHaveFocus());
  });
});
