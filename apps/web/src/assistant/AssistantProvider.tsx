import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  AssistantRuntimeProvider,
  useExternalStoreRuntime,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import { getModelProfiles } from "../api";
import type { ModelProfile } from "../types";
import { assistantApi, assistantBase } from "./api";
import type { BusinessContext, Message, Reference, Thread, ThreadDetail, ToolRecord } from "./types";
import "./assistant.css";
import { consentKey } from "./consent";
import { applyDeltas, mergePage, type TextDelta } from "./stream";

const running = new Set(["running", "waiting", "cancelling"]);
const convertMessage = (message: ThreadMessageLike) => message;
function currentContext(context: BusinessContext): BusinessContext {
  return { ...context, ...(context.capabilities ? { capabilities: context.capabilities.filter(value => value !== "templates") } : {}) };
}
export function navigateAssistant(
  reference: Reference | { kind: "draft"; draft: Record<string, unknown> },
) {
  window.dispatchEvent(
    new CustomEvent("zhiyi:assistant-navigate", { detail: reference }),
  );
}
function useController() {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [detail, setDetail] = useState<ThreadDetail | null>(null);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [profiles, setProfiles] = useState<ModelProfile[]>([]);
  const [profileId, setProfileId] = useState("");
  const consentStorage = `zhiyi:remote-consent:${assistantBase}`;
  const [grants, setGrants] = useState<string[]>(() => {
    try {
      const saved: unknown = JSON.parse(sessionStorage.getItem(consentStorage) || "[]");
      return Array.isArray(saved) ? saved.filter((v): v is string => typeof v === "string").slice(-100) : [];
    }
    catch { return []; }
  });
  const draftThread = useRef(crypto.randomUUID());
  const [context, setContext] = useState<BusinessContext>({});
  const [pageContext, setPageContext] = useState<BusinessContext>({});
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const deltas = useRef(new Map<string, TextDelta[]>());
  const streamCleanup = useRef<(() => void) | null>(null);
  const activeId = useRef<string | null>(null);
  const submitting = useRef(false);
  const submission = useRef<{ signature: string; id: string } | null>(null);
  const observer = useRef<EventSource | null>(null);
  const refreshSequence = useRef(0);
  const announced = useRef(new Set<string>());
  const pageOwner = useRef("");
  const defaultId = useRef("");
  const profile = profiles.find((p) => p.id === profileId);
  const grantKey = (thread = activeId.current || draftThread.current) => profile ? consentKey(thread, profile, context) : "";
  const remoteConsent = grants.includes(grantKey());
  function rememberConsent(key: string, allowed: boolean) {
    setGrants(previous => {
      const next = [...previous.filter(v => v !== key), ...(allowed ? [key] : [])].slice(-100);
      try { sessionStorage.setItem(consentStorage, JSON.stringify(next)); } catch { /* Session memory remains available. */ }
      return next;
    });
  }
  function setRemoteConsent(allowed: boolean) { rememberConsent(grantKey(), allowed); }
  const updateContext = useCallback((next: BusinessContext) => {
    setContext(currentContext(next));
  }, []);
  const selectProfile = useCallback((id: string) => {
    setProfileId(id);
  }, []);
  const registerPage = useCallback(
    (owner: string, next: BusinessContext | null) => {
      if (next) {
        pageOwner.current = owner;
        setPageContext(next);
      } else if (pageOwner.current === owner) {
        pageOwner.current = "";
        setPageContext({});
      }
    },
    [],
  );
  const reloadThreads = useCallback(async () => {
    const [regular, archived] = await Promise.all([
      assistantApi<Thread[]>("/threads"),
      assistantApi<Thread[]>("/threads?archived=true"),
    ]);
    setThreads([...regular, ...archived]);
  }, []);
  const loadModels = useCallback(async () => {
    const [list, settings] = await Promise.all([
      getModelProfiles(),
      assistantApi<{ profile_id: string | null }>("/settings"),
    ]);
    setProfiles(list.filter((p) => !p.is_archived));
    defaultId.current = settings.profile_id || "";
    return settings.profile_id || "";
  }, []);
  useEffect(() => {
    let mounted = true;
    void loadModels()
      .then((id) => {
        if (mounted) setProfileId(id);
      })
      .catch((e) => {
        if (mounted) setError(e.message);
      });
    void reloadThreads().catch((e) => {
      if (mounted) setError(e.message);
    });
    return () => {
      mounted = false;
      observer.current?.close();
      streamCleanup.current?.();
    };
  }, [loadModels, reloadThreads]);
  const refresh = useCallback(async (id: string, runId?: string) => {
    const seq = ++refreshSequence.current;
    const next = await assistantApi<ThreadDetail>(
      `/threads/${encodeURIComponent(id)}${runId ? `?run_id=${encodeURIComponent(runId)}` : ""}`,
    );
    if (!next || !Array.isArray(next.messages) || !Array.isArray(next.runs) || !Array.isArray(next.tools))
      throw new Error("对话暂时无法读取，请重试。");
    if (activeId.current === id && seq === refreshSequence.current) {
      setDetail(previous => {
        let merged = next.partial && previous?.id === id ? mergePage(previous, next) : next;
        for (const run of next.runs) {
          const pending = running.has(run.status) ? (deltas.current.get(run.id) || []).filter(event => event.sequence > (run.stream_cursor || 0)) : [];
          deltas.current.set(run.id, pending);
          merged = applyDeltas(merged, run.id, pending);
        }
        return merged;
      });
      for (const tool of next.tools || []) {
        if (tool.status === "approved") {
          const result = tool.result.execution as
            | { results?: Record<string, unknown>[] }
            | undefined;
          const signature = tool.id + tool.status;
          if (!announced.current.has(signature)) {
            announced.current.add(signature);
            for (const item of result?.results || [])
              window.dispatchEvent(
                new CustomEvent("zhiyi:assistant-changed", { detail: item }),
              );
          }
        }
      }
      setBusy(next.runs.some((r) => running.has(r.status)));
      const latest = next.runs[0];
      if (latest?.error && latest.status !== "cancelled")
        setError(latest.error);
    }
    return next;
  }, []);
  const observe = useCallback(
    (id: string, runId: string) => {
      observer.current?.close();
      streamCleanup.current?.();
      const source = new EventSource(
        `${assistantBase}/runs/${encodeURIComponent(runId)}/events`,
      );
      observer.current = source;
      let failures = 0;
      let frame: ReturnType<typeof setTimeout> | undefined;
      let checkpoint: ReturnType<typeof setTimeout> | undefined;
      let received: TextDelta[] = [];
      const flush = () => {
        frame = undefined;
        const batch = received;
        received = [];
        setDetail(previous => previous?.id === id ? applyDeltas(previous, runId, batch) : previous);
      };
      const sync = () => {
        checkpoint = undefined;
        void refresh(id, runId).catch((e) => setError(e.message));
      };
      streamCleanup.current = () => { clearTimeout(frame); clearTimeout(checkpoint); deltas.current.clear(); };
      source.onopen = () => { setStatus("正在接收回答…"); };
      source.onmessage = (event) => {
        failures = 0;
        if (activeId.current !== id) {
          source.close();
          return;
        }
        const data = JSON.parse(event.data) as {
          type: string;
          message?: string;
          error?: string;
          status?: string;
          text?: string;
          part_index?: number;
          offset?: number;
        };
        if (data.message) setStatus(data.message);
        if (data.type === "text.delta" && typeof data.part_index === "number" && typeof data.offset === "number") {
          setStatus("正在回答…");
          const delta = { sequence: Number(event.lastEventId), part_index: data.part_index, offset: data.offset, text: data.text || "" };
          const pending = deltas.current.get(runId) || [];
          pending.push(delta);
          deltas.current.set(runId, pending);
          received.push(delta);
          if (!frame) frame = setTimeout(flush, 24);
        } else if (!checkpoint) {
          checkpoint = setTimeout(sync, 60);
        }
        if (data.type === "run.completed") {
          source.close();
          clearTimeout(frame); clearTimeout(checkpoint);
          flush(); sync();
          setStatus(
            data.status === "cancelled" ? "已停止，生成内容已保存" : "",
          );
          setBusy(false);
          void reloadThreads().catch((e) => setError(e.message));
        }
      };
      source.onerror = () => {
        if (observer.current !== source || activeId.current !== id) { source.close(); return; }
        failures += 1;
        setStatus("连接中断，正在核对已保存的回答…");
        void refresh(id, runId)
          .then((next) => {
            if (activeId.current !== id) return;
            const run = next.runs.find((r) => running.has(r.status));
            if (!run) { source.close(); setStatus(""); return; }
            if (failures >= 5) {
              source.close();
              setError(
                "实时连接中断，回答可能仍在后台生成。可重新打开这段对话连接，或点击停止。",
              );
              setStatus("");
            } else setStatus("连接短暂中断，正在自动重连…");
          })
          .catch((e) => {
            if (failures >= 5) { source.close(); setError(e.message); }
          });
      };
    },
    [refresh, reloadThreads],
  );
  const switchThread = useCallback(
    async (id: string) => {
      if (submitting.current) return;
      observer.current?.close();
      streamCleanup.current?.();
      activeId.current = id;
      setLoading(true);
      setError("");
      setStatus("");
      setDetail(null);
      try {
        const next = await refresh(id);
        if (activeId.current !== id) return;
        setProfileId(next.profile_id || defaultId.current);
        const restored = next.messages.at(-1)?.context ?? {};
        setContext({
          ...currentContext(restored),
          mode: restored.mode === "delegate" ? "assist" : restored.mode,
        });
        const run = next.runs.find((r) => running.has(r.status));
        if (run) observe(id, run.id);
      } catch (e) {
        setError(e instanceof Error ? e.message : "读取失败");
      } finally {
        setLoading(false);
      }
    },
    [refresh, observe],
  );
  const newThread = useCallback(() => {
    if (submitting.current) return;
    observer.current?.close();
    streamCleanup.current?.();
    activeId.current = null;
    setDetail(null);
    setBusy(false);
    setProfileId(defaultId.current);
    setContext({});
    draftThread.current = crypto.randomUUID();
    setError("");
    setStatus("");
  }, []);
  const findThreads = useCallback((search: string, archived: boolean, offset = 0) =>
    assistantApi<Thread[]>(`/threads?search=${encodeURIComponent(search)}&archived=${archived}&offset=${offset}&limit=60`), []);
  async function loadOlder() {
    if (!detail?.has_more || loadingOlder) return;
    const id = detail.id;
    setLoadingOlder(true);
    try {
      const next = await assistantApi<ThreadDetail>(`/threads/${encodeURIComponent(id)}?before=${detail.oldest_position}&limit=60`);
      if (activeId.current === id) setDetail(previous => previous?.id === id ? mergePage(previous, next, true) : previous);
    } catch (e) { setError(e instanceof Error ? e.message : "读取历史失败"); }
    finally { setLoadingOlder(false); }
  }
  async function send(text: string) {
    if (submitting.current || busy) return;
    if (!profile) {
      setError("请先选择问知意使用的模型方案。");
      throw new Error("尚未选择模型");
    }
    if (profile.is_remote && !remoteConsent) {
      setError("请先确认本次向远程模型发送的范围。");
      throw new Error("尚未同意远程发送");
    }
    submitting.current = true;
    setBusy(true);
    setError("");
    setStatus("正在连接模型…");
    const signature = JSON.stringify({
      text,
      thread: activeId.current,
      profile: profile.id,
      version: profile.version,
      context,
    });
    if (submission.current?.signature !== signature)
      submission.current = { signature, id: crypto.randomUUID() };
    try {
      const started = await assistantApi<{ thread_id: string; run_id: string }>(
        "/runs",
        "POST",
        {
          request_id: submission.current.id,
          text,
          thread_id: activeId.current,
          profile_id: profile.id,
          profile_version: profile.version,
          remote_consent: profile.is_remote
            ? `${profile.id}:${profile.version}`
            : null,
          context: currentContext(context),
        },
      );
      if (profile.is_remote && remoteConsent) rememberConsent(grantKey(started.thread_id), true);
      activeId.current = started.thread_id;
      submission.current = null;
      setContext((previous) =>
        previous.mode === "delegate"
          ? { ...previous, mode: "assist" }
          : previous,
      );
      await refresh(started.thread_id);
      observe(started.thread_id, started.run_id);
      await reloadThreads();
    } catch (e) {
      setBusy(false);
      setStatus("");
      setError(e instanceof Error ? e.message : "发送失败");
      throw e;
    } finally {
      submitting.current = false;
    }
  }
  async function cancel() {
    const run = detail?.runs.find((r) => running.has(r.status));
    if (!run) return;
    try {
      await assistantApi(`/runs/${run.id}/cancel`, "POST");
      setStatus("正在停止…");
      await refresh(detail!.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "停止失败");
    }
  }
  async function mutateThread(id: string, method: string, body?: unknown) {
    try {
      await assistantApi(`/threads/${encodeURIComponent(id)}`, method, body);
      await reloadThreads();
      if (activeId.current === id) {
        if (method === "DELETE") newThread();
        else await refresh(id);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    }
  }
  async function decision(id: string, approve: boolean) {
    try {
      const outcome = await assistantApi<{
        status: string;
        result?: {
          table_id?: string;
          task_id?: string;
          execution?: { results: Record<string, unknown>[] };
        };
      }>(`/tools/${encodeURIComponent(id)}/decision`, "POST", {
        approve,
      });
      if (outcome.status === "approved") {
        for (const item of outcome.result?.execution?.results || [])
          window.dispatchEvent(
            new CustomEvent("zhiyi:assistant-changed", { detail: item }),
          );
        window.dispatchEvent(
          new CustomEvent("zhiyi:assistant-changed", {
            detail: outcome.result,
          }),
        );
      }
      if (activeId.current) await refresh(activeId.current);
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
      return false;
    }
  }
  async function undo(id: string) {
    try {
      await assistantApi(`/tools/${encodeURIComponent(id)}/undo`, "POST");
      if (activeId.current) await refresh(activeId.current);
    } catch (e) {
      setError(e instanceof Error ? e.message : "撤销预览失败");
    }
  }
  async function saveDefault() {
    try {
      await assistantApi("/settings", "PUT", { profile_id: profileId || null });
      defaultId.current = profileId;
      setStatus("已设为问知意默认方案，提取方案未改变。");
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    }
  }
  const messageCache = useRef(new WeakMap<Message, { tools: (ToolRecord | undefined)[]; value: ThreadMessageLike }>());
  const messages = useMemo<ThreadMessageLike[]>(() => {
    const tools = new Map(detail?.tools.map(tool => [tool.id, tool]));
    return (detail?.messages ?? []).map(m => {
      const references = m.parts.filter(p => p.type === "tool").map(p => tools.get(p.id));
      const cached = messageCache.current.get(m);
      if (cached && references.every((value, index) => value === cached.tools[index])) return cached.value;
      const value: ThreadMessageLike = {
        id: m.id,
        role: m.role,
        createdAt: new Date(m.created_at),
        content: m.parts.map((p) =>
          p.type === "text"
            ? p
            : {
                type: "tool-call" as const,
                toolCallId: p.id,
                toolName: p.name,
                args: {},
                result: tools.get(p.id) ?? p,
              },
        ),
        metadata: { custom: { context: m.context } },
      };
      messageCache.current.set(m, { tools: references, value });
      return value;
    });
  }, [detail]);
  const runtime = useExternalStoreRuntime({
    messages,
    convertMessage,
    isRunning: busy,
    isLoading: loading,
    isDisabled: !!detail?.archived,
    isSendDisabled: !profile || (profile.is_remote && !remoteConsent),
    onNew: async (message) => {
      await send(
        message.content
          .filter((p) => p.type === "text")
          .map((p) => p.text)
          .join("\n"),
      );
    },
    onCancel: cancel,
    adapters: {
      threadList: {
        threadId: detail?.id,
        threads: threads
          .filter((t) => !t.archived)
          .map((t) => ({ ...t, status: "regular" as const })),
        archivedThreads: threads
          .filter((t) => t.archived)
          .map((t) => ({ ...t, status: "archived" as const })),
        onSwitchToNewThread: newThread,
        onSwitchToThread: switchThread,
        onRename: (id, title) => mutateThread(id, "PATCH", { title }),
        onArchive: (id) => mutateThread(id, "PATCH", { archived: true }),
        onUnarchive: (id) => mutateThread(id, "PATCH", { archived: false }),
        onDelete: (id) => mutateThread(id, "DELETE"),
      },
    },
  });
  return {
    runtime,
    drawerOpen,
    setDrawerOpen,
    detail,
    threads,
    profiles,
    profile,
    profileId,
    selectProfile,
    remoteConsent,
    setRemoteConsent,
    context,
    updateContext,
    pageContext,
    registerPage,
    error,
    setError,
    status,
    busy,
    loading,
    loadingOlder,
    loadOlder,
    findThreads,
    switchThread,
    newThread,
    mutateThread,
    decision,
    undo,
    saveDefault,
    loadModels,
    send,
    cancel,
  };
}
type Controller = ReturnType<typeof useController>;
const AssistantContext = createContext<Controller | null>(null);
export function AssistantProvider({ children }: { children: ReactNode }) {
  const controller = useController();
  return (
    <AssistantContext.Provider value={controller}>
      <AssistantRuntimeProvider runtime={controller.runtime}>
        {children}
      </AssistantRuntimeProvider>
    </AssistantContext.Provider>
  );
}
export function useAssistant() {
  const value = useContext(AssistantContext);
  if (!value) throw new Error("问知意缺少运行时");
  return value;
}
export function useAssistantPageContext(
  owner: string,
  context: BusinessContext,
) {
  const assistant = useContext(AssistantContext);
  const register = assistant?.registerPage;
  const serialized = JSON.stringify(context);
  useEffect(() => {
    register?.(owner, JSON.parse(serialized));
    return () => register?.(owner, null);
  }, [register, owner, serialized]);
}
