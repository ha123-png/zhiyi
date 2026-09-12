import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { PDFDocumentLoadingTask, PDFDocumentProxy } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { responseErrorMessage } from "../api";

interface EvidenceRegion {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface DocumentPreviewProps {
  contentType: string;
  filename: string;
  pageNumber: number;
  region?: EvidenceRegion | null;
  scale: number;
  url: string;
  onPageCount?: (count: number) => void;
  /** 多页文件按页上下排列（用于原文件预览）；默认单页模式 */
  flowPages?: boolean;
  showDownload?: boolean;
}

interface TextPreviewResponse {
  kind: "text" | "markdown";
  text: string;
  truncated: boolean;
  image_count: number;
}

const TEXT_PREVIEW_TYPES = new Set([
  "text/plain",
  "text/markdown",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
]);
const XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

interface XlsxSheet {
  name: string;
  columns: string[];
  rows: string[][];
}

interface XlsxPreviewResponse {
  sheets: XlsxSheet[];
}

interface DocxBlock {
  type: string;
  text?: string;
  level?: number;
  rows?: string[][];
  image_index?: number;
  caption?: string;
}

interface DocxPreviewResponse {
  blocks: DocxBlock[];
}

export function DocumentPreview(props: DocumentPreviewProps) {
  return <><DocumentContent {...props} />{props.showDownload !== false && <a className="original-download" href={props.url} download={props.filename}>下载完整原件</a>}</>;
}

function DocumentContent({
  contentType,
  filename,
  pageNumber,
  region = null,
  scale,
  url,
  onPageCount,
  flowPages = false,
}: DocumentPreviewProps) {
  if (contentType === "application/pdf") {
    return flowPages ? (
      <PdfFlow filename={filename} onPageCount={onPageCount} url={url} />
    ) : (
      <PdfPage
        filename={filename}
        onPageCount={onPageCount}
        pageNumber={pageNumber}
        region={region}
        scale={scale}
        url={url}
      />
    );
  }

  if (contentType === XLSX_TYPE) {
    return <XlsxDocument filename={filename} url={url} />;
  }

  if (contentType === DOCX_TYPE) {
    return <WordDocument filename={filename} url={url} />;
  }

  if (TEXT_PREVIEW_TYPES.has(contentType)) {
    return <TextDocument filename={filename} scale={scale} url={url} />;
  }

  return (
    <div className="document-preview-stage" style={{ width: `${scale * 100}%` }}>
      <img alt={`原文件：${filename}`} className="document-preview-image" src={url} />
      {region ? <EvidenceHighlight region={region} /> : null}
    </div>
  );
}

function TextDocument({ filename, scale, url }: Pick<DocumentPreviewProps, "filename" | "scale" | "url">) {
  const [preview, setPreview] = useState<TextPreviewResponse | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setPreview(null);
    setError("");
    fetch(url.replace(/\/file$/, "/preview"), { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(await responseErrorMessage(response, "原文件预览"));
        return response.json() as Promise<TextPreviewResponse>;
      })
      .then(setPreview)
      .catch((reason: unknown) => {
        if ((reason as { name?: string })?.name !== "AbortError") {
          setError(reason instanceof Error ? reason.message : "原文件预览失败");
        }
      });
    return () => controller.abort();
  }, [url]);

  if (error) return <div className="document-preview-error"><div>{error}</div><p className="small muted">预览失败不影响已保存的原件，可下载后查看。</p></div>;
  if (!preview) return <div className="document-preview-loading">正在生成原文件预览…</div>;

  const previewBaseUrl = url.replace(/\/file$/, "/preview");
  return (
    <article
      aria-label={`原文件：${filename}`}
      className="document-text-preview"
      style={{ fontSize: `${scale}rem` }}
    >
      {preview.kind === "markdown" ? <MarkdownDocument text={preview.text} /> : <pre>{preview.text}</pre>}
      {preview.truncated ? <div className="document-preview-note">预览仅显示开头 {preview.text.length.toLocaleString("zh-CN")} 个字符，后续内容未在此展示。可下载完整原件；这不是模型的读取范围。</div> : null}
      {Array.from({ length: preview.image_count }, (_, index) => (
        <figure className="document-embedded-image" key={index}>
          <figcaption>图片 {index + 1}</figcaption>
          <img alt={`${filename} 内嵌图片 ${index + 1}`} src={`${previewBaseUrl}/images/${index + 1}`} />
        </figure>
      ))}
    </article>
  );
}

function MarkdownDocument({ text }: { text: string }) {
  return (
    <div className="document-markdown-preview">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}

const DOCX_HEADING_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6"] as const;

function WordDocument({ filename, url }: { filename: string; url: string }) {
  const [preview, setPreview] = useState<DocxPreviewResponse | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setPreview(null);
    setError("");
    fetch(url.replace(/\/file$/, "/preview/docx"), { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(await responseErrorMessage(response, "原文件预览"));
        return response.json() as Promise<DocxPreviewResponse>;
      })
      .then(setPreview)
      .catch((reason: unknown) => {
        if ((reason as { name?: string })?.name !== "AbortError") {
          setError(reason instanceof Error ? reason.message : "原文件预览失败");
        }
      });
    return () => controller.abort();
  }, [url]);

  if (error) return <div className="document-preview-error"><div>{error}</div><p className="small muted">预览失败不影响已保存的原件，可下载后查看。</p></div>;
  if (!preview) return <div className="document-preview-loading">正在生成原文件预览…</div>;

  const imageBaseUrl = url.replace(/\/file$/, "/preview");
  if (preview.blocks.length === 0) {
    return (
      <div className="document-text-preview">
        <div className="table-empty-state"><div>这个文档没有可预览内容</div></div>
      </div>
    );
  }

  return (
    <article aria-label={`原文件：${filename}`} className="document-docx-preview">
      {preview.blocks.map((block, index) => {
        switch (block.type) {
          case "heading": {
            const level = Math.min(Math.max(block.level ?? 1, 1), 6);
            const Tag = DOCX_HEADING_TAGS[level - 1];
            return <Tag key={index}>{block.text}</Tag>;
          }
          case "list":
            return <div className="docx-list-item" key={index}>{block.text}</div>;
          case "table":
            return (
              <div className="data-table-scroll" key={index}>
                {renderDocxTable(block.rows ?? [])}
              </div>
            );
          case "image":
            return (
              <figure className="document-embedded-image" key={index}>
                <figcaption>{block.caption}</figcaption>
                <img alt={block.caption} src={`${imageBaseUrl}/images/${block.image_index}`} />
              </figure>
            );
          default:
            return <p key={index}>{block.text}</p>;
        }
      })}
    </article>
  );
}

function renderDocxTable(rows: string[][]) {
  if (rows.length === 0 || (rows.length === 1 && rows[0].every((cell) => !cell))) {
    return <div className="table-empty-state"><div>这个表格没有数据</div></div>;
  }
  const columnCount = Math.max(...rows.map((row) => row.length), 0);
  return (
    <table className="data-table">
      <colgroup>
        {Array.from({ length: columnCount }, (_, index) => (
          <col key={index} style={{ width: 132 }} />
        ))}
      </colgroup>
      <tbody>
        {rows.map((row, rowIndex) => (
          <tr key={rowIndex}>
            {Array.from({ length: columnCount }, (_, colIndex) => (
              <td key={colIndex} title={row[colIndex] ?? ""}>{row[colIndex] ?? ""}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function XlsxDocument({ filename, url }: { filename: string; url: string }) {
  const [preview, setPreview] = useState<XlsxPreviewResponse | null>(null);
  const [error, setError] = useState("");
  const [activeSheet, setActiveSheet] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setPreview(null);
    setError("");
    setActiveSheet(0);
    fetch(url.replace(/\/file$/, "/preview/xlsx"), { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(await responseErrorMessage(response, "原文件预览"));
        return response.json() as Promise<XlsxPreviewResponse>;
      })
      .then(setPreview)
      .catch((reason: unknown) => {
        if ((reason as { name?: string })?.name !== "AbortError") {
          setError(reason instanceof Error ? reason.message : "原文件预览失败");
        }
      });
    return () => controller.abort();
  }, [url]);

  if (error) return <div className="document-preview-error"><div>{error}</div><p className="small muted">预览失败不影响已保存的原件，可下载后查看。</p></div>;
  if (!preview) return <div className="document-preview-loading">正在生成原文件预览…</div>;

  const sheet = preview.sheets[activeSheet] ?? preview.sheets[0];

  return (
    <article aria-label={`原文件：${filename}`} className="document-xlsx-preview">
      {preview.sheets.length > 1 && (
        <div className="xlsx-sheet-tabs" role="tablist">
          {preview.sheets.map((s, index) => (
            <button
              aria-selected={index === activeSheet}
              className={`xlsx-sheet-tab${index === activeSheet ? " active" : ""}`}
              key={s.name + index}
              onClick={() => setActiveSheet(index)}
              role="tab"
              type="button"
            >
              {s.name}
            </button>
          ))}
        </div>
      )}
      <div className="data-table-scroll">
        {sheet.columns.length === 0 ? (
          <div className="table-empty-state">
            <div>这个工作表没有数据</div>
          </div>
        ) : (
          <table className="data-table">
            <colgroup>
              {sheet.columns.map((_, index) => (
                <col key={index} style={{ width: 132 }} />
              ))}
            </colgroup>
            <thead>
              <tr>
                {sheet.columns.map((column, index) => (
                  <th key={index}>{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sheet.rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {sheet.columns.map((_, colIndex) => (
                    <td key={colIndex} title={row[colIndex]}>
                      {row[colIndex] ?? ""}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </article>
  );
}

function PdfPage({
  filename,
  onPageCount,
  pageNumber,
  region,
  scale,
  url,
}: Omit<DocumentPreviewProps, "contentType">) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [document, setDocument] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState("");
  const [rendering, setRendering] = useState(true);

  useEffect(() => {
    let cancelled = false;
    let loadingTask: PDFDocumentLoadingTask | null = null;
    setDocument(null);
    setError("");
    setRendering(true);
    import("pdfjs-dist")
      .then(({ GlobalWorkerOptions, getDocument }) => {
        GlobalWorkerOptions.workerSrc = workerUrl;
        loadingTask = getDocument({ url });
        return loadingTask.promise;
      })
      .then((pdf) => {
        if (cancelled) return;
        setDocument(pdf);
        onPageCount?.(pdf.numPages);
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : "PDF 读取失败");
          setRendering(false);
        }
      });
    return () => {
      cancelled = true;
      void loadingTask?.destroy();
    };
  }, [onPageCount, url]);

  useEffect(() => {
    if (!document || !canvasRef.current) return;
    let cancelled = false;
    let renderTask: { cancel: () => void; promise: Promise<unknown> } | null = null;
    setRendering(true);
    setError("");
    document
      .getPage(Math.min(document.numPages, Math.max(1, pageNumber)))
      .then((page) => {
        if (cancelled || !canvasRef.current) return;
        const viewport = page.getViewport({ scale });
        const outputScale = window.devicePixelRatio || 1;
        const canvas = canvasRef.current;
        const context = canvas.getContext("2d");
        if (!context) throw new Error("浏览器无法创建 PDF 画布");
        canvas.width = Math.floor(viewport.width * outputScale);
        canvas.height = Math.floor(viewport.height * outputScale);
        canvas.style.width = `${Math.floor(viewport.width)}px`;
        canvas.style.height = `${Math.floor(viewport.height)}px`;
        renderTask = page.render({
          canvas,
          canvasContext: context,
          transform: outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : undefined,
          viewport,
        });
        return renderTask.promise;
      })
      .then(() => {
        if (!cancelled) setRendering(false);
      })
      .catch((reason: unknown) => {
        if (!cancelled && (reason as { name?: string })?.name !== "RenderingCancelledException") {
          setError(reason instanceof Error ? reason.message : "PDF 页面渲染失败");
          setRendering(false);
        }
      });
    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
  }, [document, pageNumber, scale]);

  return (
    <div className="document-preview-stage pdf-preview-stage">
      <canvas aria-label={`${filename} 第 ${pageNumber} 页`} ref={canvasRef} />
      {rendering ? <div className="document-preview-loading">正在渲染第 {pageNumber} 页…</div> : null}
      {error ? <div className="document-preview-error"><div>{error}</div><p className="small muted">预览失败不影响已保存的原件，可下载后查看。</p></div> : null}
      {region ? <EvidenceHighlight region={region} /> : null}
    </div>
  );
}

/** 原文件预览：多页 PDF 从上到下按页排列，每页独立一块，向下滚动逐页查看。 */
function PdfFlow({
  filename,
  onPageCount,
  url,
}: Pick<DocumentPreviewProps, "filename" | "onPageCount" | "url">) {
  const [document, setDocument] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    let loadingTask: PDFDocumentLoadingTask | null = null;
    setDocument(null);
    setError("");
    import("pdfjs-dist")
      .then(({ GlobalWorkerOptions, getDocument }) => {
        GlobalWorkerOptions.workerSrc = workerUrl;
        loadingTask = getDocument({ url });
        return loadingTask.promise;
      })
      .then((pdf) => {
        if (cancelled) return;
        setDocument(pdf);
        onPageCount?.(pdf.numPages);
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : "PDF 读取失败");
        }
      });
    return () => {
      cancelled = true;
      void loadingTask?.destroy();
    };
  }, [onPageCount, url]);

  if (error) return <div className="document-preview-error"><div>{error}</div><p className="small muted">预览失败不影响已保存的原件，可下载后查看。</p></div>;
  if (!document) return <div className="document-preview-loading">正在读取 PDF…</div>;

  return (
    <div className="document-preview-flow">
      {Array.from({ length: document.numPages }, (_, index) => (
        <PdfFlowPage
          document={document}
          filename={filename}
          key={index + 1}
          pageNumber={index + 1}
        />
      ))}
    </div>
  );
}

function PdfFlowPage({
  document,
  filename,
  pageNumber,
}: {
  document: PDFDocumentProxy;
  filename: string;
  pageNumber: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [error, setError] = useState("");
  const [rendering, setRendering] = useState(true);

  useEffect(() => {
    let cancelled = false;
    let renderTask: { cancel: () => void; promise: Promise<unknown> } | null = null;
    setRendering(true);
    setError("");
    document
      .getPage(pageNumber)
      .then((page) => {
        if (cancelled || !canvasRef.current) return;
        const viewport = page.getViewport({ scale: 1 });
        const outputScale = window.devicePixelRatio || 1;
        const canvas = canvasRef.current;
        const context = canvas.getContext("2d");
        if (!context) throw new Error("浏览器无法创建 PDF 画布");
        canvas.width = Math.floor(viewport.width * outputScale);
        canvas.height = Math.floor(viewport.height * outputScale);
        canvas.style.width = `${Math.floor(viewport.width)}px`;
        canvas.style.height = `${Math.floor(viewport.height)}px`;
        renderTask = page.render({
          canvas,
          canvasContext: context,
          transform: outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : undefined,
          viewport,
        });
        return renderTask.promise;
      })
      .then(() => {
        if (!cancelled) setRendering(false);
      })
      .catch((reason: unknown) => {
        if (!cancelled && (reason as { name?: string })?.name !== "RenderingCancelledException") {
          setError(reason instanceof Error ? reason.message : "PDF 页面渲染失败");
          setRendering(false);
        }
      });
    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
  }, [document, pageNumber]);

  return (
    <div className="document-preview-page">
      <div className="document-preview-page-label">第 {pageNumber} 页</div>
      <div className="pdf-preview-stage">
        <canvas aria-label={`${filename} 第 ${pageNumber} 页`} ref={canvasRef} />
        {rendering ? <div className="document-preview-loading">正在渲染第 {pageNumber} 页…</div> : null}
        {error ? <div className="document-preview-error"><div>{error}</div><p className="small muted">预览失败不影响已保存的原件，可下载后查看。</p></div> : null}
      </div>
    </div>
  );
}

function EvidenceHighlight({ region }: { region: EvidenceRegion }) {
  return (
    <div
      aria-label="字段在原文件中的位置"
      className="evidence-highlight"
      role="mark"
      style={{
        left: `${region.x * 100}%`,
        top: `${region.y * 100}%`,
        width: `${region.width * 100}%`,
        height: `${region.height * 100}%`,
      }}
    />
  );
}
