import { useEffect, useState } from "react";
import { Loader2, Play, Power, RefreshCw, Unplug, Zap } from "lucide-react";
import { getLocalModelStatus, getLocalModels, loadLocalModel, startLocalModelServer, stopLocalModelServer, unloadLocalModel } from "../api";
import type { LocalModelList, LocalModelStatus } from "../types";
import { Icon } from "./Icon";

export function ExistingLocalModelSettings() {
  const [status, setStatus] = useState<LocalModelStatus | null>(null);
  const [models, setModels] = useState<LocalModelList | null>(null);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState("");
  const [contextLength, setContextLength] = useState(8192);
  const [contextDirty, setContextDirty] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh(clear = true) {
    if (clear) { setBusy(true); setPending("正在检查服务与模型…"); setError(null); setMessage(null); }
    try {
      const next = await getLocalModelStatus();
      setStatus(next);
      setModels(next.running ? await getLocalModels() : { provider: next.provider, available: [], loaded: [] });
    } catch (reason) { setError(reason instanceof Error ? reason.message : "本地模型状态读取失败。"); }
    finally { if (clear) { setBusy(false); setPending(""); } }
  }
  useEffect(() => { void refresh(); }, []);

  async function action(run: () => Promise<{ ok: boolean; message: string; detail?: string | null }>, label = "正在执行操作…") {
    setBusy(true); setPending(label); setError(null); setMessage(null);
    try {
      const result = await run();
      if (result.ok) setMessage(result.message); else setError(result.detail ? `${result.message} ${result.detail}` : result.message);
      await refresh(false);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "操作失败。"); }
    finally { setBusy(false); setPending(""); }
  }

  const loaded = new Set(models?.loaded ?? []);
  const available = models?.available ?? [];
  const providerName = status?.provider === "ollama" ? "Ollama" : "LM Studio";
  return <div className="local-model-settings">
    {status?.installed === false && <div className="callout warning">未找到 {providerName} 的本地启动组件。<a href={status.install_url ?? (status.provider === "ollama" ? "https://ollama.com/download/windows" : "https://lmstudio.ai/download?os=windows")} target="_blank" rel="noreferrer">打开官方下载页</a>，安装并打开一次后回来刷新；已安装时先手动打开服务也可以。</div>}
    <div className="setting-row"><div><div className="settings-name">{providerName} 服务 <span className={`badge ${status?.running ? "success" : "muted"}`}>{status === null ? "检查中" : status.running ? "运行中" : "未运行"}</span></div><div className="small muted">{status?.message ?? "正在检查…"}</div></div>
      <div className="local-model-actions"><button className="btn ghost sm" disabled={busy} onClick={() => void refresh()}><Icon icon={RefreshCw} size={13}/>刷新</button>
        {status?.running ? <button className="btn secondary sm" disabled={busy} onClick={() => void action(stopLocalModelServer, "正在停止服务…")}><Icon icon={busy ? Loader2 : Power} size={13} className={busy ? "spin" : undefined}/>停止服务</button>
          : <button className="btn primary sm" disabled={busy} onClick={() => void action(startLocalModelServer, "正在启动并检查服务是否就绪…")}><Icon icon={busy ? Loader2 : Play} size={13} className={busy ? "spin" : undefined}/>启动服务</button>}
      </div></div>
    {busy && <p className="support" role="status">{pending}</p>}
    {error && <div role="alert" className="callout danger">{error}</div>}{message && <div className="callout success">{message}</div>}
    <div className="local-model-context"><label className="form-label" htmlFor="local-model-context">加载时的上下文长度</label><input id="local-model-context" className="form-input" type="number" min={1024} max={262144} step={1024} value={contextLength} onChange={(event) => { setContextLength(Number(event.target.value) || 8192); setContextDirty(true); }}/>{loaded.size > 0 && contextDirty && <div className="callout warning">上下文长度已修改但尚未生效，请卸载并重新加载模型。</div>}</div>
    <div className="local-model-list" aria-label="本地可用模型">
      {available.length > 0 && <div className="support local-model-hint">建议一次只加载一个模型。加载新模型前先卸载当前模型，避免显存或内存不足。</div>}
      {available.length === 0 && <div className="small muted">{status?.running ? `没有读取到已下载的模型，请先在 ${providerName} 中下载模型。` : "服务未运行，无法列出模型；先启动服务再刷新。"}</div>}
      {available.map((model) => <div className={`local-model-item${loaded.has(model) ? " loaded" : ""}`} key={model}><span className="local-model-name">{model}{loaded.has(model) && <span className="badge live">已加载</span>}</span>{loaded.has(model) ? <button className="btn ghost sm" disabled={busy} onClick={() => void action(() => unloadLocalModel(model), "正在卸载模型…")}><Icon icon={Unplug} size={13}/>卸载</button> : <button className="btn secondary sm" disabled={busy} onClick={() => void action(() => loadLocalModel(model, contextLength), "正在加载模型，请等待加载完成…")}><Icon icon={Zap} size={13}/>加载</button>}</div>)}
    </div>
  </div>;
}
