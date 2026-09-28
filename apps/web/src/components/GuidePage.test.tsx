import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { GuidePage } from "./GuidePage";
import { getGuideIndex, getGuideTopic } from "../api";

vi.mock("../api", () => ({ getGuideIndex: vi.fn(), getGuideTopic: vi.fn() }));
const topic = { id: "data", title: "数据怎样流动", target: "tables" as const, action: "打开当前数据" };

describe("shared handbook", () => {
  it("reads only a requested topic, keeps its text and supports navigation", async () => {
    vi.mocked(getGuideIndex).mockResolvedValue({ version: "0.5.0", topics: [topic] });
    vi.mocked(getGuideTopic).mockResolvedValue({ ...topic, sections: [{ title: "同一事实", paragraphs: ["文件详情与仓库共用当前数据。"] }] });
    const navigate = vi.fn();
    render(<GuidePage onNavigate={navigate} />);
    const trigger = await screen.findByRole("button", { name: "数据怎样流动" });
    expect(getGuideTopic).not.toHaveBeenCalled();
    fireEvent.click(trigger);
    expect(await screen.findByText("文件详情与仓库共用当前数据。")).toBeVisible();
    expect(getGuideTopic).toHaveBeenCalledWith("data");
    fireEvent.click(trigger); fireEvent.click(trigger);
    expect(getGuideTopic).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "打开当前数据" }));
    expect(navigate).toHaveBeenCalledWith("tables");
  });

  it("lets a failed topic load retry without showing stale claims", async () => {
    vi.mocked(getGuideIndex).mockResolvedValue({ version: "0.5.0", topics: [topic] });
    vi.mocked(getGuideTopic).mockRejectedValueOnce(new Error("服务暂时不可用"))
      .mockResolvedValueOnce({ ...topic, sections: [{ title: "恢复", paragraphs: ["最新使用说明"] }] });
    render(<GuidePage onNavigate={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "数据怎样流动" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("服务暂时不可用");
    fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
    await waitFor(() => expect(screen.getByText("最新使用说明")).toBeVisible());
  });
});
