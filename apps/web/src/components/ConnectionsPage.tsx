import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  Boxes,
  CheckCircle2,
  ClipboardCopy,
  Code2,
  KeyRound,
  Loader2,
  RefreshCw,
  ShieldOff,
  Terminal,
  Webhook,
} from "lucide-react";
import {
  generateIntegrationKey,
  getIntegrationBaseUrl,
  getIntegrationSettings,
  getIntegrationStatus,
  getMcpRuntimeConfig,
  getTables,
  revokeIntegrationKey,
  saveIntegrationPermissions,
} from "../api";
import type { IntegrationSettings, McpRuntimeConfig } from "../api";
import type { DataTableRead } from "../types";
import { useUnsavedChanges } from "../useUnsavedChanges";
import { useModalFocus } from "../useModalFocus";
import { ConfirmDialog } from "./ConfirmDialog";
import { Icon } from "./Icon";
import { CollapseChevron } from "./CollapseChevron";
import { Toast, useToast } from "./Toast";

interface Endpoint {
  method: string;
  path: string;
  desc: string;
}

const endpoints: Endpoint[] = [
  { method: "GET", path: "/capabilities", desc: "查询能力声明与权限范围" },
  { method: "GET", path: "/tasks", desc: "分页查询任务，可按状态和文件名筛选" },
  { method: "GET", path: "/tasks/{id}/result", desc: "查询任务提取结果" },
  { method: "GET", path: "/tasks/{id}/file", desc: "下载任务原件" },
  { method: "GET", path: "/tasks/{id}/revisions", desc: "读取文件修订目录" },
  { method: "GET", path: "/tasks/{id}/revisions/{revision_id}", desc: "分页读取指定版本的字段变化" },
  { method: "POST", path: "/tasks", desc: "上传文件创建任务（写密钥）" },
  { method: "GET", path: "/tables", desc: "查询数据表列表" },
  { method: "GET", path: "/tables/{id}", desc: "查询表详情（分页行）" },
  { method: "GET", path: "/tables/{id}/revisions", desc: "查询独立记录的修订历史" },
  { method: "GET", path: "/tables/{id}/views", desc: "查询分 Sheet 视图" },
  {
    method: "GET",
    path: "/tables/{id}/views/{vid}",
    desc: "查询视图详情",
  },
  {
    method: "PATCH",
    path: "/tables/{id}/rows/{rid}",
    desc: "修改事实行（写密钥，需乐观锁版本）",
  },
];

const mcpFeatures = [
  {
    title: "结构化查询与聚合",
    desc: "按任务状态、文件名、模板和表格内容筛选；计数、求和与分组由数据库精确执行，不接收任意 SQL",
  },
  {
    title: "任务操作独立授权",
    desc: "暂停、恢复、重试、取消和待选模板处理单独开启，不等于可以修改事实数据",
  },
  {
    title: "文件访问受目录限制",
    desc: "导入和导出必须额外开启，并且只能访问用户配置的允许目录，默认根本不注册文件工具",
  },
  {
    title: "事实修改带版本保护",
    desc: "默认不开放；开启写事实后仍需提交期望版本，防止覆盖并发修改",
  },
];

function buildCurlExample(baseUrl: string) {
  return `# 查询任务列表（读密钥）
curl -H "Authorization: Bearer READ_TOKEN" \\
  ${baseUrl}/tasks

# 上传文件创建任务（写密钥）
curl -X POST -H "Authorization: Bearer WRITE_TOKEN" \\
  -F "file=@发票.pdf" -F "template_mode=smart" \\
  ${baseUrl}/tasks

# 修改数据行（写密钥，需带乐观锁版本）
# 将 COLUMN_KEY 替换为表详情 columns 中的 key，不是显示名称
curl -X PATCH -H "Authorization: Bearer WRITE_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"expected_version":1,"changes":{"COLUMN_KEY":"372.00"}}' \\
  ${baseUrl}/tables/TABLE_ID/rows/ROW_ID`;
}

function normalizeFileRoots(value: string | string[] | undefined): string[] {
  const roots = Array.isArray(value) ? value : (value ?? "").split(/\r?\n|;/);
  return roots.map((item) => item.trim()).filter(Boolean);
}

function permissionSnapshot(value: {
  data_scope?: "all" | "selected";
  table_ids?: string[];
  task_read?: boolean;
  template_read?: boolean;
  result_read?: boolean;
  revision_read?: boolean;
  original_read?: boolean;
  data_read?: boolean;
  task_control?: boolean;
  write_enabled?: boolean;
  file_access?: boolean;
  file_roots?: string | string[];
}) {
  return JSON.stringify({
    data_scope: value.data_scope ?? "all",
    table_ids: [...new Set(value.table_ids ?? [])].sort(),
    task_read: Boolean(value.task_read),
    template_read: Boolean(value.template_read),
    result_read: Boolean(value.result_read),
    revision_read: Boolean(value.revision_read),
    original_read: Boolean(value.original_read),
    data_read: Boolean(value.data_read),
    task_control: Boolean(value.task_control),
    write_enabled: Boolean(value.write_enabled),
    file_access: Boolean(value.file_access),
    file_roots: normalizeFileRoots(value.file_roots),
  });
}

interface CollapsibleCardProps {
  icon: typeof Boxes;
  title: string;
  support: string;
  defaultCollapsed?: boolean;
  children: React.ReactNode;
}

function CollapsibleCard({
  icon,
  title,
  support,
  defaultCollapsed = false,
  children,
}: CollapsibleCardProps) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  return (
    <div className={`card collapsible${collapsed ? " is-collapsed" : ""}`}>
      <button
        aria-expanded={!collapsed}
        className={`panel-title collapsible${collapsed ? " collapsed" : ""}`}
        onClick={() => setCollapsed((v) => !v)}
        type="button"
      >
        <div>
          <h3>
            <Icon icon={icon} size={16} />
            {title}
          </h3>
          <div className="support">{support}</div>
        </div>
        <CollapseChevron className="card-caret" expanded={!collapsed} size={17} />
      </button>
      <div className="card-body" inert={collapsed} aria-hidden={collapsed}>
        <div className="card-body-inner">{children}</div>
      </div>
    </div>
  );
}

export function ConnectionsPage() {
  const { toast, notify, clear } = useToast();
  const integrationBaseUrl = getIntegrationBaseUrl();
  const [status, setStatus] = useState<{
    enabled: boolean | null;
    message: string;
  }>({ enabled: null, message: "正在检测集成接口状态…" });
  const [statusLoading, setStatusLoading] = useState(true);
  const statusRequest = useRef(0);

  // 集成配置（密钥状态 + MCP 权限）
  const [settings, setSettings] = useState<IntegrationSettings | null>(null);
  const [settingsLoading, setSettingsLoading] = useState(true);
  const settingsRequest = useRef(0);
  const [mcpConfig, setMcpConfig] = useState<McpRuntimeConfig | null>(null);
  const [mcpConfigLoading, setMcpConfigLoading] = useState(true);
  const [mcpConfigError, setMcpConfigError] = useState<string | null>(null);
  const mcpConfigRequest = useRef(0);
  const [mcpCopied, setMcpCopied] = useState(false);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [settingsNotice, setSettingsNotice] = useState<string | null>(null);
  const [mcpError, setMcpError] = useState<string | null>(null);
  const [mcpNotice, setMcpNotice] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<"read" | "write" | null>(null);
  // 生成后的密钥明文：只在弹窗里出现一次，关闭后不可再查看
  const [revealedToken, setRevealedToken] = useState<{
    kind: string;
    token: string;
  } | null>(null);
  const revealedTokenInput = useRef<HTMLTextAreaElement>(null);
  const revealedTokenDialog = useModalFocus(
    revealedToken !== null,
    () => setRevealedToken(null),
    false,
    revealedTokenInput,
  );
  const [copied, setCopied] = useState(false);
  const readKeyButton = useRef<HTMLButtonElement>(null);
  const keyStatusRetry = useRef<HTMLButtonElement>(null);
  const restoreKeyStatusFocus = useRef(false);
  useLayoutEffect(() => {
    if (settingsLoading || !restoreKeyStatusFocus.current) return;
    restoreKeyStatusFocus.current = false;
    if (!settingsError && document.activeElement === document.body) readKeyButton.current?.focus();
  }, [settingsError, settingsLoading]);
  // 撤销确认
  const [revokeTarget, setRevokeTarget] = useState<"read" | "write" | null>(null);
  // 权限开关草稿
  const [permDraft, setPermDraft] = useState({
    taskRead: false,
    templateRead: false,
    resultRead: false,
    revisionRead: false,
    originalRead: false,
    dataRead: false,
    taskControl: false,
    writeEnabled: false,
    fileAccess: false,
    fileRoots: "",
  });
  const [savingPerms, setSavingPerms] = useState(false);
  const [tables, setTables] = useState<DataTableRead[]>([]);
  const [tablesLoading, setTablesLoading] = useState(false);
  const [hasLoadedTables, setHasLoadedTables] = useState(false);
  const [tableError, setTableError] = useState<string | null>(null);
  const tablesRequest = useRef(0);
  const [dataScope, setDataScope] = useState<"all" | "selected">("all");
  const [tableIds, setTableIds] = useState<string[]>([]);
  const unsavedChanges = useUnsavedChanges(() => settings !== null && permissionSnapshot({
    data_scope: dataScope,
    table_ids: tableIds,
    task_read: permDraft.taskRead,
    template_read: permDraft.templateRead,
    result_read: permDraft.resultRead,
    revision_read: permDraft.revisionRead,
    original_read: permDraft.originalRead,
    data_read: permDraft.dataRead,
    task_control: permDraft.taskControl,
    write_enabled: permDraft.writeEnabled,
    file_access: permDraft.fileAccess,
    file_roots: permDraft.fileRoots,
  }) !== permissionSnapshot(settings));
  const integrationStatusUnknown = status.enabled === null
    || (!status.enabled && !status.message.includes("集成接口未启用"));

  const applySettings = useCallback((next: IntegrationSettings) => {
    setSettings(next);
    setDataScope(next.data_scope ?? "all");
    setTableIds(next.table_ids ?? []);
    setPermDraft({
      taskRead: next.task_read,
      templateRead: next.template_read,
      resultRead: next.result_read,
      revisionRead: Boolean(next.revision_read),
      originalRead: Boolean(next.original_read),
      dataRead: next.data_read,
      taskControl: next.task_control,
      writeEnabled: next.write_enabled,
      fileAccess: next.file_access,
      fileRoots: (next.file_roots ?? []).join("\n"),
    });
  }, []);

  const refreshTables = useCallback(async () => {
    const request = ++tablesRequest.current;
    setTablesLoading(true);
    setTableError(null);
    try {
      const items = await getTables();
      if (request === tablesRequest.current) {
        setTables(items);
        setHasLoadedTables(true);
      }
    } catch (err) {
      if (request === tablesRequest.current) {
        setTableError(err instanceof Error ? err.message : "读取数据表列表失败");
      }
    } finally {
      if (request === tablesRequest.current) setTablesLoading(false);
    }
  }, []);

  const needsTableList = permDraft.dataRead || permDraft.writeEnabled ||
    permDraft.resultRead || permDraft.revisionRead || permDraft.originalRead;
  useEffect(() => {
    if (!needsTableList) {
      tablesRequest.current += 1;
      setTablesLoading(false);
      setTableError(null);
      return;
    }
    void refreshTables();
    return () => { tablesRequest.current += 1; };
  }, [needsTableList, refreshTables]);

  const refreshSettings = useCallback(async (syncPermissions = false) => {
    const request = ++settingsRequest.current;
    setSettingsLoading(true);
    try {
      const next = await getIntegrationSettings();
      if (request !== settingsRequest.current) return;
      if (syncPermissions) applySettings(next);
      else setSettings(next);
      setSettingsError(null);
    } catch (err) {
      if (request === settingsRequest.current) {
        setSettingsError(err instanceof Error ? err.message : "读取集成配置失败");
      }
    } finally {
      if (request === settingsRequest.current) setSettingsLoading(false);
    }
  }, [applySettings]);

  useEffect(() => () => { settingsRequest.current += 1; }, []);

  const refreshStatus = useCallback(async () => {
    const request = ++statusRequest.current;
    setStatus({ enabled: null, message: "正在检测集成接口状态…" });
    setStatusLoading(true);
    try {
      const next = await getIntegrationStatus();
      if (request === statusRequest.current) setStatus(next);
    } catch {
      if (request === statusRequest.current) setStatus({ enabled: false, message: "无法连接集成接口。" });
    } finally {
      if (request === statusRequest.current) setStatusLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshStatus();
    void refreshSettings(true);
    return () => { statusRequest.current += 1; };
  }, [refreshSettings, refreshStatus]);

  const refreshMcpConfig = useCallback(async () => {
    const request = ++mcpConfigRequest.current;
    setMcpConfigLoading(true);
    setMcpConfigError(null);
    setMcpCopied(false);
    try {
      const config = await getMcpRuntimeConfig();
      if (request === mcpConfigRequest.current) setMcpConfig(config);
    } catch (error) {
      if (request === mcpConfigRequest.current) {
        setMcpConfig(null);
        setMcpConfigError(`读取 MCP 配置失败：${error instanceof Error ? error.message : "请检查连接后重试。"}`);
      }
    } finally {
      if (request === mcpConfigRequest.current) setMcpConfigLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshMcpConfig();
    return () => { mcpConfigRequest.current += 1; };
  }, [refreshMcpConfig]);

  const handleGenerateKey = async (kind: "read" | "write") => {
    setBusyKey(kind);
    setSettingsError(null);
    setSettingsNotice(null);
    try {
      const result = await generateIntegrationKey(kind);
      setSettings(current => current ? { ...current, [kind === "read" ? "read_token_set" : "write_token_set"]: true,
        legacy_http_keys: current.legacy_http_keys?.filter(key => key !== kind) } : current);
      setRevealedToken(result);
      setCopied(false);
      await Promise.all([refreshSettings(), refreshStatus()]);
    } catch (err) {
      setSettingsError(err instanceof Error ? err.message : "生成密钥失败");
    } finally {
      setBusyKey(null);
    }
  };

  const handleRevokeKey = async () => {
    if (!revokeTarget) return;
    setBusyKey(revokeTarget);
    setSettingsError(null);
    try {
      await revokeIntegrationKey(revokeTarget);
      setSettings(current => current ? { ...current, [revokeTarget === "read" ? "read_token_set" : "write_token_set"]: false,
        legacy_http_keys: current.legacy_http_keys?.filter(key => key !== revokeTarget) } : current);
      setRevokeTarget(null);
      setSettingsNotice(
        `${revokeTarget === "read" ? "读" : "写"}密钥已撤销，HTTP API 立即生效。`,
      );
      await Promise.all([refreshSettings(), refreshStatus()]);
    } catch (err) {
      const message = err instanceof Error ? err.message : "撤销密钥失败";
      setSettingsError(message);
      notify(message, "error");
    } finally {
      setBusyKey(null);
    }
  };

  const copyToken = async () => {
    if (!revealedToken) return;
    try {
      await navigator.clipboard.writeText(revealedToken.token);
      setCopied(true);
    } catch {
      notify("复制失败，请手动选中密钥内容复制。", "error");
    }
  };

  const handleSavePermissions = async () => {
    setSavingPerms(true);
    setMcpError(null);
    setMcpNotice(null);
    try {
      const payload = {
        data_scope: dataScope,
        table_ids: tableIds,
        task_read: permDraft.taskRead,
        template_read: permDraft.templateRead,
        result_read: permDraft.resultRead,
        revision_read: permDraft.revisionRead,
        original_read: permDraft.originalRead,
        data_read: permDraft.dataRead,
        task_control: permDraft.taskControl,
        write_enabled: permDraft.writeEnabled,
        file_access: permDraft.fileAccess,
        file_roots: normalizeFileRoots(permDraft.fileRoots),
      };
      await saveIntegrationPermissions(payload);
      // Treat the successful submitted payload as the new baseline immediately.
      // If the follow-up GET fails, the saved form still remains clean and usable.
      applySettings({ ...settings!, ...payload });
      setMcpNotice("MCP 权限已保存；旧连接将拒绝继续操作，请在客户端重新连接以使用最新权限。");
      await refreshSettings(true);
    } catch (err) {
      setMcpError(err instanceof Error ? err.message : "保存权限失败");
    } finally {
      setSavingPerms(false);
    }
  };

  return (
    <section className="view connections-view">


      <div className="settings-stack">
        <div className="task-col">
          {/* API 端点 */}
          <CollapsibleCard
            icon={Terminal}
            support="外部程序调用 /api/integration/v1"
            title="API 端点"
          >
            <div className="api-endpoint-list">
              {endpoints.map((endpoint) => (
                <div
                  className="api-endpoint"
                  key={endpoint.method + endpoint.path}
                >
                  <span
                    className={`api-method ${endpoint.method.toLowerCase()}`}
                  >
                    {endpoint.method}
                  </span>
                  <div className="api-endpoint-body">
                    <code className="api-path">{endpoint.path}</code>
                    <div className="api-desc">{endpoint.desc}</div>
                  </div>
                </div>
              ))}
            </div>
          </CollapsibleCard>

          {/* 调用示例 */}
          <CollapsibleCard
            icon={Code2}
            support="使用 Bearer 密钥调用 /api/integration/v1"
            title="HTTP 调用示例"
          >
            <pre className="code-block">{buildCurlExample(integrationBaseUrl)}</pre>
          </CollapsibleCard>

          {/* 集成状态 */}
          <CollapsibleCard
            icon={Webhook}
            support={status.message}
            title="集成接口状态"
          >
            <div className="settings-toggles" aria-busy={statusLoading}>
              <div className="setting-row">
                <div>
                  <div className="settings-name">外部集成 API</div>
                  <div className="small muted">/api/integration/v1</div>
                </div>
                <span aria-live="polite" className={`badge ${status.enabled && !statusLoading ? "live" : "warn"}`}>
                  {statusLoading ? "正在检测" : integrationStatusUnknown ? "无法确认" : status.enabled ? "已启用" : "未启用"}
                </span>
              </div>
              {integrationStatusUnknown && !statusLoading && (
                <div className="callout danger" role="alert">
                  <span>{status.message}</span>
                  <button className="btn secondary xs" type="button" disabled={statusLoading} onClick={() => void refreshStatus()}>
                    {statusLoading ? "正在检测…" : "重新检测"}
                  </button>
                </div>
              )}
              <div className="setting-row">
                <div>
                  <div className="settings-name">监听地址</div>
                  <div className="small muted">
                    默认绑定 127.0.0.1，仅本机访问；改 0.0.0.0 前需重设认证
                  </div>
                </div>
              </div>
            </div>
          </CollapsibleCard>

          {/* 读写密钥 */}

          <CollapsibleCard
            icon={KeyRound}
            support="密钥写入本机配置文件，前端不保存原文；生成后只在弹窗显示一次"
            title="读写密钥"
          >
            <div className="settings-toggles">
              {settingsError && (
                <div className="callout danger" role="alert" style={{ marginBottom: 12 }}>
                  <span>{settingsError}</span>
                  <button className="btn secondary sm" type="button"
                    ref={keyStatusRetry}
                    aria-disabled={settingsLoading || busyKey !== null || savingPerms}
                    onClick={() => {
                      if (settingsLoading || busyKey !== null || savingPerms) return;
                      restoreKeyStatusFocus.current = document.activeElement === keyStatusRetry.current;
                      void refreshSettings(settings === null);
                    }}>
                    {settingsLoading ? "正在读取密钥状态…" : "重新读取密钥状态"}
                  </button>
                </div>
              )}
              {settingsNotice && (
                <div className="callout success" style={{ marginBottom: 12 }}>
                  {settingsNotice}
                </div>
              )}
              {(["read", "write"] as const).map(kind => {
                const label = kind === "read" ? "读密钥" : "写密钥";
                const enabled = settings?.[kind === "read" ? "read_token_set" : "write_token_set"];
                const legacy = settings?.legacy_http_keys?.includes(kind);
                return <div className="setting-row" key={kind}>
                  <div>
                    <div className="settings-name">{label}</div>
                    <div className="small muted">{kind === "read"
                      ? "查询任务、结果和数据表，下载原件并读取修订历史。"
                      : "包含读取能力，还可上传文件和修改数据行。"}</div>
                    {legacy && <div className="small muted">旧密钥沿用原权限，重新生成后使用上述权限。</div>}
                  </div>
                  <span className={`badge ${settings === null ? "muted" : enabled ? "live" : "warn"}`}>
                    {settings === null ? settingsLoading ? "正在读取" : "待确认" : enabled ? "已设置" : "未设置"}
                  </span>
                </div>;
              })}
              <div className="setting-row align-top">
                <div style={{ minWidth: 0 }}>
                  <div className="settings-name">密钥操作</div>
                  <div className="small muted" style={{ marginBottom: 8 }}>
                    密钥只用于 HTTP API。生成后立即生效，同类旧密钥随即失效。请在关闭弹窗前复制保存。
                  </div>
                  <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                    <button
                      ref={readKeyButton}
                      className="btn secondary sm"
                      disabled={busyKey !== null || savingPerms || settings === null}
                      onClick={() => void handleGenerateKey("read")}
                    >
                      <Icon icon={busyKey === "read" ? Loader2 : RefreshCw} size={13} className={busyKey === "read" ? "spin" : undefined} />
                      生成读密钥
                    </button>
                    <button
                      className="btn secondary sm"
                      disabled={busyKey !== null || savingPerms || settings === null}
                      onClick={() => void handleGenerateKey("write")}
                    >
                      <Icon icon={busyKey === "write" ? Loader2 : RefreshCw} size={13} className={busyKey === "write" ? "spin" : undefined} />
                      生成写密钥
                    </button>
                    <button
                      className="btn ghost sm danger-btn"
                      disabled={busyKey !== null || savingPerms || settings === null || !settings.read_token_set}
                      onClick={() => setRevokeTarget("read")}
                      title="撤销读密钥"
                    >
                      <Icon icon={ShieldOff} size={13} /> 撤销读
                    </button>
                    <button
                      className="btn ghost sm danger-btn"
                      disabled={busyKey !== null || savingPerms || settings === null || !settings.write_token_set}
                      onClick={() => setRevokeTarget("write")}
                      title="撤销写密钥"
                    >
                      <Icon icon={ShieldOff} size={13} /> 撤销写
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </CollapsibleCard>

        </div>

        <div className="task-col">
          {/* MCP 工具 */}
          <CollapsibleCard
            icon={Boxes}
            support="让支持 MCP 的客户端在授权范围内使用知意的数据与任务"
            title="MCP 工具"
          >
            <div className="settings-toggles">
              {mcpFeatures.map((feature) => (
                <div className="setting-row" key={feature.title}>
                  <div>
                    <div className="settings-name">{feature.title}</div>
                    <div className="small muted">{feature.desc}</div>
                  </div>
                </div>
              ))}
            </div>
            <p className="small muted">「读取原件」提供 <code>read_original_page</code>。「读取修订历史」提供文件版本和独立记录的修订查询。保存权限后请重新连接客户端。</p>
          </CollapsibleCard>

          {/* MCP 权限开关 */}
          <CollapsibleCard
            icon={Boxes}
            support="每种 AI 使用独立 MCP 进程；未开启的能力不会出现在工具列表中"
            title="MCP 权限"
          >
            <div className="settings-toggles" aria-busy={savingPerms}>
              {settings === null && (
                <div className="small muted" role="status">
                  {settingsError ? `暂时无法读取权限配置：${settingsError}` : "正在读取 MCP 权限配置…"}
                  {settingsError && (
                    <button className="btn secondary sm" type="button" disabled={settingsLoading} onClick={() => void refreshSettings(true)}>
                      {settingsLoading ? "正在读取…" : "重新读取"}
                    </button>
                  )}
                </div>
              )}
              {mcpError && (
                <div className="callout danger" style={{ marginBottom: 12 }}>
                  {mcpError}
                </div>
              )}
              {mcpNotice && (
                <div className="callout success" style={{ marginBottom: 12 }}>
                  {mcpNotice}
                </div>
              )}
              {([
                ["taskRead", "读取任务", "允许 AI 查看任务列表、状态和文件名"],
                ["templateRead", "读取模板", "允许 AI 查看模板、字段和校验规则"],
                ["resultRead", "读取文件结果", "读取当前数据、初次提取证据与校验问题"],
                ["revisionRead", "读取修订历史", "读取文件版本及修改前后值，可能包含已删除的内容"],
                ["originalRead", "读取原件", "按页或分段读取原件。外部 AI 客户端可能把内容发给云模型"],
                ["dataRead", "读取数据仓库", "允许 AI 查看数据表、数据行、视图并执行聚合查询"],
              ] as const).map(([key, label, description]) => (
                <div className="setting-row" key={key}>
                  <div>
                    <div className="settings-name">{label}</div>
                    <div className="small muted">{description}</div>
                  </div>
                  <button
                    aria-label={label}
                    aria-pressed={permDraft[key]}
                    className={`toggle ${permDraft[key] ? "on" : "off"}`}
                    type="button"
                    disabled={savingPerms || settings === null}
                    onClick={() => setPermDraft((d) => ({ ...d, [key]: !d[key] }))}
                  >
                    <span />
                  </button>
                </div>
              ))}
              <div className="setting-row">
                <div>
                  <div className="settings-name">任务控制</div>
                  <div className="small muted">允许 AI 暂停 / 恢复 / 重试 / 取消任务</div>
                </div>
                <button
                  aria-label="任务控制"
                  className={`toggle ${permDraft.taskControl ? "on" : "off"}`}
                  type="button"
                  disabled={savingPerms || settings === null}
                  onClick={() =>
                    setPermDraft((d) => ({ ...d, taskControl: !d.taskControl }))
                  }
                >
                  <span />
                </button>
              </div>
              <div className="setting-row">
                <div>
                  <div className="settings-name">写事实</div>
                  <div className="small muted">允许 AI 修改数据行；与 HTTP 写密钥无关，仍有版本保护</div>
                </div>
                <button
                  aria-label="写事实"
                  className={`toggle ${permDraft.writeEnabled ? "on" : "off"}`}
                  type="button"
                  disabled={savingPerms || settings === null}
                  onClick={() =>
                    setPermDraft((d) => ({ ...d, writeEnabled: !d.writeEnabled }))
                  }
                >
                  <span />
                </button>
              </div>
              {(permDraft.dataRead || permDraft.writeEnabled || permDraft.resultRead || permDraft.revisionRead || permDraft.originalRead) && <div className="setting-row align-top">
                <div style={{ width: "100%" }}>
                  <label className="settings-name" htmlFor="mcp-data-scope">允许访问的数据表</label>
                  <select id="mcp-data-scope" value={dataScope} disabled={savingPerms || settings === null} onChange={e => setDataScope(e.target.value as "all" | "selected")}>
                    <option value="all">全部数据表（包括以后新建的表）</option>
                    <option value="selected">只允许选中的表</option>
                  </select>
                  {dataScope === "selected" && <div className="mcp-table-scope">
                    {tablesLoading && !hasLoadedTables && <p className="small muted" role="status">正在读取数据表列表…</p>}
                    {tablesLoading && hasLoadedTables && <p className="small muted" role="status">正在更新列表；已选数据表暂时保留。</p>}
                    {tableError && <div className="callout danger mcp-table-error" role="alert">
                      <p>{hasLoadedTables ? "读取失败，仍显示上次成功读取的列表；已选数据表保持不变。" : "暂时无法读取数据表，暂不能判断这里是否为空。"} {tableError}</p>
                      <button className="btn secondary xs" type="button" disabled={tablesLoading} onClick={() => void refreshTables()}>
                        {tablesLoading ? "正在读取…" : "重新读取数据表"}
                      </button>
                    </div>}
                    {tables.map(table => <label key={table.id}>
                      <input type="checkbox" disabled={savingPerms || settings === null} checked={tableIds.includes(table.id)} onChange={e => setTableIds(ids => e.target.checked ? [...ids, table.id] : ids.filter(id => id !== table.id))} />{table.name}
                    </label>)}
                    {hasLoadedTables && !tablesLoading && !tables.length && <p className="small muted">还没有可选的数据表。</p>}
                    <p className="small muted">已选 {tableIds.length} 张表。未选择时不允许访问任何数据表。</p>
                  </div>}
                  <p className="small muted">此范围也约束文件结果、修订历史和原件。授权合并表不会授予其原表的整份文件权限。</p>
                </div>
              </div>}
              <div className="setting-row">
                <div>
                  <div className="settings-name">文件访问</div>
                  <div className="small muted">允许 AI 从指定目录导入文件；导出表格还需开启“读取数据仓库”</div>
                </div>
                <button
                  aria-label="文件访问"
                  className={`toggle ${permDraft.fileAccess ? "on" : "off"}`}
                  type="button"
                  disabled={savingPerms || settings === null}
                  onClick={() =>
                    setPermDraft((d) => ({ ...d, fileAccess: !d.fileAccess }))
                  }
                >
                  <span />
                </button>
              </div>
              {permDraft.fileAccess && (
                <div className="setting-row align-top">
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div className="settings-name">允许目录</div>
                    <div className="small muted" style={{ marginBottom: 6 }}>
                      每行一个目录，AI 只能访问这些目录（不会触及目录外文件）
                    </div>
                    <textarea
                      aria-label="允许目录（每行一个）"
                      className="form-input"
                      rows={3}
                      value={permDraft.fileRoots}
                      disabled={savingPerms || settings === null}
                      onChange={(e) =>
                        setPermDraft((d) => ({ ...d, fileRoots: e.target.value }))
                      }
                      placeholder={"D:\\我的单据\\导入\nD:\\导出"}
                    />
                  </div>
                </div>
              )}
              <div className="setting-row">
                <div>
                  <div className="settings-name">保存并生效</div>
                  <div className="small muted">保存立即生效；MCP 客户端重新连接后刷新工具列表</div>
                </div>
                <button
                  className="btn primary sm"
                  disabled={savingPerms || busyKey !== null || settings === null}
                  onClick={() => void handleSavePermissions()}
                >
                  <Icon icon={savingPerms ? Loader2 : CheckCircle2} size={13} className={savingPerms ? "spin" : undefined} />
                  {savingPerms ? "正在保存…" : "保存权限"}
                </button>
              </div>
            </div>
          </CollapsibleCard>

          {/* 当前运行环境可直接粘贴的 MCP 配置 */}
          <CollapsibleCard
            icon={Boxes}
            support={mcpConfig?.runtime === "installed" ? "已按当前知意安装位置生成，直接复制到 MCP 客户端即可。" : "当前是开发环境，已按本项目实际路径生成。"}
            title="可直接使用的 MCP 配置"
          >
            {mcpConfigLoading ? <div className="support" role="status">正在生成当前环境的 MCP 配置…</div>
              : mcpConfigError ? <>
                <div className="callout danger" role="alert">{mcpConfigError}</div>
                <button className="btn secondary sm" type="button" onClick={() => void refreshMcpConfig()}>
                  <Icon icon={RefreshCw} size={13} /> 重新读取配置
                </button>
              </> : mcpConfig ? <>
              <pre className="code-block">{JSON.stringify(mcpConfig.mcp_servers, null, 2)}</pre>
              <div className="row" style={{ justifyContent: "space-between", gap: 12, marginTop: 12 }}>
                <span className="small muted">默认不开放任何业务数据；按需授权后，请在客户端断开并重新连接。</span>
                <button className="btn primary sm" type="button" onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(JSON.stringify(mcpConfig.mcp_servers, null, 2));
                    setMcpCopied(true);
                    window.setTimeout(() => setMcpCopied(false), 1800);
                  } catch {
                    notify("复制失败，请手动选中上方 MCP 配置复制。", "error");
                  }
                }}><Icon icon={mcpCopied ? CheckCircle2 : ClipboardCopy} size={13} /> {mcpCopied ? "已复制" : "复制配置"}</button>
              </div>
            </> : null}
          </CollapsibleCard>
        </div>
      </div>

      {/* 生成密钥：明文只显示这一次 */}
      {revealedToken && (
        <div
          className="modal-overlay"
          onClick={(e) => {
            if (e.target === e.currentTarget) setRevealedToken(null);
          }}
        >
          <div className="modal-card" aria-modal="true" aria-label="新密钥" {...revealedTokenDialog} role="dialog">
            <div className="modal-head">
              <div>
                <h3>新{revealedToken.kind === "read" ? "读" : "写"}密钥已生成</h3>
                <p className="support">
                  请立即复制保存。关闭后明文不再显示，忘了只能重新生成（旧密钥随即失效）。
                </p>
              </div>
            </div>
            <div style={{ padding: "0 20px 20px" }}>
              <textarea
                aria-label="密钥内容"
                className="form-input"
                ref={revealedTokenInput}
                readOnly
                rows={3}
                value={revealedToken.token}
                onFocus={(e) => e.target.select()}
                style={{ fontFamily: "monospace", fontSize: 12 }}
              />
              <div className="row" style={{ justifyContent: "flex-end", gap: 8, marginTop: 12 }}>
                <button className="btn ghost sm" type="button" onClick={() => setRevealedToken(null)}>
                  关闭
                </button>
                <button className="btn primary sm" type="button" onClick={() => void copyToken()}>
                  <Icon icon={copied ? CheckCircle2 : ClipboardCopy} size={13} />
                  {copied ? "已复制" : "复制密钥"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 撤销密钥确认 */}
      <ConfirmDialog
        open={revokeTarget !== null}
        title="撤销密钥"
        description={`将撤销${revokeTarget === "read" ? "读" : "写"}密钥。使用该密钥的 HTTP API 请求立即失效，无需重启；已生成的密钥不可恢复。确认撤销吗？`}
        buttonLabel="确认撤销"
        busy={busyKey === revokeTarget}
        onConfirm={() => void handleRevokeKey()}
        onClose={() => setRevokeTarget(null)}
      />
      {unsavedChanges.dialog}
      <Toast toast={toast} onDismiss={clear} />
    </section>
  );
}
