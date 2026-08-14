import { useEffect, useState } from "react";
import {
  ArrowRight,
  CheckCircle2,
  Cloud,
  Cpu,
  ExternalLink,
  FileUp,
  HardDrive,
  Home,
  PlugZap,
  ShieldAlert,
  Sparkles,
  UploadCloud,
  X,
} from "lucide-react";
import { setOnboardingState } from "../api";
import type { DemoScenario, ModelStatus, NavigationKey } from "../types";
import { Icon } from "./Icon";

export const ONBOARDING_VERSION = 4;
export const ONBOARDING_DONE_KEY = `onboarding-done-v${ONBOARDING_VERSION}`;

export function onboardingDone(): boolean {
  try {
    return localStorage.getItem(ONBOARDING_DONE_KEY) === "1";
  } catch {
    return false;
  }
}

export function markOnboardingDone() {
  try {
    localStorage.setItem(ONBOARDING_DONE_KEY, "1");
  } catch {
    // Storage is only a convenience; onboarding must still be usable without it.
  }
  void setOnboardingState(ONBOARDING_VERSION).catch(() => undefined);
}

interface OnboardingProps {
  open: boolean;
  modelStatus: ModelStatus | null;
  onNavigate: (page: NavigationKey) => void;
  onShowDemo: (scenario: DemoScenario) => void;
  onClose: () => void;
}

const STEPS = ["欢迎", "选择方案", "启用智能", "开始使用"] as const;
type SetupPath = "local" | "cloud" | "existing";

export function Onboarding({ open, modelStatus, onNavigate, onShowDemo, onClose }: OnboardingProps) {
  const [step, setStep] = useState(0);
  const [setupPath, setSetupPath] = useState<SetupPath>("local");

  useEffect(() => {
    if (open) {
      setStep(0);
      setSetupPath("local");
    }
  }, [open]);

  if (!open) return null;

  const modelReady = Boolean(modelStatus?.connected && modelStatus.configured_model);

  function openSettings() {
    markOnboardingDone();
    onNavigate("settings");
    onClose();
  }

  return (
    <div className="onboarding-overlay" role="dialog" aria-modal="true" aria-label="首次使用引导">
      <div className="onboarding-card">
        <button className="onboarding-close" type="button" aria-label="跳过引导" title="跳过引导（可在设置中重新打开）" onClick={() => { markOnboardingDone(); onClose(); }}>
          <Icon icon={X} size={18} />
        </button>

        <div className="onboarding-steps" aria-label="引导步骤">
          {STEPS.map((label, index) => (
            <span key={label} className={`onboarding-step${index === step ? " active" : ""}${index < step ? " done" : ""}`}>
              {index < step ? <Icon icon={CheckCircle2} size={12} /> : <span>{index + 1}</span>}
              {label}
            </span>
          ))}
        </div>

        <div className="onboarding-body">
          <div className="onboarding-stage" key={step}>
          {step === 0 && (
            <>
              <div className="onboarding-icon"><Icon icon={Sparkles} size={30} /></div>
              <h2>从文件到可用数据，只需要一条完整链路</h2>
              <p className="support">知意负责识别、审核、入库和导出。第一次使用先配置视觉模型，之后上传文件即可。</p>
              <ul className="onboarding-features">
                <li><Icon icon={FileUp} size={14} /> 文档在本产品中排队、审核、保存到数据表</li>
                <li><Icon icon={Cpu} size={14} /> 支持 LM Studio、Ollama 和兼容云端视觉模型</li>
                <li><Icon icon={ShieldAlert} size={14} /> 默认完全本地；只有你主动激活云端方案才会发送文件</li>
              </ul>
            </>
          )}

          {step === 1 && (
            <>
              <div className="onboarding-icon"><Icon icon={modelReady ? CheckCircle2 : Cpu} size={30} /></div>
              <h2>{modelReady ? "当前模型已可用" : "你希望怎样使用 AI？"}</h2>
              {modelReady && <div className="callout success">当前使用：<strong>{modelStatus?.configured_model}</strong>。你可以直接进入下一步体验。</div>}
              <div className="onboarding-choice" style={{ marginTop: 14 }}>
                <button className={`onboarding-choice-card${setupPath === "local" ? " selected" : ""}`} type="button" onClick={() => setSetupPath("local")}>
                  <span className="onboarding-choice-head">
                    <span className="onboarding-choice-icon"><Icon icon={Home} size={20} /></span>
                    <span className="onboarding-choice-badge">推荐</span>
                  </span>
                  <span className="onboarding-choice-title">LM Studio 本地模型</span>
                  <span className="onboarding-choice-desc">文件留在本机；安装 LM Studio 后可在设置中启动、加载和卸载模型</span>
                </button>
                <button className={`onboarding-choice-card${setupPath === "cloud" ? " selected" : ""}`} type="button" onClick={() => setSetupPath("cloud")}>
                  <span className="onboarding-choice-head">
                    <span className="onboarding-choice-icon"><Icon icon={Cloud} size={20} /></span>
                  </span>
                  <span className="onboarding-choice-title">云端 AI 服务</span>
                  <span className="onboarding-choice-desc">文件发送给你选择的服务商处理，可能产生费用</span>
                </button>
                <button className={`onboarding-choice-card${setupPath === "existing" ? " selected" : ""}`} type="button" onClick={() => setSetupPath("existing")}>
                  <span className="onboarding-choice-head">
                    <span className="onboarding-choice-icon"><Icon icon={PlugZap} size={20} /></span>
                  </span>
                  <span className="onboarding-choice-title">我已有模型服务</span>
                  <span className="onboarding-choice-desc">直接填写地址、模型名并测试连接，立即可用</span>
                </button>
              </div>
            </>
          )}

          {step === 2 && (
            <>
              <div className="onboarding-icon"><Icon icon={HardDrive} size={30} /></div>
              {setupPath === "cloud" ? (
                <CloudGuide onOpenSettings={openSettings} onSeeDemo={() => setStep(3)} />
              ) : setupPath === "existing" ? (
                <ExistingGuide onOpenSettings={openSettings} onSeeDemo={() => setStep(3)} />
              ) : (
                <LocalGuide onOpenSettings={openSettings} onSeeDemo={() => setStep(3)} />
              )}
            </>
          )}

          {step === 3 && (
            <>
              <div className="onboarding-icon"><Icon icon={UploadCloud} size={30} /></div>
              <h2>选择一个例子，看知意怎样解析含义</h2>
              <p className="support">六个示例都会跳到提取页面，同时展示原内容、解析结果和规则校验；不调用模型、不写入历史。</p>
              <div className="onboarding-choice onboarding-demo-choices">
                {([
                  ["sentiment", "新闻情感分析", "从新闻中判断倾向并给出依据"],
                  ["article", "文章关键信息", "提取标题、作者、摘要和关键词"],
                  ["grading", "作业批改", "按模板提示中的标准答案逐题判断对错"],
                  ["mistakes", "错题分析", "整理原题、解析、易错点和举一反三"],
                  ["business", "发票／送货单", "把抬头与明细解析成结构化数据"],
                  ["rule", "规则发现错误", "AI 结果金额不一致，规则激活人工兜底"],
                ] as const).map(([id, title, desc]) => (
                  <button className="onboarding-choice-card" key={id} type="button" onClick={() => { markOnboardingDone(); onShowDemo(id); onClose(); }}>
                    <span className="onboarding-choice-title">{title}</span>
                    <span className="onboarding-choice-desc">{desc}</span>
                    <span className="small">在提取页面查看 →</span>
                  </button>
                ))}
              </div>
            </>
          )}
          </div>
        </div>

        <div className="onboarding-actions">
          {step > 0 && <button className="btn ghost" type="button" onClick={() => setStep(step - 1)}>上一步</button>}
          <span style={{ flex: 1 }} />
          {step < STEPS.length - 1 ? (
            <button className="btn primary" type="button" onClick={() => setStep(step + 1)}>下一步 <Icon icon={ArrowRight} size={14} /></button>
          ) : (
            <button className="btn ghost" type="button" onClick={() => { markOnboardingDone(); onClose(); }}>稍后再说</button>
          )}
        </div>
      </div>
    </div>
  );
}

function LocalGuide({ onOpenSettings, onSeeDemo }: { onOpenSettings: () => void; onSeeDemo: () => void }) {
  return (
    <>
      <h2>使用 LM Studio 本地模型</h2>
      <p className="support">先安装并打开 LM Studio，下载支持图片的模型；随后在知意设置中启动服务、选择 8192 上下文并加载模型。</p>
      <ol className="onboarding-guide">
        <li><strong>① 安装并打开 LM Studio</strong><span className="onboarding-guide-desc">如果已经安装，可以直接进入下一步。</span></li>
        <li><strong>② 下载多模态模型</strong><span className="onboarding-guide-desc">例如 Qwen3.5-4B；图片任务不能使用纯文本模型。</span></li>
        <li><strong>③ 在知意中启动并加载</strong><span className="onboarding-guide-desc">打开设置里的“本地模型管理”，刷新列表并加载模型。</span></li>
      </ol>
      <div className="onboarding-guide-actions" style={{ marginTop: 10 }}>
        <button className="btn primary" type="button" onClick={onOpenSettings}>
          查看完整模型设置 <Icon icon={ArrowRight} size={14} />
        </button>
        <button className="btn ghost" type="button" onClick={onSeeDemo}>
          先看示例 →
        </button>
      </div>
    </>
  );
}

function CloudGuide({ onOpenSettings, onSeeDemo }: { onOpenSettings: () => void; onSeeDemo: () => void }) {
  return (
    <>
      <h2>使用云端大模型</h2>
      <p className="support">识别交给云服务商，先拿一个 API Key。下面是国内网络最省心的推荐方案。</p>
      <div className="onboarding-providers">
        <div className="onboarding-provider">
          <div className="onboarding-provider-head">
            <strong>阿里云百炼 · 通义千问</strong>
            <span className="onboarding-choice-badge">国内直连 · 推荐</span>
          </div>
          <p className="small muted">注册后领免费额度，支持看图。地址和模型名都替你填好了：</p>
          <a href="https://bailian.console.aliyun.com/" target="_blank" rel="noreferrer">打开百炼控制台领取 Key <Icon icon={ExternalLink} size={12} /></a>
          <p className="onboarding-provider-code small muted">服务商：OpenAI 兼容服务<br />地址：<code>https://dashscope.aliyuncs.com/compatible-mode/v1</code><br />模型：<code>qwen3.6-flash</code></p>
        </div>
      </div>
      <p className="small muted" style={{ marginTop: 8 }}>
        以上只是推荐，<strong>不是必须</strong>——你也可以使用任何 OpenAI 兼容服务。注意：识别图片需要<strong>多模态模型</strong>（如 <code>qwen-vl</code>、<code>gpt-4o</code>）；纯文本模型传图片会失败。
      </p>
      <div className="callout warning callout-sm" style={{ marginTop: 8 }}>
        <strong>数据边界：</strong>启用云端方案后，待识别文件会发送到你填写的服务商，请先确认其隐私条款、数据地区与费用。
      </div>
      <div className="onboarding-guide-actions" style={{ marginTop: 10 }}>
        <button className="btn primary" type="button" onClick={onOpenSettings}>
          去配置云端方案 <Icon icon={ArrowRight} size={14} />
        </button>
        <button className="btn ghost" type="button" onClick={onSeeDemo}>
          先看示例 →
        </button>
      </div>
    </>
  );
}

function ExistingGuide({ onOpenSettings, onSeeDemo }: { onOpenSettings: () => void; onSeeDemo: () => void }) {
  return (
    <>
      <h2>连接已有模型服务</h2>
      <p className="support">已有 Ollama、本地接口或公司服务？填三项就能用。</p>
      <ol className="onboarding-guide">
        <li>
          <strong>① 确认服务地址和模型名</strong>
          <span className="onboarding-guide-desc">例如 Ollama 通常是 <code>http://127.0.0.1:11434/v1</code>，模型名在服务里查。</span>
        </li>
        <li>
          <strong>② 填写并测试连接</strong>
          <span className="onboarding-guide-desc">在「AI 服务配置」选服务商、填地址和模型名，点「测试连接」。</span>
        </li>
        <li>
          <strong>③ 确认模型能看图</strong>
          <span className="onboarding-guide-desc">图片文件需要多模态模型（如 <code>qwen-vl</code>、<code>llava</code>、<code>gpt-4o</code>）；纯文本模型处理图片会失败。</span>
        </li>
      </ol>
      <div className="onboarding-guide-actions" style={{ marginTop: 10 }}>
        <button className="btn primary" type="button" onClick={onOpenSettings}>
          去配置已有服务 <Icon icon={ArrowRight} size={14} />
        </button>
        <button className="btn ghost" type="button" onClick={onSeeDemo}>
          先看示例 →
        </button>
      </div>
    </>
  );
}
