import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ModelProfilesSettings } from "./ModelProfilesSettings";

describe("ModelProfilesSettings", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("creates a real model profile and clears the key from the form", async () => {
    const requests: RequestInit[] = [];
    vi.stubGlobal("fetch", vi.fn(async (_input: string | URL, init?: RequestInit) => {
      if (!init?.method) {
        return { ok: true, json: async () => [] };
      }
      requests.push(init);
      return {
        ok: true,
        json: async () => ({
          id: "profile-1",
          version: 1,
          current_version: 1,
          name: "本地 AI",
          provider: "lm_studio",
          base_url: "http://127.0.0.1:1234/v1",
          model_name: "qwen3.5-4b",
          reasoning_effort: "none",
          timeout_seconds: 180,
          has_api_key: true,
          is_remote: false,
          remote_data_acknowledged: false,
          is_active: false,
          active_version: null,
          is_archived: false,
          created_at: "2026-08-07T00:00:00Z",
          updated_at: "2026-08-07T00:00:00Z",
        }),
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
