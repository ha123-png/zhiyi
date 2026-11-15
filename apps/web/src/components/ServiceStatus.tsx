import { useState } from "react";
import * as Popover from "@radix-ui/react-popover";
import { X } from "lucide-react";
import { ModelConnectionState } from "../assistant/ModelConnectionState";
import type { ModelStatus, SystemStatus } from "../types";
import { CollapseChevron } from "./CollapseChevron";
import { Icon } from "./Icon";
import "./global-task-bar.css";

export function ServiceStatus({ systemStatus, modelStatus, failed }: { systemStatus: SystemStatus | null; modelStatus: ModelStatus | null; failed: boolean }) {
  const [open, setOpen] = useState(false);
  const label = systemStatus == null ? failed ? "连接暂不可用" : "正在连接服务…"
    : !systemStatus.api.connected ? "服务未连接" : !systemStatus.worker.connected ? "文件处理未启动" : "服务已就绪";
  return <div className="gtb-service"><Popover.Root open={open} onOpenChange={setOpen}>
    <Popover.Trigger className="system-status-trigger" type="button">{label}<CollapseChevron expanded={open} /></Popover.Trigger>
    <Popover.Portal><Popover.Content className="system-status-popover" aria-label="连接详情" align="end" sideOffset={8} collisionPadding={16}>
      <div className="system-status-heading"><strong>连接详情</strong><Popover.Close className="icon-button" aria-label="关闭连接详情"><Icon icon={X} size={16} /></Popover.Close></div>
      <div className="system-status-summary" role="status">
        {systemStatus ? <><span title={systemStatus.api.message}>本机程序：{systemStatus.api.connected ? "可用" : "未连接"}</span><span title={systemStatus.worker.message}>文件处理：{systemStatus.worker.connected ? "已启动" : "未启动"}</span></>
          : <span>{failed ? "暂时无法读取服务状态，将自动重试。" : "正在检查程序、文件处理和模型连接…"}</span>}
        <span title={modelStatus?.configuration_error || "按当前提取方案显示模型名称和连接状态。"}>提取模型：{modelStatus == null ? "状态待确认" : <>{modelStatus.configured_model || "未配置"} · {modelStatus.configuration_error ? "配置需检查" : modelStatus.connected ? "服务可达" : "未连接"}</>}</span>
        <ModelConnectionState />
      </div>
    </Popover.Content></Popover.Portal>
  </Popover.Root></div>;
}
