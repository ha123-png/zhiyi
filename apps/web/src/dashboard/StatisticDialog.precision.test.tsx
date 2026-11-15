import { cloneElement, type ReactElement } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { dashboardApi } from "./api";
import { StatisticDialog } from "./StatisticDialog";

vi.mock("./api", () => ({ dashboardApi: { options: vi.fn(), preview: vi.fn(), save: vi.fn() } }));
// Keep the dialog and actual chart connected; only supply jsdom's missing size.
vi.mock("recharts", async importOriginal => {
  const actual = await importOriginal<typeof import("recharts")>();
  return { ...actual, ResponsiveContainer: ({ children }: { children: ReactElement<{ width: number; height: number }> }) => cloneElement(children, { width: 640, height: 226 }) };
});

it("shows the API total in the statistic preview without recomputing rounded decimal groups", async () => {
  vi.mocked(dashboardApi.options).mockResolvedValue({ tables: [{ id: "synthetic-total", name: "合成金额", columns: [
    { key: "amount", label: "金额", value_type: "number", section: "item" },
    { key: "category", label: "分类", value_type: "text", section: "item" },
  ] }] });
  vi.mocked(dashboardApi.preview).mockResolvedValue({ status: "ready", display: "donut", analysis: {
    analysis_id: "synthetic-total-preview",
    source: { table_id: "synthetic-total", table_name: "合成金额", row_count: 2, document_count: 2,
      grain: "row", generated_at: "2026-11-01", request: {} },
    data: [{ label: "甲", "sum:amount": 0.1 }, { label: "乙", "sum:amount": 0.2 }],
    metric_keys: ["sum:amount"], metric_labels: { "sum:amount": "金额" },
    totals: { "sum:amount": 0.3 }, truncated: false, warnings: [],
  } });
  const { container } = render(<StatisticDialog initial={{ name: "金额观察", table_id: "synthetic-total",
    metric: "sum", metric_field: "amount", group_field: "category", display: "donut" }}
    onClose={vi.fn()} onSaved={vi.fn()} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "保存统计" })).toBeEnabled());
  expect(container.querySelector(".dashboard-donut-center strong")?.textContent).toBe("0.3");
});
