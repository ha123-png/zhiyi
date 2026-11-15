import "./status-dot.css";

export type StatusDotState = "processing" | "queued" | "pending" | "completed" | "failed" | "paused" | "cancelled" | "idle" | "unknown";

export function StatusDot({ state, healthy = false, className = "" }: { state: StatusDotState; healthy?: boolean; className?: string }) {
  return <span className={`status-dot${healthy ? " is-healthy" : ""}${className ? ` ${className}` : ""}`} data-state={state} aria-hidden="true" />;
}
