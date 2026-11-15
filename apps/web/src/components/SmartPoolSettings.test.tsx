import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ExtractionTemplate } from "../types";
import { SmartPoolSettings } from "./SmartPoolSettings";

const template = (id: string, name: string): ExtractionTemplate => ({
  id, name, version: 1, is_system: true, builtin_key: id, source_template_id: null,
  description: "", extra_instructions: "", fields: [], validation_rules: [], deterministic_rules: [],
  output_mapping: {}, created_at: "2026-08-01T00:00:00Z", updated_at: "2026-08-01T00:00:00Z",
  in_smart_pool: true, is_active: true, engine_kind: "template",
});
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });

describe("SmartPoolSettings response merging", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows a real empty state instead of loading forever when no active templates exist", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json([{ ...template("disabled", "已停用"), is_active: false }])));
    render(<SmartPoolSettings />);
    expect(await screen.findByText("暂无可用模板，请在模板页新建或启用模板。")).toBeInTheDocument();
    expect(screen.queryByText("正在读取模板…")).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("offers a retry after a failed read and restores the actual selection", async () => {
    const fetch = vi.fn()
      .mockRejectedValueOnce(new Error("网络暂时不可用"))
      .mockResolvedValueOnce(json([{ ...template("invoice", "发票"), in_smart_pool: false }]));
    vi.stubGlobal("fetch", fetch);
    render(<SmartPoolSettings />);
    expect(await screen.findByRole("alert")).toHaveTextContent("网络暂时不可用");
    expect(screen.queryByText("正在读取模板…")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新读取模板" }));
    expect(await screen.findByRole("checkbox", { name: /发票/ })).not.toBeChecked();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("does not update the parent from a save completed after leaving this control", async () => {
    let finish!: (response: Response) => void;
    const updated = vi.fn();
    vi.stubGlobal("fetch", vi.fn((input: string | URL) => String(input).includes("/smart-pool")
      ? new Promise<Response>(resolve => { finish = resolve; })
      : Promise.resolve(json([template("invoice", "发票")]))));
    const view = render(<SmartPoolSettings onUpdated={updated} />);
    fireEvent.click(await screen.findByRole("checkbox", { name: /发票/ }));
    updated.mockClear();
    view.unmount();
    finish(json({ ...template("invoice", "发票"), in_smart_pool: false }));
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(updated).not.toHaveBeenCalled();
  });

  it("keeps keyboard focus during save and prevents concurrent writes to the same template", async () => {
    let finish!: (response: Response) => void;
    const fetchMock = vi.fn((input: string | URL) => String(input).includes("/smart-pool")
      ? new Promise<Response>(resolve => { finish = resolve; })
      : Promise.resolve(json([template("invoice", "发票")])));
    vi.stubGlobal("fetch", fetchMock);
    render(<SmartPoolSettings />);
    const checkbox = await screen.findByRole("checkbox", { name: /发票/ });
    checkbox.focus();
    fireEvent.click(checkbox);
    expect(checkbox).toHaveAttribute("aria-disabled", "true");
    expect(checkbox).not.toBeDisabled();
    expect(checkbox).toHaveFocus();
    fireEvent.click(checkbox);
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes("/smart-pool"))).toHaveLength(1);
    finish(json({ ...template("invoice", "发票"), in_smart_pool: false }));
    await waitFor(() => expect(checkbox).not.toBeChecked());
    expect(checkbox).toHaveAttribute("aria-disabled", "false");
    expect(checkbox).toHaveFocus();
  });

  it("merges out-of-order changes to different templates instead of restoring stale checkboxes", async () => {
    let releaseInvoice!: (response: Response) => void;
    let releaseDelivery!: (response: Response) => void;
    const invoice = template("invoice", "发票");
    const delivery = template("delivery", "送货单");
    const updated: ExtractionTemplate[][] = [];
    vi.stubGlobal("fetch", vi.fn((input: string | URL) => {
      const url = String(input);
      if (url.endsWith("/templates?include_inactive=true")) return Promise.resolve(json([invoice, delivery]));
      if (url.endsWith("/invoice/smart-pool")) return new Promise<Response>(resolve => { releaseInvoice = resolve; });
      if (url.endsWith("/delivery/smart-pool")) return new Promise<Response>(resolve => { releaseDelivery = resolve; });
      return Promise.resolve(json({}));
    }));
    render(<SmartPoolSettings onUpdated={templates => updated.push(templates)} />);
    const invoiceCheckbox = await screen.findByLabelText(/发票/);
    const deliveryCheckbox = screen.getByLabelText(/送货单/);
    fireEvent.click(invoiceCheckbox);
    fireEvent.click(deliveryCheckbox);
    expect(releaseInvoice).toBeDefined();
    expect(releaseDelivery).toBeDefined();

    releaseDelivery(json({ ...delivery, in_smart_pool: false }));
    await waitFor(() => expect(screen.getByLabelText(/送货单/)).not.toBeChecked());
    releaseInvoice(json({ ...invoice, in_smart_pool: false }));

    await waitFor(() => {
      expect(screen.getByLabelText(/发票/)).not.toBeChecked();
      expect(screen.getByLabelText(/送货单/)).not.toBeChecked();
    });
    expect(updated.at(-1)?.filter(item => item.in_smart_pool === false).map(item => item.id).sort()).toEqual(["delivery", "invoice"]);
  });
});
