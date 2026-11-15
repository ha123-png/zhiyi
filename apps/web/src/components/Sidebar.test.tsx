import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Sidebar } from "./Sidebar";

describe("Sidebar", () => {
  it("prepares a page on pointer or keyboard intent without changing the current page", () => {
    const navigate = vi.fn();
    const prepare = vi.fn();
    render(<Sidebar active="workspace" modelConnected={null} modelName={null} onNavigate={navigate} onNavigateIntent={prepare} />);
    const settings = screen.getByRole("button", { name: "设置" });
    fireEvent.pointerEnter(settings);
    fireEvent.focus(settings);
    expect(prepare.mock.calls).toEqual([["settings"], ["settings"]]);
    expect(navigate).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "状态监控" })).toHaveAttribute("aria-current", "page");
    fireEvent.click(settings);
    expect(navigate).toHaveBeenCalledExactlyOnceWith("settings");
  });
  it.each(["pointercancel", "blur"])("stops resizing when the pointer session ends with %s", endEvent => {
    render(<Sidebar active="workspace" modelConnected={null} modelName={null} onNavigate={vi.fn()} />);
    const separator = screen.getByRole("separator", { name: "调整侧边栏宽度" });
    fireEvent(separator, new MouseEvent("pointerdown", { bubbles: true, clientX: 100, button: 0 }));
    fireEvent(document, new MouseEvent("pointermove", { bubbles: true, clientX: 120 }));
    expect(separator).toHaveAttribute("aria-valuenow", "252");
    fireEvent(endEvent === "blur" ? window : document, new Event(endEvent, { bubbles: true }));
    fireEvent(document, new MouseEvent("pointermove", { bubbles: true, clientX: 170 }));
    expect(separator).toHaveAttribute("aria-valuenow", "252");
    fireEvent(document, new Event("pointerup", { bubbles: true }));
  });

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
    expect(screen.getByRole("button", { name: "状态监控" })).toHaveAttribute("aria-current", "page");
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

  it("opens a labeled mobile page menu and returns focus after Escape", async () => {
    render(
      <Sidebar
        active="workspace"
        modelConnected
        modelName="qwen"
        onNavigate={vi.fn()}
      />,
    );

    const trigger = screen.getByRole("button", { name: "展开页面导航" });
    fireEvent.click(trigger);

    const dialog = screen.getByRole("dialog", { name: "工作区页面" });
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(trigger).toHaveAttribute("aria-controls", dialog.id);
    expect(within(dialog).getByRole("button", { name: "数据仓库" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "设置" })).toBeInTheDocument();

    fireEvent.keyDown(dialog, { key: "Escape" });

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "工作区页面" })).not.toBeInTheDocument();
      expect(trigger).toHaveAttribute("aria-expanded", "false");
      expect(trigger).toHaveFocus();
    });
  });

  it("closes the mobile page menu after navigating", async () => {
    const onNavigate = vi.fn();
    render(
      <Sidebar
        active="workspace"
        modelConnected
        modelName="qwen"
        onNavigate={onNavigate}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "展开页面导航" }));
    const dialog = screen.getByRole("dialog", { name: "工作区页面" });
    fireEvent.click(within(dialog).getByRole("button", { name: "模板" }));

    expect(onNavigate).toHaveBeenCalledWith("templates");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "工作区页面" })).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "展开页面导航" })).toHaveFocus();
  });
});
