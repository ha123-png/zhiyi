import type { ComponentPropsWithoutRef } from "react";
import { ChevronDown } from "lucide-react";
import { Icon } from "./Icon";
import "./collapse-chevron.css";

export function CollapseChevron({ expanded, size = 14, className = "" }: {
  expanded?: boolean; size?: number; className?: string;
}) {
  return <Icon icon={ChevronDown} size={size}
    className={`collapse-chevron${expanded ? " is-expanded" : ""}${className ? ` ${className}` : ""}`} />;
}

/** Keep the native details/summary keyboard and open-state semantics. */
export function DisclosureSummary({ children, className = "", ...props }: ComponentPropsWithoutRef<"summary">) {
  return <summary {...props} className={`disclosure-summary${className ? ` ${className}` : ""}`}>
    <span>{children}</span><CollapseChevron />
  </summary>;
}
