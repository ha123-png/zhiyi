import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ExistingLocalModelSettings } from "./ExistingLocalModelSettings";
import { getLocalModelStatus, getLocalModels, loadLocalModel, startLocalModelServer } from "../api";

vi.mock("../api", () => ({ getLocalModelStatus: vi.fn(), getLocalModels: vi.fn(), loadLocalModel: vi.fn(),
  startLocalModelServer: vi.fn(), stopLocalModelServer: vi.fn(), unloadLocalModel: vi.fn() }));

afterEach(() => { localStorage.removeItem("zhiyi.local-model.load-context"); });
it("shows the matching installer and preserves an actionable startup error after refresh", async () => {
  vi.mocked(getLocalModelStatus).mockResolvedValue({ provider: "ollama", base_url: "http://127.0.0.1:11434",
    running: false, installed: false, loaded: [], message: "未找到启动组件", install_url: "https://ollama.com/download/windows" });
  vi.mocked(startLocalModelServer).mockResolvedValue({ ok: false, message: "服务启动失败。", detail: "端口已被占用，请检查端口。" });
  render(<ExistingLocalModelSettings />);
  expect(await screen.findByRole("link", { name: "下载安装" })).toHaveAttribute("href", "https://ollama.com/download/windows");
  fireEvent.click(screen.getByRole("button", { name: "启动服务" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("端口已被占用");
  expect(screen.queryByText("Ollama 服务已启动")).not.toBeInTheDocument();
});

const localReady = {provider: "lm_studio" as const, base_url: "http://127.0.0.1:1234/v1", running: true,
  installed: true, loaded: [], message: "合成本地服务就绪"};

it("keeps the next load length after returning and displays the separate reported capacity", async () => {
  vi.mocked(getLocalModelStatus).mockResolvedValue(localReady);
  vi.mocked(getLocalModels).mockResolvedValue({
    provider: "lm_studio", available: ["synthetic-local"], loaded: ["synthetic-local"],
    loaded_context_lengths: {"synthetic-local": 65536},
  });
  const first = render(<ExistingLocalModelSettings />);
  await screen.findByText("上下文 65,536 Token");
  const input = screen.getByRole("spinbutton", {name: "加载时的上下文长度"});
  expect(input).toHaveValue(65536);
  fireEvent.change(input, {target: {value: "100000"}});
  first.unmount();
  render(<ExistingLocalModelSettings />);
  await screen.findByText("上下文 65,536 Token");
  expect(screen.getByRole("spinbutton", {name: "加载时的上下文长度"})).toHaveValue(100000);
});
it.each(["status", "models"])("audit: keeps a failed initial %s read unknown instead of uninstalled or empty", async failed => {
  vi.mocked(getLocalModelStatus).mockReset().mockResolvedValue(localReady);
  vi.mocked(getLocalModels).mockReset().mockResolvedValue({provider: "lm_studio", available: [], loaded: []});
  if (failed === "status") vi.mocked(getLocalModelStatus).mockRejectedValueOnce(new Error("合成本地读取失败"));
  else vi.mocked(getLocalModels).mockRejectedValueOnce(new Error("合成本地读取失败"));
  render(<ExistingLocalModelSettings />);
  await screen.findByRole("alert");
  expect(screen.queryByText(/服务未运行，无法列出模型/)).not.toBeInTheDocument();
  expect(screen.queryByText(/没有读取到已下载的模型/)).not.toBeInTheDocument();
  expect(screen.queryByText("检查中")).not.toBeInTheDocument();
  expect(screen.getByText(/尚未确认|尚未读取到模型列表/)).toBeInTheDocument();
  if (failed === "status") expect(screen.getByRole("button", {name: "启动服务"})).toBeDisabled();
});

it.each([false, true])("audit: marks a loaded context applied only when no newer draft was entered (%s)", async newerDraft => {
  let loaded = false;
  let finishLoad!: () => void;
  vi.mocked(getLocalModelStatus).mockReset().mockResolvedValue(localReady);
  vi.mocked(getLocalModels).mockReset().mockImplementation(async () => ({provider: "lm_studio", available: ["synthetic-local"], loaded: loaded ? ["synthetic-local"] : []}));
  vi.mocked(loadLocalModel).mockReset().mockImplementation(() => new Promise(resolve => { finishLoad = () => {
    loaded = true;
    resolve({ok: true, message: "合成模型已加载"});
  }; }));
  render(<ExistingLocalModelSettings />);
  const load = await screen.findByRole("button", {name: "加载"});
  await waitFor(() => expect(load).toBeEnabled());
  const context = screen.getByRole("spinbutton", {name: "加载时的上下文长度"});
  fireEvent.change(context, {target: {value: "16384"}});
  fireEvent.click(load);
  await waitFor(() => expect(finishLoad).toBeTypeOf("function"));
  if (newerDraft) fireEvent.change(context, {target: {value: "32768"}});
  await act(async () => { finishLoad(); });
  await screen.findByText("已加载", {selector: ".badge"});
  expect(loadLocalModel).toHaveBeenCalledWith("synthetic-local", 16384);
  expect(context).toHaveValue(newerDraft ? 32768 : 16384);
  const warning = screen.queryByText("上下文长度已修改但尚未生效，请卸载并重新加载模型。");
  if (newerDraft) expect(warning).toBeInTheDocument();
  else expect(warning).not.toBeInTheDocument();
});

it("audit: changes provider after activation without reviving the old provider's late load result", async () => {
  let provider: "lm_studio" | "ollama" = "lm_studio";
  let finishOldLoad!: () => void;
  vi.mocked(getLocalModelStatus).mockReset().mockImplementation(async () => ({...localReady, provider,
    base_url: provider === "ollama" ? "http://127.0.0.1:11434" : localReady.base_url}));
  vi.mocked(getLocalModels).mockReset().mockImplementation(async () => ({provider,
    available: [provider === "ollama" ? "synthetic-ollama" : "synthetic-lm"], loaded: []}));
  vi.mocked(loadLocalModel).mockReset().mockImplementation(() => new Promise(resolve => {
    finishOldLoad = () => resolve({ok: true, message: "旧方案迟到的加载成功"});
  }));
  const page = render(<ExistingLocalModelSettings />);
  await waitFor(() => expect(screen.getByRole("button", {name: "加载"})).toBeEnabled());
  const context = screen.getByRole("spinbutton", {name: "加载时的上下文长度"});
  fireEvent.change(context, {target: {value: "16384"}});
  fireEvent.click(screen.getByRole("button", {name: "加载"}));
  await waitFor(() => expect(finishOldLoad).toBeTypeOf("function"));
  provider = "ollama";
  page.rerender(<ExistingLocalModelSettings refreshSignal={1} />);
  expect(await screen.findByText("synthetic-ollama")).toBeInTheDocument();
  await act(async () => { finishOldLoad(); });
  expect(screen.queryByText("synthetic-lm")).not.toBeInTheDocument();
  expect(screen.queryByText("旧方案迟到的加载成功")).not.toBeInTheDocument();
  expect(context).toHaveValue(16384);
  expect(screen.getByRole("button", {name: "加载"})).toBeEnabled();
});

it.each(["", "512", "262145", "1.5"])("audit-context-input: preserves invalid draft %s and does not send a model load", async draft => {
  vi.mocked(getLocalModelStatus).mockReset().mockResolvedValue(localReady);
  vi.mocked(getLocalModels).mockReset().mockResolvedValue({provider: "lm_studio", available: ["synthetic-local"], loaded: []});
  vi.mocked(loadLocalModel).mockReset().mockResolvedValue({ok: true, message: "unexpected load"});
  render(<ExistingLocalModelSettings />);
  await waitFor(() => expect(screen.getByRole("button", {name: "加载"})).toBeEnabled());
  const context = screen.getByRole("spinbutton", {name: "加载时的上下文长度"});
  fireEvent.change(context, {target: {value: draft}});
  expect(context).toHaveValue(draft === "" ? null : Number(draft));
  fireEvent.click(screen.getByRole("button", {name: "加载"}));
  expect(await screen.findByRole("alert")).toHaveTextContent("请输入 1024 到 262144 之间的整数");
  expect(loadLocalModel).not.toHaveBeenCalled();
  expect(context).toHaveValue(draft === "" ? null : Number(draft));
  expect(context).toHaveFocus();
});
