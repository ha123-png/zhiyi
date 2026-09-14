import { useEffect, useState } from "react";
import { useAssistant } from "./AssistantProvider";
import { assistantApi } from "./api";

type ModelState = {
  state: string;
  at?: string;
  model?: string;
  error?: string;
  responded?: boolean;
  tool_warning?: boolean;
  simulated?: boolean;
};
export function ModelConnectionState() {
  const ask = useAssistant();
  const [result, setResult] = useState<ModelState | null>(null);
  useEffect(() => {
    let alive = true;
    setResult(null);
    const read = () =>
      void assistantApi<ModelState>(
        `/model-state?profile_id=${encodeURIComponent(ask.profileId)}`,
      )
        .then((value) => {
          if (alive) setResult(value);
        })
        .catch(() => {
          if (alive) setResult(null);
        });
    read();
    const timer = window.setInterval(read, 8000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [ask.profileId, ask.profile?.version, ask.busy]);
  if (result?.simulated)
    return (
      <span className="ask-model-state">
        界面演示 · 模拟回答（不调用所选模型）
      </span>
    );
  const labels: Record<string, string> = {
    unconfigured: "未配置",
    unverified: "尚未验证",
    running: "请求中",
    success: "最近调用成功",
    cancelled: "最近请求已停止",
    failed: result?.responded ? "服务已响应 · 本次处理失败" : "最近请求失败",
  };
  const at = result?.at
    ? new Date(
        /Z$|[+-]\d\d:\d\d$/.test(result.at) ? result.at : result.at + "Z",
      ).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })
    : "";
  return (
    <span
      className="ask-model-state"
      title={
        result?.error ||
        "按当前聊天方案的最近请求显示；历史成功不代表此刻一定在线。"
      }
    >
      问知意模型：{ask.profile?.name ? `${ask.profile.name} · ` : ""}
      {labels[result?.state || ""] || "状态待确认"}
      {at ? ` · ${at}` : ""}
      {result?.tool_warning ? " · 工具调用有异常" : ""}
    </span>
  );
}
