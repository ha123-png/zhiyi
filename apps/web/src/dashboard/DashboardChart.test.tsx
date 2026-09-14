import { cloneElement, type ReactElement } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { DashboardChart } from "./DashboardChart";

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
