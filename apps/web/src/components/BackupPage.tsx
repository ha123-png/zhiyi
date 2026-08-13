import { useCallback, useEffect, useState } from "react";
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
import { serverDate } from "../time";
import { ConfirmDialog } from "./ConfirmDialog";
import { Icon } from "./Icon";

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

async function waitForApiRestart(): Promise<void> {
  await new Promise((resolve) => window.setTimeout(resolve, 1200));
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    try {
      await getBackupStatus();
      return;
    } catch {
      await new Promise((resolve) => window.setTimeout(resolve, 500));
    }
  }
  throw new Error("应用重启超过 60 秒。请重新打开应用；当前数据不会因等待超时而丢失。");
}

export function BackupPage() {
  const [backups, setBackups] = useState<BackupRead[]>([]);
  const [status, setStatus] = useState<BackupStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [restoreTarget, setRestoreTarget] = useState<BackupRead | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<BackupRead | null>(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      const [backupList, backupStatus] = await Promise.all([
        getBackups(),
        getBackupStatus(),
      ]);
      setBackups(backupList);
      setStatus(backupStatus);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const runAction = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
    } finally {
      setBusy(false);
    }
  };

  const handleCreate = () =>
    runAction(async () => {
      await createBackup();
      setNotice("备份已创建。");
    });

  const handleRestore = () =>
    runAction(async () => {
      if (!restoreTarget) return;
      const result = await restoreBackup(restoreTarget.name);
      setRestoreTarget(null);
      if (result.scheduled) {
        setNotice("正在安全停止处理组件、恢复数据并重启应用，请稍候…");
        await waitForApiRestart();
        window.location.reload();
        return;
      }
      setNotice(
        result.restart_required
          ? "已恢复到备份时的状态。为让队列等组件同步，建议重启应用后再继续使用。"
          : "已恢复到备份时的状态。",
      );
    });

  const handleDelete = () =>
    runAction(async () => {
      if (!deleteTarget) return;
      await deleteBackup(deleteTarget.name);
      setDeleteTarget(null);
      setNotice("备份已删除。");
    });

  const freeLabel = status
    ? formatBytes(status.free_bytes)
    : "—";
  const lastLabel = status?.last_success_at
    ? formatDateTime(status.last_success_at)
    : "尚未备份过";

  return (
    <div className="view">
      <div className="page-header">
        <div className="eyebrow">数据安全</div>
        <h1>备份与恢复</h1>
        <div className="support">一键把任务、提取结果、数据表和原文件保存为完整备份；需要时恢复到一个备份时间点。</div>
      </div>

      {error && (
        <div className="callout danger" style={{ marginBottom: 16 }}>{error}</div>
      )}
      {notice && (
        <div className="callout success" style={{ marginBottom: 16 }}>{notice}</div>
      )}
      {status?.restore_message && !notice && (
        <div
          className={`callout ${status.restore_state === "failed" ? "danger" : "success"}`}
          style={{ marginBottom: 16 }}
        >
          {status.restore_message}
        </div>
      )}

      <div className="backup-stats-bar">
        <div className="backup-stat-item">
          <div className="backup-stat-value" title={lastLabel}>{lastLabel}</div>
          <div className="backup-stat-label">最近成功备份</div>
        </div>
        <div className="backup-stat-divider"></div>
        <div className="backup-stat-item">
          <div className="backup-stat-value">
            {status?.count ?? 0} / {status?.retention ?? 10}
          </div>
          <div className="backup-stat-label">备份数量 / 保留上限</div>
        </div>
        <div className="backup-stat-divider"></div>
        <div className="backup-stat-item">
          <div className="backup-stat-value">{freeLabel}</div>
          <div className="backup-stat-label">磁盘剩余空间</div>
        </div>
        <div className="backup-stat-divider"></div>
        <div className="backup-stat-item">
          <div className="backup-stat-value backup-dir-value" title={status?.backup_dir}>
            {status?.backup_dir ?? "—"}
          </div>
          <div className="backup-stat-label">备份存放位置</div>
        </div>
      </div>

      <div className="card flush" style={{ marginTop: 20, padding: 20 }}>
        <div className="backup-cta">
          <div className="backup-cta-copy">
            <div className="backup-cta-title">
              <Icon icon={DatabaseBackup} size={18} /> 立即创建备份
            </div>
            <div className="backup-cta-support">
              备份包含任务记录、提取结果、数据表和原文件，不包含模型密钥。
              最多保留 {status?.retention ?? 10} 份，超出时自动删除最旧的。
            </div>
          </div>
          <button
            className="btn primary"
            type="button"
            disabled={busy}
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

      <div className="card flush history-table-card" style={{ marginTop: 20 }}>
        <div className="card-title-row">
          <div className="backup-list-title">
            <Icon icon={ArchiveRestore} size={16} /> 备份列表
          </div>
          <div className="backup-list-hint">恢复前请先创建一份新备份，作为可回退的恢复演练点。</div>
        </div>
        {loading ? (
          <div className="hist-empty-row" style={{ padding: 40, textAlign: "center" }}>
            <Icon icon={Loader2} size={22} className="spin" />
          </div>
        ) : backups.length === 0 ? (
          <div className="hist-empty-row" style={{ padding: 40, textAlign: "center" }}>
            还没有备份。点击上方"立即备份"创建第一份。
          </div>
        ) : (
          <div className="data-table-wrap" style={{ border: 0, borderRadius: 0 }}>
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
                      <div className="hist-actions">
                        <button
                          className="btn secondary xs"
                          type="button"
                          disabled={busy}
                          onClick={() => setRestoreTarget(backup)}
                          title="恢复到这个备份时间点的数据"
                        >
                          <Icon icon={ArchiveRestore} style={{ width: "12px", height: "12px" }} /> 恢复
                        </button>
                        <button
                          className="btn ghost xs danger-btn"
                          type="button"
                          disabled={busy}
                          onClick={() => setDeleteTarget(backup)}
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
        description={`将把当前数据替换为「${formatDateTime(restoreTarget?.created_at ?? "")}」的备份内容。系统会自动把当前数据移到回滚目录（失败时自动还原，不覆盖当前数据）。建议先创建一份新备份，再执行恢复。恢复完成后建议重启应用。此操作不可撤销。`}
        buttonLabel="确认恢复"
        busy={busy}
        onConfirm={() => void handleRestore()}
        onClose={() => setRestoreTarget(null)}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        title="删除备份"
        description={`将删除「${formatDateTime(deleteTarget?.created_at ?? "")}」的备份文件。删除后无法用它恢复数据。此操作不可恢复。`}
        buttonLabel="确认删除"
        busy={busy}
        onConfirm={() => void handleDelete()}
        onClose={() => setDeleteTarget(null)}
      />
    </div>
  );
}
