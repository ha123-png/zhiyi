import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SettingsPage } from "./SettingsPage";

vi.mock("./ExistingLocalModelSettings", () => ({ ExistingLocalModelSettings: () => null }));
vi.mock("./ModelProfilesSettings", () => ({ ModelProfilesSettings: () => null }));
vi.mock("./SmartPoolSettings", () => ({ SmartPoolSettings: () => null }));
vi.mock("./ClearLocalData", () => ({ ClearLocalData: () => null }));

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("SettingsPage audit regression", () => {
  it("clears a prior setting-save error after a successful retry", async () => {
    let serverSettings = {
      image_convert: true,
      office_convert: true,
      word_include_images: true,
      allow_limited_input: false,
      upload_limit_mb: 50,
      input_text_limit: 20000,
      input_page_limit: 10,
      input_row_limit: 500,
      input_docx_image_limit: 10,
    };
    let saveCount = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith("/system/settings") && init?.method === "PUT") {
        saveCount += 1;
        if (saveCount === 1) return jsonResponse({ detail: "合成 503 保存失败" }, 503);
        serverSettings = { ...serverSettings, ...JSON.parse(String(init.body)) };
        return jsonResponse(serverSettings);
      }
      if (path.endsWith("/system/settings")) return jsonResponse(serverSettings);
      if (path.endsWith("/models/status")) return jsonResponse({
        connected: true, provider: "openai_compatible", configured_model: "test-model", available_models: [],
      });
      if (path.endsWith("/system/status")) return jsonResponse({
        api: { connected: true, message: "就绪" },
        worker: { connected: true, message: "就绪" },
        model: { connected: true, message: "就绪" },
        model_provider: "openai_compatible", configured_model: "test-model",
      });
      return jsonResponse({});
    }));

    const firstRender = render(<SettingsPage />);
    const officeToggle = await screen.findByRole("button", { name: "Office 文档自动转换" });
    expect(officeToggle).toHaveClass("toggle", "on");

    fireEvent.click(officeToggle);
    expect(await screen.findByText("合成 503 保存失败")).toBeInTheDocument();
    await waitFor(() => expect(officeToggle).toHaveClass("toggle", "on"));

    fireEvent.click(officeToggle);
    await waitFor(() => expect(serverSettings.office_convert).toBe(false));
    expect(await screen.findByText(/已关闭Office 文档自动转换/)).toBeInTheDocument();
    expect(screen.queryByText("合成 503 保存失败")).not.toBeInTheDocument();
    expect(officeToggle).toHaveClass("toggle", "off");

    firstRender.unmount();
    render(<SettingsPage />);
    expect(await screen.findByRole("button", { name: "Office 文档自动转换" }))
      .toHaveClass("toggle", "off");
    expect(screen.queryByText("合成 503 保存失败")).not.toBeInTheDocument();
  });
});
