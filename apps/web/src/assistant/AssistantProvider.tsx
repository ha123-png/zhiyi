import { serverDate } from "../time";
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
import { consentKey, readServiceConsentState, revokeProfileGrants, saveServiceConsent, serviceConsentKey, serviceConsentStorageKey, serviceProfileId, sessionConsentRevision, type ServiceConsentState } from "./consent";
import { applyDeltas, mergePage, type TextDelta } from "./stream";

const running = new Set(["running", "waiting", "cancelling"]);
const convertMessage = (message: ThreadMessageLike) => message;
interface ThreadAuthorizationPayload {
  context: BusinessContext;
  profile_id: string | null;
  profile_version: number | null;
  remote_authorized: boolean;
}
interface ThreadAuthorizationSnapshot {
  threadId: string;
  key: string;
  body: ThreadAuthorizationPayload;
}
interface ThreadAuthorizationState {
  threadId: string;
  key: string;
  status: "syncing" | "ready" | "failed";
  error?: string;
}
interface ThreadAuthorizationResponse {
  authorization_revision: number;
  invalidated_tools: number;
  cancelled_runs: number;
}
function currentContext(context: BusinessContext): BusinessContext {
  return { ...context, ...(context.capabilities ? { capabilities: context.capabilities.filter(value => value !== "templates") } : {}) };
}
function authorizationSnapshot(
  threadId: string,
  context: BusinessContext,
  profileId: string,
  profileVersion: number | null,
  remoteAuthorized: boolean,
): ThreadAuthorizationSnapshot {
  const body: ThreadAuthorizationPayload = {
    context: currentContext(context),
    profile_id: profileId || null,
    profile_version: profileVersion,
    remote_authorized: remoteAuthorized,
  };
  const normalizedContext = Object.fromEntries(Object.entries(body.context)
    .filter(([key, value]) => key !== "table_name" && value !== undefined)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, value]) => [key, Array.isArray(value) ? [...value].sort() : value]));
  const key = JSON.stringify({ ...body, context: { ...normalizedContext, mode: body.context.mode || "assist" } });
  return { threadId, key, body };
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
  const serviceConsentStorage = serviceConsentStorageKey(assistantBase);
  const [serviceConsent, setServiceConsent] = useState(() => readServiceConsentState(serviceConsentStorage));
  const serviceConsentRef = useRef(serviceConsent);
  const [grants, setGrants] = useState<string[]>(() => {
    if (!serviceConsent.epoch) return [];
    try {
      const saved: unknown = JSON.parse(sessionStorage.getItem(consentStorage) || "[]");
      return Array.isArray(saved) ? saved.filter((v): v is string => typeof v === "string").slice(-100) : [];
    }
    catch { return []; }
  });
  const grantsRef = useRef(grants);
  const consentRevision = useRef(0);
  const [consentNotice, setConsentNotice] = useState("");
  const draftThread = useRef(crypto.randomUUID());
  const [context, setContext] = useState<BusinessContext>({});
  const [pageContext, setPageContext] = useState<BusinessContext>({});
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [authorizationState, setAuthorizationState] = useState<ThreadAuthorizationState | null>(null);
  const [authorizationReadyThread, setAuthorizationReadyThread] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [failedThread, setFailedThread] = useState<string | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const deltas = useRef(new Map<string, TextDelta[]>());
  const streamCleanup = useRef<(() => void) | null>(null);
  const activeId = useRef<string | null>(null);
  const submitting = useRef(false);
  const submission = useRef<{ signature: string; id: string } | null>(null);
  const observer = useRef<EventSource | null>(null);
  const cancellation = useRef<{ threadId: string; runId: string; source: EventSource | null } | null>(null);
  const refreshSequence = useRef(0);
  const announced = useRef(new Set<string>());
  const pageOwner = useRef("");
  const defaultId = useRef("");
  const selectionRevision = useRef(0);
  const analysisSubmission = useRef<{ signature: string; id: string } | null>(null);
  const authorizationQueue = useRef<Promise<void>>(Promise.resolve());
  const synchronizedAuthorization = useRef(new Map<string, string>());
  const authorizationSnapshotRef = useRef<ThreadAuthorizationSnapshot | null>(null);
  const authorizationReadyThreadRef = useRef<string | null>(null);
  const lastModelRun = detail?.last_model_run ?? detail?.runs.find(run => run.execution_kind !== "analysis_refresh");
  const profile = profiles.find((p) => p.id === profileId);
  const grantKey = (thread = activeId.current || draftThread.current) => profile
    ? consentKey(thread, profile, context, sessionConsentRevision(serviceConsent, profile.id)) : "";
  const serviceKey = profile ? serviceConsentKey(profile) : "";
  const rememberedRemoteConsent = !!serviceKey && serviceConsent.keys.includes(serviceKey);
  const remoteConsent = !!profile?.is_remote && (rememberedRemoteConsent || (!!grantKey() && grants.includes(grantKey())));
  const authProfileVersion = profile?.version ?? (lastModelRun?.profile_id === profileId ? lastModelRun.profile_version : null);
  const authorizationThread = activeId.current && (detail?.id === activeId.current || authorizationReadyThread === activeId.current)
    ? activeId.current : null;
  const authorizationSnapshotCurrent = authorizationThread
    ? authorizationSnapshot(authorizationThread, context, profileId, authProfileVersion, !!profile?.is_remote && remoteConsent)
    : null;
  authorizationSnapshotRef.current = authorizationSnapshotCurrent;
  authorizationReadyThreadRef.current = authorizationReadyThread;
  const sendStateRef = useRef({ context, profile, remoteConsent, rememberedRemoteConsent });
  sendStateRef.current = { context, profile, remoteConsent, rememberedRemoteConsent };
  const authorizationPending = !!authorizationThread && (
    authorizationReadyThread !== authorizationThread || authorizationState?.threadId !== authorizationThread ||
    authorizationState?.key !== authorizationSnapshotCurrent?.key || authorizationState.status !== "ready"
  );
  function storeSessionGrants(next: string[]) {
    grantsRef.current = next;
    setGrants(next);
    try {
      if (next.length && serviceConsentRef.current.epoch) sessionStorage.setItem(consentStorage, JSON.stringify(next));
      else sessionStorage.removeItem(consentStorage);
    } catch {
      // A failed overwrite must not leave an older allow record on reload.
      try { sessionStorage.removeItem(consentStorage); } catch { /* The service epoch also invalidates revoked grants. */ }
    }
  }
  function rememberConsent(key: string, allowed: boolean) {
    if (!key) return;
    storeSessionGrants([...grantsRef.current.filter(value => value !== key), ...(allowed ? [key] : [])].slice(-100));
  }
  function storeServiceConsent(next: ServiceConsentState) {
    serviceConsentRef.current = next;
    setServiceConsent(next);
  }
  function revokeServiceProfiles(profileIds: string[]) {
    storeSessionGrants(profileIds.reduce((remaining, id) => revokeProfileGrants(remaining, id), grantsRef.current));
    if (!sendStateRef.current.profile || !profileIds.includes(sendStateRef.current.profile.id)) return;
    consentRevision.current++;
    sendStateRef.current = { ...sendStateRef.current, remoteConsent: false, rememberedRemoteConsent: false };
    const snapshot = authorizationSnapshotRef.current;
    if (!snapshot || authorizationReadyThreadRef.current !== snapshot.threadId) return;
    const revoked = authorizationSnapshot(snapshot.threadId, snapshot.body.context,
      snapshot.body.profile_id || "", snapshot.body.profile_version, false);
    authorizationSnapshotRef.current = revoked;
    void synchronizeAuthorization(revoked, true).catch(reason => {
      setError(`未能同步撤销的云端允许：${reason instanceof Error ? reason.message : "授权同步失败"}`);
    });
  }
  function setRemoteConsent(allowed: boolean, rememberService = false) {
    if (!profile?.is_remote || !serviceKey || !grantKey()) {
      setConsentNotice("模型服务地址无效，请先检查模型设置。");
      return;
    }
    setConsentNotice("");
    if (!allowed) {
      const result = saveServiceConsent(serviceConsentStorage, serviceKey, false);
      storeServiceConsent(result.state);
      revokeServiceProfiles([profile.id]);
      if (!result.saved) setConsentNotice(result.sessionSaved
        ? "已撤销此窗口的允许，刷新后仍有效；浏览器未能保存到设备。其他窗口或重新打开知意后请再次检查。"
        : "已撤销当前允许，但浏览器拒绝保存撤销。刷新或重新打开前，请在浏览器设置中清除此站点的存储。");
      return;
    }
    const result = saveServiceConsent(serviceConsentStorage, serviceKey, true, rememberService);
    storeServiceConsent(result.state);
    if (rememberService && result.saved) return;
    if (rememberService) {
      setConsentNotice("浏览器未能记住此选择；当前只允许这段对话，可稍后重试。");
    }
    rememberConsent(consentKey(activeId.current || draftThread.current, profile, context,
      sessionConsentRevision(result.state, profile.id)), true);
  }
  useEffect(() => { setConsentNotice(""); }, [serviceKey]);
  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key !== null && event.key !== serviceConsentStorage) return;
      const previous = serviceConsentRef.current;
      const next = readServiceConsentState(serviceConsentStorage);
      const removed = previous.keys.filter(key => !next.keys.includes(key)).map(serviceProfileId);
      const sessionProfiles = grantsRef.current.flatMap(key => {
        try { const id: unknown = JSON.parse(key)[1]; return typeof id === "string" ? [id] : []; }
        catch { return []; }
      });
      const revoked = [...new Set([...removed, ...sessionProfiles, ...Object.keys(previous.revoked), ...Object.keys(next.revoked)])]
        .filter((id): id is string => !!id && (removed.includes(id) || sessionConsentRevision(previous, id) !== sessionConsentRevision(next, id)));
      storeServiceConsent(next);
      if (revoked.length) revokeServiceProfiles(revoked);
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [serviceConsentStorage]);
  const updateContext = useCallback((next: BusinessContext) => {
    setContext(currentContext(next));
  }, []);
  const selectProfile = useCallback((id: string) => {
    selectionRevision.current++;
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
    const revision = selectionRevision.current;
    void loadModels()
      .then((id) => {
        if (mounted && !activeId.current && revision === selectionRevision.current) setProfileId(id);
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
  const refresh = useCallback(async (id: string, runId?: string, ownsResponse?: () => boolean) => {
    const seq = ++refreshSequence.current;
    const next = await assistantApi<ThreadDetail>(
      `/threads/${encodeURIComponent(id)}${runId ? `?run_id=${encodeURIComponent(runId)}` : ""}`,
    );
    if (!next || !Array.isArray(next.messages) || !Array.isArray(next.runs) || !Array.isArray(next.tools))
      throw new Error("对话暂时无法读取，请重试。");
    const canCommit = () => activeId.current === id && seq === refreshSequence.current && (!ownsResponse || ownsResponse());
    if (canCommit()) {
      const pending = cancellation.current;
      const stopped = pending?.threadId === id && pending.source === observer.current
        ? next.runs.find(run => run.id === pending.runId && !running.has(run.status)) : undefined;
      if (stopped) {
        cancellation.current = null;
        setStatus(stopped.status === "cancelled" ? "已停止，生成内容已保存" : "");
      }
      setDetail(previous => {
        if (!canCommit()) return previous;
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
  const synchronizeAuthorization = useCallback(async (
    snapshot: ThreadAuthorizationSnapshot,
    force = false,
  ) => {
    const synchronize = async () => {
      if (!force && synchronizedAuthorization.current.get(snapshot.threadId) === snapshot.key) {
        setAuthorizationState(previous => previous?.threadId === snapshot.threadId && previous.key === snapshot.key && previous.status === "ready"
          ? previous : { threadId: snapshot.threadId, key: snapshot.key, status: "ready" });
        return;
      }
      setAuthorizationState(previous => previous?.threadId === snapshot.threadId && previous.key === snapshot.key && previous.status === "syncing"
        ? previous : { threadId: snapshot.threadId, key: snapshot.key, status: "syncing" });
      try {
        const response = await assistantApi<ThreadAuthorizationResponse>(
          `/threads/${encodeURIComponent(snapshot.threadId)}/authorization`,
          "PUT",
          snapshot.body,
        );
        synchronizedAuthorization.current.set(snapshot.threadId, snapshot.key);
        if (authorizationSnapshotRef.current?.threadId === snapshot.threadId && authorizationSnapshotRef.current.key === snapshot.key) {
          setAuthorizationState({ threadId: snapshot.threadId, key: snapshot.key, status: "ready" });
        }
        if (response.invalidated_tools > 0 || response.cancelled_runs > 0) {
          if (activeId.current === snapshot.threadId) {
            setStatus(response.cancelled_runs > 0 ? "云端允许已撤销，正在停止本轮回答。" : "授权已变化，旧操作预览已失效，请重新生成");
            await refresh(snapshot.threadId);
          }
        }
      } catch (reason) {
        const message = reason instanceof Error ? reason.message : "授权同步失败";
        if (authorizationSnapshotRef.current?.threadId === snapshot.threadId && authorizationSnapshotRef.current.key === snapshot.key) {
          setAuthorizationState({ threadId: snapshot.threadId, key: snapshot.key, status: "failed", error: message });
        }
        throw reason;
      }
    };
    const pending = authorizationQueue.current.catch(() => undefined).then(synchronize);
    authorizationQueue.current = pending.then(() => undefined, () => undefined);
    await pending;
  }, [refresh]);
  const ensureAuthorizationCurrent = useCallback(async () => {
    for (let attempt = 0; attempt < 8; attempt++) {
      const snapshot = authorizationSnapshotRef.current;
      if (!snapshot) return;
      if (authorizationReadyThreadRef.current !== snapshot.threadId) {
        throw new Error("当前对话授权状态尚未读取完成，请稍后重试。");
      }
      await synchronizeAuthorization(snapshot);
      const latest = authorizationSnapshotRef.current;
      if (latest?.threadId === snapshot.threadId && latest.key === snapshot.key) return;
    }
    throw new Error("授权范围刚刚发生变化，请重新确认后再操作。");
  }, [synchronizeAuthorization]);
  useEffect(() => {
    const snapshot = authorizationSnapshotCurrent;
    if (!snapshot || authorizationReadyThread !== snapshot.threadId || loading || busy || submitting.current) return;
    void synchronizeAuthorization(snapshot).catch(reason => {
      const message = reason instanceof Error ? reason.message : "授权同步失败";
      setError(`未能同步当前对话授权，操作预览无法确认：${message}`);
    });
  }, [authorizationSnapshotCurrent?.threadId, authorizationSnapshotCurrent?.key, authorizationReadyThread, loading, busy, synchronizeAuthorization]);
  const observe = useCallback(
    (id: string, runId: string) => {
      observer.current?.close();
      streamCleanup.current?.();
      const source = new EventSource(
        `${assistantBase}/runs/${encodeURIComponent(runId)}/events`,
      );
      observer.current = source;
      let disposed = false;
      let finished = false;
      let streamRevision = 0;
      const isCurrent = () => !disposed && observer.current === source && activeId.current === id;
      let failures = 0;
      let frame: ReturnType<typeof setTimeout> | undefined;
      let checkpoint: ReturnType<typeof setTimeout> | undefined;
      let received: TextDelta[] = [];
      const flush = () => {
        frame = undefined;
        const batch = received;
        received = [];
        if (!isCurrent()) return;
        setDetail(previous => isCurrent() && previous?.id === id ? applyDeltas(previous, runId, batch) : previous);
      };
      const sync = () => {
        checkpoint = undefined;
        if (!isCurrent()) return;
        const terminalAtRead = finished;
        const ownsSnapshot = () => isCurrent() && terminalAtRead === finished;
        void refresh(id, runId, ownsSnapshot).catch((e) => { if (ownsSnapshot()) setError(e.message); });
      };
      streamCleanup.current = () => {
        disposed = true;
        clearTimeout(frame); clearTimeout(checkpoint);
        received = [];
        deltas.current.clear();
        source.onopen = null; source.onmessage = null; source.onerror = null;
        if (cancellation.current?.source === source) cancellation.current = null;
        if (observer.current === source) observer.current = null;
      };
      source.onopen = () => {
        if (!isCurrent() || finished) return;
        streamRevision++;
        setStatus("正在接收回答…");
      };
      source.onmessage = (event) => {
        if (!isCurrent() || finished) {
          source.close();
          return;
        }
        streamRevision++;
        failures = 0;
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
          finished = true;
          if (cancellation.current?.source === source) cancellation.current = null;
          source.close();
          clearTimeout(frame); clearTimeout(checkpoint);
          flush(); sync();
          setStatus(
            data.status === "cancelled" ? "已停止，生成内容已保存" : "",
          );
          setBusy(false);
          void reloadThreads().catch((e) => { if (isCurrent()) setError(e.message); });
        }
      };
      source.onerror = () => {
        if (!isCurrent() || finished) { source.close(); return; }
        failures += 1;
        const revision = ++streamRevision;
        const ownsRecovery = () => isCurrent() && !finished && revision === streamRevision;
        setStatus("连接中断，正在核对已保存的回答…");
        void refresh(id, runId, ownsRecovery)
          .then((next) => {
            if (!ownsRecovery()) return;
            const run = next.runs.find((r) => running.has(r.status));
            if (!run) {
              source.close();
              setStatus(next.runs.find(value => value.id === runId)?.status === "cancelled" ? "已停止，生成内容已保存" : "");
              return;
            }
            if (failures >= 5) {
              source.close();
              setError(
                "实时连接中断，回答可能仍在后台生成。可重新打开这段对话连接，或点击停止。",
              );
              setStatus("");
            } else setStatus("连接短暂中断，正在自动重连…");
          })
          .catch((e) => {
            if (!ownsRecovery()) return;
            if (failures >= 5) { source.close(); setError(e.message); }
          });
      };
    },
    [refresh, reloadThreads],
  );
  const switchThread = useCallback(
    async (id: string) => {
      if (submitting.current) return;
      const revision = ++selectionRevision.current;
      const ownsSelection = () => activeId.current === id && selectionRevision.current === revision;
      observer.current?.close();
      streamCleanup.current?.();
      activeId.current = id;
      setAuthorizationReadyThread(null);
      setAuthorizationState(null);
      setLoading(true);
      setFailedThread(null);
      setError("");
      setStatus("");
      setDetail(null);
      try {
        const next = await refresh(id, undefined, ownsSelection);
        if (!ownsSelection()) return;
        const last = next.last_model_run ?? next.runs.find(run => run.execution_kind !== "analysis_refresh");
        setProfileId(last?.profile_id || next.profile_id || "");
        const restored = next.messages.at(-1)?.context ?? {};
        setContext({
          ...currentContext(restored),
          mode: restored.mode === "delegate" ? "assist" : restored.mode,
        });
        setAuthorizationReadyThread(id);
        const run = next.runs.find((r) => running.has(r.status));
        if (run) observe(id, run.id);
      } catch (e) {
        if (ownsSelection()) {
          setFailedThread(id);
          setError(e instanceof Error ? e.message : "读取失败");
        }
      } finally {
        if (ownsSelection()) setLoading(false);
      }
    },
    [refresh, observe],
  );
  const newThread = useCallback(() => {
    if (submitting.current) return;
    selectionRevision.current++;
    observer.current?.close();
    streamCleanup.current?.();
    activeId.current = null;
    setAuthorizationReadyThread(null);
    setAuthorizationState(null);
    setDetail(null);
    setBusy(false);
    setLoading(false);
    setFailedThread(null);
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
    submitting.current = true;
    setBusy(true);
    setError("");
    setStatus(activeId.current ? "正在同步授权…" : "正在连接模型…");
    try {
      if (activeId.current) await ensureAuthorizationCurrent();
      const current = sendStateRef.current;
      const sendingConsentRevision = consentRevision.current;
      if (!current.profile) throw new Error("请先选择问知意使用的模型方案。");
      if (current.profile.is_remote && !current.remoteConsent) throw new Error("请先确认本次向远程模型发送的范围。");
      const signature = JSON.stringify({
        text,
        thread: activeId.current,
        profile: current.profile.id,
        version: current.profile.version,
        context: current.context,
      });
      if (submission.current?.signature !== signature)
        submission.current = { signature, id: crypto.randomUUID() };
      setStatus("正在连接模型…");
      const started = await assistantApi<{ thread_id: string; run_id: string }>(
        "/runs",
        "POST",
        {
          request_id: submission.current.id,
          text,
          thread_id: activeId.current,
          profile_id: current.profile.id,
          profile_version: current.profile.version,
          remote_consent: current.profile.is_remote
            ? `${current.profile.id}:${current.profile.version}`
            : null,
          context: currentContext(current.context),
        },
      );
      const revokedDuringSend = current.profile.is_remote && sendingConsentRevision !== consentRevision.current;
      if (current.profile.is_remote && current.remoteConsent && !current.rememberedRemoteConsent && !revokedDuringSend)
        rememberConsent(consentKey(started.thread_id, current.profile, current.context,
          sessionConsentRevision(serviceConsentRef.current, current.profile.id)), true);
      const sentAuthorization = authorizationSnapshot(started.thread_id, current.context, current.profile.id, current.profile.version,
        current.profile.is_remote && current.remoteConsent);
      synchronizedAuthorization.current.set(started.thread_id, sentAuthorization.key);
      setAuthorizationReadyThread(started.thread_id);
      setAuthorizationState({ threadId: started.thread_id, key: sentAuthorization.key, status: "ready" });
      activeId.current = started.thread_id;
      submission.current = null;
      let revocationError = "";
      if (revokedDuringSend) {
        // The first request may create its thread after the revocation event.
        // Always revoke again once the server supplies that thread, even when
        // an earlier PUT for an existing thread had already completed.
        const revoked = authorizationSnapshot(started.thread_id, current.context, current.profile.id, current.profile.version, false);
        authorizationSnapshotRef.current = revoked;
        authorizationReadyThreadRef.current = started.thread_id;
        try { await synchronizeAuthorization(revoked, true); }
        catch (reason) {
          revocationError = `未能同步撤销的云端允许，请在对话设置中重试：${reason instanceof Error ? reason.message : "授权同步失败"}`;
        }
      }
      setContext((previous) =>
        previous.mode === "delegate"
          ? { ...previous, mode: "assist" }
          : previous,
      );
      await refresh(started.thread_id);
      observe(started.thread_id, started.run_id);
      await reloadThreads();
      if (revocationError) setError(revocationError);
      else if (revokedDuringSend) setStatus("云端允许已撤销，已请求停止本轮回答。");
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
    const threadId = detail?.id;
    const run = detail?.runs.find((r) => running.has(r.status));
    if (!threadId || !run || (cancellation.current?.threadId === threadId && cancellation.current.runId === run.id)) return;
    const attempt = { threadId, runId: run.id, source: observer.current };
    const ownsView = () => activeId.current === threadId && observer.current === attempt.source;
    const isCurrent = () => cancellation.current === attempt && ownsView();
    const previousStatus = status;
    cancellation.current = attempt;
    setError("");
    setStatus("正在停止…");
    try {
      await assistantApi(`/runs/${run.id}/cancel`, "POST");
      if (!isCurrent()) return;
      const next = await refresh(threadId, undefined, ownsView);
      if (!isCurrent()) return;
      const stopped = next.runs.find(value => value.id === run.id);
      if (stopped && !running.has(stopped.status)) setStatus(stopped.status === "cancelled" ? "已停止，生成内容已保存" : "");
    } catch (e) {
      if (isCurrent()) {
        setStatus(current => current === "正在停止…" ? previousStatus : current);
        setError(e instanceof Error ? e.message : "停止失败");
      }
    } finally {
      if (cancellation.current === attempt) cancellation.current = null;
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
      if (approve) await ensureAuthorizationCurrent();
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
      if (activeId.current) void refresh(activeId.current).catch(() => {});
      return false;
    }
  }
  async function undo(id: string) {
    try {
      await ensureAuthorizationCurrent();
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
        createdAt: serverDate(m.created_at),
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
    isSendDisabled: loading || !!failedThread || !profile || (profile.is_remote && !remoteConsent) ||
      (authorizationThread !== null && authorizationState?.threadId === authorizationThread && authorizationState.status === "failed"),
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
        // Keep the composer attached while this thread is loading or retrying.
        threadId: activeId.current ?? undefined,
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
  async function refreshAnalysis(toolId: string) {
    const threadId = activeId.current;
    if (!threadId || submitting.current || busy) return;
    submitting.current = true;
    setBusy(true);
    setError("");
    setStatus("正在同步授权…");
    const signature = JSON.stringify({ threadId, toolId, context: currentContext(context) });
    if (analysisSubmission.current?.signature !== signature)
      analysisSubmission.current = { signature, id: crypto.randomUUID() };
    try {
      await ensureAuthorizationCurrent();
      setStatus("正在按原统计口径刷新…");
      await assistantApi(`/tools/${encodeURIComponent(toolId)}/refresh-analysis`, "POST", {
        request_id: analysisSubmission.current.id, thread_id: threadId, context: currentContext(context),
      });
      await refresh(threadId);
      await reloadThreads();
      analysisSubmission.current = null;
    } catch (e) {
      setError(e instanceof Error ? e.message : "刷新失败");
    } finally {
      submitting.current = false;
      setBusy(false);
      setStatus("");
    }
  }
  async function retryAuthorizationSync() {
    try {
      const snapshot = authorizationSnapshotRef.current;
      if (!snapshot) throw new Error("请先打开一段对话。");
      await synchronizeAuthorization(snapshot, true);
      setError("");
    } catch (reason) {
      setError(`未能同步当前对话授权，操作预览无法确认：${reason instanceof Error ? reason.message : "授权同步失败"}`);
    }
  }
  return {
    runtime,
    drawerOpen,
    setDrawerOpen,
    detail,
    threads,
    profiles,
    profile,
    profileId,
    lastModelRun,
    refreshAnalysis,
    selectProfile,
    remoteConsent,
    rememberedRemoteConsent,
    consentNotice,
    setRemoteConsent,
    context,
    updateContext,
    pageContext,
    registerPage,
    error,
    setError,
    status,
    busy,
    authorizationPending,
    authorizationError: authorizationState?.status === "failed" && authorizationState.threadId === authorizationThread
      ? authorizationState.error || "授权同步失败" : "",
    retryAuthorizationSync,
    loading,
    failedThread,
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
