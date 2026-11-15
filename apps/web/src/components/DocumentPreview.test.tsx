import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
      "/api/v1/tasks/task-1/preview/image",
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

  it("keeps markdown external images inert and opens explicit web links without document referrers", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ kind: "markdown",
      text: "![远程示意](https://example.test/pixel?document=private)\n[参考网站](https://example.test/reference)\n[相对原件](../private.md)", truncated: false }) }));
    render(<DocumentPreview contentType="text/markdown" filename="来源说明.md" pageNumber={1} scale={1}
      url="/api/v1/tasks/markdown-external/file" />);
    const external = await screen.findByRole("link", { name: "参考网站" });
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(external).toHaveAttribute("href", "https://example.test/reference");
    expect(external).toHaveAttribute("target", "_blank");
    expect(external).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.queryByRole("link", { name: "相对原件" })).not.toBeInTheDocument();
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

  it("distinguishes a truncated preview from model input and retains the original link", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ kind: "text", text: "只显示开头", truncated: true, image_count: 0 }),
    }));
    render(<DocumentPreview contentType="text/plain" filename="完整.txt" pageNumber={1}
      scale={1} url="/api/v1/tasks/long/file" />);
    expect(await screen.findByText(/预览仅显示开头 5 个字符/)).toBeInTheDocument();
    expect(screen.getByText(/这不是模型的读取范围/)).toBeInTheDocument();
  });

  it("keeps access to the original when Office preview fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("预览资源超限")));
    render(<DocumentPreview contentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
      filename="大表.xlsx" pageNumber={1} scale={1} url="/api/v1/tasks/large/file" />);
    expect(await screen.findByText("预览资源超限")).toBeInTheDocument();
    expect(screen.getByText(/预览失败不影响已保存的原件/)).toBeInTheDocument();
  });

  it("fetches the next bounded text window and restores its bookmark when reopened", async () => {
    const fetcher = vi.fn(async (url: string) => ({ ok: true, json: async () => url.includes("offset=32766")
      ? { kind: "text", text: "后半段核对内容", next_offset: null }
      : { kind: "text", text: "首段核对内容", next_offset: 32766 } }));
    vi.stubGlobal("fetch", fetcher);
    const props = { contentType: "text/plain", filename: "分页.txt", pageNumber: 1, scale: 1, url: "/api/v1/tasks/offset-test/file" };
    const first = render(<DocumentPreview {...props} />);
    await screen.findByText("首段核对内容");
    fireEvent.click(screen.getByRole("button", { name: "下一段" }));
    await screen.findByText("后半段核对内容");
    expect(screen.queryByText("首段核对内容")).not.toBeInTheDocument();
    first.unmount();
    render(<DocumentPreview {...props} />);
    await screen.findByText("后半段核对内容");
    expect(fetcher.mock.calls.at(-1)?.[0]).toContain("offset=32766");
    fireEvent.click(screen.getByRole("button", { name: "上一段" }));
    await screen.findByText("首段核对内容");
  });

  it("returns correctly after more than 64 text windows while keeping bookmark history bounded", async () => {
    const totalBytes = 2_326_528;
    const offsets: number[] = [];
    const bookmarkSizes: number[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const offset = Number(new URL(url, "http://localhost").searchParams.get("offset") ?? 0);
      offsets.push(offset);
      const next = offset + 32_768 < totalBytes ? offset + 32_768 : null;
      return { ok: true, json: async () => ({ kind: "text", text: `byte offset ${offset}`, offset,
        next_offset: next, truncated: next != null, total_bytes: totalBytes }) };
    }));
    const props = { contentType: "text/plain", filename: "长文.txt", pageNumber: 1, scale: 1,
      url: "/api/v1/tasks/long-window-history/file",
      onBookmarkChange: (bookmark: { previousOffsets?: number[] }) => bookmarkSizes.push(bookmark.previousOffsets?.length ?? 0) };
    render(<DocumentPreview {...props} />);
    await screen.findByText("byte offset 0");

    for (let page = 1; page <= 66; page++) {
      fireEvent.click(screen.getByRole("button", { name: "下一段" }));
      await screen.findByText(`byte offset ${page * 32_768}`);
    }
    for (let page = 65; page >= 1; page--) {
      fireEvent.click(screen.getByRole("button", { name: "上一段" }));
      await screen.findByText(`byte offset ${page * 32_768}`);
    }

    expect(screen.getByText("byte offset 32768")).toBeInTheDocument();
    expect(Math.max(...bookmarkSizes)).toBeLessThanOrEqual(64);
    expect(offsets.length).toBeGreaterThan(132);
  }, 15_000);

  it("keeps the last scroll position when its preview pane is removed immediately", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ kind: "text", text: "可核对的原件内容", truncated: false }),
    }));
    const props = { contentType: "text/plain", filename: "位置.txt", pageNumber: 1, scale: 1,
      url: "/api/v1/tasks/scroll-bookmark-test/file" };
    const preview = (open: boolean) => <div>{open && <div data-document-scroll style={{overflowY: "auto"}}>
      <DocumentPreview {...props} />
    </div>}</div>;
    const view = render(preview(true));
    await screen.findByText("可核对的原件内容");
    const first = document.querySelector<HTMLElement>("[data-document-scroll]")!;
    let liveScrollTop = 0;
    Object.defineProperty(first, "scrollTop", { configurable: true,
      get() { return this.isConnected ? liveScrollTop : 0; },
      set(value: number) { liveScrollTop = value; },
    });
    act(() => { first.scrollTop = 220; fireEvent.scroll(first); });
    view.rerender(preview(false));
    view.rerender(preview(true));
    const reopened = document.querySelector<HTMLElement>("[data-document-scroll]")!;
    await screen.findByText("可核对的原件内容");
    expect(reopened.scrollTop).toBe(220);
  });

  it("cancels stale requests and cannot display A's delayed response inside B", async () => {
    let resolveA!: (value: unknown) => void;
    let signalA!: AbortSignal;
    vi.stubGlobal("fetch", vi.fn((url: string, init: RequestInit) => {
      if (url.includes("cancel-a")) {
        signalA = init.signal!;
        return new Promise(resolve => { resolveA = resolve; });
      }
      return Promise.resolve({ ok: true, json: async () => ({ text: "文件 B", kind: "text" }) });
    }));
    const props = { contentType: "text/plain", filename: "cancel.txt", pageNumber: 1, scale: 1 };
    const view = render(<DocumentPreview {...props} url="/api/v1/tasks/cancel-a/file" />);
    view.rerender(<DocumentPreview {...props} url="/api/v1/tasks/cancel-b/file" />);
    await screen.findByText("文件 B");
    expect(signalA.aborted).toBe(true);
    await act(async () => resolveA({ ok: true, json: async () => ({ text: "文件 A", kind: "text" }) }));
    expect(screen.queryByText("文件 A")).not.toBeInTheDocument();
  });

  it("requests one Excel sheet and row window, permits jumping to the final rows", async () => {
    const fetcher = vi.fn(async (url: string) => {
      const query = new URL(url, "http://localhost").searchParams;
      const offset = Number(query.get("offset"));
      const index = Number(query.get("sheet"));
      return { ok: true, json: async () => ({ sheets: [{ name: index ? "第二页" : "第一页", columns: ["A"], rows: [[`第 ${offset + 1} 行`]] }],
        sheet_names: ["第一页", "第二页"], total_rows: 10000, next_offset: offset < 9950 ? offset + 50 : null }) };
    });
    vi.stubGlobal("fetch", fetcher);
    render(<DocumentPreview contentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
      filename="一万行.xlsx" pageNumber={1} scale={1} url="/api/v1/tasks/xlsx-window-test/file" />);
    await screen.findByText("第 1 行");
    fireEvent.change(screen.getByRole("spinbutton", { name: "原件行号" }), { target: { value: "10000" } });
    fireEvent.click(screen.getByRole("button", { name: "前往" }));
    await screen.findByText("第 9951 行");
    expect(fetcher.mock.calls.at(-1)?.[0]).toContain("offset=9950");
    fireEvent.click(screen.getByRole("tab", { name: "第二页" }));
    await screen.findByText("第 1 行");
    expect(fetcher.mock.calls.at(-1)?.[0]).toContain("sheet=1&offset=0");
    expect(screen.getByRole("spinbutton", { name: "原件行号" })).toHaveValue(1);
    fireEvent.click(screen.getByRole("tab", { name: "第一页" }));
    await screen.findByText("第 1 行");
    expect(screen.getByRole("spinbutton", { name: "原件行号" })).toHaveValue(1);
    fireEvent.click(screen.getByRole("button", { name: "下一段" }));
    await screen.findByText("第 51 行");
    expect(screen.getByRole("spinbutton", { name: "原件行号" })).toHaveValue(51);
  });

  it("reports image failure inside the preview", async () => {
    render(<DocumentPreview contentType="image/png" filename="fail.png" pageNumber={1} scale={1}
      url="/api/v1/tasks/image-failure-test/file" />);
    expect(screen.queryByText("下载完整原件")).not.toBeInTheDocument();
    fireEvent.error(screen.getByRole("img"));
    await waitFor(() => expect(screen.getByText("这张图片无法预览，请在原件中查看。")).toBeInTheDocument());
  });
});
