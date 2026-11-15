import { useLayoutEffect, type ReactNode } from "react";
import { useListScroll, type ListScrollMemory } from "../useListScroll";

export const HISTORY_PAGE_SIZE = 10;

export function HistoryTableGrid({ children, scrollMemory, scrollSlot, query }: {
  children: ReactNode;
  scrollMemory: ListScrollMemory;
  scrollSlot: "history-completed" | "history-problems";
  query: string | null;
}) {
  const { viewport, rememberScroll } = useListScroll(scrollMemory, scrollSlot, query);
  useLayoutEffect(() => {
    const area = viewport.current;
    const header = area?.querySelector("thead");
    if (!area || !header) return;
    const measure = () => {
      const height = Math.max(0, area.clientHeight - header.getBoundingClientRect().height);
      area.style.setProperty("--history-fit-row-height", `${Math.floor(height / HISTORY_PAGE_SIZE * 64) / 64}px`);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(area);
    observer.observe(header);
    return () => observer.disconnect();
  }, [viewport]);
  return <div className="data-table-wrap history-grid-viewport" ref={viewport} onScroll={rememberScroll}
    role="region" aria-label="文件历史表格，可滚动" tabIndex={0}>{children}</div>;
}
