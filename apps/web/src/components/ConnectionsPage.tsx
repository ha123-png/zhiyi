import { useCallback, useEffect, useState } from "react";
import {
  Boxes,
  CheckCircle2,
  ChevronDown,
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
  revokeIntegrationKey,
  saveIntegrationPermissions,
} from "../api";
import type { IntegrationSettings, McpRuntimeConfig } from "../api";
import { ConfirmDialog } from "./ConfirmDialog";
import { Icon } from "./Icon";

interface Endpoint {
  method: string;
  path: string;
  desc: string;
}

const endpoints: Endpoint[] = [
  { method: "GET", path: "/capabilities", desc: "查询能力声明与权限范围" },
  { method: "GET", path: "/tasks", desc: "查询任务列表" },
  { method: "GET", path: "/tasks/{id}/result", desc: "查询任务提取结果" },
  { method: "POST", path: "/tasks", desc: "上传文件创建任务（写密钥）" },
  { method: "GET", path: "/tables", desc: "查询数据表列表" },
  { method: "GET", path: "/tables/{id}", desc: "查询表详情（分页行）" },
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

const cloudPlannerExample = `# 云端规划者：能看任务/模板并控制任务，不能读提取正文或业务数据
DOCUMENT_PIPELINE_MCP_RESULT_READ_ENABLED=0
DOCUMENT_PIPELINE_MCP_DATA_READ_ENABLED=0
DOCUMENT_PIPELINE_MCP_TASK_CONTROL_ENABLED=1`;

function buildCurlExample(baseUrl: string) {
  return `# 查询任务列表（读密钥）
curl -H "Authorization: Bearer READ_TOKEN" \\
  ${baseUrl}/tasks

# 上传文件创建任务（写密钥）
curl -X POST -H "Authorization: Bearer WRITE_TOKEN" \\
  -F "file=@发票.pdf" -F "template_mode=smart" \\
  ${baseUrl}/tasks

# 修改数据行（写密钥，需带乐观锁版本）
curl -X PATCH -H "Authorization: Bearer WRITE_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"expected_version":1,"changes":{"金额":"372.00"}}' \\
  ${baseUrl}/tables/TABLE_ID/rows/ROW_ID`;
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
        <Icon
          className={`card-caret${collapsed ? " is-collapsed" : ""}`}
          icon={ChevronDown}
          size={17}
        />
      </button>
      <div className="card-body">
        <div className="card-body-inner">{children}</div>
      </div>
    </div>
  );
}

export function ConnectionsPage() {
  const integrationBaseUrl = getIntegrationBaseUrl();
  const [status, setStatus] = useState<{
    enabled: boolean;
    message: string;
  }>({ enabled: false, message: "正在检测集成接口状态..." });

  // 集成配置（密钥状态 + MCP 权限）
  const [settings, setSettings] = useState<IntegrationSettings | null>(null);
  const [mcpConfig, setMcpConfig] = useState<McpRuntimeConfig | null>(null);
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
  const [copied, setCopied] = useState(false);
  // 撤销确认
  const [revokeTarget, setRevokeTarget] = useState<"read" | "write" | null>(null);
  // 权限开关草稿
  const [permDraft, setPermDraft] = useState({
    taskRead: false,
    templateRead: false,
    resultRead: false,
    dataRead: false,
    taskControl: false,
    writeEnabled: false,
    fileAccess: false,
    fileRoots: "",
  });
  const [savingPerms, setSavingPerms] = useState(false);

  const refreshSettings = useCallback(() => {
    getIntegrationSettings()
      .then(setSettings)
      .catch((err) =>
        setSettingsError(err instanceof Error ? err.message : "读取集成配置失败"),
      );
  }, []);

  useEffect(() => {
    void getIntegrationStatus().then(setStatus);
    refreshSettings();
    void getMcpRuntimeConfig().then(setMcpConfig).catch(() => setMcpConfig(null));
  }, [refreshSettings]);

  // 权限草稿与当前配置同步
  useEffect(() => {
    if (!settings) return;
    setPermDraft({
      taskRead: settings.task_read,
      templateRead: settings.template_read,
      resultRead: settings.result_read,
      dataRead: settings.data_read,
      taskControl: settings.task_control,
      writeEnabled: settings.write_enabled,
      fileAccess: settings.file_access,
      fileRoots: settings.file_roots.join("; "),
    });
  }, [settings]);

  const handleGenerateKey = async (kind: "read" | "write") => {
    setBusyKey(kind);
    setSettingsError(null);
    setSettingsNotice(null);
    try {
      const result = await generateIntegrationKey(kind);
      setRevealedToken(result);
      setCopied(false);
      await refreshSettings();
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
      setRevokeTarget(null);
      setSettingsNotice(
        `${revokeTarget === "read" ? "读" : "写"}密钥已撤销，HTTP API 立即生效。`,
      );
      await refreshSettings();
    } catch (err) {
      setSettingsError(err instanceof Error ? err.message : "撤销密钥失败");
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
      setSettingsError("复制失败，请手动选中复制。");
    }
  };

  const handleSavePermissions = async () => {
    setSavingPerms(true);
    setMcpError(null);
    setMcpNotice(null);
    try {
      const fileRoots = permDraft.fileRoots
        .split(/[;,，]/)
        .map((item) => item.trim())
        .filter(Boolean);
      await saveIntegrationPermissions({
        task_read: permDraft.taskRead,
        template_read: permDraft.templateRead,
        result_read: permDraft.resultRead,
        data_read: permDraft.dataRead,
        task_control: permDraft.taskControl,
        write_enabled: permDraft.writeEnabled,
        file_access: permDraft.fileAccess,
        file_roots: fileRoots,
      });
      setMcpNotice("MCP 权限已保存；无需重启知意，请在 MCP 客户端重新连接以刷新工具列表。");
      await refreshSettings();
    } catch (err) {
      setMcpError(err instanceof Error ? err.message : "保存权限失败");
    } finally {
      setSavingPerms(false);
    }
  };

  return (
    <section className="view connections-view">
      <header className="page-header">
        <span className="eyebrow">集成</span>
        <h1>接口与 MCP</h1>
        <p className="support">外部系统集成与 MCP 工具的接入说明</p>
      </header>

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
            <div className="settings-toggles">
              <div className="setting-row">
                <div>
                  <div className="settings-name">外部集成 API</div>
                  <div className="small muted">/api/integration/v1</div>
                </div>
                <span className={`badge ${status.enabled ? "live" : "warn"}`}>
                  {status.enabled ? "已启用" : "未启用"}
                </span>
              </div>
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
                <div className="callout danger" style={{ marginBottom: 12 }}>
                  {settingsError}
                </div>
              )}
              {settingsNotice && (
                <div className="callout success" style={{ marginBottom: 12 }}>
                  {settingsNotice}
                </div>
              )}
              <div className="setting-row">
                <div>
                  <div className="settings-name">读密钥</div>
                  <div className="small muted">查询任务、结果、表和分 Sheet</div>
                </div>
                <span className={`badge ${settings?.read_token_set ? "live" : "warn"}`}>
                  {settings?.read_token_set ? "已设置" : "未设置"}
                </span>
              </div>
              <div className="setting-row">
                <div>
                  <div className="settings-name">写密钥</div>
                  <div className="small muted">仅用于 HTTP API：上传文件、修改事实行</div>
                </div>
                <span className={`badge ${settings?.write_token_set ? "live" : "warn"}`}>
                  {settings?.write_token_set ? "已设置" : "未设置"}
                </span>
              </div>
              <div className="setting-row align-top">
                <div style={{ minWidth: 0 }}>
                  <div className="settings-name">密钥操作</div>
                  <div className="small muted" style={{ marginBottom: 8 }}>
                    生成后请立即复制保存；关闭弹窗后明文不可再查看。密钥只用于 HTTP API，并立即生效。
                  </div>
                  <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                    <button
                      className="btn secondary sm"
                      disabled={busyKey !== null}
                      onClick={() => void handleGenerateKey("read")}
                    >
                      <Icon icon={busyKey === "read" ? Loader2 : RefreshCw} size={13} className={busyKey === "read" ? "spin" : undefined} />
                      生成读密钥
                    </button>
                    <button
                      className="btn secondary sm"
                      disabled={busyKey !== null}
                      onClick={() => void handleGenerateKey("write")}
                    >
                      <Icon icon={busyKey === "write" ? Loader2 : RefreshCw} size={13} className={busyKey === "write" ? "spin" : undefined} />
                      生成写密钥
                    </button>
                    <button
                      className="btn ghost sm danger-btn"
                      disabled={busyKey !== null || !settings?.read_token_set}
                      onClick={() => setRevokeTarget("read")}
                      title="撤销读密钥"
                    >
                      <Icon icon={ShieldOff} size={13} /> 撤销读
                    </button>
                    <button
                      className="btn ghost sm danger-btn"
                      disabled={busyKey !== null || !settings?.write_token_set}
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
            support="协议适配器，不拥有独立业务逻辑；当前锁定 mcp==1.28.1"
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
            <div style={{ marginTop: 14 }}>
              <div className="settings-name">最小权限配置示例</div>
              <div className="small muted" style={{ marginBottom: 8 }}>
                每种 AI 使用独立 MCP 进程；未开启的工具不会出现在工具列表中。
              </div>
              <pre className="code-block">{cloudPlannerExample}</pre>
              <div className="small muted" style={{ marginTop: 8 }}>
                文件能力还需 MCP_FILE_ACCESS_ENABLED=1 与 MCP_FILE_ROOTS；事实修改需
                MCP_WRITE_ENABLED=1。完整变量均以 DOCUMENT_PIPELINE_ 开头。
              </div>
            </div>
          </CollapsibleCard>

          {/* MCP 权限开关 */}
          <CollapsibleCard
            icon={Boxes}
            support="每种 AI 使用独立 MCP 进程；未开启的能力不会出现在工具列表中"
            title="MCP 权限"
          >
            <div className="settings-toggles">
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
                ["resultRead", "读取提取结果", "允许 AI 查看任务的结构化提取结果与校验问题"],
                ["dataRead", "读取数据仓库", "允许 AI 查看数据表、数据行、视图并执行聚合查询"],
              ] as const).map(([key, label, description]) => (
                <div className="setting-row" key={key}>
                  <div>
                    <div className="settings-name">{label}</div>
                    <div className="small muted">{description}</div>
                  </div>
                  <button
                    aria-label={label}
                    className={`toggle ${permDraft[key] ? "on" : "off"}`}
                    type="button"
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
                  onClick={() =>
                    setPermDraft((d) => ({ ...d, writeEnabled: !d.writeEnabled }))
                  }
                >
                  <span />
                </button>
              </div>
              <div className="setting-row">
                <div>
                  <div className="settings-name">文件访问</div>
                  <div className="small muted">允许 AI 从指定目录导入文件 / 导出表格</div>
                </div>
                <button
                  aria-label="文件访问"
                  className={`toggle ${permDraft.fileAccess ? "on" : "off"}`}
                  type="button"
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
                  disabled={savingPerms || busyKey !== null}
                  onClick={() => void handleSavePermissions()}
                >
                  <Icon icon={savingPerms ? Loader2 : CheckCircle2} size={13} className={savingPerms ? "spin" : undefined} />
                  保存权限
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
            {mcpConfig ? <>
              <pre className="code-block">{JSON.stringify(mcpConfig.mcp_servers, null, 2)}</pre>
              <div className="row" style={{ justifyContent: "space-between", gap: 12, marginTop: 12 }}>
                <span className="small muted">默认不开放任何业务数据；按需授权后，请在客户端断开并重新连接。</span>
                <button className="btn primary sm" type="button" onClick={async () => {
                  await navigator.clipboard.writeText(JSON.stringify(mcpConfig.mcp_servers, null, 2));
                  setMcpCopied(true);
                  window.setTimeout(() => setMcpCopied(false), 1800);
                }}><Icon icon={mcpCopied ? CheckCircle2 : ClipboardCopy} size={13} /> {mcpCopied ? "已复制" : "复制配置"}</button>
              </div>
            </> : <div className="support">正在生成当前环境的 MCP 配置…</div>}
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
          <div className="modal-card" role="dialog" aria-modal="true" aria-label="新密钥">
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
                readOnly
                rows={3}
                value={revealedToken.token}
                onFocus={(e) => e.target.select()}
                style={{ fontFamily: "monospace", fontSize: 12 }}
              />
              <div className="row" style={{ justifyContent: "flex-end", gap: 8, marginTop: 12 }}>
                <button className="btn ghost sm" onClick={() => setRevealedToken(null)}>
                  关闭
                </button>
                <button className="btn primary sm" onClick={() => void copyToken()}>
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
        description={`将撤销${revokeTarget === "read" ? "读" : "写"}密钥。撤销后使用该密钥的访问会失效（重启后生效）；已生成的密钥不可恢复。确认撤销吗？`}
        buttonLabel="确认撤销"
        busy={busyKey === revokeTarget}
        onConfirm={() => void handleRevokeKey()}
        onClose={() => setRevokeTarget(null)}
      />
    </section>
  );
}
