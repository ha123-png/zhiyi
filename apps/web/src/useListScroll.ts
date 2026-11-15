import { useLayoutEffect, useRef, type UIEvent } from "react";

type ScrollSlot = "tasks" | "history-completed" | "history-problems";
export type ListScrollMemory = Partial<Record<ScrollSlot, { query: string; top: number; left: number }>>;

export function useListScroll(memory: ListScrollMemory, slot: ScrollSlot, query: string | null) {
  const viewport = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const area = viewport.current;
    if (!area || query === null) return;
    const saved = memory[slot];
    area.scrollTop = saved?.query === query ? saved.top : 0;
    area.scrollLeft = saved?.query === query ? saved.left : 0;
  }, [memory, slot, query]);

  function rememberScroll({ currentTarget }: UIEvent<HTMLDivElement>) {
    if (query !== null) {
      memory[slot] = { query, top: currentTarget.scrollTop, left: currentTarget.scrollLeft };
    }
  }

  return { viewport, rememberScroll };
}
