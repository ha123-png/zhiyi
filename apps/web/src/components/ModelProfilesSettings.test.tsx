import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    expect(body.context_length).toBe(8192);
  });
});
