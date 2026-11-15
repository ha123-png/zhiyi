import { useId, useLayoutEffect, useRef, type ReactNode } from "react";
import { X } from "lucide-react";
import { Icon } from "./Icon";
import { trapModalTab } from "../useModalFocus";
import "./extract-configuration.css";

export function NoticeDialog({ title, children, actions, onClose, busy = false, footerClose = true }: {
  title: string;
  children: ReactNode;
  actions?: ReactNode;
  onClose: () => void;
  busy?: boolean;
  footerClose?: boolean;
}) {
  const id = useId();
  const ref = useRef<HTMLDialogElement>(null);
  useLayoutEffect(() => {
    const dialog = ref.current;
    const opener = document.activeElement as HTMLElement | null;
    dialog?.showModal();
    return () => {
      dialog?.close();
      if (opener?.isConnected) opener.focus({ preventScroll: true });
    };
  }, []);
  return <dialog ref={ref} className="template-history-dialog extract-scope-dialog notice-dialog"
    aria-labelledby={id} tabIndex={-1} onKeyDown={trapModalTab}
    onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <div className="template-history-head">
      <h3 id={id}>{title}</h3>
      <button className="btn ghost icon-only sm" type="button" aria-label={`关闭${title}`} disabled={busy} onClick={onClose}>
        <Icon icon={X} size={18} />
      </button>
    </div>
    <div className="extract-scope-body notice-dialog-body">{children}</div>
    <div className="extract-scope-footer notice-dialog-actions">
      {footerClose && <button className="btn secondary" type="button" disabled={busy} onClick={onClose}>关闭</button>}
      {actions}
    </div>
  </dialog>;
}
