import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { MutableRefObject, ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { PDFDocumentLoadingTask, PDFDocumentProxy } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { responseErrorMessage } from "../api";
import "./document-preview.css";

interface EvidenceRegion { x: number; y: number; width: number; height: number }
export interface DocumentPreviewBookmark {
  page?: number;
  pageOffset?: number;
  offset?: number;
  sheet?: number;
  columnOffset?: number;
  scrollTop?: number;
  scrollLeft?: number;
  scale?: number;
  previousOffsets?: number[];
}
interface DocumentPreviewProps {
  contentType: string;
  filename: string;
  pageNumber: number;
  region?: EvidenceRegion | null;
  scale: number;
  url: string;
  onPageCount?: (count: number) => void;
  flowPages?: boolean;
  bookmark?: DocumentPreviewBookmark;
  onBookmarkChange?: (bookmark: DocumentPreviewBookmark) => void;
}
interface PreviewState {
  bookmark: MutableRefObject<DocumentPreviewBookmark>;
  root: MutableRefObject<HTMLDivElement | null>;
  save: (patch: DocumentPreviewBookmark) => void;
}
const PreviewContext = createContext<PreviewState | null>(null);
const readingPositions = new Map<string, DocumentPreviewBookmark>();
function useReading() { return useContext(PreviewContext)!; }
function scrollContainer(root: HTMLElement | null): HTMLElement | null {
  const marked = root?.closest<HTMLElement>("[data-document-scroll]");
  if (marked) return marked;
  let parent = root?.parentElement;
  while (parent) {
    if (/(auto|scroll)/.test(getComputedStyle(parent).overflowY)) return parent;
    parent = parent.parentElement;
  }
  return null;
}
function previewUrl(url: string, suffix: string) { return url.replace(/\/(file|original)(?:\?.*)?$/, suffix); }

/** Changing files remounts requests/canvases, while only small reading bookmarks survive. */
export function DocumentPreview(props: DocumentPreviewProps) {
  return <PreviewSession key={props.url} {...props} />;
}
function PreviewSession(props: DocumentPreviewProps) {
  const root = useRef<HTMLDivElement | null>(null);
  const bookmark = useRef<DocumentPreviewBookmark>({ ...(readingPositions.get(props.url) ?? {}), ...props.bookmark });
  const notify = useRef(props.onBookmarkChange);
  notify.current = props.onBookmarkChange;
  const save = useCallback((patch: DocumentPreviewBookmark) => {
    bookmark.current = { ...bookmark.current, ...patch };
    readingPositions.delete(props.url);
    readingPositions.set(props.url, bookmark.current);
    if (readingPositions.size > 24) readingPositions.delete(readingPositions.keys().next().value!);
    notify.current?.(bookmark.current);
  }, [props.url]);
  const value = useMemo(() => ({ root, bookmark, save }), [save]);
  useEffect(() => {
    const scroll = scrollContainer(root.current);
    if (!scroll) return;
    const remember = () => {
      // Persist before React can remove the preview pane. A queued animation
      // frame can be cancelled during unmount, and reading scrollTop from a
      // detached pane may return 0, overwriting the last useful bookmark.
      save({ scrollTop: scroll.scrollTop, scrollLeft: scroll.scrollLeft });
    };
    scroll.addEventListener("scroll", remember, { passive: true });
    return () => {
      scroll.removeEventListener("scroll", remember);
    };
  }, [save]);
  return <PreviewContext.Provider value={value}><div ref={root} className="document-preview-content">
    <DocumentContent {...props} />
  </div></PreviewContext.Provider>;
}

const XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
function DocumentContent(props: DocumentPreviewProps) {
  const { contentType, filename, url, scale, region } = props;
  if (contentType === "application/pdf") return <PdfDocument {...props} />;
  if (contentType === XLSX_TYPE) return <XlsxDocument filename={filename} url={url} />;
  if (contentType === DOCX_TYPE) return <WordDocument filename={filename} url={url} />;
  if (["text/plain", "text/markdown"].includes(contentType)) return <TextDocument {...props} />;
  return <ImageDocument filename={filename} scale={scale} region={region} url={url} />;
}
function ImageDocument({ filename, scale, region, url }: Pick<DocumentPreviewProps, "filename" | "scale" | "region" | "url">) {
  const [error, setError] = useState(false);
  const { root, bookmark } = useReading();
  if (error) return <PreviewError error="这张图片无法预览，请在原件中查看。" />;
  return <div className="document-preview-stage" style={{ width: `${scale * 100}%` }}>
    <img alt={`原文件：${filename}`} className="document-preview-image" src={previewUrl(url, "/preview/image")}
      onError={() => setError(true)} onLoad={() => {
        const scroll = scrollContainer(root.current);
        if (scroll) { scroll.scrollTop = bookmark.current.scrollTop ?? 0; scroll.scrollLeft = bookmark.current.scrollLeft ?? 0; }
      }} />
    {region && <EvidenceHighlight region={region} />}
  </div>;
}
function PreviewError({ error }: { error: string }) {
  return <div className="document-preview-error" role="status"><div>{error}</div><p className="small muted">预览失败不影响已保存的原件，可在原件中查看。</p></div>;
}
function useWindow<T>(requestUrl: string) {
  const [result, setResult] = useState<{ url: string; data?: T; error?: string }>({ url: requestUrl });
  const { root, bookmark } = useReading();
  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    fetch(requestUrl, { signal: controller.signal, cache: "no-store" }).then(async response => {
      if (!response.ok) throw new Error(await responseErrorMessage(response, "原文件预览"));
      return await response.json() as T;
    }).then(data => {
      if (!cancelled) setResult({ url: requestUrl, data });
    }).catch((reason: unknown) => {
      if (!cancelled) setResult({ url: requestUrl, error: reason instanceof Error ? reason.message : "原文件预览失败" });
    });
    return () => { cancelled = true; controller.abort(); };
  }, [requestUrl]);
  const data = result.url === requestUrl ? result.data : undefined;
  useLayoutEffect(() => {
    if (!data) return;
    const scroll = scrollContainer(root.current);
    if (scroll) { scroll.scrollTop = bookmark.current.scrollTop ?? 0; scroll.scrollLeft = bookmark.current.scrollLeft ?? 0; }
  }, [data, root, bookmark]);
  return { data, error: result.url === requestUrl ? result.error : undefined };
}
function PageControls({ offset, next, onMove, label, previous, onPrevious, previousDisabled = false }: {
  offset: number; next?: number | null; onMove: (offset: number) => void; label: string; previous: number;
  onPrevious?: () => void; previousDisabled?: boolean;
}) {
  if (!offset && next == null) return null;
  return <nav className="document-preview-pagination" aria-label="原件预览分页">
    <button className="btn secondary sm" disabled={offset === 0 || previousDisabled} onClick={() => onPrevious ? onPrevious() : onMove(previous)} type="button">上一段</button>
    <span className="small muted">{label}</span>
    <button className="btn secondary sm" disabled={next == null} onClick={() => next != null && onMove(next)} type="button">下一段</button>
  </nav>;
}
function useOffset() {
  const { bookmark, save } = useReading();
  const [offset, setOffset] = useState(bookmark.current.offset ?? 0);
  const back = useRef<number[]>(bookmark.current.previousOffsets ?? []);
  const move = (next: number) => {
    if (next > offset) back.current = [...back.current, offset].slice(-64);
    else if (back.current.at(-1) === next) back.current.pop();
    save({ offset: next, scrollTop: 0, previousOffsets: back.current }); setOffset(next);
  };
  return { offset, move, previous: back.current.at(-1) ?? 0, hasPrevious: back.current.length > 0 };
}
interface TextPreviewResponse { kind: "text" | "markdown"; text: string; truncated: boolean; next_offset?: number | null; offset?: number; total_bytes?: number }
function TextDocument({ filename, scale, url }: DocumentPreviewProps) {
  const { offset, move, previous, hasPrevious } = useOffset();
  const { data, error } = useWindow<TextPreviewResponse>(`${previewUrl(url, "/preview")}?offset=${offset}`);
  const [resolvingPrevious, setResolvingPrevious] = useState(false);
  const [previousError, setPreviousError] = useState<string | null>(null);
  const offsetRef = useRef(offset);
  offsetRef.current = offset;
  const previousSeqRef = useRef(0);
  const previousControllerRef = useRef<AbortController | null>(null);
  useEffect(() => () => {
    previousSeqRef.current += 1;
    previousControllerRef.current?.abort();
  }, []);

  const goPrevious = () => {
    if (hasPrevious) {
      previousSeqRef.current += 1;
      previousControllerRef.current?.abort();
      setResolvingPrevious(false);
      setPreviousError(null);
      move(previous);
      return;
    }
    const target = offset;
    const seq = ++previousSeqRef.current;
    previousControllerRef.current?.abort();
    const controller = new AbortController();
    previousControllerRef.current = controller;
    setResolvingPrevious(true);
    setPreviousError(null);
    const baseUrl = previewUrl(url, "/preview");
    const candidates = [...new Set([0, 1, 2, 3].map((tail) => Math.max(0, target - 32_768 + tail)))];
    void Promise.all(candidates.map(async (candidate) => {
      const response = await fetch(`${baseUrl}?offset=${candidate}`, { signal: controller.signal, cache: "no-store" });
      if (!response.ok) throw new Error(await responseErrorMessage(response, "原文件预览"));
      return { candidate, data: await response.json() as TextPreviewResponse };
    })).then((windows) => {
      if (seq !== previousSeqRef.current || offsetRef.current !== target) return;
      const previousWindow = windows.find((window) => window.data.next_offset === target);
      if (!previousWindow) {
        setPreviousError("无法确定上一段的原件位置，请重试或下载完整原件查看。");
        return;
      }
      move(previousWindow.candidate);
    }).catch((reason: unknown) => {
      if (seq !== previousSeqRef.current || controller.signal.aborted) return;
      setPreviousError(reason instanceof Error ? reason.message : "读取上一段失败，请重试。");
    }).finally(() => {
      if (seq === previousSeqRef.current) setResolvingPrevious(false);
    });
  };
  if (error) return <PreviewError error={error} />;
  if (!data) return <div className="document-preview-loading">正在读取原文件…</div>;
  return <article aria-label={`原文件：${filename}`} className="document-text-preview" style={{ fontSize: `${scale}rem` }}>
    {data.kind === "markdown" ? <div className="document-markdown-preview"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{
      img: ({ alt }) => <span>[图片：{alt || "未加载外部图片"}]</span>,
      a: ({ children, href }) => <a href={href?.startsWith("https://") || href?.startsWith("http://") ? href : undefined}
        target="_blank" rel="noopener noreferrer">{children}</a>,
    }}>{data.text}</ReactMarkdown></div> : <pre>{data.text}</pre>}
    {data.truncated && data.next_offset == null && <div className="document-preview-note">预览仅显示开头 {data.text.length.toLocaleString("zh-CN")} 个字符；这不是模型的读取范围。</div>}
    {previousError && <div className="document-preview-error" role="alert">{previousError}</div>}
    <PageControls offset={offset} next={data.next_offset} onMove={(next) => { previousSeqRef.current += 1; previousControllerRef.current?.abort(); setResolvingPrevious(false); move(next); }} previous={previous}
      previousDisabled={resolvingPrevious} onPrevious={goPrevious}
      label="分段预览 · 原件完整保留" />
  </article>;
}
interface DocxBlock { type: string; text?: string; level?: number; rows?: string[][]; image_index?: number; caption?: string }
interface DocxPreviewResponse { blocks: DocxBlock[]; next_offset?: number | null; clipped_cells?: boolean }
const HEADING_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6"] as const;
function WordDocument({ filename, url }: { filename: string; url: string }) {
  const { offset, move } = useOffset();
  const { data, error } = useWindow<DocxPreviewResponse>(`${previewUrl(url, "/preview/docx")}?offset=${offset}`);
  if (error) return <PreviewError error={error} />;
  if (!data) return <div className="document-preview-loading">正在读取原文件…</div>;
  return <article aria-label={`原文件：${filename}`} className="document-docx-preview">
    {!data.blocks.length && <div className="table-empty-state">这个文档没有可预览内容</div>}
    {data.blocks.map((block, index) => {
      switch (block.type) {
        case "heading": { const Tag = HEADING_TAGS[Math.min(Math.max(block.level ?? 1, 1), 6) - 1]; return <Tag key={index}>{block.text}</Tag>; }
        case "list": return <div className="docx-list-item" key={index}>{block.text}</div>;
        case "table": return <div className="data-table-scroll" key={index}><table className="data-table"><tbody>{block.rows?.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}>{cell}</td>)}</tr>)}</tbody></table></div>;
        case "image": return <figure className="document-embedded-image" key={index}><figcaption>{block.caption}</figcaption><img loading="lazy" alt={block.caption} src={`${previewUrl(url, "/preview")}/images/${block.image_index}`} /></figure>;
        default: return <p key={index}>{block.text}</p>;
      }
    })}
    {data.clipped_cells && <div className="document-preview-note">超长单元格仅预览前 256 字；完整内容保留在原件中。</div>}
    <PageControls offset={offset} next={data.next_offset} onMove={move} previous={Math.max(0, offset - 40)} label={`第 ${Math.floor(offset / 40) + 1} 段`} />
  </article>;
}
interface XlsxSheet { name: string; columns: string[]; rows: string[][] }
interface XlsxPreviewResponse {
  sheets: XlsxSheet[]; sheet_names?: string[]; next_offset?: number | null;
  next_column_offset?: number | null; total_rows?: number; clipped_cells?: boolean;
}
function XlsxDocument({ filename, url }: { filename: string; url: string }) {
  const { bookmark, save } = useReading();
  const { offset, move } = useOffset();
  const [sheetIndex, setSheet] = useState(bookmark.current.sheet ?? 0);
  const [column, setColumn] = useState(bookmark.current.columnOffset ?? 0);
  const [rowJump, setRowJump] = useState(String(offset + 1));
  const { data, error } = useWindow<XlsxPreviewResponse>(`${previewUrl(url, "/preview/xlsx")}?sheet=${sheetIndex}&offset=${offset}&column_offset=${column}`);
  if (error) return <PreviewError error={error} />;
  if (!data) return <div className="document-preview-loading">正在读取工作表…</div>;
  const sheet = data.sheets[0];
  const names = data.sheet_names ?? data.sheets.map(s => s.name);
  return <article aria-label={`原文件：${filename}`} className="document-xlsx-preview">
    {names.length > 1 && <div className="xlsx-sheet-tabs" role="tablist" aria-label="原件工作表">{names.map((name, index) =>
      <button className={`xlsx-sheet-tab${index === sheetIndex ? " active" : ""}`} role="tab" aria-selected={index === sheetIndex} key={name} type="button" onClick={() => {
        save({ sheet: index, columnOffset: 0, offset: 0, scrollTop: 0 }); setSheet(index); setColumn(0); setRowJump("1"); move(0);
      }}>{name}</button>)}</div>}
    <div className="data-table-scroll">{!sheet?.columns.length ? <div className="table-empty-state">这个工作表没有数据</div> :
      <table className="data-table"><thead><tr><th scope="col">行</th>{sheet.columns.map(column => <th key={column} scope="col">{column}</th>)}</tr></thead>
        <tbody>{sheet.rows.map((row, i) => <tr key={i}><th scope="row">{offset + i + 1}</th>{sheet.columns.map((column, j) => <td key={column}>{row[j] ?? ""}</td>)}</tr>)}</tbody></table>}</div>
    {(column > 0 || data.next_column_offset != null) && <nav className="document-preview-pagination" aria-label="原件预览列">
      <button className="btn secondary sm" type="button" disabled={!column} onClick={() => { const next = Math.max(0, column - 16); save({ columnOffset: next }); setColumn(next); }}>前 16 列</button>
      <span className="small muted">{sheet?.columns[0]}–{sheet?.columns.at(-1)}</span>
      <button className="btn secondary sm" type="button" disabled={data.next_column_offset == null} onClick={() => { save({ columnOffset: data.next_column_offset! }); setColumn(data.next_column_offset!); }}>后 16 列</button>
    </nav>}
    {data.clipped_cells && <div className="document-preview-note">超长单元格仅预览前 256 字；完整内容保留在原件中。</div>}
    {(data.total_rows ?? 0) > 50 && <form className="document-preview-page-jump" onSubmit={event => {
      event.preventDefault();
      const row = Math.max(1, Math.min(data.total_rows!, Number(rowJump) || 1));
      setRowJump(String(row));
      move(Math.floor((row - 1) / 50) * 50);
    }}><label>定位行 <input aria-label="原件行号" type="number" min={1} max={data.total_rows} value={rowJump} onChange={event => setRowJump(event.target.value)} /></label>
      <button className="btn secondary sm" type="submit">前往</button></form>}
    <PageControls offset={offset} next={data.next_offset} previous={Math.max(0, offset - 50)} onMove={next => { setRowJump(String(next + 1)); move(next); }}
      label={`${offset + 1}–${offset + (sheet?.rows.length ?? 0)} 行${data.total_rows ? ` / ${data.total_rows}` : ""}`} />
  </article>;
}

function PdfDocument(props: DocumentPreviewProps) {
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState("");
  const pageCount = useRef(props.onPageCount); pageCount.current = props.onPageCount;
  useEffect(() => {
    let cancelled = false;
    let loading: PDFDocumentLoadingTask | undefined;
    import("pdfjs-dist").then(({ GlobalWorkerOptions, getDocument }) => {
      if (cancelled) return;
      GlobalWorkerOptions.workerSrc = workerUrl;
      loading = getDocument({ url: props.url, disableAutoFetch: true, disableStream: true });
      return loading.promise;
    }).then(result => {
      if (result && !cancelled) { setPdf(result); pageCount.current?.(result.numPages); }
    }).catch(reason => { if (!cancelled) setError(reason instanceof Error ? reason.message : "PDF 读取失败"); });
    return () => { cancelled = true; void loading?.destroy(); };
  }, [props.url]);
  if (error) return <PreviewError error={error} />;
  if (!pdf) return <div className="document-preview-loading">正在读取 PDF…</div>;
  return props.flowPages ? <PdfFlow pdf={pdf} {...props} /> : <PdfCanvas pdf={pdf} {...props} pageNumber={Math.max(1, Math.min(props.pageNumber, pdf.numPages))} />;
}
function PdfCanvas({ pdf, filename, pageNumber, scale, region, onSize }: {
  pdf: PDFDocumentProxy; filename: string; pageNumber: number; scale: number; region?: EvidenceRegion | null;
  onSize?: (width: number, height: number) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [error, setError] = useState("");
  const [rendering, setRendering] = useState(true);
  const sizeCallback = useRef(onSize); sizeCallback.current = onSize;
  useEffect(() => {
    let cancelled = false;
    let task: { cancel: () => void; promise: Promise<unknown> } | undefined;
    let loadedPage: Awaited<ReturnType<PDFDocumentProxy["getPage"]>> | undefined;
    const canvas = canvasRef.current!;
    setRendering(true); setError("");
    pdf.getPage(pageNumber).then(page => {
      loadedPage = page;
      if (cancelled) { page.cleanup(); return; }
      const viewport = page.getViewport({ scale });
      const outputScale = Math.min(window.devicePixelRatio || 1, 2, 1600 / Math.max(viewport.width, viewport.height));
      const context = canvas.getContext("2d");
      if (!context) throw new Error("浏览器无法创建 PDF 画布");
      canvas.width = Math.max(1, Math.floor(viewport.width * outputScale));
      canvas.height = Math.max(1, Math.floor(viewport.height * outputScale));
      canvas.style.width = `${viewport.width}px`; canvas.style.height = `${viewport.height}px`;
      sizeCallback.current?.(viewport.width / scale, viewport.height / scale);
      task = page.render({ canvas, canvasContext: context, viewport,
        transform: outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : undefined });
      return task.promise;
    }).then(() => { if (!cancelled) setRendering(false); }).catch(reason => {
      if (!cancelled && reason?.name !== "RenderingCancelledException") { setRendering(false); setError(reason instanceof Error ? reason.message : "PDF 页面渲染失败"); }
    });
    return () => {
      cancelled = true; task?.cancel(); canvas.width = 0; canvas.height = 0;
      if (task) void task.promise.catch(() => undefined).finally(() => loadedPage?.cleanup());
      else loadedPage?.cleanup();
    };
  }, [pdf, pageNumber, scale]);
  return <div className="document-preview-stage pdf-preview-stage">
    <canvas aria-label={`${filename} 第 ${pageNumber} 页`} ref={canvasRef} />
    {rendering && <div className="document-preview-loading">正在渲染第 {pageNumber} 页…</div>}
    {error && <PreviewError error={error} />}
    {region && <EvidenceHighlight region={region} />}
  </div>;
}

/** A bounded canvas window. Spacers preserve continuous scrolling, not full-page canvases. */
function PdfFlow({ pdf, filename }: { pdf: PDFDocumentProxy; filename: string }) {
  const { root, bookmark, save } = useReading();
  const flow = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  const [sizes, setSizes] = useState<Record<number, [number, number]>>({});
  const [range, setRange] = useState<[number, number]>(() => {
    const page = Math.min(pdf.numPages - 1, Math.max(0, (bookmark.current.page ?? 1) - 1));
    return [Math.max(0, page - 1), Math.min(pdf.numPages, page + 3)];
  });
  const [jump, setJump] = useState(String(bookmark.current.page ?? 1));
  const restored = useRef(false);
  const currentLayout = useRef<{ tops: number[]; heights: number[] }>({ tops: [], heights: [] });
  const natural = sizes[1] ?? [595, 842];
  const heights = Array.from({ length: pdf.numPages }, (_, i) => {
    const [w, h] = sizes[i + 1] ?? natural;
    return (width || 600) * h / w + 44;
  });
  const tops = [0]; for (const height of heights) tops.push(tops[tops.length - 1] + height);
  currentLayout.current = { tops, heights };
  useLayoutEffect(() => {
    const node = flow.current;
    if (!node) return;
    const measure = () => { const value = Math.floor(node.clientWidth); if (value > 0) setWidth(Math.min(760, value)); };
    measure();
    const observer = new ResizeObserver(measure); observer.observe(node);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const scroll = scrollContainer(root.current);
    const node = flow.current;
    if (!scroll || !node) return;
    let frame = 0;
    const update = () => {
      const { tops: positions, heights: spans } = currentLayout.current;
      const y = Math.max(0, scroll.getBoundingClientRect().top - node.getBoundingClientRect().top);
      let first = 0; while (first < pdf.numPages - 1 && positions[first + 1] <= y) first++;
      let last = first; while (last < pdf.numPages - 1 && positions[last + 1] < y + scroll.clientHeight) last++;
      setRange(old => old[0] === Math.max(0, first - 1) && old[1] === Math.min(pdf.numPages, last + 2) ? old : [Math.max(0, first - 1), Math.min(pdf.numPages, last + 2)]);
      if (restored.current) {
        save({ page: first + 1, pageOffset: Math.max(0, Math.min(1, (y - positions[first]) / spans[first])) });
        setJump(String(first + 1));
      }
    };
    const schedule = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(update); };
    scroll.addEventListener("scroll", schedule, { passive: true });
    const observer = new ResizeObserver(schedule); observer.observe(scroll);
    schedule();
    return () => { cancelAnimationFrame(frame); scroll.removeEventListener("scroll", schedule); observer.disconnect(); };
  }, [pdf.numPages, root, save]);
  useLayoutEffect(() => {
    const scroll = scrollContainer(root.current); const node = flow.current;
    if (!scroll || !node || !width) return;
    const { tops: positions, heights: spans } = currentLayout.current;
    const page = Math.max(0, Math.min(pdf.numPages - 1, (bookmark.current.page ?? 1) - 1));
    // Keep the same page and within-page position when a splitter resize or
    // newly measured mixed page size changes the virtual document's geometry.
    const offset = scroll.scrollTop + node.getBoundingClientRect().top - scroll.getBoundingClientRect().top;
    scroll.scrollTop = offset + positions[page] + (bookmark.current.pageOffset ?? 0) * spans[page];
    restored.current = true;
    scroll.dispatchEvent(new Event("scroll"));
  }, [width, sizes, pdf.numPages, bookmark, root]);
  const goToPage = () => {
    const page = Math.max(1, Math.min(pdf.numPages, Number(jump) || 1));
    const scroll = scrollContainer(root.current); const node = flow.current;
    if (!scroll || !node) return;
    scroll.scrollTop += node.getBoundingClientRect().top - scroll.getBoundingClientRect().top + currentLayout.current.tops[page - 1];
    save({ page, pageOffset: 0 });
    scroll.dispatchEvent(new Event("scroll"));
  };
  const pageNodes: ReactNode[] = [];
  for (let i = range[0]; width > 0 && i < range[1]; i++) {
    const size = sizes[i + 1] ?? natural;
    pageNodes.push(<div className="document-preview-page" key={i} style={{ height: heights[i] }} data-pdf-page={i + 1}>
      <div className="document-preview-page-label">第 {i + 1} 页</div>
      <PdfCanvas pdf={pdf} filename={filename} pageNumber={i + 1} scale={width / size[0]} onSize={(w, h) => {
        setSizes(old => old[i + 1]?.[0] === w && old[i + 1]?.[1] === h ? old : { ...old, [i + 1]: [w, h] });
      }} />
    </div>);
  }
  return <div className="document-pdf-window">
    <form className="document-preview-page-jump" onSubmit={event => { event.preventDefault(); goToPage(); }}>
      <label>页码 <input aria-label="原件页码" type="number" min={1} max={pdf.numPages} value={jump} onChange={event => setJump(event.target.value)} /></label>
      <span className="small muted">/ {pdf.numPages}</span><button className="btn secondary sm" type="submit">前往</button>
    </form>
    <div className="document-preview-flow virtual-pdf-flow" ref={flow}>
      <div aria-hidden="true" style={{ height: tops[range[0]], flexShrink: 0 }} />
      {pageNodes}
      <div aria-hidden="true" style={{ height: tops[pdf.numPages] - tops[range[1]], flexShrink: 0 }} />
    </div>
  </div>;
}
function EvidenceHighlight({ region }: { region: EvidenceRegion }) {
  return <div aria-label="字段在原文件中的位置" className="evidence-highlight" role="mark" style={{
    left: `${region.x * 100}%`, top: `${region.y * 100}%`, width: `${region.width * 100}%`, height: `${region.height * 100}%`,
  }} />;
}
