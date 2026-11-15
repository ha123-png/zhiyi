import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import type { ExtractionTemplate } from "../types";
import { ExtractConfiguration } from "./ExtractConfiguration";

const template = (id: string, name: string): ExtractionTemplate => ({
  id, name, version: 1, is_system: false, builtin_key: null, source_template_id: null,
  description: "按字段整理文件", extra_instructions: "", fields: [], validation_rules: [], deterministic_rules: [],
  output_mapping: {}, created_at: "2026-10-30T00:00:00Z", updated_at: "2026-10-30T00:00:00Z",
  in_smart_pool: true, is_active: true, engine_kind: "template",
});
const initial = [template("invoice", "发票"), template("delivery", "送货单")];
const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
function Harness() {
  const [templates, setTemplates] = useState(initial);
  const [selected, setSelected] = useState("smart");
  return <ExtractConfiguration templates={templates} automatic={selected === "smart"}
    selectedTemplateId={selected} onSelect={setSelected} onUpdated={setTemplates} />;
}

describe("matching range dialog", () => {
  it("keeps forward and backward keyboard navigation in the dialog", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(initial)));
    render(<Harness />);
    const trigger = screen.getByRole("button", { name: "匹配范围" });
    trigger.focus();
    fireEvent.click(trigger);
    await screen.findByRole("checkbox", { name: /发票/ });
    const close = screen.getByRole("button", { name: "关闭匹配范围" });
    const done = screen.getByRole("button", { name: "完成" });
    close.focus();
    fireEvent.keyDown(close, { key: "Tab", shiftKey: true });
    expect(done).toHaveFocus();
    fireEvent.keyDown(done, { key: "Tab" });
    expect(close).toHaveFocus();
    fireEvent(screen.getByRole("dialog"), new Event("cancel", { cancelable: true }));
    expect(trigger).toHaveFocus();
  });

  it("loads only when opened, remains modal through concurrent saves, and returns focus with the saved summary", async () => {
    const releases = new Map<string, (response: Response) => void>();
    const fetch = vi.fn((input: string | URL) => {
      const url = String(input);
      if (url.includes("smart-pool")) return new Promise<Response>(resolve => releases.set(url.includes("invoice") ? "invoice" : "delivery", resolve));
      return Promise.resolve(json(initial));
    });
    vi.stubGlobal("fetch", fetch);
    render(<Harness />);
    expect(fetch).not.toHaveBeenCalled();
    const trigger = screen.getByRole("button", { name: "匹配范围" });
    trigger.focus();
    fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "匹配范围" });
    fireEvent.click(await screen.findByRole("checkbox", { name: /发票/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /送货单/ }));
    expect(screen.getByRole("button", { name: "完成" })).toBeDisabled();
    fireEvent(dialog, new Event("cancel", { cancelable: true }));
    expect(dialog).toBeInTheDocument();
    await act(async () => { releases.get("delivery")!(json({ ...initial[1], in_smart_pool: false })); });
    expect(screen.getByRole("button", { name: "关闭匹配范围" })).toBeDisabled();
    await act(async () => { releases.get("invoice")!(json({ ...initial[0], in_smart_pool: false })); });
    await waitFor(() => expect(screen.getByRole("button", { name: "完成" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "完成" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByText(/尚未选择普通模板/)).toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("keeps saved selections after a failed write and allows Escape once the request settles", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      if (String(input).includes("smart-pool")) throw new Error("连接中断，请重试");
      return json(initial);
    }));
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "匹配范围" }));
    fireEvent.click(await screen.findByRole("checkbox", { name: /发票/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("连接中断，请重试");
    expect(screen.getByRole("checkbox", { name: /发票/ })).toBeChecked();
    await waitFor(() => expect(screen.getByRole("button", { name: "完成" })).toBeEnabled());
    fireEvent(screen.getByRole("dialog"), new Event("cancel", { cancelable: true }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByText(/2 个模板/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("提取模板"), { target: { value: "invoice" } });
    expect(screen.queryByRole("button", { name: "匹配范围" })).not.toBeInTheDocument();
  });
});
