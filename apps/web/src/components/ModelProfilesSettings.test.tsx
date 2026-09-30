import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ModelProfilesSettings } from "./ModelProfilesSettings";

describe("ModelProfilesSettings", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("does not let a late initial list response replace a newly started draft", async () => {
    let releaseList!: (response: Response) => void;
    const profile = modelProfile("profile-a", "旧方案");
    vi.stubGlobal("fetch", vi.fn((input: string | URL) => {
      if (String(input).endsWith("/models/profiles")) return new Promise<Response>(resolve => { releaseList = resolve; });
      return Promise.resolve(jsonResponse([]));
    }));

    render(<ModelProfilesSettings />);
    fireEvent.click(screen.getByRole("button", { name: /新建空白方案/ }));
    fireEvent.change(screen.getByLabelText("方案名称"), { target: { value: "我正在写的新草稿" } });
    releaseList(jsonResponse([profile]));

    expect(await screen.findByRole("button", { name: /旧方案/ })).toBeInTheDocument();
    expect(screen.getByLabelText("方案名称")).toHaveValue("我正在写的新草稿");
  });

  it("keeps profile B clean and suppresses profile A's success message after its late save", async () => {
    let releaseSave!: (response: Response) => void;
    let profileReads = 0;
    const profile = modelProfile("profile-a", "原方案");
    const profileB = { ...modelProfile("profile-b", "B 方案"), model_name: "model-b" };
    vi.stubGlobal("fetch", vi.fn((input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/models/profiles") && !init?.method) {
        profileReads += 1;
        return Promise.resolve(jsonResponse([profileReads === 1 ? profile : { ...profile, name: "已保存方案", version: 2 }, profileB]));
      }
      if (url.endsWith("/models/profiles/profile-a") && init?.method === "PUT") {
        return new Promise<Response>(resolve => { releaseSave = resolve; });
      }
      return Promise.resolve(jsonResponse([]));
    }));

    render(<ModelProfilesSettings />);
    fireEvent.change(await screen.findByLabelText("方案名称"), { target: { value: "待保存的修改" } });
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
    await waitFor(() => expect(releaseSave).toBeDefined());

    fireEvent.click(screen.getByRole("button", { name: /B 方案/ }));
    const switchDialog = screen.getByRole("dialog", { name: "修改尚未保存" });
    fireEvent.click(within(switchDialog).getByRole("button", { name: "放弃修改并继续" }));
    expect(screen.getByLabelText("模型名称")).toHaveValue("model-b");
    releaseSave(jsonResponse({ ...profile, name: "已保存方案", version: 2 }));

    await waitFor(() => expect(profileReads).toBe(2));
    expect(screen.queryByText("已保存为新版本；排队中的旧任务不会改变。")).not.toBeInTheDocument();
    expect(screen.getByLabelText("模型名称")).toHaveValue("model-b");
    let navigationAllowed = false;
    act(() => {
      navigationAllowed = window.dispatchEvent(new CustomEvent("zhiyi:before-navigate", {
        cancelable: true,
        detail: { proceed: vi.fn() },
      }));
    });
    expect(navigationAllowed).toBe(true);
  });

  it("keeps a probe result attached to the profile draft it tested", async () => {
    let releaseProbe!: (response: Response) => void;
    let savedBody: Record<string, unknown> | undefined;
    const textOnly = modelProfile("profile-b", "B 文本方案", false);
    const imageProfile = modelProfile("profile-a", "A 图片方案", false);
    vi.stubGlobal("fetch", vi.fn((input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/models/profiles") && !init?.method) return Promise.resolve(jsonResponse([imageProfile, textOnly]));
      if (url.includes("/models/profiles/probe?profile_id=profile-a")) return new Promise<Response>(resolve => { releaseProbe = resolve; });
      if (url.endsWith("/models/profiles/profile-b") && init?.method === "PUT") {
        savedBody = JSON.parse(String(init.body));
        return Promise.resolve(jsonResponse({ ...textOnly, version: 2 }));
      }
      if (url.endsWith("/models/profiles/profile-a") && init?.method === "PUT") return Promise.resolve(jsonResponse(imageProfile));
      return Promise.resolve(jsonResponse([imageProfile, textOnly]));
    }));

    render(<ModelProfilesSettings />);
    fireEvent.click(await screen.findByRole("button", { name: /测试连接/ }));
    await waitFor(() => expect(releaseProbe).toBeDefined());
    fireEvent.click(screen.getByRole("button", { name: /B 文本方案/ }));
    releaseProbe(jsonResponse({ connected: true, model_listed: true, multimodal: true, message: "连接成功", available_models: ["A"] }));

    expect(await screen.findByText("仅文本", { selector: ".badge" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
    await waitFor(() => expect(savedBody).toBeDefined());
    expect(savedBody?.multimodal).toBe(false);
  });

  it("clears the model-bound capability and ignores a late probe after the model name changes", async () => {
    let releaseProbe!: (response: Response) => void;
    let savedBody: Record<string, unknown> | undefined;
    const profile = modelProfile("profile-a", "A 本地方案", false);
    vi.stubGlobal("fetch", vi.fn((input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/models/profiles") && !init?.method) return Promise.resolve(jsonResponse([profile]));
      if (url.includes("/models/profiles/probe?profile_id=profile-a")) return new Promise<Response>(resolve => { releaseProbe = resolve; });
      if (url.endsWith("/models/profiles/profile-a") && init?.method === "PUT") {
        savedBody = JSON.parse(String(init.body));
        return Promise.resolve(jsonResponse({ ...profile, version: 2 }));
      }
      return Promise.resolve(jsonResponse([profile]));
    }));

    render(<ModelProfilesSettings />);
    fireEvent.click(await screen.findByRole("button", { name: /测试连接/ }));
    await waitFor(() => expect(releaseProbe).toBeDefined());
    fireEvent.change(screen.getByLabelText("模型名称"), { target: { value: "user-edited-model" } });
    releaseProbe(jsonResponse({ connected: true, model_listed: true, multimodal: true, message: "连接成功", available_models: ["A"] }));

    expect(screen.getByText("未验证", { selector: ".badge" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
    await waitFor(() => expect(savedBody).toBeDefined());
    expect(savedBody?.model_name).toBe("user-edited-model");
    expect(savedBody?.multimodal).toBeNull();
  });

  it("clears a settled probe conclusion when the tested model name changes", async () => {
    const profile = modelProfile("profile-a", "A 本地方案");
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      const url = String(input);
      if (url.endsWith("/models/profiles/probe?profile_id=profile-a")) {
        return jsonResponse({ connected: true, model_listed: true, multimodal: true, message: "合成探测通过", available_models: ["synthetic-model"] });
      }
      return jsonResponse([profile]);
    }));

    render(<ModelProfilesSettings />);
    await screen.findByRole("button", { name: /测试连接/ });
    fireEvent.click(screen.getByRole("button", { name: "测试连接" }));
    expect(await screen.findByText("合成探测通过")).toBeInTheDocument();
    expect(screen.getByText("支持图片", { selector: ".badge" })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("模型名称"), { target: { value: "other-model" } });

    expect(screen.queryByText("合成探测通过")).not.toBeInTheDocument();
    expect(screen.getByText("未验证", { selector: ".badge" })).toBeInTheDocument();
  });

  it("protects a dirty profile draft from app navigation and profile switching", async () => {
    const profileA = modelProfile("profile-a", "A 本地方案");
    const profileB = { ...modelProfile("profile-b", "B 本地方案"), model_name: "model-b" };
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse([profileA, profileB])));

    render(<ModelProfilesSettings />);
    expect(await screen.findByRole("button", { name: /A 本地方案/ })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("模型名称"), { target: { value: "unsaved-model" } });

    const proceed = vi.fn();
    let navigationAllowed = true;
    act(() => {
      navigationAllowed = window.dispatchEvent(new CustomEvent("zhiyi:before-navigate", {
        cancelable: true,
        detail: { proceed },
      }));
    });
    const navigationDialog = screen.getByRole("dialog", { name: "修改尚未保存" });
    expect(navigationAllowed).toBe(false);
    fireEvent.click(within(navigationDialog).getByRole("button", { name: "取消" }));
    expect(proceed).not.toHaveBeenCalled();
    expect(screen.getByLabelText("模型名称")).toHaveValue("unsaved-model");

    fireEvent.click(screen.getByRole("button", { name: /B 本地方案/ }));
    const switchDialog = screen.getByRole("dialog", { name: "修改尚未保存" });
    fireEvent.click(within(switchDialog).getByRole("button", { name: "放弃修改并继续" }));
    expect(screen.getByLabelText("模型名称")).toHaveValue("model-b");
  });

  it("creates a real model profile and clears the key from the form", async () => {
    const requests: RequestInit[] = [];
    let createdProfile: Record<string, unknown> | null = null;
    vi.stubGlobal("fetch", vi.fn(async (_input: string | URL, init?: RequestInit) => {
      if (!init?.method) {
        return { ok: true, json: async () => createdProfile ? [createdProfile] : [] };
      }
      requests.push(init);
      createdProfile = {
        id: "profile-1",
        version: 1,
        current_version: 1,
        name: "本地 AI",
        provider: "lm_studio",
        base_url: "http://127.0.0.1:1234/v1",
        model_name: "qwen3.5-4b",
        reasoning_effort: "none",
        timeout_seconds: 180,
        context_length: 8192,
        context_policy: "auto",
        temperature: null,
        multimodal: null,
        has_api_key: true,
        is_remote: false,
        remote_data_acknowledged: false,
        is_active: false,
        active_version: null,
        is_archived: false,
      };
      return {
        ok: true,
        json: async () => createdProfile,
      };
    }));

    render(<ModelProfilesSettings />);
    expect(await screen.findByText(/还没有保存的方案/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("API Key（可选）"), {
      target: { value: "secret-value" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建方案" }));

    expect(await screen.findByText("方案已创建，请确认后激活。")).toBeInTheDocument();
    expect(screen.getByLabelText("API Key（可选）")).toHaveValue("");
    await waitFor(() => expect(requests).toHaveLength(1));
    const body = JSON.parse(String(requests[0].body));
    expect(body).toMatchObject({
      provider: "lm_studio",
      model_name: "qwen3.5-4b",
      api_key: "secret-value",
      timeout_seconds: 180,
    });
    expect(body).not.toHaveProperty("max_tokens");
    expect(body.temperature).toBeNull();
    expect(body.context_length).toBeNull();
  });

  it("asks before archiving a profile and keeps failures visible for retry", async () => {
    let archiveCalls = 0;
    const profile = {id: "local", version: 1, name: "本地备用方案", provider: "lm_studio", base_url: "http://127.0.0.1:1234/v1",
      model_name: "local-model", timeout_seconds: 180, context_length: 8192, temperature: null, reasoning_effort: "none",
      is_active: false, is_archived: false, active_version: null, multimodal: true};
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      if (String(input).endsWith("/archive")) {
        archiveCalls += 1;
        return new Response(JSON.stringify({detail: "方案正在使用，暂不能停用。"}), {status: 409});
      }
      return new Response(JSON.stringify([profile]));
    }));
    render(<ModelProfilesSettings />);
    fireEvent.click(await screen.findByRole("button", {name: "停用方案"}));
    const dialog = screen.getByRole("dialog", {name: "停用“本地备用方案”？"});
    expect(dialog).toHaveTextContent("不能再次激活");
    expect(archiveCalls).toBe(0);
    fireEvent.click(within(dialog).getByRole("button", {name: "取消"}));
    expect(archiveCalls).toBe(0);
    fireEvent.click(screen.getByRole("button", {name: "停用方案"}));
    fireEvent.click(within(screen.getByRole("dialog", {name: "停用“本地备用方案”？"})).getByRole("button", {name: "确认停用"}));
    expect(await screen.findByRole("alert")).toHaveTextContent("方案正在使用，暂不能停用");
    expect(archiveCalls).toBe(1);
    expect(screen.getByRole("button", {name: "确认停用"})).toBeEnabled();
  });
});

function modelProfile(id: string, name: string, multimodal: boolean | null = null) {
  return {
    id, version: 1, current_version: 1, name, provider: "lm_studio", base_url: "http://127.0.0.1:1234/v1",
    model_name: `${id}-model`, reasoning_effort: "none", timeout_seconds: 180, context_length: 8192,
    context_policy: "manual", temperature: null, multimodal, has_api_key: false, is_remote: false,
    remote_data_acknowledged: false, is_active: false, active_version: null, is_archived: false,
    created_at: "2026-08-07T00:00:00Z", updated_at: "2026-08-07T00:00:00Z",
  };
}

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}
