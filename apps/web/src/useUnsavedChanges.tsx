import { useCallback, useEffect, useRef, useState } from "react";
import { ConfirmDialog } from "./components/ConfirmDialog";

export function useUnsavedChanges(dirty: boolean | (() => boolean)) {
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const [pending, setPending] = useState<(() => void) | null>(null);
  const protect = useCallback((action: () => void) => {
    if (typeof dirtyRef.current === "function" ? dirtyRef.current() : dirtyRef.current) setPending(() => action);
    else action();
  }, []);
  useEffect(() => {
    const navigate = (event: Event) => {
      if (!(typeof dirtyRef.current === "function" ? dirtyRef.current() : dirtyRef.current)) return;
      event.preventDefault();
      setPending(() => (event as CustomEvent<{ proceed: () => void }>).detail.proceed);
    };
    const unload = (event: BeforeUnloadEvent) => {
      if (typeof dirtyRef.current === "function" ? dirtyRef.current() : dirtyRef.current) { event.preventDefault(); event.returnValue = ""; }
    };
    window.addEventListener("zhiyi:before-navigate", navigate);
    window.addEventListener("beforeunload", unload);
    return () => { window.removeEventListener("zhiyi:before-navigate", navigate); window.removeEventListener("beforeunload", unload); };
  }, []);
  return { protect, dialog: <ConfirmDialog open={pending !== null} title="修改尚未保存" tone="normal"
    description="离开会丢弃这次编辑。可以取消并继续编辑，保存后再离开。" buttonLabel="放弃修改并继续"
    onClose={() => setPending(null)} onConfirm={() => { const action = pending; setPending(null); action?.(); }} /> };
}
