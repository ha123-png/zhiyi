import {
  useEffect,
  useRef,
  useState,
  useId,
  type ReactNode,
  type PointerEvent,
} from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { ChevronRight } from "lucide-react";

export function useConversationScroll(
  threadId: string | undefined,
  busy: boolean,
) {
  const viewport = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const lastTop = useRef(0);
  const wasBusy = useRef(false);
  const [paused, setPaused] = useState(false);
  const touchY = useRef<number | null>(null);
  const resume = () => {
    following.current = true;
    setPaused(false);
    const element = viewport.current;
    if (element) {
      element.scrollTop = element.scrollHeight;
      lastTop.current = element.scrollTop;
    }
  };
  const pause = () => {
    if (!viewport.current?.querySelector(".ask-message")) return;
    following.current = false;
    setPaused(true);
  };
  useEffect(() => {
    resume();
  }, [threadId]);
  useEffect(() => {
    if (busy && !wasBusy.current) resume();
    wasBusy.current = busy;
  }, [busy]);
  useEffect(() => {
    let frame = 0;
    const follow = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const element = viewport.current;
        if (following.current && element) {
          element.scrollTop = element.scrollHeight;
          lastTop.current = element.scrollTop;
        }
      });
    };
    const resize = new ResizeObserver(follow);
    const mutation = new MutationObserver(follow);
    if (content.current) {
      resize.observe(content.current);
      mutation.observe(content.current, {
        subtree: true,
        childList: true,
        characterData: true,
      });
    }
    if (viewport.current) resize.observe(viewport.current);
    return () => {
      cancelAnimationFrame(frame);
      resize.disconnect();
      mutation.disconnect();
    };
  }, []);
  return {
    viewport,
    content,
    paused,
    resume,
    events: {
      onWheel: (e: React.WheelEvent) => {
        if (e.deltaY < 0) pause();
      },
      onPointerDown: pause,
      onTouchStart: (e: React.TouchEvent) => {
        touchY.current = e.touches[0]?.clientY ?? null;
      },
      onTouchMove: (e: React.TouchEvent) => {
        if (touchY.current != null && e.touches[0]?.clientY > touchY.current)
          pause();
      },
      onKeyDown: (e: React.KeyboardEvent) => {
        if (["ArrowUp", "PageUp", "Home"].includes(e.key)) pause();
      },
      onScroll: () => {
        const e = viewport.current;
        if (!e) return;
        if (e.scrollHeight - e.clientHeight - e.scrollTop < 20) {
          if (!following.current) {
            following.current = true;
            setPaused(false);
          }
        } else if (e.scrollTop < lastTop.current - 1) pause();
        lastTop.current = e.scrollTop;
      },
    },
  };
}

export function Fold({
  title,
  children,
  initialOpen = false,
  className = "",
}: {
  title: ReactNode;
  children: ReactNode;
  initialOpen?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(initialOpen);
  const bodyId = useId();
  const reduced = useReducedMotion();
  return (
    <div className={`ask-fold ${className}`}>
      <button
        type="button"
        className="ask-fold-toggle"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen(!open)}
      >
        <ChevronRight
          size={13}
          style={{ transform: open ? "rotate(90deg)" : undefined }}
        />
        {title}
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{
              duration: reduced ? 0 : 0.18,
              ease: [0.2, 0.8, 0.2, 1],
            }}
            className="ask-fold-body"
            id={bodyId}
          >
            <div>{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export function usePanelWidth(
  key: string,
  initial: number,
  min: number,
  max: number,
  reverse = false,
) {
  const [width, setWidth] = useState(() => {
    const saved = Number(localStorage.getItem(key));
    return saved >= min && saved <= max ? saved : initial;
  });
  const cleanup = useRef<(() => void) | null>(null);
  useEffect(() => () => cleanup.current?.(), []);
  const update = (value: number) => {
    const next = Math.max(min, Math.min(max, value));
    setWidth(next);
    localStorage.setItem(key, String(next));
  };
  return {
    width,
    separator: {
      role: "separator" as const,
      tabIndex: 0,
      "aria-orientation": "vertical" as const,
      "aria-valuenow": Math.round(width),
      "aria-valuemin": min,
      "aria-valuemax": max,
      onKeyDown: (e: React.KeyboardEvent) => {
        if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
          e.preventDefault();
          update(
            width + (e.key === "ArrowRight" ? 16 : -16) * (reverse ? -1 : 1),
          );
        }
      },
      onPointerDown: (e: PointerEvent) => {
        e.preventDefault();
        cleanup.current?.();
        const start = e.clientX;
        const move = (p: globalThis.PointerEvent) =>
          update(width + (p.clientX - start) * (reverse ? -1 : 1));
        const end = () => {
          window.removeEventListener("pointermove", move);
          window.removeEventListener("pointerup", end);
          window.removeEventListener("pointercancel", end);
        };
        cleanup.current = end;
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", end);
        window.addEventListener("pointercancel", end);
      },
    },
  };
}
