import { useEffect, useState } from "react";
import { Check, ChevronDown, Eye, EyeOff, Loader2, Pencil, Plus, Zap } from "lucide-react";
import {
  activateModelProfile,
  archiveModelProfile,
  createModelProfile,
  getModelProfiles,
  probeModelProfile,
  updateModelProfile,
} from "../api";
import type { ModelProfile, ModelProfileDraft, ModelProbeResult, ModelProviderName } from "../types";
import { Icon } from "./Icon";
import { ConfirmDialog } from "./ConfirmDialog";

const EMPTY_DRAFT: ModelProfileDraft = {
  name: "",
  provider: "lm_studio",
  base_url: "",
  model_name: "",
  reasoning_effort: "none",
  timeout_seconds: 180,
  context_length: 8192,
  temperature: null,
  multimodal: null,
  acknowledge_remote_data_transfer: false,
};

const EXAMPLE_DRAFT: ModelProfileDraft = {
  ...EMPTY_DRAFT,
  name: "本地 AI 方案",
  base_url: "http://127.0.0.1:1234/v1",
  model_name: "qwen3.5-4b",
};

type ServicePreset = "lm_studio" | "ollama" | "aliyun" | "openai" | "custom";

const SERVICE_PRESETS: Record<ServicePreset, { label: string; provider: ModelProviderName; baseUrl: string; hint: string }> = {
  lm_studio: { label: "LM Studio（本机）", provider: "lm_studio", baseUrl: "http://127.0.0.1:1234/v1", hint: "先在 LM Studio 中启动本地服务器并加载模型。" },
  ollama: { label: "Ollama（本机）", provider: "ollama", baseUrl: "http://127.0.0.1:11434", hint: "填写 Ollama 中已经下载的模型名称。" },
  aliyun: { label: "阿里云百炼", provider: "openai_compatible", baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", hint: "北京地域专属 Workspace 地址应完整填写到 /compatible-mode/v1，不能只填到 /co。" },
  openai: { label: "OpenAI API", provider: "openai_compatible", baseUrl: "https://api.openai.com/v1", hint: "使用 OpenAI 兼容的模型名称与 API Key。" },
  custom: { label: "其他 OpenAI 兼容服务", provider: "openai_compatible", baseUrl: "", hint: "填写服务商文档给出的 API 根地址，通常以 /v1 或 /compatible-mode/v1 结尾。" },
};

function inferPreset(draft: Pick<ModelProfileDraft, "provider" | "base_url">): ServicePreset {
  if (draft.provider === "lm_studio") return "lm_studio";
  if (draft.provider === "ollama") return "ollama";
  if (draft.base_url.includes("dashscope.aliyuncs.com") || draft.base_url.includes("maas.aliyuncs.com")) return "aliyun";
  if (draft.base_url.includes("api.openai.com")) return "openai";
  return "custom";
}

export function ModelProfilesSettings({ onActiveProfileChanged }: { onActiveProfileChanged?: () => void } = {}) {
  const [profiles, setProfiles] = useState<ModelProfile[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<ModelProfileDraft>(EXAMPLE_DRAFT);
  const [showApiKey, setShowApiKey] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [probeState, setProbeState] = useState<{ loading: boolean; result: ModelProbeResult | null }>({
    loading: false,
    result: null,
  });
  // 云端方案首次激活的确认：参考原型，创建/编辑时不弹，使用（激活）时才确认
  const [confirmActivate, setConfirmActivate] = useState<ModelProfile | null>(null);
  const [servicePreset, setServicePreset] = useState<ServicePreset>("lm_studio");

  useEffect(() => {
    void reload();
  }, []);

  async function reload(preferredId?: string) {
    setLoading(true);
    try {
      const items = await getModelProfiles();
      setProfiles(items);
      const selected = items.find((item) => item.id === preferredId)
        ?? items.find((item) => item.is_active)
        ?? items.find((item) => !item.is_archived);
      if (selected) selectProfile(selected);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "AI 服务方案加载失败。");
    } finally {
      setLoading(false);
    }
  }

  function selectProfile(profile: ModelProfile) {
    setSelectedId(profile.id);
    setDraft({
      name: profile.name,
      provider: profile.provider,
      base_url: profile.base_url,
      model_name: profile.model_name,
      reasoning_effort: profile.reasoning_effort,
      timeout_seconds: profile.timeout_seconds,
      context_length: profile.context_length,
      temperature: profile.temperature,
      multimodal: profile.multimodal,
      acknowledge_remote_data_transfer: false,
    });
    setServicePreset(inferPreset(profile));
    setMessage(null);
    setError(null);
    setProbeState({ loading: false, result: null });
  }

  function newProfile() {
    setSelectedId(null);
    setDraft(EMPTY_DRAFT);
    setServicePreset("lm_studio");
    setMessage(null);
    setError(null);
    setProbeState({ loading: false, result: null });
  }

  async function save() {
    if (!draft.name.trim() || !draft.base_url.trim() || !draft.model_name.trim()) {
      setError("请填写方案名称、服务地址和模型名称。");
      return;
    }
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const current = profiles.find((item) => item.id === selectedId);
      const body = { ...draft, api_key: draft.api_key?.trim() || undefined };
      const saved = current
        ? await updateModelProfile(current.id, current.version, body)
        : await createModelProfile(body);
      setDraft((value) => ({ ...value, api_key: "" }));
      await reload(saved.id);
      setMessage(current ? "已保存为新版本；排队中的旧任务不会改变。" : "方案已创建，请确认后激活。");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存失败。");
    } finally {
      setSaving(false);
    }
  }

  async function probe() {
    if (!draft.name.trim() || !draft.base_url.trim() || !draft.model_name.trim()) {
      setError("请先填写服务地址和模型名称，再测试连接。");
      return;
    }
    setProbeState({ loading: true, result: null });
    setError(null);
    setMessage(null);
    try {
      const body = { ...draft, api_key: draft.api_key?.trim() || undefined };
      const result = await probeModelProfile(body, selectedId ?? undefined);
      setProbeState({ loading: false, result });
      // 探测到明确的多模态结论时，固定写入方案（保存后不再跟随表单变化）
      if (result.multimodal !== null) {
        setDraft((value) => ({ ...value, multimodal: result.multimodal }));
      }
    } catch (reason) {
      setProbeState({ loading: false, result: null });
      setError(reason instanceof Error ? reason.message : "测试连接失败。");
    }
  }

  function requestActivate(profile: ModelProfile) {
    // 每次切换到云端方案都必须确认，防止用户不小心切到了云端方案
    if (profile.is_remote) {
      setConfirmActivate(profile);
      return;
    }
    void doActivate(profile, false);
  }

  async function doActivate(profile: ModelProfile, acknowledge: boolean) {
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      await activateModelProfile(profile.id, profile.version, acknowledge);
      setConfirmActivate(null);
      await reload(profile.id);
      // 方案切换是全局状态：通知顶部状态栏和本地模型卡片立即重新探测，
      // 不等下一次进入页面或刷新浏览器。
      onActiveProfileChanged?.();
      setMessage("已激活；之后新上传的文件会使用这套方案。");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "激活失败。");
    } finally {
      setSaving(false);
    }
  }

  async function archive(profile: ModelProfile) {
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      await archiveModelProfile(profile.id);
      await reload();
      setMessage("方案已停用；停用后不能再激活，但历史任务快照不受影响。");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "停用失败。");
    } finally {
      setSaving(false);
    }
  }

  const selected = profiles.find((item) => item.id === selectedId);
  const latestIsActive = selected?.active_version === selected?.version;

  return (
    <>
      <div className="callout info callout-sm" style={{ marginBottom: 16 }}>
        本地模型建议在 LM Studio 使用上下文 8192、并发 1。这里保存服务连接；新设置只影响之后上传的文件。
        云端方案的"文件离开本机"确认会在激活（使用）时弹出。
      </div>
      {error && <div className="callout danger">{error}</div>}
      {message && <div className="callout success">{message}</div>}
      {probeState.result && (
        <div
          className={`callout ${
            !probeState.result.connected
              ? "danger"
              : probeState.result.model_listed === false
                ? "warning"
                : "success"
          }`}
        >
          {probeState.result.message}
          {probeState.result.connected && probeState.result.model_listed === false && (
            <span> 保存前请确认模型名称与服务中加载的一致。</span>
          )}
        </div>
      )}

      <div className="settings-row-2">
        <div className="form-field">
          <label className="form-label" htmlFor="model-profile-name">方案名称</label>
          <input id="model-profile-name" className="form-input" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
        </div>
        <div className="form-field">
          <label className="form-label" htmlFor="model-profile-provider">服务类型</label>
          <select
            id="model-profile-provider"
            className="form-input"
            value={servicePreset}
            onChange={(event) => {
              const preset = event.target.value as ServicePreset;
              const config = SERVICE_PRESETS[preset];
              setServicePreset(preset);
              setDraft({ ...draft, provider: config.provider, base_url: config.baseUrl || draft.base_url, multimodal: null });
              setProbeState({ loading: false, result: null });
            }}
          >
            {Object.entries(SERVICE_PRESETS).map(([key, preset]) => <option key={key} value={key}>{preset.label}</option>)}
          </select>
          <div className="small muted">{SERVICE_PRESETS[servicePreset].hint}</div>
        </div>
      </div>
      <div className="form-field">
        <label className="form-label" htmlFor="model-profile-url">服务地址</label>
        <input id="model-profile-url" className="form-input" value={draft.base_url} onChange={(event) => setDraft({ ...draft, base_url: event.target.value, multimodal: null })} placeholder="https://服务商地址/v1" />
      </div>
      <div className="settings-row-2">
        <div className="form-field">
          <label className="form-label" htmlFor="model-profile-model">模型名称</label>
          <input id="model-profile-model" className="form-input" value={draft.model_name} onChange={(event) => setDraft({ ...draft, model_name: event.target.value })} placeholder="qwen3.5-4b" />
        </div>
        <div className="form-field">
          <label className="form-label" htmlFor="model-profile-timeout">超时（秒）</label>
          <input id="model-profile-timeout" className="form-input" type="number" min={1} max={3600} value={draft.timeout_seconds} onChange={(event) => setDraft({ ...draft, timeout_seconds: Number(event.target.value) })} />
        </div>
      </div>
      <div className="settings-row-2">
        <div className="form-field">
          <label className="form-label" htmlFor="model-profile-reasoning">推理强度</label>
          <select id="model-profile-reasoning" className="form-input" value={draft.reasoning_effort ?? ""} onChange={(event) => setDraft({ ...draft, reasoning_effort: event.target.value || null })}>
            <option value="">由服务决定</option>
            <option value="none">关闭额外推理</option>
            <option value="low">低</option>
            <option value="medium">中</option>
            <option value="high">高</option>
          </select>
        </div>
        <div className="form-field">
          <label className="form-label" htmlFor="model-profile-key">API Key（可选）</label>
          <div className="api-key-wrap">
            <input id="model-profile-key" className="form-input" type={showApiKey ? "text" : "password"} value={draft.api_key ?? ""} onChange={(event) => setDraft({ ...draft, api_key: event.target.value })} placeholder={selected?.has_api_key ? "已安全保存；留空则保持不变" : "本地服务通常留空"} />
            <button className="api-key-toggle" type="button" aria-label={showApiKey ? "隐藏 API Key" : "显示 API Key"} onClick={() => setShowApiKey((value) => !value)}>
              <Icon icon={showApiKey ? EyeOff : Eye} size={14} />
            </button>
          </div>
        </div>
      </div>
      <div className="settings-row-2">
        <div className="form-field">
          <span className="form-label">多模态能力</span>
          <div className="multimodal-status">
            {draft.multimodal === false ? (
              <span className="badge danger">仅文本</span>
            ) : draft.multimodal === true ? (
              <span className="badge success">支持图片</span>
            ) : (
              <span className="badge muted">未验证</span>
            )}
            <span className="small muted">由真实图片请求判定。连接或接口异常只会显示“未验证”，不会误判为仅文本；仅文本方案不能用于文档提取。</span>
          </div>
        </div>
        <div className="form-field" style={{ alignSelf: "flex-end" }}>
          <button
            type="button"
            className="btn secondary sm"
            disabled={probeState.loading || saving}
            onClick={() => void probe()}
            title="检查服务是否可访问，并用一张 1x1 测试图确认模型是否接受图片输入；已保存密钥的云端方案会自动使用"
          >
            <Icon icon={probeState.loading ? Loader2 : Zap} size={13} className={probeState.loading ? "spin" : undefined} />
            {probeState.loading ? "测试中…" : "测试连接"}
          </button>
        </div>
      </div>
      <button
        type="button"
        className={`settings-advanced-toggle${advancedOpen ? " open" : ""}`}
        onClick={() => setAdvancedOpen((value) => !value)}
        aria-expanded={advancedOpen}
      >
        <span>高级设置</span>
        <Icon icon={ChevronDown} size={14} />
      </button>
      <div className={`collapse${advancedOpen ? " open" : ""}`}>
        <div className="collapse-content">
          <div className="settings-row-2 settings-advanced-body">
            <div className="form-field">
              <label className="form-label" htmlFor="model-profile-temperature">温度（可选）</label>
              <input
                id="model-profile-temperature"
                className="form-input"
                type="number"
                min={0}
                max={2}
                step={0.1}
                value={draft.temperature ?? ""}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    temperature: event.target.value === "" ? null : Number(event.target.value),
                  })
                }
                placeholder="留空使用默认 0.1"
              />
              <div className="small muted">越低越稳定；结构化提取建议保持较低值。</div>
            </div>
          </div>
        </div>
      </div>
      <div className="settings-form-actions">
        <button className="btn secondary sm" type="button" onClick={newProfile}><Icon icon={Plus} size={13} /> 新建空白方案</button>
        <button className="btn primary sm" type="button" disabled={saving} onClick={() => void save()}>
          <Icon icon={saving ? Loader2 : Check} className={saving ? "spin" : undefined} size={13} /> {selected ? "保存修改" : "创建方案"}
        </button>
        {selected && !selected.is_archived && !latestIsActive && (
          <button className="btn secondary sm" type="button" disabled={saving} onClick={() => requestActivate(selected)}><Icon icon={Check} size={13} /> 激活方案</button>
        )}
        {selected && !selected.is_archived && !selected.is_active && (
          <button className="btn danger-soft sm" type="button" disabled={saving} onClick={() => void archive(selected)}><Icon icon={Pencil} size={13} /> 删除方案</button>
        )}
      </div>

      <div className="profile-list" aria-label="模型方案列表">
        {loading && <div className="small muted"><Icon icon={Loader2} className="spin" size={13} /> 正在读取方案…</div>}
        {!loading && profiles.length === 0 && <div className="small muted">还没有保存的方案。上方示例可直接创建本地 LM Studio 方案。</div>}
        {profiles.map((profile) => (
          <button key={profile.id} type="button" className={`profile-item${selectedId === profile.id ? " active" : ""}`} onClick={() => selectProfile(profile)}>
            <span className="profile-info">
              <span className="profile-head"><strong>{profile.name}</strong>{profile.is_active && <span className="badge live">使用中</span>}</span>
              <span className="profile-meta">{profile.provider} · {profile.model_name} · {profile.multimodal === false ? "仅文本" : profile.multimodal === true ? "支持图片" : "多模态未验证"}</span>
              <span className="profile-url">{profile.base_url}</span>
            </span>
            <span className="profile-actions"><Icon icon={Pencil} size={12} /> 编辑</span>
          </button>
        ))}
      </div>

      <ConfirmDialog
        open={confirmActivate !== null}
        title="确认使用云端模型？"
        description={`此方案的服务地址不在本机（${confirmActivate?.base_url ?? ""}）。激活后，新上传的文件内容会被发送到该服务进行处理。每次切换到这个方案都会再次确认，防止误切到云端。`}
        buttonLabel="我确认，使用云端服务"
        busy={saving}
        onConfirm={() => {
          if (confirmActivate) void doActivate(confirmActivate, true);
        }}
        onClose={() => setConfirmActivate(null)}
      />
    </>
  );
}
