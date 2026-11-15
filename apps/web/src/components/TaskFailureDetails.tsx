import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { getTaskDiagnostics } from "../api";
import { DisclosureSummary } from "./CollapseChevron";
import { Icon } from "./Icon";

export function TaskFailureDetails({ taskId }: { taskId: string }) {
  const [detail, setDetail] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  const [loading, setLoading] = useState(false);
  async function load() {
    if (loading) return;
    setLoading(true); setError(""); setCopied(false); setCopyError(false);
    try {
      const value = await getTaskDiagnostics(taskId);
      setDetail(value.detail);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "诊断读取失败。"); }
    finally { setLoading(false); }
  }
  return <details className="task-failure-details" onToggle={event => { if (event.currentTarget.open) void load(); }}>
    <DisclosureSummary>诊断详情</DisclosureSummary>
    {loading ? <p className="support">正在读取…</p> : error ? <p role="alert">{error}</p> : detail && <>
      <div className="task-diagnostic-content">
        <pre>{detail}</pre>
        <button type="button" className="btn ghost xs icon-only" aria-label="复制诊断" title="复制诊断" onClick={async () => {
          try { await navigator.clipboard.writeText(detail); setCopied(true); setCopyError(false); }
          catch { setCopied(false); setCopyError(true); }
        }}><Icon icon={copied ? Check : Copy} size={14} /></button>
      </div>
      {copied && <span className="support" role="status">已复制</span>}
      {copyError && <span className="support" role="status">复制失败，可选中文字手动复制。</span>}
    </>}
  </details>;
}
