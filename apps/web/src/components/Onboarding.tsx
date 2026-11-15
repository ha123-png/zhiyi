import { useEffect, useRef, useState } from "react";
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
import "./onboarding.css";

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
  onSetup?: (section: "local" | "ai") => void;
  onClose: () => void;
}

const STEPS = ["认识知意", "选择模型", "连接服务", "开始使用"] as const;
type SetupPath = "local" | "cloud" | "existing";

function connectionIdentity(status: ModelStatus | null): string {
  return JSON.stringify([status?.connected, status?.provider, status?.configured_model, status?.configuration_error]);
}

export function Onboarding({ open, modelStatus, onNavigate, onShowDemo, onSetup, onClose }: OnboardingProps) {
  const [step, setStep] = useState(0);
  const [setupPath, setSetupPath] = useState<SetupPath>("local");
  const [setupStartedFrom, setSetupStartedFrom] = useState<string | null>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const resumeAfterSetup = useRef(false);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement;
    return () => { if (previous instanceof HTMLElement && previous.isConnected) previous.focus(); };
  }, [open]);
  useEffect(() => { if (open) stageRef.current?.focus(); }, [open, step]);

  useEffect(() => {
    if (open) {
      if (resumeAfterSetup.current) {
        resumeAfterSetup.current = false;
        return;
      }
      setStep(0);
      setSetupPath("local");
      setSetupStartedFrom(null);
    }
  }, [open]);

  if (!open) return null;

  const modelReady = Boolean(modelStatus?.connected && modelStatus.configured_model && !modelStatus.configuration_error);
  const canContinue = modelReady && (setupStartedFrom === null || setupStartedFrom !== connectionIdentity(modelStatus));

  function chooseSetup(path: SetupPath) {
    setSetupPath(path);
    setSetupStartedFrom(connectionIdentity(modelStatus));
  }

  function openSettings() {
    resumeAfterSetup.current = true;
    if (onSetup) onSetup(setupPath === "local" ? "local" : "ai");
    else {
      onNavigate("settings");
      onClose();
    }
  }

  return (
    <div className="onboarding-overlay" role="dialog" aria-modal="true" aria-label="首次使用引导">
      <div className="onboarding-card" ref={cardRef} onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          markOnboardingDone();
          onClose();
          return;
        }
        if (event.key !== "Tab") return;
        const controls = cardRef.current?.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], input:not([disabled]), [tabindex="0"]');
        if (!controls?.length) return;
        const first = controls[0], last = controls[controls.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement === stageRef.current)) {
          event.preventDefault(); last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault(); first.focus();
        }
      }}>
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
          <div className="onboarding-stage" key={step} ref={stageRef} tabIndex={-1} role="group" aria-label={STEPS[step]} style={{ outline: "none" }}>
          {step === 0 && (
            <>
              <div className="onboarding-icon"><Icon icon={Sparkles} size={30} /></div>
              <h2>让文件成为可核对、可复用的数据</h2>
              <p className="support">用模板告诉知意要理解什么。模型整理信息，规则检查结果，你核对后继续使用；提取时保留完整原件。</p>
              <ul className="onboarding-features">
                <li><Icon icon={FileUp} size={14} /> 模板定义字段、理解要求和校验规则，下一批文件继续复用</li>
                <li><Icon icon={Cpu} size={14} /> 自由选择 LM Studio、Ollama 或兼容的云端模型服务</li>
                <li><Icon icon={ShieldAlert} size={14} /> 数据与原件保存在本机；使用远程模型时，处理内容会发送给所选服务</li>
              </ul>
            </>
          )}

          {step === 1 && (
            <>
              <div className="onboarding-icon"><Icon icon={modelReady ? CheckCircle2 : Cpu} size={30} /></div>
              <h2>{modelReady ? "当前模型服务已连接" : "你希望怎样使用 AI？"}</h2>
              {modelReady && <p className="support">当前方案：<strong>{modelStatus?.configured_model}</strong>。用一份文件核对实际提取效果。</p>}
              <div className="onboarding-choice" style={{ marginTop: 14 }}>
                <button className={`onboarding-choice-card${setupPath === "local" ? " selected" : ""}`} type="button" onClick={() => chooseSetup("local")}>
                  <span className="onboarding-choice-head">
                    <span className="onboarding-choice-icon"><Icon icon={Home} size={20} /></span>
                    <span className="onboarding-choice-badge">推荐</span>
                  </span>
                  <span className="onboarding-choice-title">LM Studio 本地模型</span>
                  <span className="onboarding-choice-desc">文件留在本机；安装 LM Studio 后可在设置中启动、加载和卸载模型</span>
                </button>
                <button className={`onboarding-choice-card${setupPath === "cloud" ? " selected" : ""}`} type="button" onClick={() => chooseSetup("cloud")}>
                  <span className="onboarding-choice-head">
                    <span className="onboarding-choice-icon"><Icon icon={Cloud} size={20} /></span>
                  </span>
                  <span className="onboarding-choice-title">云端 AI 服务</span>
                  <span className="onboarding-choice-desc">文件发送给你选择的服务商处理，可能产生费用</span>
                </button>
                <button className={`onboarding-choice-card${setupPath === "existing" ? " selected" : ""}`} type="button" onClick={() => chooseSetup("existing")}>
                  <span className="onboarding-choice-head">
                    <span className="onboarding-choice-icon"><Icon icon={PlugZap} size={20} /></span>
                  </span>
                  <span className="onboarding-choice-title">我已有模型服务</span>
                  <span className="onboarding-choice-desc">填写服务地址和模型名，测试连接并确认模型能力</span>
                </button>
              </div>
            </>
          )}

          {step === 2 && (
            <>
              <div className="onboarding-icon"><Icon icon={HardDrive} size={30} /></div>
              {canContinue ? (
                <><h2>连接已就绪</h2><p className="support">当前提取方案：{modelStatus?.configured_model}。可以继续体验。</p></>
              ) : setupPath === "cloud" ? (
                <CloudGuide />
              ) : setupPath === "existing" ? (
                <ExistingGuide />
              ) : (
                <LocalGuide />
              )}
            </>
          )}

          {step === 3 && (
            <>
              <div className="onboarding-icon"><Icon icon={UploadCloud} size={30} /></div>
              <h2>选择一个例子，看知意怎样解析含义</h2>
              <p className="support">六个只读示例展示原内容、解析结果和规则校验。</p>
              <div className="onboarding-choice onboarding-demo-choices">
                {([
                  ["sentiment", "新闻情感分析", "从新闻中判断倾向并给出依据"],
                  ["article", "文章关键信息", "提取标题、作者、摘要和关键词"],
                  ["grading", "作业批改", "按模板提示中的标准答案逐题判断对错"],
                  ["mistakes", "错题分析", "整理原题、解析、易错点和举一反三"],
                  ["business", "发票／送货单", "把抬头与明细解析成结构化数据"],
                  ["rule", "规则发现错误", "AI 结果金额不一致，规则提醒你核对金额差异"],
                ] as const).map(([id, title, desc]) => (
                  <button className="onboarding-choice-card" key={id} type="button" onClick={() => { markOnboardingDone(); onShowDemo(id); onClose(); }}>
                    <span className="onboarding-choice-title">{title}</span>
                    <span className="onboarding-choice-desc">{desc}</span>
                    <span className="small">查看示例 →</span>
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
          {step === 2 ? (
            <>
              <button className="btn ghost" type="button" onClick={() => setStep(3)}>先看示例</button>
              <button className="btn primary" type="button" onClick={canContinue ? () => setStep(3) : openSettings}>
                {canContinue ? "继续" : "前往连接设置"} <Icon icon={ArrowRight} size={14} />
              </button>
            </>
          ) : step < STEPS.length - 1 ? (
            <button className="btn primary" type="button" onClick={() => setStep(step + 1)}>下一步 <Icon icon={ArrowRight} size={14} /></button>
          ) : (
            <button className="btn primary" type="button" onClick={() => { markOnboardingDone(); onNavigate("extract"); onClose(); }}>使用自己的文件 <Icon icon={ArrowRight} size={14} /></button>
          )}
        </div>
      </div>
    </div>
  );
}

function LocalGuide() {
  return (
    <>
      <h2>使用 LM Studio 本地模型</h2>
      <p className="support">在 LM Studio 下载模型，再到知意设置中启动服务并加载。文本可使用文本模型；图片或扫描件需要支持图片的模型。</p>
      <ol className="onboarding-guide">
        <li><strong>① 安装并打开 LM Studio</strong><span className="onboarding-guide-desc">如果已经安装，可以直接进入下一步。</span></li>
        <li><strong>② 选择适合文件的模型</strong><span className="onboarding-guide-desc">按文件类型和电脑配置选择；需要读图时，确认模型具有视觉能力。</span></li>
        <li><strong>③ 在知意中启动并加载</strong><span className="onboarding-guide-desc">打开设置里的“本地模型管理”，刷新列表并加载模型。上下文长度与读取范围应适合文件大小。</span></li>
      </ol>
    </>
  );
}

function CloudGuide() {
  return (
    <>
      <h2>使用云端大模型</h2>
      <p className="support">准备服务地址、模型名称和 API Key，然后在连接设置中测试并保存。</p>
      <div className="onboarding-providers">
        <div className="onboarding-provider">
          <div className="onboarding-provider-head">
            <strong>阿里云百炼 · 通义千问</strong>
          </div>
          <a href="https://bailian.console.aliyun.com/" target="_blank" rel="noreferrer">打开百炼控制台 <Icon icon={ExternalLink} size={12} /></a>
        </div>
      </div>
      <p className="small muted" style={{ marginTop: 8 }}>
        处理图片或扫描件时，选择<strong>支持图片的模型</strong>。
      </p>
      <p className="small muted">使用云端模型时，选定的文件内容会发送给该服务商。</p>
    </>
  );
}

function ExistingGuide() {
  return (
    <>
      <h2>连接已有模型服务</h2>
      <p className="support">已有 Ollama、本地接口或公司模型服务？保存一个方案并测试连接，就能按需选用。</p>
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
          <strong>③ 确认模型适合当前文件</strong>
          <span className="onboarding-guide-desc">文本可使用文本模型；图片和扫描件需要视觉能力。连接成功后，再用一份文件检查实际提取效果。</span>
        </li>
      </ol>
    </>
  );
}
