import { useEffect, useLayoutEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

/** Pointer sorting previews positions without editing the draft until drop. */
export function useFieldSort(identity: string, onMove: (from: number, to: number) => void) {
  const list = useRef<HTMLDivElement>(null);
  const callback = useRef(onMove); callback.current = onMove;
  const [placeholder, setPlaceholder] = useState<{ top: number; height: number } | null>(null);
  const beforeDrop = useRef<Map<HTMLElement, number> | null>(null);
  const active = useRef<{
    from: number; to: number; y: number; startY: number; scroll: HTMLElement; startScroll: number;
    rows: HTMLElement[]; positions: { top: number; height: number }[]; handle: HTMLElement; pointer: number; lifted: boolean; frame: number;
  } | null>(null);
  function preview() {
    const drag = active.current;
    if (!drag) return;
    const dy = drag.y - drag.startY + drag.scroll.scrollTop - drag.startScroll;
    if (!drag.lifted && Math.abs(dy) < 5) return;
    if (!drag.lifted) {
      drag.lifted = true;
      setPlaceholder(drag.positions[drag.from]);
      drag.rows[drag.from].classList.add("field-lifted");
    }
    const origin = drag.positions[drag.from];
    const center = origin.top + origin.height / 2 + dy;
    let to = drag.from;
    drag.positions.forEach((p, index) => {
      if (index > drag.from && center >= p.top + p.height / 2) to = index;
    });
    for (let index = drag.from - 1; index >= 0; index--) if (center <= drag.positions[index].top + drag.positions[index].height / 2) to = index;
    drag.to = to;
    const gap = drag.positions.length > 1 ? drag.positions[1].top - drag.positions[0].top - drag.positions[0].height : 0;
    drag.rows.forEach((row, index) => {
      const offset = index === drag.from ? dy : index > drag.from && index <= to ? -origin.height - gap : index < drag.from && index >= to ? origin.height + gap : 0;
      row.style.transform = `translateY(${offset}px)`;
    });
  }
  function finish(commit: boolean) {
    const drag = active.current;
    if (!drag) return;
    cancelAnimationFrame(drag.frame);
    const moved = commit && drag.lifted && drag.from !== drag.to;
    if (moved) beforeDrop.current = new Map(drag.rows.map(row => [row, row.getBoundingClientRect().top]));
    drag.rows.forEach(row => { row.style.transform = ""; row.classList.remove("field-lifted"); });
    active.current = null; setPlaceholder(null);
    if (drag.handle.hasPointerCapture(drag.pointer)) drag.handle.releasePointerCapture(drag.pointer);
    if (moved) callback.current(drag.from, drag.to);
    drag.handle.focus({ preventScroll: true });
  }
  useEffect(() => {
    const cancel = (event: KeyboardEvent) => { if (event.key === "Escape" && active.current) { event.preventDefault(); event.stopPropagation(); finish(false); } };
    document.addEventListener("keydown", cancel, true);
    return () => { document.removeEventListener("keydown", cancel, true); if (active.current) cancelAnimationFrame(active.current.frame); };
  }, []);
  useLayoutEffect(() => {
    const previous = beforeDrop.current; beforeDrop.current = null;
    if (!previous || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    previous.forEach((top, row) => {
      const distance = top - row.getBoundingClientRect().top;
      if (distance && row.animate) row.animate([{ transform: `translateY(${distance}px)` }, { transform: "translateY(0)" }], { duration: 170, easing: "cubic-bezier(.2,.8,.2,1)" });
    });
  }, [identity]);
  function onPointerDown(event: ReactPointerEvent<HTMLButtonElement>, from: number) {
    if (event.button !== 0 || active.current || !list.current) return;
    const rows = [...list.current.querySelectorAll<HTMLElement>("[data-field-index]")];
    const scroll = list.current.closest<HTMLElement>(".app-page-wrap");
    if (!scroll) return;
    event.preventDefault(); event.currentTarget.focus({ preventScroll: true }); event.currentTarget.setPointerCapture(event.pointerId);
    active.current = { from, to: from, y: event.clientY, startY: event.clientY, scroll, startScroll: scroll.scrollTop, rows,
      positions: rows.map(row => ({ top: row.offsetTop, height: row.offsetHeight })), handle: event.currentTarget, pointer: event.pointerId, lifted: false, frame: 0 };
    function tick() {
      const drag = active.current; if (!drag) return;
      if (drag.lifted) {
        const bounds = drag.scroll.getBoundingClientRect();
        const amount = drag.y < bounds.top + 48 ? -Math.min(16, (bounds.top + 48 - drag.y) / 3) : drag.y > bounds.bottom - 48 ? Math.min(16, (drag.y - bounds.bottom + 48) / 3) : 0;
        if (amount) { drag.scroll.scrollTop += amount; preview(); }
      }
      drag.frame = requestAnimationFrame(tick);
    }
    active.current.frame = requestAnimationFrame(tick);
  }
  return { list, placeholder, onPointerDown,
    onPointerMove: (event: ReactPointerEvent<HTMLButtonElement>) => { if (active.current?.pointer === event.pointerId) { active.current.y = event.clientY; preview(); } },
    onPointerUp: () => finish(true), onPointerCancel: () => finish(false), cancel: () => finish(false),
  };
}
