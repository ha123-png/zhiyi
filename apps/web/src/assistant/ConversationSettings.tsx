import { useState, useEffect, useRef, useId } from "react";
import * as Popover from "@radix-ui/react-popover";
import {
  Settings2,
  Check,
  X,
  Database,
  FileText,
  LayoutTemplate,
} from "lucide-react";
import { useAssistant } from "./AssistantProvider";
import { assistantApi } from "./api";
import type { BusinessContext } from "./types";
import { Fold } from "./Interaction";

type Resource = {
  id: string;
  name: string;
  kind: "table" | "task" | "template";
};
export function scopeText(context: BusinessContext) {
  if (context.workspace) return "全部资料 · 按需查找";
  const tables = new Set([
    ...(context.table_ids || []),
    ...(context.table_id ? [context.table_id] : []),
  ]).size;
  const tasks = context.task_ids?.length || (context.task_id ? 1 : 0);
  const templates =
    context.template_ids?.length || (context.template_id ? 1 : 0);
  return (
    [
      context.table_id
        ? `${context.table_name || "当前表"}${tables > 1 ? `及另外 ${tables - 1} 张表` : ""}`
        : tables
          ? `${tables} 张表`
          : "",
      context.row_ids != null ? `${context.row_ids.length} 条选中记录` : "",
      context.view_id ? "当前分组" : "",
      context.search ? `搜索：${context.search}` : "",
      tasks ? `${tasks} 份文件` : "",
      templates ? `${templates} 个模板` : "",
    ]
      .filter(Boolean)
      .join(" · ") || "不使用业务资料"
  );
}

export function ConversationSettings() {
  const ask = useAssistant();
  const [resources, setResources] = useState<Resource[]>([]);
  const [search, setSearch] = useState("");
  const [kind, setKind] = useState<Resource["kind"]>("table");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const resourceId = useId();
  const requestId = useRef(0);
  const [known, setKnown] = useState<Record<string, string>>({});
  const ctx = ask.context;
  const selectionKey = JSON.stringify([
    ctx.table_id, ctx.task_id, ctx.template_id,
    ctx.table_ids, ctx.task_ids, ctx.template_ids,
  ]);
  useEffect(() => {
    if (!open) return;
    let alive = true;
    void Promise.all(
      (["table", "task", "template"] as const).map(async (type) => {
        const ids = [...new Set([
          ...(ctx[`${type}_ids`] || []),
          ...(ctx[`${type}_id`] ? [ctx[`${type}_id`]!] : []),
        ])];
        return ids.length
          ? assistantApi<Resource[]>(
              `/resources?kind=${type}&${ids.map((id) => `ids=${encodeURIComponent(id)}`).join("&")}`,
            )
          : [];
      }),
    ).then((groups) => {
      if (alive) setKnown((previous) => ({
        ...previous,
        ...Object.fromEntries(groups.flat().map((item) => [item.kind + ":" + item.id, item.name])),
      }));
    }).catch(() => {});
    return () => { alive = false; };
  }, [open, selectionKey]);
  const hasPage = !!(
    ask.pageContext.table_id ||
    ask.pageContext.task_id ||
    ask.pageContext.template_id
  );
  const caps = (ctx.capabilities ?? ["data", "tasks", "organize"]).filter(value => value !== "templates");
  useEffect(() => {
    if (!open) return;
    const id = ++requestId.current;
    setLoading(true);
    const timer = window.setTimeout(
      () => {
        void assistantApi<Resource[]>(
          `/resources?kind=${kind}&search=${encodeURIComponent(search)}`,
        )
          .then((items) => {
            if (id !== requestId.current) return;
            setResources(items);
            setKnown((previous) => ({
              ...previous,
              ...Object.fromEntries(
                items.map((item) => [item.kind + ":" + item.id, item.name]),
              ),
            }));
            setError("");
          })
          .catch((e) => {
            if (id === requestId.current) setError(e.message);
          })
          .finally(() => {
            if (id === requestId.current) setLoading(false);
          });
      },
      search ? 180 : 0,
    );
    return () => {
      window.clearTimeout(timer);
      requestId.current++;
    };
  }, [open, kind, search]);
  const selected = (["table", "task", "template"] as const).flatMap((type) =>
    [
      ...new Set([
        ...(ctx[`${type}_ids`] || []),
        ...(ctx[`${type}_id`] ? [ctx[`${type}_id`]!] : []),
      ]),
    ].map((id) => ({
      id,
      kind: type,
      name:
        known[type + ":" + id] ||
        (type === "table" && ctx.table_name) ||
        { table: "当前数据表", task: "当前文件", template: "当前模板" }[type],
    })),
  );
  function remove(item: Resource) {
    const next = {
      ...ctx,
      [`${item.kind}_ids`]: (ctx[`${item.kind}_ids`] || []).filter(
        (id) => id !== item.id,
      ),
    };
    if (ctx[`${item.kind}_id`] === item.id) {
      delete next[`${item.kind}_id`];
      if (item.kind === "table") {
        delete next.table_name;
        delete next.row_ids;
        delete next.view_id;
        delete next.search;
      }
    }
    ask.updateContext(next);
  }
  function select(item: Resource) {
    const key = `${item.kind}_ids` as "table_ids" | "task_ids" | "template_ids";
    const ids = ctx[key] ?? [];
    const base =
      item.kind === "table" && item.id === ctx.table_id
        ? {
            ...ctx,
            table_id: undefined,
            table_name: undefined,
            row_ids: undefined,
            search: undefined,
            view_id: undefined,
            task_id: undefined,
          }
        : ctx;
    ask.updateContext({
      ...base,
      workspace: false,
      [key]: ids.includes(item.id)
        ? ids.filter((v) => v !== item.id)
        : [...ids, item.id],
    });
  }
  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <button
          className="ask-settings-trigger"
          aria-label="对话设置"
          title={`${ask.profile?.name || "选择模型"} · ${scopeText(ctx)}`}
        >
          <span>对话设置</span>
          <small>{ask.profile?.name || "选择模型"}</small>
          <Settings2 size={15} />
        </button>
      </Popover.Trigger>
      <Popover.Content
        className="ask-settings-popover"
        sideOffset={10}
        align="end"
        collisionPadding={14}
        aria-label="对话设置面板"
        onEscapeKeyDown={(e) => e.stopPropagation()}
      >
        <header>
          <strong>对话设置</strong>
          <Popover.Close aria-label="关闭对话设置">
            <X size={16} />
          </Popover.Close>
        </header>
        <section className="ask-setting-section" aria-label="聊天模型设置">
        <label className="ask-setting-label">
          聊天模型 <span>独立于文件提取</span>
        </label>
        <div className="ask-model-row">
          <select
            aria-label="问知意模型方案"
            value={ask.profileId}
            disabled={ask.busy}
            onChange={(e) => ask.selectProfile(e.target.value)}
            onFocus={() =>
              void ask.loadModels().catch((e) => ask.setError(e.message))
            }
          >
            <option value="">选择问知意模型方案</option>
            {ask.profiles.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} · {p.is_remote ? "远程" : "本地"} · {p.model_name}
              </option>
            ))}
          </select>
          <button
            disabled={!ask.profile || ask.busy}
            onClick={() => void ask.saveDefault()}
            title="只设置问知意默认方案"
          >
            设为默认
          </button>
        </div>
        {ask.profile?.is_remote && <p className="ask-consent-setting">
          {ask.remoteConsent ? "已允许这段对话发送所选资料" : "发送前会确认云端资料范围"}
          {ask.remoteConsent && <button disabled={ask.busy} onClick={() => ask.setRemoteConsent(false)}>撤销授权</button>}
        </p>}
        </section>
        <section className="ask-setting-section" aria-label="资料范围设置">
        <label className="ask-setting-label">
          允许这段对话使用哪些资料 <span>勾选后按需读取</span>
        </label>
        <div className="ask-scope-actions">
          <button
            disabled={ask.busy}
            onClick={() =>
              ask.updateContext({
                mode: ctx.mode,
                capabilities: ctx.capabilities,
              })
            }
          >
            清除范围
          </button>
          {hasPage && (
            <button
              disabled={ask.busy}
              onClick={() =>
                ask.updateContext({
                  ...ask.pageContext,
                  mode: ctx.mode,
                  capabilities: ctx.capabilities,
                })
              }
            >
              附加当前页面
            </button>
          )}
          <label>
            <input
              type="checkbox"
              aria-label="允许查找知意资料"
              checked={!!ctx.workspace}
              disabled={ask.busy}
              onChange={(e) =>
                ask.updateContext({
                  workspace: e.target.checked,
                  mode: ctx.mode,
                  capabilities: ctx.capabilities,
                })
              }
            />
            全部资料
          </label>
        </div>
        <p className="ask-scope-description">
          {ctx.workspace
            ? "允许按需查找全部资料"
            : selected.length
              ? ctx.row_ids != null
                ? `仅限当前表选中的 ${ctx.row_ids.length} 条记录${selected.filter(item => item.kind === "table").length > 1 ? "；其他已选表使用全部记录" : ""}`
                : ctx.view_id || ctx.search
                  ? scopeText(ctx)
                  : "已选资料可用于这段对话；数据表默认使用全部记录"
              : "尚未选择资料，模型只能与你聊天"}
        </p>
        {!ctx.workspace && !!selected.length && (
          <div className="ask-selected-resources">
            {selected.map((item) => (
              <button
                key={item.kind + item.id}
                disabled={ask.busy}
                title={`移除${item.name}`}
                onClick={() => remove(item)}
              >
                <span>{item.name}</span>
                <X size={12} />
              </button>
            ))}
          </div>
        )}
        {!ctx.workspace && (
          <div className="ask-resource-picker">
            <div
              className="ask-resource-tabs"
              role="tablist"
              aria-label="资料类型"
            >
              {(
                [
                  ["table", "数据表"],
                  ["task", "文件"],
                  ["template", "模板"],
                ] as const
              ).map(([value, label]) => (
                <button
                  key={value}
                  role="tab"
                  id={`${resourceId}-${value}`}
                  aria-selected={kind === value}
                  aria-controls={`${resourceId}-panel`}
                  tabIndex={kind === value ? 0 : -1}
                  onKeyDown={(event) => {
                    const order = ["table", "task", "template"] as const;
                    const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
                    if (!step && event.key !== "Home" && event.key !== "End") return;
                    event.preventDefault();
                    const next = event.key === "Home" ? order[0] : event.key === "End" ? order[2]
                      : order[(order.indexOf(kind) + step + order.length) % order.length];
                    setKind(next);
                    setSearch("");
                    setResources([]);
                    document.getElementById(`${resourceId}-${next}`)?.focus();
                  }}
                  onClick={() => {
                    setKind(value);
                    setSearch("");
                    setResources([]);
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
            <div role="tabpanel" id={`${resourceId}-panel`} aria-labelledby={`${resourceId}-${kind}`}>
            <input
              className="ask-resource-search"
              aria-label="搜索可选资料"
              placeholder={`筛选${{ table: "数据表", task: "文件", template: "模板" }[kind]}名称…`}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <div className="ask-resource-list">
              {resources
                .filter((r) => r.kind === kind)
                .map((r) => (
                  <label key={r.id}>
                    <input
                      type="checkbox"
                      disabled={ask.busy}
                      checked={
                        (ctx[`${r.kind}_ids`] ?? []).includes(r.id) ||
                        ctx[`${r.kind}_id`] === r.id
                      }
                      onChange={(e) =>
                        e.target.checked ? select(r) : remove(r)
                      }
                    />
                    {r.kind === "table" ? (
                      <Database size={13} />
                    ) : r.kind === "task" ? (
                      <FileText size={13} />
                    ) : (
                      <LayoutTemplate size={13} />
                    )}
                    <span>{r.name}</span>
                  </label>
                ))}
              {resources.length === 100 && (
                <p>当前显示前 100 项，可输入名称缩小列表。</p>
              )}
              {loading && <p>正在读取列表…</p>}
              {!resources.length && !loading && !error && (
                <p>
                  {search
                    ? "没有匹配的资料，试试其他名称。"
                    : "还没有这类资料。"}
                </p>
              )}
            </div>
            {error && <p role="alert">{error}</p>}
            </div>
          </div>
        )}
        </section>
        <section className="ask-setting-section" aria-label="操作权限设置">
        <label className="ask-setting-label">操作方式</label>
        <div className="ask-mode-options">
          {(
            [
              ["read", "只读分析"],
              ["assist", "协助操作"],
              ["delegate", "本次委托"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              aria-pressed={(ctx.mode || "assist") === value}
              disabled={ask.busy}
              onClick={() => ask.updateContext({ ...ctx, mode: value })}
            >
              {label}
            </button>
          ))}
        </div>
        <p className="ask-scope-description">
          {ctx.mode === "read"
            ? "查询与分析，不生成业务修改。"
            : ctx.mode === "delegate"
              ? "仅下一次发送：自动执行授权内的数据修改和版本恢复。新增、删除、任务与表结构操作仍需确认。"
              : "先展示修改预览，确认后执行。"}
        </p>
        {ctx.mode !== "read" && (
          <Fold title="允许的操作能力">
            <div className="ask-capabilities">
              {(
                [
                  ["data", "数据编辑"],
                  ["tasks", "任务控制"],
                  ["organize", "表结构与分组"],
                ] as const
              ).map(([key, label]) => (
                <label key={key}>
                  <input
                    type="checkbox"
                    checked={caps.includes(key)}
                    disabled={ask.busy}
                    onChange={(e) =>
                      ask.updateContext({
                        ...ctx,
                        capabilities: e.target.checked
                          ? [...caps, key]
                          : caps.filter((v) => v !== key),
                      })
                    }
                  />
                  {label}
                </label>
              ))}
            </div>
          </Fold>
        )}
        </section>
        <Popover.Close className="ask-settings-done">
          <Check size={14} />
          完成
        </Popover.Close>
      </Popover.Content>
    </Popover.Root>
  );
}
