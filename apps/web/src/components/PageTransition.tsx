import { useLayoutEffect, type RefObject } from "react";
import "./page-transition.css";

// This runs inside Suspense, when the destination is actually ready. It never
// remounts a page, delays navigation, or transforms the scrolling/dialog layer.
export function PageEntrance({ page, container }: { page: string; container: RefObject<HTMLDivElement | null> }) {
  useLayoutEffect(() => {
    const element = container.current;
    const preference = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if (!element?.animate || preference?.matches) return;
    const animations = [element.animate([{ opacity: 0.92 }, { opacity: 1 }], {
      duration: 180, easing: "cubic-bezier(.2,.7,.2,1)",
    })];
    const cancel = () => animations.forEach(animation => animation.cancel());
    const preferenceChanged = () => { if (preference?.matches) cancel(); };
    preference?.addEventListener("change", preferenceChanged);
    return () => {
      cancel();
      preference?.removeEventListener("change", preferenceChanged);
    };
  }, [page, container]);
  return null;
}

export function PageOpening() {
  return <div className="page-opening" role="status" aria-label="页面加载中">
    <div className="page-opening-body" aria-hidden="true"><span /><span /><span /></div>
  </div>;
}
