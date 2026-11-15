import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SystemSettings } from "../types";
import { SettingsPage } from "./SettingsPage";

vi.mock("./ExistingLocalModelSettings", () => ({ ExistingLocalModelSettings: () => null }));
vi.mock("./ModelProfilesSettings", () => ({ ModelProfilesSettings: () => null }));
vi.mock("./SmartPoolSettings", () => ({ SmartPoolSettings: () => null }));
vi.mock("./ClearLocalData", () => ({ ClearLocalData: () => null }));

const initial: SystemSettings = {
  image_convert: true, office_convert: true, word_include_images: false, allow_limited_input: true,
  upload_limit_mb: 125, input_text_limit: 20000, input_page_limit: 10, input_row_limit: 500, input_docx_image_limit: 10,
};
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});
function useServer(save: (patch: Partial<SystemSettings>) => Promise<Response> | Response) {
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/system/settings")) return init?.method === "PUT"
      ? save(JSON.parse(String(init.body)) as Partial<SystemSettings>) : response(initial);
    if (path.endsWith("/models/status")) return response({ connected: true, provider: "openai_compatible" });
    return response({ api: { connected: true }, worker: { connected: true }, model: { connected: true } });
  }));
}
beforeEach(() => {
  localStorage.clear(); sessionStorage.clear();
  delete document.documentElement.dataset.zhiyiThemePreference;
});

describe("SettingsPage pending edits", () => {
  it("keeps a newer upload-limit draft and its focus when the earlier save returns", async () => {
    let release!: (value: Response) => void;
    useServer(() => new Promise<Response>(resolve => { release = resolve; }));
    render(<SettingsPage />);
    const input = screen.getByRole("spinbutton", { name: /单文件上传上限/ });
    await waitFor(() => expect(input).toBeEnabled());
    input.focus();
    fireEvent.change(input, { target: { value: "175" } });
    fireEvent.blur(input);
    fireEvent.change(input, { target: { value: "200" } });
    input.focus();
    await waitFor(() => expect(release).toBeTypeOf("function"));
    await act(async () => release(response({ ...initial, upload_limit_mb: 175 })));
    expect(screen.getByRole("spinbutton", { name: /单文件上传上限/ })).toHaveValue(200);
    expect(input).toHaveFocus();
  });

  it("does not replace another limit with a stale full snapshot from a delayed save", async () => {
    const pending: Array<(value: Response) => void> = [];
    useServer(() => new Promise<Response>(resolve => { pending.push(resolve); }));
    render(<SettingsPage />);
    const text = await screen.findByRole("spinbutton", { name: "从开头读取文字" });
    const pages = screen.getByRole("spinbutton", { name: "PDF 页数／多帧图片帧数" });
    fireEvent.change(text, { target: { value: "12345" } }); fireEvent.blur(text);
    fireEvent.change(pages, { target: { value: "7" } }); fireEvent.blur(pages);
    await waitFor(() => expect(pending).toHaveLength(2));
    await act(async () => pending[1](response({ ...initial, input_text_limit: 12345, input_page_limit: 7 })));
    await act(async () => pending[0](response({ ...initial, input_text_limit: 12345, input_page_limit: 10 })));
    expect(screen.getByRole("spinbutton", { name: "从开头读取文字" })).toHaveValue(12345);
    expect(screen.getByRole("spinbutton", { name: "PDF 页数／多帧图片帧数" })).toHaveValue(7);
  });

  it("rolls back only the failed toggle without resending or undoing an independent successful choice", async () => {
    let rejectOffice!: (value: Response) => void;
    let server = { ...initial };
    let calls = 0;
    useServer(patch => {
      calls += 1;
      if (calls === 1) return new Promise<Response>(resolve => { rejectOffice = resolve; });
      server = { ...server, ...patch };
      return response(server);
    });
    render(<SettingsPage />);
    const office = screen.getByRole("button", { name: "Office 文档自动转换" });
    await waitFor(() => expect(office).toBeEnabled());
    fireEvent.click(office);
    fireEvent.click(screen.getByRole("button", { name: "图片自动格式转换" }));
    await screen.findByText(/已关闭图片自动格式转换/);
    await act(async () => rejectOffice(response({ detail: "合成Office保存失败" }, 503)));
    expect(await screen.findByText("合成Office保存失败")).toBeInTheDocument();
    expect(office).toHaveClass("on");
    expect(screen.getByRole("button", { name: "图片自动格式转换" })).toHaveClass("off");
    expect(server.office_convert).toBe(true);
    expect(server.image_convert).toBe(false);
  });

  it("saves rapid changes to the same field in order while keeping the latest draft", async () => {
    const pending: Array<{ value: number | undefined; release: (value: Response) => void }> = [];
    useServer(patch => new Promise<Response>(release => { pending.push({ value: patch.upload_limit_mb, release }); }));
    render(<SettingsPage />);
    const input = screen.getByRole("spinbutton", { name: "单文件上传上限" });
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "175" } }); fireEvent.blur(input);
    fireEvent.change(input, { target: { value: "200" } }); fireEvent.blur(input);
    await waitFor(() => expect(pending).toHaveLength(1));
    expect(pending[0].value).toBe(175);
    await act(async () => pending[0].release(response({ ...initial, upload_limit_mb: 175 })));
    await waitFor(() => expect(pending).toHaveLength(2));
    expect(input).toHaveValue(200);
    expect(pending[1].value).toBe(200);
    await act(async () => pending[1].release(response({ ...initial, upload_limit_mb: 200 })));
    expect(input).toHaveValue(200);
    expect(screen.queryByText("正在保存文件设置…")).not.toBeInTheDocument();
  });

  it("keeps a newer invalid draft and its error when an earlier valid save returns", async () => {
    let release!: (value: Response) => void;
    useServer(() => new Promise<Response>(resolve => { release = resolve; }));
    render(<SettingsPage />);
    const input = screen.getByRole("spinbutton", { name: "单文件上传上限" });
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "175" } }); fireEvent.blur(input);
    await waitFor(() => expect(release).toBeTypeOf("function"));
    fireEvent.change(input, { target: { value: "0" } }); fireEvent.blur(input);
    expect(screen.getByText("请输入大于零的整数。")).toBeInTheDocument();
    await act(async () => release(response({ ...initial, upload_limit_mb: 175 })));
    expect(input).toHaveValue(0);
    expect(screen.getByText("请输入大于零的整数。")).toBeInTheDocument();
  });
});
