import type { CSSProperties, MouseEvent } from "react";
import type { LucideIcon } from "lucide-react";

interface IconProps {
  className?: string;
  icon: LucideIcon;
  size?: number;
  style?: CSSProperties;
  onClick?: (event: MouseEvent<SVGElement>) => void;
}

export function Icon({ className, icon: Glyph, size = 17, style, onClick }: IconProps) {
  return (
    <Glyph
      aria-hidden="true"
      className={className}
      onClick={onClick}
      size={size}
      strokeWidth={1.8}
      style={style}
    />
  );
}
