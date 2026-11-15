import { useState } from "react";
import { Download } from "lucide-react";
import { desktopApi } from "../desktop";
import { Icon } from "./Icon";

export function OriginalDownload({ url, filename, size = "sm" }: { url: string; filename: string; size?: "xs" | "sm" }) {
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ text: string; failed?: boolean } | null>(null);
  return <span className="original-file-download">
    <a className={`btn secondary ${size}`} href={url} download={filename} aria-disabled={busy || undefined}
      title="下载上传时保存的原文件" onClick={async event => {
        const bridge = desktopApi();
        if (!bridge) return;
        event.preventDefault();
        if (busy) return;
        setBusy(true);
        setFeedback(null);
        try {
          const path = await bridge.download_task_file(new URL(url, window.location.href).toString(), filename);
          setFeedback({ text: `下载完成：${path}` });
        } catch (error) {
          setFeedback({ text: error instanceof Error ? error.message : "原文件下载失败", failed: true });
        } finally {
          setBusy(false);
        }
      }}>
      <Icon icon={Download} size={14} />{busy ? "下载中…" : "下载原文件"}
    </a>
    {feedback && <span className={feedback.failed ? "callout danger" : "callout"} role={feedback.failed ? "alert" : "status"}>{feedback.text}</span>}
  </span>;
}
