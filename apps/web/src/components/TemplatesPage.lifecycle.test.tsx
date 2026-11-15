import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { TemplatesPage } from "./TemplatesPage";
import type { ExtractionTemplate } from "../types";

it.each(["saving", "inactive"])("locks field structure controls while the template is %s", async mode => {
  let template: ExtractionTemplate = {
    id: "synthetic-template", version: 1, is_system: false, is_active: mode !== "inactive",
    builtin_key: null, source_template_id: null, name: "字段结构验收", description: "原说明",
    extra_instructions: "", validation_rules: [], deterministic_rules: [], output_mapping: {},
    created_at: "2026-11-01T00:00:00Z", updated_at: "2026-11-01T00:00:00Z",
    fields: ["字段甲", "字段乙"].map((label, index) => ({
      key: `field_${index}`, label, section: "header", value_type: "text", instructions: "", example: "",
    })),
  };
  let finishSave!: (response: Response) => void;
  const pendingSave = new Promise<Response>(resolve => { finishSave = resolve; });
  let writes = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/templates?include_inactive=true")) return Response.json([template]);
    if (url.endsWith("/local-export")) return Response.json({ revision: 0, enabled: false, parent_path: null, destination: null });
    if (url.endsWith("/templates/synthetic-template") && init?.method === "PUT") {
      writes += 1;
      template = { ...template, ...JSON.parse(init.body as string), version: 2 };
      return pendingSave;
    }
    throw new Error(`Unexpected synthetic request: ${url}`);
  }));
  render(<TemplatesPage />);
  await screen.findByRole("heading", { name: "字段结构验收" });
  if (mode === "saving") {
    fireEvent.change(screen.getByLabelText("用途说明"), { target: { value: "已提交说明" } });
    fireEvent.click(screen.getByRole("button", { name: /^保存$/ }));
    await waitFor(() => expect(writes).toBe(1));
  }
  const add = screen.getByRole("button", { name: "添加字段" });
  const remove = screen.getByRole("button", { name: "删除字段 字段甲" });
  fireEvent.click(add);
  fireEvent.click(remove);
  expect(screen.getAllByRole("textbox", { name: /^字段名$/ }).map(input => (input as HTMLInputElement).value))
    .toEqual(["字段甲", "字段乙"]);
  expect(add).toBeDisabled();
  expect(remove).toBeDisabled();
  if (mode === "saving") await act(async () => { finishSave(Response.json(template)); });
});
