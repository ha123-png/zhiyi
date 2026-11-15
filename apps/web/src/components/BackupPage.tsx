import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArchiveRestore,
  DatabaseBackup,
  Loader2,
  Plus,
  Trash2,
} from "lucide-react";
import {
  createBackup,
  deleteBackup,
  getBackups,
  getBackupStatus,
  restoreBackup,
} from "../api";
import type { BackupRead, BackupStatus } from "../types";
import { desktopApi } from "../desktop";
import { serverDate } from "../time";
import { ConfirmDialog } from "./ConfirmDialog";
import { Icon } from "./Icon";
import { Toast, useToast } from "./Toast";
import { NoticeDialog } from "./NoticeDialog";

const SEEN_RESTORE = "zhiyi:seen-backup-restore";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function formatDateTime(iso: string): string {
  const d = serverDate(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

async function waitForRestoreResult(previous: number | null | undefined): Promise<void> {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    await new Promise((resolve) => window.setTimeout(resolve, 500));
    try {
      const result = await getBackupStatus();
      if (result.restore_completed_at && result.restore_completed_at !== previous) return;
    } catch {
      // The desktop supervisor temporarily stops the API during restoration.
    }
  }
  throw new Error("暂未收到恢复结果。请重新打开应用查看备份状态。");
}

export function BackupPage() {
  const [backups, setBackups] = useState<BackupRead[]>([]);
  const [status, setStatus] = useState<BackupStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [readError, setReadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [backupNoticeOpen, setBackupNoticeOpen] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const { toast, notify, clear } = useToast();
  const [pathNotice, setPathNotice] = useState("");
  const [restoreTarget, setRestoreTarget] = useState<BackupRead | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<BackupRead | null>(null);
  const loadRequest = useRef(0);
  const mounted = useRef(true);
  const actionInFlight = useRef(false);
  const seenRestore = useRef<string | null>(null);
  const pathInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    const request = ++loadRequest.current;
    setLoading(true);
    setReadError(null);
    try {
      const [backupList, backupStatus] = await Promise.all([
        getBackups(),
        getBackupStatus(),
      ]);
      if (!mounted.current || request !== loadRequest.current) return;
      setBackups(backupList);
      setStatus(backupStatus);
      if (backupStatus.restore_state === "succeeded" && backupStatus.restore_message) {
        const identity = JSON.stringify([backupStatus.backup_dir, backupStatus.restore_completed_at ?? null]);
        let alreadySeen = seenRestore.current === identity;
        seenRestore.current = identity;
        try {
          alreadySeen ||= localStorage.getItem(SEEN_RESTORE) === identity;
          localStorage.setItem(SEEN_RESTORE, identity);
        } catch { /* The current page can still dismiss the notification. */ }
        if (!alreadySeen) notify(backupStatus.restore_message, "success");
      }
    } catch (err) {
      if (mounted.current && request === loadRequest.current) {
        setReadError(err instanceof Error ? err.message : "读取备份信息失败");
      }
    } finally {
      if (mounted.current && request === loadRequest.current) setLoading(false);
    }
  }, [notify]);

  useEffect(() => {
    mounted.current = true;
    void load();
    return () => { mounted.current = false; loadRequest.current += 1; };
  }, [load]);

  const runAction = async (action: () => Promise<void>) => {
    if (actionInFlight.current || loading || readError || !status) return;
    actionInFlight.current = true;
    setBusy(true);
    setActionError(null);
    clear();
    try {
      await action();
      if (mounted.current) await load();
    } catch (err) {
      const message = err instanceof Error ? err.message : "操作失败";
      setActionError(message);
      if (!restoreTarget && !deleteTarget) notify(message, "error");
    } finally {
      actionInFlight.current = false;
      setBusy(false);
    }
  };

  const handleCreate = () =>
    runAction(async () => {
      await createBackup();
      notify("备份已创建。", "success");
    });

  const handleRestore = () =>
    runAction(async () => {
      if (!restoreTarget) return;
      const result = await restoreBackup(restoreTarget.name);
      setRestoreTarget(null);
      if (result.scheduled) {
        setRestoring(true);
        try {
          await waitForRestoreResult(status?.restore_completed_at);
          window.location.reload();
        } finally {
          setRestoring(false);
        }
        return;
      }
      notify(
        result.restart_required
          ? "已恢复到备份时的状态。请重启应用以同步任务队列。"
          : "已恢复到备份时的状态。",
        result.restart_required ? "info" : "success",
      );
    });

  const handleDelete = () =>
    runAction(async () => {
      if (!deleteTarget) return;
      await deleteBackup(deleteTarget.name);
      setDeleteTarget(null);
      notify("备份已删除。", "success");
    });

  const freeLabel = status
    ? formatBytes(status.free_bytes)
    : "—";
  const lastLabel = status
    ? status.last_success_at ? formatDateTime(status.last_success_at) : "尚未备份过"
    : "—";
  const actionsUnavailable = busy || loading || Boolean(readError) || !status;
  const backupPath = status?.backup_dir;
  const bridge = desktopApi();
  const restoreError = !restoring && status?.restore_state === "failed" ? status.restore_message : null;

  async function copyBackupPath() {
    if (!backupPath) return;
    try {
      await navigator.clipboard.writeText(backupPath);
      setPathNotice("已复制");
    } catch {
      pathInput.current?.focus();
      pathInput.current?.select();
      setPathNotice("按 Ctrl+C 复制路径");
    }
  }

  async function openBackupFolder() {
    setPathNotice("");
    try {
      await bridge?.open_backup_folder?.();
    } catch {
      setPathNotice("无法打开备份文件夹");
    }
  }

  return (
    <div className="view backup-page">
      <Toast toast={toast} onDismiss={clear} />
      {backupNoticeOpen && (readError || restoreError) && <NoticeDialog title="备份事项" onClose={() => setBackupNoticeOpen(false)}>
        {readError && <p>{status ? "未能刷新备份，保留上次读取的内容。" : "暂时无法读取备份信息。"} {readError}</p>}
        {restoreError && <p>{restoreError}</p>}
      </NoticeDialog>}
      <div className="backup-stats-bar" aria-busy={loading}>
        <div className="backup-stat-item">
          <div className="backup-stat-value" title={lastLabel}>{lastLabel}</div>
          <div className="backup-stat-label">最近成功备份</div>
        </div>
        <div className="backup-stat-divider"></div>
        <div className="backup-stat-item">
          <div className="backup-stat-value">
            {status?.count ?? "—"}
          </div>
          <div className="backup-stat-label">已保存的备份</div>
        </div>
        <div className="backup-stat-divider"></div>
        <div className="backup-stat-item">
          <div className="backup-stat-value">{freeLabel}</div>
          <div className="backup-stat-label">磁盘剩余空间</div>
        </div>
        <div className="backup-stat-divider"></div>
        <div className="backup-stat-item">
          {backupPath ? <input ref={pathInput} className="backup-dir-value" title={backupPath}
            aria-label="备份存放位置" value={backupPath} readOnly /> : <div className="backup-stat-value">—</div>}
          <div className="backup-stat-label">备份存放位置</div>
          {backupPath && <div className="backup-dir-actions">
            <button type="button" className="btn ghost sm" onClick={() => void copyBackupPath()}>复制路径</button>
            {bridge?.open_backup_folder && <button type="button" className="btn ghost sm" onClick={() => void openBackupFolder()}>打开文件夹</button>}
          </div>}
          {pathNotice && <span className="support" role="status">{pathNotice}</span>}
        </div>
      </div>

      <div className="card flush" style={{ marginTop: 20, padding: 20 }}>
        <div className="backup-cta">
          <div className="backup-cta-copy">
            <div className="backup-cta-title">
              <Icon icon={DatabaseBackup} size={18} /> 立即创建备份
            </div>
            <div className="backup-cta-support">
              备份包含当前数据、文件修订历史、模板与内部原件，不包含模型密钥。
              手工备份由你保留和删除，新建备份不会删除旧备份。
            </div>
          </div>
          <button
            className="btn primary"
            type="button"
            disabled={actionsUnavailable}
            onClick={() => void handleCreate()}
          >
            {busy ? (
              <Icon icon={Loader2} size={14} className="spin" />
            ) : (
              <Icon icon={Plus} size={14} />
            )}{" "}
            立即备份
          </button>
        </div>
      </div>

      <div className="card flush backup-list-card">
        <div className="card-title-row">
          <div className="backup-list-title">
            <Icon icon={ArchiveRestore} size={16} /> 备份列表
            {restoring && <span className="support" role="status">正在恢复…</span>}
            {(readError || restoreError) && <button className="btn ghost sm" type="button" aria-haspopup="dialog"
              onClick={() => setBackupNoticeOpen(true)}>备份事项</button>}
          </div>
          <button className="btn secondary sm" type="button" disabled={busy || loading} onClick={() => {
            clear();
            setPathNotice("");
            void load();
          }}>
            重新读取备份
          </button>
        </div>
        {!status && loading ? (
          <div className="backup-empty" role="status">
            <Icon icon={Loader2} size={22} className="spin" /> 正在读取备份…
          </div>
        ) : !status ? (
          <div className="backup-empty">
            备份列表尚未读取，请重新读取。
          </div>
        ) : backups.length === 0 ? (
          <div className="backup-empty">
            还没有备份。点击上方"立即备份"创建第一份。
          </div>
        ) : (
          <div className="data-table-wrap backup-list-scroll">
            <table className="data-table history-table">
              <thead>
                <tr>
                  <th>备份时间</th>
                  <th>大小</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {backups.map((backup) => (
                  <tr key={backup.name}>
                    <td>{formatDateTime(backup.created_at)}</td>
                    <td className="numeric">{formatBytes(backup.size_bytes)}</td>
                    <td>
                      <div className="backup-actions">
                        <button
                          className="btn secondary xs"
                          type="button"
                          disabled={actionsUnavailable}
                          onClick={() => { setActionError(null); setRestoreTarget(backup); }}
                          title="恢复到这个备份时间点的数据"
                        >
                          <Icon icon={ArchiveRestore} style={{ width: "12px", height: "12px" }} /> 恢复
                        </button>
                        <button
                          className="btn ghost xs danger-btn"
                          type="button"
                          disabled={actionsUnavailable}
                          onClick={() => { setActionError(null); setDeleteTarget(backup); }}
                          title="删除这个备份文件"
                        >
                          <Icon icon={Trash2} style={{ width: "12px", height: "12px" }} /> 删除
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <ConfirmDialog
        open={restoreTarget !== null}
        title="恢复备份"
        description={`当前数据将恢复到「${formatDateTime(restoreTarget?.created_at ?? "")}」。知意会保留恢复前的数据副本。`}
        buttonLabel="确认恢复"
        busy={busy}
        error={actionError}
        onConfirm={() => void handleRestore()}
        onClose={() => setRestoreTarget(null)}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        title="删除备份"
        description={`将永久删除「${formatDateTime(deleteTarget?.created_at ?? "")}」的备份文件。`}
        buttonLabel="确认删除"
        busy={busy}
        error={actionError}
        onConfirm={() => void handleDelete()}
        onClose={() => setDeleteTarget(null)}
      />
    </div>
  );
}
