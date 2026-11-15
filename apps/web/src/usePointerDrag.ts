import { useCallback, useEffect, useRef } from "react";

/** One pointer session per owner, released on cancellation, window blur or unmount. */
export function usePointerDrag() {
  const cleanup = useRef<(() => void) | null>(null);
  useEffect(() => () => cleanup.current?.(), []);
  return useCallback((
    onMove: (event: PointerEvent) => void,
    onEnd?: () => void,
    pointerId?: number,
    target?: Element,
  ) => {
    cleanup.current?.();
    const matches = (event: PointerEvent) => pointerId === undefined || event.pointerId === pointerId;
    const move = (event: PointerEvent) => { if (matches(event)) onMove(event); };
    const release = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      window.removeEventListener("lostpointercapture", end);
      window.removeEventListener("blur", finish);
      if (cleanup.current === release) cleanup.current = null;
      if (pointerId !== undefined && target?.hasPointerCapture?.(pointerId)) target.releasePointerCapture(pointerId);
    };
    const finish = () => { release(); onEnd?.(); };
    const end = (event: PointerEvent) => { if (matches(event)) finish(); };
    cleanup.current = release;
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
    window.addEventListener("lostpointercapture", end);
    window.addEventListener("blur", finish);
    // Capture keeps the matching pointer's release observable outside the element.
    if (pointerId !== undefined && target?.setPointerCapture) target.setPointerCapture(pointerId);
  }, []);
}
