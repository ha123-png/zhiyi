import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AssistantProvider, useAssistant } from "./AssistantProvider";
import type { ThreadDetail } from "./types";

function Probe() {
  const assistant = useAssistant();
  return <>
    <button onClick={() => void assistant.switchThread("a")}>打开 A</button>
    <button onClick={() => void assistant.switchThread("b")}>打开 B</button>
    <output data-testid="snapshot">{JSON.stringify({
      title: assistant.detail?.title, profile: assistant.profileId,
      rows: assistant.context.row_ids, loading: assistant.loading,
      failed: assistant.failedThread, error: assistant.error,
    })}</output>
  </>;
}

function snapshot(id: string, profile: string, row: number, running = false): ThreadDetail {
  return { id, title: `${id}-${row}`, archived: false, profile_id: profile,
    updated_at: "2026-11-01T00:00:00Z", tools: [],
    messages: [{ id: `message-${row}`, role: "user", created_at: "2026-11-01T00:00:00Z",
      context: { table_id: "table", row_ids: [row] }, parts: [{ type: "text", text: "合成问题" }] }],
    runs: running ? [{ id: "old-run", status: "running", model: "synthetic", error: null,
      profile_id: profile, profile_version: 1 }] : [] };
}

afterEach(() => { vi.restoreAllMocks(); });

it.each(["success", "failure"])("ignores a late %s from the first A after switching A to B to A", async outcome => {
  localStorage.clear(); sessionStorage.clear();
  let finishFirstA!: (value: Response) => void;
  let rejectFirstA!: (reason: Error) => void;
  const firstA = new Promise<Response>((resolve, reject) => { finishFirstA = resolve; rejectFirstA = reject; });
  let aReads = 0;
  const streams: string[] = [];
  class SyntheticSource {
    constructor(url: string) { streams.push(url); }
    close() {}
  }
  vi.stubGlobal("EventSource", SyntheticSource);
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/models/profiles")) return Response.json(["old", "new"].map(id => ({
      id, version: 1, name: id, provider: "lm_studio", base_url: "http://127.0.0.1:1234/v1",
      model_name: "synthetic", is_remote: false, is_archived: false,
    })));
    if (url.endsWith("/assistant/settings")) return Response.json({ profile_id: "new" });
    if (url.endsWith("/authorization") && init?.method === "PUT")
      return Response.json({ authorization_revision: 1, invalidated_tools: 0, cancelled_runs: 0 });
    if (url.endsWith("/threads") || url.includes("/threads?")) return Response.json([]);
    if (url.endsWith("/threads/a")) return ++aReads === 1 ? firstA : Response.json(snapshot("a", "new", 2));
    if (url.endsWith("/threads/b")) return Response.json(snapshot("b", "new", 3));
    throw new Error(`Unexpected synthetic request: ${url}`);
  }));
  render(<AssistantProvider><Probe /></AssistantProvider>);
  await waitFor(() => expect(screen.getByTestId("snapshot")).toHaveTextContent('"profile":"new"'));
  fireEvent.click(screen.getByRole("button", { name: "打开 A" }));
  await waitFor(() => expect(aReads).toBe(1));
  fireEvent.click(screen.getByRole("button", { name: "打开 B" }));
  await waitFor(() => expect(screen.getByTestId("snapshot")).toHaveTextContent('"title":"b-3"'));
  fireEvent.click(screen.getByRole("button", { name: "打开 A" }));
  await waitFor(() => expect(screen.getByTestId("snapshot")).toHaveTextContent('"title":"a-2"'));
  await act(async () => {
    if (outcome === "success") finishFirstA(Response.json(snapshot("a", "old", 1, true)));
    else rejectFirstA(new Error("旧 A 读取失败"));
  });
  const current = JSON.parse(screen.getByTestId("snapshot").textContent!);
  expect(current).toEqual({ title: "a-2", profile: "new", rows: [2], loading: false, failed: null, error: "" });
  expect(streams).toEqual([]);
});
