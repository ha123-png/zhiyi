import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Sidebar } from "./Sidebar";

describe("Sidebar", () => {
  it("collapses without hiding the navigation controls", () => {
    render(
      <Sidebar
        active="workspace"
        modelConnected
        modelName="qwen"
        onNavigate={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "收起侧边栏" }));

    expect(screen.getByRole("button", { name: "展开侧边栏" })).toBeInTheDocument();
    expect(screen.getByRole("complementary")).toHaveStyle({ width: "72px" });
    expect(screen.getByRole("button", { name: "状态监控" })).toBeInTheDocument();
  });

  it("supports precise keyboard resizing", () => {
    render(
      <Sidebar
        active="workspace"
        modelConnected
        modelName="qwen"
        onNavigate={vi.fn()}
      />,
    );

    const separator = screen.getByRole("separator", {
      name: "调整侧边栏宽度",
    });
    fireEvent.keyDown(separator, { key: "ArrowRight" });

    expect(separator).toHaveAttribute("aria-valuenow", "248");
    expect(screen.getByRole("complementary")).toHaveStyle({ width: "248px" });

    fireEvent.keyDown(separator, { key: "End" });
    expect(separator).toHaveAttribute("aria-valuenow", "360");
  });
});
