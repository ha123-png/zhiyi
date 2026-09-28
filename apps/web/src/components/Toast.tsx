import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

export type ToastTone = "info" | "error" | "success";

export interface ToastState {
  message: string;
  tone: ToastTone;
  /** 自动消失时长（毫秒）。info/success 默认 3.2s；error 默认停留直到点击，
   *  传入该值后错误 toast 也会定时自动消失（用于任务失败通知等不阻塞的场景）。 */
  duration?: number;
}

/**
 * Auto-dismissing toast hook. Success/info toasts clear after 3.2s;
 * error toasts stay until dismissed (the user needs to read them),
 * unless an explicit duration is passed.
 */
export function useToast() {
  const [toast, setToast] = useState<ToastState | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
    if (toast) {
      const ms =
        toast.tone === "error" ? (toast.duration ?? 0) : 3200;
      if (ms > 0) {
        timer.current = window.setTimeout(() => setToast(null), ms);
      }
    }
    return () => {
      if (timer.current !== null) {
        window.clearTimeout(timer.current);
        timer.current = null;
      }
    };
  }, [toast]);

  const notify = useCallback((message: string, tone: ToastTone = "info", duration?: number) =>
    setToast({ message, tone, duration }), []);
  const clear = useCallback(() => setToast(null), []);
  return {
    toast,
    notify,
    clear,
  };
}

export function Toast({
  toast,
  onDismiss,
}: {
  toast: ToastState | null;
  onDismiss?: () => void;
}) {
  const [host, setHost] = useState<HTMLElement | null>(null);
  useEffect(() => {
    if (!toast) return;
    let activeHost: HTMLElement | null = null;
    // Native dialogs occupy the browser top layer: z-index on body cannot put
    // a notice above them. Mount inside the active dialog without moving focus.
    const locate = () => {
      const dialogs = Array.from(document.querySelectorAll<HTMLDialogElement>("dialog[open]"));
      const focused = document.activeElement?.closest<HTMLDialogElement>("dialog[open]");
      activeHost = focused ?? dialogs.at(-1) ?? document.body;
      setHost(activeHost);
    };
    locate();
    const observer = new MutationObserver(records => {
      // Streaming text and virtualised previews mutate often. Only dialog
      // lifecycle changes need another top-layer lookup.
      if (!activeHost?.isConnected || records.some(record => record.type === "attributes" ||
        [...record.addedNodes].some(node => node instanceof Element &&
          (node.matches("dialog[open]") || node.querySelector("dialog[open]"))))) locate();
    });
    observer.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ["open"] });
    document.addEventListener("focusin", locate);
    return () => { observer.disconnect(); document.removeEventListener("focusin", locate); };
  }, [toast]);
  if (!toast) {
    return null;
  }
  return createPortal(
    <div
      className={`app-toast ${toast.tone}`}
      role={toast.tone === "error" ? "alert" : "status"}
    >
      <span>{toast.message}</span>
      {onDismiss && <button type="button" className="toast-dismiss" aria-label="关闭通知" onClick={onDismiss}><X size={16} aria-hidden="true" /></button>}
    </div>, host?.isConnected ? host : document.body,
  );
}
