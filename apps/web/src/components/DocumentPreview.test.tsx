import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DocumentPreview } from "./DocumentPreview";

describe("DocumentPreview", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("renders the real image and a normalized evidence region", () => {
    render(
      <DocumentPreview
        contentType="image/png"
        filename="invoice.png"
        pageNumber={1}
        region={{ x: 0.1, y: 0.2, width: 0.3, height: 0.4 }}
        scale={1}
        url="/api/v1/tasks/task-1/original"
      />,
    );

    expect(screen.getByRole("img", { name: "原文件：invoice.png" })).toHaveAttribute(
      "src",
      "/api/v1/tasks/task-1/original",
    );
    expect(screen.getByRole("mark", { name: "字段在原文件中的位置" })).toHaveStyle({
      left: "10%",
      top: "20%",
      width: "30%",
      height: "40%",
    });
  });

  it("does not imply a location when no reliable region exists", () => {
    render(
      <DocumentPreview
        contentType="image/jpeg"
        filename="delivery.jpg"
        pageNumber={1}
        scale={1}
        url="/api/v1/tasks/task-2/original"
      />,
    );

    expect(screen.queryByRole("mark")).not.toBeInTheDocument();
  });

  it("renders markdown text instead of treating it as an image", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ kind: "markdown", text: "# 标题\n正文", truncated: false, image_count: 0 }),
    }));

    render(
      <DocumentPreview
        contentType="text/markdown"
        filename="说明.md"
        pageNumber={1}
        scale={1}
        url="/api/v1/tasks/task-md/file"
      />,
    );

    expect(await screen.findByRole("heading", { name: "标题" })).toBeInTheDocument();
    expect(screen.getByText("正文")).toBeInTheDocument();
  });

  it("renders Word document as structured blocks with in-place images", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        blocks: [
          { type: "heading", level: 1, text: "合同标题" },
          { type: "paragraph", text: "合同正文" },
          { type: "image", image_index: 1, caption: "图片 1" },
        ],
      }),
    }));

    render(
      <DocumentPreview
        contentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        filename="合同.docx"
        pageNumber={1}
        scale={1}
        url="/api/v1/tasks/task-docx/file"
      />,
    );

    expect(await screen.findByRole("heading", { name: "合同标题" })).toBeInTheDocument();
    expect(screen.getByText("合同正文")).toBeInTheDocument();
    expect(screen.getByText("图片 1")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "图片 1" })).toHaveAttribute(
      "src",
      "/api/v1/tasks/task-docx/preview/images/1",
    );
  });
});
