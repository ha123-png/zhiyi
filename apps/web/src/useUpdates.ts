import { useCallback, useEffect, useRef, useState } from "react";
import { checkForUpdates, downloadAppUpdate, getUpdateStatus, saveAutoUpdateCheck, type UpdateStatus } from "./api";
import { desktopApi } from "./desktop";

export function useUpdates() {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [checking, setChecking] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [updating, setUpdating] = useState<"downloading" | "restarting" | null>(null);
  const checkInFlight = useRef(false);
  const lastCheck = useRef<number | undefined>(undefined);

  const check = useCallback(async () => {
    if (checkInFlight.current) return;
    checkInFlight.current = true;
    setChecking(true);
    setError("");
    try {
      const next = await checkForUpdates();
      lastCheck.current = Date.parse(next.checked_at);
      setStatus(current => current ? { ...current, result: next } : current);
    } catch {
      setError("暂时无法检查更新。");
    } finally {
      checkInFlight.current = false;
      setChecking(false);
    }
  }, []);

  const load = useCallback(async () => {
    setError("");
    try {
      const next = await getUpdateStatus();
      if (!next?.current_version) throw new Error();
      if (next.result) lastCheck.current = Date.parse(next.result.checked_at);
      setStatus(next);
    } catch {
      setError("暂时无法读取版本信息。");
    }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!status?.auto_check) return;
    if (lastCheck.current === undefined || Date.now() - lastCheck.current >= 86_400_000) void check();
    const timer = window.setInterval(() => void check(), 86_400_000);
    return () => window.clearInterval(timer);
  }, [status?.auto_check, check]);

  async function toggle() {
    if (!status || saving) return;
    setSaving(true);
    setError("");
    try {
      const next = await saveAutoUpdateCheck(!status.auto_check);
      setStatus(current => current ? { ...current, auto_check: next.auto_check } : current);
    } catch {
      setError("暂时无法保存自动检查设置。");
    } finally {
      setSaving(false);
    }
  }

  async function update() {
    if (updating) return;
    setError("");
    const restart = Boolean(status?.downloaded_version);
    setUpdating(restart ? "restarting" : "downloading");
    try {
      if (restart) {
        const apply = desktopApi()?.restart_with_update;
        if (!apply) throw new Error("请在 Windows 安装版中重启并更新。");
        const result = await apply();
        if (!result.ok) throw new Error(result.message || "无法启动更新。");
      } else {
        const next = await downloadAppUpdate();
        setStatus(current => current ? { ...current, ...next, install_failed: false } : current);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "更新暂时不可用。");
      setUpdating(null);
      return;
    }
    if (!restart) setUpdating(null);
  }

  return { status, checking, saving, error, check, load, toggle, updating, update };
}
