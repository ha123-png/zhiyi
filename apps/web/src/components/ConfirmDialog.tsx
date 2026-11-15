import { useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { useModalFocus } from "../useModalFocus";
import { Loader2, ShieldAlert } from "lucide-react";
import { Icon } from "./Icon";

interface ConfirmDialogProps {
  tone?: "danger" | "normal";
  open: boolean;
  title: string;
  titleClassName?: string;
  description: ReactNode;
  /** 必须逐字输入的确认文字（参考 GitHub 删除仓库）。为空表示无需输入、可直接确认 */
  confirmText?: string;
  buttonLabel: string;
  cancelLabel?: string;
  eyebrow?: string;
  busy?: boolean;
  error?: string | null;
  onConfirm: () => void;
  onClose: () => void;
}

export function ConfirmDialog({
  tone = "danger",
  open,
  title,
  titleClassName,
  description,
  confirmText,
  buttonLabel,
  cancelLabel = "取消",
  eyebrow,
  busy = false,
  error = null,
  onConfirm,
  onClose,
}: ConfirmDialogProps) {
  const [input, setInput] = useState("");
  const cancelRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  // 未提供确认文字时无需输入，按钮始终可用
  const requireText = typeof confirmText === "string" && confirmText !== "";
  const matched = requireText ? input === confirmText : true;

  const dialog = useModalFocus(open, onClose, busy, requireText ? inputRef : cancelRef);
  useLayoutEffect(() => { if (!open) setInput(""); }, [open]);
  useLayoutEffect(() => {
    if (open && !busy && dialog.ref.current && !dialog.ref.current.contains(document.activeElement)) {
      (requireText ? inputRef.current : cancelRef.current)?.focus({ preventScroll: true });
    }
  }, [open, busy, requireText, dialog.ref]);

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
        {...dialog}
        role="dialog"
      >
        <div className="confirm-dialog-scroll" style={{ minHeight: 0, overflowY: "auto", overflowX: "hidden" }}>
          <div className="modal-head modal-head-centered">
          <span className="modal-eyebrow">
            <Icon icon={ShieldAlert} size={13} /> {eyebrow ?? (tone === "danger" ? "危险操作" : "未保存的编辑")}
          </span>
          <h3 id={titleId} className={titleClassName}>{title}</h3>
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
              ref={inputRef}
              id="confirm-dialog-input"
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.nativeEvent.isComposing && matched && !busy) {
                  // Closing restores focus to the opener. Consume Enter so the
                  // browser cannot activate that button again after the close.
                  e.preventDefault();
                  e.stopPropagation();
                  onConfirm();
                }
              }}
              placeholder={`输入 ${confirmText} 后确认按钮可用`}
            />
          </div>
        )}
          {error && <div className="callout danger" role="alert" style={{ margin: "0 20px 14px" }}>{error}</div>}
        </div>
        <div className="modal-foot" style={{ flexShrink: 0 }}>
          <button ref={cancelRef} className="btn secondary" type="button" disabled={busy} onClick={onClose}>
            {cancelLabel}
          </button>
          <button
            className={`btn ${tone === "danger" ? "danger" : "primary"}`}
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
