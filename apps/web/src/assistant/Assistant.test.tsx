import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
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
import type { ThreadDetail } from "./types";

let posted: Record<string, unknown>[];
let stored: ThreadDetail | null;
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
function ContextPage() {
  useAssistantPageContext("test", {
    table_id: "t",
    table_name: "合成数据",
    row_ids: [1, 2, 3],
  });
  return null;
}
function Shell() {
  return (
    <AssistantProvider>
      <ContextPage />
      <AssistantPage />
      <AssistantTrigger />
      <AssistantDrawer />
    </AssistantProvider>
  );
}
beforeEach(() => {
  posted = [];
  stored = null;
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
        return Response.json([local, remote]);
      if (url.endsWith("/assistant/settings"))
        return Response.json({ profile_id: null });
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

describe("问知意", () => {
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
    expect(screen.getByText(/上次回答使用：cloud-original/)).toBeVisible();
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
    fireEvent.click(screen.getByRole("button", { name: "数据与来源" }));
    fireEvent.click(screen.getByRole("button", { name: "按最新数据重新分析" }));
    expect(await screen.findByText(/更新于/)).toBeVisible();
    expect(refreshBodies).toHaveLength(1);
    expect(refreshBodies[0]).toEqual({ thread_id: "thread", request_id: expect.any(String), context: { workspace: true } });
    expect(posted).toHaveLength(0);
    expect(screen.getByRole("textbox", { name: "向问知意提问" })).toHaveValue("未发送的草稿");
    expect(screen.getAllByText("50,005,000").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByRole("button", { name: "重新回答" })).toHaveLength(1);
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
    expect(screen.getByText("暂无归档对话。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "对话设置" }));
    await screen.findByRole("checkbox", { name: "第二张表" });
    fireEvent.keyDown(screen.getByRole("tab", { name: "数据表" }), { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "文件" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "文件" })).toHaveFocus();
    await screen.findByRole("checkbox", { name: "收据原件.png" });
    fireEvent.keyDown(screen.getByRole("tab", { name: "文件" }), { key: "End" });
    await screen.findByRole("checkbox", { name: "收据模板" });
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
    fireEvent.click(screen.getByRole("button", { name: "清除范围" }));
    fireEvent.change(screen.getByRole("combobox", { name: "问知意模型方案" }), {
      target: { value: "cloud" },
    });
    if (screen.queryByRole("button", { name: "关闭对话设置" }))
      fireEvent.click(screen.getByRole("button", { name: "关闭对话设置" }));
    fireEvent.change(screen.getByRole("textbox", { name: "向问知意提问" }), {
      target: { value: "你好" },
    });
    expect(screen.getByRole("button", { name: "发送问题" })).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: /允许这段对话向/ }));
    fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
    await screen.findByText("保存的回答");
    expect(posted[0].remote_consent).toBe("cloud:1");
    expect(posted[0].context).toEqual({});
    expect(screen.queryByRole("checkbox", {name:/允许这段对话向/})).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", {name:"向问知意提问"}), {target:{value:"继续"}});
    await waitFor(() => expect(screen.getByRole("button", {name:"发送问题"})).toBeEnabled());
    fireEvent.click(screen.getByRole("button", {name:"发送问题"}));
    await waitFor(() => expect(posted).toHaveLength(2));
    expect(posted[1].remote_consent).toBe("cloud:1");
    fireEvent.click(screen.getByRole("button", {name:"对话设置"}));
    await waitFor(() => expect(screen.getByRole("button", {name:"撤销授权"})).toBeEnabled());
    fireEvent.click(screen.getByRole("button", {name:"撤销授权"}));
    fireEvent.click(screen.getByRole("button", {name:"关闭对话设置"}));
    expect(screen.getByRole("checkbox", {name:/允许这段对话向/})).not.toBeChecked();
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
