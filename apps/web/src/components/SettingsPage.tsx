import { deletionNotice } from "../deletionNotice";
import { Disclosure } from "./Disclosure";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Cpu,
  FileCog,
  FolderOpen,
  Layers,
  Info,
  ListChecks,
  MonitorCog,
  Rocket,
  RotateCcw,
  SunMoon,
  Trash2,
} from "lucide-react";
import {
  clearHistory,
  getModelStatus,
  getSystemSettings,
  getSystemStatus,
  updateSystemSettings,
} from "../api";
import type { ModelStatus, SystemSettings, SystemStatus } from "../types";
import { ConfirmDialog } from "./ConfirmDialog";
import { NoticeDialog } from "./NoticeDialog";
import { Toast, useToast } from "./Toast";
import { ClearLocalData } from "./ClearLocalData";
import { Icon } from "./Icon";
import { CollapseChevron } from "./CollapseChevron";
import { ExistingLocalModelSettings } from "./ExistingLocalModelSettings";
import { ModelProfilesSettings } from "./ModelProfilesSettings";
import { SmartPoolSettings } from "./SmartPoolSettings";
import { desktopApi, isDesktopApp } from "../desktop";
import type { useUpdates } from "../useUpdates";
import "./cards.css";

type Theme = "auto" | "light" | "dark";
type LimitKey = "upload_limit_mb" | "input_text_limit" | "input_page_limit" | "input_row_limit" | "input_docx_image_limit";

function SettingLimitField({ label, unit, value, fallback, disabled, rangeMax, onCommit }: {
  label: string; unit: string; value?: number; fallback?: number; disabled?: boolean; rangeMax?: number;
  onCommit: (raw: string) => Promise<number | null>;
}) {
  const [draft, setDraft] = useState(String(value ?? fallback ?? ""));
  const dirty = useRef(false);
  const revision = useRef(0);
  useEffect(() => {
    if (!dirty.current) setDraft(String(value ?? fallback ?? ""));
  }, [value, fallback]);
  const edit = (raw: string) => {
    dirty.current = true;
    revision.current += 1;
    setDraft(raw);
  };
  const commit = async (raw: string) => {
    const submitted = revision.current;
    const saved = await onCommit(raw);
    if (saved !== null && submitted === revision.current) {
      dirty.current = false;
      setDraft(String(saved));
    }
  };
  return <label className="input-limit-field">
    {label}<span className="input-with-unit"><input aria-label={label} type="number" min="1" disabled={disabled}
      value={draft} onChange={event => edit(event.target.value)} onBlur={event => void commit(event.target.value)} /><span>{unit}</span></span>
    {rangeMax !== undefined && <input aria-label={`${label}滑块`} type="range" min="1" disabled={disabled}
      max={Math.max(rangeMax, value ?? fallback ?? 1, Number(draft) || 1)} value={draft || String(value ?? fallback ?? 1)}
      onChange={event => edit(event.target.value)} onPointerUp={event => void commit(event.currentTarget.value)}
      onKeyUp={event => {
        if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown"].includes(event.key)) {
          void commit(event.currentTarget.value);
        }
      }} />}
  </label>;
}

function readTheme(): Theme {
  // Keep a theme applied during this page session when storage is unavailable.
  const applied = document.documentElement.dataset.zhiyiThemePreference;
  if (applied === "auto" || applied === "light" || applied === "dark") return applied;
  try {
    const saved = localStorage.getItem("theme");
    return saved === "light" || saved === "dark" ? saved : "auto";
  } catch { return "auto"; }
}

interface CardProps {
  id: string;
  title: string;
  icon: typeof Cpu;
  support?: string;
  collapsed: Record<string, boolean>;
  onToggle: (id: string) => void;
  children: React.ReactNode;
}

function SettingsCard({ id, title, icon, support, collapsed, onToggle, children }: CardProps) {
  const open = !collapsed[id];
  return (
    <div className="card settings-card" id={`settings-${id}`}>
      <button
        type="button"
        className="settings-card-head"
        aria-expanded={open}
        onClick={() => onToggle(id)}
      >
        <span className="settings-card-title" role="heading" aria-level={3}>
          <Icon icon={icon} size={16} />
          {title}
        </span>
        <CollapseChevron expanded={open} size={15} className="settings-card-caret" />
      </button>
      <div className={`collapse${open ? " open" : ""}`} inert={!open} aria-hidden={!open}>
        <div className="collapse-content">
          <div className="settings-card-body">
            {support ? <div className="support settings-card-support">{support}</div> : null}
            {children}
          </div>
        </div>
      </div>
    </div>
  );
}

export function SettingsPage({
  onShowOnboarding,
  onResumeOnboarding,
  onModelStatusChanged,
  settingsFocusRequest,
  updates,
}: {
  onShowOnboarding?: () => void;
  onResumeOnboarding?: () => void;
  onModelStatusChanged?: () => void;
  settingsFocusRequest?: { section: "local" | "ai" | "about"; revision: number };
  updates?: ReturnType<typeof useUpdates>;
} = {}) {
  const [modelStatus, setModelStatus] = useState<ModelStatus | null>(null);
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [settings, setSettings] = useState<SystemSettings | null>(null);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [settingsLoading, setSettingsLoading] = useState(true);
  const [saveErrors, setSaveErrors] = useState<Partial<Record<keyof SystemSettings, string>>>({});
  const [savingCount, setSavingCount] = useState(0);
  const confirmedSettings = useRef<SystemSettings | null>(null);
  const saveQueues = useRef(new Map<keyof SystemSettings, Promise<unknown>>());
  const saveVersions = useRef<Partial<Record<keyof SystemSettings, number>>>({});
  const [statusLoading, setStatusLoading] = useState(true);
  const statusRequest = useRef(0);
  const [localModelRefresh, setLocalModelRefresh] = useState(0);
  const [clearOpen, setClearOpen] = useState(false);
  const [clearError, setClearError] = useState<string | null>(null);
  const [resetOpen, setResetOpen] = useState(false);
  const [clearBusy, setClearBusy] = useState(false);
  const [resetBusy, setResetBusy] = useState(false);
  const { toast, notify, clear } = useToast();
  const [statusNoticeOpen, setStatusNoticeOpen] = useState(false);
  const [themeNotice, setThemeNotice] = useState<string | null>(null);
  const [exportDirectory, setExportDirectory] = useState<string | null>(null);
  const [theme, setTheme] = useState<Theme>(readTheme);

  const loadStatus = useCallback(async () => {
    const request = ++statusRequest.current;
    setStatusLoading(true);
    setError(null);
    const [model, system] = await Promise.allSettled([getModelStatus(), getSystemStatus()]);
    if (request !== statusRequest.current) return;
    if (model.status === "fulfilled") setModelStatus(model.value);
    if (system.status === "fulfilled") setSystemStatus(system.value);
    const failures = [model, system].flatMap(result => result.status === "rejected"
      ? [result.reason instanceof Error ? result.reason.message : "状态读取失败"] : []);
    if (failures.length) setError([...new Set(failures)].join("；"));
    setStatusLoading(false);
  }, []);
  useEffect(() => {
    void loadStatus();
    return () => { statusRequest.current += 1; };
  }, [loadStatus]);
  const activeProfileChanged = useCallback(() => {
    setModelStatus(null);
    setSystemStatus(null);
    void loadStatus();
    setLocalModelRefresh(value => value + 1);
    onModelStatusChanged?.();
  }, [loadStatus, onModelStatusChanged]);

  useEffect(() => {
    const bridge = desktopApi();
    if (bridge) void bridge.get_export_directory().then(setExportDirectory);
  }, []);

  const loadSettings = useCallback(async () => {
    setSettingsLoading(true);
    setSettingsError(null);
    try {
      const loaded = await getSystemSettings();
      confirmedSettings.current = loaded;
      setSettings(loaded);
    }
    catch (reason) { setSettingsError(reason instanceof Error ? reason.message : "加载设置失败"); }
    finally { setSettingsLoading(false); }
  }, []);
  useEffect(() => { void loadSettings(); }, [loadSettings]);

  useEffect(() => {
    let anchor: string | null = settingsFocusRequest?.section ?? null;
    if (!anchor) {
      try { anchor = sessionStorage.getItem("zhiyi-settings-anchor"); }
      catch { return; }
    }
    if (!anchor) return;
    try { sessionStorage.removeItem("zhiyi-settings-anchor"); } catch { /* The selected section can still open. */ }
    const cardId = anchor === "smart-pool-settings" ? "smart-pool" : anchor;
    setCollapsed((current) => ({ ...current, [cardId]: false }));
    const frame = requestAnimationFrame(() => {
      const section = document.getElementById(`settings-${cardId}`);
      const reduced = typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      section?.scrollIntoView({ behavior: reduced ? "instant" : "smooth", block: "start" });
      section?.querySelector<HTMLButtonElement>(".settings-card-head")?.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [settingsFocusRequest]);

  function toggleCard(id: string) {
    setCollapsed((current) => ({ ...current, [id]: !current[id] }));
  }

  function applyTheme(nextTheme: Theme) {
    setTheme(nextTheme);
    document.documentElement.dataset.zhiyiThemePreference = nextTheme;
    const isDark = nextTheme === "dark"
      || (nextTheme === "auto"
        && typeof window.matchMedia === "function"
        && window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.classList.toggle("dark", isDark);
  }

  function changeTheme(nextTheme: Theme) {
    applyTheme(nextTheme);
    try {
      localStorage.setItem("theme", nextTheme);
      setThemeNotice(null);
    } catch {
      setThemeNotice("主题已在本次打开中生效；浏览器未能记住，刷新或重新打开后可能恢复原主题。");
    }
  }

  function persistSetting<Key extends keyof SystemSettings>(key: Key, value: SystemSettings[Key]): Promise<SystemSettings[Key] | null> {
    const version = (saveVersions.current[key] ?? 0) + 1;
    saveVersions.current[key] = version;
    const previous = saveQueues.current.get(key) ?? Promise.resolve();
    setSavingCount(count => count + 1);
    // Serialize each field, while allowing independent fields to save without
    // resending an optimistic snapshot of unrelated settings.
    const task = previous.then(async () => {
      try {
        const confirmed = confirmedSettings.current;
        const saved = confirmed?.[key] === value ? confirmed
          : await updateSystemSettings({ [key]: value } as Pick<SystemSettings, Key>);
        const savedValue = saved?.[key] ?? value;
        confirmedSettings.current = { ...confirmedSettings.current!, [key]: savedValue };
        if (saveVersions.current[key] === version) {
          setSettings(current => current ? { ...current, [key]: savedValue } : current);
          setSaveErrors(current => { const next = { ...current }; delete next[key]; return next; });
        }
        return savedValue;
      } catch (reason) {
        if (saveVersions.current[key] === version) {
          setSaveErrors(current => ({ ...current, [key]: reason instanceof Error ? reason.message : "保存失败，请重试。" }));
          setSettings(current => current && confirmedSettings.current
            ? { ...current, [key]: confirmedSettings.current[key] } : current);
        }
        return null;
      } finally {
        setSavingCount(count => Math.max(0, count - 1));
      }
    });
    saveQueues.current.set(key, task);
    void task.then(() => { if (saveQueues.current.get(key) === task) saveQueues.current.delete(key); });
    return task;
  }

  const toggleSetting = async (
    key: "image_convert" | "office_convert" | "word_include_images" | "allow_limited_input",
  ) => {
    if (!settings) return;
    const nextValue = !settings[key];
    setSettings(current => current ? { ...current, [key]: nextValue } : current);
    const pending = persistSetting(key, nextValue);
    const version = saveVersions.current[key];
    const saved = await pending;
    if (saved !== null && saveVersions.current[key] === version) {
      notify(
        `已${saved ? "开启" : "关闭"}${
          key === "image_convert"
            ? "图片自动格式转换"
            : key === "office_convert"
              ? "Office 文档自动转换"
              : key === "word_include_images" ? "Word 内嵌图片" : "限制发送给模型的内容"
        }，新上传的文件立即生效。`, "success",
      );
    }
  };

  async function saveLimit(key: LimitKey, raw: string): Promise<number | null> {
    if (!settings) return null;
    const value = Number(raw);
    if (!Number.isSafeInteger(value) || value < 1) {
      saveVersions.current[key] = (saveVersions.current[key] ?? 0) + 1;
      setSaveErrors(current => ({ ...current, [key]: "请输入大于零的整数。" }));
      return null;
    }
    const saved = await persistSetting(key, value);
    return typeof saved === "number" ? saved : null;
  }

  const handleClearHistory = async () => {
    setClearBusy(true);
    setClearError(null);
    try {
      const result = await clearHistory("清除历史");
      setClearOpen(false);
      notify(
        deletionNotice(result, result.cleared_count > 0
          ? `已清理 ${result.cleared_count} 个文件及其当前数据、原件和修订历史。`
          : "没有可清理的历史记录。"), "success",
      );
    } catch (reason) {
      setClearError(reason instanceof Error ? reason.message : "清理历史失败");
    } finally {
      setClearBusy(false);
    }
  };

  const handleResetSettings = async () => {
    setResetBusy(true);
    try {
      applyTheme("auto");
      let saved = false;
      try { localStorage.removeItem("theme"); saved = true; }
      catch {
        try { localStorage.setItem("theme", "auto"); saved = true; }
        catch { /* The current theme is already reset even without persistence. */ }
      }
      setResetOpen(false);
      setThemeNotice(saved ? null : "本次已恢复为跟随系统；浏览器未能保存重置，刷新或重新打开后可能恢复原主题。");
      notify(saved ? "主题已恢复为跟随系统。" : "本次主题已恢复为跟随系统，但浏览器未能保存重置。", saved ? "success" : "info");
    } finally {
      setResetBusy(false);
    }
  };

  return (
    <div className="view">
      {onResumeOnboarding && <div className="onboarding-return">
        <span>使用引导 · 连接服务</span>
        <button className="btn secondary sm" type="button" onClick={onResumeOnboarding}>返回引导</button>
      </div>}

      <Toast toast={toast} onDismiss={clear} />
      {statusNoticeOpen && error && <NoticeDialog title="运行状态读取失败" onClose={() => setStatusNoticeOpen(false)}
        actions={<button className="btn primary sm" type="button" disabled={statusLoading}
          onClick={() => { setStatusNoticeOpen(false); void loadStatus(); }}>重新检查状态</button>}>
        <p>{error}</p>
      </NoticeDialog>}

      <div className="settings-stack">
        <div className="task-col">
          {updates && <SettingsCard collapsed={collapsed} icon={Info} id="about" onToggle={toggleCard} title="关于知意">
            <div className="settings-toggles">
              <div className="setting-row">
                <div>
                  <div className="settings-name">当前版本 {updates.status ? `v${updates.status.current_version}` : ""}</div>
                  <div className="small muted" role="status">
                    {updates.error || (updates.updating === "downloading" ? "正在下载并校验更新…" :
                      updates.status?.install_failed ? "上次更新未完成，可重新安装。" :
                      updates.status?.downloaded_version ? `v${updates.status.downloaded_version} 已下载` :
                      updates.checking ? "正在检查…" : updates.status?.result?.available
                      ? `发现新版本 v${updates.status.result.latest_version}${updates.status.result.downloadable ? "" : " · 暂无可校验的安装包"}` : updates.status?.result
                        ? `当前没有更高版本 · 最新公开版 v${updates.status.result.latest_version}` : "检查公开发行的 Windows 版本，含 Beta。")}
                  </div>
                </div>
                <div className="row" style={{ gap: 8, flexWrap: "wrap", justifyContent: "flex-end" }}>
                  <button className="btn secondary sm" type="button" disabled={updates.checking || Boolean(updates.updating)}
                    onClick={() => void (updates.status ? updates.check() : updates.load())}>{updates.status ? "检查更新" : "读取版本"}</button>
                  {(updates.status?.downloaded_version || updates.status?.result?.downloadable && updates.status.result.available) &&
                    <button className="btn primary sm" type="button" disabled={Boolean(updates.updating) || updates.checking}
                      onClick={() => void updates.update()}>
                      {updates.updating === "restarting" ? "正在重启…" : updates.status?.downloaded_version ? "重启并更新" : updates.updating ? "下载中…" : "下载更新"}
                    </button>}
                </div>
              </div>
              <div className="setting-row">
                <div><div className="settings-name">自动检查更新</div>
                  <div className="small muted">开启后定期检查。下载完成后，可选择重启应用更新。</div>
                </div>
                <button aria-label="自动检查更新" aria-pressed={Boolean(updates.status?.auto_check)}
                  className={`toggle${updates.status?.auto_check ? " on" : ""}`} type="button"
                  disabled={!updates.status || updates.saving} onClick={() => void updates.toggle()}><span /></button>
              </div>
            </div>
          </SettingsCard>}
          <SettingsCard
            collapsed={collapsed}
            icon={MonitorCog}
            id="status"
            onToggle={toggleCard}
            title="当前运行状态"
          >
            <div className="settings-toggles">
              <StatusRow label="本机程序" message={systemStatus ? (systemStatus.api.connected ? "运行正常" : "暂时无法连接") : (statusLoading ? "正在检查…" : "暂时无法读取")} ready={systemStatus?.api.connected} />
              <StatusRow label="文件处理" message={systemStatus ? (systemStatus.worker.connected ? "已启动" : "尚未启动") : (statusLoading ? "正在检查…" : "暂时无法读取")} ready={systemStatus?.worker.connected} />
              <StatusRow
                label="AI 服务"
                message={modelStatus?.configuration_error ?? systemStatus?.model.message ?? (statusLoading ? "正在检查…" : "暂时无法读取")}
                ready={modelStatus?.connected}
              />
              {error && <button className="link-btn" type="button" aria-haspopup="dialog"
                onClick={() => setStatusNoticeOpen(true)}>运行状态读取失败</button>}
            </div>
          </SettingsCard>

          <SettingsCard
            collapsed={collapsed}
            icon={Cpu}
            id="ai"
            onToggle={toggleCard}
            support="配置提取模型、服务地址和识别参数。"
            title="AI 服务配置"
          >
            <ModelProfilesSettings onActiveProfileChanged={activeProfileChanged} />
          </SettingsCard>

          <SettingsCard
            collapsed={collapsed}
            icon={ListChecks}
            id="smart-pool"
            onToggle={toggleCard}
            title="智能匹配预选池"
          >
            <SmartPoolSettings />
          </SettingsCard>

          <SettingsCard
            collapsed={collapsed}
            icon={Rocket}
            id="onboarding"
            onToggle={toggleCard}
            title="使用引导"
          >
            <div className="setting-row">
              <div>
                <div className="settings-name">重新查看引导</div>
                <div className="small muted">了解核心流程，查看只读示例。</div>
              </div>
              <button
                className="btn secondary sm"
                type="button"
                onClick={() => onShowOnboarding?.()}
              >
                打开引导
              </button>
            </div>
          </SettingsCard>
        </div>

        <div className="task-col">
          <SettingsCard
            collapsed={collapsed}
            icon={Rocket}
            id="local"
            onToggle={toggleCard}
            support="启动本地服务，加载或卸载模型。"
            title="本地模型管理"
          >
            <ExistingLocalModelSettings refreshSignal={localModelRefresh} />
          </SettingsCard>

          <SettingsCard
            collapsed={collapsed}
            icon={SunMoon}
            id="appearance"
            onToggle={toggleCard}
            title="外观"
          >
            <div className="segmented" role="group" aria-label="界面主题">
              {(["auto", "light", "dark"] as const).map((value) => (
                <button key={value} type="button" aria-pressed={theme === value} className={theme === value ? "active" : ""} onClick={() => changeTheme(value)}>
                  {value === "auto" ? "跟随系统" : value === "light" ? "浅色" : "深色"}
                </button>
              ))}
            </div>
            {themeNotice && <p className="small muted" role="status">{themeNotice}</p>}
          </SettingsCard>

          {/* 导出/入文件配置：桌面导出位置与上传格式准入 */}
          <SettingsCard
            collapsed={collapsed}
            icon={FileCog}
            id="formats"
            onToggle={toggleCard}
            support="设置文件转换、下载位置与提取范围。"
            title="导出/入文件配置"
          >
            <div className="settings-toggles">
              {settingsLoading && !settings && <p className="small muted" role="status">正在读取文件设置…</p>}
              {isDesktopApp() && (
                <div className="setting-row export-directory-row">
                  <div>
                    <div className="settings-name">默认导出文件夹</div>
                    <div className="small mono muted">{exportDirectory ?? "正在读取…"}</div>
                    <div className="small muted">仅影响桌面版；浏览器版仍使用浏览器下载设置。</div>
                  </div>
                  <button className="btn secondary sm" type="button" onClick={async () => {
                    const selected = await desktopApi()?.choose_export_directory();
                    if (selected) {
                      setExportDirectory(selected);
                      notify(`默认导出位置已改为 ${selected}`, "success");
                    }
                  }}><Icon icon={FolderOpen} size={13} /> 选择文件夹</button>
                </div>
              )}
              <div className="setting-row">
                <div>
                  <div className="settings-name">Office 文档自动转换</div>
                  <div className="small muted">开启后可在文件提取上传 .docx、.xlsx、.txt 和 .md，并读取其中的文字。关闭后，文件提取不接收这四种格式。</div>
                </div>
                <button
                  aria-label="Office 文档自动转换"
                  aria-pressed={Boolean(settings?.office_convert)}
                  className={`toggle ${settings?.office_convert ? "on" : "off"}`}
                  type="button"
                  disabled={!settings}
                  onClick={() => void toggleSetting("office_convert")}
                >
                  <span />
                </button>
              </div>
              <div className="setting-row">
                <div>
                  <div className="settings-name">Word 内嵌图片</div>
                  <div className="small muted">默认仅提取文字。开启后，受支持的 .docx 正文图片也会发送给模型，需使用支持图片的模型。</div>
                </div>
                <button
                  aria-label="Word 内嵌图片"
                  aria-pressed={Boolean(settings?.word_include_images)}
                  className={`toggle ${settings?.word_include_images ? "on" : "off"}`}
                  type="button"
                  disabled={!settings || !settings.office_convert}
                  onClick={() => void toggleSetting("word_include_images")}
                >
                  <span />
                </button>
              </div>
              <div className="setting-row">
                <div>
                  <div className="settings-name">图片自动格式转换</div>
                  <div className="small muted">WebP、BMP、TIFF 和 GIF 转为 PNG，供模型读取</div>
                </div>
                <button
                  aria-label="图片自动格式转换"
                  aria-pressed={Boolean(settings?.image_convert)}
                  className={`toggle ${settings?.image_convert ? "on" : "off"}`}
                  type="button"
                  disabled={!settings}
                  onClick={() => void toggleSetting("image_convert")}
                >
                  <span />
                </button>
              </div>
              <div className="input-settings-panel">
                <h3>文件接收与读取</h3>
                <SettingLimitField label="单文件上传上限" unit="MB" disabled={!settings} value={settings?.upload_limit_mb}
                  onCommit={raw => saveLimit("upload_limit_mb", raw)} />
                <p className="small muted">超过上限的文件将停止上传。</p>
                <div className="setting-row">
                  <div><div className="settings-name">限制发送给模型的内容</div>
                    <p className="small muted">开启后只提供文件开头，到上限即停止，结果可能缺少信息。原件仍完整保存，具体范围可在来源详情查看。</p></div>
                  <button aria-label="限制发送给模型的内容" aria-pressed={Boolean(settings?.allow_limited_input)} className={`toggle ${settings?.allow_limited_input ? "on" : "off"}`} type="button" disabled={!settings} onClick={() => void toggleSetting("allow_limited_input")}><span /></button>
                </div>
                {settings?.allow_limited_input && <div className="input-limits-grid">{([
                  ["input_text_limit", "从开头读取文字", "字符", 20000, 100000],
                  ["input_page_limit", "PDF 页数／多帧图片帧数", "页／帧", 10, 100],
                  ["input_row_limit", "Excel 非空行", "行", 500, 10000],
                  ["input_docx_image_limit", "Word 内嵌图片", "张", 10, 100],
                ] as const).map(([key, label, unit, fallback, max]) => <SettingLimitField key={key}
                  label={label} unit={unit} value={settings[key]} fallback={fallback} rangeMax={max}
                  onCommit={raw => saveLimit(key, raw)} />)}</div>}
                <Disclosure className="reading-help" title="如何读取">
                  <p>关闭限制时，将全部可读取内容交给 AI；超时或超出服务限制时会提示失败。</p>
                  <p>从文件开头读取：PDF 和多帧图片按页，Excel 按工作表累计非空行，Word 按文字和图片上限。超出部分不参与本次提取。</p>
                  <p>实际读取位置可在结果的来源详情中查询。安全解压和像素限制仍生效。设置只影响新上传文件，普通重试沿用原设置；选择按当前读取设置重试，才会重新确定范围。</p>
                </Disclosure>
              </div>
              {settingsError && <div className="small danger-text" role="alert">
                <span>{settingsError}</span>{" "}
                {!settings && <button className="btn secondary sm" type="button" disabled={settingsLoading} onClick={() => void loadSettings()}>重试读取文件设置</button>}
              </div>}
              {savingCount > 0 && <p className="small muted" role="status">正在保存文件设置…</p>}
              {Object.entries(saveErrors).map(([key, message]) => <p className="small danger-text" role="alert" key={key}>{message}</p>)}
            </div>
          </SettingsCard>

          {/* 数据与存储：危险操作需要输入确认文字，参考 GitHub 删除仓库 */}
          <SettingsCard
            collapsed={collapsed}
            icon={Layers}
            id="data"
            onToggle={toggleCard}
            support="清理与重置不可撤销，操作前必须输入确认文字。"
            title="数据与存储"
          >
            <div className="settings-toggles">
              <div className="setting-row">
                <div>
                  <div className="settings-name">清理历史数据</div>
                  <div className="small muted">删除历史文件及其当前数据、修订和内部原件。合并表随原表更新，独立导入数据、外部归档和备份保留。</div>
                </div>
                <button className="btn danger sm" type="button" onClick={() => { setClearError(null); setClearOpen(true); }}>
                  <Icon icon={Trash2} size={13} /> 清理
                </button>
              </div>
              <div className="setting-row">
                <div>
                  <div className="settings-name">重置界面主题</div>
                  <div className="small muted">立即恢复为跟随系统；不改变模型配置和业务数据</div>
                </div>
                <button className="btn danger sm" type="button" onClick={() => setResetOpen(true)}>
                  <Icon icon={RotateCcw} size={13} /> 重置
                </button>
              </div>
              <ClearLocalData />
            </div>
          </SettingsCard>

        </div>
      </div>

      <ConfirmDialog
        open={clearOpen}
        title="清理历史数据"
        description="将删除已完成、待核对、失败和已取消文件的记录、当前数据、修订历史及内部原件。相关行也会从实时合并表中移除。独立导入数据、外部归档文件和备份保留。此操作不可恢复。"
        confirmText="清除历史"
        buttonLabel="确认清理"
        busy={clearBusy}
        error={clearError}
        onConfirm={() => void handleClearHistory()}
        onClose={() => setClearOpen(false)}
      />
      <ConfirmDialog
        open={resetOpen}
        title="重置界面主题"
        description="将把主题立即恢复为跟随系统；模型方案与业务数据不受影响。"
        confirmText="重置"
        buttonLabel="确认重置"
        busy={resetBusy}
        onConfirm={() => void handleResetSettings()}
        onClose={() => setResetOpen(false)}
      />

    </div>
  );
}

function StatusRow({ label, message, ready }: { label: string; message: string; ready?: boolean }) {
  return (
    <div className="setting-row">
      <div>
        <div className="settings-name">{label}</div>
        <div className="small muted">{message}</div>
      </div>
      <span className="badge muted">{ready === undefined ? "待确认" : ready ? "可用" : "需检查"}</span>
    </div>
  );
}
