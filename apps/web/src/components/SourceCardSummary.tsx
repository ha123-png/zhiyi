import type { ReactNode } from "react";
import { FileText } from "lucide-react";
import { displayCardValue } from "./cardValues";
import { Icon } from "./Icon";
import "./cards.css";

interface CardField { key: string; label: string; value: unknown }

function previewValue(value: unknown, search: string) {
  const text = displayCardValue(value) || "—";
  const query = search.trim();
  if (!query) return text;
  const start = text.toLocaleLowerCase().indexOf(query.toLocaleLowerCase());
  if (start < 0) return text;
  const match = text.slice(start, start + query.length);
  const after = Array.from(text.slice(start + match.length));
  return <><mark>{match}</mark>{after.slice(0, 12).join("")}{after.length > 12 ? "…" : ""}</>;
}

/** One source card's display only; callers retain all reads, selection and edits. */
export function SourceCardSummary({ filename, fields, summary, onOpen, headingAction, status, search = "", className = "" }: {
  filename: string; fields: readonly CardField[]; summary: ReactNode; onOpen: () => void;
  headingAction?: ReactNode; status?: ReactNode; search?: string; className?: string;
}) {
  return <article className={`file-data-card${className ? ` ${className}` : ""}`} aria-label={filename}>
    <div className="file-card-heading"><Icon icon={FileText} size={16} />
      <button type="button" title={filename} onClick={onOpen}>{filename}</button>
      {headingAction}
    </div>
    <dl className="file-field-list">{fields.slice(0, 6).map(field => <div key={field.key}>
      <dt title={field.label}>{field.label}</dt><dd title={displayCardValue(field.value)}>{previewValue(field.value, search)}</dd>
    </div>)}</dl>
    {!fields.length && <p className="support">尚未填写内容</p>}
    <div className="file-card-status">{status}</div>
    <div className="file-card-footer"><span>{summary}</span>
      <button className="btn secondary xs" type="button" onClick={onOpen}>查看与编辑</button>
    </div>
  </article>;
}

export function SourceFieldList<T extends {key: string; label: string}>({ columns, renderValue }: {
  columns: readonly T[]; renderValue: (column: T) => ReactNode;
}) {
  return <dl className="file-field-list expanded">
    {columns.map(column => <div key={column.key}>
      <dt>{column.label}</dt><dd>{renderValue(column)}</dd>
    </div>)}
  </dl>;
}
