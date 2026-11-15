import { useId, type ReactNode } from "react";
import { useModalFocus } from "../useModalFocus";

export function TaskActionDialog({ open, title, filename, busy = false, onClose, children }: {
  open: boolean; title: string; filename: string; busy?: boolean; onClose: () => void; children: ReactNode;
}) {
  const titleId = useId();
  const dialog = useModalFocus(open, onClose, busy);
  if (!open) return null;
  return <div className="modal-overlay" onMouseDown={event => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <div className="modal-card task-action-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} {...dialog}>
      <div className="modal-head"><h3 id={titleId}>{title}</h3><p className="support task-dialog-filename">{filename}</p></div>
      <div className="modal-body task-dialog-body">{children}</div>
      <div className="modal-foot"><button type="button" className="btn secondary" disabled={busy} onClick={onClose}>关闭</button></div>
    </div>
  </div>;
}
