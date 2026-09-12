import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ExistingLocalModelSettings } from "./ExistingLocalModelSettings";
import { getLocalModelStatus, startLocalModelServer } from "../api";

vi.mock("../api", () => ({ getLocalModelStatus: vi.fn(), getLocalModels: vi.fn(), loadLocalModel: vi.fn(),
  startLocalModelServer: vi.fn(), stopLocalModelServer: vi.fn(), unloadLocalModel: vi.fn() }));

it("shows the matching installer and preserves an actionable startup error after refresh", async () => {
  vi.mocked(getLocalModelStatus).mockResolvedValue({ provider: "ollama", base_url: "http://127.0.0.1:11434",
    running: false, installed: false, loaded: [], message: "未找到启动组件", install_url: "https://ollama.com/download/windows" });
  vi.mocked(startLocalModelServer).mockResolvedValue({ ok: false, message: "服务启动失败。", detail: "端口已被占用，请检查端口。" });
  render(<ExistingLocalModelSettings />);
  expect(await screen.findByRole("link", { name: "打开官方下载页" })).toHaveAttribute("href", "https://ollama.com/download/windows");
  fireEvent.click(screen.getByRole("button", { name: "启动服务" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("端口已被占用");
  expect(screen.queryByText("Ollama 服务已启动")).not.toBeInTheDocument();
});
