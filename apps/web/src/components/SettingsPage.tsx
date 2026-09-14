import { Disclosure } from "./Disclosure";
import { useEffect, useState } from "react";
import {
  ChevronDown,
  Cpu,
  FileCog,
  FolderOpen,
  Layers,
  ListChecks,
  MonitorCog,
  Rocket,
  RotateCcw,
  ShieldCheck,
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
import { ClearLocalData } from "./ClearLocalData";
import { Icon } from "./Icon";
import { ExistingLocalModelSettings } from "./ExistingLocalModelSettings";
import { ModelProfilesSettings } from "./ModelProfilesSettings";
import { SmartPoolSettings } from "./SmartPoolSettings";
import { desktopApi, isDesktopApp } from "../desktop";

type Theme = "auto" | "light" | "dark";

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
        <Icon
          icon={ChevronDown}
          size={15}
          className={`settings-card-caret${open ? "" : " collapsed"}`}
        />
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
  onModelStatusChanged,
}: {
  onShowOnboarding?: () => void;
  onModelStatusChanged?: () => void;
} = {}) {
  const [modelStatus, setModelStatus] = useState<ModelStatus | null>(null);
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [settings, setSettings] = useState<SystemSettings | null>(null);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [clearOpen, setClearOpen] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [clearBusy, setClearBusy] = useState(false);
  const [resetBusy, setResetBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [exportDirectory, setExportDirectory] = useState<string | null>(null);
  const [theme, setTheme] = useState<Theme>(
    (localStorage.getItem("theme") as Theme | null) ?? "auto",
  );

  useEffect(() => {
    Promise.all([getModelStatus(), getSystemStatus()])
      .then(([model, system]) => {
        setModelStatus(model);
        setSystemStatus(system);
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : "加载失败"));
  }, []);

  useEffect(() => {
    const bridge = desktopApi();
    if (bridge) void bridge.get_export_directory().then(setExportDirectory);
  }, []);

  useEffect(() => {
    getSystemSettings()
      .then(setSettings)
      .catch((reason) =>
        setSettingsError(reason instanceof Error ? reason.message : "加载设置失败"));
  }, []);

  useEffect(() => {
    const anchor = sessionStorage.getItem("zhiyi-settings-anchor");
    if (!anchor) return;
    sessionStorage.removeItem("zhiyi-settings-anchor");
    const cardId = anchor === "smart-pool-settings" ? "smart-pool" : anchor;
    setCollapsed((current) => ({ ...current, [cardId]: false }));
    requestAnimationFrame(() => {
      document.getElementById(`settings-${cardId}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }, []);

  function toggleCard(id: string) {
    setCollapsed((current) => ({ ...current, [id]: !current[id] }));
  }

  function changeTheme(nextTheme: Theme) {
    setTheme(nextTheme);
    localStorage.setItem("theme", nextTheme);
    const isDark = nextTheme === "dark"
      || (nextTheme === "auto"
        && typeof window.matchMedia === "function"
        && window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.classList.toggle("dark", isDark);
  }

  const toggleSetting = async (
    key: "image_convert" | "office_convert" | "word_include_images" | "allow_limited_input",
  ) => {
    if (!settings) return;
    const next = { ...settings, [key]: !settings[key] };
    setSettings(next);
    try {
      await updateSystemSettings(next);
      setNotice(
        `已${next[key] ? "开启" : "关闭"}${
          key === "image_convert"
            ? "图片自动格式转换"
            : key === "office_convert"
              ? "Office 文档自动转换"
              : key === "word_include_images" ? "Word 内嵌图片" : "限制发送给模型的内容"
        }，新上传的文件立即生效。`,
      );
    } catch (reason) {
      setSettingsError(reason instanceof Error ? reason.message : "保存设置失败");
      setSettings(settings);
    }
  };

  async function saveLimit(key: "upload_limit_mb" | "input_text_limit" | "input_page_limit" | "input_row_limit" | "input_docx_image_limit", raw: string) {
    if (!settings) return;
    const value = Number(raw);
    if (!Number.isSafeInteger(value) || value < 1) { setSettingsError("请输入大于零的整数。"); return; }
    if (settings[key] === value) return;
    try { const saved = await updateSystemSettings({ [key]: value }); setSettings(saved); setSettingsError(null); }
    catch (reason) { setSettingsError(reason instanceof Error ? reason.message : "保存失败，请重试。"); }
  }

  const handleClearHistory = async () => {
    setClearBusy(true);
    try {
      const result = await clearHistory("清除历史");
      setClearOpen(false);
      setNotice(
        result.cleared_count > 0
          ? `已清理 ${result.cleared_count} 条历史任务记录及其原文件。`
          : "没有可清理的历史记录。",
      );
    } catch (reason) {
      setSettingsError(reason instanceof Error ? reason.message : "清理历史失败");
    } finally {
      setClearBusy(false);
    }
  };

  const handleResetSettings = async () => {
    setResetBusy(true);
    try {
      localStorage.removeItem("theme");
      setResetOpen(false);
      setNotice("主题已恢复为跟随系统，页面正在刷新…");
      setTimeout(() => window.location.reload(), 800);
    } finally {
      setResetBusy(false);
    }
  };

  const isLocalProvider =
    modelStatus?.provider === "lm_studio" || modelStatus?.provider === "ollama";

  return (
    <div className="view">
      <div className="page-header">
        <div className="eyebrow">配置</div>
        <h1>系统设置</h1>
        <div className="support">管理 AI 服务、数据存储和本机使用偏好。</div>
      </div>

      {error && <div className="callout danger" style={{ marginBottom: 16 }}>{error}</div>}
      {notice && (
        <div className="callout" style={{ marginBottom: 16, borderColor: "var(--color-success-300)", background: "var(--color-success-50)", color: "var(--color-success-700)" }}>
          {notice}
        </div>
      )}

      <div className="settings-stack">
        <div className="task-col">
          <SettingsCard
            collapsed={collapsed}
            icon={MonitorCog}
            id="status"
            onToggle={toggleCard}
            support="区分程序、任务处理器与 AI 服务，避免把模型离线误报成程序损坏。"
            title="当前运行状态"
          >
            <div className="settings-toggles">
              <StatusRow label="本地程序" message={systemStatus?.api.message ?? "正在检查…"} ready={systemStatus?.api.connected} />
              <StatusRow label="任务处理器" message={systemStatus?.worker.message ?? "正在检查…"} ready={systemStatus?.worker.connected} />
              <StatusRow
                label="AI 服务"
                message={modelStatus?.configuration_error ?? systemStatus?.model.message ?? "正在检查…"}
                ready={modelStatus?.connected}
              />
              <div className="setting-row">
                <div>
                  <div className="settings-name">当前页面地址</div>
                  <div className="small muted">正式安装版由同一个本地端口提供网页和 API。</div>
                </div>
                <div className="small mono">{window.location.origin}</div>
              </div>
            </div>
          </SettingsCard>

          <SettingsCard
            collapsed={collapsed}
            icon={Cpu}
            id="ai"
            onToggle={toggleCard}
            support="方案版本、系统密钥和排队任务快照由后端统一保护。"
            title="AI 服务配置"
          >
            <ModelProfilesSettings onActiveProfileChanged={onModelStatusChanged} />
          </SettingsCard>

          <SettingsCard
            collapsed={collapsed}
            icon={ListChecks}
            id="smart-pool"
            onToggle={toggleCard}
            support="智能匹配只在预选池内选模板；勾选太杂或用途重叠容易匹配错。"
            title="智能匹配预选池"
          >
            <SmartPoolSettings />
          </SettingsCard>

          <SettingsCard
            collapsed={collapsed}
            icon={Rocket}
            id="onboarding"
            onToggle={toggleCard}
            support="可随时重新查看首次使用引导：上传 → 提取 → 保存到表 → 数据仓库。"
            title="使用引导"
          >
            <div className="setting-row">
              <div>
                <div className="settings-name">重新查看引导</div>
                <div className="small muted">带你走一遍核心流程，也可以用示例发票直接试一次</div>
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
            support="管理已经安装的 LM Studio 或 Ollama：启动服务、设置上下文、加载和卸载模型。"
            title="本地模型管理"
          >
            <ExistingLocalModelSettings />
          </SettingsCard>

          <SettingsCard
            collapsed={collapsed}
            icon={SunMoon}
            id="appearance"
            onToggle={toggleCard}
            support="外观偏好只保存在当前浏览器，不改变业务数据。"
            title="外观"
          >
            <div className="segmented" role="group" aria-label="界面主题">
              {(["auto", "light", "dark"] as const).map((value) => (
                <button key={value} type="button" className={theme === value ? "active" : ""} onClick={() => changeTheme(value)}>
                  {value === "auto" ? "跟随系统" : value === "light" ? "浅色" : "深色"}
                </button>
              ))}
            </div>
          </SettingsCard>

          {/* 导出/入文件配置：桌面导出位置与上传格式准入 */}
          <SettingsCard
            collapsed={collapsed}
            icon={FileCog}
            id="formats"
            onToggle={toggleCard}
            support="图片类（webp/bmp/tiff/gif）自动转 PNG；Word/Excel/文本按原始结构提取文字并交给模型。Word 默认只提取文本，可开启连同内嵌图片一起识别。关闭总开关后对应格式将无法上传。"
            title="导出/入文件配置"
          >
            <div className="settings-toggles">
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
                      setNotice(`默认导出位置已改为 ${selected}`);
                    }
                  }}><Icon icon={FolderOpen} size={13} /> 选择文件夹</button>
                </div>
              )}
              <div className="setting-row">
                <div>
                  <div className="settings-name">Office 文档自动转换</div>
                  <div className="small muted">Word / Excel / 纯文本转为文字后识别；关闭则这些格式无法上传</div>
                </div>
                <button
                  aria-label="Office 文档自动转换"
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
                  <div className="small muted">默认只提取文本；开启后文字与文档里的图片（如发票截图）一起交给模型</div>
                </div>
                <button
                  aria-label="Word 内嵌图片"
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
                  <div className="small muted">webp → jpg、tiff → png，确保能传给大模型</div>
                </div>
                <button
                  aria-label="图片自动格式转换"
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
                <label className="input-limit-field">单文件上传上限
                  <span className="input-with-unit"><input type="number" min="1" defaultValue={settings?.upload_limit_mb ?? 50} key={`upload-${settings?.upload_limit_mb}`} onBlur={(event) => void saveLimit("upload_limit_mb", event.target.value)} /><span>MB</span></span>
                </label>
                <p className="small muted">防止误传过大的文件。超过此大小会停止上传，不代表 AI 无法处理。</p>
                <div className="setting-row">
                  <div><div className="settings-name">限制发送给模型的内容</div>
                    <p className="small muted">开启后只提供文件开头，到上限即停止，结果可能缺少信息。原件仍完整保存，具体范围可在来源详情查看。</p></div>
                  <button aria-label="限制发送给模型的内容" aria-pressed={Boolean(settings?.allow_limited_input)} className={`toggle ${settings?.allow_limited_input ? "on" : "off"}`} type="button" disabled={!settings} onClick={() => void toggleSetting("allow_limited_input")}><span /></button>
                </div>
                {settings?.allow_limited_input && <div className="input-limits-grid">{([
                  ["input_text_limit", "从开头读取文字", "字符", 20000, 1, 100000],
                  ["input_page_limit", "PDF 页数／多帧图片帧数", "页／帧", 10, 1, 100],
                  ["input_row_limit", "Excel 非空行", "行", 500, 1, 10000],
                  ["input_docx_image_limit", "Word 内嵌图片", "张", 10, 1, 100],
                ] as const).map(([key, label, unit, fallback, min, max]) => <label className="input-limit-field" key={`${key}-${settings[key]}`}>
                  {label}<span className="input-with-unit"><input aria-label={label} type="number" min={min} defaultValue={settings[key] ?? fallback} onBlur={(event) => void saveLimit(key, event.target.value)} /><span>{unit}</span></span>
                  <input aria-label={`${label}滑块`} type="range" min={min} max={Math.max(max, settings[key] ?? fallback)} defaultValue={settings[key] ?? fallback} onPointerUp={(event) => void saveLimit(key, event.currentTarget.value)} onKeyUp={(event) => void saveLimit(key, event.currentTarget.value)} />
                </label>)}</div>}
                <Disclosure className="reading-help" title="如何读取">
                  <p>关闭限制时，知意提供全部可读取内容，由 AI 服务决定能否接受；超时或服务拒绝会明确提示，不会偷偷截取后重试。</p>
                  <p>PDF 从第 1 页、多帧图片从第 1 帧开始连续读取。Excel 按工作表顺序累计非空行，同时受文字上限限制；Word 达到文字或图片上限即停止。达到上限后，后续内容不会交给 AI，跨越读取边界的信息可能不完整。</p>
                  <p>实际读取位置可在结果的来源详情中查询。安全解压和像素限制仍生效。设置只影响新上传文件，普通重试沿用原设置；选择按当前读取设置重试，才会重新确定范围。</p>
                </Disclosure>
              </div>
              {settingsError && <div className="small danger-text">{settingsError}</div>}
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
                  <div className="small muted">删除已完成/失败/取消任务及内部原件；数据表保留但断开追溯，外部副本保留</div>
                </div>
                <button className="btn danger sm" type="button" onClick={() => setClearOpen(true)}>
                  <Icon icon={Trash2} size={13} /> 清理
                </button>
              </div>
              <div className="setting-row">
                <div>
                  <div className="settings-name">重置界面主题</div>
                  <div className="small muted">恢复为跟随系统并刷新页面；不改变模型配置和业务数据</div>
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
        description="将删除全部已完成、失败、已取消任务的处理历史、校验记录和知意内部原件。数据表中已确认的数据保留，但会断开与原文件的追溯。已导出的外部副本和备份保留。此操作不可恢复。"
        confirmText="清除历史"
        buttonLabel="确认清理"
        busy={clearBusy}
        onConfirm={() => void handleClearHistory()}
        onClose={() => setClearOpen(false)}
      />
      <ConfirmDialog
        open={resetOpen}
        title="重置界面主题"
        description="将把主题恢复为跟随系统并刷新页面；模型方案与业务数据不受影响。"
        confirmText="重置"
        buttonLabel="确认重置"
        busy={resetBusy}
        onConfirm={() => void handleResetSettings()}
        onClose={() => setResetOpen(false)}
      />

      <div className="settings-footnote" style={{ marginTop: 16 }}>
        <Icon icon={ShieldCheck} size={13} /> 首版接收 PDF、JPG、PNG 及已开启转换的图片与 Office 文档；本地模型任务固定串行。危险操作均有二次确认。
      </div>
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
      <span className={`badge ${ready ? "live" : "muted"}`}>{ready ? "可用" : "需检查"}</span>
    </div>
  );
}
