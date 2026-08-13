import {
  Activity,
  Braces,
  Database,
  DatabaseBackup,
  FileText,
  History,
  CircleHelp,
  LayoutGrid,
  PanelLeftClose,
  PanelLeftOpen,
  Settings2,
  Upload,
} from "lucide-react";
import { useState } from "react";
import type {
  KeyboardEvent as ReactKeyboardEvent,
  PointerEvent as ReactPointerEvent,
} from "react";
import type { LucideIcon } from "lucide-react";
import { Icon } from "./Icon";
import type { NavigationKey } from "../types";

interface NavItem {
  key: NavigationKey;
  label: string;
  ariaLabel?: string;
  icon: LucideIcon;
  caption: string;
}

// 与设计稿一致（已删除「分析」「AI 助手」）
const navigation: NavItem[] = [
  { key: "workspace", label: "状态监控", icon: Activity, caption: "任务" },
  { key: "dashboard", label: "仪表盘", icon: LayoutGrid, caption: "概览" },
  { key: "extract", label: "提取", ariaLabel: "提取工作台", icon: Upload, caption: "上传" },
  { key: "tables", label: "数据仓库", icon: Database, caption: "" },
  { key: "history", label: "文件历史", icon: History, caption: "" },
  { key: "templates", label: "模板", icon: FileText, caption: "" },
  { key: "backups", label: "备份", caption: "数据安全", icon: DatabaseBackup },
  { key: "connections", label: "接口", icon: Braces, caption: "API" },
  { key: "guide", label: "使用说明", icon: CircleHelp, caption: "帮助" },
  { key: "settings", label: "设置", icon: Settings2, caption: "" },
];

interface SidebarProps {
  active: NavigationKey;
  modelConnected: boolean | null;
  modelName: string | null;
  onNavigate: (key: NavigationKey) => void;
}

export function Sidebar({ active, onNavigate }: SidebarProps) {
  const [width, setWidth] = useState(232);
  const [collapsed, setCollapsed] = useState(false);

  function startResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (collapsed) return;
    const startX = event.clientX;
    const startWidth = width;

    function resize(moveEvent: PointerEvent) {
      setWidth(Math.min(360, Math.max(196, startWidth + moveEvent.clientX - startX)));
    }

    function stopResize() {
      document.removeEventListener("pointermove", resize);
      document.removeEventListener("pointerup", stopResize);
    }

    document.addEventListener("pointermove", resize);
    document.addEventListener("pointerup", stopResize);
  }

  function resizeWithKeyboard(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      setWidth((current) => Math.max(196, current - 16));
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      setWidth((current) => Math.min(360, current + 16));
    } else if (event.key === "Home") {
      event.preventDefault();
      setWidth(196);
    } else if (event.key === "End") {
      event.preventDefault();
      setWidth(360);
    }
  }

  return (
    <aside
      className={`app-sidebar${collapsed ? " is-collapsed" : ""}`}
      style={{ width: collapsed ? 72 : width }}
    >
      <div className="brand">
        <div className="brand-mark">
          <img alt="知意" className="brand-logo" src="/logo.png" />
        </div>
        <div className="brand-copy">
          <strong>知意</strong>
          <small>本地智能数据工作台</small>
        </div>
      </div>

      <div className="nav-stack">
        <div>
          <div className="nav-section-title">工作区</div>
          {navigation.map((item) => (
            <button
              aria-label={item.ariaLabel ?? item.label}
              className={`nav-item${active === item.key ? " active" : ""}`}
              key={item.key}
              onClick={() => onNavigate(item.key)}
              type="button"
            >
              <span className="nav-label">
                <Icon icon={item.icon} size={15} />
                <span>{item.label}</span>
              </span>
              {item.caption ? (
                <span className="nav-caption">{item.caption}</span>
              ) : null}
            </button>
          ))}
        </div>
      </div>

      <div className="sidebar-footer">
        <div className="nav-section-title">队列</div>
        <div className="queue-pill">
          <span className="queue-dot processing" />
          <span>处理中</span>
        </div>
        <button
          aria-label={collapsed ? "展开侧边栏" : "收起侧边栏"}
          className="icon-button sidebar-toggle"
          onClick={() => setCollapsed((current) => !current)}
          title={collapsed ? "展开侧边栏" : "收起侧边栏"}
          type="button"
        >
          <Icon icon={collapsed ? PanelLeftOpen : PanelLeftClose} size={15} />
        </button>
      </div>
      <div
        aria-label="调整侧边栏宽度"
        aria-orientation="vertical"
        aria-valuemax={360}
        aria-valuemin={196}
        aria-valuenow={width}
        className="sidebar-resizer"
        onKeyDown={resizeWithKeyboard}
        onPointerDown={startResize}
        role="separator"
        tabIndex={collapsed ? -1 : 0}
      />
    </aside>
  );
}
