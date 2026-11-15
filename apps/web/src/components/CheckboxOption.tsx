import type { ReactNode } from "react";
import "./checkbox-option.css";

export function CheckboxOption({ title, description, checked, disabled, busy, onChange, accessory }: {
  title: ReactNode;
  description?: ReactNode;
  checked: boolean;
  disabled?: boolean;
  busy?: boolean;
  onChange: (checked: boolean) => void;
  accessory?: ReactNode;
}) {
  return <label className={`checkbox-option${checked ? " is-selected" : ""}`}>
    <input type="checkbox" checked={checked} disabled={disabled} aria-disabled={busy}
      onChange={event => onChange(event.target.checked)} />
    <span className="checkbox-option-copy">
      <span className="checkbox-option-name">{title}</span>
      {description && <span className="checkbox-option-description">{description}</span>}
    </span>
    {accessory}
  </label>;
}
