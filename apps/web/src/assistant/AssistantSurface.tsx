import { containsTemplateWrite, OperationPreview } from "./OperationPreview";
import { assistantBase } from "./api";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { MoreHorizontal, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { Fold, usePanelWidth, useConversationScroll } from "./Interaction";
import { ConversationSettings, scopeText } from "./ConversationSettings";
import {
  lazy,
  Suspense,
  useEffect,
  useRef,
  useState,
  type ComponentProps,
  type ReactNode,
} from "react";
import {
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useAuiState,
} from "@assistant-ui/react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowUp,
  Square,
  X,
  Maximize2,
  Plus,
  MessageCircle,
  Cloud,
  Monitor,
  Copy,
  ArrowUpRight,
  Search,
  Archive,
  Pencil,
  Trash2,
  Check,
  Paperclip,
} from "lucide-react";
import { ConfirmDialog } from "../components/ConfirmDialog";
const LazyAnalysisCard = lazy(() =>
  import("./AnalysisCard").then((module) => ({ default: module.AnalysisCard })),
);
function AnalysisCard(props: ComponentProps<typeof LazyAnalysisCard>) {
  return (
    <Suspense fallback={<p className="ask-status">正在准备图表…</p>}>
      <LazyAnalysisCard {...props} />
    </Suspense>
  );
}
import { navigateAssistant, useAssistant } from "./AssistantProvider";
import type { Analysis, BusinessContext, ToolRecord, Thread } from "./types";

const toolNames: Record<string, string> = {
  get_operation_tools: "准备业务操作",
  enable_analysis_options: "准备条件分析",
  read_tool_result: "读取保存结果",
  propose_operations: "操作预览",
  propose_undo: "撤销预览",
  read_analysis_snapshot: "读取分析快照",
  read_conversation_history: "回读历史",
  read_original_page: "读取原件页",
  prepare_export: "导出快照",
  catalog: "查找资料",
  read_data_rows: "读取数据行",
  read_resource: "读取资料",
  analyze_data_table: "分析数据",
  render_chart: "生成图表",
  propose_changes: "修改预览",
  propose_task_control: "任务操作预览",
  draft_template: "历史模板提案",
};
const statusNames: Record<string, string> = {
  pending: "等待确认",
  completed: "已完成",
  failed: "未完成",
  approved: "已执行",
  rejected: "已取消",
  expired: "已失效",
  undone: "已撤销",
  stale: "已过期或冲突",
  superseded: "已被新请求替代",
};
function ToolCard({
  tool,
  compact = false,
}: {
  tool: ToolRecord;
  compact?: boolean;
}) {
  const important =
    tool.result.error || ["failed", "stale", "superseded"].includes(tool.status) ||
    tool.result.chart ||
    tool.result.draft ||
    tool.result.kind === "operation_plan" ||
    tool.status === "pending" ||
    (tool.result.analysis_id && !compact) ||
    tool.result.kind === "export";
  if (important) return <ToolCardContent tool={tool} />;
  return (
    <Fold
      className="ask-tool-card"
      title={
        <>
          {toolNames[tool.name] || tool.name} ·{" "}
          {statusNames[tool.status] || tool.status}
        </>
      }
    >
      <ToolCardContent tool={tool} nested />
    </Fold>
  );
}
function ToolCardContent({
  tool,
  nested = false,
}: {
  nested?: boolean;
  tool: ToolRecord;
}) {
  const ask = useAssistant();
  const [deciding, setDeciding] = useState(false);
  const result = tool.result;
  const templateWrite = containsTemplateWrite(result);
  if (result.chart && result.analysis)
    return (
      <AnalysisCard
        spec={result.chart}
        analysis={result.analysis}
        navigate={navigateAssistant}
        refresh={() =>
          ask.runtime.thread.composer.setText(
            `请按最新数据重新分析，沿用此统计口径：${JSON.stringify(result.analysis!.source.request)}`,
          )
        }
      />
    );
  if (result.source && (result.analysis_id || result.rows)) {
    const card = (
      <AnalysisCard
        analysis={result as Analysis}
        navigate={navigateAssistant}
        refresh={() =>
          ask.runtime.thread.composer.setText(
            `请按最新数据重新分析，沿用此统计口径：${JSON.stringify(result.source!.request)}`,
          )
        }
      />
    );
    // The process group already supplies the disclosure. Keep its single
    // analysis readable after that one click instead of nesting a second gate.
    return card;
  }
  async function decide(approve: boolean) {
    setDeciding(true);
    try {
      await ask.decision(tool.id, approve);
    } finally {
      setDeciding(false);
    }
  }
  return (
    <section className={`ask-tool ${result.kind === "operation_plan" || result.affected ? "ask-operation-card" : ""} ${result.error ? "ask-tool-error" : ""}`}>
      {!nested && (
        <header>
          <strong>{toolNames[tool.name] || tool.name}</strong>
          <span>{statusNames[tool.status] || tool.status}</span>
        </header>
      )}
      {result.error && <p role="alert">{result.error}</p>}
      {tool.status === "stale" && !result.error && <p className="ask-plan-note">数据已变化或预览已过期。请重新提出修改，核对新的预览。</p>}
      {tool.status === "superseded" && <p className="ask-plan-note">后续请求已替代此提案，此处保留历史变更记录。</p>}
      {result.kind === "operation_plan" && <OperationPreview result={result} />}
      {result.kind === "export" && (
        <a
          className="ask-export-link"
          href={`${assistantBase}/tools/${encodeURIComponent(tool.id)}/export`}
          download="zhiyi-analysis.xlsx"
        >
          下载 Excel · {String(result.title)}
        </a>
      )}
      {tool.name === "read_original_page" && !result.error && (
        <p>
          原件第 {String(result.page)} 页
          {result.vision ? " · 已准备本页图像供所选模型读取" : " · 读取文字层"}
        </p>
      )}
      {tool.status === "approved" && !templateWrite &&
        (result.execution as { can_undo?: boolean } | undefined)?.can_undo && (
          <button
            disabled={ask.busy || deciding}
            onClick={async () => {
              setDeciding(true);
              try {
                await ask.undo(tool.id);
              } finally {
                setDeciding(false);
              }
            }}
          >
            撤销本次修改
          </button>
        )}
      {result.note &&
        ![
          "get_operation_tools",
          "read_conversation_history",
          "read_tool_result",
        ].includes(tool.name) && <p>{result.note}</p>}
      {tool.name === "get_operation_tools" && (
        <p>已准备所需操作能力，接下来生成具体方案。</p>
      )}
      {tool.name === "read_conversation_history" && !result.error && (
        <div className="ask-recalled-text">
          <small>{result.role === "user" ? "你之前说" : "知意之前回答"}</small>
          <p>{String(result.text || "没有更多相关历史。")}</p>
        </div>
      )}
      {tool.name === "read_tool_result" && !result.error && (
        <Fold title="已读取的资料" className="ask-recalled-text"><p>{result.text ? String(result.text) : result.data !== undefined ? JSON.stringify(result.data, null, 2) : "没有更多结果。"}</p></Fold>
      )}
      {tool.name === "catalog" && (
        <div className="ask-catalog-list">
          {Array.isArray(result.items) && result.items.length ? (
            (result.items as { id: string; name: string }[]).map((item) => (
              <button
                className="ask-resource-link"
                key={item.id}
                onClick={() =>
                  navigateAssistant({
                    kind: String(result.kind || "tables").replace(/s$/, ""),
                    id: item.id,
                  })
                }
              >
                {item.name}
                <ArrowUpRight size={13} />
              </button>
            ))
          ) : (
            <p>
              当前没有可用的授权资料。请在“对话设置”中勾选数据表、文件或模板。
            </p>
          )}
        </div>
      )}
      {result.reference && (
        <button
          className="ask-resource-link"
          type="button"
          onClick={() => navigateAssistant(result.reference!)}
        >
          {result.reference.label || "查看资料"}
          <ArrowUpRight size={13} />
        </button>
      )}
      {result.original && (
        <button
          type="button"
          onClick={() => navigateAssistant(result.original!)}
        >
          查看原件
          <ArrowUpRight size={13} />
        </button>
      )}
      {result.affected && (
        <>
          <p>
            {tool.status === "approved" ? "已修改" : tool.status === "pending" ? "将修改" : "此预览涉及"}「{String(result.table_name)}」中的 {result.affected.length}{" "}
            条记录。公共字段会同步到下列相关明细。
          </p>
          <div className="ask-data-scroll">
            <table>
              <thead>
                <tr>
                  <th>记录</th>
                  <th>字段</th>
                  <th>修改前</th>
                  <th>修改后</th>
                </tr>
              </thead>
              <tbody>
                {result.affected.flatMap((row) =>
                  Object.keys(row.after).map((key) => (
                    <tr key={`${row.row_id}-${key}`}>
                      <td>#{row.row_id}</td>
                      <td>
                        {String(
                          (
                            result.columns as unknown as Record<string, string>
                          )?.[key] || key,
                        )}
                      </td>
                      <td>{String(row.before[key] ?? "—")}</td>
                      <td>{String(row.after[key] ?? "—")}</td>
                    </tr>
                  )),
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
      {tool.name === "propose_task_control" && !result.error && (
        <p>
          {
            (
              {
                pause: "暂停",
                resume: "恢复",
                retry: "重试",
                cancel: "取消",
              } as Record<string, string>
            )[String(result.action)]
          }
          ：{String(result.filename)}
          <br />
          当前状态：{String(result.expected_status)}
        </p>
      )}
      {result.draft && <p>历史模板提案「{String(result.draft.name || "未命名模板")}」。请在模板页查看或编辑模板。</p>}
      {tool.status === "pending" && !templateWrite && (
        <div className="ask-approval">
          <button
            type="button"
            disabled={deciding || ask.busy}
            onClick={() => void decide(false)}
          >
            取消
          </button>
          <button
            type="button"
            className="ask-primary"
            disabled={deciding || ask.busy}
            onClick={() => void decide(true)}
          >
            <Check size={13} />
            {deciding ? "正在处理…" : "确认执行"}
          </button>
        </div>
      )}
      {!templateWrite && !result.affected &&
        result.kind !== "operation_plan" &&
        ![
          "catalog",
          "enable_analysis_options",
          "get_operation_tools",
          "read_conversation_history",
          "read_tool_result",
        ].includes(tool.name) && (
          <Fold title="高级详情 · 原始结果">
            <pre>{JSON.stringify(result, null, 2)}</pre>
          </Fold>
        )}
    </section>
  );
}
function ResultNarrative({ folded, children }: { folded: boolean; children: ReactNode }) {
  return folded ? <Fold title="文字说明" className="ask-result-narrative">{children}</Fold> : <>{children}</>;
}
function MessageView() {
  const message = useAuiState((s) => s.message);
  const ask = useAssistant();
  const [copied, setCopied] = useState(false);
  const text = message.content
    .filter((p) => p.type === "text")
    .map((p) => p.text)
    .join("\n");
  const context = message.metadata.custom?.context as
    | BusinessContext
    | undefined;
  const results = message.content.flatMap(part => part.type === "tool-call" ? [(part.result as ToolRecord)?.result] : []).filter(Boolean);
  const sameScope = (first: Analysis["source"], second: Analysis["source"]) => first.table_id === second.table_id &&
    ["row_ids", "filters", "search", "task_id", "grain"].every(key => JSON.stringify(first.request[key] ?? null) === JSON.stringify(second.request[key] ?? null));
  const intermediate = message.content.filter(part => {
    if (part.type !== "tool-call") return false;
    const t = part.result as ToolRecord;
    const r = t?.result;
    return r && !r.chart && !r.draft && !r.error && t.status === "completed"
      && r.kind !== "export" && r.kind !== "operation_plan"
      && (!r.analysis_id || message.content.some(other => other.type === "tool-call"
        && (other.result as ToolRecord)?.result?.chart?.analysis_id === r.analysis_id)
        || (r.rows && r.source && results.some(other => {
          const final = other.analysis || other;
          return final !== r && final.source && !final.rows && (other.chart || final.data) && sameScope(r.source!, final.source);
        })));
  });
  const hasResultCard = message.content.some(part => part.type === "tool-call" && !intermediate.includes(part) &&
    ((part.result as ToolRecord)?.result?.chart || (part.result as ToolRecord)?.result?.analysis_id));
  return (
    <MessagePrimitive.Root
      className={`ask-message ask-message-${message.role}`}
    >
      <div className="ask-message-label">
        {message.role === "user" ? "你" : "问知意"}
      </div>
      {context &&
        message.role === "user" &&
        scopeText(context) !== "不使用业务资料" && (
          <small className="ask-message-scope">{scopeText(context)}</small>
        )}
      <div className="ask-message-body">
        {!!intermediate.length && <Fold title={intermediate.length === 1 && intermediate[0].type === "tool-call"
          ? `${toolNames[(intermediate[0].result as ToolRecord).name] || "处理过程"} · 已完成`
          : `已查阅资料与处理过程 · ${intermediate.length} 步`} className="ask-process">
          {intermediate.map(part => part.type === "tool-call" ? <ToolCardContent key={part.toolCallId} tool={part.result as ToolRecord} nested={intermediate.length === 1} /> : null)}
        </Fold>}
        {message.content.map((part, index) =>
          intermediate.includes(part) ? null : part.type === "text" ? (
            <ResultNarrative key={index} folded={hasResultCard && !(ask.busy && ask.detail?.messages.at(-1)?.id === message.id) &&
              (part.text.length > 240 || /(?:^|\n)\s*(?:\d+[.)]|[-*+])\s+/.test(part.text))}>
            <ReactMarkdown
              key={index}
              remarkPlugins={[remarkGfm]}
              components={{
                table: ({ children }) => {
                  const table = <div className="ask-data-scroll" tabIndex={0} aria-label="回答中的表格，可横向滚动">
                    <table>{children}</table>
                  </div>;
                  return hasResultCard ? <Fold title="补充表格">{table}</Fold> : table;
                },
                img: ({ alt }) => (
                  <span>[图片：{alt || "未加载外部图片"}]</span>
                ),
                a: ({ children, href }) => (
                  <a
                    href={
                      href?.startsWith("https://") ||
                      href?.startsWith("http://")
                        ? href
                        : undefined
                    }
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {children}
                  </a>
                ),
              }}
            >
              {part.text}
            </ReactMarkdown>
            </ResultNarrative>
          ) : part.type === "tool-call" ? (
            <ToolCard
              key={part.toolCallId}
              tool={part.result as ToolRecord}
              compact={message.content.some(
                (other) =>
                  other.type === "tool-call" &&
                  (other.result as ToolRecord)?.result?.chart?.analysis_id ===
                    (part.result as ToolRecord)?.result?.analysis_id,
              )}
            />
          ) : null,
        )}
      </div>
      {text && (
        <div className="ask-message-actions">
          <button
            type="button"
            title="复制文本"
            onClick={() => {
              void navigator.clipboard
                .writeText(text)
                .then(() => setCopied(true))
                .catch(() => ask.setError("复制失败，请选择文本手动复制。"));
            }}
          >
            <Copy size={12} />
            {copied ? "已复制" : "复制"}
          </button>
          {message.role === "user" && (
            <button
              type="button"
              onClick={() => ask.runtime.thread.composer.setText(text)}
            >
              <Pencil size={12} />
              修改后再问
            </button>
          )}
          {message.role === "assistant" && (
            <button
              type="button"
              disabled={ask.busy}
              onClick={() =>
                ask.runtime.thread.composer.setText(
                  "请基于相同的问题和授权范围重新回答，保留上一个回答供比较。",
                )
              }
            >
              重新回答
            </button>
          )}
        </div>
      )}
    </MessagePrimitive.Root>
  );
}
function RemoteConsent() {
  const ask = useAssistant();
  if (!ask.profile?.is_remote || ask.remoteConsent) return null;
  return (
    <label className="ask-remote-consent">
      <input
        type="checkbox"
        checked={ask.remoteConsent}
        disabled={ask.busy}
        onChange={(e) => ask.setRemoteConsent(e.target.checked)}
      />
      <span>
        允许这段对话向 <strong>{new URL(ask.profile.base_url).host}</strong>{" "}
        发送问题、历史及所选资料。相同范围无需重复确认，可在对话设置中撤销。
      </span>
    </label>
  );
}
function Conversation() {
  const ask = useAssistant();
  const scroll = useConversationScroll(ask.detail?.id, ask.busy);
  return (
    <ThreadPrimitive.Root className="ask-conversation">
      <div className="ask-conversation-toolbar">
        <button
          className="ask-scope-summary"
          title={`${scopeText(ask.context)} · 打开对话设置选择资料`}
          onClick={() =>
            document
              .querySelector<HTMLButtonElement>(".ask-settings-trigger")
              ?.click()
          }
        >
          {scopeText(ask.context)}
        </button>
        <ConversationSettings />
      </div>
      <ThreadPrimitive.Viewport
        className="ask-viewport"
        turnAnchor="bottom"
        ref={scroll.viewport}
        {...scroll.events}
        autoScroll={false}
        scrollToBottomOnRunStart={false}
        scrollToBottomOnInitialize={false}
        scrollToBottomOnThreadSwitch={false}
      >
        <div ref={scroll.content} className="ask-scroll-content">
          {ask.detail?.has_more && <button type="button" className="ask-load-more" disabled={ask.loadingOlder} onClick={async () => {
            const viewport = scroll.viewport.current;
            const height = viewport?.scrollHeight || 0;
            const top = viewport?.scrollTop || 0;
            await ask.loadOlder();
            requestAnimationFrame(() => { if (viewport) viewport.scrollTop = top + viewport.scrollHeight - height; });
          }}>{ask.loadingOlder ? "正在读取…" : "查看更早的消息"}</button>}
          {!ask.detail?.messages.length && !ask.loading && (
            <div className="ask-empty">
              <span className="ask-emblem">
                <MessageCircle size={27} strokeWidth={1.4} />
              </span>
              <h2>把问题交给知意</h2>
              <p>
                聊一个想法，理解一份文件，
                <br />
                或从已有资料里找出答案。
              </p>
              <div className="ask-starters">
                {[
                  "模板是什么？怎样设计得更好用？",
                  "怎样核对提取结果和原件？",
            "帮我分析所选数据的变化趋势",
                ].map((text) => (
                  <button
                    type="button"
                    key={text}
                    onClick={() => ask.runtime.thread.composer.setText(text)}
                  >
                    {text}
                    <ArrowUpRight size={13} />
                  </button>
                ))}
              </div>
              <small>需要业务资料时，先附加当前页面或允许查找知意资料。</small>
            </div>
          )}
          <ThreadPrimitive.Messages components={{ Message: MessageView }} />
          {ask.busy && (
            <div className="ask-progress" role="status">
              <span />
              {ask.status || "正在回答…"}
            </div>
          )}
        </div>
      </ThreadPrimitive.Viewport>
      <div className="ask-bottom">
        {scroll.paused && (
          <button className="ask-jump-latest" onClick={scroll.resume}>
            回到最新
          </button>
        )}
        {ask.error && (
          <div className="ask-error" role="alert">
            {ask.error}
            <button
              type="button"
              aria-label="收起提示"
              onClick={() => ask.setError("")}
            >
              <X size={13} />
            </button>
          </div>
        )}
        {ask.status && !ask.busy && (
          <p className="ask-status" role="status">
            {ask.status}
          </p>
        )}
        {ask.detail?.archived ? (
          <p>
            这段对话已归档。
            <button
              type="button"
              onClick={() =>
                void ask.mutateThread(ask.detail!.id, "PATCH", {
                  archived: false,
                })
              }
            >
              恢复后继续
            </button>
          </p>
        ) : (
          <>
            <RemoteConsent />
            <ComposerPrimitive.Root className="ask-composer">
              <ComposerPrimitive.Input
                aria-label="向问知意提问"
                placeholder="问知意…"
                rows={1}
                maxLength={12000}
              />
              <div className="ask-composer-footer">
                <span>
                  {ask.profile
                    ? "Enter 发送 · Shift + Enter 换行"
                    : "先选择聊天模型，输入内容不会自动发送"}
                </span>
                {ask.busy ? (
                  <ComposerPrimitive.Cancel aria-label="停止回答">
                    <Square size={15} />
                  </ComposerPrimitive.Cancel>
                ) : (
                  <ComposerPrimitive.Send aria-label="发送问题">
                    <ArrowUp size={18} />
                  </ComposerPrimitive.Send>
                )}
              </div>
            </ComposerPrimitive.Root>
          </>
        )}
      </div>
    </ThreadPrimitive.Root>
  );
}
export function AssistantTrigger() {
  const ask = useAssistant();
  return (
    <button
      className="ask-trigger"
      aria-label="问知意"
      title="问知意"
      hidden={ask.drawerOpen}
      type="button"
      onClick={() => ask.setDrawerOpen(true)}
    >
      <MessageCircle size={23} strokeWidth={1.6} />
      {ask.busy && <span className="ask-live-dot" />}
    </button>
  );
}
export function AssistantDrawer() {
  const ask = useAssistant();
  const ref = useRef<HTMLDialogElement>(null);
  const panel = usePanelWidth("zhiyi-ask-drawer-width", 560, 380, 960, true);
  useEffect(() => {
    if (ask.drawerOpen) {
      ref.current?.showModal();
      ref.current?.querySelector<HTMLTextAreaElement>("textarea")?.focus();
    } else ref.current?.close();
  }, [ask.drawerOpen]);
  return (
    <dialog
      ref={ref}
      className="ask-drawer"
      style={{ width: panel.width }}
      aria-label="快捷问知意"
      onCancel={(event) => {
        if (event.target === event.currentTarget) ask.setDrawerOpen(false);
      }}
      onClose={(event) => {
        if (event.target === event.currentTarget) ask.setDrawerOpen(false);
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) ask.setDrawerOpen(false);
      }}
    >
      <div
        {...panel.separator}
        aria-label="调整快捷对话宽度"
        className="ask-resizer ask-drawer-resizer"
      />
      <div className="ask-drawer-inner">
        <header className="ask-heading">
          <div>
            <span className="ask-eyebrow">知意 · 随手问</span>
            <h2>问知意</h2>
          </div>
          <div>
            <button
              type="button"
              title="新对话"
              aria-label="新对话"
              onClick={() => {
                ask.newThread();
                ask.runtime.thread.composer.setText("");
              }}
            >
              <Plus size={17} />
            </button>
            <button
              type="button"
              title="展开到问知意页面"
              aria-label="展开到问知意页面"
              onClick={() => {
                ask.setDrawerOpen(false);
                navigateAssistant({ kind: "assistant", id: "" });
              }}
            >
              <Maximize2 size={16} />
            </button>
            <button
              type="button"
              aria-label="关闭快捷问知意"
              onClick={() => ask.setDrawerOpen(false)}
            >
              <X size={18} />
            </button>
          </div>
        </header>
        {ask.drawerOpen && <Conversation />}
      </div>
    </dialog>
  );
}
export function AssistantPage() {
  const ask = useAssistant();
  const [search, setSearch] = useState("");
  const [archived, setArchived] = useState(false);
  const panel = usePanelWidth("zhiyi-ask-history-width", 200, 150, 360);
  const [narrow, setNarrow] = useState(() => window.innerWidth < 900);
  const [collapsed, setCollapsed] = useState(narrow);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 899px)");
    const adjust = () => { setNarrow(media.matches); setCollapsed(media.matches); };
    media.addEventListener("change", adjust);
    return () => media.removeEventListener("change", adjust);
  }, []);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [found, setFound] = useState<Thread[] | null>(null);
  const [more, setMore] = useState(false);
  const [listing, setListing] = useState(false);
  const listVersion = useRef(0);
  useEffect(() => {
    const version = ++listVersion.current;
    setFound(null); setMore(false);
    const timer = setTimeout(() => {
      setListing(true);
      void ask.findThreads(search, archived).then(values => {
        if (version === listVersion.current) { setFound(values); setMore(values.length === 60); }
      }).catch(error => ask.setError(error.message)).finally(() => {
        if (version === listVersion.current) setListing(false);
      });
    }, search ? 180 : 0);
    return () => { clearTimeout(timer); listVersion.current++; };
  }, [search, archived, ask.threads, ask.findThreads]);
  const threads = found || ask.threads.filter(t => t.archived === archived && t.title.toLocaleLowerCase().includes(search.toLocaleLowerCase()));
  return (
    <div className={`ask-page ${collapsed ? "ask-history-collapsed" : ""}`}>
      <aside
        className="ask-history"
        style={{ width: panel.width }}
        hidden={collapsed}
      >
        <header>
          <h2>问知意</h2>
          <button
            type="button"
            aria-label="新对话"
            onClick={() => {
              ask.newThread();
              ask.runtime.thread.composer.setText("");
            }}
          >
            <Plus size={17} />
          </button>
          {narrow && <button type="button" aria-label="关闭对话列表" onClick={() => setCollapsed(true)}><X size={16} /></button>}
        </header>
        <label className="ask-search">
          <Search size={14} />
          <input
            aria-label="搜索对话"
            placeholder="搜索对话"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        <div className="ask-history-tabs">
          <button
            type="button"
            aria-pressed={!archived}
            onClick={() => setArchived(false)}
          >
            最近对话
          </button>
          <button
            type="button"
            aria-pressed={archived}
            onClick={() => setArchived(true)}
          >
            已归档
          </button>
        </div>
        <div className="ask-thread-list">
          {threads.map((t) => (
            <div
              className={`ask-thread-item ${ask.detail?.id === t.id ? "active" : ""}`}
              key={t.id}
            >
              {renaming === t.id ? (
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (title.trim())
                      void ask
                        .mutateThread(t.id, "PATCH", { title: title.trim() })
                        .then(() => setRenaming(null));
                  }}
                >
                  <input
                    aria-label="对话名称"
                    autoFocus
                    maxLength={160}
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Escape") setRenaming(null);
                    }}
                  />
                  <button type="submit" aria-label="保存对话名称">
                    <Check size={13} />
                  </button>
                </form>
              ) : (
                <button
                  type="button"
                  className="ask-thread-title"
                  title={t.title}
                  onClick={() => {
                    void ask.switchThread(t.id);
                    if (window.innerWidth < 900) setCollapsed(true);
                  }}
                >
                  <span className="ask-thread-title-text">{t.title}</span>
                  <small>
                    {new Date(t.updated_at).toLocaleDateString("zh-CN")}
                  </small>
                </button>
              )}
              <DropdownMenu.Root>
                <DropdownMenu.Trigger
                  className="ask-thread-more"
                  aria-label={`管理对话 ${t.title}`}
                >
                  <MoreHorizontal size={16} />
                </DropdownMenu.Trigger>
                <DropdownMenu.Portal>
                  <DropdownMenu.Content
                    className="ask-menu"
                    sideOffset={4}
                    align="end"
                  >
                    <DropdownMenu.Item asChild>
                      <button
                        type="button"
                        aria-label={`重命名${t.title}`}
                        onClick={() => {
                          setRenaming(t.id);
                          setTitle(t.title);
                        }}
                      >
                        <Pencil size={12} />
                        重命名
                      </button>
                    </DropdownMenu.Item>
                    <DropdownMenu.Item asChild>
                      <button
                        type="button"
                        aria-label={`${archived ? "恢复" : "归档"}${t.title}`}
                        onClick={() =>
                          void ask.mutateThread(t.id, "PATCH", {
                            archived: !archived,
                          })
                        }
                      >
                        <Archive size={12} />
                        {archived ? "恢复" : "归档"}
                      </button>
                    </DropdownMenu.Item>
                    <DropdownMenu.Item asChild>
                      <button
                        type="button"
                        aria-label={`删除${t.title}`}
                        onClick={() => setDeleting(t.id)}
                      >
                        <Trash2 size={12} />
                        删除
                      </button>
                    </DropdownMenu.Item>
                  </DropdownMenu.Content>
                </DropdownMenu.Portal>
              </DropdownMenu.Root>
            </div>
          ))}
          {more && <button type="button" className="ask-load-more" disabled={listing} onClick={async () => {
            const version = listVersion.current;
            setListing(true);
            try {
              const next = await ask.findThreads(search, archived, threads.length);
              if (version === listVersion.current) {
                setFound([...threads, ...next.filter(value => !threads.some(t => t.id === value.id))]);
                setMore(next.length === 60);
              }
            } catch (error) { ask.setError(error instanceof Error ? error.message : "读取对话失败"); }
            finally { if (version === listVersion.current) setListing(false); }
          }}>{listing ? "正在读取…" : "更多对话"}</button>}
          {!threads.length && (
            <p className="ask-history-empty">
              {search ? "没有匹配的对话" : archived ? "暂无归档对话。" : "发送第一条消息后，便会保存在这里。"}
            </p>
          )}
        </div>
        <p className="ask-local-note">对话保存在本机，随知意数据备份。</p>
      </aside>
      {!collapsed && (
        <div
          {...panel.separator}
          aria-label="调整对话列表宽度"
          className="ask-resizer"
        />
      )}
      <section className="ask-main">
        <header className="ask-heading">
          <button
            aria-label={collapsed ? "展开对话列表" : "收起对话列表"}
            onClick={() => setCollapsed(!collapsed)}
          >
            {collapsed ? (
              <PanelLeftOpen size={17} />
            ) : (
              <PanelLeftClose size={17} />
            )}
          </button>
          <div>
            <span className="ask-eyebrow"></span>
            <h1 title={ask.detail?.title}>{ask.detail?.title || "问知意"}</h1>
          </div>
          {collapsed && <button type="button" aria-label="新对话" title="新对话" onClick={() => {
            ask.newThread();
            ask.runtime.thread.composer.setText("");
          }}><Plus size={17} /></button>}
        </header>
        {!ask.drawerOpen && <Conversation />}
      </section>
      <ConfirmDialog
        open={!!deleting}
        title="删除这段对话？"
        description="会同时删除消息、工具记录和保存的分析快照。已确认的业务修改仍保留在数据仓库。"
        buttonLabel="删除对话"
        onClose={() => setDeleting(null)}
        onConfirm={() => {
          if (deleting)
            void ask
              .mutateThread(deleting, "DELETE")
              .then(() => setDeleting(null));
        }}
      />
    </div>
  );
}
