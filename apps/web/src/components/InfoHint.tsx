import { useId, useState } from "react";
import { Info } from "lucide-react";
import { Icon } from "./Icon";

export function InfoHint({ label, text }: { label: string; text: string }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  return <span className={`info-hint${open ? " open" : ""}`} onBlur={(event) => {
    if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
  }}>
    <button type="button" className="info-hint-button" aria-label={label} aria-describedby={id}
      aria-expanded={open} onClick={() => setOpen(!open)} onKeyDown={(event) => {
        if (event.key === "Escape") { setOpen(false); event.currentTarget.blur(); }
      }}><Icon icon={Info} size={14} /></button>
    <span role="tooltip" id={id} className="info-hint-text">{text}</span>
  </span>;
}
