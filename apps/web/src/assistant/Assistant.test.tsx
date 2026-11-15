import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  AssistantProvider,
  useAssistantPageContext,
} from "./AssistantProvider";
import {
  AssistantDrawer,
  AssistantPage,
  AssistantTrigger,
} from "./AssistantSurface";
import { AnalysisCard } from "./AnalysisCard";
import type { ThreadDetail, ToolRecord } from "./types";
import { consentKey, readServiceConsentState, saveServiceConsent, serviceConsentKey, serviceConsentStorageKey, sessionConsentRevision } from "./consent";
import { assistantBase } from "./api";

let posted: Record<string, unknown>[];
let stored: ThreadDetail | null;
let authorizationCalls: {url: string; body: Record<string, unknown>}[];
let requestOrder: string[];
let failAuthorizationSync: boolean;
let sources: Source[];
class Source {
  onmessage: ((event: { data: string; lastEventId?: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor() {
    sources.push(this);
  }
  close() {}
}
const local = {
  id: "local",
  name: "本地聊天",
  provider: "lm_studio",
  version: 1,
  base_url: "http://127.0.0.1:1234/v1",
  model_name: "qwen",
  is_remote: false,
  is_archived: false,
  is_active: false,
};
const remote = {
  ...local,
  id: "cloud",
  name: "提取云模型",
  base_url: "https://models.example.test/v1",
  is_remote: true,
  is_active: true,
};
let availableProfiles = [local, remote];
function ContextPage() {
  useAssistantPageContext("test", {
    table_id: "t",
    table_name: "合成数据",
    row_ids: [1, 2, 3],
  });
  return null;
}
function Shell({ showTrigger = true }: { showTrigger?: boolean } = {}) {
  return (
    <AssistantProvider>
      <ContextPage />
      <AssistantPage focusComposer={!showTrigger} />
      {showTrigger && <AssistantTrigger />}
      <AssistantDrawer />
    </AssistantProvider>
  );
}
beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  availableProfiles = [local, remote];
  posted = [];
  stored = null;
  authorizationCalls = [];
  requestOrder = [];
  failAuthorizationSync = false;
  sources = [];
  vi.stubGlobal("EventSource", Source);
  HTMLElement.prototype.scrollTo = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.includes("/assistant/resources")) {
        const query = new URL(url, "http://localhost").searchParams;
        const resources = [
          { id: "t", name: "合成数据", kind: "table" },
          { id: "second", name: "第二张表", kind: "table" },
          { id: "receipt", name: "收据模板", kind: "template" },
          { id: "source", name: "收据原件.png", kind: "task" },
        ];
        return Response.json(resources.filter((r) => r.kind === query.get("kind") &&
          (query.has("ids") ? query.getAll("ids").includes(r.id) : r.name.includes(query.get("search") || ""))));
      }
      if (url.endsWith("/models/profiles"))
        return Response.json(availableProfiles);
      if (url.endsWith("/assistant/settings"))
        return Response.json({ profile_id: null });
      if (url.endsWith("/assistant/threads/thread/authorization") && method === "PUT") {
        authorizationCalls.push({url, body: JSON.parse(init.body)});
        requestOrder.push("authorization");
        return failAuthorizationSync
          ? Response.json({detail: "授权服务暂不可用"}, {status: 503})
          : Response.json({authorization_revision: authorizationCalls.length, invalidated_tools: 0, cancelled_runs: 0});
      }
      if (url.endsWith("/assistant/tools/pending-tool/decision") && method === "POST") {
        requestOrder.push("decision");
        return Response.json({status: "approved"});
      }
      if (url.endsWith("/assistant/threads") || url.includes("threads?"))
        return Response.json(
          stored && !url.includes("archived=true") ? [stored] : [],
        );
      if (url.endsWith("/assistant/threads/thread") || url.includes("/assistant/threads/thread?run_id="))
        return Response.json(stored);
      if (url.endsWith("/assistant/runs") && method === "POST") {
        const body = JSON.parse(init.body);
        posted.push(body);
        stored = {
          id: "thread",
          title: body.text,
          profile_id: body.profile_id,
          archived: false,
          updated_at: "2026-09-13T00:00:00Z",
          messages: [
            {
              id: "u",
              role: "user",
              parts: [{ type: "text", text: body.text }],
              context: body.context,
              created_at: "2026-09-13T00:00:00Z",
            },
            {
              id: "a",
              role: "assistant",
              parts: [{ type: "text", text: "保存的回答" }],
              context: body.context,
              created_at: "2026-09-13T00:00:00Z",
            },
          ],
          runs: [
            {
              id: "run",
              status: "completed",
              error: null,
              model: "qwen",
              profile_id: body.profile_id,
              profile_version: 1,
            },
          ],
          tools: [],
        };
        return Response.json({ thread_id: "thread", run_id: "run" });
      }
      return Response.json({});
    }),
  );
});
afterEach(() => vi.restoreAllMocks());

async function selectCloud() {
  fireEvent.click(screen.getByRole("button", { name: "对话设置" }));
  await screen.findByRole("option", { name: /提取云模型/ });
  fireEvent.change(screen.getByRole("combobox", { name: "问知意模型方案" }), { target: { value: "cloud" } });
  fireEvent.click(screen.getByRole("button", { name: "关闭对话设置" }));
}

function rememberCloud() {
  fireEvent.click(screen.getByRole("checkbox", { name: "在此设备记住对此模型服务的允许" }));
  fireEvent.click(screen.getByRole("button", { name: "允许并记住" }));
}

describe("问知意", () => {
  it("distinguishes a failed history search from an empty list and retries with the same search", async () => {
    const fetchBefore = globalThis.fetch;
    let fail = true;
    vi.stubGlobal("fetch", vi.fn(async (input, init) => {
      if (fail && String(input).includes("/assistant/threads?search="))
        return Response.json({ detail: "合成的列表读取失败" }, { status: 503 });
      return fetchBefore(input, init);
    }));
    render(<Shell />);
    await screen.findByRole("button", { name: "重新读取列表" });
    expect(screen.queryByText("发送第一条消息后，便会保存在这里。")).not.toBeInTheDocument();
    const search = screen.getByRole("textbox", { name: "搜索对话" });
    fireEvent.change(search, { target: { value: "保留筛选" } });
    await screen.findByRole("button", { name: "重新读取列表" });
    expect(screen.queryByText("没有匹配的对话")).not.toBeInTheDocument();
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "重新读取列表" }));
    expect(search).toHaveFocus();
    expect(search).toHaveValue("保留筛选");
    await screen.findByText("没有匹配的对话");
  });

  it("shows thread loading and retries a failed read without replacing it with a new-conversation welcome", async () => {
    stored = { id: "thread", title: "合成历史对话", profile_id: "local", archived: false, updated_at: "2026-09-13T00:00:00Z",
      messages: [{ id: "answer", role: "assistant", parts: [{ type: "text", text: "恢复的合成回答" }], context: {}, created_at: "2026-09-13T00:00:00Z" }],
      runs: [], tools: [] };
    const fetchBefore = globalThis.fetch;
    let release!: () => void;
    let fail = true;
    const delayed = new Promise<void>(resolve => { release = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (input, init) => {
      if (String(input).endsWith("/assistant/threads/thread") && fail) {
        await delayed;
        return Response.json({ detail: "合成的读取失败" }, { status: 503 });
      }
      return fetchBefore(input, init);
    }));
    render(<Shell />);
    fireEvent.click(await screen.findByText("合成历史对话"));
    expect(screen.getByText("正在读取对话…")).toHaveAttribute("role", "status");
    expect(screen.queryByText("把问题交给知意")).not.toBeInTheDocument();
    await act(async () => release());
    const retry = await screen.findByRole("button", { name: "重新读取对话" });
    expect(screen.queryByText("把问题交给知意")).not.toBeInTheDocument();
    const composer = screen.getByRole("textbox", { name: "向问知意提问" });
    fireEvent.change(composer, { target: { value: "读取失败时保留的草稿" } });
    expect(screen.getByRole("button", { name: "发送问题" })).toBeDisabled();
    fail = false;
    fireEvent.click(retry);
    await screen.findByText("恢复的合成回答");
    expect(composer).toHaveValue("读取失败时保留的草稿");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("ignores a failed thread response after the user has moved to a new conversation", async () => {
    stored = { id: "thread", title: "即将失败的旧对话", profile_id: "local", archived: false, updated_at: "2026-09-13T00:00:00Z",
      messages: [], runs: [], tools: [] };
    const fetchBefore = globalThis.fetch;
    let release!: () => void;
    const delayed = new Promise<void>(resolve => { release = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (input, init) => {
      if (String(input).endsWith("/assistant/threads/thread")) {
        await delayed;
        return Response.json({ detail: "旧对话读取失败" }, { status: 503 });
      }
      return fetchBefore(input, init);
    }));
    render(<Shell />);
    fireEvent.click(await screen.findByText("即将失败的旧对话"));
    fireEvent.click(screen.getByRole("button", { name: "新对话" }));
    expect(screen.getByText("把问题交给知意")).toBeInTheDocument();
    await act(async () => release());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重新读取对话" })).not.toBeInTheDocument();
  });

  it("retries failed resource filtering in place and preserves search focus and selected scope", async () => {
    const fetchBefore = globalThis.fetch;
    let failSearch = true;
    vi.stubGlobal("fetch", vi.fn(async (input, init) => {
      const url = String(input);
      if (failSearch && url.includes("/assistant/resources") && new URL(url, "http://localhost").searchParams.get("search"))
        return Response.json({ detail: "合成的资料列表失败" }, { status: 503 });
      return fetchBefore(input, init);
    }));
    render(<Shell />);
    fireEvent.click(screen.getByRole("button", { name: "对话设置" }));
    fireEvent.click(await screen.findByRole("checkbox", { name: "合成数据" }));
    const search = screen.getByRole("textbox", { name: "搜索可选资料" });
    fireEvent.change(search, { target: { value: "合成" } });
    await screen.findByRole("button", { name: "重新读取资料" });
    expect(screen.getByRole("checkbox", { name: "合成数据" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "合成数据" })).toBeDisabled();
    expect(screen.queryByText("还没有这类资料。")).not.toBeInTheDocument();
    failSearch = false;
    fireEvent.click(screen.getByRole("button", { name: "重新读取资料" }));
    expect(search).toHaveFocus();
    expect(search).toHaveValue("合成");
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "合成数据" })).toBeEnabled());
    expect(screen.getByRole("checkbox", { name: "合成数据" })).toBeChecked();
  });

  it("synchronizes a restored authorization snapshot before approving a pending preview", async () => {
    const pending = {id: "pending-tool", name: "propose_task_control", status: "pending",
      result: {kind: "task_control", action: "pause", filename: "待处理.pdf", expected_status: "queued"}};
    stored = {id: "thread", title: "待确认操作", profile_id: "local", archived: false, updated_at: "2026-09-13T00:00:00Z",
      last_model_run: {id: "run", status: "completed", error: null, model: "qwen", profile_id: "local", profile_version: 1},
      runs: [], tools: [pending], messages: [{id: "assistant", role: "assistant", parts: [{type: "tool", ...pending}],
        context: {table_id: "t", mode: "assist", capabilities: ["data"]}, created_at: "2026-09-13T00:00:00Z"}]} as ThreadDetail;
    render(<Shell />);
    fireEvent.click(await screen.findByText("待确认操作"));
    const approve = await screen.findByRole("button", {name: "确认执行"});
    await waitFor(() => expect(approve).toBeEnabled());
    expect(authorizationCalls[0].body).toMatchObject({
      context: {table_id: "t", mode: "assist", capabilities: ["data"]},
      profile_id: "local", profile_version: 1, remote_authorized: false,
    });
    fireEvent.click(approve);
    await waitFor(() => expect(requestOrder.slice(-2)).toEqual(["authorization", "decision"]));
  });

  it("keeps approval blocked when thread authorization cannot be synchronized", async () => {
    failAuthorizationSync = true;
    const pending = {id: "pending-tool", name: "propose_task_control", status: "pending",
      result: {kind: "task_control", action: "pause", filename: "待处理.pdf", expected_status: "queued"}};
    stored = {id: "thread", title: "授权待同步", profile_id: "local", archived: false, updated_at: "2026-09-13T00:00:00Z",
      last_model_run: {id: "run", status: "completed", error: null, model: "qwen", profile_id: "local", profile_version: 1},
      runs: [], tools: [pending], messages: [{id: "assistant", role: "assistant", parts: [{type: "tool", ...pending}],
        context: {table_id: "t", mode: "assist", capabilities: ["data"]}, created_at: "2026-09-13T00:00:00Z"}]} as ThreadDetail;
    render(<Shell />);
    fireEvent.click(await screen.findByText("授权待同步"));
    const approve = await screen.findByRole("button", {name: "确认执行"});
    await waitFor(() => expect(approve).toBeDisabled());
    fireEvent.click(screen.getByRole("button", {name: "对话设置"}));
    expect(await screen.findByRole("button", {name: "重试同步"})).toBeEnabled();
    expect(requestOrder).not.toContain("decision");
  });

  it("restores the last actual model and scope despite a late default response", async () => {
    const modelRun = { id: "old-cloud", status: "completed", error: null, model: "cloud-original", profile_id: "cloud", profile_version: 1 };
    stored = { id: "thread", title: "云端历史", profile_id: "local", archived: false, updated_at: "2026-09-13T00:00:00Z",
      last_model_run: modelRun, runs: [{ ...modelRun, id: "refresh", model: "知意统计", execution_kind: "analysis_refresh" }], tools: [],
      messages: [{ id: "answer", role: "assistant", parts: [{ type: "text", text: "已保存的云端回答" }],
        context: { workspace: true, mode: "read" }, created_at: "2026-09-13T00:00:00Z" }] };
    const originalFetch = vi.mocked(fetch).getMockImplementation()!;
    let resolveDefault!: (value: Response) => void;
    const delayed = new Promise<Response>(resolve => { resolveDefault = resolve; });
    vi.stubGlobal("fetch", vi.fn((input, init) => String(input).endsWith("/assistant/settings")
      ? delayed : originalFetch(input, init)));
    render(<Shell />);
    fireEvent.click(await screen.findByRole("button", { name: /云端历史.*2026/ }));
    await screen.findByText("已保存的云端回答");
    await act(async () => resolveDefault(Response.json({ profile_id: "local" })));
    fireEvent.click(screen.getByRole("button", { name: "对话设置" }));
    await screen.findByRole("option", { name: /提取云模型/ });
    expect(screen.getByRole("combobox", { name: "问知意模型方案" })).toHaveValue("cloud");
    expect(screen.getByRole("checkbox", { name: "允许查找知意资料" })).toBeChecked();
    expect(screen.getByRole("button", { name: "只读分析" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("下一条使用")).toBeVisible();
    fireEvent.change(screen.getByRole("combobox", { name: "问知意模型方案" }), { target: { value: "local" } });
    expect(screen.getByText(/上次回答使用 cloud-original/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "关闭对话设置" }));
    fireEvent.change(screen.getByRole("textbox", { name: "向问知意提问" }), { target: { value: "继续" } });
    fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0].profile_id).toBe("local");
  });
  it("keeps explanation visible and refreshes saved analysis without a prompt or model consent", async () => {
    const result = { analysis_id: "snapshot", source: { table_id: "t", table_name: "历史汇总", row_count: 10000,
      document_count: 10000, grain: "auto", generated_at: "2026-09-13T00:00:00Z", request: { table_id: "t", row_ids: null } },
      data: [{ label: "全部", "sum:total": 50005000 }], metric_keys: ["sum:total"], warnings: [], truncated: false };
    const tool = { id: "stat", name: "analyze_data_table", status: "completed", result };
    const prose = "这是完整数据的解释，有助于理解业务变化。".repeat(20);
    stored = { id: "thread", title: "刷新验收", profile_id: "cloud", archived: false, updated_at: "2026-09-13T00:00:00Z", runs: [], tools: [tool],
      messages: [{ id: "a", role: "assistant", context: { workspace: true }, created_at: "2026-09-13T00:00:00Z",
        parts: [{ type: "tool", ...tool }, { type: "text", text: prose }] }] };
    const originalFetch = vi.mocked(fetch).getMockImplementation()!;
    const refreshBodies: Record<string, unknown>[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input, init) => {
      if (String(input).endsWith("/tools/stat/refresh-analysis")) {
        refreshBodies.push(JSON.parse(String(init?.body)));
        const updated = { ...tool, id: "stat2", result: { ...result, analysis_id: "new", refreshed_from: "stat",
          source: { ...result.source, generated_at: "2026-09-15T00:00:00Z" } } };
        stored!.tools.push(updated);
        stored!.messages.push({ id: "a2", role: "assistant", context: { workspace: true }, created_at: "2026-09-15T00:00:00Z", parts: [{ type: "tool", ...updated }] });
        return Response.json({ thread_id: "thread", run_id: "local-refresh" });
      }
      return originalFetch(input, init);
    }));
    render(<Shell />);
    fireEvent.click(await screen.findByRole("button", { name: /刷新验收.*2026/ }));
    expect(await screen.findByText(prose)).toBeVisible();
    expect(screen.queryByRole("button", { name: "文字说明" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "向问知意提问" }), { target: { value: "未发送的草稿" } });
    fireEvent.click(screen.getByRole("button", { name: "来源" }));
    fireEvent.click(screen.getByRole("button", { name: "按最新数据重新分析" }));
    expect(await screen.findByText(/更新于/)).toBeVisible();
    expect(refreshBodies).toHaveLength(1);
    expect(refreshBodies[0]).toEqual({ thread_id: "thread", request_id: expect.any(String), context: { workspace: true } });
    expect(posted).toHaveLength(0);
    expect(screen.getByRole("textbox", { name: "向问知意提问" })).toHaveValue("未发送的草稿");
    expect(screen.getAllByText("50,005,000").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByRole("button", { name: "重新回答" })).toHaveLength(1);
  });
  it("keeps the answer visible and puts raw row evidence behind one disclosure", async () => {
    const result = { analysis_id: "rows", source: { table_id: "t", table_name: "采购", row_count: 1,
      document_count: 1, grain: "row", generated_at: "2026-09-21", request: {} },
      rows: [{ row_id: 1, version: 0, task_id: null, values: { amount: 10 } }], columns: [{ key: "amount", label: "金额" }],
      warnings: [], truncated: false };
    const tool = { id: "rows", name: "analyze_data_table", status: "completed", result };
    stored = { id: "thread", title: "明细阅读", profile_id: "local", archived: false,
      updated_at: "2026-09-21T00:00:00Z", runs: [], tools: [tool], messages: [
        { id: "a", role: "assistant", context: {}, created_at: "2026-09-21T00:00:00Z",
          parts: [{ type: "tool", ...tool }, { type: "text", text: "金额为 10，以下是说明。" }] },
      ] };
    render(<Shell />);
    fireEvent.click(await screen.findByRole("button", { name: /明细阅读.*2026/ }));
    expect(await screen.findByText("金额为 10，以下是说明。")).toBeVisible();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "已读取的数据与来源" }));
    // The disclosure mounts its content before its opening opacity animation completes.
    await waitFor(() => expect(screen.getByRole("table")).toBeVisible());
  });
  it("renders incremental Unicode text without polling and reconciles final saved content", async () => {
    stored = { id: "thread", title: "真实增量通路", profile_id: "local", archived: false, updated_at: "2026-09-13T00:00:00Z",
      messages: [{ id: "a", role: "assistant", parts: [], context: {}, created_at: "2026-09-13T00:00:00Z" }], tools: [],
      runs: [{ id: "run", message_id: "a", stream_cursor: 0, status: "running", error: null, model: "qwen", profile_id: "local", profile_version: 1 }] };
    render(<Shell />);
    fireEvent.click(await screen.findByRole("button", { name: /真实增量通路.*2026/ }));
    await waitFor(() => expect(sources.length).toBe(1));
    const reads = () => vi.mocked(fetch).mock.calls.filter(([url]) => String(url).includes("/assistant/threads/thread")).length;
    const before = reads();
    const event = { data: JSON.stringify({ type: "text.delta", part_index: 0, offset: 0, text: "你好😀，这是增量文字。" }), lastEventId: "1" };
    act(() => { sources[0].onmessage?.(event); sources[0].onmessage?.(event); });
    expect(await screen.findByText("你好😀，这是增量文字。")).toBeVisible();
    expect(reads()).toBe(before);
    stored.messages[0].parts = [{ type: "text", text: "最终核对后的回答。" }];
    stored.runs[0].status = "completed";
    stored.runs[0].stream_cursor = 2;
    act(() => sources[0].onmessage?.({ data: JSON.stringify({ type: "run.completed", status: "completed" }), lastEventId: "2" }));
    expect(await screen.findByText("最终核对后的回答。")).toBeVisible();
    expect(screen.queryByText("你好😀，这是增量文字。")).not.toBeInTheDocument();
  });
  it("loads actual resource lists automatically and retains named selections across categories", async () => {
    render(<Shell />);
    fireEvent.click(screen.getByRole("button", { name: "对话设置" }));
    await screen.findByRole("checkbox", { name: "第二张表" });
    fireEvent.click(screen.getByRole("tab", { name: "模板" }));
    fireEvent.click(await screen.findByRole("checkbox", { name: "收据模板" }));
    fireEvent.click(screen.getByRole("tab", { name: "文件" }));
    await screen.findByRole("checkbox", { name: "收据原件.png" });
    expect(screen.getByRole("button", { name: "收据模板" })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "搜索可选资料" }), { target: { value: "不存在" } });
    await waitFor(() => expect(screen.queryByRole("checkbox", { name: "收据原件.png" })).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "查找" })).not.toBeInTheDocument();
  });
  it("supports keyboard resource tabs and distinguishes an empty archive", async () => {
    render(<Shell />);
    fireEvent.click(screen.getByRole("button", { name: "已归档" }));
    expect(await screen.findByText("暂无归档对话。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "对话设置" }));
    await screen.findByRole("checkbox", { name: "第二张表" });
    fireEvent.keyDown(screen.getByRole("tab", { name: "数据表" }), { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "文件" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "文件" })).toHaveFocus();
    await screen.findByRole("checkbox", { name: "收据原件.png" });
    fireEvent.keyDown(screen.getByRole("tab", { name: "文件" }), { key: "End" });
    await screen.findByRole("checkbox", { name: "收据模板" });
  });
  it.each([
    { data: { table_id: "private-table-xyz", row_id: 714220, invoice_id: "ORD-001" } },
    { text: '{"table_id":"private-table-xyz","row_id":714220,"invoice_id":"ORD-001"}' },
  ])("summarizes a saved result without exposing its internal JSON and keeps the answer and original", async result => {
    const saved = { id: "saved", name: "read_tool_result", status: "completed", result: result as unknown as ToolRecord["result"] };
    const original = { id: "original", name: "read_original_page", status: "completed",
      result: { page: 1, original: { kind: "original", id: "private-original-id" } } };
    stored = { id: "thread", title: "资料回读", profile_id: "local", archived: false,
      updated_at: "2026-09-13T00:00:00Z", runs: [], tools: [saved, original], messages: [
        { id: "a", role: "assistant", context: {}, created_at: "2026-09-13T00:00:00Z",
          parts: [{ type: "tool", ...saved }, { type: "tool", ...original }, { type: "text", text: "订单编号是 ORD-001。" }] },
      ] };
    const navigation = vi.fn();
    window.addEventListener("zhiyi:assistant-navigate", navigation);
    try {
      render(<Shell />);
      fireEvent.click(await screen.findByRole("button", { name: /资料回读.*2026/ }));
      expect(await screen.findByText("订单编号是 ORD-001。")).toBeVisible();
      fireEvent.click(screen.getByRole("button", { name: "已查阅资料与处理过程 · 2 步" }));
      const oldRawDisclosure = screen.queryByRole("button", { name: "已读取的资料" });
      if (oldRawDisclosure) fireEvent.click(oldRawDisclosure);
      expect(screen.getByText("已读取保存的资料。")).toBeInTheDocument();
      expect(screen.queryByText(/private-table-xyz|714220|table_id|row_id/)).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "查看原件" }));
      expect((navigation.mock.calls[0][0] as CustomEvent).detail).toEqual({ kind: "original", id: "private-original-id" });
    } finally {
      window.removeEventListener("zhiyi:assistant-navigate", navigation);
    }
  });

  it("labels older change previews by preview position while keeping business values", async () => {
    const tool = { id: "old-change", name: "propose_changes", status: "pending", result: {
      table_name: "采购", columns: { invoice_id: "订单编号", amount: "金额" }, affected: [
        { row_id: 714220, version: 1, before: { invoice_id: "ORD-001", amount: 10 }, after: { invoice_id: "ORD-002", amount: 11 } },
        { row_id: 714999, version: 1, before: { invoice_id: "ORD-003", amount: 20 }, after: { invoice_id: "ORD-004", amount: 21 } },
      ] } as unknown as ToolRecord["result"] };
    stored = { id: "thread", title: "历史资料修改", profile_id: "local", archived: false,
      updated_at: "2026-09-13T00:00:00Z", runs: [], tools: [tool], messages: [
        { id: "a", role: "assistant", context: {}, created_at: "2026-09-13T00:00:00Z", parts: [{ type: "tool", ...tool }] },
      ] };
    render(<Shell />);
    fireEvent.click(await screen.findByRole("button", { name: /历史资料修改.*2026/ }));
    expect(await screen.findByText(/将修改.*采购.*2\s*条记录/)).toBeInTheDocument();
    expect(screen.getAllByRole("cell", { name: "预览记录 1" })).toHaveLength(2);
    expect(screen.getAllByRole("cell", { name: "预览记录 2" })).toHaveLength(2);
    expect(screen.getByRole("cell", { name: "ORD-004" })).toBeInTheDocument();
    expect(screen.queryByText(/714220|714999/)).not.toBeInTheDocument();
  });

  it("shows an operation failure without hiding it behind a process disclosure", async () => {
    const failed = { id: "failed-tool", name: "read_resource", status: "failed", result: { error: "所选资料已删除，请重新选择资料范围。" } };
    stored = {
      id: "thread", title: "读取失败的合成对话", profile_id: "local", archived: false,
      updated_at: "2026-09-13T00:00:00Z", runs: [], tools: [failed],
      messages: [{ id: "failed-answer", role: "assistant", context: {}, created_at: "2026-09-13T00:00:00Z", parts: [{ type: "tool", ...failed }] }],
    };
    render(<Shell />);
    fireEvent.click(await screen.findByRole("button", { name: /读取失败的合成对话.*2026/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(failed.result.error);
    expect(screen.queryByRole("button", { name: "读取资料 · 未完成" })).not.toBeInTheDocument();
  });
  it.each([["stale", "已过期或冲突"], ["superseded", "已被新请求替代"]])("keeps %s proposals visible without execution controls", async (status, label) => {
    const tool = { id: "retired", name: "propose_operations", status, result: { kind: "operation_plan", title: "修改数据 · 收支", items: [] } };
    stored = { id: "thread", title: "历史修改状态", profile_id: "local", archived: false,
      updated_at: "2026-09-13T00:00:00Z", runs: [], tools: [tool], messages: [
        { id: "a", role: "assistant", context: {}, created_at: "2026-09-13T00:00:00Z", parts: [{ type: "tool", ...tool }] },
      ] };
    render(<Shell />);
    fireEvent.click(await screen.findByRole("button", { name: /历史修改状态.*2026/ }));
    expect(await screen.findByText(label)).toBeVisible();
    expect(screen.getByText("修改数据 · 收支")).toBeVisible();
    expect(screen.queryByRole("button", { name: "确认执行" })).not.toBeInTheDocument();
  });

  it.each(["pending", "approved"])("does not revive template writes from %s historical proposals", async status => {
    const tool = { id: "old-template", name: "propose_operations", status, result: { kind: "operation_plan", title: "历史模板提案",
      execution: { can_undo: true }, items: [{ label: "创建模板", operation: { kind: "create_template" }, affected: [], before: null, after: { name: "收据" } }] } };
    stored = { id: "thread", title: "历史模板操作", profile_id: "local", archived: false, updated_at: "2026-09-13T00:00:00Z", runs: [], tools: [tool],
      messages: [{ id: "old-a", role: "assistant", context: {}, created_at: "2026-09-13T00:00:00Z", parts: [{ type: "tool", ...tool }] }] };
    render(<Shell />);
    fireEvent.click(await screen.findByRole("button", { name: /历史模板操作.*2026/ }));
    expect(await screen.findByText(/此历史模板提案已停止支持执行/)).toBeVisible();
    expect(screen.queryByRole("button", { name: /确认执行|撤销本次修改|编辑并保存|编辑为新模板/ })).not.toBeInTheDocument();
  });

  it("preserves template reading and removes template write capabilities", async () => {
    render(<Shell />);
    expect(screen.queryByRole("button", { name: /起草.*模板/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "对话设置" }));
    fireEvent.click(screen.getByRole("button", { name: "允许的操作能力" }));
    expect(screen.getByRole("checkbox", { name: "数据编辑" })).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "模板管理" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "模板" }));
    expect(await screen.findByRole("checkbox", { name: "收据模板" })).toBeInTheDocument();
  });

  it("does not inherit the active extraction model or create empty history", async () => {
    render(<Shell />);
    fireEvent.click(screen.getByRole("button", { name: "对话设置" }));
    await screen.findByRole("option", { name: /提取云模型/ });
    expect(
      screen.getByRole("combobox", { name: "问知意模型方案" }),
    ).toHaveValue("");
    if (screen.queryByRole("button", { name: "关闭对话设置" }))
      fireEvent.click(screen.getByRole("button", { name: "关闭对话设置" }));
    fireEvent.change(screen.getByRole("textbox", { name: "向问知意提问" }), {
      target: { value: "你好" },
    });
    expect(screen.getByRole("button", { name: "发送问题" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "问知意" }));
    expect(
      screen.getByRole("dialog", { name: "快捷问知意" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "关闭快捷问知意" }));
    expect(posted).toHaveLength(0);
  });

  it.each(["close button", "Escape", "backdrop", "native close"])("returns focus to the quick-ask opener after %s", async closeWith => {
    render(<Shell />);
    const opener = screen.getByRole("button", { name: "问知意" });
    opener.focus();
    fireEvent.click(opener);
    const drawer = screen.getByRole("dialog", { name: "快捷问知意" });
    expect(within(drawer).getByRole("textbox", { name: "向问知意提问" })).toHaveFocus();
    if (closeWith === "close button") {
      const closeButton = within(drawer).getByRole("button", { name: "关闭快捷问知意" });
      closeButton.focus();
      fireEvent.click(closeButton);
    } else if (closeWith === "Escape") {
      fireEvent(drawer, new Event("cancel", { bubbles: false, cancelable: true }));
    } else if (closeWith === "backdrop") {
      fireEvent.click(drawer);
    } else {
      act(() => (drawer as HTMLDialogElement).close());
    }
    expect(drawer).not.toHaveAttribute("open");
    expect(opener).toBeVisible();
    expect(opener).toHaveFocus();
    expect(posted).toHaveLength(0);
  });

  it("leaves existing page focus alone while the quick-ask drawer starts closed", async () => {
    render(<><button autoFocus>合成设置项</button><Shell /></>);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: "合成设置项" })).toHaveFocus();
  });

  it("hands focus and the draft to the new composer instead of the removed quick-ask opener", () => {
    const view = render(<Shell />);
    const opener = screen.getByRole("button", { name: "问知意" });
    opener.focus();
    fireEvent.click(opener);
    const oldComposer = screen.getByRole("textbox", { name: "向问知意提问" });
    fireEvent.change(oldComposer, { target: { value: "展开后继续的合成草稿" } });
    const focusOpener = vi.spyOn(opener, "focus");
    const nativeFocus = HTMLElement.prototype.focus;
    // Native modal dialogs make the destination page inert until close().
    // jsdom's dialog shim does not implement that restriction.
    const modalFocus = vi.spyOn(HTMLElement.prototype, "focus").mockImplementation(function (this: HTMLElement, options?: FocusOptions) {
      const modal = document.querySelector("dialog[open]");
      if (!modal || modal.contains(this)) nativeFocus.call(this, options);
    });
    // App removes AssistantTrigger when navigation reaches the assistant page.
    const onNavigate = vi.fn(() => view.rerender(<Shell showTrigger={false} />));
    window.addEventListener("zhiyi:assistant-navigate", onNavigate);
    try {
      const expand = screen.getByRole("button", { name: "展开到问知意页面" });
      expand.focus();
      fireEvent.click(expand);
      expect(onNavigate).toHaveBeenCalledOnce();
      expect(opener).not.toBeInTheDocument();
      expect(focusOpener).not.toHaveBeenCalled();
      expect(view.container.querySelector("dialog.ask-drawer")).not.toHaveAttribute("open");
      const newComposer = screen.getByRole("textbox", { name: "向问知意提问" });
      expect(newComposer).not.toBe(oldComposer);
      expect(newComposer).toHaveValue("展开后继续的合成草稿");
      expect(newComposer).toHaveFocus();
      expect(modalFocus).toHaveBeenLastCalledWith({ preventScroll: true });
    } finally {
      window.removeEventListener("zhiyi:assistant-navigate", onNavigate);
      modalFocus.mockRestore();
    }
  });

  it("keeps selected-row scope and the same conversation across drawer and page", async () => {
    render(<Shell />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "问知意" }));
    if (!screen.queryByRole("button", { name: "附加当前页面" }))
      fireEvent.click(screen.getByRole("button", { name: "对话设置" }));
    fireEvent.click(screen.getByRole("button", { name: "附加当前页面" }));
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "合成数据" })).toBeChecked());
    expect(
      screen.getAllByText("合成数据 · 3 条选中记录").length,
    ).toBeGreaterThan(0);
    fireEvent.change(screen.getByRole("combobox", { name: "问知意模型方案" }), {
      target: { value: "local" },
    });
    if (screen.queryByRole("button", { name: "关闭对话设置" }))
      fireEvent.click(screen.getByRole("button", { name: "关闭对话设置" }));
    fireEvent.change(screen.getByRole("textbox", { name: "向问知意提问" }), {
      target: { value: "分析三行" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
    await screen.findByText("保存的回答");
    expect(posted[0].context).toEqual({
      table_id: "t",
      table_name: "合成数据",
      row_ids: [1, 2, 3],
    });
    await act(async () => {
      sources[0]?.onmessage?.({
        data: JSON.stringify({ type: "run.completed", status: "completed" }),
      });
    });
    fireEvent.click(screen.getByRole("button", { name: "关闭快捷问知意" }));
    expect(screen.getByText("保存的回答")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "问知意" }));
    expect(
      within(screen.getByRole("dialog", { name: "快捷问知意" })).getByText(
        "保存的回答",
      ),
    ).toBeInTheDocument();
    expect(posted).toHaveLength(1);
  });

  it("remembers cloud consent for continuous sends and allows revocation", async () => {
    render(<Shell />);
    fireEvent.click(screen.getByRole("button", { name: "对话设置" }));
    await screen.findByRole("option", { name: /提取云模型/ });
    if (!screen.queryByRole("button", { name: "附加当前页面" }))
      fireEvent.click(screen.getByRole("button", { name: "对话设置" }));
    fireEvent.click(screen.getByRole("button", { name: "附加当前页面" }));
    fireEvent.click(screen.getByRole("button", { name: "清除资料范围" }));
    fireEvent.change(screen.getByRole("combobox", { name: "问知意模型方案" }), {
      target: { value: "cloud" },
    });
    if (screen.queryByRole("button", { name: "关闭对话设置" }))
      fireEvent.click(screen.getByRole("button", { name: "关闭对话设置" }));
    fireEvent.change(screen.getByRole("textbox", { name: "向问知意提问" }), {
      target: { value: "你好" },
    });
    expect(screen.getByRole("button", { name: "发送问题" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "允许这段对话" }));
    fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
    await screen.findByText("保存的回答");
    expect(posted[0].remote_consent).toBe("cloud:1");
    expect(posted[0].context).toEqual({});
    expect(screen.queryByRole("group", {name:"远程模型传输确认"})).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", {name:"向问知意提问"}), {target:{value:"继续"}});
    await waitFor(() => expect(screen.getByRole("button", {name:"发送问题"})).toBeEnabled());
    fireEvent.click(screen.getByRole("button", {name:"发送问题"}));
    await waitFor(() => expect(posted).toHaveLength(2));
    expect(posted[1].remote_consent).toBe("cloud:1");
    fireEvent.click(screen.getByRole("button", {name:"对话设置"}));
    await waitFor(() => expect(screen.getByRole("button", {name:"撤销对此服务的允许"})).toBeEnabled());
    fireEvent.click(screen.getByRole("button", {name:"撤销对此服务的允许"}));
    fireEvent.click(screen.getByRole("button", {name:"关闭对话设置"}));
    expect(screen.getByRole("button", {name:"允许这段对话"})).toBeEnabled();
    expect(readServiceConsentState(serviceConsentStorageKey(assistantBase)).keys).toEqual([]);
  });

  it("requires an explicit remember choice and carries it across a browser session without widening context", async () => {
    const first = render(<Shell />);
    await selectCloud();
    const storageKey = serviceConsentStorageKey(assistantBase);
    const remember = screen.getByRole("checkbox", { name: "在此设备记住对此模型服务的允许" });
    expect(remember).not.toBeChecked();
    fireEvent.click(remember);
    expect(localStorage.getItem(storageKey)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "允许并记住" }));
    expect(readServiceConsentState(storageKey).keys).toEqual([serviceConsentKey(remote)]);
    first.unmount();
    sessionStorage.clear();
    render(<Shell />);
    await selectCloud();
    expect(screen.queryByRole("group", { name: "远程模型传输确认" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "向问知意提问" }), { target: { value: "新对话" } });
    fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({ remote_consent: "cloud:1", context: {} });
  });

  it("keeps selected rows and read-only permissions when a remembered service is used", async () => {
    render(<Shell />);
    await selectCloud();
    rememberCloud();
    fireEvent.click(screen.getByRole("button", { name: "对话设置" }));
    fireEvent.click(await screen.findByRole("button", { name: "附加当前页面" }));
    fireEvent.click(screen.getByRole("button", { name: "只读分析" }));
    fireEvent.click(screen.getByRole("button", { name: "关闭对话设置" }));
    expect(screen.queryByRole("group", { name: "远程模型传输确认" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "向问知意提问" }), { target: { value: "只分析所选记录" } });
    fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0].context).toEqual({ table_id: "t", table_name: "合成数据", row_ids: [1, 2, 3], mode: "read" });
    expect(posted[0].remote_consent).toBe("cloud:1");
    fireEvent.click(screen.getByRole("button", { name: "对话设置" }));
    fireEvent.click(await screen.findByRole("button", { name: "撤销对此服务的允许" }));
    await waitFor(() => expect(authorizationCalls.at(-1)?.body).toMatchObject({
      remote_authorized: false, context: { table_id: "t", row_ids: [1, 2, 3], mode: "read" },
    }));
  });

  it("reconfirms when the same profile changes service path or model", async () => {
    const first = render(<Shell />);
    await selectCloud();
    rememberCloud();
    first.unmount();
    availableProfiles = [local, { ...remote, version: 2, base_url: "https://models.example.test/tenant-b/v1" }];
    const second = render(<Shell />);
    await selectCloud();
    expect(screen.getByRole("group", { name: "远程模型传输确认" })).toHaveTextContent("tenant-b");
    expect(screen.getByRole("checkbox", { name: "在此设备记住对此模型服务的允许" })).not.toBeChecked();
    rememberCloud();
    second.unmount();
    availableProfiles = [local, { ...remote, version: 3, base_url: "https://models.example.test/tenant-b/v1", model_name: "new-target" }];
    render(<Shell />);
    await selectCloud();
    expect(screen.getByRole("button", { name: "允许这段对话" })).toBeEnabled();
  });

  it("revokes remembered and old scoped session grants together, including across reload", async () => {
    const sessionKey = `zhiyi:remote-consent:${assistantBase}`;
    sessionStorage.setItem(sessionKey, JSON.stringify([
      consentKey("thread", remote, {}), consentKey("another", remote, { table_id: "t" }),
      JSON.stringify(["legacy", remote.id, 1, remote.base_url, {}]),
    ]));
    const first = render(<Shell />);
    await selectCloud();
    rememberCloud();
    fireEvent.click(screen.getByRole("button", { name: "对话设置" }));
    fireEvent.click(screen.getByRole("button", { name: "撤销对此服务的允许" }));
    expect(JSON.parse(sessionStorage.getItem(sessionKey) || "[]")).toEqual([]);
    expect(readServiceConsentState(serviceConsentStorageKey(assistantBase)).keys).toEqual([]);
    first.unmount();
    render(<Shell />);
    await selectCloud();
    expect(screen.getByRole("button", { name: "允许这段对话" })).toBeEnabled();
    expect(screen.getByRole("checkbox", { name: "在此设备记住对此模型服务的允许" })).not.toBeChecked();
  });

  it("uses only temporary approval and explains when browser storage is unavailable", async () => {
    render(<Shell />);
    await selectCloud();
    const unavailable = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("blocked", "SecurityError"); });
    rememberCloud();
    expect(screen.getByRole("status")).toHaveTextContent("浏览器未能记住此选择");
    fireEvent.change(screen.getByRole("textbox", { name: "向问知意提问" }), { target: { value: "本次允许" } });
    expect(screen.getByRole("button", { name: "发送问题" })).toBeEnabled();
    expect(localStorage.getItem(serviceConsentStorageKey(assistantBase))).toBeNull();
    unavailable.mockRestore();
  });

  it("applies another window's revocation to the current conversation", async () => {
    render(<Shell />);
    await selectCloud();
    rememberCloud();
    fireEvent.change(screen.getByRole("textbox", { name: "向问知意提问" }), { target: { value: "先提问" } });
    fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
    await screen.findByText("保存的回答");
    const key = serviceConsentStorageKey(assistantBase);
    localStorage.removeItem(key);
    act(() => window.dispatchEvent(new StorageEvent("storage", { key, newValue: null })));
    expect(screen.getByRole("button", { name: "允许这段对话" })).toBeEnabled();
    await waitFor(() => expect(authorizationCalls.at(-1)?.body.remote_authorized).toBe(false));
  });

  it.each(["another window", "this window"])("revokes a late first run after consent is withdrawn in %s", async (origin) => {
    const fetchBefore = globalThis.fetch;
    let release!: () => void;
    const delayed = new Promise<void>(resolve => { release = resolve; });
    const order: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/assistant/runs") && init?.method === "POST") {
        const response = await fetchBefore(input, init);
        stored!.runs[0].status = "running";
        await delayed;
        order.push("run-response");
        return response;
      }
      if (url.endsWith("/authorization") && init?.method === "PUT") {
        order.push("authorization");
        const body = JSON.parse(String(init.body));
        if (!body.remote_authorized) stored!.runs[0].status = "cancelled";
      }
      if (url.endsWith("/assistant/threads/thread")) order.push("thread-read");
      return fetchBefore(input, init);
    }));
    render(<Shell />);
    await selectCloud();
    if (origin === "another window") rememberCloud();
    else fireEvent.click(screen.getByRole("button", { name: "允许这段对话" }));
    const composer = screen.getByRole("textbox", { name: "向问知意提问" });
    expect(composer).toHaveFocus();
    fireEvent.change(composer, { target: { value: "等待模型连接" } });
    fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
    await waitFor(() => expect(posted).toHaveLength(1));
    if (origin === "another window") {
      const key = serviceConsentStorageKey(assistantBase);
      saveServiceConsent(key, serviceConsentKey(remote), false);
      act(() => window.dispatchEvent(new StorageEvent("storage", { key })));
    } else {
      fireEvent.click(screen.getByRole("button", { name: "对话设置" }));
      const revoke = screen.getByRole("button", { name: "撤销对此服务的允许" });
      expect(revoke).toBeEnabled();
      fireEvent.click(revoke);
      expect(screen.getByRole("group", { name: "模型服务传输允许" })).toHaveFocus();
      fireEvent.click(screen.getByRole("button", { name: "关闭对话设置" }));
    }
    expect(authorizationCalls).toHaveLength(0);
    await act(async () => release());
    await waitFor(() => expect(authorizationCalls.at(-1)?.body.remote_authorized).toBe(false));
    expect(order.slice(0, 3)).toEqual(["run-response", "authorization", "thread-read"]);
    expect(authorizationCalls.every(call => call.body.remote_authorized === false)).toBe(true);
    expect(stored!.runs[0].status).toBe("cancelled");
    await waitFor(() => expect(screen.getByRole("button", { name: "允许这段对话" })).toBeEnabled());
    expect(JSON.parse(sessionStorage.getItem(`zhiyi:remote-consent:${assistantBase}`) || "[]")).toEqual([]);
    fireEvent.change(composer, { target: { value: "继续" } });
    expect(screen.getByRole("button", { name: "发送问题" })).toBeDisabled();
  });

  it("repeats revocation after an existing thread's late run even if an earlier revoke already succeeded", async () => {
    render(<Shell />);
    await selectCloud();
    rememberCloud();
    fireEvent.change(screen.getByRole("textbox", { name: "向问知意提问" }), { target: { value: "第一轮" } });
    fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
    await screen.findByText("保存的回答");
    const fetchBefore = globalThis.fetch;
    let release!: () => void;
    const delayed = new Promise<void>(resolve => { release = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (input, init) => {
      const response = await fetchBefore(input, init);
      if (String(input).endsWith("/assistant/runs") && init?.method === "POST") {
        await delayed;
        stored!.runs[0].status = "running";
      } else if (String(input).endsWith("/authorization") && !JSON.parse(String(init?.body)).remote_authorized) {
        stored!.runs[0].status = "cancelled";
      }
      return response;
    }));
    fireEvent.change(screen.getByRole("textbox", { name: "向问知意提问" }), { target: { value: "第二轮延迟" } });
    fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
    await waitFor(() => expect(posted).toHaveLength(2));
    const key = serviceConsentStorageKey(assistantBase);
    saveServiceConsent(key, serviceConsentKey(remote), false);
    act(() => window.dispatchEvent(new StorageEvent("storage", { key })));
    await waitFor(() => expect(authorizationCalls.at(-1)?.body.remote_authorized).toBe(false));
    const beforeResponse = authorizationCalls.length;
    await act(async () => release());
    await waitFor(() => expect(authorizationCalls.length).toBeGreaterThan(beforeResponse));
    expect(authorizationCalls.at(-1)?.body.remote_authorized).toBe(false);
    expect(stored!.runs[0].status).toBe("cancelled");
  });

  it("does not revoke a pending request when another window withdraws permission for a different profile", async () => {
    const fetchBefore = globalThis.fetch;
    let release!: () => void;
    const delayed = new Promise<void>(resolve => { release = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (input, init) => {
      const response = await fetchBefore(input, init);
      if (String(input).endsWith("/assistant/runs") && init?.method === "POST") await delayed;
      return response;
    }));
    render(<Shell />);
    await selectCloud();
    rememberCloud();
    fireEvent.change(screen.getByRole("textbox", { name: "向问知意提问" }), { target: { value: "保留当前模型允许" } });
    fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
    await waitFor(() => expect(posted).toHaveLength(1));
    const key = serviceConsentStorageKey(assistantBase);
    saveServiceConsent(key, serviceConsentKey({ ...remote, id: "unrelated" }), false);
    act(() => window.dispatchEvent(new StorageEvent("storage", { key })));
    await act(async () => release());
    await screen.findByText("保存的回答");
    expect(authorizationCalls.filter(call => call.body.remote_authorized === false)).toEqual([]);
    expect(screen.queryByRole("group", { name: "远程模型传输确认" })).not.toBeInTheDocument();
  });

  it("does not restore a dormant scoped grant after another window revoked the profile", async () => {
    const key = serviceConsentStorageKey(assistantBase);
    const state = saveServiceConsent(key, serviceConsentKey(remote), true, false).state;
    const oldGrant = consentKey("thread", remote, {}, sessionConsentRevision(state, remote.id));
    sessionStorage.setItem(`zhiyi:remote-consent:${assistantBase}`, JSON.stringify([oldGrant]));
    saveServiceConsent(key, serviceConsentKey(remote), false);
    stored = { id: "thread", title: "旧窗口的对话", profile_id: "cloud", archived: false, updated_at: "2026-09-13T00:00:00Z",
      messages: [], runs: [], tools: [] };
    render(<Shell />);
    fireEvent.click(await screen.findByText("旧窗口的对话"));
    await screen.findByRole("button", { name: "允许这段对话" });
    fireEvent.change(screen.getByRole("textbox", { name: "向问知意提问" }), { target: { value: "旧许可不能沿用" } });
    expect(screen.getByRole("button", { name: "发送问题" })).toBeDisabled();
    await waitFor(() => expect(authorizationCalls.at(-1)?.body.remote_authorized).toBe(false));
  });

  it("lets the user retry a failed revocation while a late run is still active", async () => {
    const fetchBefore = globalThis.fetch;
    let release!: () => void;
    const delayed = new Promise<void>(resolve => { release = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/assistant/runs") && init?.method === "POST") {
        const response = await fetchBefore(input, init);
        stored!.runs[0].status = "running";
        await delayed;
        return response;
      }
      if (url.endsWith("/authorization") && init?.method === "PUT" && !failAuthorizationSync) {
        await fetchBefore(input, init);
        stored!.runs[0].status = "cancelled";
        return Response.json({ authorization_revision: authorizationCalls.length, invalidated_tools: 0, cancelled_runs: 1 });
      }
      return fetchBefore(input, init);
    }));
    render(<Shell />);
    await selectCloud();
    rememberCloud();
    fireEvent.change(screen.getByRole("textbox", { name: "向问知意提问" }), { target: { value: "撤销需要重试" } });
    fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
    await waitFor(() => expect(posted).toHaveLength(1));
    const key = serviceConsentStorageKey(assistantBase);
    saveServiceConsent(key, serviceConsentKey(remote), false);
    act(() => window.dispatchEvent(new StorageEvent("storage", { key })));
    failAuthorizationSync = true;
    await act(async () => release());
    await screen.findByText(/未能同步撤销的云端允许，请在对话设置中重试/);
    fireEvent.click(screen.getByRole("button", { name: "对话设置" }));
    const retry = await screen.findByRole("button", { name: "重试同步" });
    expect(retry).toBeEnabled();
    expect(screen.getByRole("button", { name: "停止回答" })).toBeEnabled();
    failAuthorizationSync = false;
    fireEvent.click(retry);
    await waitFor(() => expect(stored!.runs[0].status).toBe("cancelled"));
    await waitFor(() => expect(screen.queryByRole("button", { name: "停止回答" })).not.toBeInTheDocument());
    expect(authorizationCalls.every(call => call.body.remote_authorized === false)).toBe(true);
  });

  it("keeps revocation after a reload when session grant replacement and durable updates fail", async () => {
    const first = render(<Shell />);
    await selectCloud();
    rememberCloud();
    const set = Storage.prototype.setItem;
    const remove = Storage.prototype.removeItem;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, name, value) {
      if (this === localStorage || name === `zhiyi:remote-consent:${assistantBase}`) throw new DOMException("blocked", "SecurityError");
      set.call(this, name, value);
    });
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(function (this: Storage, name) {
      if (this === localStorage) throw new DOMException("blocked", "SecurityError");
      remove.call(this, name);
    });
    fireEvent.click(screen.getByRole("button", { name: "对话设置" }));
    fireEvent.click(screen.getByRole("button", { name: "撤销对此服务的允许" }));
    expect(screen.getAllByRole("status").some(node => node.textContent?.includes("刷新后仍有效"))).toBe(true);
    first.unmount();
    render(<Shell />);
    await selectCloud();
    expect(screen.getByRole("button", { name: "允许这段对话" })).toBeEnabled();
    expect(readServiceConsentState(serviceConsentStorageKey(assistantBase)).keys).toEqual([]);
  });

  it("renders historical numeric snapshots and closes only the nested chart dialog", async () => {
    const data = {
      analysis_id: "snapshot",
      source: {
        table_id: "t",
        table_name: "历史数据",
        row_count: 3,
        document_count: 2,
        grain: "auto",
        generated_at: "2026-09-13T00:00:00Z",
        request: {},
      },
      data: [{ label: "全部", "sum:total": 9600 }],
      metric_keys: ["sum:total"],
      truncated: false,
      warnings: [],
    };
    const onClose = vi.fn();
    render(
      <dialog open onClose={onClose}>
        <AnalysisCard analysis={data} navigate={vi.fn()} />
      </dialog>,
    );
    expect(screen.getByText("9,600")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /展开历史数据/ }));
    fireEvent.click(screen.getByRole("button", { name: "关闭展开图表" }));
    expect(onClose).not.toHaveBeenCalled();
    expect(posted).toHaveLength(0);
  });
  it("keeps settings out of the conversation and supports selected tables and keyboard resizing", async () => {
    render(<Shell />);
    expect(
      screen.queryByRole("combobox", { name: "问知意模型方案" }),
    ).not.toBeInTheDocument();
    const divider = screen.getByRole("separator", { name: "调整对话列表宽度" });
    const before = Number(divider.getAttribute("aria-valuenow"));
    fireEvent.keyDown(divider, { key: "ArrowRight" });
    expect(Number(divider.getAttribute("aria-valuenow"))).toBe(before + 16);
    fireEvent.click(screen.getByRole("button", { name: "对话设置" }));
    await screen.findByRole("checkbox", { name: /第二张表/ });
    fireEvent.click(screen.getByRole("checkbox", { name: /第二张表/ }));
    fireEvent.click(screen.getByRole("button", { name: "只读分析" }));
    fireEvent.change(screen.getByRole("combobox", { name: "问知意模型方案" }), {
      target: { value: "local" },
    });
    fireEvent.click(screen.getByRole("button", { name: "关闭对话设置" }));
    fireEvent.change(screen.getByRole("textbox", { name: "向问知意提问" }), {
      target: { value: "只看第二张表" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
    await screen.findByText("保存的回答");
    expect(posted[0].context).toEqual({
      workspace: false,
      table_ids: ["second"],
      mode: "read",
    });
  });
  it("delegation expires after the next successful send", async () => {
    render(<Shell />);
    fireEvent.click(screen.getByRole("button", { name: "对话设置" }));
    await screen.findByRole("option", { name: /本地聊天/ });
    fireEvent.change(screen.getByRole("combobox", { name: "问知意模型方案" }), {
      target: { value: "local" },
    });
    fireEvent.click(screen.getByRole("button", { name: "本次委托" }));
    fireEvent.click(screen.getByRole("button", { name: "关闭对话设置" }));
    fireEvent.change(screen.getByRole("textbox", { name: "向问知意提问" }), {
      target: { value: "你好" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
    await screen.findByText("保存的回答");
    expect((posted[0].context as { mode: string }).mode).toBe("delegate");
    fireEvent.click(screen.getByRole("button", { name: "对话设置" }));
    expect(screen.getByRole("button", { name: "协助操作" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });
});
