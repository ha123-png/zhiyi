import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
