import { useEffect, useId, useRef, useState } from "react";
import { Loader2, ShieldAlert } from "lucide-react";
import { Icon } from "./Icon";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  /** 必须逐字输入的确认文字（参考 GitHub 删除仓库）。为空表示无需输入、可直接确认 */
  confirmText?: string;
  buttonLabel: string;
  busy?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmText,
  buttonLabel,
  busy = false,
  onConfirm,
  onClose,
}: ConfirmDialogProps) {
  const [input, setInput] = useState("");
  const dialogRef = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  // 未提供确认文字时无需输入，按钮始终可用
  const requireText = typeof confirmText === "string" && confirmText !== "";
  const matched = requireText ? input === confirmText : true;

  useEffect(() => {
    if (!open) {
      setInput("");
      return;
    }
    if (!requireText) cancelRef.current?.focus();
  }, [open, requireText]);

  if (!open) return null;

  return (
    <div className="modal-overlay" role="presentation" onMouseDown={(e) => {
      if (e.target === e.currentTarget && !busy) onClose();
    }}>
      <div
        aria-describedby={descriptionId}
        aria-labelledby={titleId}
        aria-modal="true"
        className="modal-card ai-tpl-modal"
        onKeyDown={(event) => {
          if (event.key === "Escape" && !busy) {
            event.preventDefault();
            onClose();
            return;
          }
          if (event.key !== "Tab" || !dialogRef.current) return;
          const focusable = Array.from(
            dialogRef.current.querySelectorAll<HTMLElement>(
              'button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
            ),
          );
          if (focusable.length === 0) return;
          const first = focusable[0];
          const last = focusable[focusable.length - 1];
          if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
          }
        }}
        ref={dialogRef}
        role="dialog"
      >
        <div className="modal-head modal-head-centered">
          <span className="modal-eyebrow">
            <Icon icon={ShieldAlert} size={13} /> 危险操作
          </span>
          <h3 id={titleId}>{title}</h3>
          <div className="support" id={descriptionId}>{description}</div>
        </div>
        {requireText && (
          <div className="modal-body">
            <label className="settings-name" htmlFor="confirm-dialog-input" style={{ display: "block", marginBottom: 8 }}>
              请输入 <code className="mono" style={{ fontWeight: 600 }}>{confirmText}</code> 以确认
            </label>
            <input
              aria-label={`输入 ${confirmText} 确认删除`}
              className="form-input"
              id="confirm-dialog-input"
              type="text"
              value={input}
              autoFocus
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && matched && !busy) onConfirm();
              }}
              placeholder={`输入 ${confirmText} 后确认按钮可用`}
            />
          </div>
        )}
        <div className="modal-foot">
          <button ref={cancelRef} className="btn secondary" type="button" disabled={busy} onClick={onClose}>
            取消
          </button>
          <button
            className="btn danger"
            type="button"
            disabled={!matched || busy}
            onClick={onConfirm}
          >
            {busy && <Icon icon={Loader2} size={13} className="spin" />} {buttonLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
