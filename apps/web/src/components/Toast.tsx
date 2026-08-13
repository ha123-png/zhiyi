import { useEffect, useRef, useState } from "react";

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

  return {
    toast,
    notify: (message: string, tone: ToastTone = "info", duration?: number) =>
      setToast({ message, tone, duration }),
    clear: () => setToast(null),
  };
}

export function Toast({
  toast,
  onDismiss,
}: {
  toast: ToastState | null;
  onDismiss?: () => void;
}) {
  if (!toast) {
    return null;
  }
  return (
    <div
      className={`app-toast ${toast.tone}`}
      onClick={onDismiss}
      role={toast.tone === "error" ? "alert" : "status"}
      title={toast.tone === "error" ? "点击关闭" : undefined}
    >
      {toast.message}
    </div>
  );
}
