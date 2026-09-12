import { useState } from "react";
import { getTaskDiagnostics } from "../api";

export function TaskFailureDetails({ taskId }: { taskId: string }) {
  const [detail, setDetail] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const [loading, setLoading] = useState(false);
  async function load() {
    if (loading) return;
    setLoading(true); setError("");
    try {
      const value = await getTaskDiagnostics(taskId);
      setDetail(value.detail);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "诊断读取失败。"); }
    finally { setLoading(false); }
  }
  return <details className="task-failure-details" onToggle={event => { if (event.currentTarget.open) void load(); }}>
    <summary>诊断详情</summary>
    {loading ? <p className="support">正在读取…</p> : error ? <p role="alert">{error}</p> : <>
      <pre>{detail}</pre>
      <button type="button" className="btn ghost sm" onClick={async () => {
        try { await navigator.clipboard.writeText(detail ?? ""); setCopied(true); }
        catch { setError("复制失败，可以直接选中诊断文字复制。"); }
      }}>{copied ? "已复制" : "复制诊断"}</button>
      <p className="support">显示当前处理尝试的诊断，已遮蔽常见凭证和路径；对外分享前请检查内容。</p>
    </>}
  </details>;
}
