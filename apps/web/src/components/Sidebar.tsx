import {
  Activity,
  MessageCircle,
  Braces,
  Database,
  DatabaseBackup,
  FileText,
  History,
  CircleHelp,
  LayoutGrid,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  Settings2,
  Upload,
  X,
} from "lucide-react";
import { useRef, useState } from "react";
import type {
  KeyboardEvent as ReactKeyboardEvent,
  PointerEvent as ReactPointerEvent,
} from "react";
import type { LucideIcon } from "lucide-react";
import { Icon } from "./Icon";
import type { NavigationKey } from "../types";
import { usePointerDrag } from "../usePointerDrag";

interface NavItem {
  key: NavigationKey;
  label: string;
  ariaLabel?: string;
  icon: LucideIcon;
  caption: string;
}

const navigation: NavItem[] = [
  { key: "workspace", label: "状态监控", icon: Activity, caption: "任务" },
  { key: "dashboard", label: "仪表盘", icon: LayoutGrid, caption: "概览" },
  { key: "extract", label: "文件提取", icon: Upload, caption: "上传" },
  { key: "tables", label: "数据仓库", icon: Database, caption: "" },
  { key: "history", label: "文件历史", icon: History, caption: "" },
  { key: "templates", label: "模板", icon: FileText, caption: "" },
  { key: "assistant", label: "问知意", icon: MessageCircle, caption: "对话" },
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
  onNavigateIntent?: (key: NavigationKey) => void;
}

export function Sidebar({ active, onNavigate, onNavigateIntent }: SidebarProps) {
  const startPointerDrag = usePointerDrag();
  const [width, setWidth] = useState(232);
  const [collapsed, setCollapsed] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const mobileMenuDialogRef = useRef<HTMLDialogElement>(null);
  const mobileMenuTriggerRef = useRef<HTMLButtonElement>(null);

  function openMobileMenu() {
    const dialog = mobileMenuDialogRef.current;
    if (!dialog) return;
    if (!dialog.open) {
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    }
    setMobileMenuOpen(true);
    requestAnimationFrame(() => {
      dialog.querySelector<HTMLButtonElement>(".mobile-nav-dialog-list .nav-item")?.focus({ preventScroll: true });
    });
  }

  function closeMobileMenu() {
    const dialog = mobileMenuDialogRef.current;
    if (dialog?.open) {
      if (typeof dialog.close === "function") dialog.close();
      else dialog.removeAttribute("open");
    }
    setMobileMenuOpen(false);
    requestAnimationFrame(() => mobileMenuTriggerRef.current?.focus({ preventScroll: true }));
  }

  function navigateFromMobileMenu(key: NavigationKey) {
    closeMobileMenu();
    onNavigate(key);
  }

  function startResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (collapsed || event.button !== 0) return;
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = width;

    function resize(moveEvent: PointerEvent) {
      setWidth(Math.min(360, Math.max(196, startWidth + moveEvent.clientX - startX)));
    }

    startPointerDrag(resize, undefined, event.pointerId, event.currentTarget);
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

      <button
        aria-controls="mobile-workspace-navigation"
        aria-expanded={mobileMenuOpen}
        aria-haspopup="dialog"
        aria-label={mobileMenuOpen ? "页面导航已展开" : "展开页面导航"}
        className="mobile-nav-toggle"
        onClick={openMobileMenu}
        ref={mobileMenuTriggerRef}
        type="button"
      >
        <Icon icon={Menu} size={16} />
        <span>菜单</span>
      </button>

      <nav aria-label="工作区" className="nav-stack">
        <div>
          <div className="nav-section-title">工作区</div>
          {navigation.map((item) => (
            <button
              aria-current={active === item.key ? "page" : undefined}
              aria-label={item.ariaLabel ?? item.label}
              className={`nav-item${active === item.key ? " active" : ""}`}
              key={item.key}
              onClick={() => onNavigate(item.key)}
              onPointerEnter={() => onNavigateIntent?.(item.key)}
              onFocus={() => onNavigateIntent?.(item.key)}
              title={item.label}
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
      </nav>

      <dialog
        aria-label="工作区页面"
        className="mobile-nav-dialog"
        id="mobile-workspace-navigation"
        onClick={(event) => {
          if (event.target === event.currentTarget) closeMobileMenu();
        }}
        onClose={() => {
          setMobileMenuOpen(false);
          requestAnimationFrame(() => mobileMenuTriggerRef.current?.focus({ preventScroll: true }));
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            closeMobileMenu();
          }
        }}
        ref={mobileMenuDialogRef}
      >
        <div className="mobile-nav-dialog-head">
          <strong>工作区</strong>
          <button
            aria-label="关闭页面导航"
            className="icon-button"
            onClick={closeMobileMenu}
            type="button"
          >
            <Icon icon={X} size={16} />
          </button>
        </div>
        <nav aria-label="工作区页面" className="mobile-nav-dialog-list">
          {navigation.map((item) => (
            <button
              aria-current={active === item.key ? "page" : undefined}
              aria-label={item.ariaLabel ?? item.label}
              className={`nav-item${active === item.key ? " active" : ""}`}
              key={item.key}
              onClick={() => navigateFromMobileMenu(item.key)}
              onPointerEnter={() => onNavigateIntent?.(item.key)}
              onFocus={() => onNavigateIntent?.(item.key)}
              type="button"
            >
              <span className="nav-label">
                <Icon icon={item.icon} size={15} />
                <span>{item.label}</span>
              </span>
              {item.caption ? <span className="nav-caption">{item.caption}</span> : null}
            </button>
          ))}
        </nav>
      </dialog>

      <div className="sidebar-footer">
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
