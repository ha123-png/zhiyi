import { useRef, useState } from "react";
import { extractPendingDuplicate, StoredOriginalUnavailable, uploadTask, type PendingDuplicateRecord } from "../api";
import { desktopApi, type NativeImportFile } from "../desktop";
import { NoticeDialog } from "./NoticeDialog";

export type PendingDuplicate = PendingDuplicateRecord;

export function PendingDuplicateDialog({ item, onClose, onSkip, onQueued, onBeforeRetry, onView }: {
  item: PendingDuplicate;
  onClose: () => void;
  onSkip: () => Promise<void>;
  onQueued: () => void;
  onBeforeRetry: () => Promise<string | null | undefined>;
  onView?: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const working = useRef(false);
  const [error, setError] = useState("");
  const [chooseAgain, setChooseAgain] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  async function attempt(action: () => Promise<void>) {
    if (working.current) return;
    working.current = true;
    setBusy(true);
    setError("");
    try { await action(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "操作失败，请重试。"); }
    finally { working.current = false; setBusy(false); }
  }

  async function enqueue(file: File | NativeImportFile) {
    if (file.name !== item.name || file.size !== item.size) throw new Error("请选择原来提交的文件。");
    const fingerprint = await onBeforeRetry();
    if (!fingerprint) throw new Error("待确认文件尚未取得指纹，请重新上传原文件。");
    await uploadTask(file, item.selection, undefined, "allow", fingerprint, item.id);
    onQueued();
  }

  function retry() {
    if (!chooseAgain) {
      void attempt(async () => {
        await onBeforeRetry();
        try { await extractPendingDuplicate(item.id); }
        catch (reason) {
          if (reason instanceof StoredOriginalUnavailable) setChooseAgain(true);
          throw reason;
        }
        onQueued();
      });
      return;
    }
    const pick = desktopApi()?.choose_import_files;
    if (!pick) { input.current?.click(); return; }
    void attempt(async () => {
      const files = await pick();
      if (files.length > 1) throw new Error("一次请选择一份原文件。");
      if (files[0]) await enqueue(files[0]);
    });
  }

  return <NoticeDialog title="重复待确认" onClose={onClose} busy={busy} footerClose={false} actions={<>
    <button className="btn ghost" type="button" disabled={busy} onClick={() => void attempt(onSkip)}>跳过</button>
    {onView && <button className="btn secondary" type="button" disabled={busy} onClick={() => void attempt(onView)}>查看结果</button>}
    <button className="btn primary" type="button" disabled={busy} onClick={retry}>{chooseAgain ? "选择原件" : "再次提取"}</button>
  </>}>
    <strong className="task-dialog-filename">{item.name}</strong>
    <p>再次提取会新建任务，原结果仍保留。</p>
    {error && <p role="alert">{error}</p>}
    <input type="file" hidden ref={input} onChange={event => {
      const file = event.target.files?.[0];
      event.target.value = "";
      if (file) void attempt(() => enqueue(file));
    }} />
  </NoticeDialog>;
}
