import { cloneElement, type ReactElement } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { AnimatedNumber, DashboardChart } from "./DashboardChart";

// Only replace jsdom's absent layout measurement. Charts, keyboard focus and
// the shared animated Fold remain real components.
vi.mock("recharts", async importOriginal => {
  const actual = await importOriginal<typeof import("recharts")>();
  return { ...actual, ResponsiveContainer: ({children}: {children: ReactElement<{width: number; height: number}>}) => cloneElement(children, {width: 640, height: 226}) };
});

it("keeps a named keyboard chart and an operable animated data disclosure", async () => {
  render(<DashboardChart data={[{label:"甲供应方", value:120}, {label:"乙供应方", value:80}]} type="bar" label="采购金额" />);
  const chart = await screen.findByRole("application", {name:"采购金额条形图"});
  expect(chart).toHaveAttribute("tabindex", "0");
  chart.focus();
  expect(chart).toHaveFocus();
  const toggle = screen.getByRole("button", {name:"查看数据"});
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
  fireEvent.click(toggle);
  const table = await screen.findByRole("table", {name:"采购金额"});
  expect(table).toHaveTextContent("甲供应方120");
  expect(table).toHaveTextContent("乙供应方80");
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  await waitFor(() => expect(screen.queryByRole("table")).not.toBeInTheDocument());
});

it("keeps a single category identifiable when it becomes a numeric chart", () => {
  render(<DashboardChart data={[{label:"唯一供应方", value:120}]} type="number" label="金额" />);
  expect(screen.getByText("唯一供应方")).toBeInTheDocument();
  expect(screen.getAllByText("120")).toHaveLength(2); // visual count and stable accessible value
});

it("keeps a small nonzero value readable in both the visual and accessible metric", () => {
  render(<DashboardChart data={[{ label: "微量", value: 0.00000001 }]} type="number" label="剂量" />);
  expect(screen.getAllByText("1e-8")).toHaveLength(2);
});

it("preserves full fractional values in the chart's data table", async () => {
  render(<DashboardChart data={[{ label: "甲", value: 0.12345678912345678 }, { label: "乙", value: 0.2 }]} type="bar" label="精度检查" />);
  fireEvent.click(screen.getByRole("button", { name: "查看数据" }));
  expect(await screen.findByRole("cell", { name: "0.12345678912345678" })).toBeInTheDocument();
});

it("uses readable scientific ticks instead of labelling an entire small-value axis zero", async () => {
  render(<DashboardChart data={[{ label: "甲", value: 0.00000001 }, { label: "乙", value: 0.00000002 }]} type="bar" label="微量" />);
  const chart = await screen.findByRole("application", { name: "微量条形图" });
  const labels = [...chart.querySelectorAll(".recharts-xAxis-tick-labels .recharts-cartesian-axis-tick-value")].map(tick => tick.textContent);
  expect(labels).not.toHaveLength(0);
  expect(labels.some(label => /e-\d+/.test(label ?? ""))).toBe(true);
});

it("uses the server total rather than recomputing a decimal donut total", () => {
  const { container } = render(<DashboardChart data={[{ label: "甲", value: 0.1 }, { label: "乙", value: 0.2 }]}
    total={0.3} type="donut" label="金额" />);
  expect(container.querySelector(".dashboard-donut-center strong")?.textContent).toBe("0.3");
});

it("lands on the exact target when integer interpolation loses a unit through cancellation", () => {
  let tick!: FrameRequestCallback;
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { tick = callback; return 1; });
  vi.stubGlobal("cancelAnimationFrame", () => {});
  const now = vi.spyOn(performance, "now").mockReturnValue(0);
  const view = render(<AnimatedNumber value={9007199254740991} />);
  view.rerender(<AnimatedNumber value={-9007199254740990} />);
  act(() => tick(420));
  expect(screen.getAllByText("-9,007,199,254,740,990")).toHaveLength(2);
  now.mockRestore();
});
