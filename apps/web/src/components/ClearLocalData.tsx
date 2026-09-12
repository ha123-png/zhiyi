import { useEffect, useRef, useState } from "react";
import { Trash2 } from "lucide-react";
import { clearAllLocalData, getClearDataStatus, type ClearDataStatus } from "../api";
import { ConfirmDialog } from "./ConfirmDialog";
import { Icon } from "./Icon";

export function resetKnownBrowserData() {
  for (const key of Object.keys(localStorage)) {
    if (["theme", "zhiyi-card-split", "last-extraction-record-v1", "extract-batch-queue-v1", "last-upload-mode-v1"].includes(key)
      || key.startsWith("onboarding-done-v")) localStorage.removeItem(key);
  }
  sessionStorage.removeItem("zhiyi-settings-anchor");
}

const reload = () => window.location.reload();

export function ClearLocalData({ onCleared = reload }: { onCleared?: () => void }) {
  const [status, setStatus] = useState<ClearDataStatus | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const previousResult = useRef<string | number | undefined>(undefined);
  const finished = useRef(false);

  function complete() {
    if (finished.current) return;
    finished.current = true;
    resetKnownBrowserData();
    setWaiting(false);
    setBusy(false);
    setOpen(false);
    onCleared();
  }

  useEffect(() => {
    let active = true;
    void getClearDataStatus().then((value) => { if (active) setStatus(value); })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "无法读取清除范围"); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!waiting) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function check() {
      try {
        const value = await getClearDataStatus();
        if (!active) return;
        setStatus(value);
        const fresh = value.result && value.result.completed_at !== previousResult.current;
        if (fresh && !value.incomplete && value.result?.state === "succeeded") {
          complete();
          return;
        }
        if (fresh && value.result?.state === "failed") {
          setError(value.result.message || "清除未完成，请重试。外部副本和备份未被删除。");
          setWaiting(false);
          setBusy(false);
          return;
        }
      } catch { /* The desktop API is temporarily unavailable during restart. */ }
      if (active) timer = setTimeout(() => void check(), 2000);
    }
    timer = setTimeout(() => void check(), 2000);
    return () => { active = false; clearTimeout(timer); };
  }, [waiting]);

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      const before = await getClearDataStatus();
      previousResult.current = before.result?.completed_at;
      const result = await clearAllLocalData();
      if (result.scheduled) {
        setOpen(false);
        setWaiting(true);
      } else if (result.state === "succeeded") complete();
      else throw new Error("未收到清除完成的确认，请检查状态后重试。");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "清除未完成");
      setOpen(false);
      setBusy(false);
      void getClearDataStatus().then(setStatus).catch(() => undefined);
    }
  }

  return <>
    <div className="setting-row">
      <div>
        <div className="settings-name">清除全部本地数据</div>
        <div className="small muted">删除内部原件、全部记录和数据表、自定义模板及保存的设置和密钥。保留外部副本、备份和模型文件。</div>
      </div>
      <button className="btn danger sm" type="button" disabled={busy || !status} onClick={() => setOpen(true)}>
        <Icon icon={Trash2} size={13} /> {status?.incomplete ? "重试清除" : "清除全部"}
      </button>
    </div>
    {status?.incomplete && !waiting && <div className="callout danger" role="alert">上次清除未完成，部分数据可能已经删除。新任务已暂停，请重试清除。</div>}
    {waiting && <div className="callout" role="status">正在停止任务、清除数据并重新启动知意。请保持页面打开，完成后会自动刷新。</div>}
    {error && <div className="callout danger" role="alert">{error}
      {!status && <button className="btn secondary sm" type="button" onClick={() => {
        void getClearDataStatus().then((value) => { setStatus(value); setError(null); })
          .catch((reason) => setError(reason instanceof Error ? reason.message : "无法读取清除范围"));
      }}>重新读取清除范围</button>}
    </div>}
    <ConfirmDialog open={open} title="清除全部本地数据" confirmText="清除全部本地数据" buttonLabel="永久清除本地数据" busy={busy}
      description={<div style={{ textAlign: "left" }}>
        <p>停止全部任务，并清除当前知意的数据与设置：</p>
        <ul>
          <li><strong>删除数据：</strong>内部原件、文件历史、提取结果、数据表与修改记录、自定义模板。</li>
          <li><strong>重置设置：</strong>模型连接方案及保存的密钥、路径绑定和本机设置；内置模板恢复默认，AI 连接需要重新配置。</li>
          <li><strong>保留：</strong>外部导出的副本、已保存的备份（包括恢复前的备份）、模型软件及模型文件、程序本身。</li>
        </ul>
        <p>内部原件位置：{status?.original_directory ?? "正在读取"}</p>
        <p>仅清除当前浏览器的知意偏好；其他浏览器需自行清理。此操作无法撤销，只有另存的备份可用于恢复。完成后页面刷新。</p>
      </div>}
      onConfirm={() => void confirm()} onClose={() => setOpen(false)} />
  </>;
}
