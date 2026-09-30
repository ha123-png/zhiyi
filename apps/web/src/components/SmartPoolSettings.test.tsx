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

  it("merges out-of-order changes to different templates instead of restoring stale checkboxes", async () => {
    let releaseInvoice!: (response: Response) => void;
    let releaseDelivery!: (response: Response) => void;
    const invoice = template("invoice", "发票");
    const delivery = template("delivery", "送货单");
    const updated: ExtractionTemplate[][] = [];
    vi.stubGlobal("fetch", vi.fn((input: string | URL, init?: RequestInit) => {
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
