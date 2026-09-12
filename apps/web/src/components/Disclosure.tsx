import { useId, useState, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { Icon } from "./Icon";

export function Disclosure({ title, children, className = "", defaultOpen = false }: {
  title: ReactNode; children: ReactNode; className?: string; defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  return <section className={`disclosure ${className}`}>
    <button className="disclosure-trigger" type="button" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
      <span>{title}</span><Icon icon={ChevronDown} size={14} className={open ? "disclosure-arrow open" : "disclosure-arrow"} />
    </button>
    <div className={`collapse${open ? " open" : ""}`} id={id} inert={!open}>
      <div className="collapse-content"><div className="disclosure-body">{children}</div></div>
    </div>
  </section>;
}
