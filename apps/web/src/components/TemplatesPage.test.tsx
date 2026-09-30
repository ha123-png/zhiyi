import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ExtractionTemplate } from "../types";
import { TemplatesPage } from "./TemplatesPage";

const invoice: ExtractionTemplate = {
  id: "builtin-invoice",
  version: 1,
  is_system: true,
  builtin_key: "invoice",
  source_template_id: null,
  name: "发票",
  description: "整理发票内容",
  extra_instructions: "不确定时留空",
  fields: [
    {
      key: "seller_name",
      label: "销售方",
      section: "header",
      example: "某某公司",
      instructions: "",
      value_type: "text",
    },
  ],
  validation_rules: [],
  deterministic_rules: [],
  output_mapping: {},
  created_at: "2026-07-30T00:00:00Z",
  updated_at: "2026-07-30T00:00:00Z",
};

describe("TemplatesPage", () => {
  it("retains separately selected same-name samples and explains unsupported files", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      const url = String(input);
      if (url.endsWith("/models/profiles")) return response([]);
      if (url.endsWith("/local-export")) return response({ revision: 0, enabled: false });
      return response([invoice]);
    }));
    render(<TemplatesPage />);
    await screen.findByRole("heading", { name: "发票" });
    fireEvent.click(screen.getByRole("button", { name: "AI 生成模板" }));
    const dialog = screen.getByRole("dialog");
    const input = dialog.querySelector('input[type="file"]')!;
    fireEvent.change(input, { target: { files: [new File(["page one"], "scan.png", { type: "image/png" })] } });
    fireEvent.change(input, { target: { files: [new File(["different page two"], "scan.png", { type: "image/png" })] } });
    expect(within(dialog).getByRole("button", { name: "移除样例 1 scan.png" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "移除样例 2 scan.png" })).toBeInTheDocument();
    fireEvent.change(input, { target: { files: [new File(["text"], "source.txt", { type: "text/plain" })] } });
    expect(within(dialog).getByText(/有 1 个样例未添加/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "移除样例 1 scan.png" }));
    expect(within(dialog).getAllByRole("button", { name: /移除样例/ })).toHaveLength(1);
  });

  it("keeps keyboard focus in the AI dialog, closes with Escape, and restores its opener repeatedly", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      const url = String(input);
      if (url.endsWith("/models/profiles")) return response([]);
      if (url.endsWith("/local-export")) return response({ revision: 0, enabled: false });
      return response([invoice]);
    }));
    render(<TemplatesPage />);
    await screen.findByRole("heading", { name: "发票" });
    const opener = screen.getByRole("button", { name: "AI 生成模板" });
    for (let round = 0; round < 3; round += 1) {
      opener.focus();
      fireEvent.click(opener);
      const dialog = screen.getByRole("dialog", { name: "智能创建提取模板" });
      const close = within(dialog).getByRole("button", { name: "关闭" });
      const cancel = within(dialog).getByRole("button", { name: "取消" });
      expect(close).toHaveFocus();
      fireEvent.keyDown(close, { key: "Tab", shiftKey: true });
      expect(cancel).toHaveFocus();
      fireEvent.keyDown(cancel, { key: "Tab" });
      expect(close).toHaveFocus();
      fireEvent.keyDown(close, { key: "Escape" });
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(opener).toHaveFocus();
    }
  });

  it("protects an edited template on selection and navigation, with a working cancel path", async () => {
    const custom = { ...invoice, id: "custom", name: "验收模板", is_system: false, builtin_key: null };
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => String(input).endsWith("/local-export")
      ? response({ revision: 0, enabled: false, parent_path: null, destination: null }) : response([custom, invoice])));
    const { container } = render(<TemplatesPage />);
    await screen.findByRole("heading", { name: "验收模板" });
    fireEvent.change(screen.getByLabelText("用途说明"), { target: { value: "不能丢失的编辑" } });
    const other = [...container.querySelectorAll(".template-list-item")].find(e => e.textContent?.includes("发票"))!;
    fireEvent.click(other);
    expect(await screen.findByRole("heading", { name: "修改尚未保存" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.getByLabelText("用途说明")).toHaveValue("不能丢失的编辑");
    const proceed = vi.fn();
    fireEvent(window, new CustomEvent("zhiyi:before-navigate", { cancelable: true, detail: { proceed } }));
    fireEvent.click(await screen.findByRole("button", { name: "放弃修改并继续" }));
    expect(proceed).toHaveBeenCalledOnce();
  });

  it("saves a folder binding through Save without creating a template version", async () => {
    const template = { ...invoice, id: "local-template", is_system: false, builtin_key: null };
    const fetchMock = vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/templates?include_inactive=true")) return response([template]);
      if (url.endsWith("/local-export")) return response(init?.method === "PUT"
        ? { revision: 1, enabled: true, parent_path: "D:\\副本", destination: "D:\\副本\\发票" }
        : { revision: 0, enabled: false, parent_path: null, destination: null });
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<TemplatesPage />);
    await screen.findByRole("heading", { name: "发票" });
    fireEvent.click(screen.getByText("高级设置"));
    const toggle = screen.getByRole("combobox", { name: "原文件处理" });
    await waitFor(() => expect(toggle).toBeEnabled());
    fireEvent.change(toggle, { target: { value: "move" } });
    fireEvent.click(screen.getByRole("button", { name: "设置服务端目录" }));
    fireEvent.change(screen.getByLabelText("知意服务所在电脑的绝对路径"), { target: { value: "D:\\副本" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("设置已保存。");
    const writes = fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT");
    expect(writes).toHaveLength(1);
    expect(String(writes[0][0])).toMatch(/\/local-export$/);
    expect(JSON.parse(writes[0][1]!.body as string)).toEqual({ expected_revision: 0, enabled: true, mode: "move", parent_path: "D:\\副本" });
  });

  beforeEach(() => {
    const copy = {
      ...invoice,
      id: "custom-invoice",
      is_system: false,
      builtin_key: null,
      source_template_id: invoice.id,
      name: "发票 副本",
    };
    const updated = { ...copy, version: 2, name: "门店发票" };
    let listCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (input: string | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/local-export")) return { ok: true, json: async () => ({ revision: 0, enabled: false, parent_path: null, destination: null }) };
        if (url.endsWith("/templates?include_inactive=true") && !init?.method) {
          listCount += 1;
          const templates =
            listCount === 1 ? [invoice] : listCount === 2 ? [invoice, copy] : [invoice, updated];
          return response(templates);
        }
        if (url.endsWith("/builtin-invoice/copy")) {
          return response(copy);
        }
        if (url.endsWith("/custom-invoice") && init?.method === "PUT") {
          return response(updated);
        }
        throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
      }),
    );
  });

  it("keeps the selected template and local binding stable while a save is pending", async () => {
    const custom = { ...invoice, id: "custom", name: "物资", is_system: false, builtin_key: null };
    let finishWrite!: (value: ReturnType<typeof response>) => void;
    const pendingWrite = new Promise<ReturnType<typeof response>>(resolve => { finishWrite = resolve; });
    const fetchMock = vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/templates?include_inactive=true")) return response([custom, invoice]);
      if (url.endsWith("/local-export")) return response({ revision: 0, enabled: false, parent_path: null, destination: null });
      if (url.endsWith("/templates/custom") && init?.method === "PUT") return pendingWrite;
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(<TemplatesPage />);
    await screen.findByRole("heading", { name: "物资" });
    fireEvent.change(screen.getByLabelText("用途说明"), { target: { value: "更新用途" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "新建空白模板" })).toBeDisabled());
    const target = [...container.querySelectorAll(".template-list-item")].find(e => e.textContent?.includes("发票"))!;
    expect(target).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(target);
    fireEvent.keyDown(target, { key: "Enter" });
    expect(screen.getByRole("heading", { name: "物资" })).toBeInTheDocument();
    finishWrite(response({ ...custom, version: 2 }));
    await screen.findByText("模板已保存（第 2 版）。");
    fireEvent.click(target);
    await screen.findByRole("heading", { name: "发票" });
    expect(screen.getByLabelText("模板名称")).toBeDisabled();
  });

  it("keeps field examples mounted so closing can animate without losing form values", async () => {
    const { container } = render(<TemplatesPage />);
    await screen.findByRole("heading", { name: "发票" });
    fireEvent.click(screen.getByRole("button", { name: "展开示例" }));
    const example = screen.getByRole("textbox", { name: "字段 1 示例" });
    fireEvent.click(screen.getByRole("button", { name: "收起示例" }));
    expect(screen.queryByRole("textbox", { name: "字段 1 示例" })).not.toBeInTheDocument();
    expect(container.querySelector('[aria-label="字段 1 示例"]')).toBe(example);
    expect(container.querySelector("#template-field-detail-0")).toHaveAttribute("inert");
  });

  it("copies a system template before editing and saves a new version", async () => {
    const { container } = render(<TemplatesPage />);

    expect(await screen.findByRole("heading", { name: "发票" })).toBeInTheDocument();
    expect(screen.getByLabelText("模板名称")).toBeDisabled();
    expect(screen.getByRole("button", { name: "导入" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "导出" })).toBeEnabled();
    expect(
      screen.getByLabelText("额外提示词"),
    ).toBeInTheDocument();
    expect(container.querySelector('input[type="file"]')).toHaveClass("visually-hidden");
    expect(container.querySelector(".template-list-item")).toContainElement(
      container.querySelector(".tpl-item-main"),
    );

    fireEvent.click(screen.getByRole("button", { name: "复制后编辑" }));

    const name = await screen.findByLabelText("模板名称");
    expect(name).toBeEnabled();
    expect(name).toHaveValue("发票 副本");
    fireEvent.change(name, { target: { value: "门店发票" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(
      await screen.findByText("模板已保存（第 2 版）。"),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "门店发票" })).toBeInTheDocument(),
    );
  });

  it("uses the native bridge when exporting a template in the desktop app", async () => {
    const exportTemplate = vi.fn().mockResolvedValue("D:\\知意导出\\发票.template.json");
    window.pywebview = {
      api: {
        get_export_directory: vi.fn(),
        choose_export_directory: vi.fn(),
        export_table: vi.fn(),
        export_template: exportTemplate,
        download_task_file: vi.fn(),
      },
    };

    render(<TemplatesPage />);
    await screen.findByRole("heading", { name: "发票" });
    fireEvent.click(screen.getByRole("button", { name: "导出" }));

    await waitFor(() => expect(exportTemplate).toHaveBeenCalledTimes(1));
    expect(exportTemplate.mock.calls[0][0]).toBe("发票.template.json");
    expect(JSON.parse(exportTemplate.mock.calls[0][1])).toMatchObject({
      format: "document-pipeline-template",
      template: { name: "发票" },
    });
    expect(await screen.findByText(/模板已导出到 D:/)).toBeInTheDocument();
    delete window.pywebview;
  });

  it("stays honest when the template service is unavailable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("本地模板服务未连接")),
    );

    render(<TemplatesPage />);

    expect(await screen.findByText("本地模板服务未连接")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "未命名模板" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "创建模板" })).toBeDisabled();
    expect(screen.queryByText("第 0 版")).not.toBeInTheDocument();
  });

  it("opens templates returned by an older api without deterministic rules", async () => {
    const legacy = { ...invoice } as Partial<ExtractionTemplate>;
    delete legacy.deterministic_rules;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(response([legacy])),
    );

    render(<TemplatesPage />);

    expect(await screen.findByRole("heading", { name: "发票" })).toBeInTheDocument();
    expect(screen.getByText("暂无校验规则。"))
      .toBeInTheDocument();
  });

  it("preserves deterministic rules when importing a template", async () => {
    const rule = {
      kind: "required" as const,
      field: "header.seller_name",
    };
    const imported = {
      ...invoice,
      id: "imported-template",
      is_system: false,
      builtin_key: null,
      name: "带检查的模板",
      deterministic_rules: [rule],
    };
    let submitted: Record<string, unknown> | undefined;
    let listCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (input: string | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/templates?include_inactive=true") && !init?.method) {
          listCount += 1;
          return response(listCount === 1 ? [invoice] : [invoice, imported]);
        }
        if (url.endsWith("/templates") && init?.method === "POST") {
          submitted = JSON.parse(String(init.body)) as Record<string, unknown>;
          return response(imported);
        }
        throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
      }),
    );
    const { container } = render(<TemplatesPage />);
    await screen.findByRole("heading", { name: "发票" });
    const input = container.querySelector<HTMLInputElement>('input[type="file"]');
    const file = new File([], "template.json", { type: "application/json" });
    Object.defineProperty(file, "text", {
      value: async () => JSON.stringify({
        format: "document-pipeline-template",
        version: 1,
        template: {
          ...invoice,
          name: imported.name,
          deterministic_rules: [rule],
        },
      }),
    });

    fireEvent.change(input!, { target: { files: [file] } });

    expect(await screen.findByText("已导入模板“带检查的模板”。")).toBeInTheDocument();
    expect(submitted?.deterministic_rules).toEqual([rule]);
  });

  it("archives and restores a user template without deleting it", async () => {
    const active = {
      ...invoice,
      id: "user-template",
      is_system: false,
      is_active: true,
      builtin_key: null,
      name: "门店模板",
    };
    const inactive = { ...active, is_active: false };
    let current = active;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (input: string | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/templates?include_inactive=true") && !init?.method) {
          return response([current]);
        }
        if (url.endsWith("/user-template/archive") && init?.method === "POST") {
          current = inactive;
          return response(inactive);
        }
        if (url.endsWith("/user-template/restore") && init?.method === "POST") {
          current = active;
          return response(active);
        }
        throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
      }),
    );
    render(<TemplatesPage />);
    await screen.findByRole("heading", { name: "门店模板" });

    fireEvent.click(screen.getByTitle("停用模板"));
    expect(await screen.findByText("自定义模板 · 已停用 · 1 个字段 · 整理发票内容"))
      .toBeInTheDocument();
    expect(screen.getByLabelText("模板名称")).toBeDisabled();

    fireEvent.click(screen.getByTitle("恢复模板"));
    await waitFor(() => expect(screen.getByLabelText("模板名称")).toBeEnabled());
  });

  it("uses section-aware identities when header and item share a field key", async () => {
    const template = {
      ...invoice,
      fields: [
        { ...invoice.fields[0], key: "tax_amount", label: "整单税额", section: "header" as const },
        { ...invoice.fields[0], key: "tax_amount", label: "明细税额", section: "item" as const },
      ],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(response([template])),
    );
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    render(<TemplatesPage />);

    expect(await screen.findByDisplayValue("整单税额")).toBeInTheDocument();
    expect(screen.getByDisplayValue("明细税额")).toBeInTheDocument();
    expect(
      consoleError.mock.calls.some((args) => args.join(" ").includes("same key")),
    ).toBe(false);
    consoleError.mockRestore();
  });
});

function response(body: unknown) {
  return Promise.resolve({
    ok: true,
    json: async () => body,
  });
}

describe("template action transactions", () => {
  const template: ExtractionTemplate = { ...invoice, id: "editable-template", name: "编辑中模板", is_system: false, is_active: true };
  const binding = { revision: 0, enabled: false, parent_path: null, destination: null };
  const http = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

  it("cancels deletion without a request, keeps a failed confirmation open, and prevents duplicate requests while busy", async () => {
    let resolveFirstDelete!: (response: Response) => void;
    const firstDelete = new Promise<Response>(resolve => { resolveFirstDelete = resolve; });
    let deleteCalls = 0;
    let deleted = false;
    const fetchMock = vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/local-export")) return http(binding);
      if (init?.method === "DELETE") {
        deleteCalls += 1;
        if (deleteCalls === 1) return firstDelete;
        deleted = true;
        return new Response(null, { status: 204 });
      }
      return http(deleted ? [] : [template]);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<TemplatesPage />);
    await screen.findByRole("heading", { name: template.name });

    const openDelete = () => fireEvent.click(screen.getByTitle("删除模板入口，保留已有文件、当前数据和历史规则"));
    openDelete();
    const dialog = await screen.findByRole("dialog", { name: "确定删除这个模板吗？" });
    expect(within(dialog).getByText(/已有数据表、文件和修订历史保留/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(deleteCalls).toBe(0);
    expect(screen.getByRole("heading", { name: template.name })).toBeInTheDocument();

    openDelete();
    const confirm = within(await screen.findByRole("dialog", { name: "确定删除这个模板吗？" })).getByRole("button", { name: "删除模板" });
    fireEvent.click(confirm);
    await waitFor(() => expect(deleteCalls).toBe(1));
    expect(confirm).toBeDisabled();
    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
    fireEvent.click(confirm);
    expect(deleteCalls).toBe(1);

    await act(async () => {
      resolveFirstDelete(http({ detail: "暂时无法删除" }, 409));
      await firstDelete;
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("暂时无法删除");
    expect(screen.getByRole("dialog", { name: "确定删除这个模板吗？" })).toBeInTheDocument();
    expect(deleteCalls).toBe(1);

    fireEvent.click(screen.getByRole("button", { name: "删除模板" }));
    await waitFor(() => expect(deleteCalls).toBe(2));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "确定删除这个模板吗？" })).not.toBeInTheDocument());
    expect(await screen.findByText("模板已删除。")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url, init]) => init?.method === "DELETE" && String(url).endsWith(`/templates/${template.id}`))).toBe(true);
  });

  it("lets the user keep an AI draft or explicitly discard it without a native confirm", async () => {
    const generatedDraft = {
      name: "本地模拟草稿",
      description: "synthetic response",
      fields: [{ key: "invoice_no", label: "发票号码", section: "header", example: "", value_type: "text" }],
      rule_suggestions: [],
    };
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      const url = String(input);
      if (url.endsWith("/models/profiles")) return http([]);
      if (url.endsWith("/local-export")) return http(binding);
      if (url.endsWith("/templates/generate")) return http(generatedDraft);
      return http([invoice]);
    }));
    render(<TemplatesPage />);
    await screen.findByRole("heading", { name: "发票" });
    const opener = screen.getByRole("button", { name: "AI 生成模板" });
    opener.focus();
    fireEvent.click(opener);
    let aiDialog = screen.getByRole("dialog", { name: "智能创建提取模板" });
    fireEvent.change(within(aiDialog).getByLabelText("需求描述"), { target: { value: "只使用本地合成响应验收 UI" } });
    fireEvent.click(within(aiDialog).getByRole("button", { name: "生成模板" }));
    await within(aiDialog).findByRole("button", { name: "保存模板" });

    let closeDraft = within(aiDialog).getByRole("button", { name: "关闭" });
    closeDraft.focus();
    fireEvent.click(closeDraft);
    let discardDialog = await screen.findByRole("dialog", { name: "模板尚未保存" });
    fireEvent.click(within(discardDialog).getByRole("button", { name: "取消" }));
    expect(closeDraft).toHaveFocus();
    expect(screen.getByRole("dialog", { name: "智能创建提取模板" })).toBeInTheDocument();
    expect(within(aiDialog).getByRole("button", { name: "保存模板" })).toBeInTheDocument();

    closeDraft = within(aiDialog).getByRole("button", { name: "关闭" });
    closeDraft.focus();
    fireEvent.click(closeDraft);
    discardDialog = await screen.findByRole("dialog", { name: "模板尚未保存" });
    fireEvent.click(within(discardDialog).getByRole("button", { name: "不保存并关闭" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "智能创建提取模板" })).not.toBeInTheDocument());
    expect(opener).toHaveFocus();
  });

  it("stops waiting without claiming to cancel remote work and ignores a late result after a newer generation starts", async () => {
    const generatedA = {
      name: "旧请求草稿",
      description: "late synthetic response",
      fields: [{ key: "old_field", label: "旧字段", section: "header", example: "", value_type: "text" }],
      rule_suggestions: [],
    };
    const generatedB = {
      name: "新请求草稿",
      description: "current synthetic response",
      fields: [{ key: "new_field", label: "新字段", section: "header", example: "", value_type: "text" }],
      rule_suggestions: [],
    };
    const pendingGenerations: Array<(result: Response) => void> = [];
    const generationCalls: Array<{ requirement: string; signal: AbortSignal | null }> = [];
    let createCalls = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/models/profiles")) return http([]);
      if (url.endsWith("/local-export")) return http(binding);
      if (url.endsWith("/templates/generate")) {
        const form = init?.body as FormData;
        generationCalls.push({ requirement: String(form.get("requirement")), signal: init?.signal ?? null });
        // Deliberately ignore abort: a response already in flight may still arrive.
        return new Promise<Response>((resolve) => pendingGenerations.push(resolve));
      }
      if (init?.method === "POST") createCalls += 1;
      return http([template]);
    }));

    render(<TemplatesPage />);
    await screen.findByRole("heading", { name: template.name });
    fireEvent.click(screen.getByRole("button", { name: "AI 生成模板" }));
    const dialog = screen.getByRole("dialog", { name: "智能创建提取模板" });
    const requirement = within(dialog).getByLabelText("需求描述");
    fireEvent.change(requirement, { target: { value: "第一次合成请求" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "生成模板" }));
    await waitFor(() => expect(pendingGenerations).toHaveLength(1));

    fireEvent.click(within(dialog).getByRole("button", { name: "停止等待并返回编辑" }));
    const requirementAfterStop = within(dialog).getByLabelText("需求描述");
    expect(requirementAfterStop).toHaveValue("第一次合成请求");
    expect(await within(dialog).findByRole("status")).toHaveTextContent("模型服务可能仍在处理刚才的请求");
    expect(generationCalls[0].requirement).toBe("第一次合成请求");
    expect(generationCalls[0].signal?.aborted).toBe(true);

    fireEvent.change(requirementAfterStop, { target: { value: "第二次合成请求" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "生成模板" }));
    await waitFor(() => expect(pendingGenerations).toHaveLength(2));
    expect(generationCalls.map((call) => call.requirement)).toEqual(["第一次合成请求", "第二次合成请求"]);
    expect(generationCalls[1].signal?.aborted).toBe(false);
    expect(generationCalls[1].signal).not.toBe(generationCalls[0].signal);

    await act(async () => {
      pendingGenerations[0](http(generatedA));
    });
    expect(within(dialog).getByText("正在分析样例文件，请稍候…")).toBeInTheDocument();
    expect(within(dialog).queryByLabelText("模板名称")).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "停止等待并返回编辑" })).toBeEnabled();

    await act(async () => {
      pendingGenerations[1](http(generatedB));
    });
    await waitFor(() => expect(within(dialog).getByLabelText("模板名称")).toHaveValue("新请求草稿"));
    expect(within(dialog).queryByText("旧请求草稿")).not.toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "关闭" }));
    const discard = await screen.findByRole("dialog", { name: "模板尚未保存" });
    fireEvent.click(within(discard).getByRole("button", { name: "不保存并关闭" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "智能创建提取模板" })).not.toBeInTheDocument());
    expect(createCalls).toBe(0);
  });

  it.each(["archive", "copy", "restore", "import"] as const)("protects dirty input before %s and cancels without a server mutation", async (action) => {
    const inactive = { ...template, id: "inactive-template", name: "停用中模板", is_active: false };
    let changed = false;
    const result = { ...template, id: action === "copy" || action === "import" ? "created-template" : action === "restore" ? inactive.id : template.id,
      name: action === "copy" || action === "import" ? "新模板" : action === "restore" ? inactive.name : template.name,
      is_active: action !== "archive" };
    const fetchMock = vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/local-export")) return http(binding);
      if (init?.method === "POST") { changed = true; return http(result); }
      return http(changed ? [result] : [template, inactive]);
    });
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(<TemplatesPage />);
    await screen.findByRole("heading", { name: template.name });
    fireEvent.change(screen.getByLabelText("用途说明"), { target: { value: "必须保留的草稿" } });
    const trigger = () => {
      if (action === "archive") fireEvent.click(screen.getByTitle("停用模板"));
      else if (action === "copy") fireEvent.click(within(container.querySelector<HTMLElement>(".tpl-detail-actions")!).getByRole("button", { name: "复制" }));
      else if (action === "restore") fireEvent.click(screen.getByTitle("恢复"));
      else {
        const file = new File([], "audit.template.json", { type: "application/json" });
        Object.defineProperty(file, "text", { value: async () => JSON.stringify({ template: { ...template, name: "新模板" } }) });
        fireEvent.change(container.querySelector<HTMLInputElement>('input[type="file"]')!, { target: { files: [file] } });
      }
    };
    trigger();
    await screen.findByRole("heading", { name: "修改尚未保存" });
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.getByLabelText("用途说明")).toHaveValue("必须保留的草稿");
    expect(changed).toBe(false);
    trigger();
    fireEvent.click(await screen.findByRole("button", { name: "放弃修改并继续" }));
    await waitFor(() => expect(screen.getByLabelText("用途说明")).toHaveValue(template.description));
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
    expect(screen.queryByRole("heading", { name: "修改尚未保存" })).not.toBeInTheDocument();
  });

  it("keeps the new definition baseline and binding input after a partial save, then saves further edits", async () => {
    let current = { ...template };
    const definitions: Array<{ expected_version: number; expected_updated_at: string; description: string }> = [];
    let bindingWrites = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/local-export")) {
        if (init?.method !== "PUT") return http(binding);
        bindingWrites += 1;
        return bindingWrites === 1 ? http({ detail: "目录暂时不可写" }, 422)
          : http({ ...binding, revision: 1, enabled: true, mode: "move", parent_path: "D:\\已修复目录" });
      }
      if (init?.method === "PUT") {
        const body = JSON.parse(String(init.body)); definitions.push(body);
        if (body.expected_version !== current.version || body.expected_updated_at !== current.updated_at) return http({ detail: "模板已变化" }, 409);
        current = { ...current, ...body, version: current.version + 1, updated_at: `2026-09-27T00:00:0${current.version}Z` };
        return http(current);
      }
      return http([current]);
    }));
    render(<TemplatesPage />);
    await screen.findByRole("heading", { name: template.name });
    fireEvent.change(screen.getByLabelText("用途说明"), { target: { value: "第一次定义更新" } });
    fireEvent.click(screen.getByText("高级设置"));
    const toggle = screen.getByRole("combobox", { name: "原文件处理" });
    await waitFor(() => expect(toggle).toBeEnabled());
    fireEvent.change(toggle, { target: { value: "move" } });
    fireEvent.click(screen.getByRole("button", { name: "设置服务端目录" }));
    fireEvent.change(screen.getByLabelText("知意服务所在电脑的绝对路径"), { target: { value: "D:\\暂不可写目录" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await screen.findAllByText("目录暂时不可写");
    expect(screen.getByLabelText("知意服务所在电脑的绝对路径")).toHaveValue("D:\\暂不可写目录");
    const savedAt = current.updated_at;
    fireEvent.change(screen.getByLabelText("用途说明"), { target: { value: "继续补充的新说明" } });
    fireEvent.change(screen.getByLabelText("知意服务所在电脑的绝对路径"), { target: { value: "D:\\已修复目录" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("模板已保存（第 3 版）。");
    expect(definitions).toHaveLength(2);
    expect(definitions[1]).toMatchObject({ expected_version: 2, expected_updated_at: savedAt, description: "继续补充的新说明" });
    expect(current.description).toBe("继续补充的新说明");
    expect(bindingWrites).toBe(2);
    const navigate = new CustomEvent("zhiyi:before-navigate", { cancelable: true, detail: { proceed: vi.fn() } });
    fireEvent(window, navigate);
    expect(navigate.defaultPrevented).toBe(false);
    expect(screen.queryByRole("heading", { name: "修改尚未保存" })).not.toBeInTheDocument();
  });

  it("also protects unsaved local settings and retains them when a confirmed action fails", async () => {
    let writes = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      if (String(input).endsWith("/local-export")) return http(binding);
      if (init?.method === "POST") { writes += 1; return http({ detail: "暂时不能停用" }, 409); }
      return http([template]);
    }));
    render(<TemplatesPage />);
    await screen.findByRole("heading", { name: template.name });
    fireEvent.click(screen.getByText("高级设置"));
    const toggle = screen.getByRole("combobox", { name: "原文件处理" });
    await waitFor(() => expect(toggle).toBeEnabled());
    fireEvent.change(toggle, { target: { value: "copy" } });
    fireEvent.click(screen.getByTitle("停用模板"));
    fireEvent.click(await screen.findByRole("button", { name: "取消" }));
    expect(writes).toBe(0);
    expect(toggle).toHaveValue("copy");
    fireEvent.click(screen.getByTitle("停用模板"));
    fireEvent.click(await screen.findByRole("button", { name: "放弃修改并继续" }));
    await screen.findByText("暂时不能停用");
    expect(writes).toBe(1);
    expect(toggle).toHaveValue("copy");
  });
});

describe("template pattern and naming regressions", () => {
  it("keeps an invalid pattern out of the template and saves the corrected fifth rule", async () => {
    let current: ExtractionTemplate = { ...invoice, id: "pattern-template", name: "格式模板", is_system: false, is_active: true,
      deterministic_rules: Array.from({ length: 4 }, () => ({ kind: "required" as const, field: "header.seller_name" })) };
    let savedRules: unknown;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/local-export")) return response({ revision: 0, enabled: false, parent_path: null, destination: null });
      if (url.endsWith("/preview-pattern")) {
        const body = JSON.parse(String(init?.body));
        return response(body.pattern === "(" ? { valid: false, matches: false, message: "表达式无效；不支持分组。" }
          : { valid: true, matches: false, message: "整段匹配不通过。" });
      }
      if (init?.method === "PUT") {
        const body = JSON.parse(String(init.body)); savedRules = body.deterministic_rules;
        current = { ...current, ...body, version: 2 };
        return response(current);
      }
      return response([current]);
    }));
    render(<TemplatesPage />);
    await screen.findByRole("heading", { name: current.name });
    fireEvent.click(screen.getByText("高级设置"));
    fireEvent.change(screen.getByLabelText("规则类型"), { target: { value: "pattern" } });
    fireEvent.change(screen.getByLabelText("检查字段"), { target: { value: "header.seller_name" } });
    fireEvent.change(screen.getByLabelText("格式表达式（正则）"), { target: { value: "(" } });
    fireEvent.click(screen.getByRole("button", { name: "添加校验规则" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("规则尚未添加");
    expect(screen.getByText("4 条")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("格式表达式（正则）"), { target: { value: "PO-[0-9]{3}" } });
    fireEvent.click(screen.getByRole("button", { name: "添加校验规则" }));
    await screen.findByText("5 条");
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("模板已保存（第 2 版）。");
    expect(savedRules).toEqual([...Array.from({ length: 4 }, () => ({ kind: "required", field: "header.seller_name" })),
      { kind: "pattern", field: "header.seller_name", pattern: "PO-[0-9]{3}", severity: "error" }]);
  });

  it("suggests a neutral numbered name for a blank template, skipping existing names", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => String(input).endsWith("/local-export")
      ? response({ revision: 0, enabled: false, parent_path: null, destination: null })
      : response([invoice, { ...invoice, id: "existing-numbered", name: "发票（2）" }])));
    render(<TemplatesPage />);
    await screen.findByRole("heading", { name: "发票" });
    fireEvent.click(screen.getByRole("button", { name: "新建空白模板" }));
    fireEvent.change(screen.getByLabelText("模板名称"), { target: { value: "发票" } });
    const suggestion = screen.getByRole("button", { name: "使用名称“发票（3）”" });
    fireEvent.click(suggestion);
    expect(screen.getByLabelText("模板名称")).toHaveValue("发票（3）");
    expect(screen.queryByRole("button", { name: /使用名称.*AI 生成/ })).not.toBeInTheDocument();
  });
});
