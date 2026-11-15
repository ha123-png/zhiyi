import { useEffect, useRef, useState } from "react";
import { Loader2, Play, Power, RefreshCw, Unplug, Zap } from "lucide-react";
import { getLocalModelStatus, getLocalModels, loadLocalModel, startLocalModelServer, stopLocalModelServer, unloadLocalModel } from "../api";
import type { LocalModelList, LocalModelStatus } from "../types";
import { Icon } from "./Icon";

const LOAD_CONTEXT_KEY = "zhiyi.local-model.load-context";

function validLoadContext(value: string) {
  const length = Number(value);
  return Number.isSafeInteger(length) && length >= 1024 && length <= 262144;
}

export function ExistingLocalModelSettings({ refreshSignal = 0 }: { refreshSignal?: number } = {}) {
  const [status, setStatus] = useState<LocalModelStatus | null>(null);
  const [models, setModels] = useState<LocalModelList | null>(null);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState("");
  const [preferredContext, setPreferredContext] = useState<string | null>(() => {
    try {
      const value = localStorage.getItem(LOAD_CONTEXT_KEY);
      return value && validLoadContext(value) ? value : null;
    } catch {
      return null;
    }
  });
  const [contextError, setContextError] = useState("");
  const contextInput = useRef<HTMLInputElement>(null);
  const [contextDirty, setContextDirty] = useState(false);
  const contextRevision = useRef(0);
  const scopeVersion = useRef(0);
  const refreshRequest = useRef(0);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh(clear = true) {
    const request = ++refreshRequest.current;
    if (clear) { setBusy(true); setPending("正在检查服务与模型…"); setError(null); setMessage(null); }
    try {
      const next = await getLocalModelStatus();
      if (request !== refreshRequest.current) return;
      if (next.provider !== status?.provider || next.base_url !== status?.base_url) setModels(null);
      setStatus(next);
      const nextModels = next.running ? await getLocalModels() : { provider: next.provider, available: [], loaded: [] };
      if (request === refreshRequest.current) setModels(nextModels);
    } catch (reason) { if (request === refreshRequest.current) setError(reason instanceof Error ? reason.message : "本地模型状态读取失败。"); }
    finally { if (clear && request === refreshRequest.current) { setBusy(false); setPending(""); } }
  }
  useEffect(() => {
    scopeVersion.current += 1;
    setStatus(null); setModels(null);
    void refresh();
    return () => { scopeVersion.current += 1; refreshRequest.current += 1; };
  }, [refreshSignal]);

  async function action(run: () => Promise<{ ok: boolean; message: string; detail?: string | null }>, label = "正在执行操作…", onSuccess?: () => void) {
    const scope = scopeVersion.current;
    setBusy(true); setPending(label); setError(null); setMessage(null);
    try {
      const result = await run();
      if (scope !== scopeVersion.current) return;
      if (result.ok) { setMessage(result.message); onSuccess?.(); } else setError(result.detail ? `${result.message} ${result.detail}` : result.message);
      await refresh(false);
    } catch (reason) { if (scope === scopeVersion.current) setError(reason instanceof Error ? reason.message : "操作失败。"); }
    finally { if (scope === scopeVersion.current) { setBusy(false); setPending(""); } }
  }

  function handleLoad(model: string) {
    const context = Number(contextLength);
    if (!validLoadContext(contextLength)) {
      setContextError("请输入 1024 到 262144 之间的整数，再加载模型。");
      contextInput.current?.focus();
      return;
    }
    setContextError("");
    const revision = contextRevision.current;
    void action(() => loadLocalModel(model, context), "正在加载模型，请等待加载完成…", () => {
      if (revision === contextRevision.current) setContextDirty(false);
    });
  }

  const reportedLengths = new Map(Object.entries(models?.loaded_context_lengths ?? {}));
  const loaded = new Set([...(models?.loaded ?? []), ...reportedLengths.keys()]);
  const available = [...new Set([...(models?.available ?? []), ...reportedLengths.keys()])];
  const [onlyReportedLength] = reportedLengths.values();
  const reportedDefault = reportedLengths.size === 1 ? String(onlyReportedLength) : "";
  const contextLength = preferredContext ?? (validLoadContext(reportedDefault) ? reportedDefault : "8192");
  const providerName = status === null ? "本地模型" : status.provider === "ollama" ? "Ollama" : "LM Studio";
  return <div className="local-model-settings">
    {status?.installed === false && <p className="small muted">未找到 {providerName}。<a href={status.install_url ?? (status.provider === "ollama" ? "https://ollama.com/download/windows" : "https://lmstudio.ai/download?os=windows")} target="_blank" rel="noreferrer">下载安装</a>后刷新；已经安装可先手动打开服务。</p>}
    <div className="setting-row"><div><div className="settings-name">{providerName} 服务 <span className={`badge ${status?.running ? "success" : "muted"}`}>{status === null ? busy ? "检查中" : "待确认" : status.running ? "运行中" : "未运行"}</span></div><div className="small muted">{status?.message ?? (busy ? "正在检查…" : "本地服务状态读取失败。")}</div></div>
      <div className="local-model-actions"><button className="btn ghost sm" disabled={busy} onClick={() => void refresh()}><Icon icon={RefreshCw} size={13}/>刷新</button>
        {status?.running ? <button className="btn secondary sm" disabled={busy} onClick={() => void action(stopLocalModelServer, "正在停止服务…")}><Icon icon={busy ? Loader2 : Power} size={13} className={busy ? "spin" : undefined}/>停止服务</button>
          : <button className="btn primary sm" disabled={busy || !status} onClick={() => void action(startLocalModelServer, "正在启动并检查服务是否就绪…")}><Icon icon={busy ? Loader2 : Play} size={13} className={busy ? "spin" : undefined}/>启动服务</button>}
      </div></div>
    {busy && <p className="support" role="status">{pending}</p>}
    {error && <p role="alert" className="small danger-text">{error}</p>}{message && <p className="small muted" role="status">{message}</p>}
    <div className="local-model-context">
      <label className="form-label" htmlFor="local-model-context">加载时的上下文长度</label>
      <input ref={contextInput} id="local-model-context" className="form-input" type="number"
        min={1024} max={262144} step={1} value={contextLength} aria-invalid={Boolean(contextError)}
        aria-describedby="local-model-context-hint" onChange={event => {
          const value = event.target.value;
          contextRevision.current += 1;
          setPreferredContext(value);
          setContextDirty(true);
          setContextError("");
          if (validLoadContext(value)) {
            try { localStorage.setItem(LOAD_CONTEXT_KEY, value); } catch { /* Keep the current edit usable. */ }
          }
        }} />
      <p id="local-model-context-hint" className="small muted">有效数值会保留，用于下次加载。已加载容量见模型列表。</p>
      {contextError && <p className="small danger-text" role="alert">{contextError}</p>}
      {loaded.size > 0 && contextDirty && <div className="callout warning">上下文长度已修改但尚未生效，请卸载并重新加载模型。</div>}
    </div>
    <div className="local-model-list" aria-label="本地可用模型">
      {available.length > 0 && <div className="support local-model-hint">建议一次只加载一个模型。加载新模型前先卸载当前模型，避免显存或内存不足。</div>}
      {available.length === 0 && <div className="small muted">{status === null ? busy ? "正在读取本地服务状态…" : "尚未确认本地服务状态，请刷新后继续。" : !status.running ? "服务未运行，无法列出模型；先启动服务再刷新。" : models === null ? busy ? "正在读取本地模型列表…" : "尚未读取到模型列表，请刷新后重试。" : `没有读取到已下载的模型，请先在 ${providerName} 中下载模型。`}</div>}
      {available.map(model => {
        const isLoaded = loaded.has(model);
        const length = reportedLengths.get(model);
        return <div className={`local-model-item${isLoaded ? " loaded" : ""}`} key={model}>
          <span className="local-model-name"><span className="local-model-label" title={model}>{model}</span>{isLoaded && <>
            <span className="badge live">已加载</span>
            <span className="small muted">{length ? `上下文 ${length.toLocaleString()} Token` : "容量未报告"}</span>
          </>}</span>
          {isLoaded
            ? <button className="btn ghost sm" disabled={busy} onClick={() => void action(() => unloadLocalModel(model), "正在卸载模型…")}><Icon icon={Unplug} size={13}/>卸载</button>
            : <button className="btn secondary sm" disabled={busy} onClick={() => handleLoad(model)}><Icon icon={Zap} size={13}/>加载</button>}
        </div>;
      })}
    </div>
  </div>;
}
