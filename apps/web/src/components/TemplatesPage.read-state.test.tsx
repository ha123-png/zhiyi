import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ExtractionTemplate, TemplateDraft } from "../types";
import { TemplatesPage } from "./TemplatesPage";

const template: ExtractionTemplate = {
  id: "existing", version: 1, is_system: false, builtin_key: null, source_template_id: null,
  is_active: true, name: "原有模板", description: "原有用途", extra_instructions: "",
  fields: [{ key: "value", label: "内容", section: "header", value_type: "text", example: "", instructions: "" }],
  validation_rules: [], deterministic_rules: [], output_mapping: {},
  created_at: "2026-10-30T00:00:00Z", updated_at: "2026-10-30T00:00:00Z",
};
const binding = { revision: 0, enabled: false, parent_path: null, destination: null };
const profile = (id: string) => ({ id, name: `合成方案 ${id}`, is_active: true, is_archived: false, provider: "openai_compatible", model_name: "synthetic" });
const generated = { name: "合成草稿", description: "未保存的AI草稿", fields: [{ key: "value", label: "内容", section: "header", example: "", value_type: "text" }], rule_suggestions: [] };
const http = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => { resolve = yes; });
  return { promise, resolve };
}
function mockRequests(handler: (url: string, init?: RequestInit) => Response | Promise<Response>) {
  const requests = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/local-export")) return http(binding);
    return handler(url, init);
  });
  vi.stubGlobal("fetch", requests);
  return requests;
}

describe("template catalog read boundaries", () => {
  it("shows a verified empty catalog as empty while keeping manual creation available", async () => {
    mockRequests(() => http([]));
    render(<TemplatesPage />);
    await screen.findByText("暂无模板");
    expect(screen.getByText(/共\s*0\s*个模板/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "新建空白模板" })).toBeEnabled();
  });

  it("distinguishes loading and failure from an empty catalog and retries in place", async () => {
    const first = deferred<Response>();
    let reads = 0;
    mockRequests(() => ++reads === 1 ? first.promise : http([template]));
    render(<TemplatesPage />);
    expect(screen.queryByText(/共\s*0\s*个模板/)).not.toBeInTheDocument();
    expect(screen.queryByText("暂无模板")).not.toBeInTheDocument();
    await act(async () => { first.resolve(http({ detail: "模板读取受阻" }, 503)); });
    fireEvent.click(await screen.findByRole("button", { name: "模板读取失败" }));
    expect(screen.getByRole("dialog", { name: "模板读取失败" })).toHaveTextContent("模板读取受阻");
    expect(screen.queryByText(/共\s*0\s*个模板/)).not.toBeInTheDocument();
    expect(screen.queryByText("暂无模板")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
    await screen.findByRole("heading", { name: template.name });
    expect(screen.getByText(/共\s*1\s*个模板/)).toBeInTheDocument();
    expect(reads).toBe(2);
  });

  it.each([false, true])("a late initial read preserves an explicit new draft (edited=%s) without asking to discard", async (edited) => {
    const pending = deferred<Response>();
    mockRequests(() => pending.promise);
    render(<TemplatesPage />);
    fireEvent.click(screen.getByRole("button", { name: "新建空白模板" }));
    expect(within(document.querySelector<HTMLElement>(".tpl-detail-actions")!).getByRole("button", { name: "复制" })).toBeDisabled();
    if (edited) fireEvent.change(screen.getByLabelText("用途说明"), { target: { value: "先写下的草稿" } });
    await act(async () => { pending.resolve(http([template])); });
    expect(screen.getByRole("heading", { name: "未命名模板" })).toBeInTheDocument();
    expect(screen.getByLabelText("用途说明")).toHaveValue(edited ? "先写下的草稿" : "");
    expect(screen.queryByRole("heading", { name: "修改尚未保存" })).not.toBeInTheDocument();
    expect(screen.getByText(template.name)).toBeInTheDocument();
  });

  it("consumes a handed draft once and does not replay it over newer edits when the catalog arrives", async () => {
    const pending = deferred<Response>();
    mockRequests(() => pending.promise);
    const handed: TemplateDraft = { name: "助手草稿", description: "手交内容", extra_instructions: "", fields: template.fields, validation_rules: [], deterministic_rules: [], output_mapping: {} };
    const consumed = vi.fn();
    render(<TemplatesPage initialDraft={handed} onDraftConsumed={consumed} />);
    fireEvent.change(screen.getByLabelText("用途说明"), { target: { value: "继续编辑的内容" } });
    await act(async () => { pending.resolve(http([template])); });
    expect(screen.getByLabelText("用途说明")).toHaveValue("继续编辑的内容");
    expect(consumed).toHaveBeenCalledOnce();
    expect(screen.queryByRole("heading", { name: "修改尚未保存" })).not.toBeInTheDocument();
  });

  it("does not replay an initial route selection over a newly started draft", async () => {
    const pending = deferred<Response>();
    mockRequests(() => pending.promise);
    render(<TemplatesPage initialTemplateId={template.id} initialVersion={1} />);
    fireEvent.click(screen.getByRole("button", { name: "新建空白模板" }));
    fireEvent.change(screen.getByLabelText("模板名称"), { target: { value: "新的工作" } });
    await act(async () => { pending.resolve(http([template])); });
    expect(screen.getByRole("heading", { name: "新的工作" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "修改尚未保存" })).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("still follows a later explicit template reference and protects existing unsaved edits", async () => {
    const other = { ...template, id: "other", name: "另一个模板" };
    mockRequests(() => http([template, other]));
    const view = render(<TemplatesPage />);
    await screen.findByRole("heading", { name: template.name });
    view.rerender(<TemplatesPage initialTemplateId={other.id} />);
    await screen.findByRole("heading", { name: other.name });
    fireEvent.change(screen.getByLabelText("用途说明"), { target: { value: "后续入口切换前的编辑" } });
    view.rerender(<TemplatesPage initialTemplateId={template.id} />);
    const confirm = await screen.findByRole("dialog", { name: "修改尚未保存" });
    fireEvent.click(within(confirm).getByRole("button", { name: "取消" }));
    expect(screen.getByRole("heading", { name: other.name })).toBeInTheDocument();
    expect(screen.getByLabelText("用途说明")).toHaveValue("后续入口切换前的编辑");
  });

  it("ignores an older catalog response after a create and its newer refresh have committed", async () => {
    const initial = deferred<Response>();
    const created = { ...template, id: "created", name: "已创建" };
    let reads = 0;
    let writes = 0;
    mockRequests((_url, init) => {
      if (init?.method === "POST") { writes++; return http(created); }
      return ++reads === 1 ? initial.promise : http([template, created]);
    });
    render(<TemplatesPage />);
    fireEvent.click(screen.getByRole("button", { name: "新建空白模板" }));
    fireEvent.change(screen.getByLabelText("模板名称"), { target: { value: created.name } });
    fireEvent.change(screen.getByLabelText("字段名"), { target: { value: "内容" } });
    fireEvent.click(screen.getByRole("button", { name: "创建模板" }));
    await screen.findByText("模板已保存（第 1 版）。");
    await act(async () => { initial.resolve(http([])); });
    expect(screen.getByText(/共\s*2\s*个模板/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: created.name })).toBeInTheDocument();
    expect(writes).toBe(1);
  });

  it("retains a committed copy on a failed refresh and retries only the read without replacing newer edits", async () => {
    const copied = { ...template, id: "copy", name: "新副本" };
    let reads = 0;
    let writes = 0;
    const retry = deferred<Response>();
    mockRequests((_url, init) => {
      if (init?.method === "POST") { writes++; return http(copied); }
      if (++reads === 1) return http([template]);
      if (reads === 2) return http({ detail: "刷新列表失败" }, 503);
      return retry.promise;
    });
    render(<TemplatesPage />);
    await screen.findByRole("heading", { name: template.name });
    fireEvent.click(within(document.querySelector<HTMLElement>(".tpl-detail-actions")!).getByRole("button", { name: "复制" }));
    await screen.findByText(/已创建可编辑副本/);
    expect(screen.getByRole("heading", { name: copied.name })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "模板读取失败" }));
    expect(screen.getByRole("dialog", { name: "模板读取失败" })).toHaveTextContent("刷新列表失败");
    expect(writes).toBe(1);
    fireEvent.change(screen.getByLabelText("用途说明"), { target: { value: "副本继续编辑" } });
    fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
    await act(async () => { retry.resolve(http([template, copied])); });
    expect(screen.getByLabelText("用途说明")).toHaveValue("副本继续编辑");
    expect(screen.queryByRole("heading", { name: "修改尚未保存" })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(writes).toBe(1);
  });
});

describe("AI profile reads", () => {
  it("blocks generation on loading/failure, retries metadata only, and retains requirement and samples", async () => {
    const pending = deferred<Response>();
    let profileReads = 0;
    let generationCalls = 0;
    mockRequests((url) => {
      if (url.endsWith("/models/profiles")) return ++profileReads === 1 ? pending.promise : http([profile("ready")]);
      if (url.endsWith("/templates/generate")) { generationCalls++; return http(generated); }
      return http([template]);
    });
    render(<TemplatesPage />);
    await screen.findByRole("heading", { name: template.name });
    fireEvent.click(screen.getByRole("button", { name: "AI 生成模板" }));
    const dialog = screen.getByRole("dialog", { name: "智能创建提取模板" });
    fireEvent.change(within(dialog).getByLabelText("需求描述"), { target: { value: "保留这次需求" } });
    fireEvent.change(dialog.querySelector('input[type="file"]')!, { target: { files: [new File(["synthetic"], "sample.png", { type: "image/png" })] } });
    expect(within(dialog).getByRole("button", { name: "生成模板" })).toBeDisabled();
    expect(within(dialog).queryByText(/未配置模型方案/)).not.toBeInTheDocument();
    await act(async () => { pending.resolve(http({ detail: "方案读取受阻" }, 503)); });
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("方案读取受阻");
    const generate = within(dialog).getByRole("button", { name: "生成模板" });
    expect(generate).toBeDisabled();
    fireEvent.click(generate);
    expect(generationCalls).toBe(0);
    fireEvent.click(within(dialog).getByRole("button", { name: "重新读取 AI 方案" }));
    await waitFor(() => expect(generate).toBeEnabled());
    expect(within(dialog).getByLabelText("需求描述")).toHaveValue("保留这次需求");
    expect(within(dialog).getByRole("button", { name: "移除样例 1 sample.png" })).toBeInTheDocument();
    expect(within(dialog).getByLabelText("AI 方案")).toHaveValue("ready");
    expect(profileReads).toBe(2);
    expect(generationCalls).toBe(0);
  });

  it("ignores a profile response from a closed dialog after a newer open has loaded", async () => {
    const old = deferred<Response>();
    let reads = 0;
    mockRequests((url) => url.endsWith("/models/profiles")
      ? ++reads === 1 ? old.promise : http([profile("new")]) : http([template]));
    render(<TemplatesPage />);
    await screen.findByRole("heading", { name: template.name });
    const opener = screen.getByRole("button", { name: "AI 生成模板" });
    fireEvent.click(opener);
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "关闭" }));
    fireEvent.click(opener);
    const dialog = screen.getByRole("dialog");
    await waitFor(() => expect(within(dialog).getByLabelText("AI 方案")).toHaveValue("new"));
    fireEvent.change(within(dialog).getByLabelText("需求描述"), { target: { value: "新一轮需求" } });
    await act(async () => { old.resolve(http([profile("old")])); });
    expect(within(dialog).getByLabelText("AI 方案")).toHaveValue("new");
    expect(within(dialog).getByLabelText("需求描述")).toHaveValue("新一轮需求");
  });

  it("keeps an AI draft committed when only the following catalog refresh fails", async () => {
    let reads = 0;
    let writes = 0;
    const saved = { ...template, id: "ai-created", name: generated.name, description: generated.description };
    mockRequests((url, init) => {
      if (url.endsWith("/models/profiles")) return http([profile("ready")]);
      if (url.endsWith("/templates/generate")) return http(generated);
      if (init?.method === "POST") { writes++; return http(saved); }
      return ++reads === 1 ? http([template]) : http({ detail: "仅列表刷新失败" }, 503);
    });
    render(<TemplatesPage />);
    await screen.findByRole("heading", { name: template.name });
    fireEvent.click(screen.getByRole("button", { name: "AI 生成模板" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("需求描述"), { target: { value: "合成生成需求" } });
    const generate = within(dialog).getByRole("button", { name: "生成模板" });
    await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(generate);
    fireEvent.click(await within(dialog).findByRole("button", { name: "保存模板" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByRole("heading", { name: saved.name })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "模板读取失败" }));
    expect(screen.getByRole("dialog", { name: "模板读取失败" })).toHaveTextContent("仅列表刷新失败");
    expect(writes).toBe(1);
  });

  it("locks an AI save against duplicate writes and retains the editable draft after write failure", async () => {
    const pending = deferred<Response>();
    let writes = 0;
    mockRequests((url, init) => {
      if (url.endsWith("/models/profiles")) return http([profile("ready")]);
      if (url.endsWith("/templates/generate")) return http(generated);
      if (init?.method === "POST") { writes++; return pending.promise; }
      return http([template]);
    });
    render(<TemplatesPage />);
    await screen.findByRole("heading", { name: template.name });
    fireEvent.click(screen.getByRole("button", { name: "AI 生成模板" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("需求描述"), { target: { value: "不能丢的需求" } });
    fireEvent.change(dialog.querySelector('input[type="file"]')!, { target: { files: [new File(["sample"], "kept.png", { type: "image/png" })] } });
    const generate = within(dialog).getByRole("button", { name: "生成模板" });
    await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(generate);
    const save = await within(dialog).findByRole("button", { name: "保存模板" });
    fireEvent.change(within(dialog).getByLabelText("模板名称"), { target: { value: "编辑后的草稿" } });
    fireEvent.click(save);
    fireEvent.click(save);
    expect(within(dialog).getByLabelText("模板名称")).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "关闭" })).toBeDisabled();
    await act(async () => { pending.resolve(http({ detail: "当前无法写入" }, 409)); });
    expect(within(dialog).getByRole("alert")).toHaveTextContent("当前无法写入");
    expect(within(dialog).getByLabelText("模板名称")).toHaveValue("编辑后的草稿");
    expect(within(dialog).getByLabelText("模板名称")).toBeEnabled();
    expect(writes).toBe(1);
    fireEvent.click(within(dialog).getByRole("button", { name: "重新生成" }));
    expect(within(dialog).getByLabelText("需求描述")).toHaveValue("不能丢的需求");
    expect(within(dialog).getByRole("button", { name: "移除样例 1 kept.png" })).toBeInTheDocument();
  });
});
