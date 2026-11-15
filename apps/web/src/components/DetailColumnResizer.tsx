import { usePointerDrag } from "../usePointerDrag";

const MIN_WIDTH = 64;
const MAX_WIDTH = 640;

export function DetailColumnResizer({ label, width, onResize }: {
  label: string; width: number; onResize: (width: number) => void;
}) {
  const startPointerDrag = usePointerDrag();
  const apply = (value: number) => onResize(Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, value)));
  return <span className="column-resizer" role="separator" aria-orientation="vertical"
    aria-label={`调整“${label}”列宽`} aria-valuemin={MIN_WIDTH} aria-valuemax={MAX_WIDTH}
    aria-valuenow={width} tabIndex={0} onPointerDown={event => {
      if (event.button !== 0) return;
      event.preventDefault();
      const start = event.clientX;
      startPointerDrag(move => apply(width + move.clientX - start), undefined, event.pointerId, event.currentTarget);
    }} onKeyDown={event => {
      const keys: Record<string, number> = { ArrowLeft: width - 12, ArrowRight: width + 12, Home: MIN_WIDTH, End: MAX_WIDTH };
      const next = keys[event.key];
      if (typeof next !== "number") return;
      event.preventDefault();
      apply(next);
    }} />;
}
